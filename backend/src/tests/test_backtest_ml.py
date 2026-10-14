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
    return db


def _corte(db, **kw):
    rel = bt.run_backtest(db, source="mercadolivre", horizon_hours=6, k=2, min_pool=3, **kw)
    assert [c["T"] for c in rel["cortes"]] == [T0.isoformat()]
    return rel, {i["product_id"]: i for i in rel["cortes"][0]["itens"]}


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
