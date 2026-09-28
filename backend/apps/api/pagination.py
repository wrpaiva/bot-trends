# apps/api/pagination.py
"""
Cursor opaco para as listagens (TIE-32).

O cliente não deve montar nem interpretar o cursor: é base64 de um JSON com o
tipo da listagem e a chave de ordenação do último item. Cursor adulterado, ou
de outra listagem, vira 400 — nunca 500 nem página errada.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from fastapi import HTTPException


def encode_cursor(kind: str, **fields: Any) -> str:
    bruto = json.dumps({"k": kind, **fields}, separators=(",", ":"), default=str)
    return base64.urlsafe_b64encode(bruto.encode()).decode().rstrip("=")


def decode_cursor(raw: str, kind: str, required: tuple[str, ...]) -> dict[str, Any]:
    try:
        preenchido = raw + "=" * (-len(raw) % 4)
        dados = json.loads(base64.urlsafe_b64decode(preenchido.encode()))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="cursor inválido") from None
    if (
        not isinstance(dados, dict)
        or dados.get("k") != kind
        or any(f not in dados for f in required)
    ):
        raise HTTPException(status_code=400, detail="cursor inválido") from None
    return dados
