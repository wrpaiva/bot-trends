# src/tests/test_backfill_published.py
"""
Backfill da data de publicação dos vídeos já coletados (motor de tendência).

Os vídeos gravados antes do motor novo não têm `published_at` e ficam no
cálculo antigo. Os datasets dos runs anteriores da Apify continuam legíveis:
o backfill só LÊ (não inicia run, não gasta crédito) e completa o que falta.
"""

import datetime as dt

import httpx
import mongomock
import pytest

from apps.backfill import tiktok_published as mod


def _apify(runs_por_pagina, itens_por_dataset):
    """Transporte falso: lista de runs paginada + itens de cada dataset."""
    chamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        if request.url.path.endswith("/runs"):
            offset = int(request.url.params.get("offset", 0))
            pagina = runs_por_pagina.get(offset, [])
            return httpx.Response(200, json={"data": {"items": pagina}})
        ds = request.url.path.split("/datasets/")[1].split("/")[0]
        return httpx.Response(200, json=itens_por_dataset.get(ds, []))

    return httpx.MockTransport(handler), chamadas


@pytest.fixture
def db():
    db = mongomock.MongoClient(tz_aware=True).db
    for spid in ("111", "222", "333"):
        db["products"].insert_one(
            {"product_id": f"p{spid}", "source": "tiktok", "source_product_id": spid}
        )
    # Já tem data: não pode ser sobrescrita
    db["products"].update_one(
        {"source_product_id": "333"},
        {"$set": {"published_at": dt.datetime(2020, 1, 1, tzinfo=dt.UTC)}},
    )
    return db


ITENS = {
    "ds1": [
        {"id": "111", "createTimeISO": "2026-09-24T20:28:36.000Z", "hasTikTokShopProduct": True},
        {"id": "333", "createTimeISO": "2026-09-01T00:00:00.000Z"},
    ],
    "ds2": [{"id": "222", "createTime": 1790281716, "textLanguage": "pt"}, {"id": "999"}],
}


def _roda(db, dry_run=False):
    transport, chamadas = _apify(
        {0: [{"defaultDatasetId": "ds1"}], 1: [{"defaultDatasetId": "ds2"}]}, ITENS
    )
    res = mod.backfill(db, token="t", transport=transport, dry_run=dry_run, page_size=1)
    return res, chamadas


def test_completa_a_data_dos_videos_que_nao_tem(db):
    res, _ = _roda(db)

    p111 = db["products"].find_one({"source_product_id": "111"})
    p222 = db["products"].find_one({"source_product_id": "222"})
    assert p111["published_at"] == dt.datetime(2026, 9, 24, 20, 28, 36, tzinfo=dt.UTC)
    assert p111["has_shop_product"] is True
    assert p222["published_at"] == dt.datetime(2026, 9, 24, 20, 28, 36, tzinfo=dt.UTC)
    assert p222["language"] == "pt"
    assert res["updated"] == 2


def test_nao_sobrescreve_data_existente(db):
    _roda(db)
    p333 = db["products"].find_one({"source_product_id": "333"})
    assert p333["published_at"] == dt.datetime(2020, 1, 1, tzinfo=dt.UTC)


def test_dry_run_nao_grava(db):
    res, _ = _roda(db, dry_run=True)
    assert res["updated"] == 2 and res["dry_run"] is True
    assert res["still_missing"] == 0  # o que SERIA completado já é descontado
    assert db["products"].count_documents({"published_at": {"$exists": True}}) == 1


def test_eh_idempotente(db):
    _roda(db)
    res, _ = _roda(db)
    assert res["updated"] == 0


def test_so_le_nunca_inicia_run(db):
    _, chamadas = _roda(db)
    assert {r.method for r in chamadas} == {"GET"}
    assert all(r.headers["Authorization"] == "Bearer t" for r in chamadas)
    assert not any(v == "t" for r in chamadas for v in r.url.params.values())


def test_relata_quantos_ficaram_sem_data(db):
    db["products"].insert_one(
        {"product_id": "p444", "source": "tiktok", "source_product_id": "444"}
    )
    res, _ = _roda(db)
    assert res["still_missing"] == 1  # 444 não está em nenhum dataset
