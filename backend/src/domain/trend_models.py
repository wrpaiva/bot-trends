# src/domain/trend_models.py

from dataclasses import dataclass
from typing import Optional, Literal, Dict, Any

TrendClass = Literal["ESTAVEL", "SUBINDO", "VIRALIZANDO", "PICO_TEMPORARIO", "EM_QUEDA"]
RiskLevel = Literal["BAIXO", "MEDIO", "ALTO"]


@dataclass(frozen=True)
class TrendInput:
    product_id: str
    title: str
    category: Optional[str]

    # marketplace (último ponto observado)
    price: Optional[float]
    sold_quantity: Optional[int]

    # social (último ponto observado)
    views_24h: int
    engagement_24h: int
    mentions_24h: int

    # histórico / sinais derivados
    rank_momentum: float          # 0..1 (melhora de ranking, quando existir)
    reviews_velocity: float       # delta reviews (quando existir)
    social_velocity: float        # crescimento social relativo (ex.: 0.25 = +25%)
    price_volatility: float       # 0..1 (volatilidade relativa)

    previous_final_score: Optional[float] = None


@dataclass(frozen=True)
class NumericScoreResult:
    score_0_100: float
    components: Dict[str, float]


@dataclass(frozen=True)
class LLMResult:
    trend_classification: TrendClass
    potential_score_0_100: float
    risk_level: RiskLevel
    analysis: str
    recommendation: str
    confidence_0_1: float


@dataclass(frozen=True)
class HybridResult:
    product_id: str
    numeric_score_0_100: float
    llm_score_0_100: float
    final_score_0_100: float
    trend_classification: TrendClass
    risk_level: RiskLevel
    analysis: str
    recommendation: str
    debug: Dict[str, Any]