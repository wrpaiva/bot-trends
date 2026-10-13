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
    """
    Pesos do score numérico (TIE-22). Só as dimensões que existem para a fonte
    entram no cálculo, e os pesos delas são renormalizados para somar 1 (TIE-48).
    """

    rank_position: float = 0.15
    rank: float = 0.15
    reviews: float = 0.10
    social: float = 0.25
    views: float = 0.15
    engagement: float = 0.15
    price_stability: float = 0.05

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


# Classificação quando o LLM falha (só o numérico), por fonte: (SUBINDO,
# VIRALIZANDO). Recalibrada na TIE-43 com replay das coletas reais pelo motor
# pós TIE-48 (2026-10-13; racional no README, "Alertas"):
# - ML: numérico com mediana ~35 e p95 57. SUBINDO = ALERT_THRESHOLD default,
#   para o alerta do fallback não sair rotulado como ESTAVEL (16 de 20 itens
#   acima de 62 o LLM v5 chamou de SUBINDO); VIRALIZANDO só com ~p99.
# - TikTok: só percentis de social/views/engajamento, mediana ~50 e p90 78.
#   SUBINDO = ALERT_THRESHOLD_TIKTOK (~top 10%), VIRALIZANDO ~top 5%.
#   Provisório: 1 dia de vídeos (24–25/09), sem LLM; recalibrar quando a
#   coleta voltar.
# Fonte desconhecida (dado legado) usa os cortes do ML.
FALLBACK_CORTES: dict[str, tuple[float, float]] = {
    "mercadolivre": (62.0, 80.0),
    "tiktok": (85.0, 92.0),
}
FALLBACK_CORTES_PADRAO = FALLBACK_CORTES["mercadolivre"]


def fallback_classification(numeric_score: float, source: str | None = None) -> str:
    subindo, viralizando = FALLBACK_CORTES.get(source or "", FALLBACK_CORTES_PADRAO)
    if numeric_score >= viralizando:
        return "VIRALIZANDO"
    if numeric_score >= subindo:
        return "SUBINDO"
    return "ESTAVEL"


# TIE-48: dimensões que existem em cada fonte. Item do ML não tem vídeo; vídeo
# do TikTok não tem ranking, avaliação nem preço. Fonte desconhecida (dado
# legado) pontua tudo, como antes.
DIMENSOES_POR_FONTE: dict[str, tuple[str, ...]] = {
    "mercadolivre": ("rank_position", "rank", "reviews", "price_stability"),
    "tiktok": ("social", "views", "engagement"),
}

# Métrica do percentil → dimensão do score
_DIMENSAO_DA_METRICA = {
    "reviews_velocity": "reviews",
    "social_velocity": "social",
    "views_per_hour": "views",
    "engagement_per_hour": "engagement",
}

# Chave de cada dimensão em `components` (nomes históricos, lidos pelo backtest)
_COMPONENTE = {
    "rank_position": "nm_rank_position",
    "rank": "nm_rank",
    "reviews": "nm_reviews",
    "social": "nm_social_velocity",
    "views": "nm_views",
    "engagement": "nm_engagement",
    "price_stability": "nm_price_stability",
}

# /highlights devolve o top 20 de cada categoria
RANKING_SIZE = 20


def rank_position_score(posicao: int) -> float:
    """1 no topo do ranking, 0 na última posição (ou fora do top)."""
    return clamp((RANKING_SIZE - posicao) / (RANKING_SIZE - 1), 0.0, 1.0)


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
        nao_medidas: set[str] = set()
        nm_rank = clamp(ti.rank_momentum, 0.0, 1.0)
        if ti.rank_position is None:
            nm_rank_position = 0.0
            nao_medidas.add("rank_position")
        else:
            nm_rank_position = rank_position_score(ti.rank_position)
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
            nao_medidas |= {_DIMENSAO_DA_METRICA[m] for m in normalization.nao_medidas}
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

        valores = {
            "rank_position": nm_rank_position,
            "rank": nm_rank,
            "reviews": nm_reviews,
            "social": nm_social,
            "views": nm_views,
            "engagement": nm_eng,
            "price_stability": nm_price_stability,
        }
        da_fonte = DIMENSOES_POR_FONTE.get(ti.source or "", tuple(valores))
        dimensoes = [d for d in valores if d in da_fonte and d not in nao_medidas]

        pesos = self.weights.as_dict()
        total = sum(pesos[d] for d in dimensoes)
        # Nenhuma dimensão com peso: não há o que pontuar
        pesos_efetivos = {d: pesos[d] / total if total else 0.0 for d in dimensoes}
        score_0_1 = sum(pesos_efetivos[d] * valores[d] for d in dimensoes)

        score = round(score_0_1 * 100.0, 2)
        return NumericScoreResult(
            score_0_100=score,
            components={
                **{_COMPONENTE[d]: (v if d in dimensoes else None) for d, v in valores.items()},
                "score_0_1": score_0_1,
                "normalization": basis,
                "view_signal": "views_per_hour" if ritmo else "total",
                "dimensoes": dimensoes,
                "pesos_efetivos": pesos_efetivos,
            },
        )
