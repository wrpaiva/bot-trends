# apps/migrate/main.py

from src.infrastructure.db.mongo import get_db
from src.infrastructure.db.migrations import get_migrations
from src.infrastructure.db.migrations.runner import MigrationRunner


def main() -> int:
    db = get_db()
    runner = MigrationRunner(db=db, migrations=get_migrations())
    runner.run()
    print("✅ Migrations aplicadas com sucesso.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())