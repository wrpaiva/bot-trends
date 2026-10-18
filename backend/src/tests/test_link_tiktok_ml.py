# src/tests/test_link_tiktok_ml.py
"""
`link_tiktok_ml` (TIE-18): o vídeo comercial do TikTok procura no catálogo do
ML o produto que vende. Cada vídeo é processado uma vez (o resultado fica em
`ml_match`); só `erro` volta a ser tentado. Falso positivo é pior que falso
negativo: sem candidato confiável, não vincula.
"""

import datetime as dt

import mongomock
import pytest

from apps.worker import tasks_link as mod
from src.domain.product_match import Candidato
from src.infrastructure.circuit_breaker import CircuitOpenError
from src.infrastructure.llm.schema import LLMResponseError
from src.infrastructure.utils.datetime_utils import utcnow


class _Extrator:
    respostas: dict[str, object] = {}
    chamadas: list[str] = []

    def extract_product(self, caption):
        _Extrator.chamadas.append(caption)
        r = _Extrator.respostas.get(caption)
        if isinstance(r, Exception):
            raise r
        return r


class _Catalogo:
    resultados: dict[str, list[Candidato] | None] = {}
    dominios: dict[str, str | None] = {}
    falha_dominio = False
    # Onde comprar (TIE-52): catalog_id → oferta, None (saiu do catálogo) ou exceção
    ofertas_por_id: dict[str, object] = {}
    pedidas: list[str] = []

    def __init__(self, **kwargs):
        self.errors = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def buscar_catalogo(self, consulta):
        r = _Catalogo.resultados.get(consulta, [])
        if r is None:
            self.errors += 1
        return r

    def ofertas(self, catalog_id):
        _Catalogo.pedidas.append(catalog_id)
        r = _Catalogo.ofertas_por_id.get(catalog_id, _oferta(catalog_id))
        if isinstance(r, Exception):
            raise r
        return r

    def dominio_sugerido(self, consulta):
        if _Catalogo.falha_dominio:
            self.errors += 1
            return None
        return _Catalogo.dominios.get(consulta)


def _oferta(catalog_id, preco=49.9, n=3):
    return {
        "catalog_id": catalog_id,
        "nome": f"Produto {catalog_id}",
        "permalink": f"https://www.mercadolivre.com.br/p/{catalog_id}",
        "preco_min": preco,
        "preco_buybox": None,
        "moeda": "BRL",
        "n_ofertas": n,
    }


class _Breaker:
    def __init__(self, aberto=False):
        self.aberto = aberto
        self.eventos = []

    def allow(self):
        return not self.aberto

    def record_success(self):
        self.eventos.append("ok")

    def record_failure(self):
        self.eventos.append("falha")

    def status(self):
        return {"state": "open" if self.aberto else "closed", "retry_in_s": 60}


BROCAS = "kit brocas corte lateral. produto indispensável #achadinhos #ferramenta"
BANHEIRO = "Itens Da Shopee Para O Seu Banheiro #achadinhos #shopee"


@pytest.fixture
def db(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True).db
    _Extrator.respostas = {BROCAS: "kit brocas corte lateral", BANHEIRO: None}
    _Extrator.chamadas = []
    _Catalogo.resultados = {
        "kit brocas corte lateral": [
            Candidato("MLB77", "Kit Brocas Corte Lateral E Escalonadas 9 Peças", "MLB-DRILL"),
        ]
    }
    _Catalogo.dominios = {"kit brocas corte lateral": "MLB-DRILL"}
    _Catalogo.falha_dominio = False
    _Catalogo.ofertas_por_id = {}
    _Catalogo.pedidas = []
    monkeypatch.setattr(mod.settings, "ML_CLIENT_ID", "123")
    monkeypatch.setattr(mod.settings, "ML_CLIENT_SECRET", "segredo")
    monkeypatch.setattr(mod.settings, "TIKTOK_ML_MATCH_MIN_SCORE", 0.8)
    monkeypatch.setattr(mod, "get_db", lambda: db)
    monkeypatch.setattr(mod, "_build_extractor", lambda: _Extrator())
    monkeypatch.setattr(mod, "MercadoLivreCollector", _Catalogo)
    monkeypatch.setattr(mod, "_breaker", lambda nome: _Breaker())
    return db


