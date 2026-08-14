# apps/worker/main.py

import os
from celery import Celery

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "trends_worker",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.task_routes = {
    "tasks.collect_ml": {"queue": "ml"},
    "tasks.collect_tiktok": {"queue": "tiktok"},
    "tasks.hybrid_trend_analyze": {"queue": "trend"},
}

# Scheduler (Celery Beat)
celery_app.conf.beat_schedule = {
    "collect-ml-every-6h": {
        "task": "tasks.collect_ml",
        "schedule": 60 * 60 * 6,
    },
    "collect-tiktok-every-30m": {
        "task": "tasks.collect_tiktok",
        "schedule": 60 * 30,
    },
    "trend-analysis-every-30m": {
        "task": "tasks.hybrid_trend_analyze",
        "schedule": 60 * 30,
    },
}

celery_app.autodiscover_tasks(["apps.worker"])