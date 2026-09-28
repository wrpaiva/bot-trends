# apps/api/main.py

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from apps.api.routes import router
from src.infrastructure import health
from src.infrastructure.circuit_breaker import breakers_status
from src.infrastructure.config import settings
from src.infrastructure.db.mongo import close_client, get_db
from src.infrastructure.logging_setup import configure_logging
from src.infrastructure.security.api_key import require_api_key
from src.infrastructure.security.rate_limit import limiter

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Falha no startup, não na primeira requisição (TIE-8)
    faltando = settings.missing("API_KEY")
    if faltando:
        raise RuntimeError(f"Configuração obrigatória ausente: {', '.join(faltando)}")
    yield
    # O cliente é criado sob demanda pelo get_db(); aqui só garantimos o fechamento.
    close_client()


# =========================================
# 🚀 FastAPI App
# =========================================
app = FastAPI(
    title="Trends Intelligence API",
    version="1.0.0",
    description="Motor híbrido de análise de tendências (Marketplace + Social)",
    lifespan=lifespan,
)

# Configurar limiter na app
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# =========================================
# 🔐 CORS
# =========================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
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
    return {"service": "Trends Intelligence API", "status": "running"}


@app.get("/health")
@limiter.exempt
def health_public():
    """
    Liveness: o processo responde. Sem dependência nenhuma (nem Redis do rate
    limit) — reiniciar a API não conserta Mongo fora do ar.
    """
    return {"status": "ok"}


@app.get("/health/ready")
@limiter.exempt
def health_ready():
    """
    Readiness (TIE-38). Mongo fora → 503: a API não serve nada útil.
    Redis fora ou migração pendente/falha → 200 `degraded`: a leitura funciona,
    e derrubar aqui travaria a primeira subida (o README migra depois do `up`).
    """
    try:
        db = get_db()
        mongo = health.check_mongo(db)
    except Exception as e:  # noqa: BLE001 — config quebrada também é "indisponível"
        db, mongo = None, health.Check(False, type(e).__name__)

    checks = {
        "mongo": mongo,
        "redis": health.check_redis(settings.REDIS_URL),
        "migrations": (
            health.check_migrations(db) if mongo.ok else health.Check(False, "sem mongo")
        ),
    }

    # Circuitos dos serviços externos (TIE-37): aberto = degradado, não fora
    try:
        breakers = breakers_status()
    except Exception as e:  # noqa: BLE001
        breakers = {"erro": {"state": "unknown", "detail": type(e).__name__}}
    algum_aberto = any(b.get("state") == "open" for b in breakers.values())

    if not mongo.ok:
        status, code = "unavailable", 503
    elif all(c.ok for c in checks.values()) and not algum_aberto:
        status, code = "ready", 200
    else:
        status, code = "degraded", 200

    return JSONResponse(
        status_code=code,
        content={
            "status": status,
            "checks": {k: c.as_dict() for k, c in checks.items()},
            "breakers": breakers,
        },
    )


# Rotas protegidas (com API key)
app.include_router(router, dependencies=[Depends(require_api_key)])
