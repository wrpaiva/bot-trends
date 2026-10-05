# src/tests/test_reviews_velocity.py
"""
`reviews_velocity` (TIE-16): avaliações novas por dia na janela, a partir do
`reviews_total` que a coleta do ML grava (TIE-15).
"""

import datetime as dt

import pytest

from src.domain.reviews_velocity import ReviewsReading, reviews_velocity, reviews_velocity_medido
from src.domain.trend_signals import Medida

T0 = dt.datetime(2026, 10, 4, tzinfo=dt.UTC)


def _leituras(*totais, passo_h=6):
    return [
        ReviewsReading(ts=T0 + dt.timedelta(hours=i * passo_h), total=t)
        for i, t in enumerate(totais)
    ]


def test_avaliacoes_por_dia_entre_a_mais_antiga_e_a_mais_recente():
    # 10 → 40 em 12 h = 60 por dia; a leitura do meio não muda o resultado
    assert reviews_velocity(_leituras(10, 13, 40)) == pytest.approx(60.0)


def test_ordem_das_leituras_nao_importa():
    assert reviews_velocity(list(reversed(_leituras(10, 13, 40)))) == pytest.approx(60.0)


def test_leituras_sem_total_sao_ignoradas():
    # Falha no /reviews grava None; não pode virar "zero avaliações"
    assert reviews_velocity(_leituras(10, None, 22)) == pytest.approx(24.0)


@pytest.mark.parametrize(
    "leituras",
    [
        [],
        _leituras(10),
        _leituras(None, None),
        # Total que cai: avaliação removida, não "velocidade negativa"
        _leituras(40, 30),
        _leituras(40, 40),
    ],
)
def test_sem_crescimento_mensuravel_vale_0(leituras):
    assert reviews_velocity(leituras) == 0.0


def test_leituras_proximas_demais_nao_contam():
    assert reviews_velocity(_leituras(10, 40, passo_h=0)) == 0.0


# --- Motivo do zero (TIE-16) --------------------------------------------------


@pytest.mark.parametrize(
    "leituras, motivo",
    [
        ([], "sem_avaliacoes"),
        (_leituras(None, None), "sem_avaliacoes"),
        (_leituras(10), "leitura_unica"),
        (_leituras(10, 40, passo_h=0), "intervalo_curto"),
    ],
)
def test_sem_historico_diz_o_motivo(leituras, motivo):
    m = reviews_velocity_medido(leituras)
    assert m.valor == 0.0
    assert m.motivo == motivo


def test_total_que_cai_e_medida_real_sem_motivo():
    assert reviews_velocity_medido(_leituras(40, 30)) == Medida(0.0, None)
    assert reviews_velocity_medido(_leituras(10, 40)).motivo is None
