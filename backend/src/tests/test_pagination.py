# src/tests/test_pagination.py
"""
Cursor de paginação: cursor malformado é 400, nunca 500 (TIE-56).
"""

import base64

import pytest
from fastapi import HTTPException

from apps.api.pagination import decode_cursor, encode_cursor


def test_cursor_valido_faz_ida_e_volta():
    raw = encode_cursor("ranking", s=42.5, p="abc")
    dados = decode_cursor(raw, "ranking", {"s": float, "p": str})
    assert dados["s"] == 42.5 and dados["p"] == "abc"


def test_cursor_json_muito_aninhado_vira_400():
    """JSON com 100 mil `[` estoura a recursão do json.loads (RecursionError)."""
    raw = base64.urlsafe_b64encode(b"[" * 100000).decode().rstrip("=")
    with pytest.raises(HTTPException) as erro:
        decode_cursor(raw, "ranking", {"s": float, "p": str})
    assert erro.value.status_code == 400


# TIE-57: bool é subclasse de int, então `true` não pode passar como campo int.
@pytest.mark.parametrize("valor", [True, False])
def test_cursor_com_bool_em_campo_int_vira_400(valor):
    raw = encode_cursor("ranking", n=valor)
    with pytest.raises(HTTPException) as erro:
        decode_cursor(raw, "ranking", {"n": int})
    assert erro.value.status_code == 400


def test_cursor_com_int_legitimo_continua_valido():
    raw = encode_cursor("ranking", n=7)
    assert decode_cursor(raw, "ranking", {"n": int})["n"] == 7


def test_cursor_com_bool_em_campo_float_vira_400():
    raw = encode_cursor("ranking", s=True, p="abc")
    with pytest.raises(HTTPException) as erro:
        decode_cursor(raw, "ranking", {"s": float, "p": str})
    assert erro.value.status_code == 400
