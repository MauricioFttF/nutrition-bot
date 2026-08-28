# Contratos entre Módulos — Nutrition Bot

Este documento descreve a interface pública de cada módulo do projeto.
Se precisar retomar o desenvolvimento em outra sessão/modelo, cole este
arquivo + o módulo específico que quer continuar — não precisa do
histórico completo da conversa.

**Regra geral:** cada módulo só conhece o que está descrito aqui. Nenhum
módulo acessa estado interno de outro diretamente.

---

## `sheets_client.py` — ✅ implementado

Responsável por toda comunicação com o Google Sheets. Nenhum outro
módulo deve importar `gspread` diretamente.

```python
def get_client(credentials_path: str) -> gspread.Client
    """Autentica com a conta de serviço e retorna o cliente gspread."""

def append_log(client, spreadsheet_id: str, aba: str, linha: dict) -> None
    """
    Adiciona uma linha ao final da aba de log (Eu/Amiga).
    linha = {
        "data": str, "hora": str, "refeicao": str, "alimento": str,
        "quantidade": float, "unidade": str,
        "kcal": float, "proteina": float, "carbo": float, "gordura": float
    }
    """

def carregar_cache_alimentos(client, spreadsheet_id: str) -> list[dict]
    """
    Lê a aba 'Alimentos' inteira e retorna lista de dicts:
    [{"nome": str, "fonte": str, "porcao_base_g": float,
      "kcal": float, "proteina": float, "carbo": float,
      "gordura": float, "fibra": float}, ...]
    Deve ser chamado no startup do bot e re-chamado periodicamente
    (ex: a cada 1h) para refletir edições manuais na planilha.
    """

def append_alimento_customizado(client, spreadsheet_id: str, alimento: dict) -> None
    """
    Adiciona linha na aba 'Alimentos' com fonte='Custom'.
    alimento = mesmo formato do cache_alimentos, sem 'fonte'.
    """
```

**Dependências:** `gspread`, `google-auth`
**Não sabe nada sobre:** Telegram, LLM, geração de imagem

---

## `llm_parser.py` — ✅ implementado

```python
def parsear_texto(mensagem: str) -> dict
    """
    Envia texto pro Gemini e retorna:
    {"alimento": str, "quantidade": float, "unidade": str,
     "refeicao": str | None}
    'refeicao' é None se o usuário não mencionou.
    """

def parsear_foto_comida(imagem_bytes: bytes) -> list[dict]
    """
    Retorna lista de itens estimados na foto:
    [{"alimento": str, "quantidade_estimada": float, "unidade": str}, ...]
    """

def parsear_foto_rotulo(imagem_bytes: bytes) -> dict
    """
    Extrai dados nutricionais de foto de rótulo:
    {"nome": str, "porcao_base_g": float, "kcal": float,
     "proteina": float, "carbo": float, "gordura": float, "fibra": float}
    """
```

**Dependências:** biblioteca oficial do Gemini
**Não sabe nada sobre:** Sheets, Telegram, matching de alimentos

---

## `food_matcher.py` — ✅ implementado

```python
def buscar_match(nome_extraido: str, cache: list[dict]) -> list[dict]
    """
    Retorna lista ordenada por similaridade (melhor match primeiro)
    dos itens do cache que mais se parecem com nome_extraido.
    custom_foods (fonte='Custom') tem prioridade sobre TACO em empate.
    """

def calcular_macros(item_cache: dict, quantidade: float) -> dict
    """
    Calcula macros proporcionais à quantidade informada, a partir da
    porcao_base_g do item.
    Retorna {"kcal": float, "proteina": float, "carbo": float, "gordura": float}
    """
```

**Dependências:** nenhuma externa (lógica pura)
**Não sabe nada sobre:** Sheets, Telegram, LLM

---

## `card_generator.py` — ✅ implementado

```python
def gerar_cartao_item(alimento: str, quantidade: float, unidade: str,
                       macros: dict) -> bytes
    """Retorna PNG (bytes) do cartão de confirmação de um item registrado."""

def gerar_cartao_resumo_dia(user_id: str, data: str, totais: dict) -> bytes
    """Retorna PNG (bytes) do resumo do dia."""
```

**Dependências:** `Pillow`
**Não sabe nada sobre:** Sheets, Telegram, LLM

---

## `bot.py` — ✅ implementado (orquestrador)

Único módulo que conhece todos os outros. Handlers do Telegram (`aiogram`)
chamam `llm_parser` → `food_matcher` → `sheets_client` → `card_generator`,
nessa ordem, seguindo os fluxos 3.1/3.2/3.3 do documento de especificação.

Implementa os fluxos 3.1 (texto) e 3.3 (cadastro por foto de rótulo).
Fluxo 3.2 (foto de comida) ainda não foi ligado — validar 3.1 primeiro.

Configuração via variáveis de ambiente: `TELEGRAM_BOT_TOKEN`,
`GEMINI_API_KEY`, `SPREADSHEET_ID`, `GOOGLE_CREDENTIALS_PATH` (opcional).
Mapa `USUARIOS` no topo do arquivo precisa ser preenchido com os IDs
reais do Telegram de vocês dois antes de rodar (mande /start pro bot
pra descobrir seu ID, ele informa se não estiver mapeado ainda).

---

## Status geral

- [x] Especificação técnica (`nutrition-bot-spec.md`)
- [x] `sheets_client.py`
- [x] `llm_parser.py`
- [x] `food_matcher.py`
- [x] `card_generator.py`
- [x] `bot.py`
- [ ] Import inicial do TACO pra aba "Alimentos"