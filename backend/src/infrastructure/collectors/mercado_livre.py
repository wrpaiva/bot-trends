# src/infrastructure/collectors/mercado_livre.py

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any

import httpx

from src.infrastructure.collectors.http_policy import RateLimiter, retry_transient
from src.infrastructure.config import settings
from src.infrastructure.logging_setup import http_log_hooks

logger = logging.getLogger(__name__)


class MercadoLivreCollector:
    """
    Coletor Mercado Livre baseado em /highlights (mais vendidos por categoria),
    depois consulta /items em batch para enriquecer.

    Config (via `settings`):
      ML_BASE_URL (default: https://api.mercadolibre.com)
      ML_SITE_ID  (default: MLB)

    Features:
      - Retry só em erro transitório (429/5xx/timeout), respeitando Retry-After
      - Teto de requisições por minuto (ML_MAX_REQUESTS_PER_MINUTE)
      - `errors` conta as chamadas que falharam de vez, para a task não
        reportar sucesso numa coleta que não trouxe nada (TIE-17)
      - Batch de items (até 20 por request)
      - Context manager para gerenciamento de recursos
      - Logging estruturado de erros
    """

    BATCH_SIZE = 20  # Limite da API do ML

    def __init__(
        self,
        categories: list[str],
        *,
        timeout_s: int = 20,
        max_items_per_category: int = 20,
        max_requests_per_minute: int | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = settings.ML_BASE_URL.rstrip("/")
        self.site_id = settings.ML_SITE_ID
        self.categories = [c for c in categories if c]
        self.max_items_per_category = int(max_items_per_category)
        if max_requests_per_minute is None:
            max_requests_per_minute = settings.ML_MAX_REQUESTS_PER_MINUTE
        self._limiter = RateLimiter(max_requests_per_minute)
        self.errors = 0
        self._timeout_s = timeout_s
        self._transport = transport  # injetável nos testes
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        """Lazy initialization do client HTTP."""
        if self._client is None:
            self._client = httpx.Client(
                timeout=self._timeout_s,
                headers={"User-Agent": "bot-trends/1.0 (pymongo; celery; fastapi)"},
                transport=self._transport,
                event_hooks=http_log_hooks("mercadolivre"),
            )
        return self._client

    def __enter__(self) -> MercadoLivreCollector:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.close()
        return False

    def _get(self, url: str) -> httpx.Response:
        """GET com teto de taxa; levanta HTTPStatusError em resposta de erro."""
        self._limiter.wait()
        r = self.client.get(url)
        r.raise_for_status()
        return r

    @retry_transient(attempts=3, min_s=1, max_s=10)
    def _get_highlights_item_ids(self, category_id: str) -> list[str]:
        """Busca IDs dos produtos em destaque de uma categoria."""
        url = f"{self.base_url}/highlights/{self.site_id}/category/{category_id}"
        data = self._get(url).json()

        # Formato típico: {"content":[{"id":"MLB....","type":"ITEM"}, ...]}
        content = data.get("content", []) or []
        ids: list[str] = []
        for it in content:
            if it.get("type") == "ITEM" and it.get("id"):
                ids.append(it["id"])

        return ids[: self.max_items_per_category]

    @retry_transient(attempts=3, min_s=0.5, max_s=5, multiplier=0.5)
    def _get_items_batch(self, item_ids: list[str]) -> list[dict[str, Any]]:
        """
        Busca múltiplos items em uma única request (até 20).
        Retorna lista de items válidos.
        """
        if not item_ids:
            return []

        ids_param = ",".join(item_ids[: self.BATCH_SIZE])
        url = f"{self.base_url}/items?ids={ids_param}"
        r = self._get(url)

        results: list[dict[str, Any]] = []
        for item_response in r.json():
            if item_response.get("code") == 200:
                body = item_response.get("body")
                if body:
                    results.append(body)
            else:
                item_id = item_response.get("body", {}).get("id", "unknown")
                logger.debug(f"Item {item_id} retornou código {item_response.get('code')}")

        return results

    def _normalize_item(self, item: dict[str, Any]) -> dict[str, Any]:
        """Normaliza um item do ML para o formato interno."""
        # Brand costuma vir em attributes; não é garantido
        brand = None
        for attr in item.get("attributes") or []:
            if attr.get("id") == "BRAND" and attr.get("value_name"):
                brand = attr["value_name"]
                break

        return {
            "source": "mercadolivre",
            "source_product_id": item.get("id"),
            "title": item.get("title"),
            "price": item.get("price"),
            "currency": item.get("currency_id"),
            "permalink": item.get("permalink"),
            "category": item.get("category_id"),
            "brand": brand,
            "canonical_id": item.get("id"),
            "marketplace": {
                "sold_quantity": item.get("sold_quantity"),
                "available_quantity": item.get("available_quantity"),
                "condition": item.get("condition"),
            },
        }

    def collect(self) -> Iterable[dict[str, Any]]:
        """
        Yield de itens normalizados.

        Utiliza batch requests para melhor performance.
        Formato de saída:
          {
            "source": "mercadolivre",
            "source_product_id": "...",
            "title": "...",
            "price": 123.0,
            "currency": "BRL",
            "permalink": "...",
            "category": "...",
            "brand": "...",
            "canonical_id": "...",
            "marketplace": {...}
          }
        """
        total_collected = 0

        for cat in self.categories:
            try:
                item_ids = self._get_highlights_item_ids(cat)
                logger.info(f"Categoria {cat}: {len(item_ids)} items encontrados")
            except (httpx.HTTPError, json.JSONDecodeError) as e:
                logger.warning(f"Falha ao buscar highlights da categoria {cat}: {e}")
                self.errors += 1
                continue

            # Processa em batches de 20 (limite da API)
            for i in range(0, len(item_ids), self.BATCH_SIZE):
                batch_ids = item_ids[i : i + self.BATCH_SIZE]

                try:
                    items = self._get_items_batch(batch_ids)
                except (httpx.HTTPError, json.JSONDecodeError) as e:
                    logger.warning(f"Falha ao buscar batch de items: {e}")
                    self.errors += 1
                    continue

                for item in items:
                    yield self._normalize_item(item)
                    total_collected += 1

        logger.info(f"Coleta finalizada: {total_collected} items, {self.errors} erros")

    def close(self) -> None:
        """Fecha o client HTTP de forma segura."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception as e:
                logger.debug(f"Erro ao fechar client: {e}")
            finally:
                self._client = None
