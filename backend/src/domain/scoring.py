# src/domain/scoring.py

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .percentile import Normalization
from .trend_models import NumericScoreResult, TrendInput


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def normalize_0_1(x: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return clamp((x - lo) / (hi - lo), 0.0, 1.0)


def _valida_pesos(nome: str, pesos: dict[str, float]) -> None:
    negativos = [k for k, v in pesos.items() if v < 0]
    if negativos:
        raise ValueError(f"{nome}: peso negativo em {negativos}")
    total = sum(pesos.values())
    # Tolerância: 0.1 + 0.2 != 0.3 em ponto flutuante
    if not math.isclose(total, 1.0, abs_tol=1e-6):
        raise ValueError(f"{nome}: os pesos somam {total:.4f}, precisam somar 1.0")


@dataclass(frozen=True)
class ScoreWeights:
    """Pesos do score numérico (TIE-22). Defaults = comportamento histórico."""

    rank: float = 0.20
    reviews: float = 0.15
    social: float = 0.25
    views: float = 0.15
    engagement: float = 0.15
    price_stability: float = 0.10

    def __post_init__(self) -> None:
        _valida_pesos("ScoreWeights", self.as_dict())

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class HybridWeights:
    """Split entre score numérico e LLM no score final."""

    numeric: float = 0.6
    llm: float = 0.4

    def __post_init__(self) -> None:
        _valida_pesos("HybridWeights", self.as_dict())

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


class NumericScoreStrategy:
    """
    Score matemático explicável (0..100), com pesos configuráveis (TIE-22).
    """

    def __init__(self, weights: ScoreWeights | None = None):
        self.weights = weights or ScoreWeights()

    def compute(
        self, ti: TrendInput, normalization: Normalization | None = None
    ) -> NumericScoreResult:
        """
        `normalization` (TIE-21): percentis das métricas de volume dentro da
        categoria. Sem ela, faixas fixas (normalização absoluta).
        """
        nm_rank = clamp(ti.rank_momentum, 0.0, 1.0)
        nm_price_stability = 1.0 - clamp(ti.price_volatility, 0.0, 1.0)

        if normalization is not None:
            p = normalization.values
            nm_reviews = p["reviews_velocity"]
            nm_social = p["social_velocity"]
            nm_views = p["views_24h"]
            nm_eng = p["engagement_24h"]
            basis = normalization.basis
        else:
            nm_reviews = normalize_0_1(ti.reviews_velocity, 0.0, 50.0)
            nm_social = normalize_0_1(ti.social_velocity, 0.0, 1.0)  # 0..100%+
            nm_views = normalize_0_1(ti.views_24h, 0.0, 300_000.0)
            nm_eng = normalize_0_1(ti.engagement_24h, 0.0, 20_000.0)
            basis = "absoluta"

        w = self.weights
        score_0_1 = (
            w.rank * nm_rank
            + w.reviews * nm_reviews
            + w.social * nm_social
            + w.views * nm_views
            + w.engagement * nm_eng
            + w.price_stability * nm_price_stability
        )

        score = round(score_0_1 * 100.0, 2)
        return NumericScoreResult(
            score_0_100=score,
            components={
                "nm_rank": nm_rank,
                "nm_reviews": nm_reviews,
                "nm_social_velocity": nm_social,
                "nm_views": nm_views,
                "nm_engagement": nm_eng,
                "nm_price_stability": nm_price_stability,
                "score_0_1": score_0_1,
                "normalization": basis,
            },
        )
