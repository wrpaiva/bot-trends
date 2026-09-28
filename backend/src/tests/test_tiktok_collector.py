# src/tests/test_tiktok_collector.py
"""
O APIFY_TOKEN não pode aparecer em URL nenhuma: quando a Apify responde erro,
o HTTPStatusError carrega a URL e ela vai parar no log do worker (TIE-3).
"""

import httpx
import pytest

from src.infrastructure.collectors import tiktok as tiktok_mod
from src.infrastructure.collectors.tiktok import TikTokApifyCollector

TOKEN = "apify_api_segredo_de_teste"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr(tiktok_mod.settings, "APIFY_TOKEN", TOKEN)
    # Sem espera real entre retries/polling
    monkeypatch.setattr(tiktok_mod.time, "sleep", lambda *_: None)


def _collector(handler) -> TikTokApifyCollector:
    c = TikTokApifyCollector(
        hashtags=["fyp"], transport=httpx.MockTransport(handler), max_requests_per_minute=0
    )
    # tenacity dorme entre tentativas; zera a espera no teste
    for fn in (c._run_actor, c._get_run_status, c._get_dataset_items):
        fn.retry.sleep = lambda *_: None
    return c


def test_token_vai_no_header_e_nunca_na_url():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/runs"):
            return httpx.Response(
                201, json={"data": {"id": "r1", "defaultDatasetId": "d1", "status": "RUNNING"}}
            )
        if "/actor-runs/" in request.url.path:
            return httpx.Response(200, json={"data": {"status": "SUCCEEDED"}})
        return httpx.Response(200, json=[{"id": "v1", "stats": {"playCount": 10}}])

    with _collector(handler) as c:
        items = list(c.collect())

    assert [i["source_product_id"] for i in items] == ["v1"]
    assert len(requests) == 3
    for req in requests:
        assert TOKEN not in str(req.url)
        assert req.headers["Authorization"] == f"Bearer {TOKEN}"


def test_erro_http_nao_vaza_token_no_log(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"type": "token-not-valid"}})

    with caplog.at_level("DEBUG"), _collector(handler) as c:
        assert list(c.collect()) == []

    assert "Falha ao iniciar actor TikTok" in caplog.text
    assert TOKEN not in caplog.text


# --- Retry e contagem de erros (TIE-17) ---------------------------------------


def _run_ok(request: httpx.Request) -> httpx.Response | None:
    if request.url.path.endswith("/runs"):
        return httpx.Response(
            201, json={"data": {"id": "r1", "defaultDatasetId": "d1", "status": "RUNNING"}}
        )
    return None


def test_401_ao_iniciar_actor_nao_eh_retentado_e_conta_erro():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(401)

    with _collector(handler) as c:
        assert list(c.collect()) == []
    assert len(requests) == 1
    assert c.errors == 1


def test_dataset_com_503_eh_retentado_e_conta_erro_ao_desistir():
    tentativas_dataset: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "/actor-runs/" in request.url.path:
            return httpx.Response(200, json={"data": {"status": "SUCCEEDED"}})
        if "/datasets/" in request.url.path:
            tentativas_dataset.append(1)
            return httpx.Response(503)
        return _run_ok(request)

    with _collector(handler) as c:
        assert list(c.collect()) == []
    assert len(tentativas_dataset) == 3
    assert c.errors == 1


def test_run_que_falha_conta_erro():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/actor-runs/" in request.url.path:
            return httpx.Response(200, json={"data": {"status": "FAILED"}})
        return _run_ok(request)

    with _collector(handler) as c:
        assert list(c.collect()) == []
    assert c.errors == 1


def test_erro_permanente_no_polling_aborta_em_vez_de_esperar_o_timeout():
    polls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "/actor-runs/" in request.url.path:
            polls.append(1)
            return httpx.Response(404)
        return _run_ok(request)

    with _collector(handler) as c:
        assert list(c.collect()) == []
    assert len(polls) == 1
    assert c.errors == 1


def test_sem_token_falha_rapido(monkeypatch):
    monkeypatch.setattr(tiktok_mod.settings, "APIFY_TOKEN", None)
    with pytest.raises(RuntimeError, match="APIFY_TOKEN"):
        TikTokApifyCollector(hashtags=["fyp"])


# Item real do clockworks~tiktok-scraper (2026-09-23), recortado: os contadores
# vêm no primeiro nível, não dentro de `stats`.
ITEM_APIFY_ATUAL = {
    "id": "7075778590062988546",
    "text": "bolacha #foodie",
    "webVideoUrl": "https://www.tiktok.com/@mara.lazeez/video/7075778590062988546",
    "playCount": 1200000,
    "diggCount": 25200,
    "commentCount": 297,
    "shareCount": 524,
    "stats": None,
    "authorMeta": {"name": "mara.lazeez", "nickName": "Mara Lazeez"},
}


def _normalize(item):
    return TikTokApifyCollector(hashtags=["fyp"])._normalize_item(item)


def test_normaliza_contadores_no_primeiro_nivel():
    social = _normalize(ITEM_APIFY_ATUAL)["social"]
    assert social == {
        "views": 1200000,
        "likes": 25200,
        "comments": 297,
        "shares": 524,
        "author": "mara.lazeez",
    }


def test_normaliza_formato_antigo_com_stats():
    item = {
        "id": "v1",
        "stats": {"playCount": 10, "diggCount": 5, "commentCount": 2, "shareCount": 1},
    }
    social = _normalize(item)["social"]
    assert (social["views"], social["likes"], social["comments"], social["shares"]) == (10, 5, 2, 1)


def test_normaliza_item_sem_contadores():
    social = _normalize({"id": "v1"})["social"]
    assert (social["views"], social["likes"], social["comments"], social["shares"]) == (0, 0, 0, 0)
