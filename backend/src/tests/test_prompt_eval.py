# src/tests/test_prompt_eval.py
"""
Avaliação do prompt (TIE-26): CSV cego para rotulagem humana, concordância
por versão do prompt e estabilidade da resposta por temperatura.
"""

import csv
import datetime as dt

import mongomock
import pytest

from apps.prompt_eval import main as pe
from apps.worker import tasks_trend
from src.domain.trend_models import LLMResult
from src.infrastructure.llm.schema import LLMResponseError

T = dt.datetime(2026, 10, 1, 15, tzinfo=dt.UTC)
# (product_id, classificação v3, llm v3, classificação v4, llm v4, numérico)
VIDEOS = [
    ("p1", "ESTAVEL", 40, "SUBINDO", 75, 58),
    ("p2", "ESTAVEL", 41, "ESTAVEL", 50, 52),
    ("p3", "ESTAVEL", 39, "EM_QUEDA", 15, 25),
    ("p4", "ESTAVEL", 42, "PICO_TEMPORARIO", 52, 54),
]


@pytest.fixture
def db(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True).db
    monkeypatch.setattr(tasks_trend.settings, "SCORE_PERCENTILE_MIN_GROUP", 3)
    for i, (pid, c3, l3, c4, l4, num) in enumerate(VIDEOS):
        db["products"].insert_one(
            {
                "product_id": pid,
                "source": "tiktok",
                "title": f"Achadinho {pid} link na bio",
                "permalink": f"https://www.tiktok.com/@x/video/{i}",
                "published_at": T - dt.timedelta(hours=48),
            }
        )
        db["metrics"].insert_one(
            {
                "product_id": pid,
                "source": "tiktok",
                "ts": T - dt.timedelta(hours=10),
                "views": 1000 * (i + 1),
                "engagement": 100 * (i + 1),
            }
        )
        for versao, cls, llm, atraso in (("v3", c3, l3, 3), ("v4", c4, l4, 0)):
            db["trend_insights"].insert_one(
                {
                    "product_id": pid,
                    "ts": T - dt.timedelta(minutes=atraso),
                    "window_hours": 200,
                    "prompt_version": versao,
                    "trend_classification": cls,
                    "llm_score": llm,
                    "numeric_score": num,
                }
            )
    return db


def _rotular(caminho, rotulos):
    """Preenche o CSV exportado como uma pessoa faria."""
    with open(caminho, newline="", encoding="utf-8") as f:
        linhas = list(csv.DictReader(f))
    for linha in linhas:
        if linha["product_id"] in rotulos:
            linha["classificacao_humana"], linha["score_humano"] = rotulos[linha["product_id"]]
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0]))
        w.writeheader()
        w.writerows(linhas)


# --- exportar -----------------------------------------------------------------


def test_exporta_csv_cego_com_os_videos_das_duas_versoes(db, tmp_path):
    saida = tmp_path / "rotulos.csv"
    assert pe.exportar(db, versoes=["v3", "v4"], saida=saida) == 4

    texto = saida.read_text(encoding="utf-8")
    linhas = list(csv.DictReader(texto.splitlines()))
    assert [x["product_id"] for x in linhas] == ["p1", "p2", "p3", "p4"]
    assert linhas[0]["link"] == "https://www.tiktok.com/@x/video/0"
    assert linhas[0]["classificacao_humana"] == "" and linhas[0]["score_humano"] == ""
    # Cego: nada do que o modelo respondeu pode estar no arquivo
    for proibido in ("SUBINDO", "EM_QUEDA", "PICO_TEMPORARIO", "llm", "numeric"):
        assert proibido not in texto


def test_exporta_so_o_que_tem_todas_as_versoes(db, tmp_path):
    db["trend_insights"].delete_many({"product_id": "p4", "prompt_version": "v3"})
    assert pe.exportar(db, versoes=["v3", "v4"], saida=tmp_path / "r.csv") == 3


# --- concordancia ---------------------------------------------------------------


def test_concordancia_por_versao_antes_e_depois(db, tmp_path):
    saida = tmp_path / "rotulos.csv"
    pe.exportar(db, versoes=["v3", "v4"], saida=saida)
    # A pessoa concorda com o v4 em tudo
    _rotular(
        saida,
        {
            "p1": ("SUBINDO", "80"),
            "p2": ("ESTAVEL", "50"),
            "p3": ("EM_QUEDA", "10"),
            "p4": ("PICO_TEMPORARIO", "55"),
        },
    )

    rel = pe.concordancia(db, rotulos=saida, versoes=["v3", "v4"])

    assert rel["n"] == 4
    assert rel["versoes"]["v4"]["acerto"] == pytest.approx(1.0)
    assert rel["versoes"]["v4"]["kappa"] == pytest.approx(1.0)
    assert rel["versoes"]["v4"]["spearman_llm"] == pytest.approx(1.0)
    # v3 dizia ESTAVEL para tudo: acerta 1 de 4, kappa 0
    assert rel["versoes"]["v3"]["acerto"] == pytest.approx(0.25)
    assert rel["versoes"]["v3"]["kappa"] == pytest.approx(0.0)
    # Referência: o score numérico também é comparado com o humano
    assert rel["versoes"]["v4"]["spearman_numerico"] is not None


