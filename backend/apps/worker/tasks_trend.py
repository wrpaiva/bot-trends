# apps/worker/tasks_trend.py

import datetime as dt
from celery import shared_task

from src.infrastructure.db.mongo import get_db
from src.infrastructure.db.trend_repos import TrendInsightRepo
from src.infrastructure.telegram.notifier import TelegramNotifier

from src.domain.trend_models import TrendInput
from src.domain.scoring import NumericScoreStrategy
from src.application.trend_engine import HybridTrendEngine
from src.infrastructure.llm.openai_compatible import OpenAICompatibleLLMClient


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


@shared_task(name="tasks.hybrid_trend_analyze")
def hybrid_trend_analyze(hours: int = 72, limit_products: int = 50, alert_threshold: float = 85.0):
    db = get_db()
    insight_repo = TrendInsightRepo(db)

    since = dt.datetime.utcnow() - dt.timedelta(hours=hours)
    window_from = since
    window_to = dt.datetime.utcnow()

    # Produtos ativos
    active = list(
        db["metrics"].aggregate([
            {"$match": {"ts": {"$gte": since}}},
            {"$group": {"_id": "$product_id", "last_ts": {"$max": "$ts"}}},
            {"$sort": {"last_ts": -1}},
            {"$limit": limit_products},
        ])
    )

    numeric_strategy = NumericScoreStrategy()
    llm_client = OpenAICompatibleLLMClient()
    engine = HybridTrendEngine(numeric_strategy, llm_client)

    notifier = TelegramNotifier()

    for row in active:
        product_id = row["_id"]

        product = db["products"].find_one(
            {"product_id": product_id},
            {"_id": 0, "title": 1, "category": 1}
        )

        if not product:
            continue

        metrics = list(
            db["metrics"]
            .find({"product_id": product_id, "ts": {"$gte": since}}, {"_id": 0})
            .sort("ts", -1)
            .limit(24)
        )

        if not metrics:
            continue

        last = metrics[0]

        social_velocity = _calc_social_velocity(metrics)
        price_volatility = _calc_price_volatility(metrics)

        sources = list({m.get("source") for m in metrics if m.get("source")})

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
            social_velocity=social_velocity,
            price_volatility=price_volatility,
            previous_final_score=None,
        )

        result = engine.run(ti)

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
                "debug": result.debug,
            }
        )

        if result.final_score_0_100 >= alert_threshold:
            notifier.send(
                f"📈 Tendência detectada\n"
                f"Produto: {ti.title}\n"
                f"Score: {result.final_score_0_100}\n"
                f"Status: {result.trend_classification}\n"
                f"Risco: {result.risk_level}\n\n"
                f"{result.recommendation}"
            )

    return {"status": "ok", "processed": len(active)}