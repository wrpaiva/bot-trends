# src/infrastructure/llm/cache.py
"""Cache de análises do LLM no Redis (TIE-25)."""

from __future__ import annotations

import dataclasses
import json
import logging
from typing import Any

from src.domain.interfaces import LLMCache
from src.domain.trend_models import LLMResult

logger = logging.getLogger(__name__)


class RedisLLMCache(LLMCache):
    """
    Chave: `llm:<modelo>:<hash das métricas>`. O modelo entra no prefixo para
    que trocar `LLM_MODEL` não reaproveite análise de outro modelo.
    Valor corrompido vira miss; erro de conexão sobe e o engine trata como miss.
    """

    def __init__(self, client: Any, *, model: str, ttl_s: int):
        self.client = client
        self.prefix = f"llm:{model}:"
        self.ttl_s = int(ttl_s)

    def get(self, key: str) -> LLMResult | None:
        bruto = self.client.get(self.prefix + key)
        if bruto is None:
            return None
        try:
            return LLMResult(**json.loads(bruto))
        except (ValueError, TypeError) as e:
            logger.warning("Entrada inválida no cache do LLM, ignorada: %s", e)
            return None

    def set(self, key: str, value: LLMResult) -> None:
        self.client.setex(self.prefix + key, self.ttl_s, json.dumps(dataclasses.asdict(value)))