def test_linha_sem_rotulo_fica_fora(db, tmp_path):
    saida = tmp_path / "rotulos.csv"
    pe.exportar(db, versoes=["v3", "v4"], saida=saida)
    _rotular(saida, {"p1": ("SUBINDO", "80"), "p2": ("ESTAVEL", "")})
    rel = pe.concordancia(db, rotulos=saida, versoes=["v4"])
    assert rel["n"] == 2
    # Score em branco não entra na correlação; menos de 3 pares → None
    assert rel["versoes"]["v4"]["spearman_llm"] is None


def test_rotulo_invalido_diz_a_linha(db, tmp_path):
    saida = tmp_path / "rotulos.csv"
    pe.exportar(db, versoes=["v3", "v4"], saida=saida)
    _rotular(saida, {"p2": ("subindo muito", "50")})
    with pytest.raises(ValueError, match="p2.*subindo muito"):
        pe.concordancia(db, rotulos=saida, versoes=["v4"])


# --- estabilidade -------------------------------------------------------------


class _LLMFalso:
    """Temperatura 0 responde sempre igual; acima disso alterna; 1.0 quebra o JSON."""

    def __init__(self, temperature):
        self.temperature = temperature
        self.n = 0
        self.prompts = []

    def analyze_trend(self, system, user):
        self.n += 1
        self.prompts.append(user)
        if self.temperature >= 1.0:
            raise LLMResponseError("json quebrado")
        variando = self.temperature > 0 and self.n % 2 == 0
        return LLMResult(
            trend_classification="SUBINDO" if variando else "ESTAVEL",
            potential_score_0_100=70.0 if variando else 50.0,
            risk_level="MEDIO",
            analysis="a",
            recommendation="r",
            confidence_0_1=0.8,
        )


def test_estabilidade_por_temperatura(db):
    criados = []

    def fabrica(temperature):
        criados.append(_LLMFalso(temperature))
        return criados[-1]

    rel = pe.estabilidade(
        db, versao="v4", temperaturas=[0.0, 0.2, 1.0], repeticoes=2, llm_factory=fabrica
    )

    t0, t02, t1 = (rel["temperaturas"][k] for k in ("0.0", "0.2", "1.0"))
    assert t0["chamadas"] == 8 and t0["validas"] == 8
    assert t0["estabilidade_classificacao"] == pytest.approx(1.0)
    assert t0["desvio_score_medio"] == pytest.approx(0.0)
    # Em 0.2 cada vídeo deu ESTAVEL numa repetição e SUBINDO na outra
    assert t02["estabilidade_classificacao"] == pytest.approx(0.5)
    assert t02["desvio_score_medio"] > 0
    # Formato: em 1.0 nenhuma resposta passou no schema
    assert t1["validas"] == 0 and t1["taxa_valida"] == 0.0
    # O prompt é o mesmo da análise: entrada montada como o v4 a viu
    assert "Achadinho p1" in criados[0].prompts[0]


def test_estabilidade_respeita_o_teto_de_chamadas(db):
    with pytest.raises(ValueError, match="chamadas"):
        pe.estabilidade(
            db,
            versao="v4",
            temperaturas=[0.0, 0.2],
            repeticoes=5,
            llm_factory=_LLMFalso,
            max_chamadas=10,
        )


def test_cli_exportar_concordancia_e_estabilidade(db, tmp_path, monkeypatch, capsys):
    import src.infrastructure.db.mongo as mongo
    import src.infrastructure.llm.openai_compatible as llm_mod

    monkeypatch.setattr(mongo, "get_db", lambda: db)
    monkeypatch.setattr(
        llm_mod, "OpenAICompatibleLLMClient", lambda temperature: _LLMFalso(temperature)
    )
    saida = tmp_path / "r.csv"

    monkeypatch.setattr("sys.argv", ["pe", "exportar", "--saida", str(saida)])
    assert pe.main() == 0
    assert "4 vídeos" in capsys.readouterr().out

    _rotular(saida, {"p1": ("SUBINDO", "80"), "p2": ("ESTAVEL", "50"), "p3": ("EM_QUEDA", "10")})
    monkeypatch.setattr("sys.argv", ["pe", "concordancia", "--rotulos", str(saida)])
    assert pe.main() == 0
    assert '"kappa"' in capsys.readouterr().out

    monkeypatch.setattr(
        "sys.argv", ["pe", "estabilidade", "--temperaturas", "0", "--repeticoes", "2"]
    )
    assert pe.main() == 0
    assert '"taxa_valida": 1.0' in capsys.readouterr().out
