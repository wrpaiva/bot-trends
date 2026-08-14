# tests/test_trend_engine.py

from src.domain.trend_models import TrendInput, LLMResult
from src.domain.scoring import NumericScoreStrategy
from src.application.trend_engine import HybridTrendEngine
from src.domain.interfaces import LLMClient


class FakeLLM(LLMClient):
    def __init__(self, score=90.0, cls="VIRALIZANDO", risk="MEDIO", fail=False):
        self.score = score
        self.cls = cls
        self.risk = risk
        self.fail = fail

    def analyze_trend(self, system: str, user: str) -> LLMResult:
        if self.fail:
            raise RuntimeError("LLM down")

        return LLMResult(
            trend_classification=self.cls,
            potential_score_0_100=float(self.score),
            risk_level=self.risk,
            analysis="ok",
            recommendation="buy",
            confidence_0_1=0.8,
        )


def base_input(**overrides):
    data = dict(
        product_id="uuid-1",
        title="Produto Y",
        category="cat",
        price=100.0,
        sold_quantity=None,
        views_24h=20_000,
        engagement_24h=1200,
        mentions_24h=20,
        rank_momentum=0.4,
        reviews_velocity=5.0,
        social_velocity=0.25,
        price_volatility=0.05,
        previous_final_score=None,
    )
    data.update(overrides)
    return TrendInput(**data)


def test_hybrid_combination_uses_weights():
    numeric = NumericScoreStrategy()
    llm = FakeLLM(score=80.0)
    engine = HybridTrendEngine(numeric, llm, w_numeric=0.6, w_llm=0.4)

    ti = base_input()
    res = engine.run(ti)

    # numeric calculado
    n = numeric.compute(ti).score_0_100
    expected = round(0.6 * n + 0.4 * 80.0, 2)

    assert res.llm_score_0_100 == 80.0
    assert res.final_score_0_100 == expected
    assert res.trend_classification in ("ESTAVEL", "SUBINDO", "VIRALIZANDO", "PICO_TEMPORARIO", "EM_QUEDA")


def test_engine_fallback_when_llm_fails():
    numeric = NumericScoreStrategy()
    llm = FakeLLM(fail=True)
    engine = HybridTrendEngine(numeric, llm)

    ti = base_input()
    res = engine.run(ti)

    # fallback: final == numeric e llm_score == 0
    n = numeric.compute(ti).score_0_100
    assert res.llm_score_0_100 == 0.0
    assert res.final_score_0_100 == n
    assert "LLM indisponível" in res.analysis