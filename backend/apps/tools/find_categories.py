# apps/tools/find_categories.py

import os
import re
from src.infrastructure.db.mongo import get_db


def main() -> int:
    """
    Uso:
      Q=celular docker compose run --rm api python -m apps.tools.find_categories
    """
    query = os.environ.get("Q", "").strip()
    limit = int(os.environ.get("LIMIT", "50"))

    if not query:
        print("❌ Informe Q. Ex: Q=celular")
        return 1

    db = get_db()
    col = db["categories"]

    pattern = re.compile(query, re.IGNORECASE)

    cur = col.find(
        {"name": pattern, "ml_category_id": {"$exists": True}},
        {"_id": 0, "ml_category_id": 1, "name": 1, "enabled": 1},
    ).limit(limit)

    count = 0
    for c in cur:
        enabled = "✅" if c.get("enabled") else "❌"
        print(f"{enabled} {c['ml_category_id']}\t{c['name']}")
        count += 1

    if count == 0:
        print("Nenhum resultado.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())