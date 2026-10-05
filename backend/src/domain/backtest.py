# src/domain/backtest.py
"""
Métricas do backtest (TIE-23). Regra pura, sem I/O.

A pergunta do backtest: num corte T, o ranking pelo score ordena melhor o que
vai crescer depois de T do que um baseline burro (views/hora)? Aqui só ficam
as métricas de ranking; montar os cortes é com `apps/backtest`.

- `spearman`: correlação entre as ordens (1 = mesma ordem, -1 = inversa).
- `precision_at_k`: fração do top-k previsto que está no top-k real.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence


def _postos(valores: Sequence[float]) -> list[float]:
    """Posto (1..n) de cada valor; empates recebem o posto médio."""
    ordem = sorted(range(len(valores)), key=lambda i: valores[i])
    postos = [0.0] * len(valores)
    i = 0
    while i < len(ordem):
        j = i
        while j + 1 < len(ordem) and valores[ordem[j + 1]] == valores[ordem[i]]:
            j += 1
        medio = (i + j) / 2 + 1
        for k in range(i, j + 1):
            postos[ordem[k]] = medio
        i = j + 1
    return postos


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Correlação de Spearman; None com menos de 3 pontos ou variância zero."""
    if len(xs) != len(ys):
        raise ValueError("xs e ys precisam ter o mesmo tamanho")
    if len(xs) < 3:
        return None
    rx, ry = _postos(xs), _postos(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx == 0 or vy == 0:
        return None
    return cov / math.sqrt(vx * vy)


def _top(valores: dict[str, float], k: int) -> set[str]:
    # Maior primeiro; empate desempata pelo id, para o resultado não depender
    # da ordem de inserção
    return {pid for pid, _ in sorted(valores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]}


def precision_at_k(previsto: dict[str, float], real: dict[str, float], *, k: int) -> float:
    """Fração do top-k previsto que também está no top-k real (mesmos ids)."""
    comuns = previsto.keys() & real.keys()
    if not comuns:
        raise ValueError("previsto e real não têm ids em comum")
    k = min(k, len(comuns))
    p = _top({i: previsto[i] for i in comuns}, k)
    r = _top({i: real[i] for i in comuns}, k)
    return len(p & r) / k


def summarize(valores: Sequence[float | None]) -> dict[str, float | int | None]:
    """Média dos cortes que tiveram valor definido."""
    validos = [v for v in valores if v is not None]
    return {"media": statistics.fmean(validos) if validos else None, "n": len(validos)}
