# src/tests/test_ml_auth.py
"""
OAuth do Mercado Livre (TIE-41). A API deixou de ser pública: `/highlights`,
`/sites/.../search` e `/items` devolvem 401/403 sem `Authorization: Bearer`.

O ML só aceita `authorization_code` e `refresh_token` (sem client
credentials): autoriza-se uma vez no navegador e o token é renovado sozinho.
O access token dura 6 h e o refresh token é de USO ÚNICO — cada renovação
devolve um novo, que precisa ser gravado antes de qualquer outra coisa, senão
o próximo refresh falha com `invalid_grant` e só uma nova autorização resolve.
"""

import base64
import datetime as dt
import hashlib
import logging
from urllib.parse import parse_qs, urlsplit

import httpx
import mongomock
import pytest

from src.infrastructure.collectors import ml_auth as mod
from src.infrastructure.collectors.ml_auth import (
    MercadoLivreAuth,
    MLAuthError,
    MongoTokenStore,
    authorization_url,
    code_from_redirect,
    pkce_pair,
)

AGORA = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)
SECRET = "segredo-do-app-ml"


@pytest.fixture
def store():
    return MongoTokenStore(mongomock.MongoClient(tz_aware=True).db)


def _grava(store, *, access="APP_USR-velho", refresh="TG-r1", expira_em_min=120):
    store.save(
        {
            "access_token": access,
            "refresh_token": refresh,
            "expires_at": AGORA + dt.timedelta(minutes=expira_em_min),
            "user_id": 42,
        },
        replaces=None,
    )


def _auth(store, handler) -> MercadoLivreAuth:
    auth = MercadoLivreAuth(
        store,
        client_id="123",
        client_secret=SECRET,
        transport=httpx.MockTransport(handler),
        clock=lambda: AGORA,
    )
    auth._post_token.retry.sleep = lambda *_: None
    return auth


def _token_ok(access="APP_USR-novo", refresh="TG-r2"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": access,
                "token_type": "Bearer",
                "expires_in": 21600,
                "scope": "offline_access read",
                "user_id": 42,
                "refresh_token": refresh,
            },
        )

    return handler


def _nao_chama(request):
    raise AssertionError(f"não devia chamar {request.url}")


def _form(request: httpx.Request) -> dict:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


# --- Uso do token ---------------------------------------------------------------


def test_token_valido_eh_usado_sem_chamar_o_ml(store):
    _grava(store)
    assert _auth(store, _nao_chama).access_token() == "APP_USR-velho"


def test_sem_token_gravado_pede_a_autorizacao_inicial(store):
    with pytest.raises(MLAuthError, match="apps.ml_auth"):
        _auth(store, _nao_chama).access_token()


@pytest.mark.parametrize("expira_em_min", [-10, 2])
def test_token_vencido_ou_perto_de_vencer_eh_renovado(store, expira_em_min):
    _grava(store, expira_em_min=expira_em_min)
    pedidos = []

    def handler(request):
        pedidos.append(request)
        return _token_ok()(request)

    assert _auth(store, handler).access_token() == "APP_USR-novo"

    (req,) = pedidos
    assert req.method == "POST" and req.url.path == "/oauth/token"
    assert _form(req) == {
        "grant_type": "refresh_token",
        "client_id": "123",
        "client_secret": SECRET,
        "refresh_token": "TG-r1",
    }
    # Credencial nunca na URL (TIE-3)
    assert SECRET not in str(req.url) and "TG-r1" not in str(req.url)


def test_renovacao_grava_o_novo_refresh_token_e_a_validade(store):
    _grava(store, expira_em_min=-1)
    _auth(store, _token_ok()).access_token()

    doc = store.load()
    assert doc["refresh_token"] == "TG-r2"  # o antigo não vale mais
    assert doc["expires_at"] == AGORA + dt.timedelta(seconds=21600)
    assert doc["expires_at"].tzinfo is not None


def test_token_renovado_sobrevive_a_restart(store):
    _grava(store, expira_em_min=-1)
    _auth(store, _token_ok()).access_token()

    # Outro processo (worker reiniciado) lê do Mongo, sem chamar o ML
    assert _auth(store, _nao_chama).access_token() == "APP_USR-novo"


def test_erro_transitorio_na_renovacao_eh_retentado(store):
    _grava(store, expira_em_min=-1)
    tentativas = {"n": 0}

    def handler(request):
        tentativas["n"] += 1
        if tentativas["n"] == 1:
            return httpx.Response(503)
        return _token_ok()(request)

    assert _auth(store, handler).access_token() == "APP_USR-novo"
    assert tentativas["n"] == 2


# --- Refresh token de uso único --------------------------------------------------


def _invalid_grant(request):
    return httpx.Response(400, json={"error": "invalid_grant", "message": "invalid_grant"})


