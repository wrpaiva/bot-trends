# src/tests/test_retention.py
"""Expurgo diário (TIE-47): insight velho sai, métrica fica por padrão."""

import datetime as dt

import mongomock
import pytest

from apps.worker import tasks_retention as mod
from src.infrastructure.utils.datetime_utils import utcnow


@pytest.fixture
def db(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True).db
    agora = utcnow()
    for dias in (1, 89, 91, 400):
        ts = agora - dt.timedelta(days=dias)
        db["trend_insights"].insert_one({"product_id": f"p{dias}", "ts": ts})
        db["metrics"].insert_one({"product_id": f"p{dias}", "ts": ts})
    monkeypatch.setattr(mod, "get_db", lambda: db)
    return db


def test_padrao_apaga_insight_de_mais_de_90_dias_e_mantem_metricas(db, monkeypatch):
    monkeypatch.setattr(mod.settings, "INSIGHT_RETENTION_DAYS", 90)
    monkeypatch.setattr(mod.settings, "METRICS_RETENTION_DAYS", 0)

    res = mod.purge_old_data()

    assert (res["insights_deleted"], res["metrics_deleted"]) == (2, 0)
    assert sorted(d["product_id"] for d in db["trend_insights"].find()) == ["p1", "p89"]
    assert db["metrics"].count_documents({}) == 4


def test_retencao_de_metricas_configuravel(db, monkeypatch):
    monkeypatch.setattr(mod.settings, "METRICS_RETENTION_DAYS", 365)
    assert mod.purge_old_data()["metrics_deleted"] == 1


def test_zero_desliga_o_expurgo_de_insights(db, monkeypatch):
    monkeypatch.setattr(mod.settings, "INSIGHT_RETENTION_DAYS", 0)
    assert mod.purge_old_data()["insights_deleted"] == 0
    assert db["trend_insights"].count_documents({}) == 4
