# src/tests/test_observability.py
"""
Métricas no formato Prometheus (TIE-36): contadores HTTP da API e o estado do
sistema medido na hora do scrape.
"""

import datetime as dt

import mongomock
import pytest

from src.infrastructure.observability import HttpMetrics, Sample, render, system_samples

AGORA = dt.datetime(2026, 10, 4, 12, tzinfo=dt.UTC)


def _valor(samples, nome, **labels):
    achados = [s.value for s in samples if s.name == nome and s.labels == labels]
    assert len(achados) == 1, (nome, labels, achados)
    return achados[0]


def _nomes(samples):
    return {s.name for s in samples}


# --- render ------------------------------------------------------------------


def test_render_formato_de_exposicao():
    texto = render(
        [
            Sample("trends_x", 1.5, {"a": "b"}, help="Ajuda", kind="gauge"),
            Sample("trends_x", 2, {"a": "c"}, help="Ajuda", kind="gauge"),
            Sample("trends_y", 3, help="Outra", kind="counter"),
        ]
    )
    assert texto == (
        "# HELP trends_x Ajuda\n"
        "# TYPE trends_x gauge\n"
        'trends_x{a="b"} 1.5\n'
        'trends_x{a="c"} 2\n'
        "# HELP trends_y Outra\n"
        "# TYPE trends_y counter\n"
        "trends_y 3\n"
    )


def test_render_escapa_valores_de_label():
    texto = render([Sample("trends_x", 1, {"t": 'a"b\\c\nd'}, help="h")])
    assert 'trends_x{t="a\\"b\\\\c\\nd"} 1' in texto


# --- HTTP --------------------------------------------------------------------


def test_http_conta_requisicoes_e_monta_o_histograma():
    m = HttpMetrics(buckets=(0.1, 1.0))
    m.observe("GET", "/products/{product_id}", 200, 0.05)
    m.observe("GET", "/products/{product_id}", 200, 0.5)
    m.observe("GET", "/products/{product_id}", 404, 2.0)
    s = m.samples()

    assert (
        _valor(
            s,
            "trends_http_requests_total",
            method="GET",
            route="/products/{product_id}",
            status="200",
        )
        == 2
    )
    assert (
        _valor(
            s,
            "trends_http_requests_total",
            method="GET",
            route="/products/{product_id}",
            status="404",
        )
        == 1
    )
    rota = {"method": "GET", "route": "/products/{product_id}"}
    # Histograma cumulativo: <= 0.1 → 1; <= 1.0 → 2; +Inf → 3
    assert _valor(s, "trends_http_request_duration_seconds_bucket", **rota, le="0.1") == 1
    assert _valor(s, "trends_http_request_duration_seconds_bucket", **rota, le="1.0") == 2
    assert _valor(s, "trends_http_request_duration_seconds_bucket", **rota, le="+Inf") == 3
    assert _valor(s, "trends_http_request_duration_seconds_count", **rota) == 3
    assert _valor(s, "trends_http_request_duration_seconds_sum", **rota) == pytest.approx(2.55)


def test_http_sem_requisicao_nao_emite_serie():
    assert HttpMetrics().samples() == []


# --- Sistema -----------------------------------------------------------------


class _Redis:
    def __init__(self, filas=None, falhar=False):
        self.filas = filas or {}
        self.falhar = falhar

    def llen(self, fila):
        if self.falhar:
            raise ConnectionError("redis fora")
        return self.filas.get(fila, 0)


