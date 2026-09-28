# src/infrastructure/db/migrations/versions/v006_add_insights_window_ts_index.py

from pymongo.database import Database

from ..migration_base import Migration, MigrationMeta


class V006AddInsightsWindowTsIndex(Migration):
    meta = MigrationMeta(
        migration_id="20260927_v006_add_insights_window_ts_index",
        from_version=5,
        to_version=6,
        description="Índices ts e (window_hours, ts) em trend_insights para ranking/insights.",
    )

    def up(self, db: Database) -> None:
        # /rankings/latest e /insights/latest filtram por ts (faixa) e ordenam por
        # ts; `window_hours` só entra quando pedido (TIE-28). Nenhum índice
        # anterior cobria nenhum dos dois formatos (TIE-32).
        db["trend_insights"].create_index([("ts", -1)], name="ix_trend_ts")
        db["trend_insights"].create_index(
            [("window_hours", 1), ("ts", -1)], name="ix_trend_window_ts"
        )
