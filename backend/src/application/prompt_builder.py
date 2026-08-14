# src/application/prompt_builder.py

from __future__ import annotations
import json
from typing import Dict, Any
from src.domain.trend_models import TrendInput, NumericScoreResult


SYSTEM = (
    "Você é um analista especialista em tendências de e-commerce e viralização social. "
    "Seja objetivo e técnico. Não invente dados. "
    "Quando faltar evidência, declare limitação e reduza confidence."
)


def build_trend_prompt(ti: TrendInput, numeric: NumericScoreResult) -> Dict[str, Any]:
    data = {
        "product": {
            "product_id": ti.product_id,
            "title": ti.title,
            "category": ti.category,
        },
        "marketplace": {
            "price": ti.price,
            "sold_quantity": ti.sold_quantity,
        },
        "social": {
            "views_24h": ti.views_24h,
            "engagement_24h": ti.engagement_24h,
            "mentions_24h": ti.mentions_24h,
            "social_velocity": ti.social_velocity,
        },
        "history": {
            "rank_momentum": ti.rank_momentum,
            "reviews_velocity": ti.reviews_velocity,
            "price_volatility": ti.price_volatility,
            "previous_final_score": ti.previous_final_score,
        },
        "numeric_score": {
            "score_0_100": numeric.score_0_100,
            "components": numeric.components,
        },
    }

    user = (
        "Analise os dados e responda APENAS em JSON válido com o schema:\n"
        "{\n"
        '  "trend_classification": "ESTAVEL|SUBINDO|VIRALIZANDO|PICO_TEMPORARIO|EM_QUEDA",\n'
        '  "potential_score_0_100": number,\n'
        '  "risk_level": "BAIXO|MEDIO|ALTO",\n'
        '  "analysis": string,\n'
        '  "recommendation": string,\n'
        '  "confidence_0_1": number\n'
        "}\n\n"
        "Regras:\n"
        "- Se houver pico social sem sustentação, use PICO_TEMPORARIO\n"
        "- Se social_velocity alto e engajamento forte, pode ser VIRALIZANDO\n"
        "- Se queda consistente em engajamento/score, use EM_QUEDA\n"
        "- Se faltarem dados críticos, reduza confidence\n\n"
        f"Dados:\n{json.dumps(data, ensure_ascii=False)}"
    )

    return {"system": SYSTEM, "user": user}