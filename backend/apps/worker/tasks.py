# apps/worker/tasks.py

import logging

from celery import shared_task

from src.infrastructure.circuit_breaker import breaker_for
from src.infrastructure.collectors.mercado_livre import MercadoLivreCollector
from src.infrastructure.collectors.tiktok import TikTokApifyCollector
from src.infrastructure.config import settings
from src.infrastructure.db.mongo import get_db
from src.infrastructure.db.repos import MetricsRepo, ProductRepo
from src.infrastructure.utils.datetime_utils import utcnow

logger = logging.getLogger(__name__)

# Indireção para os testes trocarem o breaker (Redis) por um falso
_breaker = breaker_for


def _circuito_aberto(breaker, servico: str) -> dict | None:
    """Circuito aberto (TIE-37): não tenta, loga e devolve o motivo."""
    if breaker is None or breaker.allow():
        return None
    st = breaker.status()
    logger.warning("Coleta %s pulada: circuito aberto", servico, extra=st)
    return {
        "status": "circuit_open",
        "inserted": 0,
        "errors": 0,
        "retry_in_s": st.get("retry_in_s"),
    }


def _registra(breaker, resultado: dict) -> dict:
    """Coleta sem nada inserido por falha = falha do serviço; o resto = sucesso."""
    if breaker is not None:
        if resultado["status"] == "error":
            breaker.record_failure()
        else:
            breaker.record_success()
    return resultado


def _resultado(inserted: int, errors: int) -> dict:
    """
    Status honesto da coleta (TIE-17): antes, falha em toda requisição virava
    `status: ok, inserted: 0`, indistinguível de "não havia nada novo".
    """
    if not errors:
        status = "ok"
    elif inserted:
        status = "partial"
    else:
        status = "error"
    return {"status": status, "inserted": inserted, "errors": errors}


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
            {"enabled": True, "ml_category_id": {"$exists": True}}, {"ml_category_id": 1}
        )
    )

    category_ids = [c["ml_category_id"] for c in categories]
    if not category_ids:
        return {"status": "no enabled categories"}

    breaker = _breaker("mercadolivre")
    if aberto := _circuito_aberto(breaker, "mercadolivre"):
        return aberto

    now = utcnow()
    count = 0

    with MercadoLivreCollector(categories=category_ids) as collector:
        for item in collector.collect():
            product_id = products.upsert(item)

            metrics.insert(
                {
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
                }
            )
            count += 1

    return _registra(breaker, _resultado(count, collector.errors))


# =========================================================
# 📱 TIKTOK
# =========================================================


@shared_task(name="tasks.collect_tiktok")
def collect_tiktok():
    db = get_db()
    products = ProductRepo(db)
    metrics = MetricsRepo(db)

    hashtags = [h.strip() for h in settings.TIKTOK_HASHTAGS.split(",") if h.strip()]
    per_hashtag = settings.TIKTOK_RESULTS_PER_HASHTAG
    breaker = _breaker("apify")
    if aberto := _circuito_aberto(breaker, "apify"):
        return aberto

    now = utcnow()
    count = 0

    with TikTokApifyCollector(
        hashtags=hashtags,
        results_per_page=per_hashtag,
        dataset_limit=per_hashtag * len(hashtags),
    ) as collector:
        for item in collector.collect():
            product_id = products.upsert(item)

            social = item.get("social", {})
            engagement = (
                int(social.get("likes", 0) or 0)
                + int(social.get("comments", 0) or 0)
                + int(social.get("shares", 0) or 0)
            )

            metrics.insert(
                {
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
                }
            )
            count += 1

    return _registra(breaker, _resultado(count, collector.errors))
