# apps/worker/tasks_trend.py

import datetime as dt
import logging

import redis
from celery import shared_task

from src.application.trend_engine import HybridTrendEngine
from src.domain.alerting import decide_alert
from src.domain.commercial import commercial_marker
from src.domain.interfaces import LLMClient
from src.domain.percentile import PercentileContext
from src.domain.scoring import NumericScoreStrategy
from src.domain.trend_models import LLMResult, TrendInput
from src.domain.trend_signals import Reading, compute_signals, is_too_old
from src.infrastructure.circuit_breaker import BreakerLLMClient, breaker_for
from src.infrastructure.config import settings
from src.infrastructure.db.alert_repos import AlertStateRepo
from src.infrastructure.db.mongo import get_db
from src.infrastructure.db.trend_repos import TrendInsightRepo
from src.infrastructure.llm.cache import RedisLLMCache
from src.infrastructure.llm.openai_compatible import OpenAICompatibleLLMClient
from src.infrastructure.telegram.notifier import TelegramError, TelegramNotifier
from src.infrastructure.utils.datetime_utils import ensure_utc, utcnow

logger = logging.getLogger(__name__)


class _LLMIndisponivel(LLMClient):
    """LLM sem config: toda chamada falha e o engine cai no score numérico."""

    def __init__(self, motivo: str):
        self.motivo = motivo

    def analyze_trend(self, system: str, user: str) -> LLMResult:
        raise RuntimeError(f"LLM indisponível: {self.motivo}")


def _build_llm() -> LLMClient:
    try:
        client = OpenAICompatibleLLMClient()
    except RuntimeError as e:
        logger.warning("LLM não configurado, análise só numérica: %s", e)
        return _LLMIndisponivel(str(e))
    # Falha prolongada (ex.: 429 por cota) abre o circuito e o resto do ciclo
    # vai direto para o score numérico, sem gastar requisição (TIE-37)
    return BreakerLLMClient(client, breaker_for("llm"))


def _build_llm_cache() -> RedisLLMCache | None:
    """Cache do LLM no Redis (TIE-25), ou None com `LLM_CACHE_TTL_HOURS=0`."""
    if settings.LLM_CACHE_TTL_HOURS <= 0:
        return None
    client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
    return RedisLLMCache(
        client, model=settings.LLM_MODEL, ttl_s=settings.LLM_CACHE_TTL_HOURS * 3600
    )


def _build_notifier() -> TelegramNotifier | None:
    try:
        return TelegramNotifier()
    except RuntimeError as e:
        logger.warning("Telegram não configurado, alertas desligados: %s", e)
        return None


def _calc_social_velocity(metrics):
    if len(metrics) < 4:
        return 0.0

    half = len(metrics) // 2
    recent = metrics[:half]
    prev = metrics[half:]

    def avg(arr, key):
        vals = [(m.get(key) or 0) for m in arr]
        return sum(vals) / max(len(vals), 1)

    recent_avg = avg(recent, "engagement")
    prev_avg = avg(prev, "engagement")

    return float((recent_avg - prev_avg) / max(prev_avg, 1))


def _calc_price_volatility(metrics):
    prices = [m.get("price") for m in metrics if m.get("price")]
    if len(prices) < 3:
        return 0.0

    mn, mx = min(prices), max(prices)
    if mn <= 0:
        return 0.0

    return min(float((mx - mn) / mn), 1.0)


# Sentinelas: vídeo mais velho que TREND_MAX_AGE_DAYS (não é tendência) e
# vídeo sem intenção comercial (dança, meme — TIE-18)
VELHO = object()
SEM_PRODUTO = object()


