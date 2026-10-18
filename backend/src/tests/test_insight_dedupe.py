# src/tests/test_insight_dedupe.py
"""Regra de insight repetido (TIE-47): só a decisão conta, não o texto."""

import pytest

from src.domain.insight_dedupe import repete


def _ins(**kw):
    base = {
        "final_score": 52.3,
        "numeric_score": 50.0,
        "llm_score": 55.75,
        "trend_classification": "SUBINDO",
        "risk_level": "MEDIO",
        "prompt_version": 5,
        "commercial_marker": None,
        "window_hours": 72,
        "sources": ["mercadolivre"],
        "score_weights": {"numeric": 0.6, "llm": 0.4},
        "debug": {},
        "analysis": "texto A",
        "signals": {"views_per_hour": 10.0},
    }
    return base | kw


def test_sem_anterior_nao_repete():
    assert repete(None, _ins()) is False


def test_mesmos_scores_com_texto_e_sinais_diferentes_repete():
    assert repete(_ins(), _ins(analysis="texto B", signals={"views_per_hour": 11.0}))


def test_diferenca_de_arredondamento_do_float_repete():
    assert repete(_ins(final_score=52.3), _ins(final_score=52.300000000001))


def test_ordem_das_fontes_nao_importa():
    a = _ins(sources=["tiktok", "mercadolivre"])
    assert repete(a, _ins(sources=["mercadolivre", "tiktok"]))


@pytest.mark.parametrize(
    "mudanca",
    [
        {"final_score": 52.4},
        {"numeric_score": 49.0},
        {"llm_score": None},
        {"trend_classification": "ESTAVEL"},
        {"risk_level": "ALTO"},
        {"prompt_version": 6},
        {"commercial_marker": "loja"},
        {"window_hours": 24},
        {"sources": ["mercadolivre", "tiktok"]},
        {"score_weights": {"numeric": 0.7, "llm": 0.3}},
        {"debug": {"llm_error": "429"}},
    ],
)
def test_qualquer_mudanca_na_decisao_nao_repete(mudanca):
    assert repete(_ins(), _ins(**mudanca)) is False
