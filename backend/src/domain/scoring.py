# src/domain/scoring.py

from __future__ import annotations
from typing import Dict
from .trend_models import TrendInput, NumericScoreResult


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def normalize_0_1(x: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return clamp((x - lo) / (hi - lo), 0.0, 1.0)


class NumericScoreStrategy:
    """
    Score matemático explicável (0..100).
    Ajustável por pesos (pode virar config depois).
    """

    def compute(self, ti: TrendInput) -> NumericScoreResult:
        # Normalizações (0..1)
        nm_rank = clamp(ti.rank_momentum, 0.0, 1.0)
        nm_reviews = normalize_0_1(ti.reviews_velocity, 0.0, 50.0)     # ajuste
        nm_social = normalize_0_1(ti.social_velocity, 0.0, 1.0)        # 0..100%+
        nm_views = normalize_0_1(ti.views_24h, 0.0, 300_000.0)         # ajuste
        nm_eng = normalize_0_1(ti.engagement_24h, 0.0, 20_000.0)       # ajuste
        nm_price_stability = 1.0 - clamp(ti.price_volatility, 0.0, 1.0)

        # Pesos
        w_rank = 0.20
        w_reviews = 0.15
        w_social = 0.25
        w_views = 0.15
        w_eng = 0.15
        w_price = 0.10

        score_0_1 = (
            w_rank * nm_rank +
            w_reviews * nm_reviews +
            w_social * nm_social +
            w_views * nm_views +
            w_eng * nm_eng +
            w_price * nm_price_stability
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
            },
        )