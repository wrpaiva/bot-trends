# src/tests/test_search.py
"""
GET /search (TIE-30) contra um Mongo de verdade.

O mongomock não implementa `$text`, e o que importa aqui — stemming em
português, acentos, relevância — só existe no Mongo real. Estes testes rodam
quando `MONGO_TEST_URI` está definido (o CI sobe um `mongo:7` para isso) e são
pulados caso contrário. Cada execução usa um banco descartável.
"""

import datetime as dt
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from apps.api import routes
from apps.api.main import app
from src.infrastructure.config import settings
from src.infrastructure.db.migrations import get_migrations
from src.infrastructure.db.migrations.runner import MigrationRunner
from src.infrastructure.security.rate_limit import limiter
from src.infrastructure.utils.datetime_utils import utcnow

CHAVE = "chave-de-teste"
URI = os.environ.get("MONGO_TEST_URI")

pytestmark = pytest.mark.skipif(not URI, reason="MONGO_TEST_URI não definido")


@pytest.fixture
def mongo():
    client = MongoClient(URI, serverSelectionTimeoutMS=3000, tz_aware=True, tzinfo=dt.UTC)
    try:
        client.admin.command("ping")
    except PyMongoError as e:
        pytest.skip(f"Mongo de teste inacessível: {e}")
    nome = f"trends_test_{uuid.uuid4().hex[:8]}"
    yield client[nome]
    client.drop_database(nome)
    client.close()


@pytest.fixture
def db(mongo):
    MigrationRunner(mongo, get_migrations()).run()
    return mongo


def _client(monkeypatch, db) -> TestClient:
    monkeypatch.setattr(routes, "get_db", lambda: db)
    monkeypatch.setattr(settings, "API_KEY", CHAVE)
    monkeypatch.setattr(limiter, "enabled", False)
    return TestClient(app, headers={"X-API-Key": CHAVE})


@pytest.fixture
def client(monkeypatch, db):
    with _client(monkeypatch, db) as c:
        yield c


def _produto(db, pid, titulo, **extra):
    db["products"].insert_one(
        {
            "product_id": pid,
            "title": titulo,
            "source": "mercadolivre",
            "source_product_id": pid,  # índice único (source, source_product_id)
            **extra,
        }
    )


def _insight(db, pid, score, horas_atras):
    db["trend_insights"].insert_one(
        {
            "product_id": pid,
            "ts": utcnow() - dt.timedelta(hours=horas_atras),
            "final_score": score,
            "trend_classification": "SUBINDO",
            "risk_level": "MEDIO",
        }
    )


def test_busca_por_palavra_do_titulo_com_stemming(client, db):
    _produto(db, "p1", "Fone de Ouvido Bluetooth")
    _produto(db, "p2", "Airfryer 4L")

    corpo = client.get("/search", params={"q": "fones"}).json()

    assert [i["product_id"] for i in corpo["items"]] == ["p1"]
    assert corpo["total"] == 1


def test_busca_ignora_acentos(client, db):
    _produto(db, "p1", "Escova Elétrica Sônica")
    assert client.get("/search", params={"q": "eletrica"}).json()["total"] == 1


def test_mais_relevante_primeiro(client, db):
    _produto(db, "p1", "Capa para celular")
    _produto(db, "p2", "Celular Celular Smartphone celular")

    ids = [i["product_id"] for i in client.get("/search", params={"q": "celular"}).json()["items"]]

    assert ids == ["p2", "p1"]


def test_inclui_o_ultimo_score_conhecido(client, db):
    _produto(db, "p1", "Smartwatch esportivo")
    _produto(db, "p2", "Smartwatch infantil")
    _insight(db, "p1", 50, horas_atras=10)
    _insight(db, "p1", 88, horas_atras=1)

    itens = {
        i["product_id"]: i
        for i in client.get("/search", params={"q": "smartwatch"}).json()["items"]
    }

    assert itens["p1"]["latest"]["final_score"] == 88
    assert itens["p1"]["latest"]["trend_classification"] == "SUBINDO"
    assert itens["p2"]["latest"] is None


