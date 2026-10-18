# tests/test_repos.py

import datetime as dt

import mongomock

from src.infrastructure.db.repos import MetricsRepo, ProductRepo
from src.infrastructure.db.trend_repos import TrendInsightRepo
from src.infrastructure.utils.datetime_utils import utcnow


def _ms(d: dt.datetime) -> dt.datetime:
    """Trunca para milissegundos, a resolução que o BSON de fato guarda."""
    return d.replace(microsecond=(d.microsecond // 1000) * 1000)


def test_productrepo_upsert_generates_and_stabilizes_uuid():
    client = mongomock.MongoClient()
    db = client["testdb"]

    repo = ProductRepo(db)

    item = {
        "source": "mercadolivre",
        "source_product_id": "MLB123",
        "title": "Produto A",
        "price": 10.0,
        "permalink": "http://x",
        "category": "MLB-cat",
        "brand": "Marca",
        "canonical_id": "MLB123",
    }

    pid1 = repo.upsert(item)
    assert isinstance(pid1, str)
    assert len(pid1) >= 10  # UUID string

    # upsert novamente (mesma chave natural) -> mantém UUID
    item2 = dict(item)
    item2["title"] = "Produto A (novo título)"
    pid2 = repo.upsert(item2)

    assert pid2 == pid1

    doc = db["products"].find_one({"product_id": pid1}, {"_id": 0})
    assert doc["title"] == "Produto A (novo título)"
    assert doc["source"] == "mercadolivre"
    assert doc["source_product_id"] == "MLB123"


def test_metricsrepo_inserts_metric():
    client = mongomock.MongoClient()
    db = client["testdb"]

    repo = MetricsRepo(db)
    repo.insert(
        {
            "ts": utcnow(),
            "product_id": "uuid-1",
            "source": "tiktok",
            "views": 1000,
            "engagement": 50,
            "mentions": 1,
            "price": None,
        }
    )

    assert db["metrics"].count_documents({"product_id": "uuid-1"}) == 1


def test_trendinsightrepo_inserts_window_fields():
    # tz_aware como em mongo.get_client(): as datas voltam aware em UTC (TIE-10)
    client = mongomock.MongoClient(tz_aware=True)
    db = client["testdb"]

    repo = TrendInsightRepo(db)

    window_to = utcnow()
    window_from = window_to - dt.timedelta(hours=72)

    repo.insert(
        product_id="uuid-1",
        window_from=window_from,
        window_to=window_to,
        window_hours=72,
        payload={
            "numeric_score": 70.0,
            "llm_score": 80.0,
            "final_score": 74.0,
            "trend_classification": "SUBINDO",
            "risk_level": "MEDIO",
            "analysis": "x",
            "recommendation": "y",
            "sources": ["tiktok"],
            "debug": {},
        },
    )

    doc = db["trend_insights"].find_one({"product_id": "uuid-1"}, {"_id": 0})
    assert doc["window_hours"] == 72
    # O BSON armazena datetime com resolução de milissegundos, então os
    # microssegundos são truncados na ida ao banco. Comparar igualdade exata
    # com o valor original falharia sempre que ele não fosse redondo.
    assert doc["window_from"] == _ms(window_from)
    assert doc["window_to"] == _ms(window_to)
    assert doc["final_score"] == 74.0


def _payload(**kw):
    return {
        "numeric_score": 70.0,
        "llm_score": 80.0,
        "final_score": 74.0,
        "trend_classification": "SUBINDO",
        "risk_level": "MEDIO",
        "analysis": "x",
        "recommendation": "y",
        "sources": ["tiktok"],
        "debug": {},
    } | kw


def _record(repo, payload, **kw):
    agora = utcnow()
    return repo.record(
        "uuid-1",
        payload,
        window_from=agora - dt.timedelta(hours=72),
        window_to=agora,
        window_hours=72,
        **kw,
    )


def test_record_confirma_insight_repetido_e_atualiza_o_texto():
    db = mongomock.MongoClient(tz_aware=True)["testdb"]
    repo = TrendInsightRepo(db)

    assert _record(repo, _payload()) == "inserted"
    primeiro = db["trend_insights"].find_one()
    assert _record(repo, _payload(analysis="texto novo")) == "confirmed"

    (doc,) = list(db["trend_insights"].find())
    assert doc["first_ts"] == primeiro["ts"]
    assert doc["ts"] >= primeiro["ts"]
    assert (doc["confirmations"], doc["analysis"]) == (1, "texto novo")


def test_record_grava_outro_quando_o_resultado_muda():
    db = mongomock.MongoClient(tz_aware=True)["testdb"]
    repo = TrendInsightRepo(db)
    _record(repo, _payload())
    assert _record(repo, _payload(final_score=75.0)) == "inserted"
    assert db["trend_insights"].count_documents({}) == 2


def test_record_sem_dedupe_grava_sempre():
    db = mongomock.MongoClient(tz_aware=True)["testdb"]
    repo = TrendInsightRepo(db)
    _record(repo, _payload())
    assert _record(repo, _payload(), dedupe=False) == "inserted"
    assert db["trend_insights"].count_documents({}) == 2


def test_record_confirma_insight_antigo_sem_first_ts():
    # Insight gravado antes da TIE-47: first_ts passa a ser o ts dele
    db = mongomock.MongoClient(tz_aware=True)["testdb"]
    repo = TrendInsightRepo(db)
    antigo = utcnow().replace(microsecond=0) - dt.timedelta(hours=1)
    db["trend_insights"].insert_one(
        {"product_id": "uuid-1", "ts": antigo, "window_hours": 72, **_payload()}
    )
    assert _record(repo, _payload()) == "confirmed"
    assert db["trend_insights"].find_one()["first_ts"] == antigo
