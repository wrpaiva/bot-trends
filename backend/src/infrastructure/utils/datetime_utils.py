# src/utils/datetime_utils.py

from __future__ import annotations
import datetime as dt


def utcnow() -> dt.datetime:
    return dt.datetime.utcnow()


def window(hours: int) -> tuple[dt.datetime, dt.datetime]:
    """
    Retorna (from, to) em UTC.
    """
    to = utcnow()
    frm = to - dt.timedelta(hours=int(hours))
    return frm, to


def iso(dt_value: dt.datetime) -> str:
    """
    ISO string (sem timezone explícito; assumimos UTC).
    """
    return dt_value.replace(microsecond=0).isoformat()