# src/infrastructure/llm/openai_compatible.py

from __future__ import annotations

import json
import logging

import httpx

from src.domain.interfaces import LLMClient
from src.domain.trend_models import LLMResult
from src.infrastructure.config import settings

logger = logging.getLogger(__name__)


class OpenAICompatibleLLMClient(LLMClient):
    """
    Cliente genérico "OpenAI-compatible".

    Env (lidas via `settings`, para não divergirem de config.py):
      LLM_BASE_URL (ex: https://api.openai.com/v1) ou gateway compatível
      LLM_API_KEY
      LLM_MODEL (default definido em config.py)
    """

    def __init__(self, timeout_s: int = 25):
        base = settings.LLM_BASE_URL
        key = settings.LLM_API_KEY
        if not base or not key:
            raise RuntimeError("LLM_BASE_URL e LLM_API_KEY devem estar definidos.")

        self.base_url = base.rstrip("/")
        self.api_key = key
        self.model = settings.LLM_MODEL
        self.client = httpx.Client(timeout=timeout_s)

        # O modelo efetivo tem que aparecer no log: a falha que esta classe já
        # teve foi silenciosa — o .env dizia LLM_MODEL_NAME, o código lia
        # LLM_MODEL, e o default entrava no lugar sem ninguém perceber.
        logger.info("LLM configurado: modelo=%s base_url=%s", self.model, self.base_url)

    def analyze_trend(self, system: str, user: str) -> LLMResult:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "temperature": 0.2,
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

        r = self.client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()

        content = data["choices"][0]["message"]["content"]
        obj = json.loads(content)

        return LLMResult(
            trend_classification=obj["trend_classification"],
            potential_score_0_100=float(obj["potential_score_0_100"]),
            risk_level=obj["risk_level"],
            analysis=str(obj["analysis"]),
            recommendation=str(obj["recommendation"]),
            confidence_0_1=float(obj.get("confidence_0_1", 0.6)),
        )
