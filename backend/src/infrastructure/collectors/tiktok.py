# src/infrastructure/collectors/tiktok.py

from __future__ import annotations

import datetime as dt
import logging
import re
import time
from collections.abc import Iterable
from typing import Any

import httpx

from src.infrastructure.collectors.http_policy import RateLimiter, is_transient, retry_transient
from src.infrastructure.config import settings
from src.infrastructure.logging_setup import http_log_hooks

logger = logging.getLogger(__name__)

# Id numérico do vídeo dentro da URL: .../@autor/video/7075778590062988546
_VIDEO_ID_NA_URL = re.compile(r"/video/(\d+)")


def parse_published_at(item: dict[str, Any]) -> dt.datetime | None:
    """Data de publicação do vídeo, aware em UTC (createTimeISO ou epoch)."""
    iso = item.get("createTimeISO")
    if iso:
        try:
            return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(dt.UTC)
        except ValueError:
            pass
    epoch = item.get("createTime")
    if isinstance(epoch, int | float) and epoch > 0:
        return dt.datetime.fromtimestamp(epoch, dt.UTC)
    return None


class TikTokApifyCollector:
    """
    Coletor TikTok via Apify Actor.

    Config (via `settings`):
      APIFY_TOKEN (obrigatório)
      APIFY_ACTOR_ID (default: clockworks~tiktok-scraper)

    O token vai no header `Authorization`, nunca na query string: a URL aparece
    nas mensagens de `httpx.HTTPStatusError`, que são logadas (TIE-3).

    Features:
      - Retry só em erro transitório (429/5xx/timeout), respeitando Retry-After
      - Teto de requisições por minuto (APIFY_MAX_REQUESTS_PER_MINUTE)
      - `errors` conta as etapas que falharam de vez (TIE-17)
      - Context manager para gerenciamento de recursos
      - Logging estruturado de erros
      - Polling com timeout para aguardar execução do actor
    """

    BASE_URL = "https://api.apify.com/v2"
    POLL_INTERVAL_S = 5
    MAX_WAIT_S = 300  # 5 minutos máximo de espera

    def __init__(
        self,
        hashtags: list[str],
        *,
        results_per_page: int = 50,
        timeout_s: int = 60,
        dataset_limit: int = 200,
        max_requests_per_minute: int | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.apify_token = settings.APIFY_TOKEN
        if not self.apify_token:
            raise RuntimeError("APIFY_TOKEN não definido no ambiente.")

        self.actor_id = settings.APIFY_ACTOR_ID
        self.hashtags = [h.strip().lstrip("#") for h in hashtags if h.strip()]
        self.results_per_page = int(results_per_page)
        self.dataset_limit = int(dataset_limit)
        if max_requests_per_minute is None:
            max_requests_per_minute = settings.APIFY_MAX_REQUESTS_PER_MINUTE
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
                base_url=self.BASE_URL,
                headers={"Authorization": f"Bearer {self.apify_token}"},
                timeout=self._timeout_s,
                transport=self._transport,
                event_hooks=http_log_hooks("apify"),
            )
        return self._client

    def __enter__(self) -> TikTokApifyCollector:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.close()
        return False

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Requisição com teto de taxa; levanta HTTPStatusError em resposta de erro."""
        self._limiter.wait()
        r = self.client.request(method, url, **kwargs)
        r.raise_for_status()
        return r

    @retry_transient(attempts=3, min_s=2, max_s=15)
    def _run_actor(self) -> dict[str, Any]:
        """Inicia execução do actor no Apify."""
        payload = {
            "hashtags": self.hashtags,
            "resultsPerPage": self.results_per_page,
            "commentsPerPost": 0,
            "maxRepliesPerComment": 0,
        }
        return self._request("POST", f"/acts/{self.actor_id}/runs", json=payload).json()

    @retry_transient(attempts=3, min_s=1, max_s=10, multiplier=0.5)
    def _get_run_status(self, run_id: str) -> dict[str, Any]:
        """Verifica status de uma execução do actor."""
        return self._request("GET", f"/actor-runs/{run_id}").json()

    def _wait_for_run(self, run_id: str) -> bool:
        """Aguarda execução do actor finalizar (polling)."""
        elapsed = 0
        while elapsed < self.MAX_WAIT_S:
            try:
                status_data = self._get_run_status(run_id)
                status = status_data.get("data", {}).get("status")

                if status == "SUCCEEDED":
                    logger.info(f"Actor run {run_id} finalizado com sucesso")
                    return True
                elif status in ("FAILED", "ABORTED", "TIMED-OUT"):
                    logger.warning(f"Actor run {run_id} falhou com status: {status}")
                    return False

                logger.debug(f"Actor run {run_id} status: {status}, aguardando...")
            except httpx.HTTPError as e:
                logger.warning(f"Erro ao verificar status do run {run_id}: {e}")
                if not is_transient(e):
                    # 4xx permanente não melhora esperando; aborta em vez de gastar 5 min
                    return False

            time.sleep(self.POLL_INTERVAL_S)
            elapsed += self.POLL_INTERVAL_S

        logger.warning(f"Timeout aguardando actor run {run_id}")
        return False

    @retry_transient(attempts=3, min_s=1, max_s=10, multiplier=0.5)
    def _get_dataset_items(self, dataset_id: str) -> list[dict[str, Any]]:
        """Busca items do dataset gerado pelo actor."""
        return self._request(
            "GET",
            f"/datasets/{dataset_id}/items",
            params={"clean": "true", "limit": self.dataset_limit},
        ).json()

    def _normalize_item(self, item: dict[str, Any]) -> dict[str, Any] | None:
        """Normaliza um item do TikTok para o formato interno."""
        # O actor atual devolve os contadores no primeiro nível do item; versões
        # antigas os aninhavam em `stats`. Aceita os dois formatos.
        stats = item.get("stats") or {}
        author = item.get("authorMeta") or {}

        def _count(*keys: str) -> Any:
            for src in (item, stats):
                for key in keys:
                    if src.get(key):
                        return src[key]
            return 0

        views = _count("playCount", "plays")
        likes = _count("diggCount", "likes")
        comments = _count("commentCount", "comments")
        shares = _count("shareCount", "shares")

        permalink = item.get("webVideoUrl") or item.get("url")
        # Sempre o id numérico: usar a URL como id fazia o mesmo vídeo entrar
        # duas vezes (TIE-19), uma com id e outra com URL.
        vid = item.get("id") or item.get("videoId")
        if not vid:
            m = _VIDEO_ID_NA_URL.search(permalink or "")
            if not m:
                return None
            vid = m.group(1)

        return {
            "source": "tiktok",
            "source_product_id": str(vid),
            # Motor de tendência: ritmo = views / idade do vídeo
            "published_at": parse_published_at(item),
            "has_shop_product": bool(item.get("hasTikTokShopProduct")),
            "language": item.get("textLanguage"),
            "title": (item.get("text") or item.get("desc") or "")[:200],
            "permalink": permalink,
            "category": None,
            "brand": None,
            "canonical_id": str(vid),
            "social": {
                "views": int(views or 0),
                "likes": int(likes or 0),
                "comments": int(comments or 0),
                "shares": int(shares or 0),
                "saves": int(item.get("collectCount") or 0),
                "author": author.get("name") or author.get("nickName"),
            },
        }

    def collect(self) -> Iterable[dict[str, Any]]:
        """
        Yield normalizado de vídeos do TikTok.

        Formato de saída:
          {
            "source": "tiktok",
            "source_product_id": "<videoId>",
            "title": "<desc>",
            "permalink": "<url>",
            "social": { views, likes, comments, shares, author }
          }
        """
        total_collected = 0

        try:
            logger.info(f"Iniciando coleta TikTok para hashtags: {self.hashtags}")
            run = self._run_actor()
        except httpx.HTTPError as e:
            logger.error(f"Falha ao iniciar actor TikTok: {e}")
            self.errors += 1
            return

        run_data = run.get("data") or {}
        run_id = run_data.get("id")
        dataset_id = run_data.get("defaultDatasetId")

        if not dataset_id:
            logger.warning("Actor não retornou dataset_id")
            self.errors += 1
            return

        # Aguarda execução finalizar (se necessário)
        if (
            run_id
            and run_data.get("status") not in ("SUCCEEDED",)
            and not self._wait_for_run(run_id)
        ):
            logger.warning("Execução do actor não finalizou com sucesso")
            self.errors += 1
            return

        try:
            items = self._get_dataset_items(dataset_id)
            logger.info(f"Dataset {dataset_id}: {len(items)} items encontrados")
        except httpx.HTTPError as e:
            logger.error(f"Falha ao buscar dataset {dataset_id}: {e}")
            self.errors += 1
            return

        for item in items:
            normalizado = self._normalize_item(item)
            if normalizado is None:
                logger.warning("Item do TikTok sem id de vídeo, ignorado")
                continue
            yield normalizado
            total_collected += 1

        logger.info(f"Coleta TikTok finalizada: {total_collected} items")

    def close(self) -> None:
        """Fecha o client HTTP de forma segura."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception as e:
                logger.debug(f"Erro ao fechar client: {e}")
            finally:
                self._client = None
