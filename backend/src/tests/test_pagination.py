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
