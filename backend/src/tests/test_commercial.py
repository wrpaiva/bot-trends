# src/tests/test_commercial.py
"""
Intenção comercial do conteúdo (TIE-18, parte 1): o motor media ritmo de
qualquer vídeo, e o topo do ranking tinha dança e meme de `#fyp`.

Os casos vêm dos 501 vídeos reais coletados até 2026-09-25. `hasTikTokShopProduct`
sozinho não serve: 79 dos 97 vídeos das hashtags de compra não o têm (são
afiliados da Shopee com "link na bio"), então o texto também conta.
"""

import pytest

from src.domain.commercial import commercial_marker, has_commercial_intent


@pytest.mark.parametrize(
    "texto",
    [
        "O Quarto É De Rica Mas Com Precinho De Shopee!! #comprinhas #shopee",
        "‼️‼️ LINK NA BIO ‼️‼️  Achei esse produto e fiquei sem palavras!😶",
        "Deixei os L!nks na minha BIO só pesquisar pelo nome do produto!",
        'comente "eu quero" que eu te envio o link 😁',
        "💬 Comenta “LINK” que eu te envio",
        "🏠 Itens de cozinha 2️⃣ 2 Jarras de Vidro R$36,98",
        "ID: BBH-ZXG-YYH Comprei na Shopee",
        "Achadinhos que eu fiz pra minha CASA no Mercado Livre",
        "Alguns achadinhos úteis que eu amo encontrar na SHEIN",
        "🛍️ Want it? Get it through the link in my bio 👆✨ #tiktokshop",
        "One of the best-kept secrets? Iink in ma Bl0! #amazonfavorites",
        "Solo picnic 💕find everything on my amazon storefront.",
        "Qual a melhor peça do vídeo ? #comprasonline #shoppinghaul",
        "Coisas que meu irmão comprou no TikTok pt 19",
    ],
)
def test_texto_de_venda_eh_comercial(texto):
    assert has_commercial_intent(texto, has_shop_product=False)


@pytest.mark.parametrize(
    "texto",
    [
        "eo tiktok  fyp",
        "#GoodVibes #Explore #PageFYP #Viral2026 #Trend",
        "مقاس الخصر 😉👖 #حرف_وحلول #lifehack #hack #tips #tricks #fyp",
        "Reply to @stegosaurusjen Maybe the kids need this brush 👀 #kitty #cat",
        "To apaixonadaa",
        "",
        None,
    ],
)
def test_conteudo_sem_venda_nao_eh_comercial(texto):
    assert not has_commercial_intent(texto, has_shop_product=False)


def test_produto_da_loja_do_tiktok_eh_comercial_mesmo_sem_texto():
    assert has_commercial_intent("fyp#tiktok#viral#fypage", has_shop_product=True)
    assert commercial_marker(None, has_shop_product=True) == "tiktok_shop"


def test_palavra_dentro_de_outra_nao_conta():
    # "compras" casa; "descompressão" não pode casar por conter "compr"
    assert not has_commercial_intent("vídeo de descompressão relaxante", has_shop_product=False)
    assert not has_commercial_intent("biologia na biografia", has_shop_product=False)
    assert not has_commercial_intent("pôr do sol em Manaus #amazonas", has_shop_product=False)
    assert has_commercial_intent("#amazonfinds da semana", has_shop_product=False)


def test_marcador_devolve_o_trecho_que_casou_para_depurar():
    assert commercial_marker("tudo da Shopee", has_shop_product=False) == "shopee"
    assert commercial_marker("dança nova", has_shop_product=False) is None
