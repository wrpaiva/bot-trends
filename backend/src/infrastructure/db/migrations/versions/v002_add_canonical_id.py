# src/infrastructure/db/migrations/versions/v002_add_canonical_id.py

import re
import datetime as dt
from pymongo.database import Database
from ..migration_base import Migration, MigrationMeta

def slugify(text: str) -> str:
    text = (text or "").lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", "", text)
    return text.replace(" ", "-")[:80]

class V002AddCanonicalAndSchemaVersion(Migration):
    meta = MigrationMeta(
        migration_id="20260222_v002_add_canonical_and_schema_version",
        from_version=1,
        to_version=2,
        description="Adiciona schema_version e canonical_id para products antigos (backfill).",
    )

    def up(self, db: Database) -> None:
        products = db["products"]
        now = dt.datetime.utcnow()

        products.update_many(
            {"schema_version": {"$exists": False}},
            {"$set": {"schema_version": 1, "updated_at": now}},
        )

        cursor = products.find(
            {"canonical_id": {"$exists": False}},
            {"_id": 1, "title": 1, "brand": 1, "category": 1},
            batch_size=500,
        )

        for doc in cursor:
            title = doc.get("title", "")
            brand = doc.get("brand", "")
            category = doc.get("category", "")
            canonical = slugify(f"{brand} {category} {title}".strip())
            if not canonical:
                canonical = f"prod-{str(doc['_id'])}"

            products.update_one(
                {"_id": doc["_id"], "canonical_id": {"$exists": False}},
                {"$set": {"canonical_id": canonical, "schema_version": 2, "updated_at": now}},
            )