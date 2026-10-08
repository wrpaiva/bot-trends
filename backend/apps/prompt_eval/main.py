# apps/prompt_eval/main.py
"""
Avaliação do prompt do LLM (TIE-26): temperatura, formato e concordância humana.

    docker compose run --rm -v "$PWD:/out" api python -m apps.prompt_eval.main exportar --saida /out/rotulos.csv
    # (rotule o CSV: classificacao_humana e score_humano 0–100)
    docker compose run --rm -v "$PWD:/out" api python -m apps.prompt_eval.main concordancia --rotulos /out/rotulos.csv
    docker compose run --rm api python -m apps.prompt_eval.main estabilidade --temperaturas 0,0.2,0.7

- `exportar`: CSV CEGO com os vídeos analisados por todas as versões pedidas
  (padrão v3 e v4 — os mesmos 20 vídeos de 01/10). Só o que uma pessoa precisa
  para julgar (título, link, idade, views/h, marcador); nada do que o modelo
  respondeu, para não ancorar o julgamento.
- `concordancia`: rótulo humano × resposta de cada versão do prompt — acerto,
  kappa de Cohen (acerto descontado do acaso) e Spearman do score humano com o
  do LLM e, como referência, com o numérico. É o "antes e depois" da TIE-26.
- `estabilidade`: remonta as entradas como a versão base as viu, chama o LLM
  N vezes por temperatura e mede formato (respostas que passam no schema) e
  estabilidade (desvio do score e se a classificação muda entre repetições).
  CUSTA chamadas ao LLM: `--max-chamadas` é o teto.

Só lê o banco.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import statistics
from collections.abc import Callable
from pathlib import Path
from typing import get_args

from apps.worker import tasks_trend
from src.application.prompt_builder import build_trend_prompt
from src.domain.agreement import acerto, cohen_kappa, moda_e_estabilidade
from src.domain.backtest import spearman
from src.domain.interfaces import LLMClient
from src.domain.percentile import PercentileContext
from src.domain.trend_models import TrendClass
from src.infrastructure.config import settings
from src.infrastructure.llm.schema import LLMResponseError
from src.infrastructure.utils.datetime_utils import ensure_utc

CLASSES = get_args(TrendClass)
COLUNAS = [
    "product_id",
    "titulo",
    "link",
    "idade_h",
    "views_por_hora",
    "marcador_comercial",
    "classificacao_humana",
    "score_humano",
    "observacao",
]


def _com_todas_as_versoes(db, versoes: list[str]) -> list[str]:
    ids = None
    for v in versoes:
        desta = set(db["trend_insights"].distinct("product_id", {"prompt_version": v}))
        ids = desta if ids is None else ids & desta
    return sorted(ids or [])


def _ultimo_insight(db, product_id: str, versao: str) -> dict | None:
    return db["trend_insights"].find_one(
        {"product_id": product_id, "prompt_version": versao}, sort=[("ts", -1)]
    )


def exportar(db, *, versoes: list[str], saida: Path) -> int:
    ids = _com_todas_as_versoes(db, versoes)
    with open(saida, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS)
        w.writeheader()
        for pid in ids:
            p = db["products"].find_one({"product_id": pid}) or {}
            # Sinais de ENTRADA (o que o modelo recebeu), nunca a resposta dele
            ins = _ultimo_insight(db, pid, versoes[-1]) or {}
            sinais = ins.get("signals") or {}
            w.writerow(
                {
                    "product_id": pid,
                    "titulo": (p.get("title") or "").replace("\n", " ")[:200],
                    "link": p.get("permalink") or "",
                    "idade_h": sinais.get("age_hours", ""),
                    "views_por_hora": sinais.get("views_per_hour", ""),
                    "marcador_comercial": ins.get("commercial_marker") or "",
                    "classificacao_humana": "",
                    "score_humano": "",
                    "observacao": "",
                }
            )
    return len(ids)


def _ler_rotulos(caminho: Path) -> list[dict]:
    rotulos = []
    with open(caminho, newline="", encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            cls = (linha.get("classificacao_humana") or "").strip().upper()
            if not cls:
                continue
            if cls not in CLASSES:
                raise ValueError(
                    f"{linha['product_id']}: classificação {linha['classificacao_humana']!r} "
                    f"inválida — use uma de {', '.join(CLASSES)}"
                )
            score = (linha.get("score_humano") or "").strip()
            rotulos.append(
                {
                    "product_id": linha["product_id"],
                    "classificacao": cls,
                    "score": float(score.replace(",", ".")) if score else None,
                }
            )
    return rotulos


def concordancia(db, *, rotulos: Path, versoes: list[str]) -> dict:
    humanos = _ler_rotulos(rotulos)
    por_versao = {}
    for v in versoes:
        pares = [(h, _ultimo_insight(db, h["product_id"], v)) for h in humanos]
        pares = [(h, ins) for h, ins in pares if ins]
        com_score = [(h, ins) for h, ins in pares if h["score"] is not None]
        humano_cls = [h["classificacao"] for h, _ in pares]
        modelo_cls = [ins.get("trend_classification") for _, ins in pares]
        por_versao[v] = {
            "n": len(pares),
            "acerto": acerto(humano_cls, modelo_cls),
            "kappa": cohen_kappa(humano_cls, modelo_cls),
            "spearman_llm": spearman(
                [h["score"] for h, _ in com_score],
                [ins.get("llm_score") or 0.0 for _, ins in com_score],
            ),
            "spearman_numerico": spearman(
                [h["score"] for h, _ in com_score],
                [ins.get("numeric_score") or 0.0 for _, ins in com_score],
            ),
        }
    return {"n": len(humanos), "versoes": por_versao}


def _prompts_como_a_versao_viu(db, versao: str) -> list[dict]:
    entradas = []
    for pid in _com_todas_as_versoes(db, [versao]):
        ins = _ultimo_insight(db, pid, versao)
        t = ensure_utc(ins["ts"])
        since = t - dt.timedelta(hours=ins.get("window_hours") or 72)
        e = tasks_trend._build_input(db, pid, since, until=t)
        if e not in (None, tasks_trend.VELHO, tasks_trend.SEM_PRODUTO):
            entradas.append(e[0])
    contexto = (
        PercentileContext(entradas, min_group=settings.SCORE_PERCENTILE_MIN_GROUP)
        if settings.SCORE_NORMALIZATION == "percentile" and entradas
        else None
    )
    return [build_trend_prompt(ti, contexto.normalize(ti) if contexto else None) for ti in entradas]


def estabilidade(
    db,
    *,
    versao: str,
    temperaturas: list[float],
    repeticoes: int,
    llm_factory: Callable[[float], LLMClient],
    max_chamadas: int = 200,
) -> dict:
    prompts = _prompts_como_a_versao_viu(db, versao)
    total = len(prompts) * len(temperaturas) * repeticoes
    if total > max_chamadas:
        raise ValueError(
            f"{total} chamadas ao LLM passam do teto de {max_chamadas} (--max-chamadas)"
        )

    por_temp = {}
    for temp in temperaturas:
        llm = llm_factory(temp)
        validas, desvios, estabilidades, scores = 0, [], [], []
        for prompt in prompts:
            resp = []
            for _ in range(repeticoes):
                try:
                    resp.append(llm.analyze_trend(prompt["system"], prompt["user"]))
                except LLMResponseError:
                    continue  # formato: resposta fora do schema
            validas += len(resp)
            if len(resp) >= 2:
                desvios.append(statistics.pstdev(r.potential_score_0_100 for r in resp))
            if resp:
                estabilidades.append(moda_e_estabilidade([r.trend_classification for r in resp])[1])
                scores += [r.potential_score_0_100 for r in resp]
        chamadas = len(prompts) * repeticoes
        por_temp[str(float(temp))] = {
            "chamadas": chamadas,
            "validas": validas,
            "taxa_valida": validas / chamadas if chamadas else None,
            "desvio_score_medio": statistics.fmean(desvios) if desvios else None,
            "estabilidade_classificacao": (
                statistics.fmean(estabilidades) if estabilidades else None
            ),
            "score_medio": statistics.fmean(scores) if scores else None,
        }
    return {
        "versao_base": versao,
        "videos": len(prompts),
        "repeticoes": repeticoes,
        "modelo": settings.LLM_MODEL,
        "temperaturas": por_temp,
    }


def main() -> int:
    from src.infrastructure.db.mongo import get_db
    from src.infrastructure.llm.openai_compatible import OpenAICompatibleLLMClient

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = parser.add_subparsers(dest="comando", required=True)
    p = sub.add_parser("exportar")
    p.add_argument("--saida", type=Path, required=True)
    p.add_argument("--versoes", default="v3,v4")
    p = sub.add_parser("concordancia")
    p.add_argument("--rotulos", type=Path, required=True)
    p.add_argument("--versoes", default="v3,v4")
    p = sub.add_parser("estabilidade")
    p.add_argument("--versao", default="v4")
    p.add_argument("--temperaturas", default="0,0.2,0.7")
    p.add_argument("--repeticoes", type=int, default=3)
    p.add_argument("--max-chamadas", type=int, default=200)
    args = parser.parse_args()

    db = get_db()
    if args.comando == "exportar":
        n = exportar(db, versoes=args.versoes.split(","), saida=args.saida)
        print(
            f"{n} vídeos em {args.saida}. Preencha classificacao_humana ({', '.join(CLASSES)}) e score_humano (0–100)."
        )
        return 0
    if args.comando == "concordancia":
        rel = concordancia(db, rotulos=args.rotulos, versoes=args.versoes.split(","))
    else:
        rel = estabilidade(
            db,
            versao=args.versao,
            temperaturas=[float(t) for t in args.temperaturas.split(",")],
            repeticoes=args.repeticoes,
            llm_factory=lambda t: OpenAICompatibleLLMClient(temperature=t),
            max_chamadas=args.max_chamadas,
        )
    print(json.dumps(rel, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
