# src/domain/interfaces.py

from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.trend_models import LLMResult


class LLMClient(ABC):
    @abstractmethod
    def analyze_trend(self, system: str, user: str) -> LLMResult:
        """Retorna análise estruturada do motor de IA."""
        raise NotImplementedError


class LLMCache(ABC):
    """Guarda análises do LLM por chave (TIE-25). Implementação na infraestrutura."""

    @abstractmethod
    def get(self, key: str) -> LLMResult | None:
        raise NotImplementedError

    @abstractmethod
    def set(self, key: str, value: LLMResult) -> None:
        raise NotImplementedError


class ProductExtractor(ABC):
    """Produto que a legenda de um vídeo vende, como consulta de busca (TIE-18)."""

    @abstractmethod
    def extract_product(self, caption: str) -> str | None:
        """Consulta curta (2–8 palavras), ou None se a legenda não nomeia um produto."""
        raise NotImplementedError
