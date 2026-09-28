# src/tests/test_health.py
"""
Liveness e readiness de verdade (TIE-38).

- `/health` (liveness): barato, sem dependência nenhuma — nem do Redis do rate limit.
- `/health/ready` (readiness): Mongo fora → 503; Redis fora ou migração
  pendente/falha → 200 `degraded` (a API ainda serve leitura).
- Redis fora do ar não derruba mais a API inteira com 500.
"""

import pathlib

import mongomock
import pytest
from fastapi.testclient import TestClient

from apps.api import main as api_main
from apps.api import routes
from src.infrastructure import health
from src.infrastructure.config import settings
from src.infrastructure.db.migrations import get_migrations

CHAVE = "chave-de-teste"
RAIZ = pathlib.Path(__file__).resolve().parents[3]


@pytest.fixture
def client(monkeypatch):
    # Limiter LIGADO de propósito: o storage aponta para um Redis inalcançável
    # no ambiente de teste, que é exatamente o cenário "Redis fora do ar".
    monkeypatch.setattr(settings, "API_KEY", CHAVE)
    with TestClient(app=api_main.app) as c:
        yield c


# --- Liveness e Redis fora -------------------------------------------------


def test_liveness_nao_depende_de_nada(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_redis_fora_nao_derruba_rota_protegida(client, monkeypatch):
    monkeypatch.setattr(routes, "get_db", lambda: mongomock.MongoClient().db)
    r = client.get("/categories", headers={"X-API-Key": CHAVE})
    assert r.status_code == 200


# --- Checks individuais -----------------------------------------------------


def test_check_mongo_ok():
    assert health.check_mongo(mongomock.MongoClient().db).ok


def test_check_mongo_falha():
    class _DbQuebrado:
        client = None

        def command(self, *_):
            raise ConnectionError("sem mongo")

    res = health.check_mongo(_DbQuebrado())
    assert not res.ok and "sem mongo" in res.detail


def test_check_redis_inalcancavel():
    res = health.check_redis("redis://127.0.0.1:1/0", timeout_s=0.5)
    assert not res.ok


def test_check_redis_ok_com_cliente_injetado():
    class _Redis:
        def ping(self):
            return True

    assert health.check_redis("redis://x", factory=lambda *a, **kw: _Redis()).ok


def test_check_migrations_pendentes_e_falhas():
    db = mongomock.MongoClient().db
    ids = [m.meta.migration_id for m in get_migrations()]

    res = health.check_migrations(db)
    assert not res.ok and res.extra["pending"] == ids

    for mid in ids[:-1]:
        db["migrations"].insert_one({"migration_id": mid, "status": "applied"})
    db["migrations"].insert_one({"migration_id": ids[-1], "status": "failed"})

    res = health.check_migrations(db)
    assert not res.ok
    assert res.extra == {"pending": [], "failed": [ids[-1]]}


def test_check_migrations_ok():
    db = mongomock.MongoClient().db
    for m in get_migrations():
        db["migrations"].insert_one({"migration_id": m.meta.migration_id, "status": "applied"})
    assert health.check_migrations(db).ok


# --- Readiness --------------------------------------------------------------


def _checks(monkeypatch, *, mongo=True, redis=True, migrations=True):
    monkeypatch.setattr(api_main, "get_db", lambda: None)
    monkeypatch.setattr(api_main, "breakers_status", lambda: {})
    monkeypatch.setattr(api_main.health, "check_mongo", lambda db: health.Check(mongo, "m"))
    monkeypatch.setattr(api_main.health, "check_redis", lambda url: health.Check(redis, "r"))
    monkeypatch.setattr(
        api_main.health, "check_migrations", lambda db: health.Check(migrations, "g")
    )


def test_ready_mostra_breakers_e_degrada_com_circuito_aberto(client, monkeypatch):
    _checks(monkeypatch)
    monkeypatch.setattr(
        api_main,
        "breakers_status",
        lambda: {
            "llm": {"state": "open", "failures": 5, "retry_in_s": 900},
            "apify": {"state": "closed"},
        },
    )
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"
    assert r.json()["breakers"]["llm"]["state"] == "open"


def test_ready_tudo_ok(client, monkeypatch):
    _checks(monkeypatch)
    r = client.get("/health/ready")
    assert r.status_code == 200 and r.json()["status"] == "ready"


@pytest.mark.parametrize("fora", ["redis", "migrations"])
def test_ready_degradado_continua_200(client, monkeypatch, fora):
    _checks(monkeypatch, **{fora: False})
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"
    assert r.json()["checks"][fora]["ok"] is False


def test_ready_sem_mongo_da_503(client, monkeypatch):
    _checks(monkeypatch, mongo=False)
    r = client.get("/health/ready")
    assert r.status_code == 503 and r.json()["status"] == "unavailable"


def test_ready_eh_publica(client, monkeypatch):
    _checks(monkeypatch)
    assert client.get("/health/ready", headers={"X-API-Key": ""}).status_code == 200


def test_mongo_com_excecao_inesperada_vira_503_e_nao_500(client, monkeypatch):
    def _explode():
        raise RuntimeError("config quebrada")

    _checks(monkeypatch)
    monkeypatch.setattr(api_main, "get_db", _explode)
    assert client.get("/health/ready").status_code == 503


# --- Healthchecks do Docker -------------------------------------------------


@pytest.mark.skipif(not (RAIZ / "docker-compose.yml").exists(), reason="compose fora do container")
def test_healthchecks_do_compose():
    yaml = pytest.importorskip("yaml")  # dependência transitiva, não declarada

    servicos = yaml.safe_load((RAIZ / "docker-compose.yml").read_text())["services"]
    # worker e beat não servem HTTP: não podem herdar o curl da imagem (TIE-38)
    worker = " ".join(servicos["worker"]["healthcheck"]["test"])
    assert "inspect ping" in worker
    assert servicos["beat"]["healthcheck"].get("disable") is True
    dockerfile = (RAIZ / "backend" / "Dockerfile").read_text()
    assert "/health/ready" in dockerfile
