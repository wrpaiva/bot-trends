# tests/test_imports_smoke.py
"""
Smoke de importação dos entrypoints.

Existe por causa da TIE-40: `db/__init__.py` estava com o conteúdo de
`db/migrations/__init__.py` e derrubava API, worker e migrations no mesmo
ModuleNotFoundError. Os testes de domínio não tocam o pacote `db`, então a
regressão passou calada desde o commit inicial. Este teste fecha essa porta.
"""

import importlib


def test_importa_entrypoint_da_api():
    assert importlib.import_module("apps.api.main").app is not None


def test_importa_entrypoint_do_worker():
    assert importlib.import_module("apps.worker.main").celery_app is not None


def test_importa_tasks_do_worker():
    importlib.import_module("apps.worker.tasks")
    importlib.import_module("apps.worker.tasks_trend")


def test_importa_entrypoint_das_migrations():
    importlib.import_module("apps.migrate.main")


def test_pacote_db_nao_reexporta_migrations():
    """`db/__init__.py` deve ser inerte: importar `db` não pode puxar migrations."""
    db = importlib.import_module("src.infrastructure.db")
    assert not hasattr(db, "get_migrations")


def test_get_migrations_devolve_as_quatro_versoes():
    from src.infrastructure.db.migrations import get_migrations

    migrations = get_migrations()
    assert [type(m).__name__ for m in migrations] == [
        "V001InitCollections",
        "V002AddCanonicalAndSchemaVersion",
        "V003AddProductUUID",
        "V004AddWindowFields",
    ]
