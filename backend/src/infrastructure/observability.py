# src/infrastructure/observability.py
"""
Métricas no formato de exposição do Prometheus (TIE-36), servidas em
`GET /metrics` pela API.

Duas fontes:

- `HttpMetrics`: requisições e latência da própria API, em memória. A API roda
  num processo uvicorn só; com mais de um worker, cada um teria o seu contador
  e o scrape veria um deles por vez — aí é hora do `prometheus_client` em modo
  multiprocesso.
- `system_samples`: o estado do sistema medido na hora do scrape (idade da
  última coleta e do último insight, fallback do LLM, filas, circuitos,
  backup). São os números por trás do check de saúde (TIE-39), que só diz
  ok/problema com limiares fixos; aqui o limiar fica com quem consome.

Sem dependência nova: o formato de texto é simples e o volume é pequeno.
Componente fora (Mongo, Redis) não derruba o scrape: some a parte dele e
`trends_component_up` vai a 0.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from src.infrastructure.utils.datetime_utils import ensure_utc

logger = logging.getLogger(__name__)

# Mesmas filas do worker (apps/worker/main.py, task_routes)
QUEUES = ("celery", "ml", "tiktok", "trend")
SOURCES = ("tiktok", "mercadolivre")
BREAKER_STATES = ("closed", "open", "half_open", "unknown")
DEFAULT_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
# Mesma janela do check de fallback do LLM (tasks_health)
LLM_FALLBACK_WINDOW = dt.timedelta(hours=6)


@dataclass(frozen=True)
class Sample:
    name: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)
    help: str = ""
    kind: str = "gauge"


def _escapa(valor: str) -> str:
    return valor.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _numero(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else repr(float(v))


def render(samples: list[Sample]) -> str:
    """Texto de exposição (versão 0.0.4); HELP/TYPE uma vez por métrica."""
    linhas: list[str] = []
    vistos: set[str] = set()
    for s in samples:
        if s.name not in vistos:
            vistos.add(s.name)
            linhas.append(f"# HELP {s.name} {s.help}")
            linhas.append(f"# TYPE {s.name} {s.kind}")
        rotulos = ""
        if s.labels:
            rotulos = "{" + ",".join(f'{k}="{_escapa(str(v))}"' for k, v in s.labels.items()) + "}"
        linhas.append(f"{s.name}{rotulos} {_numero(s.value)}")
    return "\n".join(linhas) + "\n" if linhas else ""


class HttpMetrics:
    """Contador de requisições e histograma de latência por rota (template)."""

    def __init__(self, buckets: tuple[float, ...] = DEFAULT_BUCKETS):
        self.buckets = tuple(sorted(buckets))
        self._lock = threading.Lock()
        self._total: dict[tuple[str, str, str], int] = defaultdict(int)
        self._bucket: dict[tuple[str, str], list[int]] = {}
        self._soma: dict[tuple[str, str], float] = defaultdict(float)
        self._n: dict[tuple[str, str], int] = defaultdict(int)

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        chave = (method, route)
        with self._lock:
            self._total[(method, route, str(status))] += 1
            contagens = self._bucket.setdefault(chave, [0] * len(self.buckets))
            for i, limite in enumerate(self.buckets):
                if seconds <= limite:
                    contagens[i] += 1
            self._soma[chave] += seconds
            self._n[chave] += 1

    def samples(self) -> list[Sample]:
        with self._lock:
            total = dict(self._total)
            buckets = {k: list(v) for k, v in self._bucket.items()}
            soma, n = dict(self._soma), dict(self._n)

        out = [
            Sample(
                "trends_http_requests_total",
                v,
                {"method": m, "route": r, "status": st},
                help="Requisições à API por método, rota e status",
                kind="counter",
            )
            for (m, r, st), v in sorted(total.items())
        ]
        ajuda = "Latência das requisições à API, em segundos"
        for (m, r), contagens in sorted(buckets.items()):
            rot = {"method": m, "route": r}
            out += [
                Sample(
                    "trends_http_request_duration_seconds_bucket",
                    c,
                    {**rot, "le": str(limite)},
                    help=ajuda,
                    kind="histogram",
                )
                for limite, c in zip(self.buckets, contagens, strict=True)
            ]
            out.append(
                Sample(
                    "trends_http_request_duration_seconds_bucket",
                    n[(m, r)],
                    {**rot, "le": "+Inf"},
                    help=ajuda,
                    kind="histogram",
                )
            )
            out.append(Sample("trends_http_request_duration_seconds_sum", soma[(m, r)], rot))
            out.append(Sample("trends_http_request_duration_seconds_count", n[(m, r)], rot))
        return out


http_metrics = HttpMetrics()


def _idade_s(now: dt.datetime, ts: Any) -> float:
    return max((now - ensure_utc(ts)).total_seconds(), 0.0)


def _mongo_samples(db, now: dt.datetime) -> list[Sample]:
    out: list[Sample] = []

    for fonte in SOURCES:
        doc = db["metrics"].find_one({"source": fonte}, sort=[("ts", -1)], projection={"ts": 1})
        if doc and doc.get("ts"):
            out.append(
                Sample(
                    "trends_last_collection_age_seconds",
                    _idade_s(now, doc["ts"]),
                    {"source": fonte},
                    help="Segundos desde a última leitura gravada por fonte",
                )
            )
        out.append(
            Sample(
                "trends_products",
                db["products"].count_documents({"source": fonte}),
                {"source": fonte},
                help="Produtos/vídeos conhecidos por fonte",
            )
        )

    ultimo = db["trend_insights"].find_one({}, sort=[("ts", -1)], projection={"ts": 1})
    if ultimo and ultimo.get("ts"):
        out.append(
            Sample(
                "trends_last_insight_age_seconds",
                _idade_s(now, ultimo["ts"]),
                help="Segundos desde o último insight gravado pela análise",
            )
        )
    out.append(
        Sample(
            "trends_insights_24h",
            db["trend_insights"].count_documents({"ts": {"$gte": now - dt.timedelta(hours=24)}}),
            help="Insights gravados nas últimas 24 h",
        )
    )

    # Mesmo critério do check de saúde: debug.llm_error = caiu no fallback
    recentes = list(
        db["trend_insights"]
        .find({"ts": {"$gte": now - LLM_FALLBACK_WINDOW}}, {"debug.llm_error": 1})
        .sort("ts", -1)
        .limit(200)
    )
    out.append(
        Sample(
            "trends_llm_fallback_sample",
            len(recentes),
            help="Insights das últimas 6 h considerados na taxa de fallback",
        )
    )
    if recentes:
        falhas = sum(1 for d in recentes if (d.get("debug") or {}).get("llm_error"))
        out.append(
            Sample(
                "trends_llm_fallback_ratio",
                falhas / len(recentes),
                help="Fração dos insights das últimas 6 h que caíram no score só numérico",
            )
        )

    backup = db["backups"].find_one({}, sort=[("ts", -1)])
    if backup and backup.get("ts"):
        out.append(
            Sample(
                "trends_backup_age_seconds",
                _idade_s(now, backup["ts"]),
                help="Segundos desde o último backup registrado",
            )
        )
        out.append(
            Sample(
                "trends_backup_last_ok",
                1 if backup.get("status") == "ok" else 0,
                help="1 se o último backup terminou ok",
            )
        )
        if backup.get("size_bytes") is not None:
            out.append(
                Sample(
                    "trends_backup_size_bytes",
                    backup["size_bytes"],
                    help="Tamanho do último backup",
                )
            )
    return out


def _redis_samples(client) -> list[Sample]:
    return [
        Sample(
            "trends_celery_queue_length",
            int(client.llen(fila) or 0),
            {"queue": fila},
            help="Mensagens esperando em cada fila do Celery",
        )
        for fila in QUEUES
    ]


def _breaker_samples(breakers: dict[str, dict[str, Any]]) -> list[Sample]:
    out: list[Sample] = []
    for servico, st in sorted(breakers.items()):
        atual = st.get("state", "unknown")
        out += [
            Sample(
                "trends_circuit_breaker_state",
                1 if estado == atual else 0,
                {"service": servico, "state": estado},
                help="Estado do circuit breaker de cada serviço externo (1 = atual)",
            )
            for estado in BREAKER_STATES
        ]
        if st.get("failures") is not None:
            out.append(
                Sample(
                    "trends_circuit_breaker_failures",
                    st["failures"],
                    {"service": servico},
                    help="Falhas consecutivas registradas no circuit breaker",
                )
            )
    return out


def system_samples(
    db, redis_client, *, breakers: dict[str, dict[str, Any]], now: dt.datetime
) -> list[Sample]:
    """Estado do sistema agora. Uma parte fora do ar não derruba as outras."""
    out: list[Sample] = []
    up = {}

    try:
        out += _mongo_samples(db, now)
        up["mongo"] = 1
    except Exception as e:  # noqa: BLE001 — scrape não pode cair junto
        # _mongo_samples monta a própria lista: falha no meio não deixa série solta
        logger.warning("Métricas do Mongo indisponíveis: %s", type(e).__name__)
        up["mongo"] = 0

    if redis_client is None:
        up["redis"] = 0
    else:
        try:
            out += _redis_samples(redis_client)
            up["redis"] = 1
        except Exception as e:  # noqa: BLE001
            logger.warning("Métricas do Redis indisponíveis: %s", type(e).__name__)
            up["redis"] = 0

    out += _breaker_samples(breakers)
    out += [
        Sample(
            "trends_component_up",
            v,
            {"component": c},
            help="1 se o componente respondeu durante o scrape",
        )
        for c, v in up.items()
    ]
    return out
