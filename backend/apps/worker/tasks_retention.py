# apps/worker/tasks_retention.py
"""
Expurgo de dados velhos (TIE-47). Roda uma vez por dia, fila `celery`.

`INSIGHT_RETENTION_DAYS` (90) apaga insight cujo `ts` passou do prazo — um
insight confirmado tem `ts` da última confirmação, então só sai o resultado que
ninguém repetiu nesse tempo. `METRICS_RETENTION_DAYS` é 0 (nunca) por padrão:
o backtest (TIE-23/TIE-46) recalcula o score sobre a série inteira de `metrics`,
e ela é pequena (0,9 MB em 2026-10-18). 0 desliga cada expurgo.

Por que task e não índice TTL: o prazo do TTL fica gravado no índice, e mudar a
config exigiria migração. Aqui muda no `backend/.env` e vale no dia seguinte.
"""

import datetime as dt
import logging

from celery import shared_task

from src.infrastructure.config import settings
from src.infrastructure.db.mongo import get_db
from src.infrastructure.utils.datetime_utils import utcnow

logger = logging.getLogger(__name__)


def _expurga(db, colecao: str, dias: int, agora: dt.datetime) -> int:
    if dias <= 0:
        return 0
    limite = agora - dt.timedelta(days=dias)
    return db[colecao].delete_many({"ts": {"$lt": limite}}).deleted_count


@shared_task(name="tasks.purge_old_data")
def purge_old_data():
    db = get_db()
    agora = utcnow()
    resultado = {
        "status": "ok",
        "insights_deleted": _expurga(db, "trend_insights", settings.INSIGHT_RETENTION_DAYS, agora),
        "metrics_deleted": _expurga(db, "metrics", settings.METRICS_RETENTION_DAYS, agora),
        "insight_retention_days": settings.INSIGHT_RETENTION_DAYS,
        "metrics_retention_days": settings.METRICS_RETENTION_DAYS,
    }
    logger.info("retencao.expurgo", extra=resultado)
    return resultado
