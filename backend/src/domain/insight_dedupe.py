# src/domain/insight_dedupe.py
"""
Quando um insight novo repete o anterior do mesmo produto (TIE-47). Regra pura.

Em 2026-10-18, 94% dos insights dos 7 dias anteriores tinham score, classificação
e risco iguais aos do insight anterior do produto: a análise roda a cada 30 min,
e com o cache do LLM e métricas paradas o resultado se repete. Eram 50 dos 51 MB
do banco. Em vez de gravar outro documento, a análise "confirma" o anterior: o
documento passa a valer até o `ts` novo e guarda desde quando (`first_ts`).

Só entra na chave o que muda a decisão de quem lê o ranking ou recebe o alerta.
Texto da análise e sinais brutos ficam de fora: com scores iguais, eles são
atualizados no documento confirmado, não viram um ponto novo da série.
"""

from __future__ import annotations

from typing import Any

# Campos do insight que, iguais, fazem do novo uma repetição do anterior
CAMPOS_DA_CHAVE = (
    "final_score",
    "numeric_score",
    "llm_score",
    "trend_classification",
    "risk_level",
    "prompt_version",
    "commercial_marker",
    "window_hours",
)


def _arred(v: Any) -> Any:
    # Float vindo do banco e recalculado pode diferir na 12ª casa
    return round(v, 4) if isinstance(v, float) else v


def chave(insight: dict[str, Any]) -> tuple:
    """O que precisa ser igual para o insight ser repetição."""
    debug = insight.get("debug") or {}
    pesos = insight.get("score_weights") or {}
    return (
        *(_arred(insight.get(c)) for c in CAMPOS_DA_CHAVE),
        tuple(sorted(insight.get("sources") or [])),
        tuple(sorted((k, _arred(v)) for k, v in pesos.items())),
        # Fallback e LLM com o mesmo número não são a mesma análise
        bool(debug.get("llm_error")),
    )


def repete(anterior: dict[str, Any] | None, novo: dict[str, Any]) -> bool:
    """True se `novo` não traz nada que `anterior` já não dissesse."""
    return anterior is not None and chave(anterior) == chave(novo)
