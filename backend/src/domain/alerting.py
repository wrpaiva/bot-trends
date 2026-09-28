# src/domain/alerting.py
"""
Quando mandar alerta de tendência (TIE-31). Regra pura, sem I/O.

- Abaixo do limiar: nunca.
- Primeira vez acima do limiar: sim.
- Dentro do cooldown: só se a classificação subiu de faixa.
- Passado o cooldown: sim, se continua acima do limiar.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

# Ordem de "gravidade" da tendência; subir nesta escala justifica realertar
_FAIXA = {
    "EM_QUEDA": 0,
    "ESTAVEL": 1,
    "PICO_TEMPORARIO": 2,
    "SUBINDO": 3,
    "VIRALIZANDO": 4,
}


@dataclass(frozen=True)
class AlertState:
    """Último alerta enviado para um produto."""

    alerted_at: dt.datetime
    classification: str


@dataclass(frozen=True)
class AlertDecision:
    send: bool
    reason: str


def decide_alert(
    *,
    final_score: float,
    classification: str,
    last: AlertState | None,
    now: dt.datetime,
    threshold: float,
    cooldown: dt.timedelta,
) -> AlertDecision:
    if final_score < threshold:
        return AlertDecision(False, "abaixo do limiar")

    if last is None:
        return AlertDecision(True, "primeiro alerta")

    if now - last.alerted_at >= cooldown:
        return AlertDecision(True, "cooldown expirado")

    # Rótulo desconhecido (estado legado) conta como a faixa mais alta: na
    # dúvida, não realerta dentro do cooldown.
    antes = _FAIXA.get(last.classification, max(_FAIXA.values()))
    agora = _FAIXA.get(classification, -1)
    if agora > antes:
        return AlertDecision(True, f"subiu de {last.classification} para {classification}")

    return AlertDecision(False, "em cooldown")


def decide_system_alert(
    problem: bool,
    last_alerted_at: dt.datetime | None,
    now: dt.datetime,
    cooldown: dt.timedelta,
) -> str | None:
    """
    Alerta de saúde do sistema (TIE-39): "alert", "recovered" ou None.

    Problema novo alerta; persistente só realerta após o cooldown; quando
    some, avisa uma vez que normalizou.
    """
    if problem:
        if last_alerted_at is None or now - last_alerted_at >= cooldown:
            return "alert"
        return None
    return "recovered" if last_alerted_at is not None else None
