# src/domain/interfaces.py

from __future__ import annotations
from abc import ABC, abstractmethod
from src.domain.trend_models import LLMResult


class LLMClient(ABC):
    @abstractmethod
    def analyze_trend(self, system: str, user: str) -> LLMResult:
        """Retorna análise estruturada do motor de IA."""
        raise NotImplementedError