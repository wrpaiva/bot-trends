# src/domain/rank_momentum.py
"""
`rank_momentum` (TIE-16): subida no ranking de mais vendidos. Regra pura, sem I/O.

A posição vem do `/highlights` do Mercado Livre (1 = mais vendido da
categoria). Compara a leitura mais recente da janela com a mais antiga, em
escala log: ir de 20º a 1º vale 1,0, e 10º → 5º vale o mesmo que 2º → 1º
(a posição caiu à metade nos dois casos). Ficar parado ou cair vale 0 — o
score mede subida, a queda é assunto do LLM.

Sem duas posições separadas por `min_interval_hours` (vídeo do TikTok, que não
tem ranking; item que acabou de entrar no top) vale 0: não medido, como a
aceleração com leitura única em `trend_signals`.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

from .trend_signals import MIN_INTERVAL_HOURS

# Itens pedidos por categoria no /highlights (`max_items_per_category` do collector)
DEFAULT_MAX_POSITION = 20


@dataclass(frozen=True)
class RankReading:
    ts: dt.datetime
    position: int | None


def rank_momentum(
    readings: list[RankReading],
    *,
    max_position: int = DEFAULT_MAX_POSITION,
    min_interval_hours: float = MIN_INTERVAL_HOURS,
) -> float:
    if max_position < 2:
        raise ValueError("max_position precisa ser >= 2: ranking de 1 posição não tem subida")

    com_posicao = sorted((r for r in readings if r.position), key=lambda r: r.ts)
    if len(com_posicao) < 2:
        return 0.0

    antiga, recente = com_posicao[0], com_posicao[-1]
    if (recente.ts - antiga.ts).total_seconds() / 3600 < min_interval_hours:
        return 0.0

    antes = min(antiga.position, max_position)
    agora = max(recente.position, 1)
    if agora >= antes:
        return 0.0
    return min(math.log(antes / agora) / math.log(max_position), 1.0)
