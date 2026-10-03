# src/tests/test_hybrid_trend_analyze.py
"""
`hybrid_trend_analyze` com alertas deduplicados (TIE-31).

O estado dos alertas mora no Mongo (`alert_state`), então sobrevive a restart
do worker. Falha no Telegram — ou Telegram/LLM sem config — não pode derrubar
a análise dos outros produtos.
"""

import datetime as dt

import mongomock
import pytest

from apps.worker import tasks_trend as mod
from src.domain.trend_models import LLMResult
from src.infrastructure.circuit_breaker import CircuitBreaker, MemoryBreakerStore
from src.infrastructure.telegram.notifier import TelegramError
from src.infrastructure.utils.datetime_utils import utcnow


class _LLM:
    classificacao = "SUBINDO"
    prompts: list[str] = []

    def __init__(self, *a, **kw):
        pass

    def analyze_trend(self, system, user):
        _LLM.prompts.append(user)
        return LLMResult(
            trend_classification=_LLM.classificacao,
            potential_score_0_100=100.0,
            risk_level="MEDIO",
            analysis="a",
            recommendation="r",
            confidence_0_1=0.9,
        )


class _Telegram:
    enviados: list[str] = []
    falhar = False

    def __init__(self, *a, **kw):
        pass

    def send(self, text):
        if _Telegram.falhar:
            raise TelegramError("Telegram respondeu HTTP 500")
        _Telegram.enviados.append(text)


@pytest.fixture
def db(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True).db
    agora = utcnow()
    for pid in ("p1", "p2"):
        db["products"].insert_one({"product_id": pid, "title": f"Produto {pid}"})
        db["metrics"].insert_one(
            {"product_id": pid, "ts": agora - dt.timedelta(hours=1), "source": "tiktok"}
        )

    _LLM.classificacao = "SUBINDO"
    _LLM.prompts = []
    _Telegram.enviados = []
    _Telegram.falhar = False
    monkeypatch.setattr(mod, "get_db", lambda: db)
    monkeypatch.setattr(mod, "OpenAICompatibleLLMClient", _LLM)
    monkeypatch.setattr(mod, "TelegramNotifier", _Telegram)
    # Sem Redis nos testes; o teste de cache injeta um em memória
    monkeypatch.setattr(mod, "_build_llm_cache", lambda: None)
    monkeypatch.setattr(mod, "breaker_for", lambda s: CircuitBreaker(s, MemoryBreakerStore()))
    # O LLM dá 100; com peso 0.4 o final passa de 40 — limiar baixo o bastante
    monkeypatch.setattr(mod.settings, "ALERT_THRESHOLD", 40.0)
    monkeypatch.setattr(mod.settings, "ALERT_COOLDOWN_HOURS", 24)
    return db


def test_alerta_uma_vez_por_produto_e_suprime_repeticao(db):
    primeira = mod.hybrid_trend_analyze()
    segunda = mod.hybrid_trend_analyze()

    assert len(_Telegram.enviados) == 2  # um por produto, só na primeira rodada
    assert primeira["alerts_sent"] == 2
    assert segunda["alerts_sent"] == 0 and segunda["alerts_suppressed"] == 2
    assert db["trend_insights"].count_documents({}) == 4


def test_estado_persistido_no_mongo(db):
    mod.hybrid_trend_analyze()
    estado = db["alert_state"].find_one({"product_id": "p1"})
    assert estado["classification"] == "SUBINDO"
    assert estado["alerted_at"].tzinfo is not None


def test_realerta_quando_sobe_de_faixa(db):
    mod.hybrid_trend_analyze()
    _LLM.classificacao = "VIRALIZANDO"

    res = mod.hybrid_trend_analyze()

    assert res["alerts_sent"] == 2
    assert "VIRALIZANDO" in _Telegram.enviados[-1]


def test_realerta_depois_do_cooldown(db):
    mod.hybrid_trend_analyze()
    db["alert_state"].update_many({}, {"$set": {"alerted_at": utcnow() - dt.timedelta(hours=25)}})
    assert mod.hybrid_trend_analyze()["alerts_sent"] == 2


