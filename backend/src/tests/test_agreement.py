# src/tests/test_agreement.py
"""Concordância modelo × julgamento humano (TIE-26)."""

import pytest

from src.domain.agreement import acerto, cohen_kappa, moda_e_estabilidade


def test_acerto_e_a_fracao_de_rotulos_iguais():
    assert acerto(["A", "B", "C", "A"], ["A", "B", "A", "A"]) == pytest.approx(0.75)


def test_kappa_concordancia_perfeita_e_1():
    assert cohen_kappa(["A", "B", "A"], ["A", "B", "A"]) == pytest.approx(1.0)


def test_kappa_desconta_o_acaso():
    # Todos chutam "A": acerto alto, mas nenhuma informação além do acaso
    humano = ["A", "A", "A", "B"]
    modelo = ["A", "A", "A", "A"]
    assert acerto(humano, modelo) == pytest.approx(0.75)
    assert cohen_kappa(humano, modelo) == pytest.approx(0.0)


def test_kappa_valor_conhecido():
    # Tabela 2×2 clássica: po = 0.7, pe = 0.5 → kappa = 0.4
    a = ["S"] * 5 + ["N"] * 5
    b = ["S"] * 4 + ["N"] + ["S"] * 2 + ["N"] * 3
    assert cohen_kappa(a, b) == pytest.approx(0.4)


@pytest.mark.parametrize("a, b", [([], []), (["A", "A"], ["A", "A"])])
def test_kappa_indefinido_vira_none(a, b):
    # Sem amostra, ou uma categoria só nos dois (pe = 1): kappa não existe
    assert cohen_kappa(a, b) is None


def test_listas_de_tamanhos_diferentes_falham():
    with pytest.raises(ValueError):
        acerto(["A"], ["A", "B"])
    with pytest.raises(ValueError):
        cohen_kappa(["A"], ["A", "B"])


def test_moda_e_estabilidade():
    # 2 de 3 repetições deram SUBINDO
    assert moda_e_estabilidade(["SUBINDO", "ESTAVEL", "SUBINDO"]) == (
        "SUBINDO",
        pytest.approx(2 / 3),
    )
    assert moda_e_estabilidade([]) == (None, None)
