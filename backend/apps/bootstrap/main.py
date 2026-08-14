# apps/bootstrap/main.py

import os
import re
import datetime as dt
from typing import List, Dict, Any

import httpx

from src.infrastructure.db.mongo import get_db
from src.infrastructure.db.migrations import get_migrations
from src.infrastructure.db.migrations.runner import MigrationRunner


def default_seed() -> List[Dict[str, Any]]:
    """
    Seed inicial (não depende de IDs do Mercado Livre).
    Você habilita as categorias depois via API/CLI.
    """
    return [
        {"key": "eletronicos", "name": "Eletrônicos", "keywords": ["fone", "smartwatch", "celular", "tablet"], "enabled": False},
        {"key": "informatica", "name": "Informática", "keywords": ["ssd", "memoria", "notebook", "teclado"], "enabled": False},
        {"key": "casa-cozinha", "name": "Casa & Cozinha", "keywords": ["airfryer", "panelas", "cafeteira"], "enabled": False},
        {"key": "beleza", "name": "Beleza", "keywords": ["perfume", "skincare", "hidratante"], "enabled": False},
        {"key": "moda", "name": "Moda", "keywords": ["tenis", "camiseta", "calca", "bolsa"], "enabled": False},
        {"key": "esporte", "name": "Esporte", "keywords": ["suplemento", "halter", "esteira"], "enabled": False},
    ]


def fetch_ml_categories(site_id: str) -> List[Dict[str, Any]]:
    """
    Busca as categorias do site do ML e transforma em docs para o Mongo.
    Fonte: /sites/{site_id}/categories
    """
    base_url = os.environ.get("ML_BASE_URL", "https://api.mercadolibre.com").rstrip("/")
    url = f"{base_url}/sites/{site_id}/categories"

    with httpx.Client(timeout=20) as client:
        resp = client.get(url)
        resp.raise_for_status()
        raw = resp.json()

    out: List[Dict[str, Any]] = []
    for item in raw:
        cid = item.get("id")
        name = item.get("name")
        if not cid or not name:
            continue

        key = f"ml-{cid}"
        out.append({
            "key": key,
            "name": name,
            "ml_category_id": cid,
            "keywords": [],
            "enabled": False,
        })

    return out


def seed_categories(db, categories: List[Dict[str, Any]]) -> int:
    """
    Seed idempotente:
      - upsert por key
      - mantém ml_category_id quando existir
      - mantém enabled/keywords do seed (mas você pode mudar depois via API)
    """
    col = db["categories"]
    col.create_index("key", unique=True, name="ux_categories_key")
    col.create_index("ml_category_id", name="ix_categories_ml_category_id")
    col.create_index("enabled", name="ix_categories_enabled")

    now = dt.datetime.utcnow()
    count = 0

    for c in categories:
        key = c["key"]
        update = {
            "$setOnInsert": {"created_at": now},
            "$set": {
                "key": key,
                "name": c.get("name", key),
                "keywords": c.get("keywords", []),
                "enabled": bool(c.get("enabled", False)),
                "updated_at": now,
            },
        }

        if c.get("ml_category_id"):
            update["$set"]["ml_category_id"] = c["ml_category_id"]

        col.update_one({"key": key}, update, upsert=True)
        count += 1

    return count


def auto_enable_by_keywords(db) -> int:
    """
    Opcional:
    - Se você rodou fetch ML, pode auto-habilitar categorias cujo nome combine com keywords do seed default.
    - Isso é heurístico e pode habilitar mais do que o desejado.
    """
    col = db["categories"]
    now = dt.datetime.utcnow()

    seeds = default_seed()
    enabled_total = 0

    for s in seeds:
        keywords = s.get("keywords", [])
        if not keywords:
            continue

        pattern = re.compile("|".join([re.escape(k) for k in keywords]), re.IGNORECASE)
        res = col.update_many(
            {"name": pattern, "ml_category_id": {"$exists": True}},
            {"$set": {"enabled": True, "enabled_at": now, "updated_at": now}},
        )
        enabled_total += res.modified_count

    return enabled_total


def main() -> int:
    db = get_db()

    # 1) Migrations primeiro (sempre)
    runner = MigrationRunner(db=db, migrations=get_migrations())
    runner.run()
    print("✅ migrations aplicadas")

    # 2) Seed
    fetch = os.environ.get("BOOTSTRAP_FETCH_ML_CATEGORIES", "false").lower() == "true"
    site_id = os.environ.get("ML_SITE_ID", "MLB")

    if fetch:
        categories = fetch_ml_categories(site_id)
        inserted = seed_categories(db, categories)
        print(f"✅ seed categorias do ML ({site_id}): {inserted}")
        if os.environ.get("BOOTSTRAP_AUTO_ENABLE", "false").lower() == "true":
            enabled = auto_enable_by_keywords(db)
            print(f"✅ auto-enable por keywords: {enabled}")
    else:
        categories = default_seed()
        inserted = seed_categories(db, categories)
        print(f"✅ seed default: {inserted}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())