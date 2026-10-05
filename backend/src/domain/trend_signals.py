# src/domain/trend_signals.py
"""
Sinais de tendência a partir da idade do conteúdo. Regra pura, sem I/O.

Tendência é ritmo, não total. Com o total acumulado, o topo do ranking era um
vídeo de 4 anos com 96 mi de views; 47% dos vídeos coletados tinham mais de 30
dias. Aqui:

- views/engajamento por hora desde a publicação, com piso de idade (vídeo de
  minutos não pode explodir a métrica);
- `social_velocity` = aceleração: com 2+ leituras separadas por >= 1 h, quanto
  o ritmo entre as duas últimas supera o ritmo médio de vida (0 = constante,
  1 = o dobro). Com leitura única, ou abaixo de `min_views_for_accel` views
  (ruído), vale 0. Usar o frescor aqui foi
  testado com dados reais e reprovado: contava a juventude duas vezes (views/h
  já a incorpora) e punha vídeo de 0 dia com 61 views/h no topo.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

MIN_INTERVAL_HOURS = 1.0


@dataclass(frozen=True)
class Medida:
    """
    Valor de um sinal + por que ele é 0 sem ter sido medido (TIE-16).

    `motivo` None = medido (inclusive um 0 real, como produto parado no
    ranking). Preenchido = faltou histórico, e o 0 é "não sei", não "parado".
    """

    valor: float
    motivo: str | None = None


@dataclass(frozen=True)
class Reading:
    ts: dt.datetime
    views: int
    engagement: int


@dataclass(frozen=True)
class Signals:
    age_hours: float
    views_per_hour: float
    engagement_per_hour: float
    social_velocity: float


def _horas(a: dt.datetime, b: dt.datetime) -> float:
    return (a - b).total_seconds() / 3600


def compute_signals(
    readings: list[Reading],
    published_at: dt.datetime,
    *,
    min_age_hours: float = 6.0,
    min_views_for_accel: int = 1000,
) -> Signals | None:
    if not readings:
        return None
    lidas = sorted(readings, key=lambda r: r.ts)
    ultima = lidas[-1]

    idade = max(_horas(ultima.ts, published_at), 0.0)
    denom = max(idade, min_age_hours)
    vph = ultima.views / denom
    eph = ultima.engagement / denom

    # Leitura anterior com distância suficiente para medir ritmo
    anterior = next(
        (r for r in reversed(lidas[:-1]) if _horas(ultima.ts, r.ts) >= MIN_INTERVAL_HOURS),
        None,
    )
    # Abaixo do piso de volume a aceleração é ruído: 100 → 365 views seria
    # "3,6x" e, no percentil, qualquer valor > 0 vence todos os zeros
    if anterior is not None and ultima.views >= min_views_for_accel:
        intervalo = _horas(ultima.ts, anterior.ts)
        recente = max(ultima.views - anterior.views, 0) / intervalo
        idade_ant = max(_horas(anterior.ts, published_at), min_age_hours)
        de_vida = anterior.views / idade_ant
        social = max(recente / de_vida - 1.0, 0.0) if de_vida > 0 else 0.0
    else:
        social = 0.0

    return Signals(
        age_hours=idade,
        views_per_hour=vph,
        engagement_per_hour=eph,
        social_velocity=social,
    )


def is_too_old(age_hours: float | None, *, max_age_days: float) -> bool:
    """Idade desconhecida não é cortada: não há como provar que é velho."""
    return age_hours is not None and age_hours > max_age_days * 24
