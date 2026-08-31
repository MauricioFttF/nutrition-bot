"""
bot.py

Entrypoint e orquestrador. Único módulo que conhece todos os outros
(sheets_client, llm_parser, food_matcher, card_generator, label_ocr) e
a API do Telegram (via aiogram).

Fluxos implementados (ver nutrition-bot-spec.md, seção 3):
  3.1 — Registro por texto
  3.2 — Registro por foto de comida (estimativa + confirmar/cancelar)
  3.3 — Cadastro de alimento customizado: /cadastrar pergunta o nome
        primeiro, depois pede foto do rótulo (OCR primeiro, Gemini como
        plano B) OU texto com os dados nutricionais.
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
import label_ocr
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

# TESTE DE OCR EM ANDAMENTO: com False, se o OCR não conseguir extrair
# todos os campos, o bot avisa e NÃO chama o Gemini como plano B —
# assim dá pra testar/ajustar o OCR isoladamente sem gastar cota da
# API. Volte pra True quando terminar de validar o OCR (restaura o
# comportamento normal: OCR primeiro, Gemini como plano B automático).
_PERMITIR_FALLBACK_GEMINI_FOTO = False

_CAMPOS_CADASTRO_OBRIGATORIOS = ("nome", "porcao_base_g", "kcal", "proteina", "carbo", "gordura")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
sheets = sheets_client.get_client(CREDENTIALS_PATH)

# Cache em memória da aba "Alimentos". Recarregado no startup e a cada
# 1h por um job em background (ver main()).
_cache_alimentos: list[dict] = []

# Estado temporário por usuário. Formatos possíveis:
#   {"tipo": "log", ...}
#       — registro por texto aguardando escolha de refeição (3.1)
#   {"tipo": "log_foto", "itens": [...], "nao_encontrados": [...]}
#       — foto de comida estimada, aguardando confirmar/cancelar (3.2)
#   {"tipo": "cadastro", "estado": "aguardando_nome", "dados": {}}
#       — /cadastrar foi chamado, esperando o nome do produto
#   {"tipo": "cadastro", "estado": "aguardando_dados", "dados": {"nome": ...}}
#       — nome já informado, esperando foto do rótulo ou texto com dados
#   {"tipo": "cadastro", "estado": "aguardando_edicao", "dados": {...}}
#       — resumo já mostrado, usuário apertou "Editar"
# Suficiente pra 2 usuários; se o projeto crescer, trocar por FSM do
# próprio aiogram.
# Usuários que pediram /debug_ocr — a próxima foto que mandarem mostra
# o texto bruto do OCR em vez de seguir o fluxo normal. Reseta sozinho
# depois de uma foto (modo "de uma vez só").
_debug_ocr_ativo: set[int] = set()

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


def _teclado_confirmar_foto() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Confirmar", callback_data="logfoto:confirmar"),
        InlineKeyboardButton(text="❌ Cancelar", callback_data="logfoto:cancelar"),
    ]])


def _resumo_cadastro(dados: dict) -> str:
    def _fmt(chave: str) -> str:
        v = dados.get(chave)
        # Se for None, coloca ***. Se for número, formata bonitinho sem casas decimais inúteis
        return f"{v:g}" if v is not None else "***"

    nome = dados.get('nome') or "***"
    return (
        f"📋 {nome}\n"
        f"Porção base: {_fmt('porcao_base_g')} g\n"
        f"Kcal: {_fmt('kcal')} | Proteína: {_fmt('proteina')}g | "
        f"Carbo: {_fmt('carbo')}g | Gordura: {_fmt('gordura')}g"
    )


def _campos_faltando(dados: dict) -> list[str]:
    # Agora só acusa que falta se for estritamente None. Zero (0.0) é um valor válido!
    return [c for c in _CAMPOS_CADASTRO_OBRIGATORIOS if dados.get(c) is None]

async def _pedir_dados_nutricionais(message: Message) -> None:
    await message.answer(
        "Agora manda a foto do rótulo/tabela nutricional, ou digite os dados \n "
        "📋 nome \n"
        f"Porção base: 100 g\n"
        f"Kcal: *** | Proteína: ***  | "
        f"Carbo: *** | Gordura: ***"
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
        "Bot de calorias pronto! Manda o que você comeu (texto ou foto do prato) "
        "ou use /cadastrar pra registrar um alimento novo."
    )


@dp.message(Command("cadastrar"))
async def cmd_cadastrar(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    _pendentes[user.id] = {"tipo": "cadastro", "estado": "aguardando_nome", "dados": {}}
    await message.answer("Qual o nome do produto?")


@dp.message(Command("debug_ocr"))
async def cmd_debug_ocr(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    _debug_ocr_ativo.add(user.id)
    await message.answer("Manda a foto — vou te mostrar o texto bruto que o OCR extraiu, sem processar nada.")


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

    # PRIORIDADE: se há um cadastro em andamento, esse texto é resposta
    # a ele — não deve virar uma nova tentativa de registro de refeição.
    pendente = _pendentes.get(user.id)
    if pendente is not None and pendente.get("tipo") == "cadastro":
        await _continuar_cadastro(message, user.id, texto, pendente, imagem_bytes=None)
        return

    try:
        extraido = llm_parser.parsear_texto(texto, GEMINI_API_KEY)
    except Exception:
        logger.exception("Falha ao parsear texto")
        await message.answer("Não consegui entender. Pode reformular?")
        return

    if not extraido.get("alimento") or extraido.get("quantidade") is None:
        await message.answer("Não consegui entender o que foi comido. Pode reformular?")
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
        await _salvar_e_responder(message, aba, [registro])
    else:
        _pendentes[user.id] = {"tipo": "log", "itens": [registro]}
        await message.answer("Qual refeição foi essa?", reply_markup=_teclado_refeicoes())


async def _continuar_cadastro(
    message: Message,
    user_id: int,
    texto: str | None,
    pendente: dict,
    imagem_bytes: bytes | None,
) -> None:
    estado = pendente.get("estado")
    dados = pendente["dados"]

    if estado == "aguardando_nome":
        if not texto:
            await message.answer("Manda o nome em texto, por favor.")
            return
        dados["nome"] = texto.strip()
        pendente["estado"] = "aguardando_dados"
        _pendentes[user_id] = pendente
        await _pedir_dados_nutricionais(message)
        return

    if estado in ("aguardando_dados", "aguardando_edicao"):
        try:
            if imagem_bytes is not None:
                extraido = label_ocr.extrair_dados_rotulo(imagem_bytes)
                logger.info("OCR resultado: %s", extraido)

                if extraido is None:
                    if not _PERMITIR_FALLBACK_GEMINI_FOTO:
                        await message.answer("⚠️ O OCR falhou ao ler a imagem. Use o molde abaixo para preencher:")
                        extraido = {}  # O segredo: passamos um dicionário vazio pra ele preencher tudo com ***
                    else:
                        extraido = llm_parser.parsear_foto_rotulo(imagem_bytes, GEMINI_API_KEY)
                    extraido = llm_parser.parsear_foto_rotulo(imagem_bytes, GEMINI_API_KEY)
            else:
                extraido = llm_parser.parsear_texto_cadastro(texto or "", GEMINI_API_KEY)
        except Exception:
            logger.exception("Falha ao parsear dados de cadastro")
            await message.answer("Não consegui ler os dados. Pode tentar de novo?")
            return

        nome_original = dados.get("nome")
        
        # Atualiza os dados com o que foi encontrado (ignorando o que veio vazio)
        for campo in _CAMPOS_CADASTRO_OBRIGATORIOS:
            valor_novo = (extraido or {}).get(campo)
            if valor_novo is not None:
                dados[campo] = valor_novo
                
        if nome_original:
            dados["nome"] = nome_original

        pendente["dados"] = dados
        # Se ainda tem "***", já deixa o bot engatilhado pra receber o seu texto copiado
        pendente["estado"] = "aguardando_edicao" if _campos_faltando(dados) else None
        _pendentes[user_id] = pendente
        
        await message.answer(_resumo_cadastro(dados), reply_markup=_teclado_confirmar_cadastro())
        return

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

    if aba is None or pendente is None or pendente.get("tipo") not in ("log", "log_foto"):
        await callback.answer("Registro expirado, tenta de novo.")
        return

    refeicao = data.split(":", 1)[1]
    for item in pendente["itens"]:
        item["refeicao"] = refeicao

    await callback_message.delete()
    await _salvar_e_responder(callback_message, aba, pendente["itens"])
    await callback.answer()


async def _salvar_e_responder(message: Message, aba: str, registros: list[dict]) -> None:
    """
    Salva um ou mais registros (um único item de texto, ou vários itens
    de uma foto de comida) e responde com um cartão. Com mais de um
    item, o cartão mostra o total combinado — cada item ainda vira uma
    linha separada na planilha.
    """
    agora = datetime.now()
    for registro in registros:
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

    if len(registros) == 1:
        r = registros[0]
        png = card_generator.gerar_cartao_item(r["alimento"], r["quantidade"], r["unidade"], r["macros"])
    else:
        totais = {"kcal": 0.0, "proteina": 0.0, "carbo": 0.0, "gordura": 0.0}
        for r in registros:
            for k in totais:
                totais[k] += r["macros"].get(k, 0)
        png = card_generator.gerar_cartao_item(
            f"Refeição ({len(registros)} itens)", len(registros), "itens", totais
        )

    await message.answer_photo(photo=_bytes_para_input_file(png, "registro.png"))


# ---------------------------------------------------------------------
# Fluxo 3.2 — registro por foto de comida (estimativa)
# ---------------------------------------------------------------------

async def _estimar_itens_da_foto(imagem_bytes: bytes) -> tuple[list[dict], list[str]]:
    """
    Retorna (itens_reconhecidos, nomes_nao_encontrados). Cada item
    reconhecido já vem com macros calculados, pronto pra salvar.
    """
    itens_estimados = llm_parser.parsear_foto_comida(imagem_bytes, GEMINI_API_KEY)

    reconhecidos = []
    nao_encontrados = []
    for item in itens_estimados:
        alimento = item.get("alimento")
        quantidade = item.get("quantidade_estimada")
        unidade = item.get("unidade", "g")
        if not alimento or quantidade is None:
            continue

        matches = food_matcher.buscar_match(alimento, _cache_alimentos)
        if not matches:
            nao_encontrados.append(alimento)
            continue

        melhor_match = matches[0]
        macros = food_matcher.calcular_macros(melhor_match, quantidade)
        reconhecidos.append({
            "alimento": melhor_match["nome"],
            "quantidade": quantidade,
            "unidade": unidade,
            "macros": macros,
        })

    return reconhecidos, nao_encontrados


def _resumo_itens_foto(itens: list[dict], nao_encontrados: list[str]) -> str:
    linhas = ["🍽️ Itens estimados na foto:\n"]
    for item in itens:
        linhas.append(
            f"• {item['alimento']} — {item['quantidade']:g}{item['unidade']} "
            f"({item['macros']['kcal']:g} kcal)"
        )
    if nao_encontrados:
        linhas.append(f"\n⚠️ Não encontrado na base: {', '.join(nao_encontrados)} "
                       "(não será salvo — cadastre depois se quiser)")
    if not itens:
        linhas.append("Nenhum item reconhecido.")
    return "\n".join(linhas)


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

    # Bloco para interceptar a foto e mostrar a leitura bruta do OCR
    if user.id in _debug_ocr_ativo:
        _debug_ocr_ativo.remove(user.id)
        texto_bruto = label_ocr.extrair_texto_bruto(imagem_bytes)
        await message.answer(f"🔎 **TEXTO BRUTO DO OCR:**\n\n```text\n{texto_bruto}\n```", parse_mode="Markdown")
        return

    pendente = _pendentes.get(user.id)

    # Se há um cadastro em andamento aguardando a foto do rótulo, a
    # foto é pro fluxo 3.3 (cadastro) — senão, é uma foto de refeição
    # pro fluxo 3.2 (estimativa).
    if pendente is not None and pendente.get("tipo") == "cadastro" and pendente.get("estado") == "aguardando_dados":
        await _continuar_cadastro(message, user.id, texto=None, pendente=pendente, imagem_bytes=imagem_bytes)
        return

    aguardando = await message.answer("Analisando a foto...")
    try:
        itens, nao_encontrados = await _estimar_itens_da_foto(imagem_bytes)
    except Exception:
        logger.exception("Falha ao estimar itens da foto")
        await aguardando.edit_text("Não consegui analisar a foto. Pode tentar de novo?")
        return

    if not itens:
        await aguardando.edit_text(_resumo_itens_foto(itens, nao_encontrados))
        return

    _pendentes[user.id] = {"tipo": "log_foto", "itens": itens, "nao_encontrados": nao_encontrados}
    await aguardando.edit_text(_resumo_itens_foto(itens, nao_encontrados))
    await message.answer("Confirma o registro?", reply_markup=_teclado_confirmar_foto())


@dp.callback_query(F.data == "logfoto:confirmar")
async def callback_confirmar_foto(callback: CallbackQuery) -> None:
    user = callback.from_user
    callback_message = callback.message
    if user is None or callback_message is None or not isinstance(callback_message, Message):
        await callback.answer("Registro inválido, manda a foto de novo.")
        return

    pendente = _pendentes.get(user.id)
    if pendente is None or pendente.get("tipo") != "log_foto":
        await callback.answer("Registro expirado, manda a foto de novo.")
        return

    await callback_message.answer("Qual refeição foi essa?", reply_markup=_teclado_refeicoes())
    await callback.answer()


@dp.callback_query(F.data == "logfoto:cancelar")
async def callback_cancelar_foto(callback: CallbackQuery) -> None:
    user = callback.from_user
    callback_message = callback.message
    if user is not None:
        _pendentes.pop(user.id, None)
    if callback_message is not None and isinstance(callback_message, Message):
        await callback_message.answer(
            "Descartado. Pode registrar os itens manualmente por texto se preferir."
        )
    await callback.answer()


# ---------------------------------------------------------------------
# Fluxo 3.3 — confirmação/edição de cadastro
# ---------------------------------------------------------------------

@dp.callback_query(F.data == "cadastro:confirmar")
async def callback_confirmar_cadastro(callback: CallbackQuery) -> None:
    user = callback.from_user
    callback_message = callback.message
    if user is None or not isinstance(callback_message, Message):
        await callback.answer("Cadastro inválido, use /cadastrar de novo.")
        return
        
    user_id = user.id
    pendente = _pendentes.get(user_id) # Usamos get primeiro pra não apagar se der erro

    if pendente is None or pendente.get("tipo") != "cadastro":
        await callback.answer("Cadastro expirado, use /cadastrar de novo.")
        return

    dados = pendente["dados"]
    if _campos_faltando(dados):
        # Se tentar confirmar com ***, ele avisa!
        await callback_message.answer("⚠️ Existem campos não identificados (***).\nCopie a mensagem acima, substitua os asteriscos pelos valores corretos e me envie!")
        await callback.answer()
        return

    # Se chegou aqui, tá tudo preenchido. Removemos dos pendentes e salvamos.
    _pendentes.pop(user_id, None)
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
        await callback.answer("Cadastro expirado, use /cadastrar de novo.")
        return

    pendente["estado"] = "aguardando_edicao"
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