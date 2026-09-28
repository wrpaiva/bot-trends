# src/tests/test_collect_tiktok.py
"""
Volume da coleta do TikTok. Cada vídeo coletado consome crédito da Apify, então
o limite por hashtag e o intervalo do beat precisam vir da config — sem eles o
collector usava o default de 50 por hashtag a cada 30 min (~7.200 vídeos/dia).
"""

import mongomock

from apps.worker import tasks as tasks_mod
from apps.worker.main import celery_app


class _FakeCollector:
    chamadas: list[dict] = []

    def __init__(self, **kwargs):
        _FakeCollector.chamadas.append(kwargs)
        self.errors = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def collect(self):
        return iter(())


def test_collect_tiktok_respeita_limite_por_hashtag(monkeypatch):
    _FakeCollector.chamadas = []
    monkeypatch.setattr(tasks_mod, "TikTokApifyCollector", _FakeCollector)
    monkeypatch.setattr(tasks_mod, "get_db", lambda: mongomock.MongoClient().db)
    monkeypatch.setattr(tasks_mod, "_breaker", lambda nome: None)
    monkeypatch.setattr(tasks_mod.settings, "TIKTOK_HASHTAGS", "achadinhos, ,#tiktokmademebuyit")
    monkeypatch.setattr(tasks_mod.settings, "TIKTOK_RESULTS_PER_HASHTAG", 7)

    assert tasks_mod.collect_tiktok() == {"status": "ok", "inserted": 0, "errors": 0}

    (kwargs,) = _FakeCollector.chamadas
    assert kwargs["hashtags"] == ["achadinhos", "#tiktokmademebuyit"]
    assert kwargs["results_per_page"] == 7
    # O dataset precisa comportar todas as hashtags, senão corta resultados pagos
    assert kwargs["dataset_limit"] == 14


def test_beat_do_tiktok_usa_intervalo_da_config():
    entradas = [
        e for e in celery_app.conf.beat_schedule.values() if e["task"] == "tasks.collect_tiktok"
    ]
    assert len(entradas) == 1
    assert entradas[0]["schedule"] == 60 * tasks_mod.settings.TIKTOK_INTERVAL_MIN
