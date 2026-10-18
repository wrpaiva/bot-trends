# src/tests/test_backtest_metrics.py
"""Métricas do backtest (TIE-23): correlação de ranking e precisão no top-k."""

import pytest

from src.domain.backtest import (
    acerto_top_k,
    auc,
    ic_bootstrap_por_bloco,
    precision_at_k,
    spearman,
    summarize,
)


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


def test_acerto_top_k_e_a_fracao_do_top_previsto_com_desfecho_1():
    # Desfecho binário (TIE-46, "entrou no top 5"): o top-k real de um 0/1 é
    # sorteado pelo desempate; o que importa é quantos do top-k previsto deram 1
    previsto = {"a": 9, "b": 8, "c": 1, "d": 0}
    real = {"a": 1, "b": 0, "c": 1, "d": 0}
    assert acerto_top_k(previsto, real, k=2) == pytest.approx(0.5)
    assert acerto_top_k(previsto, real, k=1) == pytest.approx(1.0)


def test_acerto_top_k_desempata_o_previsto_pelo_id():
    assert acerto_top_k({"b": 1, "a": 1}, {"a": 1, "b": 0}, k=1) == 1.0


def test_acerto_top_k_sem_ids_em_comum_falha():
    with pytest.raises(ValueError):
        acerto_top_k({"a": 1}, {"b": 1}, k=1)


# --- AUC (desfecho raro, TIE-23) ------------------------------------------------


def test_auc_separacao_perfeita_e_invertida():
    assert auc([9, 8, 1, 0], [1, 1, 0, 0]) == pytest.approx(1.0)
    assert auc([0, 1, 8, 9], [1, 1, 0, 0]) == pytest.approx(0.0)


def test_auc_empate_vale_meio():
    # Preditor constante não separa nada
    assert auc([5, 5, 5, 5], [1, 0, 0, 1]) == pytest.approx(0.5)


def test_auc_conta_pares_positivo_negativo():
    # Pares (pos, neg): (3,1) ok, (3,4) erro, (2,1) ok, (2,4) erro → 2/4
    assert auc([3, 2, 1, 4], [1, 1, 0, 0]) == pytest.approx(0.5)


@pytest.mark.parametrize("desfecho", [[1, 1, 1], [0, 0, 0], []])
def test_auc_sem_as_duas_classes_e_none(desfecho):
    assert auc([1, 2, 3][: len(desfecho)], desfecho) is None


def test_auc_exige_listas_do_mesmo_tamanho():
    with pytest.raises(ValueError):
        auc([1, 2, 3], [1, 0])


# --- intervalo de confiança por bloco (dia) -------------------------------------


def test_ic_por_bloco_usa_a_media_de_cada_bloco():
    # O dia com 3 cortes não pesa 3×: cada dia vira uma média
    r = ic_bootstrap_por_bloco({"d1": [1.0, 1.0, 1.0], "d2": [3.0]}, reps=500, seed=1)
    assert r["media"] == pytest.approx(2.0)
    assert r["blocos"] == 2
    assert 1.0 <= r["ic_inf"] <= r["media"] <= r["ic_sup"] <= 3.0


def test_ic_de_diferenca_consistente_nao_cruza_o_zero():
    blocos = {f"d{i}": [-0.25 + 0.01 * (i % 3)] for i in range(12)}
    r = ic_bootstrap_por_bloco(blocos, reps=1000, seed=7)
    assert r["ic_sup"] < 0


def test_ic_de_ruido_em_torno_de_zero_cruza_o_zero():
    blocos = {f"d{i}": [(-1) ** i * 0.3] for i in range(12)}
    r = ic_bootstrap_por_bloco(blocos, reps=1000, seed=7)
    assert r["ic_inf"] < 0 < r["ic_sup"]


def test_ic_e_deterministico_com_a_mesma_semente():
    blocos = {"a": [0.1], "b": [0.5], "c": [0.9]}
    assert ic_bootstrap_por_bloco(blocos, seed=3) == ic_bootstrap_por_bloco(blocos, seed=3)


def test_ic_com_menos_de_dois_blocos_e_none():
    assert ic_bootstrap_por_bloco({"d1": [0.2, 0.4]}) is None
    assert ic_bootstrap_por_bloco({"d1": [], "d2": [0.1]}) is None
