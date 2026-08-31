"""
sheets_client.py

Único módulo do projeto que conhece a biblioteca gspread. Todo o resto
do bot interage com o Google Sheets exclusivamente através das funções
públicas deste arquivo.

Contrato completo em CONTRATOS.md.
"""

from __future__ import annotations

import gspread
from google.oauth2.service_account import Credentials
from gspread.utils import ValueInputOption

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

_ABA_ALIMENTOS = "Alimentos"

# Ordem das colunas nas abas de log (Eu / Amiga)
_COLUNAS_LOG = [
    "data", "hora", "refeicao", "alimento",
    "quantidade", "unidade", "kcal", "proteina", "carbo", "gordura",
]

# Ordem das colunas na aba "Alimentos"
_COLUNAS_ALIMENTOS = [
    "nome", "fonte", "porcao_base_g",
    "kcal", "proteina", "carbo", "gordura", "fibra",
]


def get_client(credentials_path: str) -> gspread.Client:
    """
    Autentica com a conta de serviço (arquivo JSON baixado do Google
    Cloud) e retorna um cliente gspread pronto pra uso.

    Levanta FileNotFoundError se o caminho não existir, e
    google.auth.exceptions.RefreshError se as credenciais forem inválidas.
    """
    creds = Credentials.from_service_account_file(credentials_path, scopes=_SCOPES)
    return gspread.authorize(creds)


def append_log(
    client: gspread.Client,
    spreadsheet_id: str,
    aba: str,
    linha: dict,
) -> None:
    """
    Adiciona uma linha ao final da aba de log (ex: "Eu" ou "Amiga").

    linha deve conter as chaves: data, hora, refeicao, alimento,
    quantidade, unidade, kcal, proteina, carbo, gordura.

    Levanta KeyError se alguma chave obrigatória estiver faltando.
    """
    valores = [linha[coluna] for coluna in _COLUNAS_LOG]
    planilha = client.open_by_key(spreadsheet_id)
    worksheet = planilha.worksheet(aba)
    worksheet.append_row(valores, value_input_option=ValueInputOption.user_entered)


def carregar_cache_alimentos(client: gspread.Client, spreadsheet_id: str) -> list[dict]:
    """
    Lê a aba "Alimentos" inteira e retorna como lista de dicionários,
    um por linha, com as chaves: nome, fonte, porcao_base_g, kcal,
    proteina, carbo, gordura, fibra.

    Deve ser chamado no startup do bot e periodicamente (ex: a cada 1h,
    via job agendado no próprio bot.py) pra refletir edições manuais
    feitas direto na planilha.
    """
    planilha = client.open_by_key(spreadsheet_id)
    worksheet = planilha.worksheet(_ABA_ALIMENTOS)
    registros = worksheet.get_all_records()  # já usa a 1ª linha como header

    cache = []
    for registro in registros:
        cache.append({
            "nome": str(registro.get("nome", "")).strip(),
            "fonte": str(registro.get("fonte", "")).strip(),
            "porcao_base_g": float(registro.get("porcao_base_g") or 0),
            "kcal": float(registro.get("kcal") or 0),
            "proteina": float(registro.get("proteina") or 0),
            "carbo": float(registro.get("carbo") or 0),
            "gordura": float(registro.get("gordura") or 0),
            "fibra": float(registro.get("fibra") or 0),
        })
    return cache


def append_alimento_customizado(
    client: gspread.Client,
    spreadsheet_id: str,
    alimento: dict,
) -> None:
    """
    Adiciona uma linha na aba "Alimentos" com fonte fixa em "Custom".

    alimento deve conter idealmente: nome, porcao_base_g, kcal, proteina,
    carbo, gordura, fibra (sem a chave 'fonte' — ela é definida aqui).
    Campos numéricos ausentes (ex: fibra não informada pelo LLM) viram 0
    em vez de quebrar a escrita.
    """
    linha = {**alimento, "fonte": "Custom"}
    valores = [linha.get(coluna, 0 if coluna not in ("nome", "fonte") else "") for coluna in _COLUNAS_ALIMENTOS]

    planilha = client.open_by_key(spreadsheet_id)
    worksheet = planilha.worksheet(_ABA_ALIMENTOS)
    worksheet.append_row(valores, value_input_option=ValueInputOption.user_entered)


def importar_alimentos_em_lote(
    client: gspread.Client,
    spreadsheet_id: str,
    alimentos: list[dict],
) -> None:
    """
    Adiciona várias linhas de uma vez na aba "Alimentos" (usado pelo
    script de import inicial da TACO). Muito mais eficiente que chamar
    append_alimento_customizado em loop — evita centenas de chamadas
    de API separadas e o risco de esbarrar em rate limit.

    Cada item de `alimentos` deve conter: nome, fonte, porcao_base_g,
    kcal, proteina, carbo, gordura, fibra.
    """
    linhas = [[item[coluna] for coluna in _COLUNAS_ALIMENTOS] for item in alimentos]

    planilha = client.open_by_key(spreadsheet_id)
    worksheet = planilha.worksheet(_ABA_ALIMENTOS)
    worksheet.append_rows(linhas, value_input_option=ValueInputOption.user_entered)