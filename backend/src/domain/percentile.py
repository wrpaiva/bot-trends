# src/domain/percentile.py
"""
Normalização por percentil dentro da categoria (TIE-21). Regra pura, sem I/O.

Cada métrica de volume vira o percentil do produto entre os da mesma
categoria no ciclo de análise — 5 mil views é excepcional para capinha e
medíocre para celular.

Fallback, nesta ordem:
  1. categoria com >= `min_group` produtos → percentil na categoria;
  2. senão, pool global (todos do ciclo) com >= `min_group` → percentil global;
  3. senão → None: o scoring usa a normalização absoluta (faixas fixas).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .trend_models import TrendInput

# Métricas de volume, cuja escala depende da categoria. rank_momentum já vem
# em 0..1 e a estabilidade de preço é relativa por construção.
PERCENTILE_METRICS = ("views_24h", "engagement_24h", "social_velocity", "reviews_velocity")


def percentile_rank(value: float, population: list[float]) -> float:
    """Percentil por midrank, em (0, 1): empates ficam no meio, não no topo."""
    n = len(population)
    if n == 0:
        return 0.5
    abaixo = sum(1 for x in population if x < value)
    iguais = sum(1 for x in population if x == value)
    return (abaixo + 0.5 * iguais) / n


@dataclass(frozen=True)
class Normalization:
    values: dict[str, float]
    basis: str  # "categoria" | "global"


class PercentileContext:
    def __init__(self, inputs: list[TrendInput], *, min_group: int = 5):
        if min_group < 2:
            raise ValueError("min_group precisa ser >= 2: percentil de 1 produto não diz nada")
        self.min_group = min_group
        self._por_categoria: dict[str | None, list[TrendInput]] = defaultdict(list)
        for ti in inputs:
            self._por_categoria[ti.category].append(ti)
        self._global = list(inputs)

    def normalize(self, ti: TrendInput) -> Normalization | None:
        grupo = self._por_categoria.get(ti.category, [])
        if len(grupo) >= self.min_group:
            pool, basis = grupo, "categoria"
        elif len(self._global) >= self.min_group:
            pool, basis = self._global, "global"
        else:
            return None

        return Normalization(
            values={
                m: percentile_rank(getattr(ti, m), [getattr(x, m) for x in pool])
                for m in PERCENTILE_METRICS
            },
            basis=basis,
        )
