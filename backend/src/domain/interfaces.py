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
