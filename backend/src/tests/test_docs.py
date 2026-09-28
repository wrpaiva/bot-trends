# src/tests/test_docs.py
"""
A documentação não pode divergir do código (TIE-33).

O README documentou por meses um `GET /search` que não existia e omitia rotas
que existiam. Aqui a tabela de endpoints do README é comparada com as rotas
registradas no app, e o `.env.example` com os campos do `Settings`.

O README fica na raiz do repositório: no CI (checkout completo) estes testes
rodam sempre; num container que só monta `backend/`, são pulados.
"""

import pathlib
import re

import pytest
from fastapi.routing import APIRoute

from apps.api.main import app
from src.infrastructure.config import Settings

BACKEND = pathlib.Path(__file__).resolve().parents[2]
README = BACKEND.parent / "README.md"

# Linha da tabela de endpoints: | `GET /rankings/latest` | ... |
LINHA_ENDPOINT = re.compile(r"^\|\s*`(GET|POST|PUT|PATCH|DELETE) (/[^`\s]*)`", re.MULTILINE)

# Rotas geradas pelo FastAPI, não pelo projeto
AUTOMATICAS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def _rotas_do_app() -> set[tuple[str, str]]:
    return {
        (metodo, rota.path)
        for rota in app.routes
        if isinstance(rota, APIRoute) and rota.path not in AUTOMATICAS
        for metodo in rota.methods
    }


@pytest.mark.skipif(not README.exists(), reason="README.md da raiz fora do container")
def test_endpoints_do_readme_batem_com_as_rotas():
    documentadas = set(LINHA_ENDPOINT.findall(README.read_text(encoding="utf-8")))

    assert documentadas, "tabela de endpoints não encontrada no README"
    assert documentadas - _rotas_do_app() == set(), "README documenta rota que não existe"
    assert _rotas_do_app() - documentadas == set(), "rota existe mas não está no README"


def test_env_example_cobre_todo_o_settings():
    # API_KEY e CORS_ORIGINS chegam pelo compose (.env da raiz), não pelo backend/.env
    via_compose = {"API_KEY", "CORS_ORIGINS"}
    exemplo = (BACKEND / ".env.example").read_text(encoding="utf-8")
    declaradas = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", exemplo, re.MULTILINE))

    faltando = set(Settings.model_fields) - via_compose - declaradas
    assert faltando == set(), f"documente no backend/.env.example: {sorted(faltando)}"
