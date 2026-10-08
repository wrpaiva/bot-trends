# src/application/prompt_builder.py
"""
Prompt da análise de tendência (TIE-26).

O LLM recebe os SINAIS do produto e a referência do grupo, nunca o score
numérico pronto: com ele no prompt, o `llm_score` dos 20 primeiros insights
reais ficou a ±0,4 do numérico — o modelo copiava a nota e o peso do LLM no
`final_score` não acrescentava nada. Pelo mesmo motivo não vai o score final
anterior (que é, quase todo, o numérico anterior).

Mudou o texto, os exemplos ou os campos? Suba `PROMPT_VERSION` em
`src/domain/llm_cache.py`, senão o cache devolve análise do prompt antigo.
"""

from __future__ import annotations

import json
from typing import Any

from src.domain.percentile import Normalization
from src.domain.trend_models import TrendInput

# Exemplos sintéticos de vídeo: um por classificação, mais o caso "novo sem tração"; depois, os
# de item do marketplace (TIE-42). Respeitam o que os sinais
# conseguem medir: social_velocity nunca é negativo (desaceleração aparece
# como 0 com 2+ leituras), e com leitura única ele vale 0 sem dizer nada.
_REF = {
    "base": "global",
    "produtos_no_grupo": 40,
    "views_per_hour": {"mediana": 1200.0, "p90": 6000.0},
    "engagement_per_hour": {"mediana": 60.0, "p90": 320.0},
    "social_velocity": {"mediana": 0.0, "p90": 0.6},
}

