# apps/bootstrap/main.py

import re
from typing import Any

import httpx

from src.infrastructure.config import settings
from src.infrastructure.db.migrations import get_migrations
from src.infrastructure.db.migrations.runner import MigrationRunner
from src.infrastructure.db.mongo import get_db
from src.infrastructure.utils.datetime_utils import utcnow


def default_seed() -> list[dict[str, Any]]:
    """
    Seed inicial: categorias de primeiro nível do Mercado Livre Brasil.

    Precisa trazer `ml_category_id`: sem ele a categoria some de `GET /categories`,
    `POST /categories/enable` não a encontra e `collect_ml` nunca a coleta (TIE-20).

    Tudo nasce desabilitado — habilite via `POST /categories/enable` com os IDs.
    Os IDs não puderam ser conferidos na API (`/sites/MLB/categories` exige token
    desde 2026-09, ver TIE-41); confira-os quando a autenticação existir.
    """
    return [
        {
            "key": "eletronicos",
            "name": "Eletrônicos, Áudio e Vídeo",
            "ml_category_id": "MLB1000",
            "keywords": ["fone", "caixa de som", "tv", "smartwatch"],
            "enabled": False,
        },
        {
            "key": "celulares",
            "name": "Celulares e Telefones",
            "ml_category_id": "MLB1051",
            "keywords": ["celular", "smartphone", "carregador", "capinha"],
            "enabled": False,
        },
        {
            "key": "informatica",
            "name": "Informática",
            "ml_category_id": "MLB1648",
            "keywords": ["ssd", "memoria", "notebook", "teclado"],
            "enabled": False,
        },
        {
            "key": "casa-cozinha",
            "name": "Casa, Móveis e Decoração",
            "ml_category_id": "MLB1574",
            "keywords": ["airfryer", "panelas", "cafeteira"],
            "enabled": False,
        },
        {
            "key": "beleza",
            "name": "Beleza e Cuidado Pessoal",
            "ml_category_id": "MLB1246",
            "keywords": ["perfume", "skincare", "hidratante"],
            "enabled": False,
        },
        {
            "key": "moda",
            "name": "Calçados, Roupas e Bolsas",
            "ml_category_id": "MLB1430",
            "keywords": ["tenis", "camiseta", "calca", "bolsa"],
            "enabled": False,
        },
        {
            "key": "esporte",
            "name": "Esportes e Fitness",
            "ml_category_id": "MLB1276",
            "keywords": ["suplemento", "halter", "esteira"],
            "enabled": False,
        },
    ]


def fetch_ml_categories(site_id: str) -> list[dict[str, Any]]:
    """
    Busca as categorias do site do ML e transforma em docs para o Mongo.
    Fonte: /sites/{site_id}/categories
    """
    base_url = settings.ML_BASE_URL.rstrip("/")
    url = f"{base_url}/sites/{site_id}/categories"

    with httpx.Client(timeout=20) as client:
        resp = client.get(url)
        resp.raise_for_status()
        raw = resp.json()

    out: list[dict[str, Any]] = []
    for item in raw:
        cid = item.get("id")
        name = item.get("name")
        if not cid or not name:
            continue

        key = f"ml-{cid}"
        out.append(
            {
                "key": key,
                "name": name,
                "ml_category_id": cid,
                "keywords": [],
                "enabled": False,
            }
        )

    return out


def seed_categories(db, categories: list[dict[str, Any]]) -> int:
    """
    Seed idempotente.

    - Casa por `ml_category_id` quando existe, senão por `key`: a árvore do ML
      (key `ml-<id>`) atualiza a categoria do seed default em vez de duplicá-la.
    - `name`/`ml_category_id` são atualizados; `key`, `enabled` e `keywords` só
      no insert — rodar o bootstrap de novo não desfaz o que o usuário habilitou.
    """
    col = db["categories"]
    col.create_index("key", unique=True, name="ux_categories_key")
    col.create_index("ml_category_id", name="ix_categories_ml_category_id")
    col.create_index("enabled", name="ix_categories_enabled")

    now = utcnow()
    count = 0

    for c in categories:
        key = c["key"]
        ml_id = c.get("ml_category_id")
        filt = {"ml_category_id": ml_id} if ml_id else {"key": key}

        update = {
            "$setOnInsert": {
                "created_at": now,
                "key": key,
                "keywords": c.get("keywords", []),
                "enabled": bool(c.get("enabled", False)),
            },
            "$set": {"name": c.get("name", key), "updated_at": now},
        }
        if ml_id:
            update["$set"]["ml_category_id"] = ml_id

        col.update_one(filt, update, upsert=True)
        count += 1

    return count


def auto_enable_by_keywords(db) -> int:
    """
    Opcional:
    - Se você rodou fetch ML, pode auto-habilitar categorias cujo nome combine com keywords do seed default.
    - Isso é heurístico e pode habilitar mais do que o desejado.
    """
    col = db["categories"]
    now = utcnow()

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
    site_id = settings.ML_SITE_ID

    if settings.BOOTSTRAP_FETCH_ML_CATEGORIES:
        categories = fetch_ml_categories(site_id)
        inserted = seed_categories(db, categories)
        print(f"✅ seed categorias do ML ({site_id}): {inserted}")
        if settings.BOOTSTRAP_AUTO_ENABLE:
            enabled = auto_enable_by_keywords(db)
            print(f"✅ auto-enable por keywords: {enabled}")
    else:
        categories = default_seed()
        inserted = seed_categories(db, categories)
        print(f"✅ seed default: {inserted}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
