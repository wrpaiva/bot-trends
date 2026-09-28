# src/tests/test_ml_auth_cli.py
"""`apps.ml_auth.main`: autorização inicial do ML (TIE-41). Nada de token na tela."""

import datetime as dt
from urllib.parse import parse_qs, urlsplit

import httpx
import mongomock
import pytest

from apps.ml_auth import main as cli
from src.infrastructure.collectors.ml_auth import MongoTokenStore


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(cli.settings, "ML_CLIENT_ID", "123")
    monkeypatch.setattr(cli.settings, "ML_CLIENT_SECRET", "segredo")
    monkeypatch.setattr(cli.settings, "ML_REDIRECT_URI", "https://localhost/cb")
    return mongomock.MongoClient(tz_aware=True).db


def _token(request):
    return httpx.Response(
        200,
        json={
            "access_token": "APP_USR-primeiro",
            "refresh_token": "TG-primeiro",
            "expires_in": 21600,
            "scope": "offline_access read",
            "user_id": 42,
        },
    )


def test_fluxo_completo_grava_o_token_e_nao_o_imprime(db):
    saida: list[str] = []

    def ler(_prompt):
        # O usuário cola a URL de retorno, com o state que a CLI gerou
        url = next(s for s in saida if s.startswith("https://"))
        state = parse_qs(urlsplit(url).query)["state"][0]
        return f"https://localhost/cb?code=TG-code&state={state}"

    rc = cli.autorizar(db, ler=ler, out=saida.append, transport=httpx.MockTransport(_token))

    assert rc == 0
    assert MongoTokenStore(db).load()["refresh_token"] == "TG-primeiro"
    texto = "\n".join(saida)
    assert "code_challenge=" in texto and "OK: token gravado" in texto
    assert "APP_USR-primeiro" not in texto and "TG-primeiro" not in texto
    assert "segredo" not in texto


def test_state_errado_nao_grava(db):
    saida: list[str] = []
    rc = cli.autorizar(
        db,
        ler=lambda _: "https://localhost/cb?code=TG-code&state=forjado",
        out=saida.append,
        transport=httpx.MockTransport(_token),
    )
    assert rc == 1 and MongoTokenStore(db).load() is None
    assert "state" in saida[-1]


def test_sem_pkce_nao_manda_challenge(db):
    saida: list[str] = []
    cli.autorizar(
        db,
        pkce=False,
        ler=lambda _: "TG-code",
        out=saida.append,
        transport=httpx.MockTransport(_token),
    )
    assert "code_challenge" not in "\n".join(saida)


def test_sem_redirect_uri_avisa(db, monkeypatch):
    monkeypatch.setattr(cli.settings, "ML_REDIRECT_URI", None)
    saida: list[str] = []
    assert cli.autorizar(db, out=saida.append) == 1
    assert "ML_REDIRECT_URI" in saida[0]


def test_status(db):
    saida: list[str] = []
    assert cli.status(db, out=saida.append) == 1

    MongoTokenStore(db).save(
        {
            "access_token": "APP_USR-x",
            "refresh_token": "TG-x",
            "expires_at": dt.datetime.now(dt.UTC) + dt.timedelta(hours=5),
            "user_id": 42,
        },
        replaces=None,
    )
    assert cli.status(db, out=saida.append) == 0
    assert "válido" in saida[-1] and "APP_USR-x" not in saida[-1]
