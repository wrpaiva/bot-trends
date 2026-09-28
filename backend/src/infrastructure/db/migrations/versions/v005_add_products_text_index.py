# src/infrastructure/db/migrations/versions/v005_add_products_text_index.py

from pymongo.database import Database

from ..migration_base import Migration, MigrationMeta


class V005AddProductsTextIndex(Migration):
    meta = MigrationMeta(
        migration_id="20260927_v005_add_products_text_index",
        from_version=4,
        to_version=5,
        description="Índice de texto em products.title para GET /search (TIE-30).",
    )

    def up(self, db: Database) -> None:
        # Stemming em português: "fones" encontra "Fone". Acento já é ignorado
        # pelo índice de texto v3. create_index com a mesma spec é no-op.
        db["products"].create_index(
            [("title", "text")],
            name="ix_products_title_text",
            default_language="portuguese",
        )
