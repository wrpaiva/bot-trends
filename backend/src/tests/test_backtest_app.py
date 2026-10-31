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


def test_relatorio_de_calibracao_tiktok_mede_percentis_e_limiares(db):
    rel = bt.run_backtest(db, horizon_hours=6, k=2, min_pool=3, calibration_thresholds=(0, 50, 101))
    calibracao = rel["calibracao"]
    assert calibracao["n_scores"] == 6
    assert calibracao["dias"] == 1
    assert calibracao["intervalo_observado"] == {
        "primeiro_T": T0.isoformat(),
        "ultimo_T": T0.isoformat(),
        "dias": 1,
    }
    assert set(calibracao["percentis"]) == {"p50", "p90", "p95", "p99"}
    assert calibracao["limiares"]["0"]["candidatos"] == 6
    assert calibracao["limiares"]["0"]["videos_distintos"] == 6
    assert calibracao["limiares"]["0"]["candidatos_por_dia"] == 6
    assert calibracao["limiares"]["0"]["alertas_simulados"] == 6
    assert calibracao["limiares"]["101"]["candidatos"] == 0
    assert any("cooldown" in texto for texto in calibracao["limitacoes"])
    assert any("desfecho futuro" in texto for texto in calibracao["limitacoes"])


def test_relatorio_de_calibracao_vazio_nao_divide_por_zero():
    db = mongomock.MongoClient(tz_aware=True).db
    rel = bt.run_backtest(db, calibration_thresholds=(85,))
    assert rel["calibracao"]["percentis"]["p50"] is None
    assert rel["calibracao"]["limiares"]["85"]["percentual"] is None


def test_calibracao_nao_realerta_artificialmente_ao_cruzar_corte_fixo(monkeypatch):
    monkeypatch.setattr(bt.settings, "ALERT_COOLDOWN_HOURS", 24)
    cortes = [
        {"T": T0.isoformat(), "itens": [{"product_id": "v1", "score": 80}]},
        {
            "T": (T0 + dt.timedelta(hours=6)).isoformat(),
            "itens": [{"product_id": "v1", "score": 85}],
        },
    ]

    # Em um limiar candidato 75, ambos já são SUBINDO. Cruzar o corte fixo
    # de produção (85) não pode parecer escalada e furar o cooldown.
    assert bt._simula_alertas_tiktok(cortes, 75) == 1


def test_calibracao_simula_dedupe_e_cooldown_por_video(monkeypatch):
    monkeypatch.setattr(bt.settings, "ALERT_COOLDOWN_HOURS", 24)
    cortes = [
        {"T": T0.isoformat(), "itens": [{"product_id": "v1", "score": 85}]},
        {
            "T": (T0 + dt.timedelta(hours=6)).isoformat(),
            "itens": [
                {"product_id": "v1", "score": 85},
                {"product_id": "v2", "score": 85},
            ],
        },
        {
            "T": (T0 + dt.timedelta(hours=30)).isoformat(),
            "itens": [{"product_id": "v1", "score": 85}],
        },
    ]

    calibracao = bt._calibracao_tiktok(cortes, (85,))
    dados = calibracao["limiares"]["85"]
    assert calibracao["intervalo_observado"] == {
        "primeiro_T": T0.isoformat(),
        "ultimo_T": (T0 + dt.timedelta(hours=30)).isoformat(),
        "dias": 2,
    }
    assert dados["candidatos"] == 4
    assert dados["videos_distintos"] == 2
    # v1 no segundo corte ainda está em cooldown; v2 e v1 após 30 h alertam.
    assert dados["alertas_simulados"] == 3
    assert dados["alertas_simulados_por_dia"] == pytest.approx(1.5)


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
    assert "Calibracao TikTok" in texto
    assert "alertas/dia (sim.)" in texto
    assert "intervalo observado" in texto


def test_main_imprime_a_tabela(db, monkeypatch, capsys):
    import src.infrastructure.db.mongo as mongo

    monkeypatch.setattr(mongo, "get_db", lambda: db)
    monkeypatch.setattr("sys.argv", ["backtest", "--horizon-hours", "6", "--min-pool", "3"])
    assert bt.main() == 0
    assert "Cortes avaliados: 1" in capsys.readouterr().out


def test_coleta_que_sai_um_pouco_antes_do_horizonte_conta(db):
    # TIE-46: o "6 em 6 h" da coleta sai com 5,99 h; sem folga o corte sumia
    for m in db["metrics"].find({"ts": {"$gt": T0}}):
        db["metrics"].update_one({"_id": m["_id"]}, {"$set": {"ts": T0 + dt.timedelta(hours=5.99)}})
    rel = bt.run_backtest(db, horizon_hours=6, k=2, min_pool=3)
    assert [c["T"] for c in rel["cortes"]] == [T0.isoformat()]


def test_fonte_desconhecida_falha():
    with pytest.raises(ValueError):
        bt.run_backtest(mongomock.MongoClient(tz_aware=True).db, source="shopee")
