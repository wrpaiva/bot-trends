# src/domain/trend_models.py

from dataclasses import dataclass
from typing import Any, Literal

TrendClass = Literal["ESTAVEL", "SUBINDO", "VIRALIZANDO", "PICO_TEMPORARIO", "EM_QUEDA"]
RiskLevel = Literal["BAIXO", "MEDIO", "ALTO"]


@dataclass(frozen=True)
class TrendInput:
    product_id: str
    title: str
    category: str | None

    # marketplace (último ponto observado)
    price: float | None
    sold_quantity: int | None

    # social: totais ACUMULADOS na última leitura (o nome "24h" é histórico).
    # Não entram no score quando a idade é conhecida — ver views_per_hour.
    views_24h: int
    engagement_24h: int
    mentions_24h: int

    # histórico / sinais derivados
    rank_momentum: float  # 0..1 (melhora de ranking, quando existir)
    reviews_velocity: float  # delta reviews (quando existir)
    social_velocity: float  # crescimento social relativo (ex.: 0.25 = +25%)
    price_volatility: float  # 0..1 (volatilidade relativa)

    previous_final_score: float | None = None

    # Motor de tendência: ritmo desde a publicação (None = idade desconhecida,
    # ex.: produto do ML, ou vídeo coletado antes de gravarmos a data)
    age_hours: float | None = None
    views_per_hour: float | None = None
    engagement_per_hour: float | None = None
    has_shop_product: bool = False

    # TIE-26: contexto para o LLM. O que fez o vídeo contar como produto
    # (TIE-18) e quantas leituras sustentam os sinais: com uma só,
    # social_velocity = 0 quer dizer "não medido", não "parado".
    commercial_marker: str | None = None
    n_readings: int | None = None

    # TIE-42: contexto de marketplace para o LLM. Item do ML não tem sinal
    # social; o que ele tem é a posição no ranking de mais vendidos (1 = topo),
    # na leitura mais recente e na mais antiga da janela. Com as duas o LLM vê
    # também a queda, que o rank_momentum (só subida) não mostra.
    source: str | None = None
    rank_position: int | None = None
    rank_position_start: int | None = None


@dataclass(frozen=True)
class NumericScoreResult:
    score_0_100: float
    # Componentes normalizados + "normalization" (base usada, TIE-21)
    components: dict[str, float | str]


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
    debug: dict[str, Any]