def _video(db, pid, titulo, *, language="pt", shop=False, **extra):
    db["products"].insert_one(
        {
            "product_id": pid,
            "source": "tiktok",
            "source_product_id": f"v-{pid}",
            "title": titulo,
            "language": language,
            "has_shop_product": shop,
            "published_at": utcnow() - dt.timedelta(days=1),
            **extra,
        }
    )


def _match(db, pid):
    return db["products"].find_one({"product_id": pid})["ml_match"]


def test_video_que_nomeia_o_produto_e_vinculado(db):
    _video(db, "v1", BROCAS)

    res = mod.link_tiktok_ml()

    ml = db["products"].find_one({"source": "mercadolivre", "source_product_id": "MLB77"})
    assert ml["title"] == "Kit Brocas Corte Lateral E Escalonadas 9 Peças"
    assert ml["linked_videos"] == ["v1"]
    m = _match(db, "v1")
    assert (m["status"], m["consulta"], m["ml_catalog_id"], m["ml_product_id"]) == (
        "vinculado",
        "kit brocas corte lateral",
        "MLB77",
        ml["product_id"],
    )
    assert m["score"] == 1.0 and m["dominio"] == "MLB-DRILL" and m["prompt_version"] == 1
    assert res == {
        "status": "ok",
        "inserted": 1,
        "errors": 0,
        "processed": 1,
        "linked": 1,
        "no_product": 0,
        "no_candidate": 0,
    }


def test_vinculo_a_produto_ja_coletado_nao_mexe_nos_dados_do_ranking(db):
    # O produto do ranking tem categoria do /highlights: o vínculo não a apaga
    db["products"].insert_one(
        {
            "product_id": "ml-1",
            "source": "mercadolivre",
            "source_product_id": "MLB77",
            "title": "Nome do ranking",
            "category": "MLB1000",
        }
    )
    _video(db, "v1", BROCAS)

    mod.link_tiktok_ml()

    ml = db["products"].find_one({"product_id": "ml-1"})
    assert (ml["title"], ml["category"], ml["linked_videos"]) == (
        "Nome do ranking",
        "MLB1000",
        ["v1"],
    )
    assert _match(db, "v1")["ml_product_id"] == "ml-1"
    assert db["products"].count_documents({"source": "mercadolivre"}) == 1


def test_compilado_sem_produto_nao_busca_no_catalogo(db):
    _video(db, "v1", BANHEIRO)

    res = mod.link_tiktok_ml()

    assert _match(db, "v1")["status"] == "sem_produto"
    assert res["no_product"] == 1 and res["linked"] == 0
    assert db["products"].count_documents({"source": "mercadolivre"}) == 0


def test_candidato_fraco_nao_vincula_e_fica_registrado_para_auditoria(db):
    _video(db, "v1", "Achadinhos para Casa com Cara de Milionária #achadinhos")
    _Extrator.respostas["Achadinhos para Casa com Cara de Milionária #achadinhos"] = (
        "cara milionaria"
    )
    _Catalogo.resultados["cara milionaria"] = [Candidato("MLB5", "Livro - A Milionária")]

    res = mod.link_tiktok_ml()

    m = _match(db, "v1")
    assert (m["status"], m["consulta"], m["melhor_candidato"]) == (
        "sem_candidato",
        "cara milionaria",
        "Livro - A Milionária",
    )
    assert res["no_candidate"] == 1 and res["status"] == "ok"


