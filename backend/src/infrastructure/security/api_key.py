import os
from fastapi import Header, HTTPException

API_KEY = os.getenv("API_KEY")

def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    if not API_KEY:
        # em prod, você NÃO deve permitir isso
        raise HTTPException(status_code=500, detail="API_KEY não configurada")
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")