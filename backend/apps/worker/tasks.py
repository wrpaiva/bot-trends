# apps/worker/tasks.py

import datetime as dt
import os
from celery import shared_task

from src.infrastructure.db.mongo import get_db
from src.infrastructure.db.repos import ProductRepo, MetricsRepo
from src.infrastructure.collectors.mercado_livre import MercadoLivreCollector
from src.infrastructure.collectors.tiktok import TikTokApifyCollector


# =========================================================
# 📦 MERCADO LIVRE
# =========================================================

@shared_task(name="tasks.collect_ml")
def collect_ml():
    db = get_db()
    products = ProductRepo(db)
    metrics = MetricsRepo(db)

    categories = list(
        db["categories"].find(
            {"enabled": True, "ml_category_id": {"$exists": True}},
            {"ml_category_id": 1}
        )
    )

    category_ids = [c["ml_category_id"] for c in categories]
    if not category_ids:
        return {"status": "no enabled categories"}

    now = dt.datetime.utcnow()
    count = 0

    with MercadoLivreCollector(categories=category_ids) as collector:
        for item in collector.collect():
            product_id = products.upsert(item)

            metrics.insert({
                "ts": now,
                "product_id": product_id,
                "source": "mercadolivre",
                "rank_position": None,
                "price": item.get("price"),
                "reviews_total": None,
                "reviews_delta": 0,
                "mentions": 0,
                "engagement": 0,
                "views": 0,
            })
            count += 1

    return {"status": "ok", "inserted": count}


# =========================================================
# 📱 TIKTOK
# =========================================================

@shared_task(name="tasks.collect_tiktok")
def collect_tiktok():
    db = get_db()
    products = ProductRepo(db)
    metrics = MetricsRepo(db)

    hashtags = os.getenv("TIKTOK_HASHTAGS", "fyp").split(",")
    now = dt.datetime.utcnow()
    count = 0

    with TikTokApifyCollector(hashtags=hashtags) as collector:
        for item in collector.collect():
            product_id = products.upsert(item)

            social = item.get("social", {})
            engagement = (
                int(social.get("likes", 0) or 0)
                + int(social.get("comments", 0) or 0)
                + int(social.get("shares", 0) or 0)
            )

            metrics.insert({
                "ts": now,
                "product_id": product_id,
                "source": "tiktok",
                "rank_position": None,
                "price": None,
                "reviews_total": None,
                "reviews_delta": 0,
                "mentions": 1,
                "engagement": engagement,
                "views": int(social.get("views", 0) or 0),
            })
            count += 1

    return {"status": "ok", "inserted": count}