# src/infrastructure/db/migrations/versions/v004_add_window_fields.py

import datetime as dt
from pymongo.database import Database
from ..migration_base import Migration, MigrationMeta

class V004AddWindowFields(Migration):
    meta = MigrationMeta(
        migration_id="20260222_v004_add_window_fields",
        from_version=3,
        to_version=4,
        description="Adiciona campos de janela em trend_insights antigos (window_from/to/hours).",
    )

    def up(self, db: Database) -> None:
        insights = db["trend_insights"]
        now = dt.datetime.utcnow()

        # define default para registros antigos que não tinham janela
        default_hours = 72

        cursor = insights.find(
            {"window_from": {"$exists": False}},
            {"_id": 1, "ts": 1},
            batch_size=500,
        )

        for doc in cursor:
            ts = doc.get("ts") or now
            window_to = ts
            window_from = ts - dt.timedelta(hours=default_hours)

            insights.update_one(
                {"_id": doc["_id"], "window_from": {"$exists": False}},
                {"$set": {"window_hours": default_hours, "window_from": window_from, "window_to": window_to}},
            )