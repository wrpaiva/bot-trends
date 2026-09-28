# src/tests/test_product_dedupe.py
"""
Deduplicação de produtos (TIE-19).

Chave de dedupe: (source, source_product_id), com índice único. Se o upsert
erra, o mesmo item vira dois produtos e a série em `metrics` se parte ao meio
— toda métrica de velocidade passa a calcular sobre histórico incompleto.
"""

import sys

import mongomock
import pytest
from pymongo.errors import DuplicateKeyError

from src.infrastructure.collectors import tiktok as tiktok_mod
from src.infrastructure.collectors.tiktok import TikTokApifyCollector
from src.infrastructure.db.repos import ProductRepo


@pytest.fixture
def repo():
    return ProductRepo(mongomock.MongoClient().db)


def _item(spid="MLB1", source="mercadolivre", **kw):
    return {"source": source, "source_product_id": spid, "canonical_id": spid, "title": "X", **kw}


def test_mesmo_item_em_ciclos_diferentes_nao_cria_produto_novo(repo):
    primeiro = repo.upsert(_item(title="Fone v1"))
    segundo = repo.upsert(_item(title="Fone v2"))

    assert primeiro == segundo
    assert repo.col.count_documents({}) == 1
    assert repo.col.find_one()["title"] == "Fone v2"


def test_mesmo_id_em_fontes_diferentes_sao_produtos_diferentes(repo):
    a = repo.upsert(_item(source="mercadolivre"))
    b = repo.upsert(_item(source="tiktok"))
    assert a != b and repo.col.count_documents({}) == 2


def test_canonical_id_nao_eh_chave_de_dedupe(repo):
    # canonical_id é para casar produtos entre fontes (TIE-18), não para dedupe
    repo.upsert(_item(spid="MLB1", canonical_id="fone-x"))
    repo.upsert(_item(spid="MLB2", canonical_id="fone-x"))
    assert repo.col.count_documents({}) == 2


def test_indice_unico_na_chave_de_dedupe(repo):
    idx = {i["name"]: i for i in repo.col.list_indexes()}
    assert idx["ux_source_sourceProduct"]["unique"] is True
    assert list(idx["ux_source_sourceProduct"]["key"]) == ["source", "source_product_id"]


def test_upsert_devolve_o_product_id_gravado_mesmo_perdendo_a_corrida(repo, monkeypatch):
    """
    Duas coletas veem o mesmo item novo ao mesmo tempo. A que perde a corrida
    não pode devolver um UUID que nunca foi gravado: as métricas dela ficariam
    órfãs e a série temporal se partiria.
    """
    vencedor = repo.upsert(_item())
    original = repo.col.find_one_and_update
    chamadas = {"n": 0}

    def _perde_a_corrida(*args, **kwargs):
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            raise DuplicateKeyError("E11000 corrida simulada")
        return original(*args, **kwargs)

    monkeypatch.setattr(repo.col, "find_one_and_update", _perde_a_corrida)

    assert repo.upsert(_item(title="outra coleta")) == vencedor
    assert repo.col.count_documents({}) == 1


def test_leitura_defasada_nao_gera_uuid_fantasma(repo, monkeypatch):
    """
    A corrida como ela acontece: esta coleta lê "não existe" (a outra ainda
    não gravou), mas quando escreve o documento já está lá. O UUID devolvido
    tem que ser o gravado, não um recém-gerado.
    """
    gravado = repo.upsert(_item())
    original = repo.col.find_one

    def _defasado(*args, **kwargs):
        # Só a leitura feita pelo repo vem defasada; o mongomock usa find_one
        # internamente e precisa do valor real.
        if sys._getframe(1).f_code.co_filename.endswith("db/repos.py"):
            return None
        return original(*args, **kwargs)

    monkeypatch.setattr(repo.col, "find_one", _defasado)

    assert repo.upsert(_item(title="coleta concorrente")) == gravado
    assert repo.col.count_documents({}) == 1


def test_item_sem_chave_eh_recusado(repo):
    with pytest.raises(ValueError, match="source_product_id"):
        repo.upsert(_item(spid=""))


# --- TikTok: o mesmo vídeo não pode entrar com dois ids ---------------------


@pytest.fixture
def normalize(monkeypatch):
    monkeypatch.setattr(tiktok_mod.settings, "APIFY_TOKEN", "x")
    return TikTokApifyCollector(hashtags=["fyp"])._normalize_item


URL = "https://www.tiktok.com/@mara.lazeez/video/7075778590062988546"


def test_video_sem_id_usa_o_id_da_url(normalize):
    com_id = normalize({"id": "7075778590062988546", "webVideoUrl": URL})
    sem_id = normalize({"webVideoUrl": URL})
    assert com_id["source_product_id"] == sem_id["source_product_id"] == "7075778590062988546"
    assert sem_id["canonical_id"] == "7075778590062988546"


def test_url_com_query_string_nao_muda_o_id(normalize):
    assert normalize({"webVideoUrl": URL + "?is_from_webapp=1"})["source_product_id"] == (
        "7075778590062988546"
    )


def test_video_sem_id_nem_url_valida_nao_vira_produto(normalize):
    assert normalize({"text": "sem nada"}) is None


# --- Motor de tendência: data de publicação -----------------------------------


def test_upsert_grava_publicacao_e_loja(repo):
    import datetime as dt

    pub = dt.datetime(2026, 9, 24, 20, 28, 36, tzinfo=dt.UTC)
    repo.upsert(_item(source="tiktok", published_at=pub, has_shop_product=True))
    doc = repo.col.find_one()
    assert doc["published_at"].replace(tzinfo=dt.UTC) == pub
    assert doc["has_shop_product"] is True


def test_coleta_sem_data_nao_apaga_a_data_ja_gravada(repo):
    import datetime as dt

    pub = dt.datetime(2026, 9, 24, tzinfo=dt.UTC)
    repo.upsert(_item(source="tiktok", published_at=pub))
    repo.upsert(_item(source="tiktok", published_at=None))
    assert repo.col.find_one()["published_at"] is not None
