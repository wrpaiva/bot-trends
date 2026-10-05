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
- `RedisMetricsStore`: contadores e histogramas do WORKER (duração das tasks,
  itens coletados, chamadas ao LLM). O worker roda em vários processos que a
  API não enxerga; no Redis (`HINCRBYFLOAT` num hash) eles se somam e
  sobrevivem a restart, que é o que o Prometheus espera de um counter.
  Gravar métrica nunca derruba task: `safe_inc`/`safe_observe` engolem erro.

Sem dependência nova: o formato de texto é simples e o volume é pequeno.
Componente fora (Mongo, Redis) não derruba o scrape: some a parte dele e
`trends_component_up` vai a 0.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
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
# Tasks vão de segundos (health) a minutos (coleta do TikTok espera a Apify)
TASK_BUCKETS = (1.0, 5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0)
STORE_KEY = "obs:metrics"

# Métricas que o worker grava no Redis: tipo e ajuda
WORKER_METRICS = {
    "trends_task_runs_total": (
        "counter",
        "Execuções de task por estado do Celery e status devolvido",
    ),
    "trends_task_duration_seconds": ("histogram", "Duração das tasks do Celery, em segundos"),
    "trends_collected_items_total": (
        "counter",
        "Itens gravados pelas tasks de coleta (`inserted`)",
    ),
    "trends_llm_calls_total": ("counter", "Chamadas HTTP reais ao LLM por resultado (custo)"),
    "trends_llm_cache_total": ("counter", "Consultas ao cache do LLM: hit evita chamada"),
}
# Mesma janela do check de fallback do LLM (tasks_health)
LLM_FALLBACK_WINDOW = dt.timedelta(hours=6)


@dataclass(frozen=True)
class Sample:
    name: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)
    help: str = ""
    kind: str = "gauge"
    # Histograma: as séries _bucket/_sum/_count pertencem à família `trends_x`,
    # e é ela que leva o HELP/TYPE — `# TYPE trends_x_bucket` quebra o parser
    family: str | None = None


