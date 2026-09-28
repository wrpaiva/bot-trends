# src/tests/test_system_health.py
"""
Alertas de saúde do próprio sistema (TIE-39).

A falha mais perigosa é a silenciosa: a coleta para, os insights param, e o
dashboard segue mostrando o último ranking — ninguém percebe por dias.
"""

import datetime as dt

import mongomock
import pytest

from apps.worker import tasks_health as mod
from src.domain.alerting import decide_system_alert
from src.infrastructure.telegram.notifier import TelegramError
from src.infrastructure.utils.datetime_utils import utcnow

AGORA = dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.UTC)
COOLDOWN = dt.timedelta(hours=6)


# --- Regra de disparo (domínio) ---------------------------------------------


@pytest.mark.parametrize(
    ("problema", "ultimo", "esperado"),
    [
        (False, None, None),  # tudo ok, nada a dizer
        (True, None, "alert"),  # problema novo
        (True, AGORA - dt.timedelta(hours=1), None),  # em cooldown
        (True, AGORA - dt.timedelta(hours=6), "alert"),  # cooldown vencido, ainda quebrado
        (False, AGORA - dt.timedelta(hours=1), "recovered"),  # voltou ao normal
    ],
)
def test_decide_system_alert(problema, ultimo, esperado):
    assert decide_system_alert(problema, ultimo, AGORA, COOLDOWN) == esperado


# --- Checks -----------------------------------------------------------------


@pytest.fixture
def db():
    return mongomock.MongoClient(tz_aware=True).db


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    s = mod.settings
    monkeypatch.setattr(s, "APIFY_TOKEN", "x")
    monkeypatch.setattr(s, "TIKTOK_INTERVAL_MIN", 120)
    monkeypatch.setattr(s, "HEALTH_LLM_FALLBACK_MAX", 0.5)
    monkeypatch.setattr(s, "HEALTH_LLM_MIN_SAMPLE", 10)
    monkeypatch.setattr(s, "HEALTH_QUEUE_MAX", 100)
    monkeypatch.setattr(s, "HEALTH_BACKUP_MAX_AGE_HOURS", 26)


def _metrica(db, source, horas_atras):
    db["metrics"].insert_one({"source": source, "ts": utcnow() - dt.timedelta(hours=horas_atras)})


def test_coleta_tiktok_parada(db):
    _metrica(db, "tiktok", 5)  # esperado a cada 2 h; limite 2× = 4 h
    (res,) = (c for c in mod.check_collections(db) if c.name == "coleta_tiktok")
    assert res.problem and "5" in res.detail


def test_coleta_tiktok_em_dia(db):
    _metrica(db, "tiktok", 1)
    (res,) = (c for c in mod.check_collections(db) if c.name == "coleta_tiktok")
    assert not res.problem


def test_coleta_que_nunca_rodou_eh_problema(db):
    (res,) = (c for c in mod.check_collections(db) if c.name == "coleta_tiktok")
    assert res.problem and "nunca" in res.detail


def test_fonte_desligada_nao_eh_verificada(db, monkeypatch):
    monkeypatch.setattr(mod.settings, "APIFY_TOKEN", None)
    # ML sem categoria habilitada também não conta (hoje está parado por falta de OAuth)
    assert mod.check_collections(db) == []


def test_ml_com_categoria_habilitada_eh_verificado(db, monkeypatch):
    monkeypatch.setattr(mod.settings, "APIFY_TOKEN", None)
    db["categories"].insert_one({"enabled": True, "ml_category_id": "MLB1"})
    _metrica(db, "mercadolivre", 20)  # esperado a cada 6 h
    (res,) = mod.check_collections(db)
    assert res.name == "coleta_mercadolivre" and res.problem


def test_analise_parada(db):
    db["trend_insights"].insert_one({"ts": utcnow() - dt.timedelta(hours=3)})
    assert mod.check_analysis(db).problem
    db["trend_insights"].insert_one({"ts": utcnow() - dt.timedelta(minutes=10)})
    assert not mod.check_analysis(db).problem


def _insights(db, n, com_erro):
    for i in range(n):
        db["trend_insights"].insert_one(
            {
                "ts": utcnow() - dt.timedelta(minutes=i),
                "debug": {"llm_error": "429" if i < com_erro else None},
            }
        )


def test_taxa_de_fallback_do_llm_acima_do_limiar(db):
    _insights(db, 20, com_erro=15)
    res = mod.check_llm_fallback(db)
    assert res.problem and "75%" in res.detail


def test_taxa_de_fallback_abaixo_do_limiar(db):
    _insights(db, 20, com_erro=5)
    assert not mod.check_llm_fallback(db).problem


