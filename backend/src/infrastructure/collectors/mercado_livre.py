# src/infrastructure/collectors/mercado_livre.py

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any

import httpx

from src.infrastructure.collectors.http_policy import RateLimiter, retry_transient
from src.infrastructure.collectors.ml_auth import MercadoLivreAuth, MLAuthError
from src.infrastructure.config import settings
from src.infrastructure.logging_setup import http_log_hooks

logger = logging.getLogger(__name__)


class MercadoLivreCollector:
    """
    Coletor Mercado Livre baseado em /highlights (mais vendidos por categoria),
    enriquecido pelo catálogo: /products/{id} (nome, marca, link) e
    /products/{id}/items (ofertas, de onde sai o preço).

    Por que catálogo e não anúncio: com token de usuário comum (2026-10-06) o
    /highlights só devolve PRODUCT/USER_PRODUCT, e /items, /reviews/item,
    /sites/.../search e /user-products dão 403. Por isso `sold_quantity` e
    `reviews_total` saem None (sem fonte, não zero) e USER_PRODUCT fica de fora.

    Config (via `settings`):
      ML_BASE_URL (default: https://api.mercadolibre.com)
      ML_SITE_ID  (default: MLB)

    Autenticação (TIE-41): a API deixou de ser pública. Com `auth`, toda
    chamada leva `Authorization: Bearer`; um 401 renova o token uma vez por
    coleta e repete a chamada. Sem token utilizável (`MLAuthError`) a coleta
    para inteira e `auth_error` diz por quê — nenhuma categoria funcionaria.

    Features:
      - Retry só em erro transitório (429/5xx/timeout), respeitando Retry-After
      - Teto de requisições por minuto (ML_MAX_REQUESTS_PER_MINUTE)
      - `errors` conta as chamadas que falharam de vez, para a task não
        reportar sucesso numa coleta que não trouxe nada (TIE-17)
      - Context manager para gerenciamento de recursos
      - Logging estruturado de erros
    """

    def __init__(
        self,
        categories: list[str],
        *,
        timeout_s: int = 20,
        max_items_per_category: int = 20,
        max_requests_per_minute: int | None = None,
        transport: httpx.BaseTransport | None = None,
        auth: MercadoLivreAuth | None = None,
    ):
        self.auth = auth
        self.auth_error: str | None = None
        self._renovou = False
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
        """
        GET com teto de taxa e Bearer; levanta HTTPStatusError em resposta de
        erro e MLAuthError sem token utilizável.
        """
        token = self.auth.access_token() if self.auth else None
        r = self._get_com(url, token)
        # 401 com token: venceu ou foi revogado antes do previsto. Renova uma
        # vez por coleta — 401 com token recém-renovado não é problema de token.
        if r.status_code == 401 and self.auth is not None and not self._renovou:
            self._renovou = True
            r = self._get_com(url, self.auth.refresh(stale=token))
        r.raise_for_status()
        return r

    def _get_com(self, url: str, token: str | None) -> httpx.Response:
        self._limiter.wait()
        headers = {"Authorization": f"Bearer {token}"} if token else None
        return self.client.get(url, headers=headers)

    @retry_transient(attempts=3, min_s=1, max_s=10)
    def _get_highlights(self, category_id: str) -> dict[str, int]:
        """
        Produtos do catálogo em destaque (mais vendidos) de uma categoria:
        id → posição no ranking (1 = primeiro), na ordem do ranking. A posição
        alimenta o `rank_momentum` (TIE-16).
        """
        url = f"{self.base_url}/highlights/{self.site_id}/category/{category_id}"
        data = self._get(url).json()

        # Formato: {"content":[{"id":"MLB....","type":"PRODUCT","position":1}, ...]}
        # Sem `position`, vale a ordem da lista (que já é a do ranking)
        content = data.get("content", []) or []
        posicoes: dict[str, int] = {}
        for ordem, it in enumerate(content, start=1):
            if it.get("type") == "PRODUCT" and it.get("id"):
                posicoes[it["id"]] = int(it.get("position") or ordem)
            if len(posicoes) >= self.max_items_per_category:
                break

        return posicoes

    @retry_transient(attempts=3, min_s=0.5, max_s=5, multiplier=0.5)
    def _get_product(self, product_id: str) -> dict[str, Any]:
        """Detalhe do produto do catálogo."""
        data = self._get(f"{self.base_url}/products/{product_id}").json()
        if not isinstance(data, dict):
            raise ValueError("resposta do produto não é um objeto")
        return data

    @retry_transient(attempts=3, min_s=0.5, max_s=5, multiplier=0.5)
    def _get_offers(self, product_id: str) -> list[dict[str, Any]]:
        """Anúncios ativos que vendem o produto (preço, vendedor, item_id)."""
        data = self._get(f"{self.base_url}/products/{product_id}/items").json()
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            raise ValueError("resposta das ofertas sem lista de results")
        return results

    def _normalize_product(
        self, product: dict[str, Any], offers: list[dict[str, Any]] | None, category_id: str
    ) -> dict[str, Any]:
        """
        Normaliza um produto do catálogo para o formato interno. O preço é o do
        buy box quando existe, senão a menor oferta. `category` é a categoria do
        ranking (não a folha do anúncio): é o grupo em que a posição faz sentido.
        """
        # Brand costuma vir em attributes; não é garantido
        brand = None
        for attr in product.get("attributes") or []:
            if attr.get("id") == "BRAND" and attr.get("value_name"):
                brand = attr["value_name"]
                break

        price = currency = None
        buy_box = product.get("buy_box_winner") or {}
        if buy_box.get("price") is not None:
            price, currency = buy_box["price"], buy_box.get("currency_id")
        elif offers:
            com_preco = [o for o in offers if o.get("price") is not None]
            if com_preco:
                mais_barata = min(com_preco, key=lambda o: o["price"])
                price, currency = mais_barata["price"], mais_barata.get("currency_id")

        return {
            "source": "mercadolivre",
            "source_product_id": product.get("id"),
            "title": product.get("name"),
            "price": price,
            "currency": currency,
            "permalink": product.get("permalink"),
            "category": category_id,
            "brand": brand,
            "canonical_id": product.get("id"),
            "sold_quantity": None,
            "marketplace": {
                "offers": len(offers) if offers is not None else None,
                "domain_id": product.get("domain_id"),
            },
        }

    def collect(self) -> Iterable[dict[str, Any]]:
        """
        Yield de produtos normalizados.

        Formato de saída:
          {
            "source": "mercadolivre",
            "source_product_id": "MLB...",   # id do produto no catálogo
            "title": "...",
            "price": 123.0,                  # buy box ou menor oferta; None sem oferta
            "currency": "BRL",
            "permalink": "...",
            "category": "...",               # categoria do ranking
            "brand": "...",
            "canonical_id": "...",
            "sold_quantity": None,           # sem fonte com token comum
            "marketplace": {"offers": 2, "domain_id": "..."},
            "rank_position": 1               # posição no /highlights da categoria
          }
        """
        total_collected = 0

        for cat in self.categories:
            try:
                posicoes = self._get_highlights(cat)
                logger.info(f"Categoria {cat}: {len(posicoes)} produtos encontrados")
            except MLAuthError as e:
                self._sem_token(e)
                return
            except (httpx.HTTPError, json.JSONDecodeError) as e:
                logger.warning(f"Falha ao buscar highlights da categoria {cat}: {e}")
                self.errors += 1
                continue

            for product_id, posicao in posicoes.items():
                try:
                    product = self._get_product(product_id)
                except MLAuthError as e:
                    self._sem_token(e)
                    return
                except (httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
                    logger.warning("Falha ao buscar o produto %s: %s", product_id, e)
                    self.errors += 1
                    continue

                try:
                    offers = self._get_offers(product_id)
                except MLAuthError as e:
                    self._sem_token(e)
                    return
                except (httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
                    # Sem preço o produto ainda tem posição no ranking, mas a
                    # coleta fica parcialmente falha.
                    logger.warning("Falha ao buscar ofertas do produto %s: %s", product_id, e)
                    self.errors += 1
                    offers = None

                yield dict(
                    self._normalize_product(product, offers, cat),
                    rank_position=posicao,
                )
                total_collected += 1

        logger.info(f"Coleta finalizada: {total_collected} produtos, {self.errors} erros")

    def _sem_token(self, e: MLAuthError) -> None:
        self.errors += 1
        self.auth_error = str(e)
        logger.error("Coleta do ML interrompida: %s", e)

    def close(self) -> None:
        """Fecha o client HTTP de forma segura."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception as e:
                logger.debug(f"Erro ao fechar client: {e}")
            finally:
                self._client = None
