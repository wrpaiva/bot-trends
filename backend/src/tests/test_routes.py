# src/tests/test_routes.py
"""
Rotas da API com TestClient e mongomock (TIE-12).
"""

import datetime as dt

import httpx
import mongomock
import pytest
from fastapi.testclient import TestClient

from apps.api import routes
from apps.api.main import app
from src.infrastructure.collectors.ml_auth import MLAuthError
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


def test_categorias_filtra_por_enabled_e_ordena_por_nome(client, db):
    db["categories"].insert_many(
        [
            {"key": "a", "name": "Celulares", "ml_category_id": "MLB1051", "enabled": True},
            {"key": "b", "name": "Beleza", "ml_category_id": "MLB1246", "enabled": False},
        ]
    )
    todas = client.get("/categories").json()["items"]
    assert [c["name"] for c in todas] == ["Beleza", "Celulares"]
    ativas = client.get("/categories", params={"enabled": True}).json()["items"]
    assert [c["ml_category_id"] for c in ativas] == ["MLB1051"]


def test_busca_de_categoria_trata_o_texto_como_literal(client, db):
    db["categories"].insert_one(
        {"key": "a", "name": "Celulares", "ml_category_id": "MLB1051", "enabled": False}
    )
    # Como regex, ".*" casaria com tudo e "(" daria erro no Mongo
    assert client.get("/categories", params={"query": ".*"}).json()["count"] == 0
    assert client.get("/categories", params={"query": "("}).status_code == 200


@pytest.fixture
def ml_categoria(monkeypatch):
    """Troca a consulta ao ML: {id: resposta} ou {id: exceção}."""
    respostas: dict = {}

    def falso(cid):
        r = respostas.get(cid)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(routes, "_categoria_no_ml", falso)
    return respostas


def test_adiciona_subcategoria_com_o_nome_do_ml(client, db, ml_categoria):
    ml_categoria["MLB1618"] = {
        "ml_category_id": "MLB1618",
        "name": "Cozinha",
        "path": ["Casa, Móveis e Decoração", "Cozinha"],
    }

    r = client.post("/categories", json={"ml_category_id": "MLB1618", "enabled": True})

    assert r.status_code == 200
    corpo = r.json()
    assert corpo["created"] is True
    assert corpo["category"]["name"] == "Cozinha"
    assert corpo["category"]["enabled"] is True
    doc = db["categories"].find_one({"ml_category_id": "MLB1618"})
    assert (doc["key"], doc["path"][0]) == ("ml-MLB1618", "Casa, Móveis e Decoração")
    # Aparece na lista e na coleta (que lê enabled + ml_category_id)
    assert client.get("/categories", params={"enabled": True}).json()["count"] == 1


def test_readicionar_categoria_atualiza_o_nome_e_mantem_enabled(client, db, ml_categoria):
    db["categories"].insert_one(
        {"key": "eletronicos", "name": "Antigo", "ml_category_id": "MLB1051", "enabled": True}
    )
    ml_categoria["MLB1051"] = {"ml_category_id": "MLB1051", "name": "Celulares", "path": []}

    corpo = client.post("/categories", json={"ml_category_id": "MLB1051"}).json()

    assert corpo["created"] is False
    assert db["categories"].count_documents({}) == 1
    doc = db["categories"].find_one({"ml_category_id": "MLB1051"})
    # key do seed não muda (é a chave única), enabled não é desligado
    assert (doc["key"], doc["name"], doc["enabled"]) == ("eletronicos", "Celulares", True)


@pytest.mark.parametrize(
    "resposta, status",
    [
        (None, 404),
        (MLAuthError("sem token"), 503),
        (httpx.ConnectError("fora"), 502),
    ],
)
def test_adicionar_categoria_sem_resposta_do_ml(client, db, ml_categoria, resposta, status):
    ml_categoria["MLB1618"] = resposta
    r = client.post("/categories", json={"ml_category_id": "MLB1618"})
    assert r.status_code == status
    assert db["categories"].count_documents({}) == 0


@pytest.mark.parametrize("cid, status", [("1618", 422), ("MLB16x", 422), ("MLA1618", 400)])
def test_adicionar_categoria_valida_o_id(client, ml_categoria, cid, status):
    assert client.post("/categories", json={"ml_category_id": cid}).status_code == status


def test_ranking_filtra_por_categoria_e_mostra_o_nome(client, db):
    db["categories"].insert_one(
        {"key": "c", "name": "Cozinha", "ml_category_id": "MLB1618", "enabled": True}
    )
    db["products"].insert_many(
        [
            {"product_id": "panela", "title": "Panela", "category": "MLB1618"},
            {"product_id": "fone", "title": "Fone", "category": "MLB1051"},
            {"product_id": "video", "title": "Vídeo", "category": None},
        ]
    )
    db["trend_insights"].insert_many(
        [
            _insight("panela", 50, sources=("mercadolivre",)),
            _insight("fone", 60, sources=("mercadolivre",)),
            _insight("video", 70),
        ]
    )

    todos = client.get("/rankings/latest").json()["items"]
    assert {i["product_id"]: i["category_name"] for i in todos} == {
        "video": None,
        "fone": None,  # categoria sem cadastro: a tela mostra o ID
        "panela": "Cozinha",
    }
    so_cozinha = client.get("/rankings/latest", params={"category": "MLB1618"}).json()
    assert [i["product_id"] for i in so_cozinha["items"]] == ["panela"]


