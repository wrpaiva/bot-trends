# apps/api/routes.py

import datetime as dt

import redis
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pymongo.errors import OperationFailure

from apps.api.pagination import decode_cursor, encode_cursor
from src.infrastructure.circuit_breaker import breakers_status
from src.infrastructure.config import settings
from src.infrastructure.db.mongo import get_db
from src.infrastructure.observability import http_metrics, render, system_samples
from src.infrastructure.security.rate_limit import limiter
from src.infrastructure.utils.datetime_utils import ensure_utc, utcnow

router = APIRouter()

# =========================================================
# 🔎 HEALTH
# =========================================================


@router.get("/health/migrations")
def health_migrations():
    db = get_db()

    migrations = list(db["migrations"].find({}, {"_id": 0}).sort("started_at", -1).limit(20))

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


def _redis_metrics():
    return redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)


@router.get("/metrics")
def metrics():
    """
    Métricas no formato Prometheus (TIE-36): HTTP da API + estado do sistema
    medido agora. Componente fora vira `trends_component_up 0`, não erro.
    """
    try:
        breakers = breakers_status()
    except Exception:  # noqa: BLE001 — sem Redis, sem circuitos; o resto sai
        breakers = {}
    try:
        db = get_db()
    except Exception:  # noqa: BLE001 — config quebrada conta como Mongo fora
        db = None
    samples = http_metrics.samples() + system_samples(
        db, _redis_metrics(), breakers=breakers, now=utcnow()
    )
    return PlainTextResponse(render(samples), media_type="text/plain; version=0.0.4")


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
    window_hours: int | None = Query(None, ge=1, le=720),
    cursor: str | None = Query(None, max_length=500),
):
    """
    Ranking por final_score: um item por produto (o insight mais recente),
    entre os gerados nas últimas `hours` horas. Paginado por cursor (TIE-32):
    passe o `next_cursor` da resposta para a próxima página.
    """

    db = get_db()
    # Filtra pelo momento da análise (`ts`). Filtrar por `window_from` excluía
    # tudo: a task grava window_from = t_análise - hours, sempre anterior a
    # t_requisição - hours, e o ranking voltava vazio (TIE-12).
    #
    # `hours` é só recência. Antes ele também exigia window_hours == hours, e
    # como o beat analisa sempre com janela de 72 h, qualquer outro valor no
    # dashboard dava vazio (TIE-28). Filtrar por janela agora é explícito.
    match: dict = {"ts": {"$gte": utcnow() - dt.timedelta(hours=hours)}}
    if window_hours is not None:
        match["window_hours"] = window_hours

    if source != "all":
        match["sources"] = {"$in": [source]}

    pipeline = [
        {"$match": match},
        {"$sort": {"ts": -1, "_id": -1}},
        {"$group": {"_id": "$product_id", "doc": {"$first": "$$ROOT"}}},
        {"$replaceRoot": {"newRoot": "$doc"}},
    ]
    if cursor:
        c = decode_cursor(cursor, "rankings", ("s", "p"))
        # Depois do último item: score menor, ou mesmo score e product_id maior
        pipeline.append(
            {
                "$match": {
                    "$or": [
                        {"final_score": {"$lt": c["s"]}},
                        {"final_score": c["s"], "product_id": {"$gt": c["p"]}},
                    ]
                }
            }
        )
    # product_id desempata: sem ele, scores iguais pulariam/repetiriam entre páginas
    pipeline += [{"$sort": {"final_score": -1, "product_id": 1}}, {"$limit": limit + 1}]

    insights = list(db["trend_insights"].aggregate(pipeline))
    has_more = len(insights) > limit
    insights = insights[:limit]

    product_ids = [i["product_id"] for i in insights]
    products = {
        p["product_id"]: p
        for p in db["products"].find({"product_id": {"$in": product_ids}}, {"_id": 0})
    }

    result = []
    for i in insights:
        p = products.get(i["product_id"], {})
        result.append(
            {
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
            }
        )

    ultimo = insights[-1] if insights else None
    return {
        "count": len(result),
        "hours": hours,
        "items": result,
        "has_more": has_more,
        "next_cursor": (
            encode_cursor("rankings", s=ultimo["final_score"], p=ultimo["product_id"])
            if has_more
            else None
        ),
    }


# =========================================================
# 📈 INSIGHTS
# =========================================================


