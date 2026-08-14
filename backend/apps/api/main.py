# apps/api/main.py

import os
from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from src.infrastructure.security.api_key import require_api_key
from src.infrastructure.security.rate_limit import limiter
from apps.api.routes import router

# =========================================
# 🚀 FastAPI App
# =========================================
app = FastAPI(
    title="Trends Intelligence API",
    version="1.0.0",
    description="Motor híbrido de análise de tendências (Marketplace + Social)"
)

# Configurar limiter na app
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# =========================================
# 🔐 CORS
# =========================================
allowed_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in allowed_origins if o.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)

# =========================================
# 📍 Routes
# =========================================
# Rotas públicas (sem API key)
@app.get("/")
def root():
    return {
        "service": "Trends Intelligence API",
        "status": "running"
    }

@app.get("/health")
def health_public():
    """Health check público para load balancers."""
    return {"status": "ok"}

# Rotas protegidas (com API key)
app.include_router(router, dependencies=[Depends(require_api_key)])