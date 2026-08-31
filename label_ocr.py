"""
label_ocr.py

Extração de dados nutricionais via OCR (Tesseract) + regras de texto e
sanitização de colunas de tabelas nutricionais brasileiras (RDC 429).
"""

from __future__ import annotations

import io
import re

import pytesseract
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

# Descomente e ajuste caso o Tesseract não esteja no PATH global:
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

_TESSERACT_CONFIG = "--psm 6 -c preserve_interword_spaces=1"

# Padrões para localizar a linha de cada nutriente
_LINHAS_NUTRIENTES = {
    "kcal": r"valor\s*energ[eé]tico",
    "carbo": r"carboidratos?",
    "proteina": r"prote[ií]nas?",
    "gordura": r"gorduras?\s*totais?",
    "fibra": r"fibras?\s*aliment[ae]res?",
}


def _preprocessar(imagem: Image.Image) -> Image.Image:
    """
    Pré-processamento inteligente: só faz upscale agressivo se a imagem 
    for pequena. Em imagens grandes, filtros pesados destroem a leitura.
    """
    imagem = imagem.convert("L")
    largura, altura = imagem.size
    
    # Se a foto for pequena (ex: cortada ou muito longe), aplicamos os filtros pesados
    if largura < 800:
        imagem = imagem.resize((largura * 2, altura * 2), Image.Resampling.LANCZOS)
        imagem = ImageOps.autocontrast(imagem, cutoff=2)
        enhancer = ImageEnhance.Contrast(imagem)
        imagem = enhancer.enhance(1.8)
        imagem = imagem.filter(ImageFilter.SHARPEN)
    else:
        # Se já for grande e legível, só um contraste básico resolve
        imagem = ImageOps.autocontrast(imagem, cutoff=1)
        
    return imagem


def extrair_texto_bruto(imagem_bytes: bytes) -> str:
    """Retorna o texto cru do OCR para depuração."""
    imagem = _preprocessar(Image.open(io.BytesIO(imagem_bytes)))
    return pytesseract.image_to_string(imagem, lang="por", config=_TESSERACT_CONFIG)


def _sanitizar_linha_tabela(linha: str) -> str:
    """
    Corrige ruídos clássicos do OCR em grades de tabelas antes do parsing:
    - Remove unidades como '(g)', '(9)', '(kcal)', '(mg)' que confundem dígitos.
    - Troca divisores verticais '|', '/', '\\' por espaços.
    - Converte 'O'/'o' em '0' quando colados a números (ex: '1O' -> '10').
    - Converte 'I'/'l' em '1' quando colados a números (ex: 'I2' -> '12').
    """
    texto = linha.lower()

    # Remove unidades e leituras comuns de '(g)' que viram '(9)'
    texto = re.sub(r"\((?:g|9|mg|kcal|%vd\*?)\)", " ", texto)

    # Remove barras da tabela
    texto = texto.replace("|", " ").replace("/", " ").replace("\\", " ")

    # Correções de caracteres alfabéticos em posições numéricas
    texto = re.sub(r"(?<=\d)[oO](?=\d|\b)", "0", texto)  # 1O -> 10
    texto = re.sub(r"(?<=\b)[oO](?=\d)", "0", texto)      # O5 -> 05
    texto = re.sub(r"(?<=\b)[lI!](?=\d)", "1", texto)     # I2 -> 12

    return texto


def extrair_dados_rotulo(imagem_bytes: bytes) -> dict | None:
    """
    Extrai os valores da coluna de 100g via OCR.
    """
    try:
        texto_bruto = extrair_texto_bruto(imagem_bytes)
    except Exception as e:
        print(f">>> ERRO CRÍTICO NO OCR: {e}")
        return None

    linhas = texto_bruto.splitlines()
    # Inicializa tudo como None (se o OCR não achar, o bot vai botar ***)
    valores: dict[str, float | None] = {
        "kcal": None,
        "carbo": None,
        "proteina": None,
        "gordura": None,
        "fibra": None,
    }

    for linha in linhas:
        linha_limpa = _sanitizar_linha_tabela(linha)

        for nutriente, padrao in _LINHAS_NUTRIENTES.items():
            if re.search(padrao, linha_limpa):
                partes = re.split(padrao, linha_limpa, maxsplit=1)
                if len(partes) > 1:
                    resto = partes[1]
                    numeros = re.findall(r"\d+(?:[.,]\d+)?", resto)
                    if numeros:
                        valor_str = numeros[0].replace(",", ".")
                        try:
                            valores[nutriente] = float(valor_str)
                        except ValueError:
                            pass

    # Validação mínima: se não achou as 3 coisas principais, a leitura falhou 100%
    if valores["kcal"] is None and valores["carbo"] is None and valores["proteina"] is None:
        return None

    return {
        "porcao_base_g": 100.0,
        "kcal": valores["kcal"],
        "proteina": valores["proteina"],
        "carbo": valores["carbo"],
        "gordura": valores["gordura"],
        "fibra": valores["fibra"] if valores["fibra"] is not None else 0.0,
    }