# src/domain/backtest.py
"""
Métricas do backtest (TIE-23). Regra pura, sem I/O.

A pergunta do backtest: num corte T, o ranking pelo score ordena melhor o que
vai crescer depois de T do que um baseline burro (views/hora)? Aqui só ficam
as métricas de ranking; montar os cortes é com `apps/backtest`.

- `spearman`: correlação entre as ordens (1 = mesma ordem, -1 = inversa).
- `precision_at_k`: fração do top-k previsto que está no top-k real.
- `acerto_top_k`: para desfecho 0/1 (TIE-46), fração do top-k previsto com 1.
- `auc`: para desfecho 0/1 raro, chance de um positivo ter score maior que um
  negativo (0,5 = acaso). Não depende de k nem da taxa base.
- `ic_bootstrap_por_bloco`: intervalo de confiança da média reamostrando dias
  inteiros. Cortes do mesmo dia veem quase o mesmo ranking; reamostrar corte
  a corte fingiria uma amostra muito maior do que ela é.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Mapping, Sequence


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


def acerto_top_k(previsto: dict[str, float], real: dict[str, float], *, k: int) -> float:
    """
    Fração do top-k previsto cujo desfecho é 1. Para desfecho binário o
    `precision_at_k` não serve: o "top-k real" de vários empates em 1 (ou em 0)
    sai do desempate pelo id, não do que aconteceu.
    """
    comuns = previsto.keys() & real.keys()
    if not comuns:
        raise ValueError("previsto e real não têm ids em comum")
    k = min(k, len(comuns))
    return sum(real[i] for i in _top({i: previsto[i] for i in comuns}, k)) / k


def summarize(valores: Sequence[float | None]) -> dict[str, float | int | None]:
    """Média dos cortes que tiveram valor definido."""
    validos = [v for v in valores if v is not None]
    return {"media": statistics.fmean(validos) if validos else None, "n": len(validos)}


def auc(previsto: Sequence[float], desfecho: Sequence[int]) -> float | None:
    """
    Área sob a curva ROC (Mann-Whitney): fração dos pares (positivo, negativo)
    em que o positivo tem previsto maior; empate vale 1/2. None sem as duas
    classes.
    """
    if len(previsto) != len(desfecho):
        raise ValueError("previsto e desfecho precisam ter o mesmo tamanho")
    n_pos = sum(1 for d in desfecho if d)
    n_neg = len(desfecho) - n_pos
    if n_pos == 0 or n_neg == 0:
        return None
    postos = _postos(previsto)
    soma = sum(p for p, d in zip(postos, desfecho, strict=True) if d)
    return (soma - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def ic_bootstrap_por_bloco(
    blocos: Mapping[str, Sequence[float]],
    *,
    reps: int = 2000,
    nivel: float = 0.95,
    seed: int = 0,
) -> dict[str, float | int] | None:
    """
    Média dos blocos e intervalo de confiança dela (bootstrap percentil,
    blocos sorteados com reposição). Cada bloco pesa igual, qualquer que seja
    o nº de valores dele. None com menos de 2 blocos com valor.
    """
    medias = [statistics.fmean(v) for _, v in sorted(blocos.items()) if v]
    if len(medias) < 2:
        return None
    rng = random.Random(seed)
    n = len(medias)
    amostras = sorted(statistics.fmean(rng.choices(medias, k=n)) for _ in range(reps))
    alfa = (1 - nivel) / 2
    return {
        "media": statistics.fmean(medias),
        "ic_inf": amostras[int(alfa * reps)],
        "ic_sup": amostras[min(reps - 1, math.ceil((1 - alfa) * reps) - 1)],
        "blocos": n,
    }
