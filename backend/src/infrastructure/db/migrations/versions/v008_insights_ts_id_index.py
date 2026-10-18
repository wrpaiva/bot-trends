# src/infrastructure/db/migrations/versions/v008_insights_ts_id_index.py

from pymongo.database import Database

from ..migration_base import Migration, MigrationMeta


class V008InsightsTsIdIndex(Migration):
    meta = MigrationMeta(
        migration_id="20261017_v008_insights_ts_id_index",
        from_version=7,
        to_version=8,
        description="Índices de trend_insights com _id: o sort (ts, _id) sai do índice.",
    )

    def up(self, db: Database) -> None:
        # As listagens ordenam por (ts, _id) e os índices da v006 só tinham
        # `ts`: cada página ordenava a janela inteira em memória (no QA da
        # TIE-32, 4.394 docs em 24 h para devolver 51). Os novos têm os antigos
        # como prefixo, então os antigos saem — senão o planner pode escolhê-los
        # e voltar ao SORT em memória (TIE-50).
        col = db["trend_insights"]
        col.create_index([("ts", -1), ("_id", -1)], name="ix_trend_ts_id")
        col.create_index(
            [("window_hours", 1), ("ts", -1), ("_id", -1)], name="ix_trend_window_ts_id"
        )
        existentes = set(col.index_information())
        for antigo in ("ix_trend_ts", "ix_trend_window_ts"):
            if antigo in existentes:
                col.drop_index(antigo)
