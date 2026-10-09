# src/tests/test_llm_cache.py
"""
Cache de chamadas ao LLM (TIE-25).

Antes: uma chamada por produto a cada 30 min (até 2.400/dia) mesmo com as
métricas paradas. Agora o engine reaproveita a análise do LLM quando as
métricas de entrada do produto não mudaram; o score numérico é sempre
recalculado.
"""

import dataclasses
import json
import logging

import pytest

from src.application.trend_engine import HybridTrendEngine
from src.domain.interfaces import LLMCache, LLMClient
from src.domain.llm_cache import llm_cache_key
from src.domain.scoring import NumericScoreStrategy
from src.domain.trend_models import LLMResult, TrendInput
from src.infrastructure.llm.cache import RedisLLMCache

RESULTADO = LLMResult(
    trend_classification="SUBINDO",
    potential_score_0_100=80.0,
    risk_level="MEDIO",
    analysis="a",
    recommendation="r",
    confidence_0_1=0.7,
)


def _ti(**kw) -> TrendInput:
    base = {
        "product_id": "p1",
        "title": "Fone",
        "category": None,
        "price": 10.0,
        "sold_quantity": None,
        "views_24h": 1000,
        "engagement_24h": 100,
        "mentions_24h": 1,
        "rank_momentum": 0.0,
        "reviews_velocity": 0.0,
        "social_velocity": 0.123456789,
        "price_volatility": 0.0,
    }
    return TrendInput(**{**base, **kw})


class _LLM(LLMClient):
    def __init__(self, falhar=False):
        self.chamadas = 0
        self.falhar = falhar

    def analyze_trend(self, system, user):
        self.chamadas += 1
        if self.falhar:
            raise RuntimeError("LLM fora")
        return RESULTADO


class _Cache(LLMCache):
    def __init__(self):
        self.dados: dict[str, LLMResult] = {}

    def get(self, key):
        return self.dados.get(key)

    def set(self, key, value):
        self.dados[key] = value


# --- Chave ------------------------------------------------------------------


def test_mesmas_metricas_mesma_chave():
    assert llm_cache_key(_ti()) == llm_cache_key(_ti())


@pytest.mark.parametrize(
    "mudanca",
    [
        {"views_24h": 1001},
        {"views_per_hour": 55.0},
        {"age_hours": 30.0},
        {"has_shop_product": True},
        {"engagement_24h": 101},
        {"price": 11.0},
        {"title": "Fone 2"},
        {"social_velocity": 0.2},
        {"product_id": "p2"},
        {"commercial_marker": "link_bio"},
        {"n_readings": 2},
        # TIE-42: vão para o prompt do item do ML; subir no ranking tem de chamar o LLM
        {"source": "mercadolivre"},
        {"rank_position": 3},
        {"rank_position_start": 9},
    ],
)
def test_metrica_diferente_chave_diferente(mudanca):
    assert llm_cache_key(_ti()) != llm_cache_key(_ti(**mudanca))


def test_ruido_de_ponto_flutuante_nao_muda_a_chave():
    assert llm_cache_key(_ti(social_velocity=0.123456789)) == llm_cache_key(
        _ti(social_velocity=0.1234567891)
    )


# --- Engine -----------------------------------------------------------------


def _engine(llm, cache):
    return HybridTrendEngine(NumericScoreStrategy(), llm, cache=cache)


def test_metricas_inalteradas_reaproveitam_o_llm():
    llm, cache = _LLM(), _Cache()
    engine = _engine(llm, cache)

    primeiro = engine.run(_ti())
    segundo = engine.run(_ti())

    assert llm.chamadas == 1
    assert segundo.llm_score_0_100 == primeiro.llm_score_0_100 == 80.0
    assert segundo.debug["llm_cached"] is True and primeiro.debug["llm_cached"] is False
    assert (engine.cache_hits, engine.cache_misses) == (1, 1)


def test_metricas_novas_chamam_o_llm_de_novo():
    llm = _LLM()
    engine = _engine(llm, _Cache())
    engine.run(_ti())
    engine.run(_ti(views_24h=5000))
    assert llm.chamadas == 2


def test_score_numerico_sempre_recalculado_no_acerto():
    from src.domain.percentile import Normalization

    engine = _engine(_LLM(), _Cache())
    sem = engine.run(_ti())
    com = engine.run(
        _ti(),
        Normalization(
            {
                "views_per_hour": 1,
                "engagement_per_hour": 1,
                "social_velocity": 1,
                "reviews_velocity": 1,
            },
            "categoria",
        ),
    )
    assert com.debug["llm_cached"] is True
    assert com.numeric_score_0_100 > sem.numeric_score_0_100


def test_falha_do_llm_nao_vai_para_o_cache():
    llm, cache = _LLM(falhar=True), _Cache()
    engine = _engine(llm, cache)
    engine.run(_ti())
    engine.run(_ti())
    assert llm.chamadas == 2 and cache.dados == {}


def test_cache_quebrado_vira_miss_e_nao_derruba_a_analise(caplog):
    class _Quebrado(LLMCache):
        def get(self, key):
            raise ConnectionError("redis fora")

        def set(self, key, value):
            raise ConnectionError("redis fora")

    llm = _LLM()
    with caplog.at_level(logging.WARNING):
        res = _engine(llm, _Quebrado()).run(_ti())
    assert res.llm_score_0_100 == 80.0 and llm.chamadas == 1
    assert "cache" in caplog.text.lower()


def test_sem_cache_funciona_como_antes():
    llm = _LLM()
    engine = HybridTrendEngine(NumericScoreStrategy(), llm)
    engine.run(_ti())
    engine.run(_ti())
    assert llm.chamadas == 2


# --- Redis ------------------------------------------------------------------


class _RedisFalso:
    def __init__(self, falhar=False):
        self.dados: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.falhar = falhar

    def get(self, k):
        if self.falhar:
            raise ConnectionError("fora")
        return self.dados.get(k)

    def setex(self, k, ttl, v):
        if self.falhar:
            raise ConnectionError("fora")
        self.dados[k], self.ttls[k] = v, ttl


def test_redis_guarda_com_ttl_e_prefixo_do_modelo():
    r = _RedisFalso()
    cache = RedisLLMCache(r, model="gpt-x", ttl_s=3600)
    cache.set("abc", RESULTADO)

    (chave,) = r.dados
    assert chave.startswith("llm:") and "gpt-x" in chave and chave.endswith("abc")
    assert r.ttls[chave] == 3600
    assert json.loads(r.dados[chave]) == dataclasses.asdict(RESULTADO)
    assert cache.get("abc") == RESULTADO


def test_modelo_diferente_nao_compartilha_cache():
    r = _RedisFalso()
    RedisLLMCache(r, model="gpt-x", ttl_s=60).set("abc", RESULTADO)
    assert RedisLLMCache(r, model="gpt-y", ttl_s=60).get("abc") is None


def test_valor_corrompido_no_redis_vira_miss():
    r = _RedisFalso()
    cache = RedisLLMCache(r, model="m", ttl_s=60)
    cache.set("abc", RESULTADO)
    (chave,) = r.dados
    r.dados[chave] = "{lixo"
    assert cache.get("abc") is None