def test_so_video_comercial_em_portugues_e_sem_processamento_anterior(db):
    _video(db, "pt", BROCAS)
    _video(db, "en", BROCAS, language="en")
    _video(db, "danca", "dança nova #fyp")  # sem marcador comercial
    _video(db, "feito", BROCAS, ml_match={"status": "sem_produto"})

    res = mod.link_tiktok_ml()

    assert res["processed"] == 1
    assert "ml_match" not in db["products"].find_one({"product_id": "en"})
    assert "ml_match" not in db["products"].find_one({"product_id": "danca"})
    assert _match(db, "feito") == {"status": "sem_produto"}


def test_erro_volta_a_ser_tentado_no_proximo_ciclo(db):
    _video(db, "v1", BROCAS, ml_match={"status": "erro"})

    mod.link_tiktok_ml()

    assert _match(db, "v1")["status"] == "vinculado"


def test_resposta_invalida_do_llm_marca_erro_e_segue(db):
    _video(db, "v1", BROCAS)
    _video(db, "v2", BANHEIRO)
    _Extrator.respostas[BROCAS] = LLMResponseError("fora do schema")

    res = mod.link_tiktok_ml()

    assert _match(db, "v1")["status"] == "erro"
    assert _match(db, "v2")["status"] == "sem_produto"
    assert (res["status"], res["errors"], res["processed"]) == ("partial", 1, 2)


def test_circuito_do_llm_aberto_para_o_ciclo_sem_marcar_os_videos(db):
    _video(db, "v1", BROCAS)
    _Extrator.respostas[BROCAS] = CircuitOpenError("circuito do LLM aberto")

    res = mod.link_tiktok_ml()

    assert "ml_match" not in db["products"].find_one({"product_id": "v1"})
    assert res["status"] == "circuit_open" and res["service"] == "llm"


def test_falha_na_busca_marca_erro(db):
    _video(db, "v1", BROCAS)
    _Catalogo.resultados["kit brocas corte lateral"] = None

    res = mod.link_tiktok_ml()

    assert _match(db, "v1")["status"] == "erro"
    assert res["status"] == "error" and res["errors"] == 1


def test_falha_no_dominio_marca_erro_em_vez_de_vincular_sem_filtro(db):
    # Sem o domínio a regra fica menos conservadora: melhor tentar de novo
    _video(db, "v1", BROCAS)
    _Catalogo.falha_dominio = True

    mod.link_tiktok_ml()

    assert _match(db, "v1")["status"] == "erro"


def test_teto_de_videos_por_ciclo(db, monkeypatch):
    monkeypatch.setattr(mod.settings, "TIKTOK_ML_LINK_MAX_PER_RUN", 1)
    _video(db, "v1", BROCAS)
    _video(db, "v2", BROCAS)

    res = mod.link_tiktok_ml()

    assert res["processed"] == 1 and len(_Extrator.chamadas) == 1


def test_circuito_do_ml_aberto_nem_comeca(db, monkeypatch):
    monkeypatch.setattr(mod, "_breaker", lambda nome: _Breaker(aberto=nome == "mercadolivre"))
    _video(db, "v1", BROCAS)

    res = mod.link_tiktok_ml()

    assert res["status"] == "circuit_open" and _Extrator.chamadas == []


def test_sem_llm_configurado_devolve_erro_com_motivo(db, monkeypatch):
    def sem_llm():
        raise RuntimeError("LLM_BASE_URL e LLM_API_KEY devem estar definidos.")

    monkeypatch.setattr(mod, "_build_extractor", sem_llm)
    _video(db, "v1", BROCAS)

    res = mod.link_tiktok_ml()

    assert res["status"] == "error" and "LLM_BASE_URL" in res["reason"]
    assert "ml_match" not in db["products"].find_one({"product_id": "v1"})


def test_sem_video_pendente_nao_constroi_nada(db, monkeypatch):
    monkeypatch.setattr(mod, "_build_extractor", lambda: pytest.fail("não devia"))

    assert mod.link_tiktok_ml() == {
        "status": "ok",
        "inserted": 0,
        "errors": 0,
        "processed": 0,
        "linked": 0,
        "no_product": 0,
        "no_candidate": 0,
    }


# --- onde comprar o produto vinculado (TIE-52) ----------------------------------


