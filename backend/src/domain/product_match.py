# src/domain/product_match.py
"""
Casamento vídeo do TikTok → produto do catálogo do ML (TIE-18). Regra pura.

O vídeo não casa com o ranking do ML: as duas fontes olham para universos
diferentes (achadinho da Shopee × top 20 de 7 categorias; em 2026-10-16, 25
pares com duas palavras em comum, quase todos falsos). Por isso o fluxo é o
inverso — o produto que o vídeo vende é procurado no catálogo — e esta regra
decide se o candidato que a busca devolveu é mesmo aquele produto.

Falso positivo é pior que falso negativo: um vínculo errado põe sinal social
de um produto em outro. Por isso exige consulta com ao menos duas palavras,
cobertura alta das palavras da consulta no nome do candidato e, quando o ML
sugere um domínio para a consulta, candidato desse domínio.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Palavras que não identificam produto
_VAZIAS = frozenset(
    "a as o os e de da das do dos em no na nos nas um uma para pra com sem por".split()
)
MIN_PALAVRAS = 2

# Público declarado no nome: produto de outro público não é o do vídeo, mesmo
# com as palavras todas batendo (cinta feminina → "Cinta Modeladora Masculina",
# 2026-10-16). Já sem plural e sem acento, como sai de `tokens`.
_PUBLICO = frozenset(
    "masculino masculina feminino feminina infantil menino menina bebe adulto unissex".split()
)


@dataclass(frozen=True)
class Candidato:
    product_id: str  # id do produto no catálogo do ML (MLB...)
    nome: str
    domain_id: str | None = None


@dataclass(frozen=True)
class Escolha:
    candidato: Candidato
    score: float  # cobertura da consulta no nome, 0–1


def _palavra(w: str) -> str:
    # Plural simples: "potes" e "pote" são o mesmo produto
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def tokens(texto: str | None) -> set[str]:
    """Palavras que identificam o produto: sem acento, caixa, plural nem vazias."""
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    return {
        _palavra(w)
        for w in re.findall(r"[a-z0-9]+", t)
        if w not in _VAZIAS and (len(w) >= 2 or w.isdigit())
    }


def cobertura(consulta: str, nome: str) -> float:
    """Fração das palavras da consulta que aparecem no nome do candidato."""
    q = tokens(consulta)
    if not q:
        return 0.0
    return len(q & tokens(nome)) / len(q)


def escolhe_candidato(
    consulta: str,
    candidatos: list[Candidato],
    *,
    minimo: float,
    dominio: str | None = None,
) -> Escolha | None:
    """
    Melhor candidato com cobertura ≥ `minimo`, ou None. `dominio` é o que o ML
    sugere para a consulta (`domain_discovery`); candidato de outro domínio sai,
    o sem domínio fica (a busca nem sempre o informa).
    """
    if not 0.0 < minimo <= 1.0:
        raise ValueError(f"minimo deve estar em (0, 1], veio {minimo}")
    if len(tokens(consulta)) < MIN_PALAVRAS:
        return None

    melhor: Escolha | None = None
    for c in candidatos:
        if dominio and c.domain_id and c.domain_id != dominio:
            continue
        if (tokens(c.nome) & _PUBLICO) - tokens(consulta):
            continue
        score = cobertura(consulta, c.nome)
        if score >= minimo and (melhor is None or score > melhor.score):
            melhor = Escolha(c, score)
    return melhor
