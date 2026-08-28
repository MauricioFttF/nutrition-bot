# Bot de Contagem de Calorias e Macros — Especificação Técnica

## 1. Visão Geral

Bot no Telegram para registro automático de calorias e macronutrientes via mensagem de texto ou foto, com armazenamento em Google Planilhas (fonte de verdade dos dados, editável manualmente). Uso pessoal, 2 usuários, custo zero.

**Stack:**
- **Bot**: Python + `aiogram` (Telegram Bot API)
- **Parsing de texto/visão**: Google Gemini API (free tier)
- **Armazenamento**: Google Sheets API (`gspread`) — sem banco de dados local
- **Geração de cartão visual**: Pillow (PNG enviado como foto)
- **Hospedagem**: Oracle Cloud Always Free (ou polling local/Raspberry Pi)

---

## 2. Estrutura da Planilha (Google Sheets)

Uma planilha, múltiplas abas:

### Aba "Eu" e Aba "Amiga" (log contínuo, uma linha por item registrado)

| Data | Hora | Refeição | Alimento | Quantidade | Unidade | Kcal | Proteína | Carbo | Gordura |
|---|---|---|---|---|---|---|---|---|---|
| 27/08 | 08:15 | Café da manhã | Ovo | 100 | g | 155 | 13 | 1.1 | 11 |

- Bot sempre **adiciona linha nova** (append) — nunca edita estrutura existente
- Refeição é perguntada ao usuário via inline keyboard (Café da manhã / Almoço / Lanche / Janta / Outro) quando não fica claro pelo horário
- Edição manual pelo usuário é permitida e esperada

### Aba "Alimentos" (base nutricional — TACO + cadastros customizados)

| Nome | Fonte | Porção base (g) | Kcal | Proteína | Carbo | Gordura | Fibra |
|---|---|---|---|---|---|---|---|
| Ovo | TACO | 100 | 155 | 13 | 1.1 | 11 | 0 |
| Whey Growth (marca X) | Custom | 30 | 120 | 24 | 2 | 1 | 0 |

- Populada inicialmente com import do TACO (CSV)
- Bot adiciona linha nova quando usuário cadastra alimento via foto de rótulo ou texto livre
- Bot carrega essa aba em memória (cache local) para fazer o matching — evita chamada à API a cada busca

### Aba "Resumo" (calculada via fórmulas, não escrita pelo bot)

- Reproduz o formato do print de referência (Ideal / Prescrito / Real / Diferença por refeição)
- Usa `SOMASES` filtrando data + refeição nas abas "Eu"/"Amiga"
- Campo de data no topo (dropdown ou input) define o dia exibido
- Atualiza sozinha conforme o log cresce — bot nunca precisa tocar nela

---

## 3. Fluxos do Bot

### 3.1 Registro por texto
```
Usuário manda "comi 100g de ovo no café da manhã"
  → Gemini extrai JSON: {alimento, quantidade, unidade, refeição (se mencionada)}
  → Bot busca match no cache de "Alimentos" (custom_foods tem prioridade sobre TACO)
  → Se não achar match confiável → bot pergunta via inline keyboard (lista de opções próximas)
  → Se refeição não foi mencionada → bot pergunta via inline keyboard
  → Calcula macros proporcionais à quantidade
  → Append na aba do usuário (Eu/Amiga)
  → Gera cartão PNG (Pillow) com resumo do item + responde
```

### 3.2 Registro por foto de comida
```
Usuário manda foto do prato
  → Gemini Vision estima itens + porções aproximadas
  → Bot mostra estimativa com botões "Confirmar" / "Editar quantidade" / "Corrigir item"
  → Ao confirmar → segue mesmo fluxo de cálculo e append do 3.1
```

### 3.3 Cadastro de alimento customizado (foto de rótulo ou texto)
```
Comando /cadastrar, ou foto de rótulo nutricional
  → Gemini Vision (foto) ou Gemini (texto) extrai: nome, porção base, kcal, proteína, carbo, gordura
  → Bot mostra resumo extraído com botões "Confirmar" / "Editar"
  → Ao confirmar → append na aba "Alimentos" (fonte = "Custom")
  → Cache local é atualizado
```

### 3.4 Consulta rápida
```
Comando /hoje → bot lê aba "Resumo" (ou soma direto do log do dia) e responde com cartão do dia
```

---

## 4. Estrutura do Projeto

```
nutrition-bot/
├── bot.py                 # entrypoint, handlers do Telegram (aiogram)
├── sheets_client.py        # conexão gspread: append, leitura, cache de "Alimentos"
├── llm_parser.py           # chamadas Gemini (texto e visão) → JSON estruturado
├── food_matcher.py         # fuzzy match entre texto extraído e cache de "Alimentos"
├── card_generator.py       # Pillow: gera cartão PNG de resposta
├── data/
│   └── taco_import.csv     # base TACO para import inicial na aba "Alimentos"
├── credentials/
│   └── service_account.json # chave da conta de serviço do Google (não versionar)
└── requirements.txt
```

---

## 5. Setup necessário (checklist)

1. Criar bot no Telegram via @BotFather → obter token
2. Criar projeto no Google Cloud → ativar Google Sheets API → gerar conta de serviço → baixar JSON
3. Criar planilha com as abas descritas → compartilhar com e-mail da conta de serviço (permissão de edição)
4. Obter chave da API do Gemini (Google AI Studio)
5. Importar TACO (CSV) para a aba "Alimentos" (script único de setup)
6. Configurar hospedagem (Oracle Cloud free tier ou execução local)

---

## 6. Próximos passos de implementação

- [ ] `sheets_client.py` — funções de append e leitura/cache
- [ ] `llm_parser.py` — prompt de extração de texto (definir formato exato do JSON)
- [ ] `food_matcher.py` — lógica de fuzzy match
- [ ] `bot.py` — handlers básicos (mensagem de texto → fluxo 3.1)
- [ ] `card_generator.py` — layout do cartão visual
- [ ] Fluxo de foto (3.2) e cadastro (3.3) — depois do fluxo de texto validado
