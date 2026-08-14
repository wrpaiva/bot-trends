# src/infrastructure/db/migrations/runner.py

import datetime as dt
import socket
from typing import List
from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError
from .migration_base import Migration

MIGRATIONS_COLLECTION = "migrations"
LOCK_COLLECTION = "migration_lock"

class MigrationRunner:
    def __init__(self, db: Database, migrations: List[Migration]):
        self.db = db
        self.migrations = migrations

    def _ensure_collections(self):
        self.db[MIGRATIONS_COLLECTION].create_index("migration_id", unique=True)
        self.db[LOCK_COLLECTION].create_index("lock_key", unique=True)

    def _acquire_lock(self, ttl_seconds: int = 600) -> bool:
        now = dt.datetime.utcnow()
        expires_at = now + dt.timedelta(seconds=ttl_seconds)
        host = socket.gethostname()

        res = self.db[LOCK_COLLECTION].find_one_and_update(
            {
                "lock_key": "global",
                "$or": [
                    {"expires_at": {"$lte": now}},
                    {"expires_at": {"$exists": False}},
                    {"owner": host},
                ],
            },
            {
                "$set": {
                    "lock_key": "global",
                    "owner": host,
                    "acquired_at": now,
                    "expires_at": expires_at,
                }
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return res is not None and res.get("owner") == host

    def _release_lock(self):
        host = socket.gethostname()
        self.db[LOCK_COLLECTION].delete_one({"lock_key": "global", "owner": host})

    def _is_applied(self, migration_id: str) -> bool:
        doc = self.db[MIGRATIONS_COLLECTION].find_one(
            {"migration_id": migration_id, "status": "applied"},
            {"_id": 0, "migration_id": 1},
        )
        return doc is not None

    def run(self) -> None:
        self._ensure_collections()

        if not self._acquire_lock():
            raise RuntimeError("Não foi possível adquirir lock de migração (outra instância executando).")

        try:
            for m in self.migrations:
                mid = m.meta.migration_id
                if self._is_applied(mid):
                    continue

                started = dt.datetime.utcnow()
                record = {
                    "migration_id": mid,
                    "from_version": m.meta.from_version,
                    "to_version": m.meta.to_version,
                    "description": m.meta.description,
                    "status": "running",
                    "started_at": started,
                    "finished_at": None,
                    "host": socket.gethostname(),
                }

                try:
                    self.db[MIGRATIONS_COLLECTION].insert_one(record)
                except DuplicateKeyError:
                    self.db[MIGRATIONS_COLLECTION].update_one(
                        {"migration_id": mid},
                        {"$set": {"status": "running", "started_at": started, "finished_at": None}},
                    )

                try:
                    m.up(self.db)
                    finished = dt.datetime.utcnow()
                    self.db[MIGRATIONS_COLLECTION].update_one(
                        {"migration_id": mid},
                        {"$set": {"status": "applied", "finished_at": finished}},
                    )
                except Exception as e:
                    finished = dt.datetime.utcnow()
                    self.db[MIGRATIONS_COLLECTION].update_one(
                        {"migration_id": mid},
                        {"$set": {"status": "failed", "finished_at": finished, "error": str(e)}},
                    )
                    raise
        finally:
            self._release_lock()