def test_amostra_pequena_nao_alerta(db):
    _insights(db, 5, com_erro=5)
    assert not mod.check_llm_fallback(db).problem


class _Redis:
    def __init__(self, tamanhos):
        self.tamanhos = tamanhos

    def llen(self, fila):
        return self.tamanhos.get(fila, 0)


def test_fila_acumulando():
    res = mod.check_queues(_Redis({"trend": 250, "ml": 3}))
    assert res.problem and "trend=250" in res.detail


def test_filas_normais():
    assert not mod.check_queues(_Redis({"trend": 2})).problem


def test_backup_com_falha(db):
    db["backups"].insert_one({"ts": utcnow(), "status": "failed", "error": "disco cheio"})
    res = mod.check_backups(db)
    assert res.problem and "disco cheio" in res.detail


def test_backup_velho(db):
    db["backups"].insert_one({"ts": utcnow() - dt.timedelta(hours=30), "status": "ok"})
    assert mod.check_backups(db).problem


def test_backup_recente_ok(db):
    db["backups"].insert_one({"ts": utcnow() - dt.timedelta(hours=2), "status": "ok"})
    assert not mod.check_backups(db).problem


def test_backup_nao_configurado_nao_alerta(db):
    assert not mod.check_backups(db).problem


# --- Task ---------------------------------------------------------------------


class _Telegram:
    def __init__(self, falhar=False):
        self.enviadas = []
        self.falhar = falhar

    def send(self, text):
        if self.falhar:
            raise TelegramError("HTTP 500")
        self.enviadas.append(text)


@pytest.fixture
def task(db, monkeypatch):
    tg = _Telegram()
    monkeypatch.setattr(mod, "get_db", lambda: db)
    monkeypatch.setattr(mod, "_redis", lambda: _Redis({}))
    monkeypatch.setattr(mod, "_build_system_notifier", lambda: tg)
    return tg


def test_problema_novo_alerta_no_canal_de_sistema_uma_vez(db, task):
    # Nada coletado nem analisado ainda
    primeira = mod.system_health_check()
    segunda = mod.system_health_check()

    assert primeira["alerts_sent"] >= 1
    assert segunda["alerts_sent"] == 0  # cooldown
    assert any("coleta_tiktok" in m for m in task.enviadas)
    assert all(m.startswith("🚨") for m in task.enviadas)


def test_recuperacao_avisa_uma_vez(db, task):
    mod.system_health_check()
    task.enviadas.clear()
    _metrica(db, "tiktok", 0)

    mod.system_health_check()
    mod.system_health_check()

    recuperados = [m for m in task.enviadas if "coleta_tiktok" in m]
    assert len(recuperados) == 1 and recuperados[0].startswith("✅")


def test_check_que_explode_nao_derruba_os_outros(db, task, monkeypatch):
    monkeypatch.setattr(mod, "check_queues", lambda r: (_ for _ in ()).throw(RuntimeError("boom")))
    res = mod.system_health_check()
    assert res["check_errors"] == 1
    assert any("coleta_tiktok" in m for m in task.enviadas)


def test_sem_canal_de_sistema_so_loga(db, monkeypatch, caplog):
    monkeypatch.setattr(mod, "get_db", lambda: db)
    monkeypatch.setattr(mod, "_redis", lambda: _Redis({}))
    monkeypatch.setattr(mod, "_build_system_notifier", lambda: None)
    with caplog.at_level("ERROR"):
        res = mod.system_health_check()
    assert res["alerts_sent"] == 0 and res["problems"] >= 1
    assert "coleta_tiktok" in caplog.text


def test_falha_no_envio_nao_grava_estado_e_tenta_de_novo(db, monkeypatch):
    tg = _Telegram(falhar=True)
    monkeypatch.setattr(mod, "get_db", lambda: db)
    monkeypatch.setattr(mod, "_redis", lambda: _Redis({}))
    monkeypatch.setattr(mod, "_build_system_notifier", lambda: tg)
    mod.system_health_check()
    tg.falhar = False
    assert mod.system_health_check()["alerts_sent"] >= 1


def test_notifier_de_sistema_usa_o_chat_separado(monkeypatch):
    monkeypatch.setattr(mod.settings, "TELEGRAM_BOT_TOKEN", "1:x")
    monkeypatch.setattr(mod.settings, "TELEGRAM_CHAT_ID", "tendencias")
    monkeypatch.setattr(mod.settings, "TELEGRAM_SYSTEM_CHAT_ID", "sistema")
    assert mod._build_system_notifier().chat_id == "sistema"
    monkeypatch.setattr(mod.settings, "TELEGRAM_SYSTEM_CHAT_ID", None)
    assert mod._build_system_notifier() is None
