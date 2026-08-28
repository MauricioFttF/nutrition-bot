"""
food_matcher.py

Lógica pura de comparação de texto e cálculo proporcional de macros.
Não depende de nenhuma biblioteca externa (usa apenas difflib, da
biblioteca padrão do Python) e não conhece Sheets, Telegram ou LLM.

Contrato completo em CONTRATOS.md.
"""

from __future__ import annotations

import difflib
import unicodedata

_LIMIAR_MINIMO = 0.5  # abaixo disso, não é considerado match válido
_MAX_RESULTADOS = 5


def _normalizar(texto: str) -> str:
    """Minúsculas e sem acento, pra comparação ser mais tolerante."""
    sem_acento = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return sem_acento.lower().strip()


def buscar_match(nome_extraido: str, cache: list[dict]) -> list[dict]:
    """
    Retorna até 5 itens do cache mais parecidos com nome_extraido,
    ordenados do melhor pro pior. Em empate de similaridade, itens com
    fonte "Custom" vêm antes dos da fonte "TACO"/"USDA".

    Retorna lista vazia se nada ultrapassar o limiar mínimo de
    similaridade — o chamador (bot.py) deve tratar isso como
    "não encontrei, pode cadastrar ou tentar de novo?".
    """
    alvo = _normalizar(nome_extraido)

    candidatos = []
    for item in cache:
        score = difflib.SequenceMatcher(None, alvo, _normalizar(item["nome"])).ratio()
        if score >= _LIMIAR_MINIMO:
            candidatos.append((score, item))

    candidatos.sort(
        key=lambda par: (par[0], par[1].get("fonte") == "Custom"),
        reverse=True,
    )

    return [item for _score, item in candidatos[:_MAX_RESULTADOS]]


def calcular_macros(item_cache: dict, quantidade: float) -> dict:
    """
    Calcula macros proporcionais à quantidade informada, a partir da
    porcao_base_g do item (ex: TACO usa base de 100g).

    Retorna {"kcal": float, "proteina": float, "carbo": float, "gordura": float}

    Levanta ValueError se porcao_base_g for zero ou ausente.
    """
    base = item_cache.get("porcao_base_g")
    if not base:
        raise ValueError(
            f"Item '{item_cache.get('nome')}' não tem porcao_base_g válida."
        )

    fator = quantidade / base

    return {
        "kcal": round(item_cache["kcal"] * fator, 1),
        "proteina": round(item_cache["proteina"] * fator, 1),
        "carbo": round(item_cache["carbo"] * fator, 1),
        "gordura": round(item_cache["gordura"] * fator, 1),
    }