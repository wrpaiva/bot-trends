# src/tests/test_llm_client.py
"""
OpenAICompatibleLLMClient com HTTP mockado (TIE-12): resposta válida, JSON
malformado e timeout. Os dois últimos precisam levantar exceção — é ela que
aciona o fallback numérico do HybridTrendEngine.
"""

import json

import httpx
import pytest

from src.domain.trend_models import LLMResult
from src.infrastructure.llm import openai_compatible as llm_mod
from src.infrastructure.llm.openai_compatible import OpenAICompatibleLLMClient
from src.infrastructure.llm.schema import LLMResponseError

RESPOSTA = {
    "trend_classification": "SUBINDO",
    "potential_score_0_100": 72,
    "risk_level": "MEDIO",
    "analysis": "Engajamento crescente.",
    "recommendation": "Monitorar estoque.",
    "confidence_0_1": 0.8,
}


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(llm_mod.settings, "LLM_BASE_URL", "https://llm.exemplo.com/v1/")
    monkeypatch.setattr(llm_mod.settings, "LLM_API_KEY", "chave-llm")
    monkeypatch.setattr(llm_mod.settings, "LLM_MODEL", "modelo-x")


def _client(handler) -> OpenAICompatibleLLMClient:
    return OpenAICompatibleLLMClient(transport=httpx.MockTransport(handler))


def _completion(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_resposta_valida_vira_llmresult():
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        return _completion(json.dumps(RESPOSTA))

    res = _client(handler).analyze_trend("sistema", "usuário")

    assert res == LLMResult(
        trend_classification="SUBINDO",
        potential_score_0_100=72.0,
        risk_level="MEDIO",
        analysis="Engajamento crescente.",
        recommendation="Monitorar estoque.",
        confidence_0_1=0.8,
    )
    (req,) = requests
    assert str(req.url) == "https://llm.exemplo.com/v1/chat/completions"
    assert req.headers["Authorization"] == "Bearer chave-llm"
    corpo = json.loads(req.content)
    assert corpo["model"] == "modelo-x"
    assert corpo["response_format"] == {"type": "json_object"}


def test_confidence_ausente_usa_default():
    sem_conf = {k: v for k, v in RESPOSTA.items() if k != "confidence_0_1"}
    res = _client(lambda r: _completion(json.dumps(sem_conf))).analyze_trend("s", "u")
    assert res.confidence_0_1 == 0.6


def test_json_malformado_levanta():
    with pytest.raises(LLMResponseError):
        _client(lambda r: _completion("isto não é json")).analyze_trend("s", "u")


def test_campo_obrigatorio_ausente_levanta():
    incompleto = {k: v for k, v in RESPOSTA.items() if k != "risk_level"}
    with pytest.raises(LLMResponseError, match="risk_level"):
        _client(lambda r: _completion(json.dumps(incompleto))).analyze_trend("s", "u")


@pytest.mark.parametrize(
    "corpo", [{}, {"choices": []}, {"choices": [{"message": {}}]}, {"error": "quota"}]
)
def test_envelope_fora_do_formato_levanta(corpo):
    with pytest.raises(LLMResponseError):
        _client(lambda r: httpx.Response(200, json=corpo)).analyze_trend("s", "u")


def test_timeout_levanta():
    def handler(request):
        raise httpx.ReadTimeout("demorou", request=request)

    with pytest.raises(httpx.TimeoutException):
        _client(handler).analyze_trend("s", "u")


def test_erro_http_levanta_sem_vazar_a_chave():
    with pytest.raises(httpx.HTTPStatusError) as exc:
        _client(lambda r: httpx.Response(500)).analyze_trend("s", "u")
    # A chave vai no header; a mensagem (que acaba em debug.llm_error) não a contém
    assert "chave-llm" not in str(exc.value)


def test_sem_config_falha_rapido(monkeypatch):
    monkeypatch.setattr(llm_mod.settings, "LLM_API_KEY", None)
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        OpenAICompatibleLLMClient()
