# src/tests/test_migration_runner.py
"""
MigrationRunner com mongomock (TIE-12): aplica em ordem, é idempotente,
registra falha e respeita o lock distribuído.
"""

import datetime as dt

import mongomock
import pytest

from src.infrastructure.db.migrations import get_migrations
from src.infrastructure.db.migrations.migration_base import Migration, MigrationMeta
from src.infrastructure.db.migrations.runner import (
    LOCK_COLLECTION,
    MIGRATIONS_COLLECTION,
    MigrationRunner,
)
from src.infrastructure.utils.datetime_utils import utcnow


@pytest.fixture
def db():
    return mongomock.MongoClient(tz_aware=True).db


class _Contadora(Migration):
    def __init__(self, mid: str, falha: bool = False):
        self.meta = MigrationMeta(migration_id=mid, from_version=0, to_version=1, description=mid)
        self.chamadas = 0
        self.falha = falha

    def up(self, db) -> None:
        self.chamadas += 1
        if self.falha:
            raise ValueError("deu ruim")
        db["efeitos"].insert_one({"mid": self.meta.migration_id})


def _status(db) -> dict[str, str]:
    return {m["migration_id"]: m["status"] for m in db[MIGRATIONS_COLLECTION].find()}


def test_aplica_em_ordem_e_registra():
    db = mongomock.MongoClient(tz_aware=True).db
    a, b = _Contadora("a"), _Contadora("b")

    MigrationRunner(db, [a, b]).run()

    assert [e["mid"] for e in db["efeitos"].find()] == ["a", "b"]
    assert _status(db) == {"a": "applied", "b": "applied"}
    registro = db[MIGRATIONS_COLLECTION].find_one({"migration_id": "a"})
    assert registro["finished_at"] >= registro["started_at"]


def test_rodar_de_novo_nao_reaplica(db):
    a = _Contadora("a")
    MigrationRunner(db, [a]).run()
    MigrationRunner(db, [a]).run()
    assert a.chamadas == 1


def test_falha_eh_registrada_propaga_e_libera_o_lock(db):
    ok, ruim, depois = _Contadora("ok"), _Contadora("ruim", falha=True), _Contadora("depois")

    with pytest.raises(ValueError, match="deu ruim"):
        MigrationRunner(db, [ok, ruim, depois]).run()

    assert _status(db) == {"ok": "applied", "ruim": "failed"}
    assert db[MIGRATIONS_COLLECTION].find_one({"migration_id": "ruim"})["error"] == "deu ruim"
    assert depois.chamadas == 0
    assert db[LOCK_COLLECTION].count_documents({}) == 0


def test_migracao_que_falhou_eh_retentada_na_proxima_execucao(db):
    ruim = _Contadora("ruim", falha=True)
    with pytest.raises(ValueError):
        MigrationRunner(db, [ruim]).run()

    ruim.falha = False
    MigrationRunner(db, [ruim]).run()

    assert _status(db) == {"ruim": "applied"}
    assert db[MIGRATIONS_COLLECTION].count_documents({}) == 1


def test_lock_de_outra_instancia_bloqueia_com_mensagem_clara(db):
    MigrationRunner(db, [])._ensure_collections()
    db[LOCK_COLLECTION].insert_one(
        {
            "lock_key": "global",
            "owner": "outro-host",
            "expires_at": utcnow() + dt.timedelta(minutes=5),
        }
    )
    a = _Contadora("a")

    with pytest.raises(RuntimeError, match="lock"):
        MigrationRunner(db, [a]).run()
    assert a.chamadas == 0
    # O lock alheio continua lá
    assert db[LOCK_COLLECTION].find_one()["owner"] == "outro-host"


def test_lock_expirado_de_outra_instancia_eh_assumido(db):
    MigrationRunner(db, [])._ensure_collections()
    db[LOCK_COLLECTION].insert_one(
        {
            "lock_key": "global",
            "owner": "outro-host",
            "expires_at": utcnow() - dt.timedelta(minutes=1),
        }
    )
    a = _Contadora("a")

    MigrationRunner(db, [a]).run()

    assert a.chamadas == 1


def test_migracoes_reais_rodam_e_sao_idempotentes(db):
    db["products"].insert_one({"title": "Fone Bluetooth", "brand": "X", "category": "MLB1"})

    MigrationRunner(db, get_migrations()).run()
    produto = db["products"].find_one()
    MigrationRunner(db, get_migrations()).run()

    assert set(_status(db).values()) == {"applied"}
    assert len(_status(db)) == len(get_migrations())
    depois = db["products"].find_one()
    assert depois["canonical_id"] == produto["canonical_id"] == "x-mlb1-fone-bluetooth"
    assert depois["product_id"] == produto["product_id"]
