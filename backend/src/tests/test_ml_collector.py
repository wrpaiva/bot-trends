# src/tests/test_ml_collector.py
"""
MercadoLivreCollector com o client HTTP mockado (TIE-17). Desde 2026-10 a
coleta parte do catálogo (/highlights → /products), porque /items dá 403 a
token de usuário comum. O client continua mockado: erro permanente não é
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
    for fn in (c._get_highlights, c._get_product, c._get_offers):
        fn.retry.sleep = lambda *_: None
    return c


def _produto(product_id: str) -> dict:
    # Formato de /products/{id} com token de usuário comum (2026-10-06)
    return {
        "id": product_id,
        "name": f"Produto {product_id}",
        "permalink": f"https://www.mercadolivre.com.br/p/{product_id}",
        "domain_id": "MLB-CELLPHONES",
        "buy_box_winner": None,
        "attributes": [{"id": "BRAND", "value_name": "Samsung"}],
    }


def _ofertas(*precos: float) -> dict:
    # Formato de /products/{id}/items: anúncios que vendem o produto
    return {
        "paging": {"total": len(precos), "offset": 0, "limit": 50},
        "results": [
            {"item_id": f"MLB9{i}", "price": p, "currency_id": "BRL", "category_id": "MLB1055"}
            for i, p in enumerate(precos)
        ],
    }


def _handler_ok(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if "/highlights/" in path:
        cat = path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"content": [{"id": f"{cat}-A", "type": "PRODUCT"}]})
    if path.endswith("/items"):
        return httpx.Response(200, json=_ofertas(1299.0, 1199.9))
    return httpx.Response(200, json=_produto(path.rsplit("/", 1)[-1]))


def test_coleta_normaliza_produto_do_catalogo_sem_erros():
    with _collector(_handler_ok) as c:
        itens = list(c.collect())

    assert len(itens) == 1 and c.errors == 0
    item = itens[0]
    assert item["source"] == "mercadolivre"
    assert item["source_product_id"] == item["canonical_id"] == "MLB1-A"
    assert item["title"] == "Produto MLB1-A"
    assert item["brand"] == "Samsung"
    assert item["permalink"] == "https://www.mercadolivre.com.br/p/MLB1-A"
    # Grupo do percentil = categoria do ranking, não a folha do anúncio
    assert item["category"] == "MLB1"
    assert item["rank_position"] == 1


def test_preco_eh_a_menor_oferta_e_ofertas_vao_para_marketplace():
    with _collector(_handler_ok) as c:
        item = next(iter(c.collect()))

    assert item["price"] == 1199.9 and item["currency"] == "BRL"
    assert item["marketplace"]["offers"] == 2
    assert item["marketplace"]["domain_id"] == "MLB-CELLPHONES"


def test_preco_do_buy_box_tem_prioridade_sobre_as_ofertas():
    def handler(request):
        if request.url.path.endswith("/MLB1-A"):
            produto = _produto("MLB1-A")
            produto["buy_box_winner"] = {"price": 1250.0, "currency_id": "BRL"}
            return httpx.Response(200, json=produto)
        return _handler_ok(request)

    with _collector(handler) as c:
        item = next(iter(c.collect()))

    assert item["price"] == 1250.0


def test_vendas_e_avaliacoes_ficam_sem_valor():
    # /items e /reviews/item dão 403 a token comum: sem fonte, None (não 0)
    with _collector(_handler_ok) as c:
        item = next(iter(c.collect()))

    assert item["sold_quantity"] is None
    assert item.get("reviews_total") is None


def test_falha_nas_ofertas_mantem_produto_sem_preco_e_marca_coleta_parcial():
    chamadas = 0

    def handler(request):
        nonlocal chamadas
        if request.url.path.endswith("/items"):
            chamadas += 1
            return httpx.Response(500, json={"message": "indisponível"})
        return _handler_ok(request)

    with _collector(handler) as c:
        itens = list(c.collect())

    assert itens[0]["price"] is None and itens[0]["source_product_id"] == "MLB1-A"
    assert c.errors == 1
    assert chamadas == 3


def test_produto_sem_ofertas_fica_sem_preco_sem_erro():
    def handler(request):
        if request.url.path.endswith("/items"):
            return httpx.Response(200, json=_ofertas())
        return _handler_ok(request)

    with _collector(handler) as c:
        item = next(iter(c.collect()))

    assert item["price"] is None and item["marketplace"]["offers"] == 0
    assert c.errors == 0


def test_falha_no_detalhe_do_produto_pula_o_produto_e_conta_erro():
    def handler(request):
        if "/highlights/" in request.url.path:
            conteudo = [{"id": "P1", "type": "PRODUCT"}, {"id": "P2", "type": "PRODUCT"}]
            return httpx.Response(200, json={"content": conteudo})
        if request.url.path == "/products/P1":
            return httpx.Response(404, json={"message": "not found"})
        return _handler_ok(request)

    with _collector(handler) as c:
        itens = list(c.collect())

    assert [i["source_product_id"] for i in itens] == ["P2"]
    assert c.errors == 1


def test_falha_de_token_no_meio_da_coleta_interrompe_com_motivo():
    class AuthFalhaNaTerceiraChamada(_AuthFalso):
        def __init__(self):
            super().__init__()
            self.chamadas = 0

        def access_token(self):
            self.chamadas += 1
            if self.chamadas == 3:
                raise ml_mod.MLAuthError("token indisponível durante as ofertas")
            return super().access_token()

    auth = AuthFalhaNaTerceiraChamada()
    with _com_auth(_handler_ok, auth) as c:
        assert list(c.collect()) == []

    assert c.errors == 1
    assert c.auth_error == "token indisponível durante as ofertas"


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


def test_produto_leva_a_posicao_do_highlights_e_ignora_o_que_nao_e_catalogo():
    # TIE-14: posição relativa à categoria, na ordem de /highlights. Desde 2026-10
    # o highlight só traz PRODUCT/USER_PRODUCT; USER_PRODUCT dá 403 em
    # /user-products a token comum, então fica de fora (sem contar erro).
    def handler(request: httpx.Request) -> httpx.Response:
        if "/highlights/" in request.url.path:
            conteudo = [
                {"id": "B", "type": "PRODUCT", "position": 7},
                {"id": "MLBU1", "type": "USER_PRODUCT", "position": 8},
                {"id": "C", "type": "PRODUCT"},
            ]
            return httpx.Response(200, json={"content": conteudo})
        return _handler_ok(request)

    with _collector(handler) as c:
        itens = list(c.collect())

    assert [(i["source_product_id"], i["rank_position"]) for i in itens] == [
        ("B", 7),
        ("C", 3),
    ]
    assert c.errors == 0


def test_respeita_o_maximo_de_produtos_por_categoria():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/highlights/" in request.url.path:
            conteudo = [{"id": f"P{n}", "type": "PRODUCT"} for n in range(5)]
            return httpx.Response(200, json={"content": conteudo})
        return _handler_ok(request)

    c = _collector(handler)
    c.max_items_per_category = 2
    with c:
        assert [i["source_product_id"] for i in c.collect()] == ["P0", "P1"]
