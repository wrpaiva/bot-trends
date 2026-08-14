# src/infrastructure/telegram/notifier.py

from __future__ import annotations
import os
import httpx


class TelegramNotifier:
    """
    Env:
      TELEGRAM_BOT_TOKEN
      TELEGRAM_CHAT_ID
    """

    def __init__(self, timeout_s: int = 15):
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID devem estar definidos.")

        self.token = token
        self.chat_id = chat_id
        self.client = httpx.Client(timeout=timeout_s)

    def send(self, text: str, *, parse_mode: str = "HTML", disable_preview: bool = True) -> None:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_preview,
        }
        r = self.client.post(url, json=payload)
        r.raise_for_status()

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass