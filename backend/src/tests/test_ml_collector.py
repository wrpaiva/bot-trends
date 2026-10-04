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
    for fn in (c._get_highlights, c._get_items_batch, c._get_reviews_total):
        fn.retry.sleep = lambda *_: None
    return c


def _item(item_id: str) -> dict:
    return {
        "code": 200,
        "body": {
            "id": item_id,
            "title": f"Produto {item_id}",
            "price": 10,
            "sold_quantity": 7,
        },
    }


def _handler_ok(request: httpx.Request) -> httpx.Response:
    if "/highlights/" in request.url.path:
        cat = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"content": [{"id": f"{cat}-A", "type": "ITEM"}]})
    if "/reviews/item/" in request.url.path:
        return httpx.Response(200, json={"paging": {"total": 12}})
    ids = request.url.params["ids"].split(",")
    return httpx.Response(200, json=[_item(i) for i in ids])


def test_coleta_normaliza_itens_sem_erros():
    with _collector(_handler_ok) as c:
        itens = list(c.collect())
    assert [i["source_product_id"] for i in itens] == ["MLB1-A"]
    assert itens[0]["reviews_total"] == 12
    assert itens[0]["sold_quantity"] == 7
    assert c.errors == 0


def test_falha_em_reviews_mantem_item_e_marca_coleta_parcial():
    chamadas = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal chamadas
        if "/reviews/item/" in request.url.path:
            chamadas += 1
            return httpx.Response(500, json={"message": "indisponível"})
        return _handler_ok(request)

    with _collector(handler) as c:
        itens = list(c.collect())

    assert itens[0]["reviews_total"] is None
    assert c.errors == 1
    assert chamadas == 3


def test_total_de_reviews_ausente_vira_none_sem_invalidar_item():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/reviews/item/" in request.url.path:
            return httpx.Response(200, json={"paging": {}})
        return _handler_ok(request)

    with _collector(handler) as c:
        itens = list(c.collect())

    assert itens[0]["reviews_total"] is None
    assert c.errors == 0


def test_total_de_reviews_invalido_mantem_item_e_marca_erro():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/reviews/item/" in request.url.path:
            return httpx.Response(200, json={"paging": {"total": "desconhecido"}})
        return _handler_ok(request)

    with _collector(handler) as c:
        itens = list(c.collect())

    assert itens[0]["reviews_total"] is None
    assert c.errors == 1


def test_falha_de_token_ao_buscar_reviews_interrompe_coleta_com_motivo():
    class AuthFalhaNaTerceiraChamada(_AuthFalso):
        def __init__(self):
            super().__init__()
            self.chamadas = 0

        def access_token(self):
            self.chamadas += 1
            if self.chamadas == 3:
                raise ml_mod.MLAuthError("token indisponível durante reviews")
            return super().access_token()

    auth = AuthFalhaNaTerceiraChamada()
    with _com_auth(_handler_ok, auth) as c:
        assert list(c.collect()) == []

    assert c.errors == 1
    assert c.auth_error == "token indisponível durante reviews"


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


# --- OAuth (TIE-41) -------------------------------------------------------------


class _AuthFalso:
    """Token atual e renovações pedidas; `falha` simula token impossível de obter."""

    def __init__(self, token="APP_USR-t1", falha=None):
        self.token = token
        self.falha = falha
        self.renovacoes: list[str | None] = []

    def access_token(self):
        if self.falha:
            raise ml_mod.MLAuthError(self.falha)
        return self.token

    def refresh(self, stale=None):
        self.renovacoes.append(stale)
        self.token = "APP_USR-t2"
        return self.token


def _com_auth(handler, auth, categorias=("MLB1",)) -> MercadoLivreCollector:
    c = _collector(handler, categorias)
    c.auth = auth
    return c


def test_toda_chamada_leva_o_bearer():
    headers = []

    def handler(request):
        headers.append(request.headers.get("Authorization"))
        return _handler_ok(request)

    with _com_auth(handler, _AuthFalso()) as c:
        assert len(list(c.collect())) == 1
    assert headers == ["Bearer APP_USR-t1"] * 3


def test_401_renova_o_token_uma_vez_e_repete_a_chamada():
    def handler(request):
        if request.headers["Authorization"] == "Bearer APP_USR-t1":
            return httpx.Response(401, json={"message": "invalid access token"})
        return _handler_ok(request)

    auth = _AuthFalso()
    with _com_auth(handler, auth) as c:
        itens = list(c.collect())

    assert len(itens) == 1 and c.errors == 0
    assert auth.renovacoes == ["APP_USR-t1"]


def test_401_depois_de_renovar_conta_erro_sem_entrar_em_loop():
    chamadas = []

    def handler(request):
        chamadas.append(request.headers["Authorization"])
        return httpx.Response(401, json={"message": "invalid access token"})

    auth = _AuthFalso()
    with _com_auth(handler, auth) as c:
        assert list(c.collect()) == []

    assert chamadas == ["Bearer APP_USR-t1", "Bearer APP_USR-t2"]
    assert c.errors == 1 and len(auth.renovacoes) == 1


def test_sem_token_a_coleta_para_e_diz_o_motivo():
    chamadas = []

    def handler(request):
        chamadas.append(request)
        return _handler_ok(request)

    auth = _AuthFalso(falha="nenhum token do Mercado Livre gravado")
    with _com_auth(handler, auth, categorias=("MLB1", "MLB2", "MLB3")) as c:
        assert list(c.collect()) == []

    # Não insiste categoria a categoria: sem token, nenhuma vai funcionar
    assert chamadas == [] and c.errors == 1
    assert "nenhum token" in c.auth_error


def test_item_leva_a_posicao_e_ordem_do_highlights_da_categoria():
    # TIE-14: /items pode devolver outra ordem; o ranking é relativo a cada
    # categoria e precisa preservar a ordem/posição de /highlights.
    def handler(request: httpx.Request) -> httpx.Response:
        if "/reviews/item/" in request.url.path:
            return httpx.Response(200, json={"paging": {"total": 2}})
        if "/highlights/" in request.url.path:
            conteudo = [
                {"id": "B", "type": "ITEM", "position": 7},
                {"id": "X", "type": "PRODUCT", "position": 8},
                {"id": "C", "type": "ITEM"},
            ]
            return httpx.Response(200, json={"content": conteudo})
        ids = request.url.params["ids"].split(",")
        return httpx.Response(200, json=[_item(i) for i in reversed(ids)])

    with _collector(handler) as c:
        itens = list(c.collect())

    assert [(i["source_product_id"], i["rank_position"]) for i in itens] == [
        ("B", 7),
        ("C", 3),
    ]
