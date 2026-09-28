# src/tests/test_alerting.py
"""
Regra de dedupe/throttle dos alertas (TIE-31).

Sem ela, um produto acima do limiar por dois dias gerava 96 mensagens
idênticas (análise a cada 30 min).
"""

import datetime as dt

import pytest

from src.domain.alerting import AlertState, decide_alert

AGORA = dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.UTC)
COOLDOWN = dt.timedelta(hours=24)


def _decide(score=90.0, cls="SUBINDO", last=None, threshold=85.0):
    return decide_alert(
        final_score=score,
        classification=cls,
        last=last,
        now=AGORA,
        threshold=threshold,
        cooldown=COOLDOWN,
    )


def _ultimo(horas_atras: float, cls="SUBINDO") -> AlertState:
    return AlertState(alerted_at=AGORA - dt.timedelta(hours=horas_atras), classification=cls)


def test_abaixo_do_limiar_nao_alerta():
    assert not _decide(score=84.9).send


def test_no_limiar_alerta():
    assert _decide(score=85.0).send


def test_primeira_vez_acima_do_limiar_alerta():
    d = _decide()
    assert d.send and d.reason == "primeiro alerta"


def test_dentro_do_cooldown_com_mesma_classificacao_nao_alerta():
    d = _decide(last=_ultimo(1))
    assert not d.send and "cooldown" in d.reason


@pytest.mark.parametrize(
    ("antes", "agora"),
    [("SUBINDO", "VIRALIZANDO"), ("ESTAVEL", "SUBINDO"), ("EM_QUEDA", "PICO_TEMPORARIO")],
)
def test_dentro_do_cooldown_mas_subiu_de_faixa_realerta(antes, agora):
    d = _decide(cls=agora, last=_ultimo(1, cls=antes))
    assert d.send and "subiu" in d.reason


@pytest.mark.parametrize(("antes", "agora"), [("VIRALIZANDO", "SUBINDO"), ("SUBINDO", "EM_QUEDA")])
def test_dentro_do_cooldown_e_desceu_de_faixa_nao_alerta(antes, agora):
    assert not _decide(cls=agora, last=_ultimo(1, cls=antes)).send


def test_depois_do_cooldown_realerta():
    d = _decide(last=_ultimo(24))
    assert d.send and "cooldown" in d.reason


def test_classificacao_desconhecida_no_estado_nao_quebra():
    # Estado gravado por uma versão antiga, com rótulo que não existe mais
    assert not _decide(last=_ultimo(1, cls="LEGADO")).send