def test_limiar_vem_da_config(db, monkeypatch):
    monkeypatch.setattr(mod.settings, "ALERT_THRESHOLD", 101.0)
    res = mod.hybrid_trend_analyze()
    assert res["alerts_sent"] == 0 and _Telegram.enviados == []


def test_falha_no_envio_nao_derruba_a_analise_e_tenta_de_novo(db):
    _Telegram.falhar = True
    res = mod.hybrid_trend_analyze()

    assert res["processed"] == 2
    assert res["alerts_failed"] == 2
    assert db["trend_insights"].count_documents({}) == 2
    # Não gravou estado: o próximo ciclo tenta de novo
    assert db["alert_state"].count_documents({}) == 0

    _Telegram.falhar = False
    assert mod.hybrid_trend_analyze()["alerts_sent"] == 2


def test_sem_telegram_configurado_a_analise_roda(db, monkeypatch):
    def _sem_config(*a, **kw):
        raise RuntimeError("TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID devem estar definidos.")

    monkeypatch.setattr(mod, "TelegramNotifier", _sem_config)
    res = mod.hybrid_trend_analyze()

    assert res["processed"] == 2
    assert res["alerts_sent"] == 0
    assert db["trend_insights"].count_documents({}) == 2


def test_sem_llm_configurado_cai_no_score_numerico(db, monkeypatch):
    def _sem_config(*a, **kw):
        raise RuntimeError("LLM_BASE_URL e LLM_API_KEY devem estar definidos.")

    monkeypatch.setattr(mod, "OpenAICompatibleLLMClient", _sem_config)
    res = mod.hybrid_trend_analyze()

    assert res["processed"] == 2
    insight = db["trend_insights"].find_one()
    assert insight["llm_score"] == 0.0
    assert "LLM" in insight["debug"]["llm_error"]


def test_insight_registra_os_pesos_usados(db, monkeypatch):
    monkeypatch.setattr(mod.settings, "SCORE_W_NUMERIC", 0.7)
    monkeypatch.setattr(mod.settings, "SCORE_W_LLM", 0.3)
    mod.hybrid_trend_analyze()

    pesos = db["trend_insights"].find_one()["score_weights"]
    assert pesos["hybrid"] == {"numeric": 0.7, "llm": 0.3}
    assert pesos["numeric"]["social"] == 0.25


# --- Normalização por percentil (TIE-21) ------------------------------------


def _normalizacao(db) -> set[str]:
    return {i["debug"]["numeric_components"]["normalization"] for i in db["trend_insights"].find()}


def test_percentil_na_categoria_quando_ha_produtos_suficientes(db, monkeypatch):
    monkeypatch.setattr(mod.settings, "SCORE_NORMALIZATION", "percentile")
    monkeypatch.setattr(mod.settings, "SCORE_PERCENTILE_MIN_GROUP", 2)
    mod.hybrid_trend_analyze()
    assert _normalizacao(db) == {"categoria"}


def test_poucos_produtos_voltam_ao_absoluto(db, monkeypatch):
    monkeypatch.setattr(mod.settings, "SCORE_NORMALIZATION", "percentile")
    monkeypatch.setattr(mod.settings, "SCORE_PERCENTILE_MIN_GROUP", 5)
    mod.hybrid_trend_analyze()
    assert _normalizacao(db) == {"absoluta"}


def test_normalizacao_absoluta_por_config(db, monkeypatch):
    monkeypatch.setattr(mod.settings, "SCORE_NORMALIZATION", "absolute")
    monkeypatch.setattr(mod.settings, "SCORE_PERCENTILE_MIN_GROUP", 2)
    mod.hybrid_trend_analyze()
    assert _normalizacao(db) == {"absoluta"}


# --- Cache do LLM (TIE-25) ----------------------------------------------------


