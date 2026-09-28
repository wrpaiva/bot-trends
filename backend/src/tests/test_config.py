# src/tests/test_config.py
"""
Config numa fonte só (TIE-8).

Quando cada módulo lia `os.environ` com o próprio nome e o próprio default,
o mesmo conceito aparecia com nomes diferentes (`LLM_MODEL` x `LLM_MODEL_NAME`,
TIE-2) e ninguém percebia. Agora tudo passa por `settings`.
"""

import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from src.infrastructure.config import Settings, settings

BACKEND = pathlib.Path(__file__).resolve().parents[2]
LEITURA_DIRETA = re.compile(r"\bos\.(environ|getenv)\b|\bgetenv\(")


def _codigo_de_producao():
    for raiz in ("src", "apps"):
        for arq in (BACKEND / raiz).rglob("*.py"):
            rel = arq.relative_to(BACKEND).as_posix()
            if rel.startswith("src/tests/") or rel == "src/infrastructure/config.py":
                continue
            yield rel, arq.read_text(encoding="utf-8")


def test_nenhum_modulo_le_variavel_de_ambiente_fora_do_config():
    infratores = [rel for rel, src in _codigo_de_producao() if LEITURA_DIRETA.search(src)]
    assert infratores == [], f"use `settings` em vez de os.environ: {infratores}"


def _settings(**kwargs) -> Settings:
    # _env_file=None: não deixa o backend/.env real vazar para o teste
    return Settings(_env_file=None, **kwargs)


def test_cors_origins_vira_lista_limpa():
    s = _settings(CORS_ORIGINS=" http://a.com, ,http://b.com ")
    assert s.cors_origins == ["http://a.com", "http://b.com"]


def test_flags_do_bootstrap_aceitam_texto_do_env():
    s = _settings(BOOTSTRAP_FETCH_ML_CATEGORIES="true", BOOTSTRAP_AUTO_ENABLE="false")
    assert s.BOOTSTRAP_FETCH_ML_CATEGORIES is True
    assert s.BOOTSTRAP_AUTO_ENABLE is False


@pytest.mark.parametrize("valor", [None, "", "   "])
def test_missing_trata_vazio_como_ausente(valor):
    assert _settings(API_KEY=valor).missing("API_KEY") == ["API_KEY"]


def test_missing_nao_acusa_o_que_esta_preenchido():
    assert _settings(API_KEY="k").missing("API_KEY") == []


def test_api_nao_sobe_sem_api_key(monkeypatch):
    from apps.api.main import app

    monkeypatch.setattr(settings, "API_KEY", None)
    with pytest.raises(RuntimeError, match="API_KEY"), TestClient(app):
        pass


def test_api_key_eh_lida_de_settings_na_requisicao(monkeypatch):
    from apps.api.main import app
    from src.infrastructure.security.rate_limit import limiter

    monkeypatch.setattr(settings, "API_KEY", "chave-certa")
    # O limiter aponta para o Redis do settings; aqui não há Redis
    monkeypatch.setattr(limiter, "enabled", False)
    with TestClient(app) as client:
        assert client.get("/categories", headers={"X-API-Key": "errada"}).status_code == 401
        assert client.get("/categories").status_code == 401


# --- Pesos do score (TIE-22) -------------------------------------------------


def test_pesos_do_score_vem_da_config():
    s = _settings(SCORE_W_SOCIAL="0.35", SCORE_W_VIEWS="0.05")
    assert s.score_weights().social == 0.35
    assert s.score_weights().views == 0.05
    assert s.hybrid_weights().as_dict() == {"numeric": 0.6, "llm": 0.4}


def test_pesos_invalidos_falham_na_subida():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="peso"):
        _settings(SCORE_W_SOCIAL="0.9")
    with pytest.raises(ValidationError, match="peso"):
        _settings(SCORE_W_NUMERIC="0.5")


def test_normalizacao_do_score_validada():
    from pydantic import ValidationError

    assert _settings().SCORE_NORMALIZATION == "percentile"
    with pytest.raises(ValidationError):
        _settings(SCORE_NORMALIZATION="zscore")
    with pytest.raises(ValidationError):
        _settings(SCORE_PERCENTILE_MIN_GROUP="1")
