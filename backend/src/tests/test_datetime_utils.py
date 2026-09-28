# src/tests/test_datetime_utils.py
"""
Datas timezone-aware em UTC (TIE-10).

`datetime.utcnow()` devolve datetime naive; comparar naive com aware levanta
`TypeError`, e misturar os dois em janelas temporais dá resultado errado sem
aviso. O sistema inteiro usa `utcnow()` deste módulo, e o Mongo devolve aware.
"""

import datetime as dt

import mongomock
import pytest

from src.infrastructure.utils.datetime_utils import ensure_utc, iso, utcnow, window

BRT = dt.timezone(dt.timedelta(hours=-3))


def test_utcnow_eh_aware_em_utc():
    agora = utcnow()
    assert agora.tzinfo is not None
    assert agora.utcoffset() == dt.timedelta(0)


def test_window_devolve_intervalo_aware_com_as_horas_pedidas():
    frm, to = window(24)
    assert frm.tzinfo is not None and to.tzinfo is not None
    assert to - frm == dt.timedelta(hours=24)


def test_ensure_utc_assume_utc_para_naive():
    naive = dt.datetime(2026, 9, 27, 12, 0)
    assert ensure_utc(naive) == dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.UTC)


def test_ensure_utc_converte_outro_fuso():
    em_brt = dt.datetime(2026, 9, 27, 9, 0, tzinfo=BRT)
    convertido = ensure_utc(em_brt)
    assert convertido.utcoffset() == dt.timedelta(0)
    assert convertido.hour == 12


def test_ensure_utc_aceita_none():
    assert ensure_utc(None) is None


def test_naive_e_aware_nao_se_comparam_direto():
    # É exatamente o bug que ensure_utc evita
    with pytest.raises(TypeError):
        _ = dt.datetime(2026, 9, 27) < utcnow()


@pytest.mark.parametrize(
    ("ts", "dentro"),
    [
        (dt.datetime(2026, 9, 27, 11, 0), True),  # naive legado, lido como UTC
        (dt.datetime(2026, 9, 27, 8, 30, tzinfo=BRT), True),  # 11:30 UTC
        (dt.datetime(2026, 9, 27, 9, 30), False),  # antes do início
        (dt.datetime(2026, 9, 27, 12, 30, tzinfo=dt.UTC), False),  # depois do fim
    ],
)
def test_comparacao_de_janela_com_datas_de_origens_diferentes(ts, dentro):
    frm = dt.datetime(2026, 9, 27, 10, 0, tzinfo=dt.UTC)
    to = dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.UTC)
    assert (frm <= ensure_utc(ts) <= to) is dentro


def test_iso_explicita_o_utc():
    assert iso(dt.datetime(2026, 9, 27, 12, 0, 5, 999, tzinfo=dt.UTC)) == (
        "2026-09-27T12:00:05+00:00"
    )


def test_mongo_tz_aware_filtra_janela_e_devolve_aware():
    db = mongomock.MongoClient(tz_aware=True).db
    frm, to = window(1)
    db["metrics"].insert_many(
        [
            {"id": "velho", "ts": frm - dt.timedelta(minutes=5)},
            {"id": "novo", "ts": to - dt.timedelta(minutes=5)},
        ]
    )

    docs = list(db["metrics"].find({"ts": {"$gte": frm}}))

    assert [d["id"] for d in docs] == ["novo"]
    assert docs[0]["ts"].tzinfo is not None
    assert frm <= docs[0]["ts"] <= to
