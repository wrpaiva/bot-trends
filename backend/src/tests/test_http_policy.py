# src/tests/test_http_policy.py
"""
Política HTTP dos collectors (TIE-17): retry só em erro transitório, respeito ao
`Retry-After` e teto de requisições por minuto.
"""

import datetime as dt
from email.utils import format_datetime

import httpx
import pytest

from src.infrastructure.collectors.http_policy import (
    RETRY_AFTER_MAX_S,
    RateLimiter,
    is_transient,
    retry_after_s,
    retry_transient,
)


def _status_error(code: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.exemplo.com/x")
    response = httpx.Response(code, headers=headers, request=request)
    return httpx.HTTPStatusError(f"HTTP {code}", request=request, response=response)


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_status_transitorio_eh_retentado(code):
    assert is_transient(_status_error(code))


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_4xx_permanente_nao_eh_retentado(code):
    assert not is_transient(_status_error(code))


def test_erro_de_rede_e_timeout_sao_transitorios():
    request = httpx.Request("GET", "https://api.exemplo.com/x")
    assert is_transient(httpx.ConnectTimeout("t", request=request))
    assert is_transient(httpx.ReadTimeout("t", request=request))
    assert is_transient(httpx.ConnectError("c", request=request))


def test_erro_que_nao_eh_http_nao_eh_retentado():
    assert not is_transient(ValueError("json inválido"))


def test_retry_after_em_segundos():
    assert retry_after_s(_status_error(429, {"Retry-After": "7"})) == 7.0


def test_retry_after_tem_teto():
    assert retry_after_s(_status_error(429, {"Retry-After": "99999"})) == RETRY_AFTER_MAX_S


def test_retry_after_em_data_http():
    quando = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=30)
    segundos = retry_after_s(_status_error(503, {"Retry-After": format_datetime(quando, True)}))
    assert 25 <= segundos <= 30


def test_retry_after_ausente_ou_invalido():
    assert retry_after_s(_status_error(429)) is None
    assert retry_after_s(_status_error(429, {"Retry-After": "tomorrow"})) is None
    assert retry_after_s(ValueError()) is None


def _chamada_que_falha(*erros: Exception):
    """Devolve (fn decorada, lista de chamadas, lista de esperas)."""
    chamadas: list[int] = []
    esperas: list[float] = []
    fila = list(erros)

    @retry_transient(attempts=3, min_s=1, max_s=10)
    def fn():
        chamadas.append(1)
        if fila:
            raise fila.pop(0)
        return "ok"

    fn.retry.sleep = esperas.append
    return fn, chamadas, esperas


def test_respeita_retry_after_antes_de_tentar_de_novo():
    fn, chamadas, esperas = _chamada_que_falha(_status_error(429, {"Retry-After": "3"}))
    assert fn() == "ok"
    assert len(chamadas) == 2
    assert esperas == [3.0]


def test_sem_retry_after_usa_backoff_exponencial():
    fn, chamadas, esperas = _chamada_que_falha(_status_error(503), _status_error(503))
    assert fn() == "ok"
    assert len(chamadas) == 3
    assert esperas[0] >= 1 and esperas[1] >= esperas[0]


def test_4xx_permanente_falha_na_primeira_tentativa():
    fn, chamadas, esperas = _chamada_que_falha(_status_error(404))
    with pytest.raises(httpx.HTTPStatusError):
        fn()
    assert len(chamadas) == 1
    assert esperas == []


def test_desiste_depois_de_esgotar_tentativas():
    fn, chamadas, _ = _chamada_que_falha(*[_status_error(500)] * 5)
    with pytest.raises(httpx.HTTPStatusError):
        fn()
    assert len(chamadas) == 3


class _Relogio:
    def __init__(self):
        self.agora = 0.0
        self.esperas: list[float] = []

    def __call__(self) -> float:
        return self.agora

    def sleep(self, s: float) -> None:
        self.esperas.append(s)
        self.agora += s


def test_rate_limiter_espaca_requisicoes():
    relogio = _Relogio()
    limiter = RateLimiter(60, clock=relogio, sleep=relogio.sleep)
    for _ in range(3):
        limiter.wait()
    assert relogio.esperas == [1.0, 1.0]


def test_rate_limiter_nao_espera_se_o_intervalo_ja_passou():
    relogio = _Relogio()
    limiter = RateLimiter(30, clock=relogio, sleep=relogio.sleep)
    limiter.wait()
    relogio.agora += 5
    limiter.wait()
    assert relogio.esperas == []


@pytest.mark.parametrize("teto", [None, 0])
def test_rate_limiter_desligado(teto):
    relogio = _Relogio()
    limiter = RateLimiter(teto, clock=relogio, sleep=relogio.sleep)
    for _ in range(5):
        limiter.wait()
    assert relogio.esperas == []
