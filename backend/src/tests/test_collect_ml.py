# src/tests/test_collect_ml.py
"""
`collect_ml` não pode devolver `status: ok` quando a coleta falhou (TIE-17).
Antes, um 401 em toda categoria virava `{"status": "ok", "inserted": 0}`.
"""

import mongomock
import pytest

from apps.worker import tasks as tasks_mod


class _FakeCollector:
    itens: list[dict] = []
    erros = 0

    auth_error = None
    recebido: dict = {}

    def __init__(self, **kwargs):
        self.errors = 0
        _FakeCollector.recebido = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def collect(self):
        yield from _FakeCollector.itens
        self.errors = _FakeCollector.erros
        self.auth_error = _FakeCollector.auth_error


@pytest.fixture
def db(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True).db
    db["categories"].insert_one({"enabled": True, "ml_category_id": "MLB1"})
    monkeypatch.setattr(tasks_mod.settings, "ML_CLIENT_ID", "123")
    monkeypatch.setattr(tasks_mod.settings, "ML_CLIENT_SECRET", "segredo")
    _FakeCollector.auth_error = None
    monkeypatch.setattr(tasks_mod, "get_db", lambda: db)
    monkeypatch.setattr(tasks_mod, "MercadoLivreCollector", _FakeCollector)
    # Sem Redis nos testes: breaker sempre fechado, salvo quando o teste troca
    monkeypatch.setattr(tasks_mod, "_breaker", lambda nome: _BreakerFalso())
    return db


def _item(i: int) -> dict:
    return {"source": "mercadolivre", "source_product_id": f"MLB{i}", "price": 1.0}


@pytest.mark.parametrize(
    ("itens", "erros", "status"),
    [
        (2, 0, "ok"),
        (2, 1, "partial"),
        (0, 3, "error"),
        (0, 0, "ok"),
    ],
)
def test_status_reflete_erros_da_coleta(db, itens, erros, status):
    _FakeCollector.itens = [_item(i) for i in range(itens)]
    _FakeCollector.erros = erros

    resultado = tasks_mod.collect_ml()

    assert resultado == {"status": status, "inserted": itens, "errors": erros}
    assert db["metrics"].count_documents({}) == itens


# --- Circuit breaker (TIE-37) -------------------------------------------------


class _BreakerFalso:
    def __init__(self, aberto=False):
        self.aberto = aberto
        self.eventos = []

    def allow(self):
        return not self.aberto

    def record_success(self):
        self.eventos.append("ok")

    def record_failure(self):
        self.eventos.append("falha")

    def status(self):
        return {"state": "open" if self.aberto else "closed", "retry_in_s": 120}


def test_circuito_aberto_nao_tenta_coletar(db, monkeypatch):
    construiu = []
    monkeypatch.setattr(tasks_mod, "MercadoLivreCollector", lambda **kw: construiu.append(1))
    monkeypatch.setattr(tasks_mod, "_breaker", lambda nome: _BreakerFalso(aberto=True))

    res = tasks_mod.collect_ml()

    assert res["status"] == "circuit_open" and construiu == []


@pytest.mark.parametrize(
    ("itens", "erros", "evento"), [(2, 0, "ok"), (2, 1, "ok"), (0, 3, "falha")]
)
def test_resultado_da_coleta_alimenta_o_breaker(db, monkeypatch, itens, erros, evento):
    breaker = _BreakerFalso()
    monkeypatch.setattr(tasks_mod, "_breaker", lambda nome: breaker)
    _FakeCollector.itens = [_item(i) for i in range(itens)]
    _FakeCollector.erros = erros

    tasks_mod.collect_ml()

    assert breaker.eventos == [evento]


# --- OAuth (TIE-41) -------------------------------------------------------------


def test_collector_recebe_a_autenticacao(db):
    _FakeCollector.itens, _FakeCollector.erros = [], 0
    tasks_mod.collect_ml()
    assert isinstance(_FakeCollector.recebido["auth"], tasks_mod.MercadoLivreAuth)


def test_sem_credenciais_nao_tenta_e_nao_mente(db, monkeypatch):
    breaker = _BreakerFalso()
    monkeypatch.setattr(tasks_mod, "_breaker", lambda nome: breaker)
    monkeypatch.setattr(tasks_mod.settings, "ML_CLIENT_SECRET", None)
    construiu = []
    monkeypatch.setattr(tasks_mod, "MercadoLivreCollector", lambda **kw: construiu.append(1))

    res = tasks_mod.collect_ml()

    assert res["status"] == "error" and res["inserted"] == 0
    assert "ML_CLIENT_SECRET" in res["reason"]
    # Config faltando não é falha do serviço: não abre o circuito
    assert construiu == [] and breaker.eventos == []


def test_falha_de_token_vira_erro_com_motivo(db):
    _FakeCollector.itens, _FakeCollector.erros = [], 1
    _FakeCollector.auth_error = "nenhum token do Mercado Livre gravado"

    res = tasks_mod.collect_ml()

    assert res["status"] == "error"
    assert res["reason"] == "nenhum token do Mercado Livre gravado"
