# src/tests/test_routes.py
"""
Rotas da API com TestClient e mongomock (TIE-12).
"""

import datetime as dt

import mongomock
import pytest
from fastapi.testclient import TestClient

from apps.api import routes
from apps.api.main import app
from src.infrastructure.config import settings
from src.infrastructure.security.rate_limit import limiter
from src.infrastructure.utils.datetime_utils import utcnow

CHAVE = "chave-de-teste"


@pytest.fixture
def db(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True).db
    monkeypatch.setattr(routes, "get_db", lambda: db)
    return db


@pytest.fixture
def client(monkeypatch, db):
    monkeypatch.setattr(settings, "API_KEY", CHAVE)
    # O limiter aponta para o Redis do settings; aqui não há Redis
    monkeypatch.setattr(limiter, "enabled", False)
    with TestClient(app, headers={"X-API-Key": CHAVE}) as c:
        yield c


def _insight(product_id, score, *, horas=72, atras_h=1, sources=("tiktok",)):
    """Como `hybrid_trend_analyze` grava: window_from = momento da análise - horas."""
    ts = utcnow() - dt.timedelta(hours=atras_h)
    return {
        "product_id": product_id,
        "ts": ts,
        "window_from": ts - dt.timedelta(hours=horas),
        "window_to": ts,
        "window_hours": horas,
        "final_score": score,
        "numeric_score": score,
        "llm_score": score,
        "trend_classification": "SUBINDO",
        "risk_level": "MEDIO",
        "sources": list(sources),
    }


# --- Autenticação ----------------------------------------------------------


@pytest.mark.parametrize(
    "rota",
    ["/rankings/latest", "/insights/latest", "/categories", "/health/migrations"],
)
def test_rotas_protegidas_exigem_api_key(client, rota):
    assert client.get(rota, headers={"X-API-Key": ""}).status_code == 401
    assert client.get(rota, headers={"X-API-Key": "errada"}).status_code == 401


def test_rotas_publicas_nao_exigem_api_key(client):
    assert client.get("/health", headers={"X-API-Key": ""}).json() == {"status": "ok"}
    assert client.get("/", headers={"X-API-Key": ""}).status_code == 200


# --- Rankings / insights ----------------------------------------------------


def test_ranking_ordena_por_score_e_usa_o_insight_mais_recente(client, db):
    db["products"].insert_many(
        [{"product_id": "p1", "title": "Fone"}, {"product_id": "p2", "title": "Airfryer"}]
    )
    db["trend_insights"].insert_many(
        [
            _insight("p1", 90, atras_h=5),  # antigo, deve ser ignorado
            _insight("p1", 40, atras_h=1),
            _insight("p2", 70, atras_h=2),
        ]
    )

    corpo = client.get("/rankings/latest").json()

    assert [(i["product_id"], i["final_score"]) for i in corpo["items"]] == [
        ("p2", 70),
        ("p1", 40),
    ]
    assert corpo["items"][1]["title"] == "Fone"


def test_ranking_filtra_por_fonte_e_janela(client, db):
    db["trend_insights"].insert_many(
        [
            _insight("tt", 80, sources=("tiktok",)),
            _insight("ml", 70, sources=("mercadolivre",)),
            _insight("outra-janela", 99, horas=24),
        ]
    )

    corpo = client.get("/rankings/latest", params={"source": "mercadolivre"}).json()

    assert [i["product_id"] for i in corpo["items"]] == ["ml"]


def test_ranking_valida_parametros(client):
    assert client.get("/rankings/latest", params={"source": "shopee"}).status_code == 422
    assert client.get("/rankings/latest", params={"limit": 0}).status_code == 422


def test_ranking_ignora_insight_gerado_fora_da_janela(client, db):
    db["trend_insights"].insert_many([_insight("novo", 50), _insight("velho", 99, atras_h=80)])
    corpo = client.get("/rankings/latest", params={"hours": 72}).json()
    assert [i["product_id"] for i in corpo["items"]] == ["novo"]


def test_insights_latest_filtra_janela_so_quando_pedido(client, db):
    db["trend_insights"].insert_many([_insight("p1", 50), _insight("p2", 60, horas=24)])
    todos = client.get("/insights/latest", params={"hours": 72}).json()
    so_72 = client.get("/insights/latest", params={"hours": 72, "window_hours": 72}).json()
    assert sorted(i["product_id"] for i in todos["items"]) == ["p1", "p2"]
    assert [i["product_id"] for i in so_72["items"]] == ["p1"]


