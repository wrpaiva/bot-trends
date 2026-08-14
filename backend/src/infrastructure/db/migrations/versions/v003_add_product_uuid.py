# src/infrastructure/db/migrations/versions/v003_add_product_uuid.py

import datetime as dt
import uuid
from pymongo.database import Database
from ..migration_base import Migration, MigrationMeta

class V003AddProductUUID(Migration):
    meta = MigrationMeta(
        migration_id="20260222_v003_add_product_uuid",
        from_version=2,
        to_version=3,
        description="Garante product_id UUID em products (backfill).",
    )

    def up(self, db: Database) -> None:
        products = db["products"]
        now = dt.datetime.utcnow()

        cursor = products.find({"product_id": {"$exists": False}}, {"_id": 1}, batch_size=500)

        for doc in cursor:
            products.update_one(
                {"_id": doc["_id"], "product_id": {"$exists": False}},
                {"$set": {"product_id": str(uuid.uuid4()), "schema_version": 3, "updated_at": now}},
            )