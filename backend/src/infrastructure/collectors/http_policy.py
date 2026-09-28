# src/infrastructure/collectors/http_policy.py
"""
Política HTTP comum aos collectors (TIE-17).

- Retry só em erro transitório: 429, 5xx, timeout e falha de rede. 4xx
  permanente (401, 403, 404...) falha na hora — retentar só gasta cota.
- `Retry-After` manda na espera quando o servidor o envia (com teto).
- `RateLimiter` espaça as requisições para não passar de N por minuto.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime

import httpx
from tenacity import (
    RetryCallState,
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)
from tenacity.wait import wait_base

logger = logging.getLogger(__name__)

# Um Retry-After absurdo não pode travar o worker; acima disso, desiste e loga.
RETRY_AFTER_MAX_S = 60.0


def is_transient(exc: BaseException) -> bool:
    """True se vale a pena tentar de novo."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    # TransportError cobre timeout, conexão recusada e erro de protocolo
    return isinstance(exc, httpx.TransportError)


def retry_after_s(exc: BaseException | None) -> float | None:
    """Segundos pedidos pelo header `Retry-After` (inteiro ou data HTTP), com teto."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    raw = exc.response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.UTC)
        seconds = (when - dt.datetime.now(dt.UTC)).total_seconds()
    return min(max(seconds, 0.0), RETRY_AFTER_MAX_S)


class wait_retry_after(wait_base):  # noqa: N801 — segue o estilo dos waits do tenacity
    """Usa o `Retry-After` da resposta; sem ele, cai no `fallback`."""

    def __init__(self, fallback: wait_base):
        self.fallback = fallback

    def __call__(self, retry_state: RetryCallState) -> float:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        seconds = retry_after_s(exc)
        return seconds if seconds is not None else self.fallback(retry_state)


def retry_transient(
    *, attempts: int = 3, min_s: float = 1, max_s: float = 10, multiplier: float = 1
):
    """Decorator de retry para chamadas HTTP dos collectors."""
    return retry(
        stop=stop_after_attempt(attempts),
        wait=wait_retry_after(wait_exponential(multiplier=multiplier, min=min_s, max=max_s)),
        retry=retry_if_exception(is_transient),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )


class RateLimiter:
    """
    Espaça as requisições para não passar de `max_per_minute`.
    `None` ou `0` desliga o limite.
    """

    def __init__(
        self,
        max_per_minute: int | None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] | None = None,
    ):
        self.interval = 60.0 / max_per_minute if max_per_minute else 0.0
        self._clock = clock
        self._sleep = sleep
        self._next: float | None = None

    def wait(self) -> None:
        if not self.interval:
            return
        now = self._clock()
        if self._next is not None and now < self._next:
            # time.sleep resolvido na hora: testes que o substituem continuam valendo
            (self._sleep or time.sleep)(self._next - now)
            now = self._next
        self._next = now + self.interval
