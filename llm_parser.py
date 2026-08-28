"""
llm_parser.py

Único módulo do projeto que conhece a API do Gemini. Todo o resto do
bot recebe apenas dicts/listas já estruturados — nunca lida com prompt
ou resposta bruta do modelo.

Se quiser trocar de provedor de LLM no futuro, só este arquivo muda.

Contrato completo em CONTRATOS.md.
"""

from __future__ import annotations

import json
from typing import cast

from google import genai
from google.genai import types

_MODELO = "gemini-3.6-flash"

_PROMPT_TEXTO = """Extraia informações de registro alimentar da mensagem abaixo.

Responda APENAS com um JSON no formato exato:
{{"alimento": "<nome do alimento>", "quantidade": <número>, "unidade": "<g|ml|unidade|fatia|xicara|colher>", "refeicao": "<cafe da manha|almoco|lanche|janta|null>"}}

Regras:
- "refeicao" deve ser null se a mensagem não deixar claro qual refeição é.
- "quantidade" é sempre um número (use 1 se for algo como "uma banana").
- Não inclua texto fora do JSON.

Mensagem: "{mensagem}"
"""

_PROMPT_FOTO_COMIDA = """Analise a foto de comida e liste os itens visíveis com
estimativa de quantidade.

Responda APENAS com um JSON no formato exato:
[{"alimento": "<nome>", "quantidade_estimada": <número>, "unidade": "<g|ml|unidade>"}, ...]

Seja conservador nas estimativas de porção. Não inclua texto fora do JSON."""

_PROMPT_FOTO_ROTULO = """Extraia os dados da tabela nutricional na foto do rótulo.

Responda APENAS com um JSON no formato exato:
{"nome": "<nome do produto, se visível, senão string vazia>",
 "porcao_base_g": <número, a porção de referência da tabela>,
 "kcal": <número>, "proteina": <número>, "carbo": <número>,
 "gordura": <número>, "fibra": <número, use 0 se não constar>}

Não inclua texto fora do JSON."""

_PROMPT_TEXTO_CADASTRO = """Extraia dados de cadastro de alimento da mensagem abaixo.
A mensagem pode ser uma descrição livre (ex: "whey growth, 30g tem 120kcal,
24g proteina, 2g carbo, 1g gordura") ou uma correção de valores já existentes.

Responda APENAS com um JSON no formato exato:
{{"nome": "<nome do produto>",
 "porcao_base_g": <número, a porção de referência mencionada>,
 "kcal": <número>, "proteina": <número>, "carbo": <número>,
 "gordura": <número>, "fibra": <número, use 0 se não mencionado>}}

Não inclua texto fora do JSON.

Mensagem: "{mensagem}"
"""


def _client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def _extrair_json(texto_resposta: str) -> dict | list:
    """
    Remove cercas de código (```json ... ```) que o modelo às vezes
    adiciona mesmo quando instruído a não fazer, e faz o parse.
    """
    limpo = texto_resposta.strip()
    if limpo.startswith("```"):
        limpo = limpo.split("```")[1]
        if limpo.startswith("json"):
            limpo = limpo[4:]
    return json.loads(limpo.strip())


def _parte_imagem(imagem_bytes: bytes) -> types.Part:
    return types.Part.from_bytes(data=imagem_bytes, mime_type="image/jpeg")


def parsear_texto(mensagem: str, api_key: str) -> dict:
    """
    Envia a mensagem do usuário pro Gemini e retorna:
    {"alimento": str, "quantidade": float, "unidade": str,
     "refeicao": str | None}

    Levanta json.JSONDecodeError se o modelo não retornar um JSON válido
    (chamador deve tratar como "não entendi, pode reformular?").
    """
    prompt = _PROMPT_TEXTO.format(mensagem=mensagem)
    # IMPORTANTE: manter o client numa variável antes de chamar
    # generate_content. Usá-lo como objeto temporário numa linha só
    # (_client(api_key).models.generate_content(...)) reintroduz um bug
    # conhecido do google-genai onde o httpx interno fecha antes da
    # requisição terminar (RuntimeError: "client has been closed").
    cliente = _client(api_key)
    resposta = cliente.models.generate_content(
        model=_MODELO,
        contents=prompt,
    )
    return cast(dict, _extrair_json(resposta.text or ""))


def parsear_foto_comida(imagem_bytes: bytes, api_key: str) -> list[dict]:
    """
    Retorna lista de itens estimados a partir de uma foto de refeição:
    [{"alimento": str, "quantidade_estimada": float, "unidade": str}, ...]
    """
    cliente = _client(api_key)
    resposta = cliente.models.generate_content(
        model=_MODELO,
        contents=[
            _parte_imagem(imagem_bytes),
            _PROMPT_FOTO_COMIDA,
        ],
    )
    return cast(list[dict], _extrair_json(resposta.text or ""))


def parsear_foto_rotulo(imagem_bytes: bytes, api_key: str) -> dict:
    """
    Extrai dados nutricionais de uma foto de rótulo/tabela nutricional:
    {"nome": str, "porcao_base_g": float, "kcal": float,
     "proteina": float, "carbo": float, "gordura": float, "fibra": float}
    """
    cliente = _client(api_key)
    resposta = cliente.models.generate_content(
        model=_MODELO,
        contents=[
            _parte_imagem(imagem_bytes),
            _PROMPT_FOTO_ROTULO,
        ],
    )
    return cast(dict, _extrair_json(resposta.text or ""))


def parsear_texto_cadastro(mensagem: str, api_key: str) -> dict:
    """
    Extrai dados de cadastro de alimento a partir de texto livre — usado
    tanto pra completar um nome faltante quanto pra corrigir/editar
    valores de um cadastro pendente.

    Retorna {"nome": str, "porcao_base_g": float, "kcal": float,
     "proteina": float, "carbo": float, "gordura": float, "fibra": float}
    """
    prompt = _PROMPT_TEXTO_CADASTRO.format(mensagem=mensagem)
    cliente = _client(api_key)
    resposta = cliente.models.generate_content(
        model=_MODELO,
        contents=prompt,
    )
    return cast(dict, _extrair_json(resposta.text or ""))