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

_LIMIAR_MINIMO = 0.34  # abaixo disso, não é considerado match válido
_MAX_RESULTADOS = 5

# Palavras comuns demais pra carregar significado na comparação — se
# não ignoradas, causam falsos positivos (ex: "com sal" aparecendo em
# "macarrão com salsicha" E em "manteiga com sal" infla a similaridade
# mesmo sendo alimentos completamente diferentes).
_STOPWORDS = {"com", "de", "do", "da", "sem", "e", "a", "o", "em", "ao", "no", "na"}


def _normalizar(texto: str) -> str:
    """Minúsculas e sem acento, pra comparação ser mais tolerante."""
    sem_acento = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return sem_acento.lower().strip()


def _tokens(texto: str) -> set[str]:
    """Palavras normalizadas, sem stopwords e sem palavras de 1 letra."""
    return {
        palavra
        for palavra in _normalizar(texto).split()
        if palavra not in _STOPWORDS and len(palavra) > 1
    }


def buscar_match(nome_extraido: str, cache: list[dict]) -> list[dict]:
    """
    Retorna até 5 itens do cache mais parecidos com nome_extraido,
    ordenados do melhor pro pior. Em empate de similaridade, itens com
    fonte "Custom" vêm antes dos da fonte "TACO"/"USDA".

    A pontuação prioriza sobreposição de PALAVRAS-CHAVE (quantas das
    palavras do que o usuário disse aparecem no nome do item), com a
    similaridade de caracteres como critério secundário de desempate —
    isso evita que palavras comuns (ex: "com sal") produzam matches
    errados entre alimentos completamente diferentes.

    Retorna lista vazia se nada ultrapassar o limiar mínimo — o
    chamador (bot.py) deve tratar isso como "não encontrei, pode
    cadastrar ou tentar de novo?".
    """
    alvo_tokens = _tokens(nome_extraido)
    alvo_norm = _normalizar(nome_extraido)

    candidatos = []
    for item in cache:
        item_tokens = _tokens(item["nome"])
        item_norm = _normalizar(item["nome"])

        if alvo_tokens:
            recall = len(alvo_tokens & item_tokens) / len(alvo_tokens)
        else:
            recall = 0.0

        char_ratio = difflib.SequenceMatcher(None, alvo_norm, item_norm).ratio()

        # Recall de palavras pesa mais; char_ratio só desempata entre
        # itens com sobreposição de palavras parecida.
        score = (0.75 * recall) + (0.25 * char_ratio)

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