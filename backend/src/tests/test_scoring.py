# tests/test_scoring.py

import pytest

from src.domain.percentile import Normalization
from src.domain.scoring import (
    FALLBACK_CORTES,
    HybridWeights,
    NumericScoreStrategy,
    ScoreWeights,
    fallback_classification,
)
from src.domain.trend_models import TrendInput


def base_input(**overrides):
    data = {
        "product_id": "uuid-1",
        "title": "Produto X",
        "category": "cat",
        "price": 100.0,
        "sold_quantity": None,
        "views_24h": 10_000,
        "engagement_24h": 500,
        "mentions_24h": 10,
        "rank_momentum": 0.1,
        "reviews_velocity": 2.0,
        "social_velocity": 0.10,
        "price_volatility": 0.05,
        "previous_final_score": None,
    }
    data.update(overrides)
    return TrendInput(**data)


def test_score_range_0_100():
    s = NumericScoreStrategy()
    ti = base_input()
    res = s.compute(ti)
    assert 0.0 <= res.score_0_100 <= 100.0


def test_score_increases_with_social_velocity():
    s = NumericScoreStrategy()

    low = s.compute(base_input(social_velocity=0.05, engagement_24h=400)).score_0_100
    high = s.compute(base_input(social_velocity=0.80, engagement_24h=400)).score_0_100

    assert high > low


def test_score_rewards_price_stability():
    s = NumericScoreStrategy()

    stable = s.compute(base_input(price_volatility=0.01)).score_0_100
    volatile = s.compute(base_input(price_volatility=0.80)).score_0_100

    assert stable > volatile


# --- Pesos configuráveis (TIE-22) -------------------------------------------

# Valores dourados dos defaults. Mudou um default? Recalcule à mão e atualize
# aqui, conscientemente (TIE-22). TIE-48: entrou rank_position (0,15) e saíram
# 0,05 de rank, de reviews e de preço. Sem posição no ranking a dimensão sai e
# os pesos são renormalizados — ({}) e (FORTE) dividem por 0,85:
#   {}    = (.15*.1 + .10*.04 + .25*.1 + .15*.0333 + .15*.025 + .05*.95) / .85
#   FORTE = (.15*.9 + .10*.8 + .25*.8 + .15*.8333 + .15*.75 + .05*.7) / .85
#   FORTE no topo do ranking = mesma soma + .15*1.0, sobre 1.0
FORTE = {
    "social_velocity": 0.8,
    "views_24h": 250_000,
    "engagement_24h": 15_000,
    "rank_momentum": 0.9,
    "reviews_velocity": 40,
    "price_volatility": 0.3,
}


@pytest.mark.parametrize(
    ("overrides", "esperado"),
    [({}, 11.79), (FORTE, 80.88), ({**FORTE, "rank_position": 1}, 83.75)],
)
def test_valores_dourados_dos_defaults(overrides, esperado):
    assert NumericScoreStrategy().compute(base_input(**overrides)).score_0_100 == esperado


def test_defaults_dos_pesos():
    assert ScoreWeights().as_dict() == {
        "rank_position": 0.15,
        "rank": 0.15,
        "reviews": 0.10,
        "social": 0.25,
        "views": 0.15,
        "engagement": 0.15,
        "price_stability": 0.05,
    }
    assert HybridWeights().as_dict() == {"numeric": 0.6, "llm": 0.4}


def test_pesos_customizados_mudam_o_score():
    so_social = ScoreWeights(
        rank_position=0, rank=0, reviews=0, social=1.0, views=0, engagement=0, price_stability=0
    )
    res = NumericScoreStrategy(so_social).compute(base_input(social_velocity=0.5))
    assert res.score_0_100 == 50.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"social": 0.35},  # soma 1.10
        {"rank": -0.1, "social": 0.55},  # negativo, mesmo somando 1
    ],
)
def test_pesos_numericos_invalidos(kwargs):
    with pytest.raises(ValueError, match="peso"):
        ScoreWeights(**kwargs)


@pytest.mark.parametrize(("numeric", "llm"), [(0.7, 0.4), (1.2, -0.2)])
def test_pesos_hibridos_invalidos(numeric, llm):
    with pytest.raises(ValueError, match="peso"):
        HybridWeights(numeric=numeric, llm=llm)


def test_tolerancia_de_ponto_flutuante():
    # 0.1 + 0.2 != 0.3 em float; não pode reprovar peso legítimo
    ScoreWeights(
        rank_position=0.0,
        rank=0.1,
        reviews=0.2,
        social=0.3,
        views=0.1,
        engagement=0.2,
        price_stability=0.1,
    )