@router.get("/insights/latest")
def insights_latest(
    limit: int = Query(20, ge=1, le=200),
    hours: int = Query(72, ge=1, le=720),
    window_hours: int | None = Query(None, ge=1, le=720),
    cursor: str | None = Query(None, max_length=500),
):
    """Insights das últimas `hours` horas, do mais novo ao mais antigo. Paginado por cursor."""
    db = get_db()
    since = utcnow() - dt.timedelta(hours=hours)

    # Mesmo critério do ranking: `hours` é recência; janela só se pedida
    filtro: dict = {"ts": {"$gte": since}}
    if window_hours is not None:
        filtro["window_hours"] = window_hours
    if cursor:
        c = decode_cursor(cursor, "insights", ("ts", "id"))
        try:
            ts, oid = dt.datetime.fromisoformat(c["ts"]), ObjectId(c["id"])
        except (TypeError, ValueError, InvalidId):
            raise HTTPException(status_code=400, detail="cursor inválido") from None
        # _id desempata insights gravados no mesmo milissegundo
        filtro["$or"] = [{"ts": {"$lt": ts}}, {"ts": ts, "_id": {"$lt": oid}}]

    docs = list(db["trend_insights"].find(filtro).sort([("ts", -1), ("_id", -1)]).limit(limit + 1))
    has_more = len(docs) > limit
    docs = docs[:limit]

    next_cursor = None
    if has_more:
        ultimo = docs[-1]
        next_cursor = encode_cursor(
            "insights", ts=ensure_utc(ultimo["ts"]).isoformat(), id=str(ultimo["_id"])
        )
    for d in docs:
        d.pop("_id", None)

    return {"count": len(docs), "items": docs, "has_more": has_more, "next_cursor": next_cursor}


@router.get("/products/{product_id}/insight/latest")
def product_insight_latest(product_id: str):
    db = get_db()
    item = db["trend_insights"].find_one(
        {"product_id": product_id}, sort=[("ts", -1)], projection={"_id": 0}
    )

    if not item:
        raise HTTPException(status_code=404, detail="Insight não encontrado")

    return item


# =========================================================
# 🔍 BUSCA
# =========================================================

# Código do Mongo para "$text sem índice de texto"
_INDEX_NOT_FOUND = 27


@router.get("/search")
@limiter.limit("30/minute")
def search_products(
    request: Request,  # necessário para o limiter
    q: str = Query(..., min_length=2, max_length=100),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=50),
):
    """
    Busca produtos pelo título (índice de texto, stemming em português),
    do mais relevante para o menos, com o último score conhecido (TIE-30).
    """
    db = get_db()
    skip = (page - 1) * limit

    pipeline = [
        {"$match": {"$text": {"$search": q}}},
        {"$addFields": {"relevance": {"$meta": "textScore"}}},
        # _id desempata: sem ele a paginação pode repetir/pular itens
        {"$sort": {"relevance": -1, "_id": 1}},
        {
            "$facet": {
                "items": [
                    {"$skip": skip},
                    {"$limit": limit},
                    {
                        "$project": {
                            "_id": 0,
                            "product_id": 1,
                            "title": 1,
                            "category": 1,
                            "source": 1,
                            "permalink": 1,
                            "relevance": 1,
                        }
                    },
                ],
                "total": [{"$count": "n"}],
            }
        },
    ]

    try:
        (res,) = db["products"].aggregate(pipeline)
    except OperationFailure as e:
        if e.code == _INDEX_NOT_FOUND:
            raise HTTPException(
                status_code=503,
                detail="Índice de busca ausente: rode as migrações (apps.migrate.main).",
            ) from None
        raise

    items = res["items"]
    total = res["total"][0]["n"] if res["total"] else 0

    latest = {
        d["_id"]: d["doc"]
        for d in db["trend_insights"].aggregate(
            [
                {"$match": {"product_id": {"$in": [i["product_id"] for i in items]}}},
                {"$sort": {"ts": -1}},
                {"$group": {"_id": "$product_id", "doc": {"$first": "$$ROOT"}}},
            ]
        )
    }
    for i in items:
        ins = latest.get(i["product_id"])
        i["latest"] = (
            {
                "final_score": ins.get("final_score"),
                "trend_classification": ins.get("trend_classification"),
                "risk_level": ins.get("risk_level"),
                "ts": ins.get("ts"),
            }
            if ins
            else None
        )

    return {"q": q, "page": page, "limit": limit, "total": total, "items": items}


# =========================================================
# 📉 CURVA HISTÓRICA
# =========================================================


@router.get("/products/{product_id}/curve")
def product_curve(
    product_id: str,
    hours: int = Query(72, ge=1, le=720),
):
    db = get_db()

    product = db["products"].find_one({"product_id": product_id}, {"_id": 0})

    if not product:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    since = utcnow() - dt.timedelta(hours=hours)

    metrics = list(
        db["metrics"]
        .find({"product_id": product_id, "ts": {"$gte": since}}, {"_id": 0})
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

    items = list(db["categories"].find(filt, {"_id": 0}).limit(limit))

    return {"count": len(items), "items": items}


@router.post("/categories/enable")
def enable_categories(category_ids: list[str]):
    db = get_db()
    now = utcnow()

    result = db["categories"].update_many(
        {"ml_category_id": {"$in": category_ids}},
        {"$set": {"enabled": True, "enabled_at": now, "updated_at": now}},
    )

    return {"enabled_count": result.modified_count}


@router.post("/categories/disable")
def disable_categories(category_ids: list[str]):
    db = get_db()
    now = utcnow()

    result = db["categories"].update_many(
        {"ml_category_id": {"$in": category_ids}},
        {"$set": {"enabled": False, "updated_at": now}},
    )

    return {"disabled_count": result.modified_count}
