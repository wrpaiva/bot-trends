# src/domain/scoring.py

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .percentile import Normalization
from .trend_models import NumericScoreResult, TrendInput


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def normalize_log(x: float | None, hi: float) -> float:
    """0..1 em escala log: ritmo varia em ordens de grandeza (10/h a 200 mil/h)."""
    if not x or x <= 0 or hi <= 0:
        return 0.0
    return clamp(math.log1p(x) / math.log1p(hi), 0.0, 1.0)


# Tetos da escala log no modo absoluto (views/h e engajamento/h)
VIEWS_PER_HOUR_CAP = 100_000.0
ENGAGEMENT_PER_HOUR_CAP = 10_000.0


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


# Classificação quando o LLM falha (só o numérico). Recalibrada em 2026-10-03:
# com rank_momentum = 0 e reviews no percentil 0,5 (TIE-16) o teto é ~67,5, e os
# cortes antigos (75/85) deixavam tudo ESTAVEL. SUBINDO = ALERT_THRESHOLD
# default, para o alerta do fallback não sair rotulado como ESTAVEL;
# VIRALIZANDO exige ~p97 em todos os sinais medidos (nenhum vídeo no replay de
# 24–25/09). Ligou a TIE-16? O teto vai a 100: recalibre.
FALLBACK_SUBINDO = 60.0
FALLBACK_VIRALIZANDO = 66.0


def fallback_classification(numeric_score: float) -> str:
    if numeric_score >= FALLBACK_VIRALIZANDO:
        return "VIRALIZANDO"
    if numeric_score >= FALLBACK_SUBINDO:
        return "SUBINDO"
    return "ESTAVEL"


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
        # Sem preço (vídeo do TikTok) = neutro; antes ganhava estabilidade máxima
        nm_price_stability = 0.5 if ti.price is None else 1.0 - clamp(ti.price_volatility, 0.0, 1.0)
        # Com idade conhecida o sinal é ritmo (views/h); sem ela, o total legado
        ritmo = ti.views_per_hour is not None

        if normalization is not None:
            p = normalization.values
            nm_reviews = p["reviews_velocity"]
            nm_social = p["social_velocity"]
            nm_views = p["views_per_hour"]
            nm_eng = p["engagement_per_hour"]
            basis = normalization.basis
        else:
            nm_reviews = normalize_0_1(ti.reviews_velocity, 0.0, 50.0)
            nm_social = normalize_0_1(ti.social_velocity, 0.0, 1.0)  # 0..100%+
            if ritmo:
                nm_views = normalize_log(ti.views_per_hour, VIEWS_PER_HOUR_CAP)
                nm_eng = normalize_log(ti.engagement_per_hour, ENGAGEMENT_PER_HOUR_CAP)
            else:
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
                "view_signal": "views_per_hour" if ritmo else "total",
            },
        )
