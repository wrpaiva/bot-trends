# src/infrastructure/telegram/notifier.py

from __future__ import annotations

import contextlib

import httpx

from src.infrastructure.config import settings
from src.infrastructure.logging_setup import http_log_hooks


class TelegramError(RuntimeError):
    """Falha ao enviar para o Telegram, com mensagem sem o token do bot."""


class TelegramNotifier:
    """
    Config (via `settings`):
      TELEGRAM_BOT_TOKEN
      TELEGRAM_CHAT_ID
    """

    def __init__(
        self,
        timeout_s: int = 15,
        transport: httpx.BaseTransport | None = None,
        chat_id: str | None = None,
    ):
        token = settings.TELEGRAM_BOT_TOKEN
        # chat_id explícito: canal de saúde do sistema (TIE-39), separado do de tendências
        chat_id = chat_id or settings.TELEGRAM_CHAT_ID
        if not token or not chat_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID devem estar definidos.")

        self.token = token
        self.chat_id = chat_id
        self.client = httpx.Client(
            timeout=timeout_s,
            transport=transport,  # injetável nos testes
            # O path tem o token; http_log_hooks o mascara
            event_hooks=http_log_hooks("telegram"),
        )

    def send(self, text: str, *, parse_mode: str = "HTML", disable_preview: bool = True) -> None:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_preview,
        }
        # O token faz parte da URL (exigência do Telegram) e o httpx põe a URL
        # na mensagem de erro. Reempacota sem ela e sem encadear a original,
        # senão o traceback do worker imprime o token.
        try:
            r = self.client.post(url, json=payload)
        except httpx.HTTPError as e:
            raise TelegramError(
                f"Falha de rede ao enviar ao Telegram: {type(e).__name__}"
            ) from None
        if r.is_error:
            raise TelegramError(f"Telegram respondeu HTTP {r.status_code}") from None

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.client.close()
