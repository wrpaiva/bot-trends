# apps/ml_auth/main.py
"""
Autorização inicial do app no Mercado Livre (TIE-41). Uso, uma vez (e de novo
só se o ML recusar o refresh token — a coleta avisa com `invalid_grant`):

    docker compose run --rm -it api python -m apps.ml_auth.main
    docker compose run --rm api python -m apps.ml_auth.main --status

Imprime a URL de autorização; você abre no navegador, entra com a conta do ML
e autoriza. O ML redireciona para `ML_REDIRECT_URI` — a página pode nem
carregar, não importa: copie a URL da barra de endereço (tem `?code=...`) e
cole aqui. O code vira o primeiro par de tokens, gravado no Mongo; a partir daí
o worker renova sozinho. Nenhum token é impresso.
"""

from __future__ import annotations

import argparse
import secrets
from collections.abc import Callable

from pymongo.database import Database

from src.infrastructure.collectors.ml_auth import (
    MercadoLivreAuth,
    MLAuthError,
    MongoTokenStore,
    authorization_url,
    code_from_redirect,
    pkce_pair,
)
from src.infrastructure.config import settings
from src.infrastructure.utils.datetime_utils import utcnow


def status(db: Database, out: Callable[[str], None] = print) -> int:
    doc = MongoTokenStore(db).load()
    if not doc:
        out("Nenhum token gravado. Rode sem --status para autorizar.")
        return 1
    restante = doc["expires_at"] - utcnow()
    estado = "válido" if restante.total_seconds() > 0 else "vencido (renova na próxima coleta)"
    out(
        f"Token do ML: {estado}; expira em {doc['expires_at']:%Y-%m-%d %H:%M} UTC; "
        f"user_id={doc.get('user_id')}; renovado em {doc.get('updated_at'):%Y-%m-%d %H:%M} UTC"
    )
    return 0


def autorizar(
    db: Database,
    *,
    pkce: bool = True,
    ler: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
    **auth_kwargs,
) -> int:
    if faltam := settings.missing("ML_CLIENT_ID", "ML_CLIENT_SECRET", "ML_REDIRECT_URI"):
        out(f"Faltam {', '.join(faltam)} no backend/.env")
        return 1

    state = secrets.token_urlsafe(16)
    verifier, challenge = pkce_pair() if pkce else (None, None)
    out("1. Abra esta URL no navegador e autorize o app:\n")
    out(
        authorization_url(
            settings.ML_AUTH_URL,
            client_id=settings.ML_CLIENT_ID,
            redirect_uri=settings.ML_REDIRECT_URI,
            state=state,
            code_challenge=challenge,
        )
    )
    out("\n2. Depois de autorizar, copie a URL da barra de endereço (tem ?code=...).")

    auth = MercadoLivreAuth.from_settings(db, **auth_kwargs)
    try:
        code = code_from_redirect(ler("Cole aqui a URL (ou só o code): "), state)
        info = auth.exchange_code(
            code, redirect_uri=settings.ML_REDIRECT_URI, code_verifier=verifier
        )
    except MLAuthError as e:
        out(f"Falhou: {e}")
        return 1
    finally:
        auth.close()

    out(
        f"OK: token gravado (user_id={info['user_id']}, escopo '{info['scope']}', "
        f"expira {info['expires_at']:%Y-%m-%d %H:%M} UTC). O worker renova sozinho."
    )
    return 0


def main() -> int:
    from src.infrastructure.db.mongo import get_db
    from src.infrastructure.logging_setup import configure_logging

    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--status", action="store_true", help="só mostra o token gravado")
    parser.add_argument(
        "--sem-pkce",
        action="store_true",
        help="não envia code_challenge (se o app do ML não tiver PKCE habilitado)",
    )
    args = parser.parse_args()

    db = get_db()
    if args.status:
        return status(db)
    return autorizar(db, pkce=not args.sem_pkce)


if __name__ == "__main__":
    raise SystemExit(main())
