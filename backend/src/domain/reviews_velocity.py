# src/domain/reviews_velocity.py
"""
`reviews_velocity` (TIE-16): avaliações novas por dia. Regra pura, sem I/O.

O total de avaliações vem de `/reviews/item` do Mercado Livre (TIE-15).
Compara a leitura mais antiga da janela com a mais recente que tenham total —
mais robusto que somar `reviews_delta`, que vira None a cada falha da coleta.

Vale 0 sem duas leituras separadas por `min_interval_hours` (vídeo do TikTok,
item recém-coletado) e quando o total não cresce: avaliação removida não é
"velocidade negativa". A normalização (percentil na categoria, ou 0–50/dia no
modo absoluto) fica no scoring.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .trend_signals import MIN_INTERVAL_HOURS


@dataclass(frozen=True)
class ReviewsReading:
    ts: dt.datetime
    total: int | None


def reviews_velocity(
    readings: list[ReviewsReading], *, min_interval_hours: float = MIN_INTERVAL_HOURS
) -> float:
    com_total = sorted((r for r in readings if r.total is not None), key=lambda r: r.ts)
    if len(com_total) < 2:
        return 0.0

    antiga, recente = com_total[0], com_total[-1]
    horas = (recente.ts - antiga.ts).total_seconds() / 3600
    if horas < min_interval_hours:
        return 0.0

    return max(recente.total - antiga.total, 0) / horas * 24
