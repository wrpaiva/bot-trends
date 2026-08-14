# src/infrastructure/db/trend_repos.py

import datetime as dt
from typing import Dict, Any, Optional
from pymongo.database import Database

class TrendInsightRepo:
    def __init__(self, db: Database):
        self.col = db["trend_insights"]
        self.col.create_index([("product_id", 1), ("ts", -1)], name="ix_trend_product_ts")
        self.col.create_index([("final_score", -1), ("ts", -1)], name="ix_trend_score_ts")
        self.col.create_index([("window_from", -1)], name="ix_trend_window_from")

    def insert(
        self,
        product_id: str,
        payload: Dict[str, Any],
        *,
        window_from: dt.datetime,
        window_to: dt.datetime,
        window_hours: int,
    ) -> None:
        doc = {
            "product_id": product_id,
            "ts": dt.datetime.utcnow(),
            "window_from": window_from,
            "window_to": window_to,
            "window_hours": window_hours,
            **payload,
        }
        self.col.insert_one(doc)

    def latest(self, product_id: str) -> Optional[Dict[str, Any]]:
        return self.col.find_one({"product_id": product_id}, sort=[("ts", -1)], projection={"_id": 0})