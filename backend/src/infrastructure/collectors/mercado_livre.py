# src/infrastructure/collectors/mercado_livre.py

from __future__ import annotations
import logging
import os
import time
from typing import Dict, Any, Iterable, List, Optional

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

logger = logging.getLogger(__name__)


class MercadoLivreCollector:
    """
    Coletor Mercado Livre baseado em /highlights (mais vendidos por categoria),
    depois consulta /items em batch para enriquecer.

    Env:
      ML_BASE_URL (default: https://api.mercadolibre.com)
      ML_SITE_ID  (default: MLB)

    Features:
      - Retry automático com backoff exponencial
      - Batch de items (até 20 por request)
      - Context manager para gerenciamento de recursos
      - Logging estruturado de erros
    """

    BATCH_SIZE = 20  # Limite da API do ML

    def __init__(
        self,
        categories: List[str],
        *,
        timeout_s: int = 20,
        sleep_s: float = 0.05,
        max_items_per_category: int = 20,
    ):
        self.base_url = os.getenv("ML_BASE_URL", "https://api.mercadolibre.com").rstrip("/")
        self.site_id = os.getenv("ML_SITE_ID", "MLB")
        self.categories = [c for c in categories if c]
        self.sleep_s = max(0.0, float(sleep_s))
        self.max_items_per_category = int(max_items_per_category)
        self._timeout_s = timeout_s
        self._client: Optional[httpx.Client] = None

    @property
    def client(self) -> httpx.Client:
        """Lazy initialization do client HTTP."""
        if self._client is None:
            self._client = httpx.Client(
                timeout=self._timeout_s,
                headers={"User-Agent": "bot-trends/1.0 (pymongo; celery; fastapi)"},
            )
        return self._client

    def __enter__(self) -> "MercadoLivreCollector":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.close()
        return False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _get_highlights_item_ids(self, category_id: str) -> List[str]:
        """Busca IDs dos produtos em destaque de uma categoria."""
        url = f"{self.base_url}/highlights/{self.site_id}/category/{category_id}"
        r = self.client.get(url)
        r.raise_for_status()
        data = r.json()

        # Formato típico: {"content":[{"id":"MLB....","type":"ITEM"}, ...]}
        content = data.get("content", []) or []
        ids: List[str] = []
        for it in content:
            if it.get("type") == "ITEM" and it.get("id"):
                ids.append(it["id"])

        return ids[: self.max_items_per_category]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _get_items_batch(self, item_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Busca múltiplos items em uma única request (até 20).
        Retorna lista de items válidos.
        """
        if not item_ids:
            return []

        ids_param = ",".join(item_ids[: self.BATCH_SIZE])
        url = f"{self.base_url}/items?ids={ids_param}"
        r = self.client.get(url)
        r.raise_for_status()

        results: List[Dict[str, Any]] = []
        for item_response in r.json():
            if item_response.get("code") == 200:
                body = item_response.get("body")
                if body:
                    results.append(body)
            else:
                item_id = item_response.get("body", {}).get("id", "unknown")
                logger.debug(f"Item {item_id} retornou código {item_response.get('code')}")

        return results

    def _normalize_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
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

    def collect(self) -> Iterable[Dict[str, Any]]:
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
        total_errors = 0

        for cat in self.categories:
            try:
                item_ids = self._get_highlights_item_ids(cat)
                logger.info(f"Categoria {cat}: {len(item_ids)} items encontrados")
            except (httpx.HTTPError, httpx.TimeoutException) as e:
                logger.warning(f"Falha ao buscar highlights da categoria {cat}: {e}")
                total_errors += 1
                continue

            # Processa em batches de 20 (limite da API)
            for i in range(0, len(item_ids), self.BATCH_SIZE):
                batch_ids = item_ids[i : i + self.BATCH_SIZE]

                try:
                    items = self._get_items_batch(batch_ids)
                except (httpx.HTTPError, httpx.TimeoutException) as e:
                    logger.warning(f"Falha ao buscar batch de items: {e}")
                    total_errors += 1
                    continue

                for item in items:
                    yield self._normalize_item(item)
                    total_collected += 1

                if self.sleep_s:
                    time.sleep(self.sleep_s)

        logger.info(f"Coleta finalizada: {total_collected} items, {total_errors} erros")

    def close(self) -> None:
        """Fecha o client HTTP de forma segura."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception as e:
                logger.debug(f"Erro ao fechar client: {e}")
            finally:
                self._client = None