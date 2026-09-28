# src/tests/test_logging.py
"""
Logging estruturado (TIE-13): JSON em API e worker, tasks com início/fim/
duração/contagens, chamadas externas com status e latência — e nenhuma
credencial na saída, verificado aqui explicitamente.
"""

import io
import json
import logging

import httpx
import pytest

from src.infrastructure import logging_setup as ls
from src.infrastructure.config import settings

SEGREDOS = {
    "APIFY_TOKEN": "apify_api_SEGREDO123",
    "TELEGRAM_BOT_TOKEN": "123456:TELEGRAM-SEGREDO",
    "LLM_API_KEY": "sk-LLM-SEGREDO",
    "API_KEY": "api-key-SEGREDO",
}


@pytest.fixture(autouse=True)
def _segredos(monkeypatch):
    for k, v in SEGREDOS.items():
        monkeypatch.setattr(settings, k, v)
    monkeypatch.setattr(settings, "MONGO_URI", "mongodb://trends:senha-mongo@mongo:27017")
    monkeypatch.setattr(settings, "REDIS_URL", "redis://:senha-redis@redis:6379/0")


def _formata(msg, *args, exc_info=None, **extra) -> dict:
    record = logging.LogRecord("teste", logging.WARNING, __file__, 1, msg, args, exc_info)
    record.__dict__.update(extra)
    return json.loads(ls.JsonFormatter().format(record))