def _build_input(db, product_id: str, since: dt.datetime):
    """
    (TrendInput, fontes, sinais | None, marcador comercial | None), `VELHO`,
    `SEM_PRODUTO`, ou None se faltar produto ou métricas. Com data de
    publicação, views e engajamento viram ritmo (por hora de vida) e
    `social_velocity` vira aceleração/frescor; sem ela, fica o cálculo antigo
    (produto do ML, vídeo coletado antes de gravarmos a data).
    """
    product = db["products"].find_one(
        {"product_id": product_id},
        {
            "_id": 0,
            "source": 1,
            "title": 1,
            "category": 1,
            "published_at": 1,
            "has_shop_product": 1,
        },
    )
    if not product:
        return None

    # Só vídeo do TikTok passa pelo filtro: item de marketplace já é produto
    marcador = None
    if product.get("source") == "tiktok":
        marcador = commercial_marker(
            product.get("title"), has_shop_product=bool(product.get("has_shop_product"))
        )
        if marcador is None and settings.TREND_REQUIRE_COMMERCIAL:
            return SEM_PRODUTO

    metrics = list(
        db["metrics"]
        .find({"product_id": product_id, "ts": {"$gte": since}}, {"_id": 0})
        .sort("ts", -1)
        .limit(24)
    )
    if not metrics:
        return None

    last = metrics[0]
    sources = list({m.get("source") for m in metrics if m.get("source")})

    sinais = None
    if product.get("published_at"):
        sinais = compute_signals(
            [
                Reading(
                    ts=ensure_utc(m["ts"]),
                    views=int(m.get("views") or 0),
                    engagement=int(m.get("engagement") or 0),
                )
                for m in metrics
            ],
            ensure_utc(product["published_at"]),
            min_age_hours=settings.TREND_MIN_AGE_HOURS,
        )
        if is_too_old(sinais.age_hours, max_age_days=settings.TREND_MAX_AGE_DAYS):
            return VELHO

    ti = TrendInput(
        product_id=product_id,
        title=product.get("title"),
        category=product.get("category"),
        price=last.get("price"),
        sold_quantity=None,
        views_24h=int(last.get("views", 0) or 0),
        engagement_24h=int(last.get("engagement", 0) or 0),
        mentions_24h=int(last.get("mentions", 0) or 0),
        rank_momentum=0.0,
        reviews_velocity=0.0,
        social_velocity=sinais.social_velocity if sinais else _calc_social_velocity(metrics),
        price_volatility=_calc_price_volatility(metrics),
        previous_final_score=None,
        age_hours=sinais.age_hours if sinais else None,
        views_per_hour=sinais.views_per_hour if sinais else None,
        engagement_per_hour=sinais.engagement_per_hour if sinais else None,
        has_shop_product=bool(product.get("has_shop_product")),
    )
    return ti, sources, sinais, marcador


