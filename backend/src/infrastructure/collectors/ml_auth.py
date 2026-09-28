# src/infrastructure/collectors/ml_auth.py
"""
OAuth do Mercado Livre (TIE-41).

A API do ML deixou de ser pública: `/highlights`, `/sites/.../search` e
`/items` devolvem 401/403 sem `Authorization: Bearer`. O ML só aceita os
grants `authorization_code` e `refresh_token` — não há client credentials.
Então:

1. Uma vez, alguém autoriza o app no navegador (`python -m apps.ml_auth.main`)
   e o `code` vira o primeiro par access/refresh, gravado no Mongo.
2. Daí em diante `MercadoLivreAuth.access_token()` devolve o token gravado e o
   renova sozinho quando falta menos de `margin` para vencer (dura 6 h).

O refresh token é de USO ÚNICO: cada renovação devolve um novo, e o antigo
morre. Por isso o par fica no Mongo (sobrevive a restart e é compartilhado
entre processos do worker) e a gravação é condicional ao refresh token que foi
usado — se dois workers renovarem ao mesmo tempo, o perdedor recebe
`invalid_grant`, relê o Mongo e usa o par que o vencedor gravou.

Tokens e o client secret nunca vão para URL, log ou mensagem de erro; o
`logging_setup` também mascara `APP_USR-...`/`TG-...` por padrão.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import logging
import secrets
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from pymongo.database import Database

from src.infrastructure.collectors.http_policy import is_transient, retry_transient
from src.infrastructure.config import settings
from src.infrastructure.logging_setup import http_log_hooks
from src.infrastructure.utils.datetime_utils import ensure_utc, utcnow

logger = logging.getLogger(__name__)

AUTORIZAR = "rode `python -m apps.ml_auth.main` para autorizar o app no Mercado Livre"


class MLAuthError(RuntimeError):
    """Sem token utilizável. A mensagem nunca contém credencial."""


class _InvalidGrantError(MLAuthError):
    """Refresh token (ou code) recusado: já usado, expirado ou revogado."""


class MongoTokenStore:
    """Um documento só, com o par de tokens atual."""

    COLLECTION = "ml_oauth"
    DOC_ID = "mercadolivre"

    def __init__(self, db: Database):
        self.col = db[self.COLLECTION]

    def load(self) -> dict[str, Any] | None:
        doc = self.col.find_one({"_id": self.DOC_ID}, {"_id": 0})
        if doc and doc.get("expires_at"):
            doc["expires_at"] = ensure_utc(doc["expires_at"])
        return doc

    def save(self, tokens: dict[str, Any], *, replaces: str | None) -> bool:
        """
        Grava o par. Com `replaces`, só grava se o refresh token atual ainda for
        esse (compare-and-set); devolve False se outro processo já trocou.
        """
        campos = {**tokens, "updated_at": utcnow()}
        if replaces is None:
            self.col.update_one({"_id": self.DOC_ID}, {"$set": campos}, upsert=True)
            return True
        res = self.col.update_one({"_id": self.DOC_ID, "refresh_token": replaces}, {"$set": campos})
        return res.matched_count == 1


class MercadoLivreAuth:
    TOKEN_PATH = "/oauth/token"

    def __init__(
        self,
        store: MongoTokenStore,
        *,
        client_id: str,
        client_secret: str,
        base_url: str | None = None,
        margin: dt.timedelta = dt.timedelta(minutes=5),
        timeout_s: int = 20,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], dt.datetime] = utcnow,
    ):
        self.store = store
        self._client_id = client_id
        self._client_secret = client_secret
        self._base_url = (base_url or settings.ML_BASE_URL).rstrip("/")
        self._margin = margin
        self._timeout_s = timeout_s
        self._transport = transport
        self._clock = clock
        self._client: httpx.Client | None = None

    @classmethod
    def from_settings(cls, db: Database, **kwargs) -> MercadoLivreAuth:
        if faltam := settings.missing("ML_CLIENT_ID", "ML_CLIENT_SECRET"):
            raise MLAuthError(f"faltam {', '.join(faltam)} no backend/.env")
        return cls(
            MongoTokenStore(db),
            client_id=settings.ML_CLIENT_ID,
            client_secret=settings.ML_CLIENT_SECRET,
            **kwargs,
        )

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self._base_url,
                timeout=self._timeout_s,
                headers={"Accept": "application/json"},
                transport=self._transport,
                event_hooks=http_log_hooks("mercadolivre_oauth"),
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # --- Uso ---------------------------------------------------------------

    def access_token(self) -> str:
        """Token válido para o header `Authorization`; renova se preciso."""
        doc = self._load()
        if self._valido(doc):
            return doc["access_token"]
        return self._renova(doc)

    def refresh(self, stale: str | None = None) -> str:
        """
        Renova à força — depois de um 401 com o token `stale`. Se outro
        processo já renovou (o token gravado é outro e válido), usa esse.
        """
        doc = self._load()
        if doc["access_token"] != stale and self._valido(doc):
            return doc["access_token"]
        return self._renova(doc)

    def exchange_code(
        self, code: str, *, redirect_uri: str, code_verifier: str | None = None
    ) -> dict[str, Any]:
        """Autorização inicial: troca o `code` pelo primeiro par e grava."""
        form = {
            "grant_type": "authorization_code",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
        if code_verifier:
            form["code_verifier"] = code_verifier
        novo = self._doc(self._chama(form))
        self.store.save(novo, replaces=None)
        # Para imprimir no terminal: sem token nenhum
        return {k: novo.get(k) for k in ("user_id", "scope", "expires_at")}

    # --- Interno -----------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        doc = self.store.load()
        if not doc or not doc.get("refresh_token"):
            raise MLAuthError(f"nenhum token do Mercado Livre gravado: {AUTORIZAR}")
        return doc

    def _valido(self, doc: dict[str, Any] | None) -> bool:
        return bool(
            doc
            and doc.get("access_token")
            and doc.get("expires_at")
            and doc["expires_at"] - self._margin > self._clock()
        )

    def _renova(self, doc: dict[str, Any]) -> str:
        usado = doc["refresh_token"]
        try:
            data = self._chama(
                {
                    "grant_type": "refresh_token",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": usado,
                }
            )
        except _InvalidGrantError:
            # Outro worker pode ter gastado o mesmo refresh token segundos antes
            atual = self.store.load()
            if atual and atual.get("refresh_token") != usado and self._valido(atual):
                logger.info("Token do ML renovado por outro processo; reaproveitando")
                return atual["access_token"]
            raise MLAuthError(
                f"o ML recusou o refresh token (invalid_grant): é preciso autorizar de novo; "
                f"{AUTORIZAR}"
            ) from None

        novo = self._doc(data)
        if not self.store.save(novo, replaces=usado):
            # Corrida perdida na gravação: o nosso token vale, o par gravado também
            logger.warning("Par de tokens do ML trocado por outro processo durante a renovação")
        logger.info("Token do ML renovado", extra={"expires_at": novo["expires_at"].isoformat()})
        return novo["access_token"]

    def _doc(self, data: dict[str, Any]) -> dict[str, Any]:
        if not data.get("access_token") or not data.get("refresh_token"):
            raise MLAuthError(
                "resposta do OAuth sem access/refresh token: confira se o app do ML tem o "
                "escopo offline_access"
            )
        return {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "expires_at": self._clock() + dt.timedelta(seconds=int(data.get("expires_in") or 0)),
            "user_id": data.get("user_id"),
            "scope": data.get("scope"),
        }

    def _chama(self, form: dict[str, str]) -> dict[str, Any]:
        try:
            return self._post_token(form)
        except httpx.HTTPError as e:
            # Esgotou o retry em erro transitório. Sem encadear: a exceção do
            # httpx carrega o request, e o corpo do request tem o secret.
            raise MLAuthError(f"OAuth do ML indisponível ({type(e).__name__})") from None

    @retry_transient(attempts=3, min_s=1, max_s=10)
    def _post_token(self, form: dict[str, str]) -> dict[str, Any]:
        r = self.client.post(self.TOKEN_PATH, data=form)
        if r.status_code < 400:
            return r.json()
        erro = httpx.HTTPStatusError("oauth/token", request=r.request, response=r)
        if is_transient(erro):
            raise erro  # retry_transient tenta de novo
        try:
            codigo = r.json().get("error") or ""
        except ValueError:
            codigo = ""
        if codigo == "invalid_grant":
            raise _InvalidGrantError("invalid_grant") from None
        raise MLAuthError(f"OAuth do ML respondeu HTTP {r.status_code} {codigo}".strip()) from None


# --- Autorização inicial (usado por apps/ml_auth) ------------------------------


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge S256) — RFC 7636."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


def authorization_url(
    auth_url: str,
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str | None = None,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    if code_challenge:
        params |= {"code_challenge": code_challenge, "code_challenge_method": "S256"}
    return f"{auth_url}?{urlencode(params)}"


def code_from_redirect(texto: str, state: str) -> str:
    """
    O `code` a partir do que o usuário colou: a URL inteira para onde o ML
    redirecionou (confere o `state`) ou só o code.
    """
    texto = texto.strip()
    if "://" not in texto and "?" not in texto:
        if not texto:
            raise MLAuthError("nenhum code informado")
        return texto
    q = {k: v[0] for k, v in parse_qs(urlsplit(texto).query).items()}
    if q.get("state") != state:
        raise MLAuthError("o state da URL não confere: autorização de outra tentativa?")
    if not q.get("code"):
        raise MLAuthError(f"a URL não tem code (erro do ML: {q.get('error', 'desconhecido')})")
    return q["code"]
