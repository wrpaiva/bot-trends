# apps/backfill/tiktok_published.py
"""
Backfill da data de publicação dos vídeos do TikTok já coletados (motor de
tendência). Uso, uma vez depois do deploy:

    docker compose run --rm api python -m apps.backfill.tiktok_published --dry-run
    docker compose run --rm api python -m apps.backfill.tiktok_published

Só LÊ a Apify — lista os runs bem-sucedidos e os itens dos datasets deles; não
inicia run, não gasta crédito de execução. Idempotente: não sobrescreve data já
gravada. Vídeo fora de qualquer dataset ainda disponível fica sem data e segue
no cálculo antigo (a Apify apaga datasets antigos).
"""

from __future__ import annotations

import argparse
import json
import logging

import httpx
from pymongo.database import Database

from src.infrastructure.collectors.tiktok import parse_published_at
from src.infrastructure.config import settings
from src.infrastructure.logging_setup import http_log_hooks

logger = logging.getLogger(__name__)

BASE_URL = "https://api.apify.com/v2"


def _datasets(client: httpx.Client, actor: str, page_size: int) -> list[str]:
    ids, offset = [], 0
    while True:
        pagina = (
            client.get(
                f"/acts/{actor}/runs",
                params={"status": "SUCCEEDED", "offset": offset, "limit": page_size, "desc": 1},
            )
            .raise_for_status()
            .json()["data"]["items"]
        )
        if not pagina:
            return ids
        ids += [r["defaultDatasetId"] for r in pagina if r.get("defaultDatasetId")]
        offset += len(pagina)


def _metadados(client: httpx.Client, dataset_ids: list[str]) -> dict[str, dict]:
    """source_product_id → campos a completar, do primeiro dataset que o tiver."""
    meta: dict[str, dict] = {}
    for ds in dataset_ids:
        itens = (
            client.get(f"/datasets/{ds}/items", params={"clean": "true", "limit": 10_000})
            .raise_for_status()
            .json()
        )
        for it in itens:
            vid = str(it.get("id") or "")
            pub = parse_published_at(it)
            if vid and pub and vid not in meta:
                campos = {"published_at": pub}
                if it.get("hasTikTokShopProduct") is not None:
                    campos["has_shop_product"] = bool(it["hasTikTokShopProduct"])
                if it.get("textLanguage"):
                    campos["language"] = it["textLanguage"]
                meta[vid] = campos
    return meta


def backfill(
    db: Database,
    *,
    token: str,
    actor: str = "clockworks~tiktok-scraper",
    dry_run: bool = False,
    page_size: int = 100,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    with httpx.Client(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {token}"},  # nunca na URL (TIE-3)
        timeout=60,
        transport=transport,
        event_hooks=http_log_hooks("apify"),
    ) as client:
        meta = _metadados(client, _datasets(client, actor, page_size))

    sem_data = {"source": "tiktok", "published_at": {"$exists": False}}
    atualizados = 0
    for p in db["products"].find(sem_data, {"_id": 0, "source_product_id": 1}):
        campos = meta.get(str(p["source_product_id"]))
        if not campos:
            continue
        atualizados += 1
        if not dry_run:
            db["products"].update_one(
                {**sem_data, "source_product_id": p["source_product_id"]}, {"$set": campos}
            )

    restantes = db["products"].count_documents(sem_data)
    return {
        "dry_run": dry_run,
        "videos_nos_datasets": len(meta),
        "updated": atualizados,
        # No dry-run nada foi gravado: desconta o que seria atualizado
        "still_missing": restantes - atualizados if dry_run else restantes,
    }


def main() -> int:
    from src.infrastructure.db.mongo import get_db
    from src.infrastructure.logging_setup import configure_logging

    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--dry-run", action="store_true", help="só conta, não grava")
    args = parser.parse_args()

    if not settings.APIFY_TOKEN:
        print("APIFY_TOKEN não configurado")
        return 1
    res = backfill(
        get_db(), token=settings.APIFY_TOKEN, actor=settings.APIFY_ACTOR_ID, dry_run=args.dry_run
    )
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