def test_video_vinculado_mostra_onde_comprar_no_ml(client, db):
    # TIE-52: a tendência é do vídeo; o produto do ML vinculado diz onde comprar
    ofertas = {
        "catalog_id": "MLB77",
        "nome": "Kit Brocas 9 Peças",
        "permalink": "https://www.mercadolivre.com.br/p/MLB77",
        "preco_min": 25.5,
        "preco_buybox": 29.9,
        "moeda": "BRL",
        "n_ofertas": 4,
        "disponivel": True,
        "ts": utcnow(),
    }
    db["products"].insert_many(
        [
            {
                "product_id": "video",
                "source": "tiktok",
                "title": "kit brocas #achadinhos",
                "ml_match": {"status": "vinculado", "ml_product_id": "brocas"},
            },
            {
                "product_id": "sem_vinculo",
                "source": "tiktok",
                "title": "dança",
                "ml_match": {"status": "sem_produto"},
            },
            {"product_id": "brocas", "source": "mercadolivre", "ml_offers": ofertas},
            {
                "product_id": "video2",
                "source": "tiktok",
                "title": "garrafa",
                "ml_match": {"status": "vinculado", "ml_product_id": "garrafa"},
            },
            # Vinculado, mas as ofertas ainda não foram lidas
            {"product_id": "garrafa", "source": "mercadolivre"},
            {
                "product_id": "video3",
                "source": "tiktok",
                "title": "tábua",
                "ml_match": {"status": "vinculado", "ml_product_id": "tabua"},
            },
            # Vendido fora do catálogo: sem página de produto, vai a busca
            {
                "product_id": "tabua",
                "source": "mercadolivre",
                "ml_offers": {
                    **ofertas,
                    "permalink": None,
                    "busca": "https://lista.mercadolivre.com.br/tabua-magica",
                    "preco_min": None,
                    "preco_buybox": None,
                    "n_ofertas": 0,
                },
            },
        ]
    )
    db["trend_insights"].insert_many(
        [
            _insight("video", 70),
            _insight("sem_vinculo", 60),
            _insight("video2", 50),
            _insight("video3", 40),
        ]
    )

    itens = {i["product_id"]: i for i in client.get("/rankings/latest").json()["items"]}
    ml = itens["video"]["ml_offer"]
    # Buy box quando existe: a menor oferta pode ser anúncio fora da curva
    assert ml == {
        "nome": "Kit Brocas 9 Peças",
        "link": "https://www.mercadolivre.com.br/p/MLB77",
        "link_tipo": "produto",
        "preco": 29.9,
        "preco_min": 25.5,
        "moeda": "BRL",
        "n_ofertas": 4,
        "disponivel": True,
        "atualizado_em": ml["atualizado_em"],
    }
    assert itens["sem_vinculo"]["ml_offer"] is None
    assert itens["video2"]["ml_offer"] is None
    busca = itens["video3"]["ml_offer"]
    assert busca["link"] == "https://lista.mercadolivre.com.br/tabua-magica"
    assert (busca["link_tipo"], busca["preco"], busca["n_ofertas"]) == ("busca", None, 0)


def test_ranking_com_categoria_invalida_da_422(client):
    assert client.get("/rankings/latest", params={"category": "x; drop"}).status_code == 422


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


def _cursor(**campos) -> str:
    from apps.api.pagination import encode_cursor

    return encode_cursor(**campos)


# QA da TIE-32 (2026-10-14): tipo errado dava 500 (OverflowError do Mongo) ou
# 200 com página errada; `ObjectId(None)` gera um id novo em vez de falhar
@pytest.mark.parametrize(
    "campos",
    [
        {"s": 2**63, "p": "p1"},
        {"s": 10**30, "p": "p1"},
        {"s": 10**100, "p": "p1"},  # 10**400 nem passa no max_length (422)
        {"s": "50", "p": "p1"},
        {"s": True, "p": "p1"},
        {"s": None, "p": "p1"},
        {"s": [1], "p": "p1"},
        {"s": {"$gt": 0}, "p": "p1"},
        {"s": 50.0, "p": 7},
        {"s": 50.0, "p": None},
        {"s": 50.0, "p": {"$ne": ""}},
    ],
)
def test_cursor_do_ranking_com_tipo_errado_da_400(client, db, campos):
    db["trend_insights"].insert_many([_insight(f"p{i}", 50 + i) for i in range(3)])
    r = client.get("/rankings/latest", params={"cursor": _cursor(kind="rankings", **campos)})
    assert r.status_code == 400


@pytest.mark.parametrize(
    "campos",
    [
        {"ts": "2026-10-14T00:00:00+00:00", "id": None},
        {"ts": "2026-10-14T00:00:00+00:00", "id": 12},
        {"ts": 1760400000, "id": "6720c8a1b2c3d4e5f6a7b8c9"},
        {"ts": None, "id": "6720c8a1b2c3d4e5f6a7b8c9"},
    ],
)
def test_cursor_de_insights_com_tipo_errado_da_400(client, db, campos):
    db["trend_insights"].insert_many([_insight(f"p{i}", 50 + i) for i in range(3)])
    r = client.get("/insights/latest", params={"cursor": _cursor(kind="insights", **campos)})
    assert r.status_code == 400


def test_cursor_com_score_inteiro_continua_valendo(client, db):
    # Score gravado como int (ex.: 50) é número válido, não tipo errado
    db["trend_insights"].insert_many([_insight(f"p{i}", 50 + i) for i in range(3)])
    r = client.get("/rankings/latest", params={"cursor": _cursor(kind="rankings", s=51, p="p1")})
    assert r.status_code == 200
    assert [i["product_id"] for i in r.json()["items"]] == ["p0"]


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
