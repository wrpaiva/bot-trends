# src/infrastructure/db/repos.py

import datetime as dt
import uuid
from typing import Dict, Any, Optional
from pymongo.database import Database

class ProductRepo:
    def __init__(self, db: Database):
        self.col = db["products"]
        self.col.create_index([("source", 1), ("source_product_id", 1)], unique=True, name="ux_source_sourceProduct")
        self.col.create_index("product_id", unique=True, name="ux_product_uuid")
        self.col.create_index("canonical_id", name="ix_canonical")

    def upsert(self, item: Dict[str, Any]) -> str:
        now = dt.datetime.utcnow()
        source = item["source"]
        spid = item["source_product_id"]

        existing = self.col.find_one({"source": source, "source_product_id": spid}, {"_id": 0, "product_id": 1})
        product_id = existing["product_id"] if existing and existing.get("product_id") else str(uuid.uuid4())

        update = {
            "$setOnInsert": {
                "created_at": now,
                "product_id": product_id,
                "source": source,
                "source_product_id": spid,
            },
            "$set": {
                "updated_at": now,
                "title": item.get("title"),
                "brand": item.get("brand"),
                "category": item.get("category"),
                "canonical_id": item.get("canonical_id"),
                "permalink": item.get("permalink"),
                "schema_version": 3,
            },
        }

        self.col.update_one({"source": source, "source_product_id": spid}, update, upsert=True)
        return product_id

    def get(self, product_id: str) -> Optional[Dict[str, Any]]:
        return self.col.find_one({"product_id": product_id}, {"_id": 0})


class MetricsRepo:
    def __init__(self, db: Database):
        self.col = db["metrics"]
        self.col.create_index([("product_id", 1), ("ts", -1)], name="ix_metrics_product_ts")
        self.col.create_index([("ts", -1)], name="ix_metrics_ts")
        self.col.create_index([("source", 1), ("ts", -1)], name="ix_metrics_source_ts")

    def insert(self, metric: Dict[str, Any]) -> None:
        self.col.insert_one(metric)