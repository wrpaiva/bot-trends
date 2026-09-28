# src/domain/commercial.py
"""
Intenção comercial do conteúdo (TIE-18, parte 1). Regra pura, sem I/O.

O motor mede ritmo de qualquer vídeo; sem este filtro, dança e meme de `#fyp`
disputavam o ranking com produto. Um conteúdo é comercial se tem produto da
loja do TikTok ou se o texto tem marca de venda: loja (Shopee, Shein, Amazon,
Mercado Livre...), "link na bio", "comenta QUERO", preço em R$, código de
produto, "achadinho", "comprei".

Calibrado com os 501 vídeos reais coletados até 2026-09-25:

- `hasTikTokShopProduct` sozinho não serve: 79 dos 97 vídeos das hashtags de
  compra não o têm (afiliado da Shopee manda para a bio, não para a loja).
- `isAd` ficou de fora: marcava 21 vídeos de `#fyp` sem produto nenhum.
- Nos 404 vídeos de `#fyp`, o texto não casou nenhuma vez (zero falso
  positivo); nos 97 de compra, casou 87 mesmo ignorando a hashtag de busca.

O texto é normalizado (sem acento, minúsculo, `!`→i, `0`→o, `1`→i) porque
afiliado escreve "L!nks" e "Bl0" para escapar do filtro da plataforma.
"""

from __future__ import annotations

import re
import unicodedata

_MARCADORES = [
    # Lojas
    # "amazon" sem "amazonas"/"amazonia" (estado e floresta); "temu" só como palavra
    r"shopee|shoppe|shein|amazon(?!as|ia)|amzn|mercado ?livre|aliexpress|\btemu\b|magalu|tiktok ?shop",
    # Chamada para comprar
    r"links? (?:na|in) (?:minha |my |ma )?bio\b",
    r"\bna bio\b",
    r"\bcoment\w* .{0,12}(?:quero|link)",
    r"comment .{0,10}(?:link|want)",
    r"\bget (?:it|yours)\b",
    r"\bstorefront\b",
    # Vocabulário de achado/compra
    r"\bachad(?:inho|o)s?\b",
    r"\bcompr(?:ei|ou|as|inhas?)\b|comprasonline|\bhaul\b|shoppinghaul",
    r"must ?haves?|tiktokmademebuyit|amazonfinds?",
    # Preço, cupom e código de produto (Shopee: "ID: AHN-QGM-EGU")
    r"r\$ ?\d",
    r"\bcupo(?:m|ns)\b",
    r"\bid: ?[a-z]{3}-[a-z]{3}-[a-z]{3}\b",
    r"\bofertas?\b|\bpromoc",
]
_RX = re.compile("|".join(f"(?:{m})" for m in _MARCADORES))
_LEET = str.maketrans({"!": "i", "0": "o", "1": "i"})


def _normaliza(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sem_acento.lower().translate(_LEET)


def commercial_marker(text: str | None, *, has_shop_product: bool) -> str | None:
    """O que torna o conteúdo comercial (para depurar), ou None se nada."""
    if has_shop_product:
        return "tiktok_shop"
    m = _RX.search(_normaliza(text or ""))
    return m.group(0).strip() if m else None


def has_commercial_intent(text: str | None, *, has_shop_product: bool) -> bool:
    return commercial_marker(text, has_shop_product=has_shop_product) is not None
