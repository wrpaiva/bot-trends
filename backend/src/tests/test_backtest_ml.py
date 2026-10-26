"""
Backtest com o ranking do ML (TIE-46): o ML não dá vendas, mas o /highlights é
o ranking de mais vendidos — subir nele é o desfecho observável.
"""

import datetime as dt

import mongomock
import pytest

from apps.backtest import main as bt
from apps.worker import tasks_trend

T0 = dt.datetime(2026, 10, 10, 12, tzinfo=dt.UTC)
# Coleta real: o intervalo de 6 h sai com 5,99 h (2026-10-14)
T1 = T0 + dt.timedelta(hours=5.99)
T2 = T1 + dt.timedelta(hours=5.99)

# Posição em T0 e em T1 (None = saiu do top 20). m5 entra no top 5; m7 cai
POSICOES = {
    "m0": (1, 1),
    "m1": (2, 3),
    "m2": (3, 4),
    "m3": (4, 5),
    "m4": (5, 6),
    "m5": (6, 2),
    "m6": (7, 7),
    "m7": (8, None),
}


def _produto(db, pid, categoria="MLB1"):
    db["products"].insert_one(
        {"product_id": pid, "source": "mercadolivre", "title": f"Item {pid}", "category": categoria}
    )


def _leitura(db, pid, ts, posicao, preco=100.0, categoria="MLB1"):
    db["metrics"].insert_one(
        {
            "product_id": pid,
            "source": "mercadolivre",
            "ts": ts,
            "rank_position": posicao,
            "price": preco,
            "category": categoria,
        }
    )


@pytest.fixture
def db(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True).db
    monkeypatch.setattr(tasks_trend.settings, "SCORE_PERCENTILE_MIN_GROUP", 3)
    for n, (pid, (antes, depois)) in enumerate(POSICOES.items()):
        _produto(db, pid)
        _leitura(db, pid, T0, antes, preco=10.0 * (n + 1))
        if depois is not None:
            _leitura(db, pid, T1, depois, preco=10.0 * (n + 1))
            _leitura(db, pid, T2, depois, preco=10.0 * (n + 1))
    return db


def _corte(db, **kw):
    rel = bt.run_backtest(db, source="mercadolivre", horizon_hours=6, k=2, min_pool=3, **kw)
    corte = next(c for c in rel["cortes"] if c["T"] == T0.isoformat())
    return rel, {i["product_id"]: i for i in corte["itens"]}


def test_desfechos_saem_da_posicao_no_ranking(db):
    _, itens = _corte(db)

    assert itens["m5"]["ganho_posicoes"] == 4
    assert itens["m5"]["entrou_top5"] == 1
    assert itens["m6"]["entrou_top5"] == 0
    # Já estava no top 5: "entrar" não se aplica
    assert itens["m0"]["entrou_top5"] is None
    # Maior = melhor em todo preditor e desfecho
    assert itens["m0"]["posicao_atual"] == -1
    assert itens["m5"]["posicao_futura"] == -2


def test_quem_sai_do_top_20_conta_como_posicao_21(db):
    _, itens = _corte(db)
    assert itens["m7"]["posicao_futura"] == -21
    assert itens["m7"]["ganho_posicoes"] == 8 - 21
    assert itens["m7"]["entrou_top5"] == 0


def test_sumir_numa_categoria_que_nao_foi_coletada_nao_e_queda(db):
    # MLB2 não aparece em T1 (highlights falhou): não dá para dizer que caiu
    _produto(db, "x1", categoria="MLB2")
    _leitura(db, "x1", T0, 3, categoria="MLB2")
    _, itens = _corte(db)
    assert "x1" not in itens


def test_produto_fora_do_ranking_no_corte_nao_entra(db):
    # Ativo na janela, mas não estava no ranking em T0
    _produto(db, "velho")
    _leitura(db, "velho", T0 - dt.timedelta(hours=6), 9)
    _, itens = _corte(db)
    assert "velho" not in itens


def test_posicao_atual_acerta_quem_continua_no_topo(db):
    rel, _ = _corte(db)
    m = rel["cortes"][0]["preditores"]["posicao_atual"]["posicao_futura"]
    # Postos futuros 1,3,4,5,6,2,7,8 contra 1..8: Σd² = 20 → 1 − 6·20/(8·63)
    assert m["spearman"] == pytest.approx(1 - 120 / 504)
    assert set(rel["cortes"][0]["preditores"]) == {"score", "posicao_atual", "preco"}


def test_desfecho_binario_mede_acerto_no_top_k_contra_a_taxa_base(db):
    rel, _ = _corte(db)
    entrou = rel["cortes"][0]["preditores"]["posicao_atual"]["entrou_top5"]
    # Fora do top 5 em T0: m5 (6º), m6 (7º), m7 (8º); só m5 entrou
    assert entrou["taxa_base"] == pytest.approx(1 / 3)
    # top-2 por posição atual = m5 e m6 → 1 de 2
    assert entrou["precision_at_k"] == pytest.approx(0.5)
    assert rel["resumo"]["posicao_atual"]["entrou_top5"]["taxa_base"]["n"] == 1


def test_item_sem_preco_fica_fora_so_do_preditor_preco(db):
    db["metrics"].update_many({"product_id": "m6"}, {"$set": {"price": None}})
    _, itens = _corte(db)
    assert itens["m6"]["preco"] is None
    assert itens["m6"]["posicao_atual"] == -7


