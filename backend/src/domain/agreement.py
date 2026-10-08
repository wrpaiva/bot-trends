# src/domain/agreement.py
"""
Concordância entre o modelo e o julgamento humano (TIE-26). Regra pura, sem I/O.

- `acerto`: fração de rótulos iguais. Engana quando uma classe domina: se 90%
  dos vídeos são ESTAVEL, chutar ESTAVEL sempre acerta 90%.
- `cohen_kappa`: o acerto descontado do acaso (0 = nada além do chute,
  1 = perfeito). É a métrica para comparar prompts.
- `moda_e_estabilidade`: em repetições da mesma entrada, a classificação mais
  frequente e a fração de repetições que a deram (1 = sempre igual).

Correlação de score (humano × LLM) usa o `spearman` de `domain.backtest`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence


def _mesmo_tamanho(a: Sequence, b: Sequence) -> None:
    if len(a) != len(b):
        raise ValueError("as duas listas de rótulos precisam ter o mesmo tamanho")


def acerto(a: Sequence[str], b: Sequence[str]) -> float | None:
    _mesmo_tamanho(a, b)
    if not a:
        return None
    return sum(x == y for x, y in zip(a, b, strict=True)) / len(a)


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float | None:
    """Kappa de Cohen; None sem amostra ou com uma categoria só nos dois (pe = 1)."""
    _mesmo_tamanho(a, b)
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum((ca[c] / n) * (cb[c] / n) for c in ca.keys() | cb.keys())
    if pe == 1:
        return None
    return (po - pe) / (1 - pe)


def moda_e_estabilidade(rotulos: Sequence[str]) -> tuple[str | None, float | None]:
    if not rotulos:
        return None, None
    rotulo, vezes = Counter(rotulos).most_common(1)[0]
    return rotulo, vezes / len(rotulos)