class _CacheMemoria:
    def __init__(self):
        self.dados = {}

    def get(self, key):
        return self.dados.get(key)

    def set(self, key, value):
        self.dados[key] = value


def test_segunda_rodada_com_metricas_paradas_usa_o_cache(db, monkeypatch):
    cache = _CacheMemoria()
    monkeypatch.setattr(mod, "_build_llm_cache", lambda: cache)

    primeira = mod.hybrid_trend_analyze()
    segunda = mod.hybrid_trend_analyze()

    assert (primeira["llm_cache_hits"], primeira["llm_cache_misses"]) == (0, 2)
    assert (segunda["llm_cache_hits"], segunda["llm_cache_misses"]) == (2, 0)


def test_ttl_zero_desliga_o_cache(monkeypatch):
    monkeypatch.setattr(mod.settings, "LLM_CACHE_TTL_HOURS", 0)
    assert mod._build_llm_cache() is None


# --- Circuit breaker do LLM (TIE-37) -------------------------------------------


def test_llm_com_circuito_aberto_nao_impede_a_analise(db, monkeypatch):
    chamadas = []

    class _LLMQueNaoDeviaSerChamado(_LLM):
        def analyze_trend(self, system, user):
            chamadas.append(1)
            return super().analyze_trend(system, user)

    store = MemoryBreakerStore()
    aberto = CircuitBreaker("llm", store, threshold=1, cooldown_s=3600)
    aberto.record_failure()
    monkeypatch.setattr(mod, "OpenAICompatibleLLMClient", _LLMQueNaoDeviaSerChamado)
    monkeypatch.setattr(mod, "breaker_for", lambda s: CircuitBreaker(s, store, cooldown_s=3600))

    res = mod.hybrid_trend_analyze()

    assert res["processed"] == 2 and chamadas == []
    insight = db["trend_insights"].find_one()
    assert insight["llm_score"] == 0.0
    assert "circuito do LLM aberto" in insight["debug"]["llm_error"]


# --- Motor de tendência: idade do vídeo -----------------------------------------


def test_video_mais_velho_que_o_corte_fica_fora_da_analise(db, monkeypatch):
    monkeypatch.setattr(mod.settings, "TREND_MAX_AGE_DAYS", 30)
    db["products"].update_one(
        {"product_id": "p1"}, {"$set": {"published_at": utcnow() - dt.timedelta(days=40)}}
    )
    db["products"].update_one(
        {"product_id": "p2"}, {"$set": {"published_at": utcnow() - dt.timedelta(days=2)}}
    )

    res = mod.hybrid_trend_analyze()

    assert res["skipped_too_old"] == 1
    assert [i["product_id"] for i in db["trend_insights"].find()] == ["p2"]
    comps = db["trend_insights"].find_one()["debug"]["numeric_components"]
    assert comps["view_signal"] == "views_per_hour"


def test_video_sem_data_segue_no_calculo_antigo(db):
    mod.hybrid_trend_analyze()
    comps = db["trend_insights"].find_one()["debug"]["numeric_components"]
    assert comps["view_signal"] == "total"


def test_insight_grava_idade_e_ritmo(db):
    db["products"].update_many({}, {"$set": {"published_at": utcnow() - dt.timedelta(hours=10)}})
    db["metrics"].update_many({}, {"$set": {"views": 5000, "engagement": 500}})
    mod.hybrid_trend_analyze()
    sinais = db["trend_insights"].find_one()["signals"]
    # Idade NA LEITURA: métrica de 1 h atrás, vídeo publicado há 10 h → 9 h
    assert 8.9 <= sinais["age_hours"] <= 9.1
    assert 5000 / 9.1 <= sinais["views_per_hour"] <= 5000 / 8.9


# --- Conteúdo sem produto (TIE-18) ----------------------------------------------


def _tiktok(db, pid, title, **campos):
    db["products"].update_one(
        {"product_id": pid}, {"$set": {"source": "tiktok", "title": title, **campos}}
    )


