# src/infrastructure/health.py
"""
Checks de saúde usados pela readiness da API (TIE-38).

Cada check devolve `Check` e nunca levanta: um probe que explode vira 500, e
500 não diz ao orquestrador o que está fora.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import redis
from pymongo.database import Database

from src.infrastructure.db.migrations import get_migrations


@dataclass(frozen=True)
class Check:
    ok: bool
    detail: str
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "detail": self.detail, **self.extra}


def check_mongo(db: Database) -> Check:
    try:
        db.command("ping")
        return Check(True, "ping ok")
    except Exception as e:  # noqa: BLE001 — qualquer falha = indisponível
        return Check(False, f"{type(e).__name__}: {e}")


def check_redis(
    url: str,
    *,
    timeout_s: float = 2.0,
    factory: Callable[..., Any] = redis.Redis.from_url,
) -> Check:
    try:
        client = factory(url, socket_connect_timeout=timeout_s, socket_timeout=timeout_s)
        client.ping()
        return Check(True, "ping ok")
    except Exception as e:  # noqa: BLE001
        # Sem a URL na mensagem: ela carrega a senha do Redis
        return Check(False, type(e).__name__)


def check_migrations(db: Database) -> Check:
    esperadas = [m.meta.migration_id for m in get_migrations()]
    try:
        status = {
            d["migration_id"]: d.get("status")
            for d in db["migrations"].find({}, {"_id": 0, "migration_id": 1, "status": 1})
        }
    except Exception as e:  # noqa: BLE001
        return Check(False, f"{type(e).__name__}: {e}")

    pendentes = [m for m in esperadas if m not in status]
    falhas = [m for m in esperadas if status.get(m) == "failed"]
    if not pendentes and not falhas:
        return Check(True, f"{len(esperadas)} aplicadas")
    return Check(
        False,
        "rode as migrações (apps.migrate.main)",
        {"pending": pendentes, "failed": falhas},
    )
