# src/infrastructure/db/migrations/versions/v007_text_index_language_override.py

from pymongo.database import Database

from ..migration_base import Migration, MigrationMeta

INDICE = "ix_products_title_text"


class V007TextIndexLanguageOverride(Migration):
    """
    O índice de texto da v005 usava o `language_override` padrão do Mongo: o
    campo `language` do documento escolhe o idioma do stemming. A coleta do
    TikTok passou a gravar `language` com o idioma do vídeo, e idioma que o
    Mongo não suporta ("ar", "ms", "un"...) faz a escrita falhar com
    `language override unsupported` — derrubava o backfill e a coleta.

    Recria o índice com o override apontando para um campo que nunca é gravado:
    todo título usa o stemming em português (`default_language`), como a busca
    sempre assumiu.
    """

    CAMPO_OVERRIDE = "idioma_do_indice_de_texto"

    meta = MigrationMeta(
        migration_id="20260928_v007_text_index_language_override",
        from_version=6,
        to_version=7,
        description="Índice de texto de products ignora o campo `language` (idioma do vídeo).",
    )

    def up(self, db: Database) -> None:
        col = db["products"]
        atual = col.index_information().get(INDICE)
        if atual and atual.get("language_override") == self.CAMPO_OVERRIDE:
            return
        if atual:
            col.drop_index(INDICE)
        col.create_index(
            [("title", "text")],
            name=INDICE,
            default_language="portuguese",
            language_override=self.CAMPO_OVERRIDE,
        )
