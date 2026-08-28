"""
card_generator.py

Único módulo do projeto que conhece Pillow. Recebe dados já
estruturados e devolve PNG em bytes — não sabe nada sobre Sheets,
Telegram ou LLM.

Contrato completo em CONTRATOS.md.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

_LARGURA = 640
_COR_FUNDO = (250, 247, 240)
_COR_TITULO = (35, 35, 35)
_COR_TEXTO = (70, 70, 70)
_COR_DESTAQUE = (46, 139, 87)  # verde
_COR_LINHA = (220, 215, 205)

_MACROS_LABELS = [
    ("kcal", "Calorias"),
    ("proteina", "Proteína (g)"),
    ("carbo", "Carboidrato (g)"),
    ("gordura", "Gordura (g)"),
]


def _fonte(tamanho: int, negrito: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """
    Tenta carregar DejaVu Sans (vem junto com o Pillow na maioria das
    instalações). Se não encontrar, cai pra fonte padrão do Pillow —
    o cartão continua funcional, só menos bonito.
    """
    caminho = "DejaVuSans-Bold.ttf" if negrito else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(caminho, tamanho)
    except OSError:
        return ImageFont.load_default()


def gerar_cartao_item(
    alimento: str,
    quantidade: float,
    unidade: str,
    macros: dict,
) -> bytes:
    """
    Gera PNG (bytes) de confirmação de um item registrado.

    macros = {"kcal": float, "proteina": float, "carbo": float, "gordura": float}
    """
    altura = 320
    img = Image.new("RGB", (_LARGURA, altura), _COR_FUNDO)
    desenho = ImageDraw.Draw(img)

    margem = 32
    y = margem

    desenho.text((margem, y), alimento, font=_fonte(30, negrito=True), fill=_COR_TITULO)
    y += 44

    quantidade_txt = f"{quantidade:g} {unidade}"
    desenho.text((margem, y), quantidade_txt, font=_fonte(20), fill=_COR_TEXTO)
    y += 40

    desenho.line((margem, y, _LARGURA - margem, y), fill=_COR_LINHA, width=2)
    y += 24

    for chave, rotulo in _MACROS_LABELS:
        valor = macros.get(chave, 0)
        cor = _COR_DESTAQUE if chave == "kcal" else _COR_TEXTO
        desenho.text((margem, y), rotulo, font=_fonte(20), fill=_COR_TEXTO)
        desenho.text(
            (_LARGURA - margem - 100, y), f"{valor:g}",
            font=_fonte(20, negrito=True), fill=cor, anchor="ra",
        )
        y += 38

    return _para_bytes(img)


def gerar_cartao_resumo_dia(user_id: str, data: str, totais: dict) -> bytes:
    """
    Gera PNG (bytes) de resumo do dia.

    totais = {"kcal": float, "proteina": float, "carbo": float,
              "gordura": float, "meta_kcal": float | None}
    """
    altura = 300
    img = Image.new("RGB", (_LARGURA, altura), _COR_FUNDO)
    desenho = ImageDraw.Draw(img)

    margem = 32
    y = margem

    desenho.text((margem, y), f"Resumo — {data}", font=_fonte(28, negrito=True), fill=_COR_TITULO)
    y += 48

    desenho.line((margem, y, _LARGURA - margem, y), fill=_COR_LINHA, width=2)
    y += 24

    for chave, rotulo in _MACROS_LABELS:
        valor = totais.get(chave, 0)
        cor = _COR_DESTAQUE if chave == "kcal" else _COR_TEXTO
        desenho.text((margem, y), rotulo, font=_fonte(20), fill=_COR_TEXTO)
        desenho.text(
            (_LARGURA - margem - 100, y), f"{valor:g}",
            font=_fonte(20, negrito=True), fill=cor, anchor="ra",
        )
        y += 38

    meta = totais.get("meta_kcal")
    if meta:
        diferenca = totais.get("kcal", 0) - meta
        sinal = "+" if diferenca >= 0 else ""
        y += 12
        desenho.text(
            (margem, y),
            f"Meta: {meta:g} kcal  ({sinal}{diferenca:g})",
            font=_fonte(18), fill=_COR_TEXTO,
        )

    return _para_bytes(img)


def _para_bytes(img: Image.Image) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()