def test_cortes_do_ml_ignoram_os_instantes_de_coleta_do_tiktok(db):
    db["products"].insert_one(
        {"product_id": "v1", "source": "tiktok", "title": "Achadinho link na bio"}
    )
    db["metrics"].insert_one(
        {"product_id": "v1", "source": "tiktok", "ts": T0 + dt.timedelta(hours=1), "views": 10}
    )
    rel, itens = _corte(db)
    assert "v1" not in itens


def test_main_aceita_a_fonte(db, monkeypatch, capsys):
    import src.infrastructure.db.mongo as mongo

    monkeypatch.setattr(mongo, "get_db", lambda: db)
    monkeypatch.setattr(
        "sys.argv",
        ["backtest", "--source", "mercadolivre", "--horizon-hours", "6", "--min-pool", "3"],
    )
    assert bt.main() == 0
    saida = capsys.readouterr().out
    assert "mercadolivre" in saida
    assert "entrou_top5" in saida


# --- subida forte, AUC, comparação por dia (TIE-23) -----------------------------


def test_subida_forte_so_vale_para_quem_podia_subir_tanto(db):
    rel, itens = _corte(db, subida=3)
    # m5: 6º → 2º, ganhou 4 ≥ 3
    assert itens["m5"]["subida_forte"] == 1
    assert itens["m6"]["subida_forte"] == 0
    # Do 1º ao 3º não dá para ganhar 3 posições: não se aplica
    assert itens["m2"]["subida_forte"] is None
    m = rel["cortes"][0]["preditores"]["posicao_atual"]["subida_forte"]
    # Candidatos m3..m7 (4º a 8º), só m5 subiu; posição atual põe m3, m4 acima dele
    assert m["taxa_base"] == pytest.approx(1 / 5)
    assert m["auc"] == pytest.approx(2 / 4)


def test_pico_de_uma_leitura_nao_conta_como_subida_forte(db):
    _leitura(db, "m5", T2, 6)
    _, itens = _corte(db, subida=3)
    assert itens["m5"]["subida_forte"] == 0


def test_subida_forte_sem_leitura_de_confirmacao_fica_fora_do_desfecho(db):
    db["metrics"].delete_many({"ts": T2})
    _, itens = _corte(db, subida=3)
    assert itens["m5"]["subida_forte"] is None


def test_troca_de_categoria_nao_compara_posicoes_de_rankings_diferentes(db):
    db["metrics"].update_one({"product_id": "m5", "ts": T1}, {"$set": {"category": "MLB2"}})
    _, itens = _corte(db, subida=3)
    assert "m5" not in itens


def test_troca_de_categoria_na_confirmacao_deixa_subida_indefinida(db):
    db["metrics"].update_one({"product_id": "m5", "ts": T2}, {"$set": {"category": "MLB2"}})
    _, itens = _corte(db, subida=3)
    assert itens["m5"]["subida_forte"] is None


def test_eventos_listam_cada_subida_uma_vez_com_data_e_percentil(db):
    rel, _ = _corte(db, subida=3)
    (ev,) = rel["eventos"]
    assert ev["product_id"] == "m5" and ev["T"] == T0.isoformat()
    assert (ev["pos_T"], ev["pos_futura"]) == (6, 2)
    # 6º de 8: dois abaixo (7º e 8º) e ele mesmo como meio empate → 2,5/8
    assert ev["pct_posicao"] == pytest.approx(2.5 / 8)
    assert 0 <= ev["pct_score"] <= 1


def test_comparacao_precisa_de_dois_dias(db):
    rel, _ = _corte(db)
    assert rel["comparacao"]["posicao_atual"]["posicao_futura"]["spearman"] is None
    assert set(rel["comparacao"]) == {"posicao_atual", "preco"}


def test_comparacao_agrupa_os_cortes_por_dia(db):
    # Mesmo ranking um dia depois: dois dias, um corte em cada
    d1, d1b, d1c = (
        T0 + dt.timedelta(days=1),
        T1 + dt.timedelta(days=1),
        T2 + dt.timedelta(days=1),
    )
    for n, (pid, (antes, depois)) in enumerate(POSICOES.items()):
        _leitura(db, pid, d1, antes, preco=10.0 * (n + 1))
        if depois is not None:
            _leitura(db, pid, d1b, depois, preco=10.0 * (n + 1))
            _leitura(db, pid, d1c, depois, preco=10.0 * (n + 1))
    rel = bt.run_backtest(db, source="mercadolivre", horizon_hours=6, k=2, min_pool=3)

    r = rel["comparacao"]["posicao_atual"]["posicao_futura"]["spearman"]
    assert r["blocos"] == 2
    por_corte = [
        c["preditores"]["score"]["posicao_futura"]["spearman"]
        - c["preditores"]["posicao_atual"]["posicao_futura"]["spearman"]
        for c in rel["cortes"]
        if c["T"][:10] in (T0.isoformat()[:10], d1.isoformat()[:10])
    ]
    assert r["ic_inf"] <= r["media"] <= r["ic_sup"]
    assert min(por_corte) - 1e-9 <= r["media"] <= max(por_corte) + 1e-9


def test_tabela_mostra_comparacao_e_subidas(db, monkeypatch, capsys):
    import src.infrastructure.db.mongo as mongo

    monkeypatch.setattr(mongo, "get_db", lambda: db)
    monkeypatch.setattr(
        "sys.argv",
        ["backtest", "--source", "mercadolivre", "--min-pool", "3", "--subida", "3"],
    )
    assert bt.main() == 0
    saida = capsys.readouterr().out
    assert "score − baseline" in saida and "sem 2 dias" in saida
    assert "Subidas fortes (≥ 3 posições" in saida and "Item m5" in saida
