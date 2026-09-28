# src/application/prompt_builder.py

from __future__ import annotations

import json
from typing import Any

from src.domain.trend_models import NumericScoreResult, TrendInput

SYSTEM = (
    "Você é um analista especialista em tendências de e-commerce e viralização social. "
    "Seja objetivo e técnico. Não invente dados. "
    "Quando faltar evidência, declare limitação e reduza confidence."
)


def build_trend_prompt(ti: TrendInput, numeric: NumericScoreResult) -> dict[str, Any]:
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
            "views_total": ti.views_24h,
            "engagement_total": ti.engagement_24h,
            "mentions": ti.mentions_24h,
            "age_hours": ti.age_hours,
            "views_per_hour": ti.views_per_hour,
            "engagement_per_hour": ti.engagement_per_hour,
            "social_velocity": ti.social_velocity,
            "has_shop_product": ti.has_shop_product,
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
        "- Tendência é ritmo, não total: use views_per_hour e age_hours; muitas views "
        "acumuladas em vídeo antigo NÃO são tendência\n"
        "- has_shop_product=true indica produto à venda no vídeo\n"
        "- Se queda consistente em engajamento/score, use EM_QUEDA\n"
        "- Se faltarem dados críticos, reduza confidence\n\n"
        f"Dados:\n{json.dumps(data, ensure_ascii=False)}"
    )

    return {"system": SYSTEM, "user": user}
