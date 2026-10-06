# src/tests/test_bootstrap.py
"""
Seed de categorias x `collect_ml` (TIE-20).

Antes, o seed default gravava categorias sem `ml_category_id`: elas não apareciam
em `GET /categories`, `POST /categories/enable` não as encontrava e `collect_ml`
devolvia `no enabled categories` para sempre num sistema recém-instalado.
"""

import httpx
import mongomock
import pytest

from apps.api import routes
from apps.bootstrap import main as boot
from apps.bootstrap.main import default_seed, fetch_ml_categories, seed_categories
from src.infrastructure.collectors.ml_auth import MLAuthError

# Mesmo filtro de `tasks.collect_ml`
FILTRO_COLETA = {"enabled": True, "ml_category_id": {"$exists": True}}


@pytest.fixture
def db(monkeypatch):
    db = mongomock.MongoClient().db
    monkeypatch.setattr(routes, "get_db", lambda: db)
    return db


def test_seed_default_tem_ml_category_id_e_nome():
    for c in default_seed():
        assert c["ml_category_id"].startswith("MLB"), c["key"]
        assert c["name"]
        assert c["enabled"] is False


def test_seed_default_aparece_na_listagem_da_api(db):
    seed_categories(db, default_seed())
    assert routes.list_categories(query="", limit=200)["count"] == len(default_seed())


def test_categoria_do_seed_default_pode_ser_habilitada_e_coletada(db):
    seed_categories(db, default_seed())
    assert db["categories"].count_documents(FILTRO_COLETA) == 0

    alvo = default_seed()[0]["ml_category_id"]
    assert routes.enable_categories([alvo]) == {"enabled_count": 1}

    coletaveis = [c["ml_category_id"] for c in db["categories"].find(FILTRO_COLETA)]
    assert coletaveis == [alvo]


def test_seed_eh_idempotente(db):
    seed_categories(db, default_seed())
    seed_categories(db, default_seed())
    assert db["categories"].count_documents({}) == len(default_seed())


def test_rodar_o_seed_de_novo_nao_desabilita_o_que_o_usuario_habilitou(db):
    seed_categories(db, default_seed())
    alvo = default_seed()[0]["ml_category_id"]
    routes.enable_categories([alvo])

    seed_categories(db, default_seed())

    assert db["categories"].find_one({"ml_category_id": alvo})["enabled"] is True


def test_arvore_do_ml_nao_duplica_categoria_do_seed_default(db):
    seed_categories(db, default_seed())
    padrao = default_seed()[0]
    vinda_da_api = {
        "key": f"ml-{padrao['ml_category_id']}",
        "name": "Nome oficial no ML",
        "ml_category_id": padrao["ml_category_id"],
        "keywords": [],
        "enabled": False,
    }

    seed_categories(db, [vinda_da_api])

    docs = list(db["categories"].find({"ml_category_id": padrao["ml_category_id"]}))
    assert len(docs) == 1
    assert docs[0]["name"] == "Nome oficial no ML"
    # key e keywords do seed default sobrevivem
    assert docs[0]["key"] == padrao["key"]
    assert docs[0]["keywords"] == padrao["keywords"]


# --- Árvore real do ML com token (TIE-20 + TIE-41) -----------------------------
# `/sites/MLB/categories` dá 403 sem `Authorization: Bearer` desde 2026-09.


class _AuthFalso:
    def __init__(self, token="APP_USR-t1", falha=None):
        self.token, self.falha, self.renovacoes, self.fechado = token, falha, [], False

    def access_token(self):
        if self.falha:
            raise MLAuthError(self.falha)
        return self.token

    def refresh(self, stale=None):
        self.renovacoes.append(stale)
        self.token = "APP_USR-t2"
        return self.token

    def close(self):
        self.fechado = True


_ARVORE = [
    {"id": "MLB1051", "name": "Celulares e Telefones"},
    {"id": "MLB1000", "name": "Eletrônicos, Áudio e Vídeo"},
    {"id": None, "name": "sem id"},
]


def _transporte(headers: list, status_por_token=None):
    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization")
        headers.append(auth)
        assert request.url.path == "/sites/MLB/categories"
        status = (status_por_token or {}).get(auth, 200)
        if status != 200:
            return httpx.Response(status, json={"message": "invalid access token"})
        return httpx.Response(200, json=_ARVORE)

    return httpx.MockTransport(handler)


def test_arvore_do_ml_vai_com_bearer_e_vira_categorias():
    headers: list = []

    cats = fetch_ml_categories("MLB", _AuthFalso(), transport=_transporte(headers))

    assert headers == ["Bearer APP_USR-t1"]
    assert [(c["key"], c["ml_category_id"], c["enabled"]) for c in cats] == [
        ("ml-MLB1051", "MLB1051", False),
        ("ml-MLB1000", "MLB1000", False),
    ]


def test_arvore_do_ml_com_401_renova_o_token_uma_vez():
    headers: list = []
    auth = _AuthFalso()

    cats = fetch_ml_categories(
        "MLB", auth, transport=_transporte(headers, {"Bearer APP_USR-t1": 401})
    )

    assert len(cats) == 2
    assert auth.renovacoes == ["APP_USR-t1"]
    assert headers == ["Bearer APP_USR-t1", "Bearer APP_USR-t2"]


def test_arvore_do_ml_com_403_levanta_erro_http():
    with pytest.raises(httpx.HTTPStatusError):
        fetch_ml_categories(
            "MLB", _AuthFalso(), transport=_transporte([], {"Bearer APP_USR-t1": 403})
        )


def test_bootstrap_com_fetch_e_sem_token_para_com_mensagem(monkeypatch, capsys):
    db = mongomock.MongoClient(tz_aware=True).db
    auth = _AuthFalso(falha="nenhum token do Mercado Livre gravado")
    monkeypatch.setattr(boot, "get_db", lambda: db)
    monkeypatch.setattr(boot, "MigrationRunner", _RunnerFalso)
    monkeypatch.setattr(boot.settings, "BOOTSTRAP_FETCH_ML_CATEGORIES", True)
    monkeypatch.setattr(boot.MercadoLivreAuth, "from_settings", lambda _db: auth)

    assert boot.main() == 1

    assert "nenhum token" in capsys.readouterr().out
    assert db["categories"].count_documents({}) == 0
    assert auth.fechado


def test_bootstrap_com_fetch_grava_a_arvore_do_ml(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True).db
    monkeypatch.setattr(boot, "get_db", lambda: db)
    monkeypatch.setattr(boot, "MigrationRunner", _RunnerFalso)
    monkeypatch.setattr(boot.settings, "BOOTSTRAP_FETCH_ML_CATEGORIES", True)
    monkeypatch.setattr(boot.settings, "BOOTSTRAP_AUTO_ENABLE", False)
    monkeypatch.setattr(boot.MercadoLivreAuth, "from_settings", lambda _db: _AuthFalso())
    monkeypatch.setattr(
        boot,
        "fetch_ml_categories",
        lambda site, auth: [{"key": "ml-MLB1", "name": "X", "ml_category_id": "MLB1"}],
    )

    assert boot.main() == 0
    assert db["categories"].find_one({"ml_category_id": "MLB1"})["name"] == "X"


class _RunnerFalso:
    def __init__(self, **_):
        pass

    def run(self):
        return None
