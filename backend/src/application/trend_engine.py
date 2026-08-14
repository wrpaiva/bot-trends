# src/application/trend_engine.py

from __future__ import annotations
from typing import Dict, Any

from src.domain.scoring import NumericScoreStrategy, clamp
from src.domain.trend_models import TrendInput, HybridResult
from src.application.prompt_builder import build_trend_prompt
from src.domain.interfaces import LLMClient


class HybridTrendEngine:
    def __init__(
        self,
        numeric_strategy: NumericScoreStrategy,
        llm_client: LLMClient,
        w_numeric: float = 0.6,
        w_llm: float = 0.4,
    ):
        self.numeric = numeric_strategy
        self.llm = llm_client
        self.w_numeric = w_numeric
        self.w_llm = w_llm

    def run(self, ti: TrendInput) -> HybridResult:
        numeric_res = self.numeric.compute(ti)
        numeric_score = numeric_res.score_0_100

        llm_res = None
        llm_error = None

        try:
            prompt = build_trend_prompt(ti, numeric_res)
            llm_res = self.llm.analyze_trend(prompt["system"], prompt["user"])
        except Exception as e:
            llm_error = str(e)

        if llm_res:
            llm_score = clamp(llm_res.potential_score_0_100, 0.0, 100.0)
            final = round(self.w_numeric * numeric_score + self.w_llm * llm_score, 2)

            return HybridResult(
                product_id=ti.product_id,
                numeric_score_0_100=numeric_score,
                llm_score_0_100=llm_score,
                final_score_0_100=final,
                trend_classification=llm_res.trend_classification,
                risk_level=llm_res.risk_level,
                analysis=llm_res.analysis,
                recommendation=llm_res.recommendation,
                debug={
                    "numeric_components": numeric_res.components,
                    "llm_confidence": llm_res.confidence_0_1,
                },
            )

        # fallback sem LLM
        cls = "ESTAVEL"
        if numeric_score >= 75:
            cls = "SUBINDO"
        if numeric_score >= 85:
            cls = "VIRALIZANDO"

        return HybridResult(
            product_id=ti.product_id,
            numeric_score_0_100=numeric_score,
            llm_score_0_100=0.0,
            final_score_0_100=numeric_score,
            trend_classification=cls,
            risk_level="MEDIO",
            analysis="LLM indisponível. Classificação baseada apenas no score matemático.",
            recommendation="Validar manualmente e reprocessar quando a IA estiver disponível.",
            debug={"numeric_components": numeric_res.components, "llm_error": llm_error},
        )