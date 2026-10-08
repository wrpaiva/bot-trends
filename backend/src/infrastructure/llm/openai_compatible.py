# src/infrastructure/llm/openai_compatible.py

from __future__ import annotations

import logging

import httpx

from src.domain.interfaces import LLMClient
from src.domain.trend_models import LLMResult
from src.infrastructure.config import settings
from src.infrastructure.llm.schema import LLMResponseError, parse_llm_content
from src.infrastructure.logging_setup import http_log_hooks
from src.infrastructure.observability import safe_inc

logger = logging.getLogger(__name__)


class OpenAICompatibleLLMClient(LLMClient):
    """
    Cliente genérico "OpenAI-compatible".

    Env (lidas via `settings`, para não divergirem de config.py):
      LLM_BASE_URL (ex: https://api.openai.com/v1) ou gateway compatível
      LLM_API_KEY
      LLM_MODEL (default definido em config.py)
    """

    def __init__(
        self,
        timeout_s: int = 25,
        transport: httpx.BaseTransport | None = None,
        temperature: float = 0.2,
    ):
        base = settings.LLM_BASE_URL
        key = settings.LLM_API_KEY
        if not base or not key:
            raise RuntimeError("LLM_BASE_URL e LLM_API_KEY devem estar definidos.")

        self.base_url = base.rstrip("/")
        self.api_key = key
        self.model = settings.LLM_MODEL
        # 0.2 na análise; a avaliação do prompt (TIE-26) compara outras
        self.temperature = temperature
        self.client = httpx.Client(
            timeout=timeout_s,
            transport=transport,  # injetável nos testes
            event_hooks=http_log_hooks("llm"),
        )

        # O modelo efetivo tem que aparecer no log: a falha que esta classe já
        # teve foi silenciosa — o .env dizia LLM_MODEL_NAME, o código lia
        # LLM_MODEL, e o default entrava no lugar sem ninguém perceber.
        logger.info("LLM configurado: modelo=%s base_url=%s", self.model, self.base_url)

    def analyze_trend(self, system: str, user: str) -> LLMResult:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # Toda chamada real é contada pelo resultado (TIE-36): é o que custa
        try:
            r = self.client.post(url, headers=headers, json=payload)
        except httpx.HTTPError:
            safe_inc("trends_llm_calls_total", {"result": "erro_rede"})
            raise
        if r.is_error:
            safe_inc("trends_llm_calls_total", {"result": f"http_{r.status_code}"})
            r.raise_for_status()

        try:
            content = r.json()["choices"][0]["message"]["content"]
            # Validação e clamp dos campos (TIE-24)
            res = parse_llm_content(content)
        except (ValueError, KeyError, IndexError, TypeError, LLMResponseError) as e:
            safe_inc("trends_llm_calls_total", {"result": "resposta_invalida"})
            if isinstance(e, LLMResponseError):
                raise
            raise LLMResponseError(
                "envelope da resposta fora do formato chat/completions"
            ) from None
        safe_inc("trends_llm_calls_total", {"result": "ok"})
        return res