def _ml(db, pid, catalog_id, videos=("v1",)):
    db["products"].insert_one(
        {
            "product_id": pid,
            "source": "mercadolivre",
            "source_product_id": catalog_id,
            "title": f"Produto {catalog_id}",
            "linked_videos": list(videos),
        }
    )


def test_vinculo_novo_ja_grava_onde_comprar(db):
    _video(db, "v1", BROCAS)
    mod.link_tiktok_ml()

    ml_pid = _match(db, "v1")["ml_product_id"]
    ofertas = db["products"].find_one({"product_id": ml_pid})["ml_offers"]
    assert ofertas["disponivel"] is True
    assert (ofertas["preco_min"], ofertas["n_ofertas"]) == (49.9, 3)
    assert ofertas["permalink"].endswith("/MLB77")
    # Não vira leitura: o produto não entra no ranking nem no percentil do ML
    assert db["metrics"].count_documents({}) == 0


def test_falha_nas_ofertas_nao_desfaz_o_vinculo(db):
    _video(db, "v1", BROCAS)
    _Catalogo.ofertas_por_id = {"MLB77": RuntimeError("500")}
    r = mod.link_tiktok_ml()

    assert r["linked"] == 1 and r["errors"] == 0
    assert _match(db, "v1")["status"] == "vinculado"
    ml_pid = _match(db, "v1")["ml_product_id"]
    assert "ml_offers" not in db["products"].find_one({"product_id": ml_pid})


def test_renovacao_atualiza_so_produto_com_video_vinculado(db):
    _ml(db, "p1", "MLB1")
    _ml(db, "p2", "MLB2", videos=())
    _Catalogo.ofertas_por_id = {"MLB1": _oferta("MLB1", preco=10.0)}

    r = mod.refresh_ml_offers()

    assert r["status"] == "ok" and r["inserted"] == 1
    assert _Catalogo.pedidas == ["MLB1"]
    assert db["products"].find_one({"product_id": "p1"})["ml_offers"]["preco_min"] == 10.0
    assert "ml_offers" not in db["products"].find_one({"product_id": "p2"})


def test_produto_que_saiu_do_catalogo_fica_indisponivel(db):
    _ml(db, "p1", "MLB1")
    _Catalogo.ofertas_por_id = {"MLB1": None}
    mod.refresh_ml_offers()
    ofertas = db["products"].find_one({"product_id": "p1"})["ml_offers"]
    assert ofertas["disponivel"] is False and ofertas["catalog_id"] == "MLB1"


def test_renovacao_com_falha_em_um_produto_segue_com_os_outros(db):
    _ml(db, "p1", "MLB1")
    _ml(db, "p2", "MLB2")
    _Catalogo.ofertas_por_id = {"MLB1": RuntimeError("500")}
    r = mod.refresh_ml_offers()
    assert (r["status"], r["inserted"], r["errors"]) == ("partial", 1, 1)
    assert "ml_offers" in db["products"].find_one({"product_id": "p2"})


def test_renovacao_sem_token_para_com_motivo(db):
    _ml(db, "p1", "MLB1")
    _Catalogo.ofertas_por_id = {"MLB1": mod.MLAuthError("nenhum token gravado")}
    r = mod.refresh_ml_offers()
    assert r["status"] == "error" and "nenhum token" in r["reason"]


def test_renovacao_sem_vinculo_nao_chama_o_ml(db, monkeypatch):
    def falha(*_, **__):
        raise AssertionError("não devia construir o cliente")

    monkeypatch.setattr(mod, "MercadoLivreCollector", falha)
    assert mod.refresh_ml_offers()["inserted"] == 0


def test_renovacao_com_circuito_aberto_nem_comeca(db, monkeypatch):
    _ml(db, "p1", "MLB1")
    monkeypatch.setattr(mod, "_breaker", lambda nome: _Breaker(aberto=True))
    assert mod.refresh_ml_offers()["status"] == "circuit_open"
    assert _Catalogo.pedidas == []
