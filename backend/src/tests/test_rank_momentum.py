# src/tests/test_rank_momentum.py
"""
`rank_momentum` (TIE-16): quanto o produto subiu no ranking de mais vendidos
da categoria (`/highlights` do ML) ao longo da janela, em escala log.
"""

import datetime as dt

import pytest

from src.domain.rank_momentum import RankReading, rank_momentum, rank_momentum_medido
from src.domain.trend_signals import Medida

T0 = dt.datetime(2026, 10, 3, tzinfo=dt.UTC)


def _leituras(*posicoes, passo_h=6):
    return [
        RankReading(ts=T0 + dt.timedelta(hours=i * passo_h), position=p)
        for i, p in enumerate(posicoes)
    ]


def test_do_ultimo_ao_primeiro_lugar_vale_1():
    assert rank_momentum(_leituras(20, 1), max_position=20) == pytest.approx(1.0)


def test_escala_log_mesma_razao_mesmo_momentum():
    # 10 → 5 e 2 → 1: as duas reduzem a posição à metade
    a = rank_momentum(_leituras(10, 5), max_position=20)
    b = rank_momentum(_leituras(2, 1), max_position=20)
    assert a == pytest.approx(b)
    assert 0 < a < 1


@pytest.mark.parametrize("posicoes", [(5, 5), (3, 8), (1, 20)])
def test_parado_ou_caindo_vale_0(posicoes):
    assert rank_momentum(_leituras(*posicoes)) == 0.0


def test_compara_a_ultima_com_a_mais_antiga_da_janela():
    # Subiu de 16 para 2 na janela, com oscilação no meio
    assert rank_momentum(_leituras(16, 4, 9, 2), max_position=16) == pytest.approx(0.75)


def test_ordem_das_leituras_nao_importa():
    leituras = _leituras(16, 4, 9, 2)
    assert rank_momentum(list(reversed(leituras)), max_position=16) == pytest.approx(0.75)


@pytest.mark.parametrize(
    "leituras",
    [
        [],
        _leituras(3),
        # Leituras sem posição (TikTok, ML antigo) não contam
        [RankReading(ts=T0, position=None), RankReading(ts=T0, position=None)],
        [RankReading(ts=T0, position=None), RankReading(ts=T0 + dt.timedelta(hours=6), position=2)],
    ],
)
def test_sem_duas_posicoes_nao_ha_momentum(leituras):
    assert rank_momentum(leituras) == 0.0


def test_leituras_proximas_demais_nao_contam():
    # Duas coletas no mesmo instante não medem movimento
    assert rank_momentum(_leituras(10, 1, passo_h=0)) == 0.0


def test_posicao_alem_do_teto_satura_em_1():
    assert rank_momentum(_leituras(50, 1), max_position=20) == 1.0


def test_max_position_precisa_ser_maior_que_1():
    with pytest.raises(ValueError):
        rank_momentum(_leituras(2, 1), max_position=1)


# --- Motivo do zero (TIE-16: "sem histórico suficiente → 0.0, com log") ------


@pytest.mark.parametrize(
    "leituras, motivo",
    [
        ([], "sem_ranking"),
        ([RankReading(ts=T0, position=None)], "sem_ranking"),
        (_leituras(3), "leitura_unica"),
        (_leituras(10, 1, passo_h=0), "intervalo_curto"),
    ],
)
def test_sem_historico_diz_o_motivo(leituras, motivo):
    m = rank_momentum_medido(leituras)
    assert m.valor == 0.0
    assert m.motivo == motivo


def test_parado_e_medida_real_sem_motivo():
    # Zero por não ter subido não é falta de histórico
    assert rank_momentum_medido(_leituras(5, 5)) == Medida(0.0, None)
    assert rank_momentum_medido(_leituras(20, 1), max_position=20).motivo is None