# --- Motor de tendência: velocidade em vez de total -------------------------


def test_com_idade_conhecida_usa_views_por_hora_e_nao_o_total():
    s = NumericScoreStrategy()
    # Mesmo total de views; o vídeo novo (ritmo alto) tem que pontuar mais
    velho = s.compute(base_input(views_24h=5_000_000, views_per_hour=200.0, age_hours=25_000.0))
    novo = s.compute(base_input(views_24h=5_000_000, views_per_hour=100_000.0, age_hours=50.0))
    assert novo.score_0_100 > velho.score_0_100
    assert novo.components["view_signal"] == "views_per_hour"


def test_sem_idade_mantem_o_calculo_antigo():
    # Produto do ML (sem data de publicação) e dado legado
    res = NumericScoreStrategy().compute(base_input())
    assert res.components["view_signal"] == "total"
    assert res.score_0_100 == 11.79


def test_velocidade_em_escala_log_nao_satura_cedo():
    s = NumericScoreStrategy()
    a = s.compute(base_input(views_per_hour=10_000.0, age_hours=10.0)).components["nm_views"]
    b = s.compute(base_input(views_per_hour=100_000.0, age_hours=10.0)).components["nm_views"]
    assert 0 < a < b <= 1.0


def test_preco_ausente_eh_neutro_e_nao_estabilidade_maxima():
    # Vídeo do TikTok não tem preço: antes ganhava os 10 pontos de estabilidade
    res = NumericScoreStrategy().compute(base_input(price=None, price_volatility=0.0))
    assert res.components["nm_price_stability"] == 0.5


# Classificação do fallback sem LLM, por fonte (TIE-43, replay de 2026-10-13).
# A escala do numérico difere: o vídeo pontua só percentis (mediana ~50, p90 78)
# e o item do ML depende da posição e da subida no ranking (mediana ~35, p95 57).
@pytest.mark.parametrize(
    "source, score, esperado",
    [
        ("mercadolivre", 61.99, "ESTAVEL"),
        ("mercadolivre", 62.0, "SUBINDO"),
        ("mercadolivre", 79.99, "SUBINDO"),
        ("mercadolivre", 80.0, "VIRALIZANDO"),
        ("tiktok", 84.99, "ESTAVEL"),
        ("tiktok", 85.0, "SUBINDO"),
        ("tiktok", 91.99, "SUBINDO"),
        ("tiktok", 92.0, "VIRALIZANDO"),
        # Fonte desconhecida (dado legado) usa os cortes do ML
        (None, 62.0, "SUBINDO"),
        ("outra", 61.99, "ESTAVEL"),
    ],
)
def test_fallback_classification_por_fonte(source, score, esperado):
    assert fallback_classification(score, source) == esperado


def test_mesmo_score_classifica_diferente_por_fonte():
    # 70 é top ~1% do ML e só a mediana alta do TikTok
    assert fallback_classification(70.0, "mercadolivre") == "SUBINDO"
    assert fallback_classification(70.0, "tiktok") == "ESTAVEL"


@pytest.mark.parametrize("source", ["mercadolivre", "tiktok"])
def test_cortes_do_fallback_cabem_abaixo_do_teto(source):
    # Teto de cada fonte é 100 desde a TIE-48: as faixas precisam aparecer
    subindo, viralizando = FALLBACK_CORTES[source]
    assert 0 < subindo < viralizando < 100


# --- TIE-48: score por fonte -------------------------------------------------
#
# No ML, views/engajamento/social/avaliações não existem: o percentil punha o
# empate em 0,5 e o ranking saía ordenado pela oscilação da oferta mais barata.
# Dimensão que não existe para a fonte sai do cálculo, e os pesos que sobram
# são renormalizados.

DIMS_NULAS = {"views_per_hour": 0.0, "engagement_per_hour": 0.0, "social_velocity": 0.0}


def ml_input(**overrides):
    data = {
        "source": "mercadolivre",
        "views_24h": 0,
        "engagement_24h": 0,
        "mentions_24h": 0,
        "social_velocity": 0.0,
        "reviews_velocity": 0.0,
        "rank_momentum": 0.0,
        "price_volatility": 0.0,
        "rank_position": 10,
    }
    data.update(overrides)
    return base_input(**data)


