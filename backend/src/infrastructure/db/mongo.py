# src/infrastructure/db/mongo.py
"""
Um MongoClient por processo (TIE-7).

O MongoClient já mantém pool de conexões interno; criar um por chamada joga o
pool fora e vaza sockets sob carga. Aqui ele é criado na primeira chamada e
reaproveitado até `close_client()`.

O cliente não é fork-safe: o worker Celery (prefork) chama
`reset_client_after_fork()` em cada processo filho.
"""

import datetime as dt
import threading

from pymongo import MongoClient
from pymongo.database import Database

from src.infrastructure.config import settings

_client: MongoClient | None = None
_lock = threading.Lock()


def get_client() -> MongoClient:
    """Devolve o cliente do processo, criando-o na primeira chamada."""
    global _client
    if _client is None:
        # Rotas síncronas do FastAPI rodam em threadpool: sem o lock, duas
        # requisições simultâneas na subida criariam dois clientes.
        with _lock:
            if _client is None:
                # tz_aware: datas voltam aware em UTC, comparáveis com utcnow() (TIE-10)
                _client = MongoClient(
                    settings.MONGO_URI,
                    serverSelectionTimeoutMS=5000,
                    tz_aware=True,
                    tzinfo=dt.UTC,
                )
    return _client


def get_db() -> Database:
    return get_client()[settings.MONGO_DB]


def close_client() -> None:
    """Fecha o cliente do processo. A próxima chamada a `get_client()` cria outro."""
    global _client
    with _lock:
        if _client is not None:
            _client.close()
            _client = None


def reset_client_after_fork() -> None:
    """Descarta, sem fechar, o cliente herdado do processo pai.

    Fechar no filho mexeria em sockets e threads que pertencem ao pai.
    """
    global _client, _lock
    _client = None
    _lock = threading.Lock()