def test_paginacao(client, db):
    for i in range(5):
        _produto(db, f"p{i}", f"Teclado mecânico modelo {i}")

    p1 = client.get("/search", params={"q": "teclado", "limit": 2, "page": 1}).json()
    p3 = client.get("/search", params={"q": "teclado", "limit": 2, "page": 3}).json()

    assert p1["total"] == p3["total"] == 5
    assert len(p1["items"]) == 2 and len(p3["items"]) == 1
    todos = {
        i["product_id"]
        for p in (1, 2, 3)
        for i in client.get("/search", params={"q": "teclado", "limit": 2, "page": p}).json()[
            "items"
        ]
    }
    assert todos == {f"p{i}" for i in range(5)}


def test_sem_resultado(client, db):
    _produto(db, "p1", "Panela de pressão")
    assert client.get("/search", params={"q": "notebook"}).json() == {
        "q": "notebook",
        "page": 1,
        "limit": 20,
        "total": 0,
        "items": [],
    }


def test_sem_indice_de_texto_devolve_503_com_instrucao(monkeypatch, mongo):
    # Banco sem as migrações aplicadas
    mongo["products"].insert_one({"product_id": "p1", "title": "Fone"})
    with _client(monkeypatch, mongo) as c:
        r = c.get("/search", params={"q": "fone"})
    assert r.status_code == 503
    assert "migra" in r.json()["detail"]


@pytest.mark.parametrize(
    ("filtro_extra", "indice"),
    [({}, "ix_trend_ts"), ({"window_hours": 72}, "ix_trend_window_ts")],
)
def test_indice_cobre_o_filtro_das_listagens(db, filtro_extra, indice):
    """TIE-32/28: ranking e insights filtram por ts (e por window_hours, se pedido)."""
    plano = (
        db["trend_insights"]
        .find({"ts": {"$gte": utcnow() - dt.timedelta(hours=72)}, **filtro_extra})
        .sort([("ts", -1), ("_id", -1)])
        .explain()
    )
    texto = str(plano["queryPlanner"]["winningPlan"])
    assert "IXSCAN" in texto and indice in texto


# --- Idioma do vídeo x índice de texto -------------------------------------------


def test_produto_com_idioma_nao_suportado_pelo_indice_eh_gravado(db):
    """
    O índice de texto usava o campo `language` do documento como idioma do
    stemming (padrão do Mongo). A coleta do TikTok grava `language` com o idioma
    do vídeo, e "ar", "ms", "un"... não são suportados: o Mongo recusava a
    escrita (`language override unsupported`). A v007 desliga esse override.
    """
    from src.infrastructure.db.repos import ProductRepo

    repo = ProductRepo(db)
    for i, idioma in enumerate(["ar", "ms", "un", "eo", "pt", "en"]):
        repo.upsert(
            {
                "source": "tiktok",
                "source_product_id": f"v{i}",
                "title": f"Fones bluetooth {idioma}",
                "language": idioma,
            }
        )

    assert db["products"].count_documents({"language": {"$exists": True}}) == 6
    # O stemming continua em português para todos: "fone" acha "Fones"
    assert db["products"].count_documents({"$text": {"$search": "fone"}}) == 6


def test_v007_eh_idempotente(db):
    from src.infrastructure.db.migrations.versions.v007_text_index_language_override import (
        V007TextIndexLanguageOverride,
    )

    V007TextIndexLanguageOverride().up(db)
    V007TextIndexLanguageOverride().up(db)
    info = db["products"].index_information()["ix_products_title_text"]
    assert info["language_override"] == V007TextIndexLanguageOverride.CAMPO_OVERRIDE
    assert info["default_language"] == "portuguese"
