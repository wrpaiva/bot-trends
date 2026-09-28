# src/infrastructure/utils/datetime_utils.py
"""
Datas do sistema: sempre timezone-aware, sempre em UTC (TIE-10).

Nunca use `datetime.utcnow()` (devolve naive e é deprecado no 3.12; o ruff
barra via DTZ003). O Mongo persiste em UTC e o client é criado com
`tz_aware=True`, então o que se lê do banco também volta aware em UTC.
"""

from __future__ import annotations

import datetime as dt


def utcnow() -> dt.datetime:
    """Agora, aware em UTC."""
    return dt.datetime.now(dt.UTC)


def ensure_utc(value: dt.datetime | None) -> dt.datetime | None:
    """
    Normaliza para aware em UTC. Naive é tratado como UTC — é o que o driver
    gravou em documentos antigos, antes do `tz_aware`.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def window(hours: int) -> tuple[dt.datetime, dt.datetime]:
    """
    Retorna (from, to) aware em UTC.
    """
    to = utcnow()
    frm = to - dt.timedelta(hours=int(hours))
    return frm, to


def iso(dt_value: dt.datetime) -> str:
    """
    ISO string em UTC, com o offset explícito (`+00:00`).
    """
    return ensure_utc(dt_value).replace(microsecond=0).isoformat()
