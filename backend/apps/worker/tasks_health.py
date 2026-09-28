# apps/worker/tasks_health.py
"""
Saúde do próprio sistema (TIE-39): detecta a falha silenciosa — coleta ou
análise paradas, LLM sempre no fallback, fila acumulando, backup falhando — e
avisa num canal do Telegram separado do de tendências.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

import redis
from celery import shared_task

from src.domain.alerting import decide_system_alert
from src.infrastructure.config import settings
from src.infrastructure.db.mongo import get_db
from src.infrastructure.telegram.notifier import TelegramError, TelegramNotifier
from src.infrastructure.utils.datetime_utils import ensure_utc, utcnow

logger = logging.getLogger(__name__)

# A análise roda a cada 30 min (beat); duas rodadas perdidas = problema
ANALYSIS_MAX_AGE = dt.timedelta(minutes=60)
ML_INTERVAL = dt.timedelta(hours=6)
QUEUES = ("celery", "ml", "tiktok", "trend")


@dataclass(frozen=True)
class HealthCheck:
    name: str
    problem: bool
    detail: str


def _horas(delta: dt.timedelta) -> str:
    return f"{delta.total_seconds() / 3600:.1f} h"


def _idade_ultima(db, colecao: str, filtro: dict) -> dt.timedelta | None:
    doc = db[colecao].find_one(filtro, sort=[("ts", -1)], projection={"ts": 1})
    return utcnow() - ensure_utc(doc["ts"]) if doc and doc.get("ts") else None


def check_collections(db) -> list[HealthCheck]:
    """Coleta parada. Só fontes ativas: TikTok com token, ML com categoria habilitada."""
    fontes: list[tuple[str, dt.timedelta]] = []
    if settings.APIFY_TOKEN:
        fontes.append(("tiktok", dt.timedelta(minutes=settings.TIKTOK_INTERVAL_MIN)))
    if db["categories"].find_one({"enabled": True, "ml_category_id": {"$exists": True}}):
        fontes.append(("mercadolivre", ML_INTERVAL))

    resultados = []
    for fonte, intervalo in fontes:
        idade = _idade_ultima(db, "metrics", {"source": fonte})
        limite = 2 * intervalo
        if idade is None:
            resultados.append(HealthCheck(f"coleta_{fonte}", True, "nunca coletou"))
        else:
            resultados.append(
                HealthCheck(
                    f"coleta_{fonte}",
                    idade > limite,
                    f"última coleta há {_horas(idade)} (limite {_horas(limite)})",
                )
            )
    return resultados


def check_analysis(db) -> HealthCheck:
    idade = _idade_ultima(db, "trend_insights", {})
    if idade is None:
        # Sem insight nenhum só é problema se já há o que analisar
        tem_metricas = db["metrics"].find_one({}, projection={"_id": 1}) is not None
        return HealthCheck("analise", tem_metricas, "há métricas, mas nenhum insight gerado")
    return HealthCheck(
        "analise",
        idade > ANALYSIS_MAX_AGE,
        f"último insight há {_horas(idade)} (limite {_horas(ANALYSIS_MAX_AGE)})",
    )


def check_llm_fallback(db) -> HealthCheck:
    """Fração dos insights recentes que caíram no score só numérico."""
    desde = utcnow() - dt.timedelta(hours=6)
    recentes = list(
        db["trend_insights"]
        .find({"ts": {"$gte": desde}}, {"debug.llm_error": 1})
        .sort("ts", -1)
        .limit(200)
    )
    n = len(recentes)
    if n < settings.HEALTH_LLM_MIN_SAMPLE:
        return HealthCheck("llm_fallback", False, f"amostra pequena ({n})")
    falhas = sum(1 for d in recentes if (d.get("debug") or {}).get("llm_error"))
    taxa = falhas / n
    return HealthCheck(
        "llm_fallback",
        taxa > settings.HEALTH_LLM_FALLBACK_MAX,
        f"{taxa:.0%} dos últimos {n} insights sem LLM "
        f"(limite {settings.HEALTH_LLM_FALLBACK_MAX:.0%})",
    )


def check_queues(client: Any) -> HealthCheck:
    """Mensagens paradas nas filas do Celery (listas no Redis)."""
    tamanhos = {q: int(client.llen(q) or 0) for q in QUEUES}
    cheias = {q: n for q, n in tamanhos.items() if n > settings.HEALTH_QUEUE_MAX}
    resumo = ", ".join(f"{q}={n}" for q, n in (cheias or tamanhos).items())
    return HealthCheck(
        "filas_celery", bool(cheias), f"{resumo} (limite {settings.HEALTH_QUEUE_MAX})"
    )


def check_backups(db) -> HealthCheck:
    """Lê `backups` (gravada pelo backup, TIE-35). Sem registro = não configurado."""
    ultimo = db["backups"].find_one({}, sort=[("ts", -1)])
    if ultimo is None:
        return HealthCheck("backup", False, "backup não configurado")
    if ultimo.get("status") != "ok":
        return HealthCheck("backup", True, f"último backup falhou: {ultimo.get('error', '?')}")
    idade = utcnow() - ensure_utc(ultimo["ts"])
    limite = dt.timedelta(hours=settings.HEALTH_BACKUP_MAX_AGE_HOURS)
    return HealthCheck(
        "backup", idade > limite, f"último backup há {_horas(idade)} (limite {_horas(limite)})"
    )


def _redis():
    return redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)


def _build_system_notifier() -> TelegramNotifier | None:
    """Canal separado do de tendências; sem ele, os problemas só vão para o log."""
    if not (settings.TELEGRAM_SYSTEM_CHAT_ID and settings.TELEGRAM_BOT_TOKEN):
        return None
    return TelegramNotifier(chat_id=settings.TELEGRAM_SYSTEM_CHAT_ID)


@shared_task(name="tasks.system_health_check")
def system_health_check():
    db = get_db()
    now = utcnow()
    cooldown = dt.timedelta(hours=settings.HEALTH_ALERT_COOLDOWN_HOURS)

    resultados: list[HealthCheck] = []
    erros = 0
    checks = (
        lambda: check_collections(db),
        lambda: [check_analysis(db)],
        lambda: [check_llm_fallback(db)],
        lambda: [check_queues(_redis())],
        lambda: [check_backups(db)],
    )
    for check in checks:
        # Um check quebrado não pode calar os outros
        try:
            resultados += check()
        except Exception:
            erros += 1
            logger.exception("Check de saúde falhou")

    notifier = _build_system_notifier()
    estado = db["system_alert_state"]
    enviados = 0

    for r in resultados:
        if r.problem:
            logger.error("Problema de saúde: %s — %s", r.name, r.detail)
        if notifier is None:
            continue

        anterior = estado.find_one({"name": r.name})
        decisao = decide_system_alert(
            r.problem, ensure_utc(anterior["alerted_at"]) if anterior else None, now, cooldown
        )
        if decisao is None:
            continue

        texto = (
            f"🚨 [trends] {r.name}: {r.detail}"
            if decisao == "alert"
            else f"✅ [trends] {r.name} normalizado: {r.detail}"
        )
        try:
            notifier.send(texto)
        except TelegramError as e:
            # Sem gravar estado: o próximo ciclo tenta de novo
            logger.warning("Alerta de saúde %s não enviado: %s", r.name, e)
            continue

        enviados += 1
        if decisao == "alert":
            estado.update_one({"name": r.name}, {"$set": {"alerted_at": now}}, upsert=True)
        else:
            estado.delete_one({"name": r.name})

    return {
        "status": "ok",
        "checks": len(resultados),
        "problems": sum(r.problem for r in resultados),
        "alerts_sent": enviados,
        "check_errors": erros,
    }
