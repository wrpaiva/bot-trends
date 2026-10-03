# src/tests/test_prompt_builder.py
"""
Prompt do LLM (TIE-26).

Em 2026-10-01, nos 20 primeiros insights reais com LLM, o `llm_score` ficou a
±0,4 do score numérico em todos: o prompt mandava `numeric_score.score_0_100`
pronto e o modelo se ancorava nele. Estes testes garantem que o LLM recebe os
sinais e a referência do grupo para formar opinião própria, nunca a nota já
calculada.
"""

import dataclasses
import json

import pytest

from src.application.prompt_builder import FEW_SHOT, build_trend_prompt
from src.domain.percentile import Normalization
from src.domain.trend_models import TrendInput
from src.infrastructure.llm.schema import LLMResponse

CLASSES = {"ESTAVEL", "SUBINDO", "VIRALIZANDO", "PICO_TEMPORARIO", "EM_QUEDA"}


_BASE = TrendInput(
    product_id="p1",
    title="Mini processador elétrico, link na bio",
    category=None,
    price=None,
    sold_quantity=None,
    views_24h=120_000,
    engagement_24h=9_000,
    mentions_24h=0,
    rank_momentum=0.0,
    reviews_velocity=0.0,
    social_velocity=0.4,
    price_volatility=0.0,
    previous_final_score=61.5,
    age_hours=40.0,
    views_per_hour=3_000.0,
    engagement_per_hour=225.0,
    has_shop_product=False,
    commercial_marker="link_bio",
    n_readings=3,
)


def _ti(**kw):
    return dataclasses.replace(_BASE, **kw)


def _norm():
    return Normalization(
        values={"views_per_hour": 0.8, "engagement_per_hour": 0.7, "social_velocity": 0.9},
        basis="global",
        pool_size=20,
        reference={
            "views_per_hour": {"mediana": 1_200.0, "p90": 6_000.0},
            "engagement_per_hour": {"mediana": 60.0, "p90": 300.0},
            "social_velocity": {"mediana": 0.0, "p90": 0.5},
        },
    )


def _dados(prompt) -> dict:
    """O JSON de dados do produto vem depois do marcador 'Dados:'."""
    return json.loads(prompt["user"].split("Dados:\n", 1)[1])


# --- Sem âncora ---------------------------------------------------------------


def test_prompt_nao_manda_o_score_numerico_pronto():
    prompt = build_trend_prompt(_ti(), _norm())
    texto = prompt["system"] + prompt["user"]
    assert "numeric_score" not in texto
    assert "numeric_score" not in _dados(prompt)


def test_prompt_nao_manda_o_score_final_anterior():
    # O final anterior é ~o numérico anterior: a mesma âncora por outra porta
    prompt = build_trend_prompt(_ti(previous_final_score=61.5), _norm())
    assert "previous_final_score" not in prompt["user"]
    assert "61.5" not in prompt["user"]


def test_prompt_manda_os_sinais_de_ritmo():
    social = _dados(build_trend_prompt(_ti(), _norm()))["social"]
    assert social["views_per_hour"] == 3_000.0
    assert social["engagement_per_hour"] == 225.0
    assert social["social_velocity"] == 0.4
    assert social["age_hours"] == 40.0


def test_prompt_diz_quantas_leituras_existem():
    # social_velocity = 0 com leitura única é "não medido", não "parado"
    assert _dados(build_trend_prompt(_ti(n_readings=1), _norm()))["social"]["leituras"] == 1


# --- Referência do grupo --------------------------------------------------------


def test_prompt_traz_a_referencia_do_grupo():
    ref = _dados(build_trend_prompt(_ti(), _norm()))["referencia"]
    assert ref["base"] == "global"
    assert ref["produtos_no_grupo"] == 20
    assert ref["views_per_hour"] == {"mediana": 1_200.0, "p90": 6_000.0}
    assert ref["percentil_do_produto"]["social_velocity"] == 0.9


def test_sem_normalizacao_a_referencia_vem_vazia():
    # Normalização absoluta (poucos produtos no ciclo): não há com quem comparar
    assert _dados(build_trend_prompt(_ti(), None))["referencia"] is None


# --- Intenção comercial ---------------------------------------------------------


def test_prompt_diz_por_que_o_video_conta_como_produto():
    # Sem isso o LLM dizia "não há produto à venda" num vídeo com link na bio
    dados = _dados(build_trend_prompt(_ti(commercial_marker="link_bio"), _norm()))
    assert dados["produto"]["marcador_comercial"] == "link_bio"


# --- Few-shot -------------------------------------------------------------------


def test_few_shot_cobre_as_cinco_classificacoes():
    assert {ex["saida"]["trend_classification"] for ex in FEW_SHOT} == CLASSES


@pytest.mark.parametrize("exemplo", FEW_SHOT, ids=lambda e: e["saida"]["trend_classification"])
def test_cada_exemplo_respeita_o_schema_de_resposta(exemplo):
    # Exemplo fora do contrato ensina o modelo a errar o formato
    LLMResponse.model_validate(exemplo["saida"])


@pytest.mark.parametrize("exemplo", FEW_SHOT, ids=lambda e: e["saida"]["trend_classification"])
def test_exemplos_nao_trazem_score_pronto(exemplo):
    assert "numeric_score" not in json.dumps(exemplo["entrada"])


def test_exemplos_vao_no_prompt():
    prompt = build_trend_prompt(_ti(), _norm())
    for ex in FEW_SHOT:
        assert json.dumps(ex["saida"], ensure_ascii=False) in prompt["system"]


def _exemplos(classe):
    return [ex for ex in FEW_SHOT if ex["saida"]["trend_classification"] == classe]


def test_exemplo_de_queda_eh_de_video_que_ja_teve_tempo_de_perder_atencao():
    # Na v3 o modelo marcou EM_QUEDA um vídeo de 5 h com 17 views/h: queda
    # exige atenção que se perdeu, não ausência de atenção
    assert all(ex["entrada"]["social"]["age_hours"] >= 72 for ex in _exemplos("EM_QUEDA"))


def test_ha_exemplo_de_video_novo_sem_tracao_que_nao_eh_queda():
    def sem_tracao_e_novo(ex):
        s, ref = ex["entrada"]["social"], ex["entrada"]["referencia"]
        return s["age_hours"] < 48 and s["views_per_hour"] < ref["views_per_hour"]["mediana"]

    novos = [ex for ex in FEW_SHOT if sem_tracao_e_novo(ex)]
    assert novos, "falta exemplo de vídeo novo sem tração"
    assert {ex["saida"]["trend_classification"] for ex in novos} == {"ESTAVEL"}