@shared_task(name="tasks.hybrid_trend_analyze")
def hybrid_trend_analyze(
    hours: int = 72, limit_products: int = 50, alert_threshold: float | None = None
):
    db = get_db()
    insight_repo = TrendInsightRepo(db)
    alert_repo = AlertStateRepo(db)
    threshold = settings.ALERT_THRESHOLD if alert_threshold is None else alert_threshold
    cooldown = dt.timedelta(hours=settings.ALERT_COOLDOWN_HOURS)
    alertas = {"alerts_sent": 0, "alerts_suppressed": 0, "alerts_failed": 0}

    since = utcnow() - dt.timedelta(hours=hours)
    window_from = since
    window_to = utcnow()

    # Produtos ativos
    active = list(
        db["metrics"].aggregate(
            [
                {"$match": {"ts": {"$gte": since}}},
                {"$group": {"_id": "$product_id", "last_ts": {"$max": "$ts"}}},
                {"$sort": {"last_ts": -1}},
                {"$limit": limit_products},
            ]
        )
    )

    hybrid = settings.hybrid_weights()
    engine = HybridTrendEngine(
        NumericScoreStrategy(settings.score_weights()),
        _build_llm(),
        w_numeric=hybrid.numeric,
        w_llm=hybrid.llm,
        cache=_build_llm_cache(),
    )
    pesos = engine.weights_snapshot()
    notifier = _build_notifier()

    # Fase 1: monta as entradas de todos os produtos do ciclo — o percentil
    # (TIE-21) precisa da população inteira antes de pontuar qualquer um.
    brutas = [_build_input(db, row["_id"], since) for row in active]
    velhos = sum(1 for e in brutas if e is VELHO)
    sem_produto = sum(1 for e in brutas if e is SEM_PRODUTO)
    entradas = [e for e in brutas if e not in (None, VELHO, SEM_PRODUTO)]

    contexto = (
        PercentileContext(
            [ti for ti, *_ in entradas], min_group=settings.SCORE_PERCENTILE_MIN_GROUP
        )
        if settings.SCORE_NORMALIZATION == "percentile"
        else None
    )

    # Fase 2: pontua, grava e alerta
    for ti, sources, sinais, marcador in entradas:
        product_id = ti.product_id
        result = engine.run(ti, contexto.normalize(ti) if contexto else None)

        insight_repo.insert(
            product_id=product_id,
            window_from=window_from,
            window_to=window_to,
            window_hours=hours,
            payload={
                "numeric_score": result.numeric_score_0_100,
                "llm_score": result.llm_score_0_100,
                "final_score": result.final_score_0_100,
                "trend_classification": result.trend_classification,
                "risk_level": result.risk_level,
                "analysis": result.analysis,
                "recommendation": result.recommendation,
                "sources": sources,
                # O que fez o vídeo contar como produto (TIE-18); None fora do TikTok
                "commercial_marker": marcador,
                "debug": result.debug,
                # Motor de tendência: ritmo e idade que entraram no score
                "signals": (
                    {
                        "age_hours": round(sinais.age_hours, 2),
                        "views_per_hour": round(sinais.views_per_hour, 2),
                        "engagement_per_hour": round(sinais.engagement_per_hour, 2),
                        "social_velocity": round(sinais.social_velocity, 4),
                        "has_shop_product": ti.has_shop_product,
                    }
                    if sinais
                    else None
                ),
                # Pesos usados neste insight, para comparar calibrações (TIE-22)
                "score_weights": pesos,
            },
        )

        if notifier is None:
            continue

        # Dedupe/throttle (TIE-31): um alerta por produto por cooldown, salvo
        # se a classificação subir de faixa.
        now = utcnow()
        decisao = decide_alert(
            final_score=result.final_score_0_100,
            classification=result.trend_classification,
            last=alert_repo.get(product_id),
            now=now,
            threshold=threshold,
            cooldown=cooldown,
        )
        if not decisao.send:
            if result.final_score_0_100 >= threshold:
                alertas["alerts_suppressed"] += 1
            continue

        try:
            notifier.send(
                f"📈 Tendência detectada\n"
                f"Produto: {ti.title}\n"
                f"Score: {result.final_score_0_100}\n"
                f"Status: {result.trend_classification}\n"
                f"Risco: {result.risk_level}\n\n"
                f"{result.recommendation}"
            )
        except TelegramError as e:
            # Não grava estado: o próximo ciclo tenta de novo
            logger.warning("Alerta de %s não enviado: %s", product_id, e)
            alertas["alerts_failed"] += 1
            continue

        alert_repo.record(
            product_id,
            alerted_at=now,
            classification=result.trend_classification,
            final_score=result.final_score_0_100,
        )
        alertas["alerts_sent"] += 1

    total = engine.cache_hits + engine.cache_misses
    logger.info(
        "llm.cache",
        extra={
            "hits": engine.cache_hits,
            "misses": engine.cache_misses,
            "hit_rate": round(engine.cache_hits / total, 3) if total else None,
        },
    )
    return {
        "status": "ok",
        "processed": len(active),
        "skipped_too_old": velhos,
        "skipped_no_product": sem_produto,
        **alertas,
        "llm_cache_hits": engine.cache_hits,
        "llm_cache_misses": engine.cache_misses,
    }