@pytest.fixture
def db():
    db = mongomock.MongoClient(tz_aware=True).db
    db["metrics"].insert_many(
        [
            {"source": "tiktok", "ts": AGORA - dt.timedelta(hours=2)},
            {"source": "tiktok", "ts": AGORA - dt.timedelta(hours=30)},
        ]
    )
    db["products"].insert_many(
        [{"source": "tiktok"} for _ in range(3)] + [{"source": "mercadolivre"}]
    )
    db["trend_insights"].insert_many(
        [
            {"ts": AGORA - dt.timedelta(minutes=30), "debug": {"llm_error": "429"}},
            {"ts": AGORA - dt.timedelta(hours=1), "debug": {}},
            {"ts": AGORA - dt.timedelta(hours=1), "debug": {}},
            {"ts": AGORA - dt.timedelta(hours=1), "debug": {}},
            # Fora das 24 h e da janela do fallback
            {"ts": AGORA - dt.timedelta(hours=30), "debug": {"llm_error": "x"}},
        ]
    )
    db["backups"].insert_one(
        {"ts": AGORA - dt.timedelta(hours=10), "status": "ok", "size_bytes": 722621}
    )
    return db


def _amostras(db, redis=None, breakers=None):
    return system_samples(
        db,
        redis if redis is not None else _Redis({"trend": 3}),
        breakers=breakers if breakers is not None else {"apify": {"state": "open", "failures": 5}},
        now=AGORA,
    )


def test_idades_em_segundos(db):
    s = _amostras(db)
    assert _valor(s, "trends_last_collection_age_seconds", source="tiktok") == 2 * 3600
    assert _valor(s, "trends_last_insight_age_seconds") == 30 * 60
    assert _valor(s, "trends_backup_age_seconds") == 10 * 3600


def test_fonte_que_nunca_coletou_nao_emite_idade(db):
    # Série ausente é o jeito Prometheus de dizer "não há valor"
    s = _amostras(db)
    assert not [
        x
        for x in s
        if x.name == "trends_last_collection_age_seconds" and x.labels["source"] == "mercadolivre"
    ]


def test_contagens_e_fallback_do_llm(db):
    s = _amostras(db)
    assert _valor(s, "trends_insights_24h") == 4
    assert _valor(s, "trends_products", source="tiktok") == 3
    assert _valor(s, "trends_products", source="mercadolivre") == 1
    # 1 de 4 insights das últimas 6 h sem LLM
    assert _valor(s, "trends_llm_fallback_ratio") == pytest.approx(0.25)
    assert _valor(s, "trends_llm_fallback_sample") == 4


def test_filas_e_circuitos(db):
    s = _amostras(db)
    assert _valor(s, "trends_celery_queue_length", queue="trend") == 3
    assert _valor(s, "trends_celery_queue_length", queue="ml") == 0
    assert _valor(s, "trends_circuit_breaker_state", service="apify", state="open") == 1
    assert _valor(s, "trends_circuit_breaker_state", service="apify", state="closed") == 0
    assert _valor(s, "trends_circuit_breaker_failures", service="apify") == 5


def test_backup_com_falha(db):
    db["backups"].insert_one({"ts": AGORA, "status": "error", "error": "permission denied"})
    s = _amostras(db)
    assert _valor(s, "trends_backup_last_ok") == 0
    assert _valor(s, "trends_backup_age_seconds") == 0


def test_componentes_no_ar(db):
    s = _amostras(db)
    assert _valor(s, "trends_component_up", component="mongo") == 1
    assert _valor(s, "trends_component_up", component="redis") == 1


def test_redis_fora_some_as_filas_e_marca_o_componente(db):
    s = _amostras(db, redis=_Redis(falhar=True))
    assert _valor(s, "trends_component_up", component="redis") == 0
    assert "trends_celery_queue_length" not in _nomes(s)
    # O resto continua saindo
    assert "trends_insights_24h" in _nomes(s)


def test_mongo_fora_nao_derruba_o_scrape():
    class _DbQuebrado:
        def __getitem__(self, nome):
            raise RuntimeError("mongo fora")

    s = system_samples(_DbQuebrado(), _Redis(), breakers={}, now=AGORA)
    assert _valor(s, "trends_component_up", component="mongo") == 0
    assert "trends_insights_24h" not in _nomes(s)
    assert _valor(s, "trends_component_up", component="redis") == 1
