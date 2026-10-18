# src/infrastructure/db/trend_repos.py

import datetime as dt
from typing import Any

from pymongo.database import Database

from src.domain.insight_dedupe import repete
from src.infrastructure.utils.datetime_utils import utcnow


class TrendInsightRepo:
    def __init__(self, db: Database):
        self.col = db["trend_insights"]
        self.col.create_index([("product_id", 1), ("ts", -1)], name="ix_trend_product_ts")
        self.col.create_index([("final_score", -1), ("ts", -1)], name="ix_trend_score_ts")
        self.col.create_index([("window_from", -1)], name="ix_trend_window_from")

    def insert(
        self,
        product_id: str,
        payload: dict[str, Any],
        *,
        window_from: dt.datetime,
        window_to: dt.datetime,
        window_hours: int,
    ) -> None:
        now = utcnow()
        doc = {
            "product_id": product_id,
            "ts": now,
            # Desde quando este resultado vale; `ts` avança a cada confirmação (TIE-47)
            "first_ts": now,
            "window_from": window_from,
            "window_to": window_to,
            "window_hours": window_hours,
            **payload,
        }
        self.col.insert_one(doc)

    def record(
        self,
        product_id: str,
        payload: dict[str, Any],
        *,
        window_from: dt.datetime,
        window_to: dt.datetime,
        window_hours: int,
        dedupe: bool = True,
    ) -> str:
        """
        Grava o insight, ou confirma o último do produto se ele se repete
        (`src/domain/insight_dedupe.py`, TIE-47). Confirmar atualiza o mesmo
        documento: `ts` e janela avançam, texto/sinais/debug ficam os novos,
        `first_ts` fica e `confirmations` conta. Quem lê "o insight mais recente
        por `ts`" (ranking, saúde, drawer) não percebe a diferença.
        Devolve "inserted" ou "confirmed".
        """
        novo = {"window_hours": window_hours, **payload}
        if dedupe:
            anterior = self.col.find_one({"product_id": product_id}, sort=[("ts", -1)])
            if repete(anterior, novo):
                self.col.update_one(
                    {"_id": anterior["_id"]},
                    {
                        "$set": {
                            **payload,
                            "ts": utcnow(),
                            "window_from": window_from,
                            "window_to": window_to,
                        },
                        # Insight de antes da TIE-47 não tem first_ts: começa no ts dele
                        "$min": {"first_ts": anterior["ts"]},
                        "$inc": {"confirmations": 1},
                    },
                )
                return "confirmed"
        self.insert(
            product_id,
            payload,
            window_from=window_from,
            window_to=window_to,
            window_hours=window_hours,
        )
        return "inserted"

    def latest(self, product_id: str) -> dict[str, Any] | None:
        return self.col.find_one(
            {"product_id": product_id}, sort=[("ts", -1)], projection={"_id": 0}
        )