def _escapa(valor: str) -> str:
    return valor.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _numero(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else repr(float(v))


def render(samples: list[Sample]) -> str:
    """Texto de exposição (versão 0.0.4); HELP/TYPE uma vez por família."""
    linhas: list[str] = []
    vistos: set[str] = set()
    for s in samples:
        familia = s.family or s.name
        if familia not in vistos:
            vistos.add(familia)
            linhas.append(f"# HELP {familia} {s.help}")
            linhas.append(f"# TYPE {familia} {s.kind}")
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
        fam = "trends_http_request_duration_seconds"
        meta = {"help": "Latência das requisições à API, em segundos", "kind": "histogram"}
        for (m, r), contagens in sorted(buckets.items()):
            rot = {"method": m, "route": r}
            out += [
                Sample(f"{fam}_bucket", c, {**rot, "le": str(limite)}, family=fam, **meta)
                for limite, c in zip(self.buckets, contagens, strict=True)
            ]
            out.append(
                Sample(f"{fam}_bucket", n[(m, r)], {**rot, "le": "+Inf"}, family=fam, **meta)
            )
            out.append(Sample(f"{fam}_sum", soma[(m, r)], rot, family=fam, **meta))
            out.append(Sample(f"{fam}_count", n[(m, r)], rot, family=fam, **meta))
        return out


http_metrics = HttpMetrics()


class RedisMetricsStore:
    """Contadores/histogramas compartilhados entre processos, num hash do Redis."""

    def __init__(self, client: Any, key: str = STORE_KEY):
        self.client = client
        self.key = key

    @staticmethod
    def _campo(nome: str, labels: dict[str, str]) -> str:
        return json.dumps([nome, sorted(labels.items())], ensure_ascii=False)

    def inc(self, nome: str, labels: dict[str, str], valor: float = 1.0) -> None:
        self.client.hincrbyfloat(self.key, self._campo(nome, labels), valor)

    def observe(
        self,
        nome: str,
        labels: dict[str, str],
        valor: float,
        *,
        buckets: tuple[float, ...] = TASK_BUCKETS,
    ) -> None:
        pipe = self.client.pipeline(transaction=False)
        for limite in buckets:
            if valor <= limite:
                pipe.hincrbyfloat(
                    self.key, self._campo(f"{nome}_bucket", {**labels, "le": str(limite)}), 1
                )
        pipe.hincrbyfloat(self.key, self._campo(f"{nome}_bucket", {**labels, "le": "+Inf"}), 1)
        pipe.hincrbyfloat(self.key, self._campo(f"{nome}_sum", labels), valor)
        pipe.hincrbyfloat(self.key, self._campo(f"{nome}_count", labels), 1)
        pipe.execute()

    def samples(self) -> list[Sample]:
        out = []
        for campo, valor in self.client.hgetall(self.key).items():
            nome, labels = json.loads(campo)
            familia = nome
            for sufixo in ("_bucket", "_sum", "_count"):
                if nome.endswith(sufixo) and nome.removesuffix(sufixo) in WORKER_METRICS:
                    familia = nome.removesuffix(sufixo)
            kind, ajuda = WORKER_METRICS.get(familia, ("untyped", ""))
            out.append(
                Sample(nome, float(valor), dict(labels), help=ajuda, kind=kind, family=familia)
            )

        sufixos = {"_bucket": 0, "_sum": 1, "_count": 2}

        def _ordem(x: Sample) -> tuple:
            # Família contígua; dentro dela, por série, buckets (le crescente),
            # depois _sum e _count
            le = x.labels.get("le")
            serie = sorted((k, v) for k, v in x.labels.items() if k != "le")
            sufixo = x.name.removeprefix(x.family or "")
            return (
                x.family,
                serie,
                sufixos.get(sufixo, 0),
                float("inf") if le == "+Inf" else float(le or 0),
            )

        return sorted(out, key=_ordem)


_store: RedisMetricsStore | None = None


def metrics_store() -> RedisMetricsStore:
    """Store do processo atual, criado sob demanda (depois do fork do Celery)."""
    global _store
    if _store is None:
        import redis

        from src.infrastructure.config import settings

        _store = RedisMetricsStore(
            redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        )
    return _store


def safe_inc(nome: str, labels: dict[str, str], valor: float = 1.0) -> None:
    try:
        metrics_store().inc(nome, labels, valor)
    except Exception as e:  # noqa: BLE001 — métrica não derruba task
        logger.debug("Métrica %s não gravada: %s", nome, e)


def safe_observe(nome: str, labels: dict[str, str], valor: float) -> None:
    try:
        metrics_store().observe(nome, labels, valor)
    except Exception as e:  # noqa: BLE001
        logger.debug("Métrica %s não gravada: %s", nome, e)


# --- Sinais do Celery --------------------------------------------------------

_inicios: dict[str | None, float] = {}


def on_task_prerun_metrics(task_id: str | None = None, **_: Any) -> None:
    _inicios[task_id] = time.perf_counter()


def on_task_postrun_metrics(
    task_id: str | None = None,
    task: Any = None,
    retval: Any = None,
    state: str | None = None,
    **_: Any,
) -> None:
    t0 = _inicios.pop(task_id, None)
    nome = getattr(task, "name", "desconhecida")
    status = retval.get("status") if isinstance(retval, dict) else None
    safe_inc(
        "trends_task_runs_total",
        {"task": nome, "state": state or "none", "status": str(status or "none")},
    )
    if t0 is not None:
        safe_observe("trends_task_duration_seconds", {"task": nome}, time.perf_counter() - t0)
    inseridos = retval.get("inserted") if isinstance(retval, dict) else None
    if isinstance(inseridos, int) and not isinstance(inseridos, bool):
        safe_inc("trends_collected_items_total", {"task": nome}, inseridos)


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
            # A coleta grava todas as leituras do ciclo com o mesmo `ts`
            out.append(
                Sample(
                    "trends_last_collection_items",
                    db["metrics"].count_documents({"source": fonte, "ts": doc["ts"]}),
                    {"source": fonte},
                    help="Leituras gravadas no último ciclo de coleta da fonte",
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
    filas = [
        Sample(
            "trends_celery_queue_length",
            int(client.llen(fila) or 0),
            {"queue": fila},
            help="Mensagens esperando em cada fila do Celery",
        )
        for fila in QUEUES
    ]
    return filas + RedisMetricsStore(client).samples()


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
