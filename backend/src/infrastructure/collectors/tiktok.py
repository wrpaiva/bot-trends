# src/infrastructure/collectors/tiktok.py

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


class TikTokApifyCollector:
    """
    Coletor TikTok via Apify Actor.

    Env:
      APIFY_TOKEN (obrigatório)
      APIFY_ACTOR_ID (default: clockworks~tiktok-scraper)

    Features:
      - Retry automático com backoff exponencial
      - Context manager para gerenciamento de recursos
      - Logging estruturado de erros
      - Polling com timeout para aguardar execução do actor
    """

    POLL_INTERVAL_S = 5
    MAX_WAIT_S = 300  # 5 minutos máximo de espera

    def __init__(
        self,
        hashtags: List[str],
        *,
        results_per_page: int = 50,
        timeout_s: int = 60,
        dataset_limit: int = 200,
    ):
        self.apify_token = os.environ.get("APIFY_TOKEN")
        if not self.apify_token:
            raise RuntimeError("APIFY_TOKEN não definido no ambiente.")

        self.actor_id = os.getenv("APIFY_ACTOR_ID", "clockworks~tiktok-scraper")
        self.hashtags = [h.strip().lstrip("#") for h in hashtags if h.strip()]
        self.results_per_page = int(results_per_page)
        self.dataset_limit = int(dataset_limit)
        self._timeout_s = timeout_s
        self._client: Optional[httpx.Client] = None

    @property
    def client(self) -> httpx.Client:
        """Lazy initialization do client HTTP."""
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout_s)
        return self._client

    def __enter__(self) -> "TikTokApifyCollector":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.close()
        return False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _run_actor(self) -> Dict[str, Any]:
        """Inicia execução do actor no Apify."""
        url = f"https://api.apify.com/v2/acts/{self.actor_id}/runs?token={self.apify_token}"
        payload = {
            "hashtags": self.hashtags,
            "resultsPerPage": self.results_per_page,
            "commentsPerPost": 0,
            "maxRepliesPerComment": 0,
        }
        r = self.client.post(url, json=payload)
        r.raise_for_status()
        return r.json()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _get_run_status(self, run_id: str) -> Dict[str, Any]:
        """Verifica status de uma execução do actor."""
        url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={self.apify_token}"
        r = self.client.get(url)
        r.raise_for_status()
        return r.json()

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
            except (httpx.HTTPError, httpx.TimeoutException) as e:
                logger.warning(f"Erro ao verificar status do run {run_id}: {e}")

            time.sleep(self.POLL_INTERVAL_S)
            elapsed += self.POLL_INTERVAL_S

        logger.warning(f"Timeout aguardando actor run {run_id}")
        return False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _get_dataset_items(self, dataset_id: str) -> List[Dict[str, Any]]:
        """Busca items do dataset gerado pelo actor."""
        url = (
            f"https://api.apify.com/v2/datasets/{dataset_id}/items"
            f"?token={self.apify_token}&clean=true&limit={self.dataset_limit}"
        )
        r = self.client.get(url)
        r.raise_for_status()
        return r.json()

    def _normalize_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Normaliza um item do TikTok para o formato interno."""
        stats = item.get("stats") or {}
        author = item.get("authorMeta") or {}

        views = stats.get("playCount") or stats.get("plays") or 0
        likes = stats.get("diggCount") or stats.get("likes") or 0
        comments = stats.get("commentCount") or stats.get("comments") or 0
        shares = stats.get("shareCount") or stats.get("shares") or 0

        permalink = item.get("webVideoUrl") or item.get("url")
        vid = item.get("id") or item.get("videoId") or permalink

        return {
            "source": "tiktok",
            "source_product_id": str(vid),
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
                "author": author.get("name") or author.get("nickName"),
            },
        }

    def collect(self) -> Iterable[Dict[str, Any]]:
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
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            logger.error(f"Falha ao iniciar actor TikTok: {e}")
            return

        run_data = run.get("data") or {}
        run_id = run_data.get("id")
        dataset_id = run_data.get("defaultDatasetId")

        if not dataset_id:
            logger.warning("Actor não retornou dataset_id")
            return

        # Aguarda execução finalizar (se necessário)
        if run_id and run_data.get("status") not in ("SUCCEEDED",):
            if not self._wait_for_run(run_id):
                logger.warning("Execução do actor não finalizou com sucesso")
                return

        try:
            items = self._get_dataset_items(dataset_id)
            logger.info(f"Dataset {dataset_id}: {len(items)} items encontrados")
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            logger.error(f"Falha ao buscar dataset {dataset_id}: {e}")
            return

        for item in items:
            yield self._normalize_item(item)
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