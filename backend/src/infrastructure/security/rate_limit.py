# src/infrastructure/security/rate_limit.py

import os
from slowapi import Limiter
from slowapi.util import get_remote_address

# Rate limiter global - usar Redis em produção
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100/minute"],
    storage_uri=os.getenv("REDIS_URL", "memory://"),
)
