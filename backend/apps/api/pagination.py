# apps/api/pagination.py
"""
Cursor opaco para as listagens (TIE-32).

O cliente não deve montar nem interpretar o cursor: é base64 de um JSON com o
tipo da listagem e a chave de ordenação do último item. Cursor adulterado, ou
de outra listagem, vira 400 — nunca 500 nem página errada. Cada campo tem tipo
conferido: o valor vai direto para `$lt`/`$gt`, e número fora da faixa do Mongo
dava 500 (`OverflowError`), tipo errado dava 200 com a página errada (TIE-50).
"""

from __future__ import annotations

import base64
import binascii
import json
import math
from typing import Any

from fastapi import HTTPException


def encode_cursor(kind: str, **fields: Any) -> str:
    bruto = json.dumps({"k": kind, **fields}, separators=(",", ":"), default=str)
    return base64.urlsafe_b64encode(bruto.encode()).decode().rstrip("=")


# Teto de número do cursor: inteiro exato em double; acima disso o Mongo
# recusa (int64) ou a comparação perde precisão
_MAIOR_NUMERO = 2**53


def _tipo_ok(valor: Any, tipo: type) -> bool:
    if tipo is float:
        # bool é int em Python, e não é score
        return (
            isinstance(valor, int | float)
            and not isinstance(valor, bool)
            and abs(valor) <= _MAIOR_NUMERO
            and math.isfinite(valor)
        )
    if tipo is not bool and isinstance(valor, bool):
        # bool é int em Python: True passaria como int
        return False
    return isinstance(valor, tipo)


def decode_cursor(raw: str, kind: str, campos: dict[str, type]) -> dict[str, Any]:
    """`campos`: nome → tipo esperado (`float` aceita int e float finitos)."""
    try:
        preenchido = raw + "=" * (-len(raw) % 4)
        dados = json.loads(base64.urlsafe_b64decode(preenchido.encode()))
    except (binascii.Error, ValueError, UnicodeDecodeError, RecursionError):
        raise HTTPException(status_code=400, detail="cursor inválido") from None
    if (
        not isinstance(dados, dict)
        or dados.get("k") != kind
        or any(not _tipo_ok(dados.get(f), t) for f, t in campos.items())
    ):
        raise HTTPException(status_code=400, detail="cursor inválido")
    return dados
