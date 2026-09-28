# src/application/trend_engine.py

from __future__ import annotations

import logging

from src.application.prompt_builder import build_trend_prompt
from src.domain.interfaces import LLMCache, LLMClient
from src.domain.llm_cache import llm_cache_key
from src.domain.percentile import Normalization
from src.domain.scoring import HybridWeights, NumericScoreStrategy, clamp
from src.domain.trend_models import HybridResult, TrendInput

logger = logging.getLogger(__name__)


class HybridTrendEngine:
    def __init__(
        self,
        numeric_strategy: NumericScoreStrategy,
        llm_client: LLMClient,
        w_numeric: float = 0.6,
        w_llm: float = 0.4,
        cache: LLMCache | None = None,
    ):
        self.numeric = numeric_strategy
        self.llm = llm_client
        # Valida (soma 1.0, não negativos) — levanta ValueError
        self.hybrid = HybridWeights(numeric=w_numeric, llm=w_llm)
        self.w_numeric = w_numeric
        self.w_llm = w_llm
        # TIE-25: reaproveita a análise do LLM quando as métricas não mudaram
        self.cache = cache
        self.cache_hits = 0
        self.cache_misses = 0

    def weights_snapshot(self) -> dict[str, dict[str, float]]:
        """Pesos em uso, para gravar junto do insight (rastreabilidade, TIE-22)."""
        return {"numeric": self.numeric.weights.as_dict(), "hybrid": self.hybrid.as_dict()}

    def run(self, ti: TrendInput, normalization: Normalization | None = None) -> HybridResult:
        numeric_res = self.numeric.compute(ti, normalization)
        numeric_score = numeric_res.score_0_100

        llm_res = None
        llm_error = None
        cached = False
        key = llm_cache_key(ti) if self.cache is not None else None

        if key is not None:
            try:
                llm_res = self.cache.get(key)
            except Exception as e:  # noqa: BLE001 — cache fora não derruba a análise
                logger.warning("Cache do LLM indisponível na leitura: %s", e)
            cached = llm_res is not None
            if cached:
                self.cache_hits += 1
            else:
                self.cache_misses += 1

        try:
            if llm_res is None:
                prompt = build_trend_prompt(ti, numeric_res)
                llm_res = self.llm.analyze_trend(prompt["system"], prompt["user"])
                if key is not None:
                    try:
                        self.cache.set(key, llm_res)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("Cache do LLM indisponível na escrita: %s", e)
        except Exception as e:
            # Resposta inválida, timeout, gateway fora: nada disso derruba a task
            llm_error = str(e)
            logger.warning(
                "LLM falhou para %s, usando só o score numérico: %s", ti.product_id, llm_error
            )

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
                    "llm_cached": cached,
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
            debug={
                "numeric_components": numeric_res.components,
                "llm_error": llm_error,
                "llm_cached": False,
            },
        )
