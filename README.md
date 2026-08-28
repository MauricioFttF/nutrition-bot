# 🤖 Nutrition Bot (Telegram + Gemini + Google Sheets)

Bot de Telegram inteligente desenvolvido em Python para registro automatizado de refeições e gerenciamento de tabela nutricional pessoal, utilizando Inteligência Artificial (Google Gemini) e integração em tempo real com o Google Planilhas.

---

## 🚀 Funcionalidades

* **Registro por Texto (NLP):** Envie mensagens naturais como *"comi 100g de arroz cozido"* ou *"almocei 200g de frango"*. O bot interpreta os dados, cruza com a tabela nutricional e registra a linha formatada.
* **Geração de Cartão Nutricional:** Cria e envia automaticamente uma imagem resumo com os macros da refeição registrada.
* **Cadastro de Alimentos Customizados (Fluxo em Etapas):**
  1. Comando `/cadastrar` para iniciar o fluxo.
  2. Informa o nome do produto.
  3. Envia a foto da tabela nutricional (rótulo) ou descreve os dados por texto livre para extração automática via LLM.
* **Sincronização com Google Sheets:** Logs de consumo salvos automaticamente em abas individualizadas por usuário, com leitura eficiente baseada em valores crus (`UNFORMATTED_VALUE`) para evitar distorções de escala numérica.
* **Multi-usuário:** Suporte a múltiplos perfis mapeados por IDs do Telegram.

---

## 🛠️ Tecnologias Utilizadas

* **Python 3.10+**
* **Aiogram 3.x** (Framework assíncrono para bots do Telegram)
* **Google GenAI SDK** (`google-genai` para integração com o modelo Gemini)
* **Gspread** (Manipulação da API do Google Sheets)
* **Pillow (PIL)** (Geração dinâmica dos cartões de macros em imagem)

---

## ⚙️ Configuração e Instalação

1. **Clone o repositório:**
   ```bash
   git clone [https://github.com/MauricioFttF/nutrition-bot.git](https://github.com/MauricioFttF/nutrition-bot.git)
   cd nutrition-bot