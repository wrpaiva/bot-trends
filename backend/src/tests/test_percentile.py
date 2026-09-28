# src/tests/test_percentile.py
"""
Normalização por percentil dentro da categoria (TIE-21).

Com a normalização absoluta, 5 mil views valem o mesmo para um celular e para
uma capinha — e o ranking sempre premia as categorias de maior volume. Com
percentil, cada produto é comparado com os da própria categoria.
"""

import pytest

from src.domain.percentile import PERCENTILE_METRICS, PercentileContext, percentile_rank
from src.domain.scoring import NumericScoreStrategy
from src.domain.trend_models import TrendInput


def _ti(pid, category, views, engagement=None, social=0.1, reviews=0.0):
    return TrendInput(
        product_id=pid,
        title=pid,
        category=category,
        price=10.0,
        sold_quantity=None,
        views_24h=views,
        engagement_24h=engagement if engagement is not None else views // 20,
        mentions_24h=1,
        rank_momentum=0.0,
        reviews_velocity=reviews,
        social_velocity=social,
        price_volatility=0.0,
    )


# Celular: volume alto. Capinha: volume baixo, com uma capinha excepcional.
CELULARES = [
    _ti(f"cel{i}", "celulares", v) for i, v in enumerate([50_000, 60_000, 70_000, 80_000, 90_000])
]
CAPINHAS = [_ti(f"cap{i}", "capinhas", v) for i, v in enumerate([500, 800, 1_000, 1_200, 5_000])]


# --- percentile_rank --------------------------------------------------------


def test_percentil_midrank():
    pop = [1, 2, 3, 4]
    assert percentile_rank(4, pop) == 0.875
    assert percentile_rank(1, pop) == 0.125


def test_percentil_com_empate():
    assert percentile_rank(2, [2, 2, 2, 2]) == 0.5


def test_percentil_populacao_unitaria_e_vazia():
    assert percentile_rank(10, [10]) == 0.5
    assert percentile_rank(10, []) == 0.5


def test_percentil_fica_entre_0_e_1():
    pop = [-0.5, 0.0, 0.3, 2.0]
    assert all(0.0 < percentile_rank(v, pop) < 1.0 for v in pop)


# --- Contexto por categoria -------------------------------------------------


def test_capinha_excepcional_vence_celular_mediocre_com_percentil():
    ctx = PercentileContext(CELULARES + CAPINHAS, min_group=5)
    s = NumericScoreStrategy()

    cap_top, cel_fundo = CAPINHAS[-1], CELULARES[0]

    # Absoluto: volume manda, o celular mais fraco ainda ganha da melhor capinha
    assert s.compute(cel_fundo).score_0_100 > s.compute(cap_top).score_0_100
    # Percentil: a capinha é a melhor da categoria; o celular, o pior da dele
    assert (
        s.compute(cap_top, ctx.normalize(cap_top)).score_0_100
        > s.compute(cel_fundo, ctx.normalize(cel_fundo)).score_0_100
    )


def test_normalize_devolve_percentis_e_a_base():
    ctx = PercentileContext(CELULARES + CAPINHAS, min_group=5)
    norm = ctx.normalize(CAPINHAS[-1])
    assert norm.basis == "categoria"
    assert set(norm.values) == set(PERCENTILE_METRICS)
    assert norm.values["views_24h"] == 0.9


def test_categoria_pequena_cai_no_pool_global():
    raro = _ti("raro", "drones", 30_000)
    ctx = PercentileContext(CELULARES + CAPINHAS + [raro], min_group=5)
    norm = ctx.normalize(raro)
    assert norm.basis == "global"
    # 30 mil está no meio do pool global de 11 produtos
    assert 0.3 < norm.values["views_24h"] < 0.6


def test_pool_global_pequeno_volta_ao_absoluto():
    poucos = CAPINHAS[:3]
    ctx = PercentileContext(poucos, min_group=5)
    assert ctx.normalize(poucos[0]) is None


def test_categoria_none_eh_um_grupo():
    sem_cat = [_ti(f"tt{i}", None, v) for i, v in enumerate([10, 20, 30, 40, 50])]
    ctx = PercentileContext(sem_cat, min_group=5)
    assert ctx.normalize(sem_cat[-1]).basis == "categoria"


# --- Scoring com percentis --------------------------------------------------


def test_score_registra_a_normalizacao_usada():
    ctx = PercentileContext(CELULARES + CAPINHAS, min_group=5)
    s = NumericScoreStrategy()

    absoluto = s.compute(CAPINHAS[0])
    relativo = s.compute(CAPINHAS[0], ctx.normalize(CAPINHAS[0]))

    assert absoluto.components["normalization"] == "absoluta"
    assert relativo.components["normalization"] == "categoria"


def test_percentis_substituem_so_as_metricas_de_volume():
    ctx = PercentileContext(CELULARES + CAPINHAS, min_group=5)
    ti = CAPINHAS[-1]
    comps = NumericScoreStrategy().compute(ti, ctx.normalize(ti)).components
    assert comps["nm_views"] == 0.9
    # rank_momentum e estabilidade de preço seguem absolutos
    assert comps["nm_rank"] == 0.0 and comps["nm_price_stability"] == 1.0


@pytest.mark.parametrize("min_group", [0, 1])
def test_min_group_minimo_eh_2(min_group):
    with pytest.raises(ValueError):
        PercentileContext(CAPINHAS, min_group=min_group)
