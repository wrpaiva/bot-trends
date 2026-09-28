# src/infrastructure/circuit_breaker.py
"""
Circuit breaker por serviço externo (TIE-37).

  closed ──N falhas consecutivas──▶ open ──cooldown──▶ half_open
     ▲                                                    │
     └──────────── sucesso ◀── tentativa de teste ────────┘  (falha → open de novo)

O estado mora no Redis (`breaker:<serviço>`): worker, beat e API enxergam o
mesmo circuito. Se o próprio store falhar, o breaker deixa passar (fail-open)
— a infraestrutura do breaker nunca pode ser o motivo de a coleta parar.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from src.domain.interfaces import LLMClient
from src.domain.trend_models import LLMResult
from src.infrastructure.llm.schema import LLMResponseError

logger = logging.getLogger(__name__)

SERVICES = ("mercadolivre", "apify", "llm")


class CircuitOpenError(RuntimeError):
    """Chamada recusada sem tentar: o circuito do serviço está aberto."""


class MemoryBreakerStore:
    """Para testes e processos isolados."""

    def __init__(self):
        self._dados: dict[str, dict[str, Any]] = {}

    def get(self, service: str) -> dict[str, Any]:
        return dict(self._dados.get(service, {}))

    def set(self, service: str, data: dict[str, Any]) -> None:
        self._dados[service] = dict(data)


class RedisBreakerStore:
    def __init__(self, client: Any):
        self.client = client

    def get(self, service: str) -> dict[str, Any]:
        bruto = self.client.hgetall(f"breaker:{service}") or {}
        dados = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in bruto.items()
        }
        return {
            "failures": int(dados.get("failures") or 0),
            "opened_at": float(dados["opened_at"]) if dados.get("opened_at") else None,
        }

    def set(self, service: str, data: dict[str, Any]) -> None:
        self.client.hset(
            f"breaker:{service}",
            mapping={
                "failures": data.get("failures", 0),
                # Redis não guarda None: string vazia = fechado
                "opened_at": "" if data.get("opened_at") is None else data["opened_at"],
            },
        )


class CircuitBreaker:
    def __init__(
        self,
        service: str,
        store: Any,
        *,
        threshold: int = 5,
        cooldown_s: float = 1800,
        clock: Callable[[], float] = time.time,
    ):
        self.service = service
        self.store = store
        self.threshold = max(1, int(threshold))
        self.cooldown_s = float(cooldown_s)
        self._clock = clock

    def _ler(self) -> dict[str, Any] | None:
        try:
            dados = self.store.get(self.service)
        except Exception as e:  # noqa: BLE001 — store fora = breaker transparente
            logger.warning("Breaker %s sem store (%s): deixando passar", self.service, e)
            return None
        return {"failures": int(dados.get("failures") or 0), "opened_at": dados.get("opened_at")}

    def _gravar(self, dados: dict[str, Any]) -> None:
        try:
            self.store.set(self.service, dados)
        except Exception as e:  # noqa: BLE001
            logger.warning("Breaker %s sem store (%s): estado não gravado", self.service, e)

    def _estado(self, dados: dict[str, Any] | None) -> str:
        if dados is None:
            return "unknown"
        if dados["opened_at"] is None:
            return "closed"
        if self._clock() - dados["opened_at"] >= self.cooldown_s:
            return "half_open"
        return "open"

    def state(self) -> str:
        return self._estado(self._ler())

    def allow(self) -> bool:
        return self._estado(self._ler()) != "open"

    def record_success(self) -> None:
        dados = self._ler()
        if dados is not None and (dados["failures"] or dados["opened_at"] is not None):
            if dados["opened_at"] is not None:
                logger.info("Circuito %s fechado: serviço respondeu", self.service)
            self._gravar({"failures": 0, "opened_at": None})

    def record_failure(self) -> None:
        dados = self._ler()
        if dados is None:
            return
        estado = self._estado(dados)
        falhas = dados["failures"] + 1
        # Meio-aberto: a tentativa de teste falhou → reabre já, cooldown novo
        if estado == "half_open" or falhas >= self.threshold:
            if estado != "open":
                logger.warning(
                    "Circuito %s ABERTO após %d falhas; nova tentativa em %.0f s",
                    self.service,
                    falhas,
                    self.cooldown_s,
                )
            self._gravar({"failures": falhas, "opened_at": self._clock()})
        else:
            self._gravar({"failures": falhas, "opened_at": dados["opened_at"]})

    def status(self) -> dict[str, Any]:
        dados = self._ler()
        estado = self._estado(dados)
        retry = None
        if estado == "open":
            retry = max(0, round(dados["opened_at"] + self.cooldown_s - self._clock()))
        return {
            "state": estado,
            "failures": dados["failures"] if dados else None,
            "retry_in_s": retry,
        }


class BreakerLLMClient(LLMClient):
    """
    LLM atrás do breaker. Circuito aberto → `CircuitOpenError` sem chamar, e o
    engine cai no score numérico. Resposta fora do schema não conta como
    indisponibilidade: o serviço respondeu.
    """

    def __init__(self, inner: LLMClient, breaker: CircuitBreaker):
        self.inner = inner
        self.breaker = breaker

    def analyze_trend(self, system: str, user: str) -> LLMResult:
        if not self.breaker.allow():
            st = self.breaker.status()
            raise CircuitOpenError(
                f"circuito do LLM aberto (nova tentativa em {st['retry_in_s']} s)"
            )
        try:
            res = self.inner.analyze_trend(system, user)
        except LLMResponseError:
            self.breaker.record_success()
            raise
        except Exception:
            self.breaker.record_failure()
            raise
        self.breaker.record_success()
        return res


# --- Fábrica (Redis + settings) ----------------------------------------------


def breaker_for(service: str) -> CircuitBreaker:
    import redis

    from src.infrastructure.config import settings

    client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
    return CircuitBreaker(
        service,
        RedisBreakerStore(client),
        threshold=settings.BREAKER_FAILURE_THRESHOLD,
        cooldown_s=settings.BREAKER_COOLDOWN_MIN * 60,
    )


def breakers_status() -> dict[str, dict[str, Any]]:
    return {s: breaker_for(s).status() for s in SERVICES}
