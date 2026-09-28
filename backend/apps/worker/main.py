# apps/worker/main.py

import logging

from celery import Celery
from celery.signals import (
    setup_logging,
    task_postrun,
    task_prerun,
    worker_init,
    worker_process_init,
    worker_process_shutdown,
)

from src.infrastructure.config import settings
from src.infrastructure.db.mongo import close_client, reset_client_after_fork
from src.infrastructure.logging_setup import configure_logging, on_task_postrun, on_task_prerun

logger = logging.getLogger(__name__)

REDIS_URL = settings.REDIS_URL

# Módulos de task listados explicitamente. `autodiscover_tasks(["apps.worker"])`
# só procura um módulo chamado `tasks`, e `tasks_trend.py` ficava de fora: o beat
# publicava `tasks.hybrid_trend_analyze` e o worker descartava. Módulo de task
# novo entra aqui (coberto por test_worker_registro.py).
celery_app = Celery(
    "trends_worker",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "apps.worker.tasks",
        "apps.worker.tasks_trend",
        "apps.worker.tasks_health",
    ],
)

celery_app.conf.task_routes = {
    "tasks.collect_ml": {"queue": "ml"},
    "tasks.collect_tiktok": {"queue": "tiktok"},
    "tasks.hybrid_trend_analyze": {"queue": "trend"},
    # Fila padrão: se "trend" encher, o check de fila ainda consegue rodar (TIE-39)
    "tasks.system_health_check": {"queue": "celery"},
}

# Scheduler (Celery Beat)
celery_app.conf.beat_schedule = {
    "collect-ml-every-6h": {
        "task": "tasks.collect_ml",
        "schedule": 60 * 60 * 6,
    },
    "collect-tiktok": {
        "task": "tasks.collect_tiktok",
        "schedule": 60 * settings.TIKTOK_INTERVAL_MIN,
    },
    "trend-analysis-every-30m": {
        "task": "tasks.hybrid_trend_analyze",
        "schedule": 60 * 30,
    },
    "system-health-every-15m": {
        "task": "tasks.system_health_check",
        "schedule": 60 * 15,
    },
}


# MongoClient não é fork-safe: cada processo filho do prefork abre o seu e o
# reaproveita entre tasks (TIE-7).
# Credenciais que só desligam uma parte da coleta/alerta: avisa no startup em
# vez de descobrir no primeiro ciclo do beat (TIE-8).
OPCIONAIS = {
    "APIFY_TOKEN": "coleta do TikTok",
    "TELEGRAM_BOT_TOKEN": "alertas no Telegram",
    "TELEGRAM_CHAT_ID": "alertas no Telegram",
    "TELEGRAM_SYSTEM_CHAT_ID": "alertas de saúde do sistema (só log)",
    "LLM_API_KEY": "score do LLM (cai no fallback numérico)",
}


# Com receptor em setup_logging o Celery não sequestra o root logger e o
# worker/beat saem no mesmo JSON (com redação) da API (TIE-13).
@setup_logging.connect
def _configura_logging(**_):
    configure_logging()


# Início, fim, duração e contagens de toda task, num lugar só
task_prerun.connect(on_task_prerun)
task_postrun.connect(on_task_postrun)


@worker_init.connect
def _avisa_config_incompleta(**_):
    for nome in settings.missing(*OPCIONAIS):
        logger.warning("%s não configurado: sem %s", nome, OPCIONAIS[nome])


@worker_process_init.connect
def _mongo_por_processo(**_):
    reset_client_after_fork()


@worker_process_shutdown.connect
def _fecha_mongo(**_):
    close_client()
