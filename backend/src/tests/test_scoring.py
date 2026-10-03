# tests/test_scoring.py

import pytest

from src.domain.percentile import Normalization
from src.domain.scoring import (
    FALLBACK_SUBINDO,
    FALLBACK_VIRALIZANDO,
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

# Capturados do código ANTES de os pesos saírem do código: os defaults novos
# têm que reproduzir exatamente o comportamento antigo.
FORTE = {
    "social_velocity": 0.8,
    "views_24h": 250_000,
    "engagement_24h": 15_000,
    "rank_momentum": 0.9,
    "reviews_velocity": 40,
    "price_volatility": 0.3,
}


@pytest.mark.parametrize(("overrides", "esperado"), [({}, 15.47), (FORTE, 80.75)])
def test_defaults_reproduzem_o_score_de_antes(overrides, esperado):
    assert NumericScoreStrategy().compute(base_input(**overrides)).score_0_100 == esperado


def test_defaults_dos_pesos():
    assert ScoreWeights().as_dict() == {
        "rank": 0.20,
        "reviews": 0.15,
        "social": 0.25,
        "views": 0.15,
        "engagement": 0.15,
        "price_stability": 0.10,
    }
    assert HybridWeights().as_dict() == {"numeric": 0.6, "llm": 0.4}


def test_pesos_customizados_mudam_o_score():
    so_social = ScoreWeights(
        rank=0, reviews=0, social=1.0, views=0, engagement=0, price_stability=0
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
    ScoreWeights(rank=0.1, reviews=0.2, social=0.3, views=0.1, engagement=0.2, price_stability=0.1)


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
    assert res.score_0_100 == 15.47


def test_velocidade_em_escala_log_nao_satura_cedo():
    s = NumericScoreStrategy()
    a = s.compute(base_input(views_per_hour=10_000.0, age_hours=10.0)).components["nm_views"]
    b = s.compute(base_input(views_per_hour=100_000.0, age_hours=10.0)).components["nm_views"]
    assert 0 < a < b <= 1.0


def test_preco_ausente_eh_neutro_e_nao_estabilidade_maxima():
    # Vídeo do TikTok não tem preço: antes ganhava os 10 pontos de estabilidade
    res = NumericScoreStrategy().compute(base_input(price=None, price_volatility=0.0))
    assert res.components["nm_price_stability"] == 0.5


# Classificação do fallback sem LLM, recalibrada em 2026-10-03: com rank e
# reviews fora (TIE-16) o numérico não passa de ~67,5, e os cortes antigos
# (75/85) deixavam todo produto em ESTAVEL.
@pytest.mark.parametrize(
    "score, esperado",
    [
        (0.0, "ESTAVEL"),
        (59.99, "ESTAVEL"),
        (60.0, "SUBINDO"),
        (65.99, "SUBINDO"),
        (66.0, "VIRALIZANDO"),
        (100.0, "VIRALIZANDO"),
    ],
)
def test_fallback_classification(score, esperado):
    assert fallback_classification(score) == esperado


def test_fallback_subindo_alcancavel_pelo_teto_numerico():
    # Teto com rank_momentum = 0 e reviews no percentil 0,5: os cortes do
    # fallback precisam caber abaixo dele, senão a faixa nunca aparece
    ti = base_input(rank_momentum=0.0, price=None)
    norm = Normalization(
        values={
            "views_per_hour": 1.0,
            "engagement_per_hour": 1.0,
            "social_velocity": 1.0,
            "reviews_velocity": 0.5,
        },
        basis="global",
    )
    teto = NumericScoreStrategy().compute(ti, norm).score_0_100
    assert fallback_classification(teto) == "VIRALIZANDO"
    assert FALLBACK_SUBINDO < FALLBACK_VIRALIZANDO <= teto