def test_hours_eh_recencia_e_nao_exige_a_mesma_janela(client, db):
    """
    Bug real (TIE-28): o beat analisa sempre com janela de 72 h, e a API
    exigia window_hours == hours — no dashboard, 24/48/168 h davam vazio.
    """
    db["trend_insights"].insert_many(
        [_insight("recente", 70, horas=72, atras_h=2), _insight("velho", 90, horas=72, atras_h=30)]
    )
    for rota in ("/rankings/latest", "/insights/latest"):
        ids = [i["product_id"] for i in client.get(rota, params={"hours": 24}).json()["items"]]
        assert ids == ["recente"], rota


def test_ranking_filtra_janela_quando_pedido(client, db):
    db["trend_insights"].insert_many([_insight("j72", 50, horas=72), _insight("j24", 60, horas=24)])
    corpo = client.get("/rankings/latest", params={"window_hours": 24}).json()
    assert [i["product_id"] for i in corpo["items"]] == ["j24"]


def test_insight_do_produto(client, db):
    assert client.get("/products/p1/insight/latest").status_code == 404
    db["trend_insights"].insert_one(_insight("p1", 50))
    assert client.get("/products/p1/insight/latest").json()["final_score"] == 50


# --- Curva histórica --------------------------------------------------------


def test_curva_do_produto(client, db):
    assert client.get("/products/p1/curve").status_code == 404

    db["products"].insert_one({"product_id": "p1", "title": "Fone"})
    agora = utcnow()
    db["metrics"].insert_many(
        [
            {"product_id": "p1", "ts": agora - dt.timedelta(hours=100), "views": 1},
            {"product_id": "p1", "ts": agora - dt.timedelta(hours=2), "views": 2},
            {"product_id": "p1", "ts": agora - dt.timedelta(hours=1), "views": 3},
        ]
    )

    corpo = client.get("/products/p1/curve", params={"hours": 72}).json()

    assert corpo["count"] == 2
    assert [p["views"] for p in corpo["points"]] == [2, 3]
    # Datas saem com offset explícito (TIE-10)
    assert corpo["points"][0]["ts"].endswith("+00:00")


# --- Categorias -------------------------------------------------------------


def test_categorias_listar_habilitar_desabilitar(client, db):
    db["categories"].insert_many(
        [
            {"key": "a", "name": "Celulares", "ml_category_id": "MLB1051", "enabled": False},
            {"key": "b", "name": "Beleza", "ml_category_id": "MLB1246", "enabled": False},
            {"key": "c", "name": "Sem ID", "enabled": False},
        ]
    )

    assert client.get("/categories").json()["count"] == 2
    assert client.get("/categories", params={"query": "celu"}).json()["count"] == 1

    assert client.post("/categories/enable", json=["MLB1051"]).json() == {"enabled_count": 1}
    assert db["categories"].find_one({"key": "a"})["enabled"] is True
    assert client.post("/categories/disable", json=["MLB1051"]).json() == {"disabled_count": 1}
    assert db["categories"].find_one({"key": "a"})["enabled"] is False


# --- Health das migrações ---------------------------------------------------


@pytest.mark.parametrize(
    ("estados", "esperado"),
    [
        ([], "ok"),
        (["applied", "applied"], "ok"),
        (["applied", "failed"], "degraded"),
        (["failed", "running"], "running"),
    ],
)
def test_health_migrations(client, db, estados, esperado):
    for i, s in enumerate(estados):
        db["migrations"].insert_one({"migration_id": f"m{i}", "status": s, "started_at": utcnow()})
    assert client.get("/health/migrations").json()["status"] == esperado


# --- Busca: validação de parâmetros (a busca em si está em test_search.py) --


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"q": "a"},
        {"q": "x" * 101},
        {"q": "fone", "limit": 51},
        {"q": "fone", "limit": 0},
        {"q": "fone", "page": 0},
    ],
)
def test_search_valida_parametros(client, params):
    assert client.get("/search", params=params).status_code == 422


