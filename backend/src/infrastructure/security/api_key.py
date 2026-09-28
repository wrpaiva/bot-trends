# src/infrastructure/security/api_key.py

import secrets

from fastapi import Header, HTTPException

from src.infrastructure.config import settings


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    # Lido a cada requisição (não no import), para refletir o `settings` vigente
    expected = settings.API_KEY
    if not expected:
        # Inalcançável com a API subindo pelo lifespan, que exige API_KEY
        raise HTTPException(status_code=500, detail="API_KEY não configurada")
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")