FEW_SHOT: list[dict[str, Any]] = [
    {
        "entrada": {
            "social": {
                "age_hours": 18.0,
                "views_per_hour": 24000.0,
                "engagement_per_hour": 1900.0,
                "social_velocity": 1.4,
                "leituras": 3,
            },
            "referencia": _REF,
        },
        "saida": {
            "trend_classification": "VIRALIZANDO",
            "potential_score_0_100": 90,
            "risk_level": "MEDIO",
            "analysis": "Ritmo 20x a mediana do grupo e acima do p90; entre as duas "
            "últimas leituras as views/h mais que dobraram em relação à média de vida.",
            "recommendation": "Agir agora: garantir fornecedor e testar estoque pequeno. "
            "Reavaliar em 24 h, porque viral jovem pode esfriar rápido.",
            "confidence_0_1": 0.8,
        },
    },
    {
        "entrada": {
            "social": {
                "age_hours": 52.0,
                "views_per_hour": 4100.0,
                "engagement_per_hour": 260.0,
                "social_velocity": 0.35,
                "leituras": 4,
            },
            "referencia": _REF,
        },
        "saida": {
            "trend_classification": "SUBINDO",
            "potential_score_0_100": 70,
            "risk_level": "BAIXO",
            "analysis": "Ritmo 3x a mediana, ainda abaixo do p90, e acelerando "
            "(ritmo recente 35% acima da média de vida) depois de 2 dias no ar.",
            "recommendation": "Colocar em observação prioritária e cotar fornecedores; "
            "se a aceleração se mantiver na próxima leitura, vale testar.",
            "confidence_0_1": 0.7,
        },
    },
    {
        "entrada": {
            "social": {
                "age_hours": 200.0,
                "views_per_hour": 1100.0,
                "engagement_per_hour": 55.0,
                "social_velocity": 0.0,
                "leituras": 5,
            },
            "referencia": _REF,
        },
        "saida": {
            "trend_classification": "ESTAVEL",
            "potential_score_0_100": 40,
            "risk_level": "BAIXO",
            "analysis": "Ritmo na mediana do grupo há mais de 8 dias, sem aceleração "
            "em 5 leituras: demanda constante, sem sinal de tendência nova.",
            "recommendation": "Não priorizar como tendência; serve como produto de "
            "catálogo se a margem for boa.",
            "confidence_0_1": 0.75,
        },
    },
    {
        "entrada": {
            "social": {
                "age_hours": 9.0,
                "views_per_hour": 9000.0,
                "engagement_per_hour": 120.0,
                "social_velocity": 0.0,
                "leituras": 3,
            },
            "referencia": _REF,
        },
        "saida": {
            "trend_classification": "PICO_TEMPORARIO",
            "potential_score_0_100": 45,
            "risk_level": "ALTO",
            "analysis": "Ritmo acima do p90, mas o engajamento por view é baixo e o "
            "ritmo recente não supera a média de vida em 3 leituras: alcance de "
            "algoritmo sem interesse que se sustente.",
            "recommendation": "Não comprar estoque com base neste vídeo; acompanhar "
            "por mais 24 h para ver se o ritmo se mantém.",
            "confidence_0_1": 0.6,
        },
    },
    {
        "entrada": {
            "social": {
                "age_hours": 8.0,
                "views_per_hour": 40.0,
                "engagement_per_hour": 2.0,
                "social_velocity": 0.0,
                "leituras": 1,
            },
            "referencia": _REF,
        },
        "saida": {
            "trend_classification": "ESTAVEL",
            "potential_score_0_100": 20,
            "risk_level": "MEDIO",
            "analysis": "Vídeo de 8 h com 40 views/h, 30x abaixo da mediana, em leitura "
            "única: ainda sem tração. Não é queda, porque nunca houve atenção a perder.",
            "recommendation": "Nada a fazer por enquanto; só volta a interessar se "
            "aparecer aceleração nas próximas leituras.",
            "confidence_0_1": 0.5,
        },
    },
    {
        "entrada": {
            "social": {
                "age_hours": 500.0,
                "views_per_hour": 300.0,
                "engagement_per_hour": 12.0,
                "social_velocity": 0.0,
                "leituras": 6,
            },
            "referencia": _REF,
        },
        "saida": {
            "trend_classification": "EM_QUEDA",
            "potential_score_0_100": 15,
            "risk_level": "ALTO",
            "analysis": "Vídeo de 3 semanas com ritmo a um quarto da mediana e sem "
            "aceleração em 6 leituras: a atenção já passou.",
            "recommendation": "Evitar: a janela de oportunidade ficou para trás.",
            "confidence_0_1": 0.75,
        },
    },
    # TIE-42: item do ML. Sem vídeo (social e referência null); o sinal é o
    # ranking de mais vendidos. rank_momentum = log(início/agora)/log(20), e
    # vale 0 na queda — por isso a queda só aparece comparando as posições.
    {
        "entrada": {
            "produto": {"fonte": "mercadolivre"},
            "marketplace": {
                "posicao_ranking": 4,
                "posicao_ranking_inicio_janela": 17,
                "rank_momentum": 0.48,
                "leituras": 4,
                "price": 59.9,
            },
            "social": None,
            "referencia": None,
        },
        "saida": {
            "trend_classification": "SUBINDO",
            "potential_score_0_100": 70,
            "risk_level": "MEDIO",
            "analysis": "Subiu de 17º para 4º no ranking de mais vendidos da categoria "
            "em 4 leituras: a demanda cresce rápido, mas ainda não chegou ao topo.",
            "recommendation": "Cotar fornecedor e testar estoque pequeno; se passar ao "
            "top 3 na próxima leitura, a tendência se confirma.",
            "confidence_0_1": 0.65,
        },
    },
    {
        "entrada": {
            "produto": {"fonte": "mercadolivre"},
            "marketplace": {
                "posicao_ranking": 1,
                "posicao_ranking_inicio_janela": 1,
                "rank_momentum": 0.0,
                "leituras": 6,
                "price": 249.0,
            },
            "social": None,
            "referencia": None,
        },
        "saida": {
            "trend_classification": "ESTAVEL",
            "potential_score_0_100": 45,
            "risk_level": "BAIXO",
            "analysis": "Mais vendido da categoria nas 6 leituras: demanda alta e "
            "consolidada, mas sem movimento — já é mainstream, não tendência nova.",
            "recommendation": "Serve como produto de catálogo, com concorrência forte; "
            "não é aposta antecipada.",
            "confidence_0_1": 0.75,
        },
    },
    {
        "entrada": {
            "produto": {"fonte": "mercadolivre"},
            "marketplace": {
                "posicao_ranking": 14,
                "posicao_ranking_inicio_janela": 2,
                "rank_momentum": 0.0,
                "leituras": 5,
                "price": 89.9,
            },
            "social": None,
            "referencia": None,
        },
        "saida": {
            "trend_classification": "EM_QUEDA",
            "potential_score_0_100": 20,
            "risk_level": "ALTO",
            "analysis": "Caiu de 2º para 14º no ranking em 5 leituras: a demanda que "
            "existia está passando para outros produtos.",
            "recommendation": "Evitar estoque novo; se já tem, girar o que sobrou antes "
            "que caia mais.",
            "confidence_0_1": 0.7,
        },
    },
    {
        "entrada": {
            "produto": {"fonte": "mercadolivre"},
            "marketplace": {
                "posicao_ranking": 6,
                "posicao_ranking_inicio_janela": 6,
                "rank_momentum": 0.0,
                "leituras": 1,
                "price": 34.9,
            },
            "social": None,
            "referencia": None,
        },
        "saida": {
            "trend_classification": "ESTAVEL",
            "potential_score_0_100": 45,
            "risk_level": "MEDIO",
            "analysis": "6º no ranking em leitura única: entre os mais vendidos, mas "
            "ainda não dá para dizer se sobe ou cai.",
            "recommendation": "Acompanhar as próximas leituras antes de decidir.",
            "confidence_0_1": 0.4,
        },
    },
]


def _exemplos() -> str:
    blocos = [
        f"Entrada: {json.dumps(ex['entrada'], ensure_ascii=False)}\n"
        f"Saída: {json.dumps(ex['saida'], ensure_ascii=False)}"
        for ex in FEW_SHOT
    ]
    return "\n\n".join(blocos)


