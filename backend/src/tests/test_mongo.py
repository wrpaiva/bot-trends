# tests/test_mongo.py
"""
Ciclo de vida do MongoClient (TIE-7).

`get_db()` abria um MongoClient novo a cada chamada, jogando fora o pool
interno do driver. Agora é um cliente por processo, fechado no shutdown.
"""

import datetime as dt

import mongomock
import pytest

from src.infrastructure.db import mongo


class ClienteFalso(mongomock.MongoClient):
    """mongomock com registro de fechamento, para não abrir sockets reais."""

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.kwargs = kwargs
        self.fechado = False

    def close(self):
        self.fechado = True


@pytest.fixture(autouse=True)
def cliente_falso(monkeypatch):
    monkeypatch.setattr(mongo, "MongoClient", ClienteFalso)
    mongo.close_client()
    yield
    mongo.close_client()


def test_chamadas_repetidas_devolvem_o_mesmo_cliente():
    assert mongo.get_client() is mongo.get_client()


def test_get_db_reaproveita_o_cliente():
    assert mongo.get_db().client is mongo.get_db().client


def test_cliente_devolve_datas_aware_em_utc():
    # Sem isso o driver devolve naive e comparar com utcnow() levanta TypeError (TIE-10)
    kwargs = mongo.get_client().kwargs
    assert kwargs["tz_aware"] is True
    assert kwargs["tzinfo"] == dt.UTC


def test_get_db_usa_o_banco_configurado():
    assert mongo.get_db().name == mongo.settings.MONGO_DB


def test_close_client_fecha_e_a_proxima_chamada_cria_outro():
    primeiro = mongo.get_client()
    mongo.close_client()

    assert primeiro.fechado
    assert mongo.get_client() is not primeiro


def test_close_client_sem_cliente_aberto_nao_falha():
    mongo.close_client()
    mongo.close_client()


def test_reset_apos_fork_descarta_sem_fechar():
    """No filho do prefork o cliente herdado não pode ser usado nem fechado."""
    herdado = mongo.get_client()
    mongo.reset_client_after_fork()

    assert not herdado.fechado
    assert mongo.get_client() is not herdado


def test_shutdown_da_api_fecha_o_cliente(monkeypatch):
    from fastapi.testclient import TestClient

    from apps.api.main import app

    # A API não sobe sem API_KEY (TIE-8)
    monkeypatch.setattr(mongo.settings, "API_KEY", "teste")
    with TestClient(app):
        cliente = mongo.get_client()

    assert cliente.fechado


def test_worker_reseta_o_cliente_ao_iniciar_processo_filho():
    from celery.signals import worker_process_init

    import apps.worker.main  # noqa: F401  (registra o handler do sinal)

    herdado = mongo.get_client()
    worker_process_init.send(sender=None)

    assert mongo.get_client() is not herdado
