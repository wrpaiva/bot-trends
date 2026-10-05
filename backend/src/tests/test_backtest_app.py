# src/tests/test_backtest_app.py
"""
Backtest (TIE-23): em cada corte T, pontua como a análise faria só com os
dados até T e compara com o que aconteceu depois de T.
"""

import datetime as dt

import mongomock
import pytest

from apps.backtest import main as bt
from apps.worker import tasks_trend

T0 = dt.datetime(2026, 9, 24, 12, tzinfo=dt.UTC)


def _video(db, pid, *, publicado_h_antes, leituras):
    db["products"].insert_one(
        {
            "product_id": pid,
            "source": "tiktok",
            "title": f"Achadinho {pid} link na bio",
            "published_at": T0 - dt.timedelta(hours=publicado_h_antes),
        }
    )
    for horas, views in leituras:
        db["metrics"].insert_one(
            {
                "product_id": pid,
                "source": "tiktok",
                "ts": T0 + dt.timedelta(hours=horas),
                "views": views,
                "engagement": views // 10,
            }
        )


@pytest.fixture
def db(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True).db
    monkeypatch.setattr(tasks_trend.settings, "SCORE_PERCENTILE_MIN_GROUP", 3)
    # 6 vídeos lidos em T0 e de novo 6 h depois; quem tem mais views/h em T0
    # é quem mais ganha depois → o baseline acerta tudo
    for i in range(6):
        vph = 100 * (i + 1)
        _video(
            db,
            f"v{i}",
            publicado_h_antes=24,
            leituras=[(0, vph * 24), (6, vph * 24 + vph * 6 * (i + 1))],
        )
    return db


def test_corte_compara_score_e_baseline_com_o_ganho_futuro(db):
    rel = bt.run_backtest(db, horizon_hours=6, k=2, min_pool=3)

    # Só T0 tem leitura futura no horizonte; T0+6h não tem
    assert [c["T"] for c in rel["cortes"]] == [T0.isoformat()]
    corte = rel["cortes"][0]
    assert corte["n"] == 6
    base = corte["preditores"]["views_por_hora"]
    assert base["ganho_views_h"]["spearman"] == pytest.approx(1.0)
    assert base["ganho_views_h"]["precision_at_k"] == pytest.approx(1.0)
    assert set(corte["preditores"]) == {"score", "views_por_hora", "views_total"}
    assert rel["resumo"]["views_por_hora"]["ganho_views_h"]["spearman"]["n"] == 1


def test_score_no_corte_nao_ve_o_futuro(db):
    antes = bt.run_backtest(db, horizon_hours=6, k=2, min_pool=3)["cortes"][0]["itens"]

    # Muda só as leituras posteriores a T0: o score em T0 não pode mudar
    for m in db["metrics"].find({"ts": {"$gt": T0}}):
        db["metrics"].update_one({"_id": m["_id"]}, {"$set": {"views": m["views"] * 7}})
    depois = bt.run_backtest(db, horizon_hours=6, k=2, min_pool=3)["cortes"][0]["itens"]

    assert {i["product_id"]: i["score"] for i in antes} == {
        i["product_id"]: i["score"] for i in depois
    }


def test_leitura_alem_do_dobro_do_horizonte_nao_conta(db):
    # Horizonte de 2 h: a próxima leitura (6 h) passa da tolerância (4 h)
    rel = bt.run_backtest(db, horizon_hours=2, k=2, min_pool=3)
    assert rel["cortes"] == []
    assert rel["resumo"]["score"]["ganho_views_h"]["spearman"] == {"media": None, "n": 0}


def test_corte_com_pool_pequeno_e_pulado(db):
    rel = bt.run_backtest(db, horizon_hours=6, k=2, min_pool=7)
    assert rel["cortes"] == []
    assert rel["cortes_pulados"] >= 1


def test_aceleracao_compara_ritmo_futuro_com_o_de_vida(db):
    corte = bt.run_backtest(db, horizon_hours=6, k=2, min_pool=3)["cortes"][0]
    item = next(i for i in corte["itens"] if i["product_id"] == "v0")
    # v0: 100 views/h de vida em T0; ganhou 600 em 6 h = 100/h → aceleração 1,0
    assert item["ganho_views_h"] == pytest.approx(100.0)
    assert item["aceleracao"] == pytest.approx(1.0)


def test_tabela_avisa_quando_o_pool_e_pequeno_para_o_k(db):
    texto = bt._tabela(bt.run_backtest(db, horizon_hours=6, k=3, min_pool=3))
    assert "AVISO" in texto
    assert "views_por_hora" in texto


def test_main_imprime_a_tabela(db, monkeypatch, capsys):
    import src.infrastructure.db.mongo as mongo

    monkeypatch.setattr(mongo, "get_db", lambda: db)
    monkeypatch.setattr("sys.argv", ["backtest", "--horizon-hours", "6", "--min-pool", "3"])
    assert bt.main() == 0
    assert "Cortes avaliados: 1" in capsys.readouterr().out
