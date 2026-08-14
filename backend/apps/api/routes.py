# apps/api/routes.py

import datetime as dt
from fastapi import APIRouter, Query, HTTPException, Request
from typing import List

from src.infrastructure.db.mongo import get_db
from src.infrastructure.security.rate_limit import limiter

router = APIRouter()

# =========================================================
# 🔎 HEALTH
# =========================================================

@router.get("/health/migrations")
def health_migrations():
    db = get_db()

    migrations = list(
        db["migrations"]
        .find({}, {"_id": 0})
        .sort("started_at", -1)
        .limit(20)
    )

    failed = [m for m in migrations if m.get("status") == "failed"]
    running = [m for m in migrations if m.get("status") == "running"]

    status = "ok"
    if failed:
        status = "degraded"
    if running:
        status = "running"

    return {
        "status": status,
        "failed_count": len(failed),
        "running_count": len(running),
        "recent": migrations,
    }

# =========================================================
# 📊 RANKINGS
# =========================================================

@router.get("/rankings/latest")
@limiter.limit("30/minute")
def rankings_latest(
    request: Request,  # necessário para o limiter
    source: str = Query("all", pattern="^(all|mercadolivre|tiktok)$"),
    limit: int = Query(20, ge=1, le=200),
    hours: int = Query(72, ge=1, le=720),
):
    """
    Ranking mais recente baseado em final_score.
    Filtra por janela de horas.
    """

    db = get_db()
    now = dt.datetime.utcnow()
    window_from = now - dt.timedelta(hours=hours)

    match = {
        "window_from": {"$gte": window_from},
        "window_hours": hours,
    }

    if source != "all":
        match["sources"] = {"$in": [source]}

    pipeline = [
        {"$match": match},
        {"$sort": {"ts": -1}},
        {"$group": {"_id": "$product_id", "doc": {"$first": "$$ROOT"}}},
        {"$replaceRoot": {"newRoot": "$doc"}},
        {"$sort": {"final_score": -1}},
        {"$limit": limit},
    ]

    insights = list(db["trend_insights"].aggregate(pipeline))

    product_ids = [i["product_id"] for i in insights]
    products = {
        p["product_id"]: p
        for p in db["products"].find(
            {"product_id": {"$in": product_ids}},
            {"_id": 0}
        )
    }

    result = []
    for i in insights:
        p = products.get(i["product_id"], {})
        result.append({
            "product_id": i["product_id"],
            "title": p.get("title"),
            "category": p.get("category"),
            "final_score": i.get("final_score"),
            "numeric_score": i.get("numeric_score"),
            "llm_score": i.get("llm_score"),
            "trend_classification": i.get("trend_classification"),
            "risk_level": i.get("risk_level"),
            "sources": i.get("sources", []),
            "window_hours": i.get("window_hours"),
            "ts": i.get("ts"),
        })

    return {
        "count": len(result),
        "hours": hours,
        "items": result,
    }

# =========================================================
# 📈 INSIGHTS
# =========================================================

@router.get("/insights/latest")
def insights_latest(
    limit: int = Query(20, ge=1, le=200),
    hours: int = Query(72, ge=1, le=720),
):
    db = get_db()
    now = dt.datetime.utcnow()
    window_from = now - dt.timedelta(hours=hours)

    items = list(
        db["trend_insights"]
        .find(
            {"window_from": {"$gte": window_from}, "window_hours": hours},
            {"_id": 0}
        )
        .sort("ts", -1)
        .limit(limit)
    )

    return {"count": len(items), "items": items}


@router.get("/products/{product_id}/insight/latest")
def product_insight_latest(product_id: str):
    db = get_db()
    item = db["trend_insights"].find_one(
        {"product_id": product_id},
        sort=[("ts", -1)],
        projection={"_id": 0}
    )

    if not item:
        raise HTTPException(status_code=404, detail="Insight não encontrado")

    return item

# =========================================================
# 📉 CURVA HISTÓRICA
# =========================================================

@router.get("/products/{product_id}/curve")
def product_curve(
    product_id: str,
    hours: int = Query(72, ge=1, le=720),
):
    db = get_db()

    product = db["products"].find_one(
        {"product_id": product_id},
        {"_id": 0}
    )

    if not product:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    since = dt.datetime.utcnow() - dt.timedelta(hours=hours)

    metrics = list(
        db["metrics"]
        .find(
            {"product_id": product_id, "ts": {"$gte": since}},
            {"_id": 0}
        )
        .sort("ts", 1)
    )

    return {
        "product": product,
        "hours": hours,
        "points": metrics,
        "count": len(metrics),
    }

# =========================================================
# 🗂️ CATEGORIES
# =========================================================

@router.get("/categories")
def list_categories(
    query: str = Query("", min_length=0),
    limit: int = Query(50, ge=1, le=200),
):
    db = get_db()
    filt = {"ml_category_id": {"$exists": True}}

    if query:
        filt["name"] = {"$regex": query, "$options": "i"}

    items = list(
        db["categories"]
        .find(filt, {"_id": 0})
        .limit(limit)
    )

    return {"count": len(items), "items": items}


@router.post("/categories/enable")
def enable_categories(category_ids: List[str]):
    db = get_db()
    now = dt.datetime.utcnow()

    result = db["categories"].update_many(
        {"ml_category_id": {"$in": category_ids}},
        {"$set": {"enabled": True, "enabled_at": now, "updated_at": now}},
    )

    return {"enabled_count": result.modified_count}


@router.post("/categories/disable")
def disable_categories(category_ids: List[str]):
    db = get_db()
    now = dt.datetime.utcnow()

    result = db["categories"].update_many(
        {"ml_category_id": {"$in": category_ids}},
        {"$set": {"enabled": False, "updated_at": now}},
    )

    return {"disabled_count": result.modified_count}