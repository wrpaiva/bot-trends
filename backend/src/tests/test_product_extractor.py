# src/tests/test_product_extractor.py
"""
Extração do produto que um vídeo do TikTok vende (TIE-18). A legenda quase
nunca nomeia o produto — a maioria é compilado ("Itens da Shopee para o
banheiro") — e a regra fixa gerava consulta que casava com livro. Por isso o
LLM decide, e `null` é resposta válida e frequente.
"""

import json

import httpx
import pytest

from src.infrastructure.circuit_breaker import CircuitBreaker, CircuitOpenError, MemoryBreakerStore
from src.infrastructure.llm import openai_compatible as llm_mod
from src.infrastructure.llm.openai_compatible import OpenAICompatibleLLMClient
from src.infrastructure.llm.product_extractor import LLMProductExtractor
from src.infrastructure.llm.schema import LLMResponseError, parse_product_extraction


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(llm_mod.settings, "LLM_BASE_URL", "https://llm.exemplo.com/v1")
    monkeypatch.setattr(llm_mod.settings, "LLM_API_KEY", "chave-llm")
    monkeypatch.setattr(llm_mod, "safe_inc", lambda *a, **kw: None)


def _completion(obj) -> httpx.Response:
    conteudo = obj if isinstance(obj, str) else json.dumps(obj)
    return httpx.Response(200, json={"choices": [{"message": {"content": conteudo}}]})


def _extrator(handler, breaker=None) -> LLMProductExtractor:
    client = OpenAICompatibleLLMClient(transport=httpx.MockTransport(handler), temperature=0.0)
    return LLMProductExtractor(client, breaker=breaker)


# --- schema ------------------------------------------------------------------


def test_produto_nomeado_vira_consulta_limpa():
    assert parse_product_extraction('{"produto": "  kit brocas corte lateral "}') == (
        "kit brocas corte lateral"
    )


@pytest.mark.parametrize("valor", [None, "", "   "])
def test_sem_produto_eh_none(valor):
    assert parse_product_extraction(json.dumps({"produto": valor})) is None


def test_consulta_longa_demais_eh_resposta_invalida():
    # Consulta de busca tem de 2 a 8 palavras; frase inteira é o LLM falhando
    with pytest.raises(LLMResponseError):
        parse_product_extraction(json.dumps({"produto": " ".join(["palavra"] * 9)}))


def test_campo_ausente_eh_resposta_invalida():
    with pytest.raises(LLMResponseError):
        parse_product_extraction('{"outro": 1}')


# --- extrator ----------------------------------------------------------------


def test_extrator_manda_a_legenda_e_devolve_a_consulta():
    pedidos = []

    def handler(request):
        pedidos.append(json.loads(request.content))
        return _completion({"produto": "porta temperos giratorio"})

    legenda = "Se sua cozinha vive bagunçada, olha esse porta temperos giratório! #achadinhos"
    assert _extrator(handler).extract_product(legenda) == "porta temperos giratorio"

    payload = pedidos[0]
    assert payload["temperature"] == 0.0
    assert legenda in payload["messages"][1]["content"]
    # Os exemplos ensinam o null do compilado: é o caso mais comum
    assert "null" in payload["messages"][0]["content"]


def test_compilado_sem_produto_devolve_none():
    extrator = _extrator(lambda r: _completion({"produto": None}))
    assert extrator.extract_product("Itens da Shopee para o banheiro #achadinhos") is None


def test_legenda_vazia_nem_chama_o_llm():
    def handler(request):
        raise AssertionError("não devia chamar o LLM")

    assert _extrator(handler).extract_product("   ") is None


def test_circuito_aberto_nao_chama_o_llm():
    breaker = CircuitBreaker("llm", MemoryBreakerStore(), threshold=1, cooldown_s=60)
    breaker.record_failure()

    def handler(request):
        raise AssertionError("não devia chamar o LLM")

    with pytest.raises(CircuitOpenError):
        _extrator(handler, breaker).extract_product("kit brocas")


def test_falha_do_servico_conta_no_breaker_e_resposta_invalida_nao():
    breaker = CircuitBreaker("llm", MemoryBreakerStore(), threshold=1, cooldown_s=60)

    with pytest.raises(LLMResponseError):
        _extrator(lambda r: _completion("não é json"), breaker).extract_product("kit brocas")
    assert breaker.allow()

    with pytest.raises(httpx.HTTPStatusError):
        _extrator(lambda r: httpx.Response(429), breaker).extract_product("kit brocas")
    assert not breaker.allow()
