# src/infrastructure/db/alert_repos.py
"""
Estado dos alertas por produto (TIE-31). Persistido no Mongo para o dedupe
sobreviver a restart do worker.
"""

from __future__ import annotations

import datetime as dt

from pymongo.database import Database

from src.domain.alerting import AlertState


class AlertStateRepo:
    def __init__(self, db: Database):
        self.col = db["alert_state"]
        self.col.create_index("product_id", unique=True, name="ux_alert_state_product")

    def get(self, product_id: str) -> AlertState | None:
        doc = self.col.find_one({"product_id": product_id}, {"_id": 0})
        if not doc:
            return None
        return AlertState(alerted_at=doc["alerted_at"], classification=doc["classification"])

    def record(
        self, product_id: str, *, alerted_at: dt.datetime, classification: str, final_score: float
    ) -> None:
        self.col.update_one(
            {"product_id": product_id},
            {
                "$set": {
                    "alerted_at": alerted_at,
                    "classification": classification,
                    "final_score": final_score,
                }
            },
            upsert=True,
        )
