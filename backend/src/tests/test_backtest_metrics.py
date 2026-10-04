# src/tests/test_backtest_metrics.py
"""Métricas do backtest (TIE-23): correlação de ranking e precisão no top-k."""

import pytest

from src.domain.backtest import precision_at_k, spearman, summarize


def test_spearman_ordem_identica_e_inversa():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_spearman_so_olha_a_ordem():
    # Escala diferente, mesma ordem
    assert spearman([1, 2, 3], [1, 100, 10_000]) == pytest.approx(1.0)


def test_spearman_com_empates_usa_posto_medio():
    # Postos de x: 1.5, 1.5, 3; de y: 1, 2, 3 → Pearson dos postos
    assert spearman([5, 5, 9], [1, 2, 3]) == pytest.approx(0.8660254, abs=1e-6)


@pytest.mark.parametrize("xs, ys", [([1, 2], [3, 4]), ([], []), ([1, 1, 1], [1, 2, 3])])
def test_spearman_indefinido_vira_none(xs, ys):
    # Menos de 3 pontos não diz nada; variância zero não tem correlação
    assert spearman(xs, ys) is None


def test_spearman_exige_listas_do_mesmo_tamanho():
    with pytest.raises(ValueError):
        spearman([1, 2, 3], [1, 2])


def test_precision_at_k_conta_acertos_no_topo():
    previsto = {"a": 9, "b": 8, "c": 1, "d": 0}
    real = {"a": 100, "c": 90, "b": 1, "d": 0}
    # top-2 previsto {a, b}; top-2 real {a, c} → 1 de 2
    assert precision_at_k(previsto, real, k=2) == pytest.approx(0.5)


def test_precision_at_k_limita_k_ao_tamanho_do_pool():
    assert precision_at_k({"a": 1, "b": 2}, {"a": 1, "b": 2}, k=5) == pytest.approx(1.0)


def test_precision_at_k_desempata_de_forma_estavel():
    # Empate no previsto: desempata pelo id, não pela ordem do dict
    assert precision_at_k({"b": 1, "a": 1}, {"a": 5, "b": 0}, k=1) == 1.0
    assert precision_at_k({"a": 1, "b": 1}, {"a": 5, "b": 0}, k=1) == 1.0


def test_precision_at_k_sem_ids_em_comum_falha():
    with pytest.raises(ValueError):
        precision_at_k({"a": 1}, {"b": 1}, k=1)


def test_summarize_media_ignorando_none():
    assert summarize([0.5, None, 0.1]) == {"media": pytest.approx(0.3), "n": 2}
    assert summarize([None]) == {"media": None, "n": 0}