def test_video_sem_intencao_comercial_fica_fora_da_analise(db):
    _tiktok(db, "p1", "eo tiktok fyp #foryou")
    _tiktok(db, "p2", "Achados da Shopee para a cozinha, link na bio")

    res = mod.hybrid_trend_analyze()

    assert res["skipped_no_product"] == 1
    assert [i["product_id"] for i in db["trend_insights"].find()] == ["p2"]
    assert db["trend_insights"].find_one()["commercial_marker"] == "achados"


def test_produto_da_loja_do_tiktok_passa_mesmo_sem_texto_de_venda(db):
    _tiktok(db, "p1", "#fyp #viral", has_shop_product=True)
    _tiktok(db, "p2", "#fyp #viral")

    mod.hybrid_trend_analyze()

    insight = db["trend_insights"].find_one()
    assert insight["product_id"] == "p1"
    assert insight["commercial_marker"] == "tiktok_shop"


def test_filtro_nao_se_aplica_a_item_de_marketplace(db):
    db["products"].update_many({}, {"$set": {"source": "mercadolivre", "title": "Fone"}})
    res = mod.hybrid_trend_analyze()
    assert res["skipped_no_product"] == 0
    assert db["trend_insights"].count_documents({"commercial_marker": None}) == 2


def test_filtro_desligado_pela_config_pontua_tudo(db, monkeypatch):
    monkeypatch.setattr(mod.settings, "TREND_REQUIRE_COMMERCIAL", False)
    _tiktok(db, "p1", "eo tiktok fyp")
    _tiktok(db, "p2", "dança nova")

    res = mod.hybrid_trend_analyze()

    assert res["skipped_no_product"] == 0
    assert db["trend_insights"].count_documents({}) == 2


# --- Prompt (TIE-26) -----------------------------------------------------------


def test_insight_registra_a_versao_do_prompt(db):
    from src.domain.llm_cache import PROMPT_VERSION

    mod.hybrid_trend_analyze()

    assert {i["prompt_version"] for i in db["trend_insights"].find()} == {PROMPT_VERSION}


def test_sem_llm_o_insight_nao_tem_versao_de_prompt(db, monkeypatch):
    def _sem_config(*a, **kw):
        raise RuntimeError("LLM_API_KEY ausente")

    monkeypatch.setattr(mod, "OpenAICompatibleLLMClient", _sem_config)
    mod.hybrid_trend_analyze()

    assert {i["prompt_version"] for i in db["trend_insights"].find()} == {None}


def test_marcador_comercial_chega_ao_prompt(db):
    _tiktok(db, "p1", "Achados da Shopee para a cozinha, link na bio")
    _tiktok(db, "p2", "Organizador de gaveta, comenta QUERO")

    mod.hybrid_trend_analyze()

    assert all('"marcador_comercial": "' in p for p in _LLM.prompts)
    assert len(_LLM.prompts) == 2


def test_numero_de_leituras_chega_ao_prompt(db):
    mod.hybrid_trend_analyze()

    # A fixture grava uma métrica por produto
    assert all('"leituras": 1' in p for p in _LLM.prompts)


def test_rank_momentum_vem_das_posicoes_do_ml(db):
    # TIE-16: subiu de 20º para 1º na janela → nm_rank = 1; o TikTok, sem
    # ranking, continua 0
    agora = utcnow()
    db["products"].insert_one({"product_id": "ml1", "title": "Air fryer", "source": "mercadolivre"})
    for horas, pos in ((30, 20), (18, 9), (6, 1)):
        db["metrics"].insert_one(
            {
                "product_id": "ml1",
                "ts": agora - dt.timedelta(hours=horas),
                "source": "mercadolivre",
                "rank_position": pos,
                "price": 300.0,
            }
        )

    mod.hybrid_trend_analyze(hours=72)

    nm_rank = {
        i["product_id"]: i["debug"]["numeric_components"]["nm_rank"]
        for i in db["trend_insights"].find()
    }
    assert nm_rank == {"ml1": pytest.approx(1.0), "p1": 0.0, "p2": 0.0}
