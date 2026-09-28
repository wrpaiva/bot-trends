# src/tests/test_bootstrap.py
"""
Seed de categorias x `collect_ml` (TIE-20).

Antes, o seed default gravava categorias sem `ml_category_id`: elas não apareciam
em `GET /categories`, `POST /categories/enable` não as encontrava e `collect_ml`
devolvia `no enabled categories` para sempre num sistema recém-instalado.
"""

import mongomock
import pytest

from apps.api import routes
from apps.bootstrap.main import default_seed, seed_categories

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