def tiktok_input(**overrides):
    data = {"source": "tiktok", "price": None, "rank_momentum": 0.0, "reviews_velocity": 0.0}
    data.update(overrides)
    return base_input(**data)


def norm(values=None, nao_medidas=()):
    v = {
        "views_per_hour": 0.5,
        "engagement_per_hour": 0.5,
        "social_velocity": 0.5,
        "reviews_velocity": 0.5,
    }
    v.update(values or {})
    return Normalization(values=v, basis="categoria", nao_medidas=frozenset(nao_medidas))


def test_ml_nao_pontua_dimensoes_sociais():
    c = NumericScoreStrategy().compute(ml_input(), norm()).components
    assert c["nm_views"] is None
    assert c["nm_engagement"] is None
    assert c["nm_social_velocity"] is None
    assert c["dimensoes"] == ["rank_position", "rank", "reviews", "price_stability"]


def test_tiktok_nao_pontua_ranking_avaliacao_nem_preco():
    c = NumericScoreStrategy().compute(tiktok_input(), norm()).components
    assert c["nm_rank_position"] is None
    assert c["nm_rank"] is None
    assert c["nm_reviews"] is None
    assert c["nm_price_stability"] is None
    assert c["dimensoes"] == ["social", "views", "engagement"]


def test_pesos_renormalizados_somam_1():
    c = NumericScoreStrategy().compute(ml_input(), norm()).components
    assert sum(c["pesos_efetivos"].values()) == pytest.approx(1.0)
    assert set(c["pesos_efetivos"]) == set(c["dimensoes"])


def test_tiktok_no_maximo_chega_a_100():
    # Antes o teto era ~67,5: ranking 0 e avaliação/preço fixos em 0,5
    maximo = {"views_per_hour": 1.0, "engagement_per_hour": 1.0, "social_velocity": 1.0}
    assert NumericScoreStrategy().compute(tiktok_input(), norm(maximo)).score_0_100 == 100.0


def test_metrica_nao_medida_no_grupo_sai_do_calculo():
    ti = ml_input(rank_position=1, rank_momentum=1.0)
    res = NumericScoreStrategy().compute(ti, norm(nao_medidas={"reviews_velocity"}))
    assert res.components["nm_reviews"] is None
    assert "reviews" not in res.components["dimensoes"]
    assert res.score_0_100 == 100.0


def test_ml_sem_posicao_no_ranking_nao_pontua_posicao():
    c = NumericScoreStrategy().compute(ml_input(rank_position=None), norm()).components
    assert c["nm_rank_position"] is None
    assert "rank_position" not in c["dimensoes"]


@pytest.mark.parametrize(("posicao", "esperado"), [(1, 1.0), (20, 0.0), (35, 0.0)])
def test_posicao_no_ranking_topo_vale_1(posicao, esperado):
    # /highlights devolve o top 20 de cada categoria
    c = NumericScoreStrategy().compute(ml_input(rank_position=posicao), norm()).components
    assert c["nm_rank_position"] == esperado


def test_no_ml_o_ranking_domina_o_preco():
    s = NumericScoreStrategy()
    n = norm(nao_medidas={"reviews_velocity"})
    topo_preco_instavel = s.compute(ml_input(rank_position=2, price_volatility=0.6), n)
    fundo_preco_estavel = s.compute(ml_input(rank_position=19, price_volatility=0.0), n)
    assert topo_preco_instavel.score_0_100 > fundo_preco_estavel.score_0_100


def test_no_ml_subir_no_ranking_pesa_mais_que_o_preco():
    s = NumericScoreStrategy()
    n = norm(nao_medidas={"reviews_velocity"})
    subindo = s.compute(ml_input(rank_momentum=0.5, price_volatility=0.6), n)
    parado = s.compute(ml_input(rank_momentum=0.0, price_volatility=0.0), n)
    assert subindo.score_0_100 > parado.score_0_100


def test_sem_nenhuma_dimensao_com_peso_vale_zero():
    so_social = ScoreWeights(
        rank_position=0, rank=0, reviews=0, social=1.0, views=0, engagement=0, price_stability=0
    )
    assert NumericScoreStrategy(so_social).compute(ml_input(), norm()).score_0_100 == 0.0


def test_fonte_desconhecida_usa_todas_as_dimensoes():
    c = NumericScoreStrategy().compute(base_input(rank_position=5)).components
    assert c["dimensoes"] == [
        "rank_position",
        "rank",
        "reviews",
        "social",
        "views",
        "engagement",
        "price_stability",
    ]
