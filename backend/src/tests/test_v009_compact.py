# src/tests/test_v009_compact.py
"""Migração v009 (TIE-47): sequência de insights repetidos vira um documento."""

import datetime as dt

import mongomock

from src.infrastructure.db.migrations.versions.v009_compact_repeated_insights import (
    V009CompactRepeatedInsights,
)
from src.infrastructure.utils.datetime_utils import utcnow

# Sem microssegundos: o Mongo guarda milissegundos
T0 = utcnow().replace(microsecond=0) - dt.timedelta(hours=10)


def _ins(pid, h, score, cls="SUBINDO", **kw):
    return {
        "product_id": pid,
        "ts": T0 + dt.timedelta(hours=h),
        "final_score": score,
        "numeric_score": score,
        "llm_score": score,
        "trend_classification": cls,
        "risk_level": "MEDIO",
        "window_hours": 72,
        "sources": ["tiktok"],
        "debug": {},
        "analysis": f"texto {h}",
        **kw,
    }


def _db():
    db = mongomock.MongoClient(tz_aware=True).db
    db["trend_insights"].insert_many(
        [
            # p1: 3 iguais, muda, 2 iguais, volta ao primeiro (A A A B B A)
            _ins("p1", 0, 50),
            _ins("p1", 1, 50),
            _ins("p1", 2, 50),
            _ins("p1", 3, 60),
            _ins("p1", 4, 60),
            _ins("p1", 5, 50),
            # p2: mesmos scores de p1 mas outro produto, e fallback no meio
            _ins("p2", 0, 50),
            _ins("p2", 1, 50, debug={"llm_error": "429"}),
            _ins("p2", 2, 50, debug={"llm_error": "429"}),
        ]
    )
    return db


def _serie(db, pid):
    return [
        (d["final_score"], d["ts"], d.get("first_ts"), d.get("confirmations"))
        for d in db["trend_insights"].find({"product_id": pid}).sort("ts", 1)
    ]


def _t(h):
    return T0 + dt.timedelta(hours=h)


def test_compacta_cada_sequencia_mantendo_o_ultimo():
    db = _db()
    V009CompactRepeatedInsights().up(db)

    assert _serie(db, "p1") == [
        (50, _t(2), _t(0), 2),
        (60, _t(4), _t(3), 1),
        (50, _t(5), None, None),  # sozinho: não é tocado
    ]
    assert _serie(db, "p2") == [
        (50, _t(0), None, None),
        (50, _t(2), _t(1), 1),
    ]
    # O que fica é o mais novo da sequência, com o texto dele
    assert db["trend_insights"].find_one({"product_id": "p1", "ts": _t(2)})["analysis"] == "texto 2"


def test_rodar_de_novo_nao_muda_nada():
    db = _db()
    V009CompactRepeatedInsights().up(db)
    antes = {p: _serie(db, p) for p in ("p1", "p2")}
    V009CompactRepeatedInsights().up(db)
    assert {p: _serie(db, p) for p in ("p1", "p2")} == antes


def test_soma_confirmacoes_de_insight_ja_confirmado():
    db = mongomock.MongoClient(tz_aware=True).db
    db["trend_insights"].insert_many(
        [
            _ins("p1", 0, 50, first_ts=T0 - dt.timedelta(hours=5), confirmations=4),
            _ins("p1", 1, 50),
        ]
    )
    V009CompactRepeatedInsights().up(db)
    assert _serie(db, "p1") == [(50, _t(1), T0 - dt.timedelta(hours=5), 5)]
