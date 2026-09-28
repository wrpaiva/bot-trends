# src/infrastructure/db/migrations/__init__.py

from .versions.v001_init_collections import V001InitCollections
from .versions.v002_add_canonical_id import V002AddCanonicalAndSchemaVersion
from .versions.v003_add_product_uuid import V003AddProductUUID
from .versions.v004_add_window_fields import V004AddWindowFields
from .versions.v005_add_products_text_index import V005AddProductsTextIndex
from .versions.v006_add_insights_window_ts_index import V006AddInsightsWindowTsIndex


def get_migrations():
    return [
        V001InitCollections(),
        V002AddCanonicalAndSchemaVersion(),
        V003AddProductUUID(),
        V004AddWindowFields(),
        V005AddProductsTextIndex(),
        V006AddInsightsWindowTsIndex(),
    ]
