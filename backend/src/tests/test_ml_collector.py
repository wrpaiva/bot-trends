# src/tests/test_ml_collector.py
"""
MercadoLivreCollector com o client HTTP mockado (TIE-17): erro permanente não é
retentado, erro transitório é, e uma categoria que falha não derruba as outras.
"""

import httpx

from src.infrastructure.collectors import mercado_livre as ml_mod
from src.infrastructure.collectors.mercado_livre import MercadoLivreCollector


def _collector(handler, categorias=("MLB1",)) -> MercadoLivreCollector:
    c = MercadoLivreCollector(
        list(categorias),
        transport=httpx.MockTransport(handler),
        max_requests_per_minute=0,
    )
    for fn in (c._get_highlights_item_ids, c._get_items_batch):
        fn.retry.sleep = lambda *_: None
    return c


def _item(item_id: str) -> dict:
    return {"code": 200, "body": {"id": item_id, "title": f"Produto {item_id}", "price": 10}}


def _handler_ok(request: httpx.Request) -> httpx.Response:
    if "/highlights/" in request.url.path:
        cat = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"content": [{"id": f"{cat}-A", "type": "ITEM"}]})
    ids = request.url.params["ids"].split(",")
    return httpx.Response(200, json=[_item(i) for i in ids])


def test_coleta_normaliza_itens_sem_erros():
    with _collector(_handler_ok) as c:
        itens = list(c.collect())
    assert [i["source_product_id"] for i in itens] == ["MLB1-A"]
    assert c.errors == 0


def test_categoria_com_401_nao_eh_retentada_e_nao_derruba_as_outras():
    chamadas: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request.url.path)
        if request.url.path.endswith("/MLB1"):
            return httpx.Response(401, json={"message": "unspecified_token"})
        return _handler_ok(request)

    with _collector(handler, categorias=("MLB1", "MLB2")) as c:
        itens = list(c.collect())

    assert [i["source_product_id"] for i in itens] == ["MLB2-A"]
    assert c.errors == 1
    assert chamadas.count("/highlights/MLB/category/MLB1") == 1


def test_429_eh_retentado_ate_dar_certo():
    tentativas = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/highlights/" in request.url.path and tentativas["n"] == 0:
            tentativas["n"] += 1
            return httpx.Response(429, headers={"Retry-After": "1"})
        return _handler_ok(request)

    with _collector(handler) as c:
        itens = list(c.collect())

    assert len(itens) == 1
    assert c.errors == 0


def test_json_invalido_conta_como_erro_e_nao_propaga():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>manutencao</html>")

    with _collector(handler) as c:
        assert list(c.collect()) == []
    assert c.errors == 1


def test_teto_de_requisicoes_vem_da_config(monkeypatch):
    monkeypatch.setattr(ml_mod.settings, "ML_MAX_REQUESTS_PER_MINUTE", 120)
    assert MercadoLivreCollector(["MLB1"])._limiter.interval == 0.5