SYSTEM = (
    "Você é um caçador de tendências de e-commerce. Quem lê a sua análise quer "
    "decidir se vale apostar num produto (comprar estoque, anunciar, revender) "
    "ANTES de ele virar mainstream. Você não fala com quem postou o vídeo: nunca "
    "recomende 'vincular produto' ou 'aumentar o engajamento'.\n\n"
    "Como ler os sinais:\n"
    "- Tendência é ritmo, não total. views_per_hour e engagement_per_hour são por "
    "hora de vida do vídeo; compare-os com a mediana e o p90 da referência.\n"
    "- social_velocity é a aceleração entre as duas últimas leituras: 0 = ritmo "
    "recente igual ou abaixo da média de vida, 1 = o dobro. NUNCA é negativo.\n"
    "- Com leituras = 1, social_velocity = 0 significa 'não medido': não conclua "
    "que parou; reduza a confiança.\n"
    "- marcador_comercial diz por que o vídeo conta como produto à venda "
    "(tiktok_shop, link_bio, cupom, preço...). Não diga que falta produto.\n"
    "- EM_QUEDA exige atenção que se perdeu: vídeo que já teve ritmo e esfriou. "
    "Vídeo novo com pouca tração é ESTAVEL com potencial baixo, não EM_QUEDA.\n"
    "- Sem referência do grupo, julgue só pelos sinais e reduza a confiança.\n"
    "- potential_score_0_100 é a SUA estimativa de potencial, a partir dos sinais. "
    "Use a escala inteira: ESTAVEL fica perto de 40, VIRALIZANDO acima de 80.\n"
    "- Não invente dados. Cite números dos sinais na análise.\n\n"
    "Item de marketplace (fonte mercadolivre) não é vídeo: social e referencia vêm "
    "null — não fale de views, engajamento nem de falta de tração social. O sinal "
    "é o ranking de mais vendidos da categoria (cerca de 20 posições, 1 = o mais "
    "vendido):\n"
    "- posicao_ranking é a posição agora; posicao_ranking_inicio_janela, a da "
    "leitura mais antiga da janela. Número menor = subiu = demanda crescendo; "
    "número maior = caiu = demanda esfriando.\n"
    "- rank_momentum (0 a 1) mede só subida, em escala log (20º → 1º = 1). Queda "
    "aparece como 0: compare as duas posições.\n"
    "- Com leituras = 1, ou posições iguais, não houve tempo para medir: reduza a "
    "confiança.\n"
    "- No topo e parado é demanda alta já consolidada: ESTAVEL, não tendência nova.\n"
    "- sold_quantity null é falta de dado, não de vendas: não conclua nada dele.\n\n"
    f"Exemplos:\n\n{_exemplos()}"
)


def _referencia(norm: Normalization | None) -> dict[str, Any] | None:
    if norm is None:
        return None
    return {
        "base": norm.basis,
        "produtos_no_grupo": norm.pool_size,
        **norm.reference,
        "percentil_do_produto": norm.values,
    }


MARKETPLACES = frozenset({"mercadolivre"})


def build_trend_prompt(ti: TrendInput, normalization: Normalization | None) -> dict[str, Any]:
    produto: dict[str, Any] = {"title": ti.title, "category": ti.category, "fonte": ti.source}
    marketplace: dict[str, Any] = {
        "price": ti.price,
        "sold_quantity": ti.sold_quantity,
        "price_volatility": ti.price_volatility,
    }

    if ti.source in MARKETPLACES:
        # TIE-42: sem vídeo, os sinais sociais e a referência (views, engajamento)
        # são zeros que o LLM lia como "sem tração". O sinal é o ranking.
        marketplace |= {
            "posicao_ranking": ti.rank_position,
            "posicao_ranking_inicio_janela": ti.rank_position_start,
            "rank_momentum": ti.rank_momentum,
            "leituras": ti.n_readings,
        }
        social = None
        referencia = None
    else:
        # Vídeo (ou produto gravado antes do campo `source`, quando só havia TikTok)
        produto |= {
            "marcador_comercial": ti.commercial_marker,
            "has_shop_product": ti.has_shop_product,
        }
        social = {
            "age_hours": ti.age_hours,
            "views_per_hour": ti.views_per_hour,
            "engagement_per_hour": ti.engagement_per_hour,
            "social_velocity": ti.social_velocity,
            "leituras": ti.n_readings,
            "views_total": ti.views_24h,
            "engagement_total": ti.engagement_24h,
        }
        referencia = _referencia(normalization)

    data = {
        "produto": produto,
        "marketplace": marketplace,
        "social": social,
        "referencia": referencia,
    }

    user = (
        "Responda APENAS em JSON válido com o schema:\n"
        "{\n"
        '  "trend_classification": "ESTAVEL|SUBINDO|VIRALIZANDO|PICO_TEMPORARIO|EM_QUEDA",\n'
        '  "potential_score_0_100": number,\n'
        '  "risk_level": "BAIXO|MEDIO|ALTO",\n'
        '  "analysis": string,\n'
        '  "recommendation": string,\n'
        '  "confidence_0_1": number\n'
        "}\n\n"
        f"Dados:\n{json.dumps(data, ensure_ascii=False)}"
    )

    return {"system": SYSTEM, "user": user}
