# src/tests/test_llm_schema.py
"""
Validação da resposta do LLM (TIE-24).

Antes, o cliente lia `obj["..."]` direto: um LLM alucinando `"MUITO_ALTO"` ou
score 150 entrava no banco, e campo faltando virava KeyError cru.
"""

import logging

import pytest

from src.application.trend_engine import HybridTrendEngine
from src.domain.interfaces import LLMClient
from src.domain.scoring import NumericScoreStrategy
from src.domain.trend_models import LLMResult, TrendInput
from src.infrastructure.llm.schema import LLMResponseError, parse_llm_content

VALIDO = {
    "trend_classification": "SUBINDO",
    "potential_score_0_100": 72,
    "risk_level": "MEDIO",
    "analysis": "Engajamento crescente.",
    "recommendation": "Monitorar estoque.",
    "confidence_0_1": 0.8,
}


def _com(**kw) -> dict:
    return {**VALIDO, **kw}


def _sem(campo: str) -> dict:
    return {k: v for k, v in VALIDO.items() if k != campo}


def test_resposta_valida():
    assert parse_llm_content(VALIDO) == LLMResult(
        trend_classification="SUBINDO",
        potential_score_0_100=72.0,
        risk_level="MEDIO",
        analysis="Engajamento crescente.",
        recommendation="Monitorar estoque.",
        confidence_0_1=0.8,
    )


def test_aceita_json_em_texto_e_em_bloco_markdown():
    import json

    texto = json.dumps(VALIDO)
    assert parse_llm_content(texto).potential_score_0_100 == 72.0
    assert parse_llm_content(f"```json\n{texto}\n```").risk_level == "MEDIO"


@pytest.mark.parametrize(
    ("bruto", "esperado"), [(" subindo ", "SUBINDO"), ("Em_Queda", "EM_QUEDA")]
)
def test_normaliza_caixa_e_espacos_da_classificacao(bruto, esperado):
    assert parse_llm_content(_com(trend_classification=bruto)).trend_classification == esperado


def test_normaliza_risco():
    assert parse_llm_content(_com(risk_level="alto")).risk_level == "ALTO"


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("trend_classification", "MUITO_ALTO"),
        ("risk_level", "EXTREMO"),
        ("potential_score_0_100", "alto"),
        ("potential_score_0_100", float("nan")),
        ("analysis", "   "),
        ("recommendation", None),
    ],
)
def test_valor_fora_do_dominio_eh_rejeitado(campo, valor):
    with pytest.raises(LLMResponseError, match=campo):
        parse_llm_content(_com(**{campo: valor}))


@pytest.mark.parametrize(
    ("valor", "esperado"), [(150, 100.0), (-3, 0.0), (100, 100.0), ("64.5", 64.5)]
)
def test_score_eh_clampado_em_0_100(valor, esperado):
    assert parse_llm_content(_com(potential_score_0_100=valor)).potential_score_0_100 == esperado


@pytest.mark.parametrize(("valor", "esperado"), [(1.7, 1.0), (-0.2, 0.0)])
def test_confidence_eh_clampada_em_0_1(valor, esperado):
    assert parse_llm_content(_com(confidence_0_1=valor)).confidence_0_1 == esperado


def test_confidence_ausente_usa_default():
    assert parse_llm_content(_sem("confidence_0_1")).confidence_0_1 == 0.6


@pytest.mark.parametrize(
    "campo", ["trend_classification", "potential_score_0_100", "risk_level", "analysis"]
)
def test_campo_obrigatorio_faltando(campo):
    with pytest.raises(LLMResponseError, match=campo):
        parse_llm_content(_sem(campo))


@pytest.mark.parametrize("bruto", ["isto não é json", "[1, 2]", "", '{"a": '])
def test_json_malformado_ou_que_nao_eh_objeto(bruto):
    with pytest.raises(LLMResponseError):
        parse_llm_content(bruto)


def test_campo_extra_eh_ignorado():
    assert parse_llm_content(_com(sentimento="positivo")).risk_level == "MEDIO"


def test_erro_eh_valueerror():
    # Quem só conhece ValueError continua pegando
    assert issubclass(LLMResponseError, ValueError)


# --- Engine: resposta inválida vira fallback com WARNING --------------------


class _LLMInvalido(LLMClient):
    def analyze_trend(self, system: str, user: str) -> LLMResult:
        return parse_llm_content(_com(trend_classification="MUITO_ALTO"))


def _input() -> TrendInput:
    return TrendInput(
        product_id="uuid-1",
        title="Produto",
        category=None,
        price=10.0,
        sold_quantity=None,
        views_24h=1000,
        engagement_24h=100,
        mentions_24h=1,
        rank_momentum=0.0,
        reviews_velocity=0.0,
        social_velocity=0.1,
        price_volatility=0.0,
    )


def test_resposta_invalida_cai_no_fallback_com_warning(caplog):
    engine = HybridTrendEngine(NumericScoreStrategy(), _LLMInvalido())

    with caplog.at_level(logging.WARNING, logger="src.application.trend_engine"):
        res = engine.run(_input())

    assert res.llm_score_0_100 == 0.0
    assert res.final_score_0_100 == res.numeric_score_0_100
    assert "trend_classification" in res.debug["llm_error"]
    (registro,) = (r for r in caplog.records if r.levelno == logging.WARNING)
    assert "uuid-1" in registro.getMessage()
