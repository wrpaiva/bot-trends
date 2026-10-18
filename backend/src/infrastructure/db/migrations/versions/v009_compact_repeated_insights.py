# src/infrastructure/db/migrations/versions/v009_compact_repeated_insights.py

from pymongo.database import Database

from src.domain.insight_dedupe import chave

from ..migration_base import Migration, MigrationMeta


class V009CompactRepeatedInsights(Migration):
    meta = MigrationMeta(
        migration_id="20261018_v009_compact_repeated_insights",
        from_version=8,
        to_version=9,
        description="Compacta insights repetidos: um documento por resultado (TIE-47).",
    )

    def up(self, db: Database) -> None:
        # Desde a TIE-47 a análise não grava insight que repete o anterior do
        # produto: confirma o anterior (`ts` avança, `first_ts` fica). Esta
        # migração deixa o histórico no mesmo formato: em cada sequência de
        # insights repetidos fica o ÚLTIMO (o mais novo, que o ranking já lia),
        # com `first_ts` do primeiro e `confirmations` = quantos sumiram. Em
        # 2026-10-18 eram 94% dos insights de 7 dias e 50 dos 51 MB do banco.
        #
        # Idempotente: rodar de novo não acha sequência repetida. O backtest
        # não lê insight (lê `metrics`), então nada que ele usa muda.
        col = db["trend_insights"]
        # (product_id -1, ts 1) é o índice ix_trend_product_ts percorrido ao
        # contrário: sem ele, ordenar a coleção inteira estoura a memória do sort
        cursor = col.find(
            {},
            {
                "product_id": 1,
                "ts": 1,
                "first_ts": 1,
                "confirmations": 1,
                "final_score": 1,
                "numeric_score": 1,
                "llm_score": 1,
                "trend_classification": 1,
                "risk_level": 1,
                "prompt_version": 1,
                "commercial_marker": 1,
                "window_hours": 1,
                "sources": 1,
                "score_weights": 1,
                "debug.llm_error": 1,
            },
        ).sort([("product_id", -1), ("ts", 1)])

        seq: list[dict] = []
        produto = None
        k = None

        def fecha():
            if len(seq) < 2:
                return
            fica = seq[-1]
            # Apaga antes de atualizar: se cair no meio, rodar de novo não conta
            # a mesma repetição duas vezes (perde-se no máximo o first_ts)
            col.delete_many({"_id": {"$in": [d["_id"] for d in seq[:-1]]}})
            col.update_one(
                {"_id": fica["_id"]},
                {
                    "$set": {
                        "first_ts": min(d.get("first_ts") or d["ts"] for d in seq),
                        "confirmations": sum(d.get("confirmations") or 0 for d in seq)
                        + len(seq)
                        - 1,
                    }
                },
            )

        for doc in cursor:
            kd = chave(doc)
            if doc.get("product_id") != produto or kd != k:
                fecha()
                seq = []
                produto, k = doc.get("product_id"), kd
            seq.append(doc)
        fecha()
