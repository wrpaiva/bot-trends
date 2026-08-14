# src/infrastructure/db/migrations/versions/v001_init_collections.py

from pymongo.database import Database
from ..migration_base import Migration, MigrationMeta

class V001InitCollections(Migration):
    meta = MigrationMeta(
        migration_id="20260222_v001_init_collections",
        from_version=0,
        to_version=1,
        description="Cria índices base para products, metrics, categories e trend_insights.",
    )

    def up(self, db: Database) -> None:
        db["products"].create_index([("source", 1), ("source_product_id", 1)], unique=True, name="ux_source_sourceProduct")
        db["products"].create_index("product_id", unique=True, name="ux_product_uuid")
        db["products"].create_index("canonical_id", name="ix_canonical")

        db["metrics"].create_index([("product_id", 1), ("ts", -1)], name="ix_metrics_product_ts")
        db["metrics"].create_index([("ts", -1)], name="ix_metrics_ts")
        db["metrics"].create_index([("source", 1), ("ts", -1)], name="ix_metrics_source_ts")

        db["categories"].create_index("key", unique=True, name="ux_categories_key")
        db["categories"].create_index("ml_category_id", name="ix_categories_ml_category_id")
        db["categories"].create_index("enabled", name="ix_categories_enabled")

        db["trend_insights"].create_index([("product_id", 1), ("ts", -1)], name="ix_trend_product_ts")
        db["trend_insights"].create_index([("final_score", -1), ("ts", -1)], name="ix_trend_score_ts")