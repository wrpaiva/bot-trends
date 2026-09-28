# src/tests/test_circuit_breaker.py
"""
Circuit breaker por serviço externo (TIE-37).

Retry resolve falha transitória; o breaker resolve a prolongada. Hoje o LLM
devolve 429 em toda chamada: sem breaker, cada ciclo gasta 50 requisições
(e enche o log) para cair no fallback de qualquer jeito.
"""

import pytest

from src.domain.interfaces import LLMClient
from src.infrastructure.circuit_breaker import (
    BreakerLLMClient,
    CircuitBreaker,
    CircuitOpenError,
    MemoryBreakerStore,
    RedisBreakerStore,
)
from src.infrastructure.llm.schema import LLMResponseError


class _Relogio:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def relogio():
    return _Relogio()


def _breaker(relogio, store=None, threshold=3, cooldown_s=60):
    return CircuitBreaker(
        "llm",
        store or MemoryBreakerStore(),
        threshold=threshold,
        cooldown_s=cooldown_s,
        clock=relogio,
    )


# --- Máquina de estados -----------------------------------------------------


def test_fechado_deixa_passar(relogio):
    b = _breaker(relogio)
    assert b.allow() and b.state() == "closed"


def test_abre_apos_n_falhas_consecutivas(relogio):
    b = _breaker(relogio, threshold=3)
    b.record_failure()
    b.record_failure()
    assert b.allow()
    b.record_failure()
    assert not b.allow() and b.state() == "open"


def test_sucesso_zera_a_contagem(relogio):
    b = _breaker(relogio, threshold=3)
    for _ in range(2):
        b.record_failure()
    b.record_success()
    for _ in range(2):
        b.record_failure()
    assert b.state() == "closed"


def test_meio_aberto_apos_cooldown_e_fecha_no_sucesso(relogio):
    b = _breaker(relogio, threshold=1, cooldown_s=60)
    b.record_failure()
    relogio.t += 59
    assert not b.allow()
    relogio.t += 1
    assert b.state() == "half_open" and b.allow()
    b.record_success()
    assert b.state() == "closed"


def test_falha_no_meio_aberto_reabre_com_cooldown_novo(relogio):
    b = _breaker(relogio, threshold=3, cooldown_s=60)
    for _ in range(3):
        b.record_failure()
    relogio.t += 60
    assert b.allow()  # tentativa de teste
    b.record_failure()  # uma só falha basta para reabrir
    assert b.state() == "open"
    relogio.t += 30
    assert not b.allow()


def test_status_expoe_estado_e_reabertura(relogio):
    b = _breaker(relogio, threshold=1, cooldown_s=60)
    b.record_failure()
    st = b.status()
    assert st["state"] == "open" and st["failures"] == 1
    assert st["retry_in_s"] == 60


def test_store_quebrado_deixa_passar_e_nao_levanta(relogio):
    class _Quebrado:
        def get(self, service):
            raise ConnectionError("redis fora")

        def set(self, service, data):
            raise ConnectionError("redis fora")

    b = _breaker(relogio, store=_Quebrado())
    assert b.allow()
    b.record_failure()
    b.record_success()
    assert b.state() == "unknown"


# --- LLM ----------------------------------------------------------------------


class _LLM(LLMClient):
    def __init__(self, erro=None):
        self.chamadas = 0
        self.erro = erro

    def analyze_trend(self, system, user):
        self.chamadas += 1
        if self.erro:
            raise self.erro
        return "ok"


def test_llm_com_circuito_aberto_falha_rapido_sem_chamar(relogio):
    inner = _LLM(erro=RuntimeError("429 Too Many Requests"))
    llm = BreakerLLMClient(inner, _breaker(relogio, threshold=5))

    for _ in range(50):  # um ciclo de análise inteiro
        with pytest.raises((RuntimeError, CircuitOpenError)):
            llm.analyze_trend("s", "u")

    assert inner.chamadas == 5  # só até abrir; os outros 45 nem tentam


def test_resposta_invalida_do_llm_nao_conta_como_indisponibilidade(relogio):
    inner = _LLM(erro=LLMResponseError("fora do schema"))
    b = _breaker(relogio, threshold=2)
    llm = BreakerLLMClient(inner, b)
    for _ in range(5):
        with pytest.raises(LLMResponseError):
            llm.analyze_trend("s", "u")
    assert b.state() == "closed" and inner.chamadas == 5


def test_llm_ok_fecha_o_circuito(relogio):
    b = _breaker(relogio, threshold=1, cooldown_s=10)
    b.record_failure()
    relogio.t += 10
    assert BreakerLLMClient(_LLM(), b).analyze_trend("s", "u") == "ok"
    assert b.state() == "closed"


# --- Redis --------------------------------------------------------------------


class _RedisFalso:
    def __init__(self):
        self.h = {}

    def hgetall(self, k):
        return self.h.get(k, {})

    def hset(self, k, mapping):
        self.h[k] = {kk.encode(): str(v).encode() for kk, v in mapping.items()}


def test_redis_store_ida_e_volta(relogio):
    r = _RedisFalso()
    store = RedisBreakerStore(r)
    b = _breaker(relogio, store=store, threshold=1)
    b.record_failure()

    # Outro processo, mesmo Redis: enxerga o circuito aberto
    outro = _breaker(relogio, store=RedisBreakerStore(r), threshold=1)
    assert outro.state() == "open"
    assert list(r.h) == ["breaker:llm"]