def _saida(record_fn) -> str:
    """Formata como o handler real faria, devolvendo o texto bruto."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(ls.JsonFormatter())
    log = logging.getLogger("teste.saida")
    log.handlers[:] = [handler]
    log.propagate = False
    log.setLevel(logging.DEBUG)
    record_fn(log)
    return buf.getvalue()


# --- Formato ----------------------------------------------------------------


def test_json_com_campos_basicos_e_extras():
    doc = _formata("coletou %d itens", 3, fonte="tiktok", inserted=3)
    assert doc["level"] == "WARNING"
    assert doc["logger"] == "teste"
    assert doc["msg"] == "coletou 3 itens"
    assert doc["fonte"] == "tiktok" and doc["inserted"] == 3
    assert doc["ts"].endswith("+00:00")


def test_excecao_vai_no_campo_exc():
    try:
        raise ValueError("deu ruim")
    except ValueError:
        import sys

        doc = _formata("falhou", exc_info=sys.exc_info())
    assert "ValueError: deu ruim" in doc["exc"]


def test_extra_nao_serializavel_nao_quebra_o_log():
    doc = _formata("x", objeto=object())
    assert doc["objeto"].startswith("<object")


# --- Redação de credenciais -------------------------------------------------


@pytest.mark.parametrize("nome", list(SEGREDOS))
def test_valor_de_credencial_do_settings_nunca_sai(nome):
    texto = _saida(lambda log: log.error("usando %s", SEGREDOS[nome]))
    assert SEGREDOS[nome] not in texto
    assert "***" in texto


def test_senhas_de_uri_nunca_saem():
    texto = _saida(
        lambda log: log.error(
            "conexões", extra={"mongo": settings.MONGO_URI, "redis": settings.REDIS_URL}
        )
    )
    assert "senha-mongo" not in texto and "senha-redis" not in texto
    assert "mongodb://trends:***@mongo" in texto


def test_padroes_genericos_de_token_sao_mascarados():
    texto = _saida(
        lambda log: log.error(
            "GET https://api.telegram.org/bot999:outro-token/sendMessage "
            "Authorization: Bearer abc.def.ghi"
        )
    )
    assert "outro-token" not in texto and "abc.def.ghi" not in texto


def test_credencial_dentro_de_traceback_tambem_e_mascarada():
    def _loga(log):
        try:
            raise RuntimeError(f"falhou com {SEGREDOS['APIFY_TOKEN']}")
        except RuntimeError:
            log.exception("erro")

    assert SEGREDOS["APIFY_TOKEN"] not in _saida(_loga)


# --- Configuração -----------------------------------------------------------


def test_configure_logging_eh_idempotente():
    root = logging.getLogger()
    antes = list(root.handlers)
    try:
        ls.configure_logging()
        ls.configure_logging()
        nossos = [h for h in root.handlers if getattr(h, ls.HANDLER_MARK, False)]
        assert len(nossos) == 1
        assert isinstance(nossos[0].formatter, ls.JsonFormatter)
    finally:
        root.handlers[:] = antes


def test_formato_texto_opcional(monkeypatch):
    monkeypatch.setattr(settings, "LOG_FORMAT", "text")
    root = logging.getLogger()
    antes = list(root.handlers)
    try:
        ls.configure_logging()
        (h,) = (h for h in root.handlers if getattr(h, ls.HANDLER_MARK, False))
        assert not isinstance(h.formatter, ls.JsonFormatter)
        assert isinstance(h.formatter, ls.RedactingFormatter)
    finally:
        root.handlers[:] = antes


def test_worker_assume_o_logging_do_celery():
    from celery.signals import setup_logging

    import apps.worker.main  # noqa: F401  (conecta os sinais)

    # Com receptor conectado, o Celery não sequestra o root logger
    assert setup_logging.receivers


# --- Chamadas externas ------------------------------------------------------


def test_hooks_http_logam_status_latencia_e_mascaram_path(caplog):
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(502)),
        event_hooks=ls.http_log_hooks("telegram"),
    )
    with caplog.at_level(logging.INFO, logger="http.externo"):
        client.post(f"https://api.telegram.org/bot{SEGREDOS['TELEGRAM_BOT_TOKEN']}/sendMessage")

    (rec,) = (r for r in caplog.records if r.name == "http.externo")
    assert rec.levelno == logging.WARNING  # 5xx sobe de nível
    assert rec.service == "telegram" and rec.status == 502 and rec.method == "POST"
    assert rec.elapsed_ms >= 0
    assert SEGREDOS["TELEGRAM_BOT_TOKEN"] not in rec.path
    assert "?" not in rec.path


def test_hooks_http_nao_logam_query_string(caplog):
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
        event_hooks=ls.http_log_hooks("apify"),
    )
    with caplog.at_level(logging.INFO, logger="http.externo"):
        client.get("https://api.apify.com/v2/x", params={"token": "vaza"})
    (rec,) = (r for r in caplog.records if r.name == "http.externo")
    assert "vaza" not in rec.getMessage() and "vaza" not in rec.path


def test_logger_do_httpx_fica_em_warning():
    # Em INFO ele loga a URL completa, com query string e token no path
    root = logging.getLogger()
    antes = list(root.handlers)
    try:
        ls.configure_logging()
        assert logging.getLogger("httpx").level >= logging.WARNING
        assert logging.getLogger("httpcore").level >= logging.WARNING
    finally:
        root.handlers[:] = antes


@pytest.mark.parametrize(
    ("modulo", "fabrica"),
    [
        ("mercado_livre", lambda m, t: m.MercadoLivreCollector(["MLB1"], transport=t).client),
        ("tiktok", lambda m, t: m.TikTokApifyCollector(["x"], transport=t).client),
    ],
)
def test_collectors_usam_os_hooks(modulo, fabrica):
    import importlib

    mod = importlib.import_module(f"src.infrastructure.collectors.{modulo}")
    client = fabrica(mod, httpx.MockTransport(lambda r: httpx.Response(200)))
    assert client.event_hooks["response"], modulo


# --- Tasks ------------------------------------------------------------------


class _Task:
    name = "tasks.collect_tiktok"


def test_task_loga_inicio_fim_duracao_e_contagens(caplog):
    with caplog.at_level(logging.INFO, logger="tasks"):
        ls.on_task_prerun(task_id="t1", task=_Task())
        ls.on_task_postrun(
            task_id="t1",
            task=_Task(),
            retval={"status": "partial", "inserted": 7, "errors": 1, "texto": "x"},
            state="SUCCESS",
        )

    inicio, fim = caplog.records
    assert inicio.msg == "task.inicio" and inicio.task == "tasks.collect_tiktok"
    assert fim.msg == "task.fim"
    assert fim.duration_ms >= 0
    assert fim.result_status == "partial"
    assert fim.inserted == 7 and fim.errors == 1
    assert fim.levelno == logging.WARNING  # partial/error sobe de nível


def test_task_ok_loga_em_info(caplog):
    with caplog.at_level(logging.INFO, logger="tasks"):
        ls.on_task_prerun(task_id="t2", task=_Task())
        ls.on_task_postrun(task_id="t2", task=_Task(), retval={"status": "ok"}, state="SUCCESS")
    assert caplog.records[-1].levelno == logging.INFO
