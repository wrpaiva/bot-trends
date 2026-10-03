# src/domain/llm_cache.py
"""
Chave do cache de análises do LLM (TIE-25). Regra pura, sem I/O.

Derivada das métricas **brutas** de entrada do produto, não do prompt inteiro:
com a normalização por percentil (TIE-21) os componentes numéricos mudam
quando *outros* produtos mudam, e isso zeraria a taxa de acerto sem trazer
informação nova ao LLM. O score numérico é sempre recalculado; só a opinião do
LLM é reaproveitada.
Pela mesma razão a referência do grupo que vai no prompt (mediana/p90, TIE-26)
fica fora da chave.
"""

from __future__ import annotations

import hashlib
import json

from .trend_models import TrendInput

# Suba quando o prompt ou o schema de resposta mudar: invalida o cache inteiro
PROMPT_VERSION = "v4"  # v4: sem score pronto, few-shot, referência do grupo (TIE-26)

# Casas decimais nos floats: ruído de ponto flutuante não pode virar miss
_CASAS = 4


def llm_cache_key(ti: TrendInput) -> str:
    def _r(x: float | None) -> float | None:
        return None if x is None else round(float(x), _CASAS)

    entrada = {
        "v": PROMPT_VERSION,
        "product_id": ti.product_id,
        "title": ti.title,
        "category": ti.category,
        "price": _r(ti.price),
        "sold_quantity": ti.sold_quantity,
        "views_24h": ti.views_24h,
        "engagement_24h": ti.engagement_24h,
        "mentions_24h": ti.mentions_24h,
        "rank_momentum": _r(ti.rank_momentum),
        "reviews_velocity": _r(ti.reviews_velocity),
        "social_velocity": _r(ti.social_velocity),
        "price_volatility": _r(ti.price_volatility),
        "previous_final_score": _r(ti.previous_final_score),
        "age_hours": _r(ti.age_hours),
        "views_per_hour": _r(ti.views_per_hour),
        "engagement_per_hour": _r(ti.engagement_per_hour),
        "has_shop_product": ti.has_shop_product,
        "commercial_marker": ti.commercial_marker,
        "n_readings": ti.n_readings,
    }
    bruto = json.dumps(entrada, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(bruto.encode()).hexdigest()
