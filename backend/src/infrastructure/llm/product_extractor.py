# src/infrastructure/llm/product_extractor.py
"""
Extração, pelo LLM, do produto que um vídeo do TikTok vende (TIE-18).

A legenda quase nunca nomeia o produto: em 2026-10-16, a maioria dos 72 vídeos
comerciais em português era compilado ("Itens da Shopee para o banheiro"). A
consulta montada por regra casava com o que não era ("cara milionária" → livro),
então quem decide é o LLM, e `null` é a resposta certa para compilado. Os
exemplos vêm de legendas reais.
"""

from __future__ import annotations

import json

from src.domain.interfaces import ProductExtractor
from src.infrastructure.circuit_breaker import CircuitBreaker, CircuitOpenError
from src.infrastructure.llm.openai_compatible import OpenAICompatibleLLMClient
from src.infrastructure.llm.schema import LLMResponseError, parse_product_extraction

# Mudou o prompt? Suba a versão: o vídeo grava com qual versão foi processado
EXTRACTION_PROMPT_VERSION = 1

SYSTEM = """Você recebe a legenda de um vídeo do TikTok que vende produtos.
Diga qual produto específico o vídeo vende, como uma consulta curta para buscar
esse produto num marketplace: de 2 a 6 palavras, o tipo do produto e o que o
distingue (material, função, tamanho). Sem nome de loja, sem preço, sem adjetivo
de propaganda ("lindo", "barato", "de rico").

Se a legenda não nomeia um produto específico — compilado de vários itens
(mesmo quando lista os itens um a um), "achadinhos da Shopee para casa", lista
de dicas — responda null. Na dúvida,
null: uma consulta errada vincula o vídeo ao produto errado.

Responda só JSON: {"produto": "<consulta>"} ou {"produto": null}.

Exemplos:
Legenda: "kit brocas corte lateral. produto indispensável #achadinhos #ferramenta"
{"produto": "kit brocas corte lateral"}
Legenda: "Se sua cozinha vive bagunçada, olha esse achadinho! Esses potes de condimentos em aço inox com base giratória"
{"produto": "porta condimentos giratório aço inox"}
Legenda: "Esse carregador 3 em 1 da Shopee é pequeno, moderno e superprático! Formato de cubo"
{"produto": "carregador 3 em 1 cubo"}
Legenda: "Itens Da Shopee Para O Seu Banheiro #achadinhos #shopee"
{"produto": null}
Legenda: "Itens de cozinha que parecem de loja cara 2️⃣ 2 Jarras de Vidro R$36,98 - ID: AHN-QGM 3️⃣ Kit Facas 5 peças R$15,78 - ID: BUR-UQW"
{"produto": null}
Legenda: "Coisas de Rico que Comprei na Shopee #achadinhos #comprinhas"
{"produto": null}
Legenda: "todas as peças na bio #streetwear #moletom #outfit"
{"produto": null}"""


class LLMProductExtractor(ProductExtractor):
    """
    Extrator atrás do breaker do LLM, como a análise (TIE-37): circuito aberto →
    `CircuitOpenError` sem chamar; resposta fora do schema não conta como falha
    do serviço.
    """

    def __init__(self, client: OpenAICompatibleLLMClient, breaker: CircuitBreaker | None = None):
        self.client = client
        self.breaker = breaker

    def extract_product(self, caption: str) -> str | None:
        if not (caption or "").strip():
            return None
        if self.breaker is not None and not self.breaker.allow():
            st = self.breaker.status()
            raise CircuitOpenError(
                f"circuito do LLM aberto (nova tentativa em {st['retry_in_s']} s)"
            )
        user = f"Legenda: {json.dumps(caption.strip(), ensure_ascii=False)}"
        try:
            produto = self.client.complete_json(SYSTEM, user, parse_product_extraction)
        except LLMResponseError:
            if self.breaker is not None:
                self.breaker.record_success()
            raise
        except Exception:
            if self.breaker is not None:
                self.breaker.record_failure()
            raise
        if self.breaker is not None:
            self.breaker.record_success()
        return produto
