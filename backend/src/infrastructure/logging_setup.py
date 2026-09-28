# src/infrastructure/logging_setup.py
"""
Logging estruturado (TIE-13).

- `configure_logging()`: um handler no root, em JSON (ou texto, `LOG_FORMAT=text`).
  Chamado no import da API e no sinal `setup_logging` do Celery.
- Toda saída passa por `redact()`: valores de credenciais do `settings`, senha
  em URI, `Bearer ...`, `token=...` e o token do Telegram no path viram `***`.
  A redação é na string final, então cobre mensagem, extras e traceback.
- `http_log_hooks(service)`: event hooks do httpx que logam status e latência
  de cada chamada externa (sem query string).
- `on_task_prerun`/`on_task_postrun`: início, fim, duração e contagens de toda
  task Celery, num lugar só.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import logging
import re
import sys
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from src.infrastructure.config import settings

HANDLER_MARK = "_trends_handler"

# Campos do settings cujo valor nunca pode aparecer em log
SECRET_FIELDS = (
    "API_KEY",
    "APIFY_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "LLM_API_KEY",
    "ML_CLIENT_SECRET",
)
URI_FIELDS = ("MONGO_URI", "REDIS_URL")

_PADROES = [
    (re.compile(r"(://[^:/@\s]*:)[^@\s]+@"), r"\1***@"),  # user:senha@host
    (re.compile(r"(/bot)\d+:[\w-]+"), r"\1***"),  # Telegram
    (re.compile(r"(Bearer\s+)[^\s\"']+", re.IGNORECASE), r"\1***"),
    # Tokens OAuth do Mercado Livre (access `APP_USR-...`, refresh `TG-...`):
    # não estão no settings, moram no Mongo e mudam a cada renovação (TIE-41)
    (re.compile(r"\b(APP_USR|TG)-[\w-]{8,}"), r"\1-***"),
    (
        re.compile(r"((?:token|api_key|apikey|key|password|secret)=)[^&\s\"']+", re.IGNORECASE),
        r"\1***",
    ),
]

# Atributos que todo LogRecord tem; o resto é `extra=` e vai para o JSON
_PADRAO_RECORD = set(logging.LogRecord("x", 0, "x", 0, "x", None, None).__dict__) | {
    "message",
    "asctime",
    "color_message",  # uvicorn
}


def _segredos() -> list[str]:
    valores = [getattr(settings, f, None) for f in SECRET_FIELDS]
    for f in URI_FIELDS:
        with contextlib.suppress(ValueError):
            valores.append(urlsplit(getattr(settings, f, "") or "").password)
    # Mais longos primeiro: um segredo que contém outro é mascarado inteiro
    return sorted({v for v in valores if v and len(v) >= 4}, key=len, reverse=True)


def redact(texto: str) -> str:
    for segredo in _segredos():
        texto = texto.replace(segredo, "***")
    for padrao, troca in _PADROES:
        texto = padrao.sub(troca, texto)
    return texto


class RedactingFormatter(logging.Formatter):
    """Formato texto, com a mesma redação do JSON."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "ts": dt.datetime.fromtimestamp(record.created, dt.UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for chave, valor in record.__dict__.items():
            if chave not in _PADRAO_RECORD and not chave.startswith("_"):
                doc[chave] = valor
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(doc, ensure_ascii=False, default=repr))


def configure_logging() -> None:
    """Idempotente: substitui só o handler que ele mesmo instalou."""
    root = logging.getLogger()
    root.handlers[:] = [h for h in root.handlers if not getattr(h, HANDLER_MARK, False)]

    handler = logging.StreamHandler(sys.stdout)
    setattr(handler, HANDLER_MARK, True)
    if settings.LOG_FORMAT.lower() == "text":
        handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    else:
        handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(settings.LOG_LEVEL.upper())

    # uvicorn configura handlers próprios antes de importar o app; manda tudo
    # para o root, para sair no mesmo formato e com redação.
    for nome in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(nome)
        lg.handlers[:] = []
        lg.propagate = True

    # O httpx loga cada requisição em INFO com a URL inteira — query string e o
    # token do Telegram no path. `http_log_hooks` substitui isso com path limpo.
    for nome in ("httpx", "httpcore"):
        logging.getLogger(nome).setLevel(logging.WARNING)


# --- HTTP externo -----------------------------------------------------------

_http_log = logging.getLogger("http.externo")


def http_log_hooks(service: str) -> dict[str, list]:
    """Event hooks para `httpx.Client(event_hooks=...)`."""

    def _inicio(request: httpx.Request) -> None:
        request.extensions["t0"] = time.perf_counter()

    def _fim(response: httpx.Response) -> None:
        req = response.request
        t0 = req.extensions.get("t0")
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1) if t0 else None
        _http_log.log(
            logging.WARNING if response.status_code >= 400 else logging.INFO,
            "http.chamada",
            extra={
                "service": service,
                "method": req.method,
                "host": req.url.host,
                # Sem query string: é onde tokens costumam ir parar
                "path": redact(req.url.path),
                "status": response.status_code,
                "elapsed_ms": elapsed_ms,
            },
        )

    return {"request": [_inicio], "response": [_fim]}


# --- Tasks Celery -----------------------------------------------------------

_task_log = logging.getLogger("tasks")
_inicios: dict[str, float] = {}


def on_task_prerun(task_id: str | None = None, task: Any = None, **_: Any) -> None:
    _inicios[task_id] = time.perf_counter()
    _task_log.info("task.inicio", extra={"task": task.name, "task_id": task_id})


def on_task_postrun(
    task_id: str | None = None,
    task: Any = None,
    retval: Any = None,
    state: str | None = None,
    **_: Any,
) -> None:
    t0 = _inicios.pop(task_id, None)
    extra: dict[str, Any] = {
        "task": task.name,
        "task_id": task_id,
        "state": state,
        "duration_ms": round((time.perf_counter() - t0) * 1000, 1) if t0 else None,
    }
    status = None
    if isinstance(retval, dict):
        status = retval.get("status")
        extra["result_status"] = status
        # Contagens (inserted, errors, processed, alerts_*): só números
        for chave, valor in retval.items():
            if isinstance(valor, int | float) and not isinstance(valor, bool):
                extra[chave] = valor

    ruim = state != "SUCCESS" or status in ("partial", "error")
    _task_log.log(logging.WARNING if ruim else logging.INFO, "task.fim", extra=extra)