def test_refresh_token_invalido_pede_nova_autorizacao(store):
    _grava(store, expira_em_min=-1)
    with pytest.raises(MLAuthError, match="autorizar de novo"):
        _auth(store, _invalid_grant).access_token()
    assert store.load()["refresh_token"] == "TG-r1"  # nada gravado por cima


def test_outro_worker_renovou_primeiro(store):
    """Dois workers com o mesmo refresh token: o segundo toma `invalid_grant`,
    mas o par novo já está no Mongo — usa esse em vez de falhar."""
    _grava(store, expira_em_min=-1)

    def handler(request):
        _grava(store, access="APP_USR-do-outro", refresh="TG-do-outro", expira_em_min=360)
        return _invalid_grant(request)

    assert _auth(store, handler).access_token() == "APP_USR-do-outro"


def test_refresh_apos_401_reaproveita_token_ja_renovado_por_outro(store):
    _grava(store, access="APP_USR-novo-do-outro", expira_em_min=300)
    auth = _auth(store, _nao_chama)
    assert auth.refresh(stale="APP_USR-que-deu-401") == "APP_USR-novo-do-outro"


def test_refresh_apos_401_com_o_mesmo_token_renova(store):
    _grava(store, expira_em_min=300)
    assert _auth(store, _token_ok()).refresh(stale="APP_USR-velho") == "APP_USR-novo"


def test_gravacao_nao_sobrescreve_par_mais_novo(store):
    _grava(store, refresh="TG-r1")
    _grava(store, refresh="TG-r2")  # outro processo já trocou
    assert not store.save(
        {"access_token": "x", "refresh_token": "TG-r3", "expires_at": AGORA}, replaces="TG-r1"
    )
    assert store.load()["refresh_token"] == "TG-r2"


def test_erro_do_oauth_nao_vaza_credenciais(store, caplog):
    _grava(store, expira_em_min=-1)

    def handler(request):
        return httpx.Response(401, json={"error": "invalid_client"})

    with caplog.at_level(logging.DEBUG), pytest.raises(MLAuthError) as exc:
        _auth(store, handler).access_token()

    assert "invalid_client" in str(exc.value)
    for segredo in (SECRET, "TG-r1", "APP_USR-velho"):
        assert segredo not in str(exc.value) and segredo not in caplog.text
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


# --- Autorização inicial ----------------------------------------------------------


def test_troca_do_code_grava_o_primeiro_par(store):
    pedidos = []

    def handler(request):
        pedidos.append(_form(request))
        return _token_ok(access="APP_USR-primeiro", refresh="TG-primeiro")(request)

    info = _auth(store, handler).exchange_code(
        "TG-code", redirect_uri="https://localhost/cb", code_verifier="v" * 50
    )

    assert pedidos == [
        {
            "grant_type": "authorization_code",
            "client_id": "123",
            "client_secret": SECRET,
            "code": "TG-code",
            "redirect_uri": "https://localhost/cb",
            "code_verifier": "v" * 50,
        }
    ]
    assert store.load()["refresh_token"] == "TG-primeiro"
    # O retorno é para imprimir no terminal: nada de token
    assert "access_token" not in info and "refresh_token" not in info
    assert info["user_id"] == 42


def test_pkce_s256():
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    esperado = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert challenge == esperado.decode().rstrip("=")


def test_url_de_autorizacao():
    url = authorization_url(
        "https://auth.mercadolivre.com.br/authorization",
        client_id="123",
        redirect_uri="https://localhost/cb",
        state="xyz",
        code_challenge="abc",
    )
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert url.startswith("https://auth.mercadolivre.com.br/authorization?")
    assert q == {
        "response_type": "code",
        "client_id": "123",
        "redirect_uri": "https://localhost/cb",
        "state": "xyz",
        "code_challenge": "abc",
        "code_challenge_method": "S256",
    }


def test_code_da_url_de_retorno_confere_o_state():
    assert code_from_redirect("https://localhost/cb?code=TG-abc&state=xyz", "xyz") == "TG-abc"
    assert code_from_redirect("  TG-abc \n", "xyz") == "TG-abc"  # colou só o code
    with pytest.raises(MLAuthError, match="state"):
        code_from_redirect("https://localhost/cb?code=TG-abc&state=outro", "xyz")
    with pytest.raises(MLAuthError, match="code"):
        code_from_redirect("https://localhost/cb?error=access_denied&state=xyz", "xyz")


def test_from_settings_exige_credenciais(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True).db
    monkeypatch.setattr(mod.settings, "ML_CLIENT_ID", None)
    monkeypatch.setattr(mod.settings, "ML_CLIENT_SECRET", "x")
    with pytest.raises(MLAuthError, match="ML_CLIENT_ID"):
        MercadoLivreAuth.from_settings(db)