def test_search_exige_api_key(client):
    assert client.get("/search", params={"q": "fone"}, headers={"X-API-Key": ""}).status_code == 401


# --- Paginação por cursor (TIE-32) -----------------------------------------


def _todas_as_paginas(client, rota, **params):
    """Percorre o cursor até o fim; devolve (páginas, itens)."""
    paginas, itens, cursor = [], [], None
    while True:
        q = {**params, **({"cursor": cursor} if cursor else {})}
        corpo = client.get(rota, params=q).json()
        paginas.append(corpo)
        itens += corpo["items"]
        if not corpo["has_more"]:
            assert corpo["next_cursor"] is None
            return paginas, itens
        cursor = corpo["next_cursor"]
        assert len(paginas) < 50, "cursor não avança"


def test_ranking_paginado_sem_repetir_nem_pular(client, db):
    # 7 produtos com empates de score: o desempate precisa ser estável
    scores = [90, 80, 80, 80, 70, 70, 60]
    db["trend_insights"].insert_many(
        [_insight(f"p{i}", s, atras_h=1 + i * 0.1) for i, s in enumerate(scores)]
    )

    paginas, itens = _todas_as_paginas(client, "/rankings/latest", limit=3)

    assert len(paginas) == 3
    assert [p["count"] for p in paginas] == [3, 3, 1]
    ids = [i["product_id"] for i in itens]
    assert sorted(ids) == sorted(f"p{i}" for i in range(7))  # nenhum repetido/pulado
    assert [i["final_score"] for i in itens] == sorted(scores, reverse=True)


def test_insights_paginado_sem_repetir_nem_pular(client, db):
    base = utcnow()
    docs = [_insight(f"p{i}", 50) for i in range(5)]
    for i, d in enumerate(docs):
        # Dois pares com o mesmo ts: o desempate por _id resolve
        d["ts"] = base - dt.timedelta(minutes=i // 2)
    db["trend_insights"].insert_many(docs)

    _, itens = _todas_as_paginas(client, "/insights/latest", limit=2)

    assert sorted(i["product_id"] for i in itens) == [f"p{i}" for i in range(5)]
    assert [i["ts"] for i in itens] == sorted((i["ts"] for i in itens), reverse=True)


def test_ultima_pagina_exata_nao_promete_mais(client, db):
    db["trend_insights"].insert_many([_insight(f"p{i}", 50 + i) for i in range(4)])
    corpo = client.get("/rankings/latest", params={"limit": 4}).json()
    assert corpo["has_more"] is False and corpo["next_cursor"] is None


@pytest.mark.parametrize("rota", ["/rankings/latest", "/insights/latest"])
@pytest.mark.parametrize("cursor", ["lixo", "eyJ4IjogMX0", "e30"])
def test_cursor_invalido_da_400(client, rota, cursor):
    assert client.get(rota, params={"cursor": cursor}).status_code == 400


def test_cursor_do_ranking_nao_serve_para_insights(client, db):
    db["trend_insights"].insert_many([_insight(f"p{i}", 50 + i) for i in range(3)])
    cursor = client.get("/rankings/latest", params={"limit": 1}).json()["next_cursor"]
    assert client.get("/insights/latest", params={"cursor": cursor}).status_code == 400


# --- /metrics (TIE-36) --------------------------------------------------------


@pytest.fixture
def sem_redis(monkeypatch):
    # Sem Redis nos testes: filas e circuitos vêm vazios
    monkeypatch.setattr(routes, "_redis_metrics", lambda: None)
    monkeypatch.setattr(routes, "breakers_status", lambda: {})


def test_metrics_exige_api_key(client, sem_redis):
    r = client.get("/metrics", headers={"X-API-Key": "errada"})
    assert r.status_code == 401


def test_metrics_em_formato_prometheus_com_rota_como_template(client, db, sem_redis):
    db["trend_insights"].insert_one(_insight("p1", 50))
    client.get("/products/abc/curve")

    r = client.get("/metrics")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain; version=0.0.4")
    # A rota vai como template: um produto não vira uma série nova
    assert 'route="/products/{product_id}/curve"' in r.text
    assert "/products/abc" not in r.text
    assert "trends_insights_24h 1" in r.text
    assert 'trends_component_up{component="redis"} 0' in r.text
