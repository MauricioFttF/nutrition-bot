"""
bot.py

Entrypoint e orquestrador. Único módulo que conhece todos os outros
(sheets_client, llm_parser, food_matcher, card_generator) e a API do
Telegram (via aiogram).

Fluxos implementados (ver nutrition-bot-spec.md, seção 3):
  3.1 — Registro por texto
  3.3 — Cadastro de alimento customizado por foto de rótulo (com
        preenchimento de nome inicial, foto e edição via texto livre)
(3.2 — registro por foto de comida — fica pra depois, é o mais
 arriscado em precisão; validar 3.1 primeiro.)
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import card_generator
import food_matcher
import llm_parser
import sheets_client

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Configuração (variáveis de ambiente — nunca hardcode segredos aqui)
# ---------------------------------------------------------------------
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
CREDENTIALS_PATH = os.environ.get(
    "GOOGLE_CREDENTIALS_PATH", "credentials/service_account.json"
)

# Mapeia user_id do Telegram -> nome da aba na planilha.
# Ajuste com os IDs reais de vocês dois antes de rodar.
USUARIOS = {
    1159755253: "Mauricio",
    222222222: "Lilian",  # TODO: substituir pelo ID real (ela manda /start pro bot)
}

REFEICOES = ["Café da manhã", "Almoço", "Lanche", "Janta"]

_CAMPOS_CADASTRO_OBRIGATORIOS = ("nome", "porcao_base_g", "kcal", "proteina", "carbo", "gordura")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
sheets = sheets_client.get_client(CREDENTIALS_PATH)

# Cache em memória da aba "Alimentos". Recarregado no startup e a cada
# 1h por um job em background (ver main()).
_cache_alimentos: list[dict] = []

# Estado temporário por usuário. Formatos possíveis:
#   {"tipo": "log", ...}                         — aguardando escolha de refeição
#   {"tipo": "cadastro", "dados": {...},
#    "aguardando": "nome_inicial" | "dados_iniciais" | "nome" | "edicao" | None}
_pendentes: dict[int, dict] = {}


def _aba_do_usuario(user_id: int) -> str | None:
    return USUARIOS.get(user_id)


def _teclado_refeicoes() -> InlineKeyboardMarkup:
    botoes = [
        [InlineKeyboardButton(text=r, callback_data=f"refeicao:{r}")]
        for r in REFEICOES
    ]
    return InlineKeyboardMarkup(inline_keyboard=botoes)


def _teclado_confirmar_cadastro() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Confirmar", callback_data="cadastro:confirmar"),
        InlineKeyboardButton(text="✏️ Editar", callback_data="cadastro:editar"),
    ]])


def _resumo_cadastro(dados: dict) -> str:
    return (
        f"📋 {dados.get('nome') or '(nome não identificado)'}\n"
        f"Porção base: {dados.get('porcao_base_g', 0):g} g\n"
        f"Kcal: {dados.get('kcal', 0):g} | Proteína: {dados.get('proteina', 0):g}g | "
        f"Carbo: {dados.get('carbo', 0):g}g | Gordura: {dados.get('gordura', 0):g}g"
    )


# ---------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------

@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    if _aba_do_usuario(user.id) is None:
        await message.answer(
            "Seu usuário ainda não está configurado no bot. "
            f"Seu ID é {user.id} — adicione em USUARIOS no bot.py."
        )
        return
    await message.answer(
        "Bot de calorias pronto! Manda o que você comeu (ex: '100g de ovo') "
        "ou use /cadastrar pra registrar um alimento novo."
    )


@dp.message(Command("cadastrar"))
async def cmd_cadastrar(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    
    # Inicia o fluxo pedindo o nome primeiro
    _pendentes[user.id] = {"tipo": "cadastro", "dados": {}, "aguardando": "nome_inicial"}
    await message.answer("Qual é o nome do alimento que você quer cadastrar?")


# ---------------------------------------------------------------------
# Fluxo 3.1 — registro por texto (e continuação de cadastros pendentes)
# ---------------------------------------------------------------------

@dp.message(F.text)
async def handler_texto(message: Message) -> None:
    user = message.from_user
    texto = message.text
    if user is None or texto is None:
        return
    aba = _aba_do_usuario(user.id)
    if aba is None:
        return

    pendente = _pendentes.get(user.id)
    
    # PRIORIDADE: Tratamento de fluxos de cadastro em andamento
    if pendente is not None and pendente.get("tipo") == "cadastro":
        aguardando = pendente.get("aguardando")
        
        if aguardando == "nome_inicial":
            pendente["dados"]["nome"] = texto.strip()
            pendente["aguardando"] = "dados_iniciais"
            await message.answer(
                f"Beleza, o nome será '{pendente['dados']['nome']}'.\n\n"
                "Agora mande a **foto da tabela nutricional** OU digite os dados "
                "(ex: '30g tem 120kcal, 24g proteina, 2 carbo, 1 gordura')."
            )
            return
            
        elif aguardando:
            await _continuar_cadastro_por_texto(message, user.id, texto, pendente)
            return

    # Se não há cadastro pendente, trata como log normal de refeição
    try:
        extraido = llm_parser.parsear_texto(texto, GEMINI_API_KEY)
    except Exception:
        logger.exception("Falha ao parsear texto")
        await message.answer("Não consegui entender. Pode reformular?")
        return

    matches = food_matcher.buscar_match(extraido["alimento"], _cache_alimentos)
    if not matches:
        await message.answer(
            f"Não encontrei \"{extraido['alimento']}\" na base. "
            "Use /cadastrar pra adicionar esse alimento."
        )
        return

    melhor_match = matches[0]
    macros = food_matcher.calcular_macros(melhor_match, extraido["quantidade"])

    registro = {
        "alimento": melhor_match["nome"],
        "quantidade": extraido["quantidade"],
        "unidade": extraido["unidade"],
        "macros": macros,
        "refeicao": extraido.get("refeicao"),
    }

    if registro["refeicao"]:
        await _salvar_e_responder(message, aba, registro)
    else:
        # Falta a refeição — guarda pendente e pergunta.
        _pendentes[user.id] = {"tipo": "log", **registro}
        await message.answer("Qual refeição foi essa?", reply_markup=_teclado_refeicoes())


async def _continuar_cadastro_por_texto(
    message: Message, user_id: int, texto: str, pendente: dict
) -> None:
    """
    Trata um texto enviado enquanto há um cadastro pendente aguardando
    dados numéricos ou edição. Reaproveita o LLM pra extrair/corrigir
    os dados e volta a mostrar o resumo com os botões de confirmação.
    """
    try:
        extraido = llm_parser.parsear_texto_cadastro(texto, GEMINI_API_KEY)
    except Exception:
        logger.exception("Falha ao parsear texto de cadastro")
        await message.answer("Não consegui entender. Pode tentar de novo?")
        return

    dados_atuais = pendente["dados"]
    # Mescla: qualquer campo que o LLM não tenha extraído (None/ausente)
    # mantém o valor que já existia, ao invés de apagar.
    for campo in _CAMPOS_CADASTRO_OBRIGATORIOS:
        valor_novo = extraido.get(campo)
        if valor_novo not in (None, ""):
            dados_atuais[campo] = valor_novo

    pendente["aguardando"] = None
    _pendentes[user_id] = pendente

    faltando = [c for c in _CAMPOS_CADASTRO_OBRIGATORIOS if not dados_atuais.get(c)]
    if faltando:
        pendente["aguardando"] = "nome" if "nome" in faltando else "edicao"
        await message.answer(
            f"Ainda falta: {', '.join(faltando)}. Pode complementar?"
        )
        return

    await message.answer(_resumo_cadastro(dados_atuais), reply_markup=_teclado_confirmar_cadastro())


@dp.callback_query(F.data.startswith("refeicao:"))
async def callback_refeicao(callback: CallbackQuery) -> None:
    user = callback.from_user
    callback_message = callback.message
    data = callback.data
    if user is None or callback_message is None or data is None:
        await callback.answer("Registro inválido, tenta de novo.")
        return
    if not isinstance(callback_message, Message):
        await callback.answer("Mensagem indisponível, tenta de novo.")
        return
    user_id = user.id
    aba = _aba_do_usuario(user_id)
    pendente = _pendentes.pop(user_id, None)

    if aba is None or pendente is None or pendente.get("tipo") != "log":
        await callback.answer("Registro expirado, tenta de novo.")
        return

    pendente["refeicao"] = data.split(":", 1)[1]
    await callback_message.delete()
    await _salvar_e_responder(callback_message, aba, pendente)
    await callback.answer()


async def _salvar_e_responder(message: Message, aba: str, registro: dict) -> None:
    agora = datetime.now()
    linha = {
        "data": agora.strftime("%d/%m"),
        "hora": agora.strftime("%H:%M"),
        "refeicao": registro["refeicao"],
        "alimento": registro["alimento"],
        "quantidade": registro["quantidade"],
        "unidade": registro["unidade"],
        **registro["macros"],
    }
    sheets_client.append_log(sheets, SPREADSHEET_ID, aba, linha)

    png = card_generator.gerar_cartao_item(
        registro["alimento"], registro["quantidade"], registro["unidade"], registro["macros"]
    )
    await message.answer_photo(photo=_bytes_para_input_file(png, "registro.png"))


# ---------------------------------------------------------------------
# Fluxo 3.3 — cadastro de alimento customizado (foto de rótulo)
# ---------------------------------------------------------------------

@dp.message(F.photo)
async def handler_foto(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    aba = _aba_do_usuario(user.id)
    if aba is None:
        return

    if not message.photo:
        return
    foto = message.photo[-1]  # maior resolução disponível
    arquivo = await bot.get_file(foto.file_id)
    if arquivo.file_path is None:
        await message.answer("Não consegui baixar a foto. Pode tentar de novo?")
        return
    imagem_bytes_io = io.BytesIO()
    await bot.download_file(arquivo.file_path, destination=imagem_bytes_io)
    imagem_bytes = imagem_bytes_io.getvalue()

    try:
        extraido = llm_parser.parsear_foto_rotulo(imagem_bytes, GEMINI_API_KEY)
    except Exception:
        logger.exception("Falha ao parsear foto de rótulo")
        await message.answer("Não consegui ler o rótulo. Pode tentar outra foto?")
        return

    if not extraido:
        await message.answer("Não consegui ler os dados do rótulo. Pode tentar outra foto?")
        return

    # Se já tínhamos um nome pendente guardado no passo anterior, mescla agora
    pendente_atual = _pendentes.get(user.id, {})
    nome_salvo = pendente_atual.get("dados", {}).get("nome")
    if nome_salvo and not extraido.get("nome"):
        extraido["nome"] = nome_salvo

    campos_numericos = ("porcao_base_g", "kcal", "proteina", "carbo", "gordura")
    if any(campo not in extraido or extraido[campo] is None for campo in campos_numericos):
        await message.answer("O rótulo não trouxe todos os dados necessários. Pode tentar outra foto?")
        return

    aguardando = "nome" if not extraido.get("nome") else None
    _pendentes[user.id] = {"tipo": "cadastro", "dados": extraido, "aguardando": aguardando}

    if aguardando == "nome":
        await message.answer("Não peguei o nome do produto. Digite o nome:")
        return

    await message.answer(_resumo_cadastro(extraido), reply_markup=_teclado_confirmar_cadastro())


@dp.callback_query(F.data == "cadastro:confirmar")
async def callback_confirmar_cadastro(callback: CallbackQuery) -> None:
    user = callback.from_user
    callback_message = callback.message
    if user is None or callback_message is None:
        await callback.answer("Cadastro inválido, manda a foto de novo.")
        return
    if not isinstance(callback_message, Message):
        await callback.answer("Mensagem indisponível, manda a foto de novo.")
        return
    user_id = user.id
    pendente = _pendentes.pop(user_id, None)

    if pendente is None or pendente.get("tipo") != "cadastro":
        await callback.answer("Cadastro expirado, manda a foto de novo.")
        return

    dados = pendente["dados"]
    faltando = [c for c in _CAMPOS_CADASTRO_OBRIGATORIOS if not dados.get(c)]
    if faltando:
        pendente["aguardando"] = "nome" if "nome" in faltando else "edicao"
        _pendentes[user_id] = pendente
        await callback_message.answer(f"Ainda falta: {', '.join(faltando)}. Pode complementar?")
        await callback.answer()
        return

    sheets_client.append_alimento_customizado(sheets, SPREADSHEET_ID, dados)
    _cache_alimentos.append({**dados, "fonte": "Custom"})

    await callback_message.edit_text((callback_message.text or "") + "\n\n✅ Cadastrado!")
    await callback.answer()


@dp.callback_query(F.data == "cadastro:editar")
async def callback_editar_cadastro(callback: CallbackQuery) -> None:
    user = callback.from_user
    callback_message = callback.message
    if user is None or callback_message is None or not isinstance(callback_message, Message):
        await callback.answer("Mensagem indisponível, tenta de novo.")
        return

    pendente = _pendentes.get(user.id)
    if pendente is None or pendente.get("tipo") != "cadastro":
        await callback.answer("Cadastro expirado, manda a foto de novo.")
        return

    pendente["aguardando"] = "edicao"
    _pendentes[user.id] = pendente

    await callback_message.answer(
        "Manda os dados corrigidos em texto livre (ex: 'porção 32g, 190kcal, "
        "8g proteína, 6g carbo, 16g gordura'). Só precisa mencionar o que quer mudar."
    )
    await callback.answer()


def _bytes_para_input_file(dados: bytes, nome_arquivo: str):
    from aiogram.types import BufferedInputFile
    return BufferedInputFile(dados, filename=nome_arquivo)


# ---------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------

async def _atualizar_cache_periodicamente() -> None:
    while True:
        await asyncio.sleep(3600)  # 1h
        try:
            global _cache_alimentos
            _cache_alimentos = sheets_client.carregar_cache_alimentos(sheets, SPREADSHEET_ID)
            logger.info("Cache de alimentos recarregado (%d itens)", len(_cache_alimentos))
        except Exception:
            logger.exception("Falha ao recarregar cache de alimentos")


async def main() -> None:
    global _cache_alimentos
    _cache_alimentos = sheets_client.carregar_cache_alimentos(sheets, SPREADSHEET_ID)
    logger.info("Cache inicial carregado (%d itens)", len(_cache_alimentos))

    asyncio.create_task(_atualizar_cache_periodicamente())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())