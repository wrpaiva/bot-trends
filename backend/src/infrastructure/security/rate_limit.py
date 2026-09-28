# src/infrastructure/security/rate_limit.py

from slowapi import Limiter
from slowapi.util import get_remote_address

from src.infrastructure.config import settings

# Rate limiter global, com estado no Redis (compartilhado entre workers do uvicorn).
# A conexão só é aberta no primeiro hit de rota limitada.
#
# in_memory_fallback_enabled: com o Redis fora, o contador passa para a memória
# do processo (limite por worker do uvicorn, não global) em vez de toda rota
# devolver 500 — inclusive /health (TIE-38). A readiness mostra o Redis como
# degradado. Não use swallow_errors: no slowapi 0.1.10 ele quebra o
# SlowAPIMiddleware (`State has no attribute view_rate_limit`).
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100/minute"],
    storage_uri=settings.REDIS_URL,
    in_memory_fallback_enabled=True,
)
