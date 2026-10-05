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

from .trend_signals import MIN_INTERVAL_HOURS, Medida


@dataclass(frozen=True)
class ReviewsReading:
    ts: dt.datetime
    total: int | None


def reviews_velocity_medido(
    readings: list[ReviewsReading], *, min_interval_hours: float = MIN_INTERVAL_HOURS
) -> Medida:
    """Avaliações/dia e, quando não deu para medir, o motivo (TIE-16)."""
    com_total = sorted((r for r in readings if r.total is not None), key=lambda r: r.ts)
    if not com_total:
        return Medida(0.0, "sem_avaliacoes")
    if len(com_total) < 2:
        return Medida(0.0, "leitura_unica")

    antiga, recente = com_total[0], com_total[-1]
    horas = (recente.ts - antiga.ts).total_seconds() / 3600
    if horas < min_interval_hours:
        return Medida(0.0, "intervalo_curto")

    # Total que cai é medido (avaliação removida), não falta de histórico
    return Medida(max(recente.total - antiga.total, 0) / horas * 24)


def reviews_velocity(
    readings: list[ReviewsReading], *, min_interval_hours: float = MIN_INTERVAL_HOURS
) -> float:
    return reviews_velocity_medido(readings, min_interval_hours=min_interval_hours).valor
