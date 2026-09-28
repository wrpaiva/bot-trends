# src/tests/test_trend_signals.py
"""
Sinais de tendência a partir da idade do vídeo (motor de tendência).

Antes o score usava views ACUMULADAS: o topo do ranking era um vídeo de 4
anos com 96 mi de views. Tendência é ritmo, não total: views por hora desde a
publicação, e aceleração quando o mesmo vídeo é lido mais de uma vez.
"""

import datetime as dt

import pytest

from src.domain.trend_signals import Reading, compute_signals, is_too_old

PUB = dt.datetime(2026, 9, 20, 0, 0, tzinfo=dt.UTC)


def _r(horas_depois, views, engagement=0):
    return Reading(ts=PUB + dt.timedelta(hours=horas_depois), views=views, engagement=engagement)


def test_views_por_hora_desde_a_publicacao():
    s = compute_signals([_r(10, 5000, 500)], PUB)
    assert s.age_hours == 10
    assert s.views_per_hour == 500
    assert s.engagement_per_hour == 50


def test_video_muito_novo_usa_idade_minima_no_denominador():
    # 100 mil views em 5 min não é 1,2 mi/h: o piso de 6 h segura a explosão
    s = compute_signals([_r(1 / 12, 100_000)], PUB, min_age_hours=6)
    assert s.views_per_hour == pytest.approx(100_000 / 6)
    assert s.age_hours == pytest.approx(1 / 12)


def test_video_velho_gigante_perde_para_video_novo_rapido():
    velho = compute_signals([_r(24 * 1553, 96_300_000)], PUB)
    novo = compute_signals([_r(20, 2_400_000)], PUB)
    assert novo.views_per_hour > velho.views_per_hour


def test_leitura_unica_nao_inventa_aceleracao():
    """
    Validação com dados reais: usar o frescor como sinal social colocava vídeo
    de 0 dia com 61 views/h no topo — contava a juventude duas vezes (views/h
    já a incorpora). Com uma leitura só, a aceleração é desconhecida: 0.
    """
    for horas in (1, 6, 360, 720):
        assert compute_signals([_r(horas, 1000)], PUB).social_velocity == 0.0


def test_duas_leituras_medem_aceleracao():
    # Ritmo médio até a 1ª leitura: 1000/h. Entre as leituras: 3000/h → 2x acima
    s = compute_signals([_r(10, 10_000), _r(12, 16_000)], PUB)
    assert s.social_velocity == pytest.approx(2.0)


def test_aceleracao_em_video_minusculo_eh_ruido():
    """
    Validação com dados reais: um vídeo que foi de 100 para 365 views
    "acelerou 3,6x" e passou na frente de um com 226 mil views/h — no
    percentil, qualquer valor > 0 vence todos os zeros. Abaixo do piso de
    volume a aceleração vale 0.
    """
    s = compute_signals([_r(10, 100), _r(12, 365)], PUB, min_views_for_accel=1000)
    assert s.social_velocity == 0.0
    grande = compute_signals([_r(10, 10_000), _r(12, 16_000)], PUB, min_views_for_accel=1000)
    assert grande.social_velocity == pytest.approx(2.0)


def test_ritmo_constante_nao_eh_aceleracao():
    s = compute_signals([_r(10, 10_000), _r(20, 20_000)], PUB)
    assert s.social_velocity == pytest.approx(0.0)


def test_leituras_repetidas_iguais_nao_aceleram():
    # Caso real: o mesmo vídeo lido 8 vezes com 679.300 views em todas
    s = compute_signals([_r(100 + i * 2, 679_300) for i in range(8)], PUB)
    assert s.social_velocity == 0.0


def test_leituras_muito_proximas_nao_medem_aceleracao():
    # Menos de 1 h entre leituras não mede ritmo com confiança
    s = compute_signals([_r(10, 10_000), _r(10.2, 10_500)], PUB)
    assert s.social_velocity == 0.0


def test_ordem_das_leituras_nao_importa():
    a = compute_signals([_r(12, 16_000), _r(10, 10_000)], PUB)
    b = compute_signals([_r(10, 10_000), _r(12, 16_000)], PUB)
    assert a == b


def test_relogio_adiantado_nao_gera_idade_negativa():
    s = compute_signals([_r(-1, 100)], PUB)
    assert s.age_hours == 0.0


def test_sem_leituras_nao_ha_sinal():
    assert compute_signals([], PUB) is None


@pytest.mark.parametrize(("idade_dias", "velho"), [(29.9, False), (30, False), (30.1, True)])
def test_corte_de_idade(idade_dias, velho):
    assert is_too_old(idade_dias * 24, max_age_days=30) is velho


def test_idade_desconhecida_nao_eh_cortada():
    assert is_too_old(None, max_age_days=30) is False
