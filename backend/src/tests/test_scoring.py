# tests/test_scoring.py

from src.domain.trend_models import TrendInput
from src.domain.scoring import NumericScoreStrategy


def base_input(**overrides):
    data = dict(
        product_id="uuid-1",
        title="Produto X",
        category="cat",
        price=100.0,
        sold_quantity=None,
        views_24h=10_000,
        engagement_24h=500,
        mentions_24h=10,
        rank_momentum=0.1,
        reviews_velocity=2.0,
        social_velocity=0.10,
        price_volatility=0.05,
        previous_final_score=None,
    )
    data.update(overrides)
    return TrendInput(**data)


def test_score_range_0_100():
    s = NumericScoreStrategy()
    ti = base_input()
    res = s.compute(ti)
    assert 0.0 <= res.score_0_100 <= 100.0


def test_score_increases_with_social_velocity():
    s = NumericScoreStrategy()

    low = s.compute(base_input(social_velocity=0.05, engagement_24h=400)).score_0_100
    high = s.compute(base_input(social_velocity=0.80, engagement_24h=400)).score_0_100

    assert high > low


def test_score_rewards_price_stability():
    s = NumericScoreStrategy()

    stable = s.compute(base_input(price_volatility=0.01)).score_0_100
    volatile = s.compute(base_input(price_volatility=0.80)).score_0_100

    assert stable > volatile