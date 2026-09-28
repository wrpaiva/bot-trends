# src/infrastructure/llm/schema.py
"""
Validação da resposta do LLM (TIE-24).

Fica na infraestrutura porque usa pydantic; o domínio só conhece `LLMResult`.
Regras:
  - classificação e risco restritos aos valores do domínio (caixa normalizada);
  - scores fora da faixa são clampados, não rejeitados — o LLM costuma errar
    a escala, não o sentido;
  - qualquer outra coisa vira `LLMResponseError`, que o engine trata como
    "LLM indisponível" e cai no fallback numérico.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)

from src.domain.trend_models import LLMResult, RiskLevel, TrendClass

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)

Texto = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Numero = Annotated[float, Field(allow_inf_nan=False)]


class LLMResponseError(ValueError):
    """Resposta do LLM fora do contrato."""


class LLMResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    trend_classification: TrendClass
    potential_score_0_100: Numero
    risk_level: RiskLevel
    analysis: Texto
    recommendation: Texto
    confidence_0_1: Numero = 0.6

    @field_validator("trend_classification", "risk_level", mode="before")
    @classmethod
    def _normaliza_enum(cls, v: Any) -> Any:
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("potential_score_0_100")
    @classmethod
    def _clamp_score(cls, v: float) -> float:
        return min(max(v, 0.0), 100.0)

    @field_validator("confidence_0_1")
    @classmethod
    def _clamp_confidence(cls, v: float) -> float:
        return min(max(v, 0.0), 1.0)

    def to_domain(self) -> LLMResult:
        return LLMResult(**self.model_dump())


def _carrega(content: str | dict) -> dict:
    if isinstance(content, dict):
        return content
    texto = (content or "").strip()
    # Alguns gateways embrulham o JSON em ```json ... ``` mesmo com json_object
    m = _FENCE.match(texto)
    if m:
        texto = m.group(1)
    try:
        obj = json.loads(texto)
    except json.JSONDecodeError as e:
        raise LLMResponseError(f"resposta do LLM não é JSON válido: {e.msg}") from None
    if not isinstance(obj, dict):
        raise LLMResponseError(f"resposta do LLM não é um objeto JSON: {type(obj).__name__}")
    return obj


def parse_llm_content(content: str | dict) -> LLMResult:
    """Valida o conteúdo devolvido pelo LLM e converte para o modelo de domínio."""
    obj = _carrega(content)
    try:
        return LLMResponse.model_validate(obj).to_domain()
    except ValidationError as e:
        problemas = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()
        )
        raise LLMResponseError(f"resposta do LLM fora do schema — {problemas}") from None
