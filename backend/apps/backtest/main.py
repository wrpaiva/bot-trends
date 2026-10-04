# apps/backtest/main.py
"""
Backtest do score contra baselines burros (TIE-23).

    docker compose run --rm api python -m apps.backtest.main
    docker compose run --rm api python -m apps.backtest.main --horizon-hours 12 --json

Em cada corte T (cada instante de coleta), monta as entradas como a análise as
veria em T — só leituras até T, mesmo filtro comercial, mesmo corte de idade,
mesmo percentil — e pontua com o motor numérico atual. Depois compara três
ordens com o que aconteceu entre T e a primeira leitura >= T + horizonte
(tolerância: até 2× o horizonte):

- `score`: o score numérico de hoje;
- `views_por_hora`: o baseline burro (views por hora de vida em T);
- `views_total`: popularidade pura (views acumuladas em T).

Desfechos: `ganho_views_h` (views ganhas por hora depois de T) e `aceleracao`
(esse ritmo ÷ o ritmo de vida em T; > 1 = acelerou). "O score não bate o
baseline" é resultado válido (D4).

Limites: o LLM fica de fora (não dá para refazer as chamadas do passado sem
custo nem garantir o mesmo modelo); cortes vizinhos compartilham vídeos, então
as médias não são amostras independentes. Só lê o banco.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json

from apps.worker import tasks_trend
from src.domain.backtest import precision_at_k, spearman, summarize
from src.domain.percentile import PercentileContext
from src.domain.scoring import NumericScoreStrategy
from src.infrastructure.config import settings
from src.infrastructure.utils.datetime_utils import ensure_utc

PREDITORES = ("score", "views_por_hora", "views_total")
DESFECHOS = ("ganho_views_h", "aceleracao")


def _horas(a: dt.datetime, b: dt.datetime) -> float:
    return (a - b).total_seconds() / 3600


def _desfecho(db, product_id: str, t: dt.datetime, horizon_hours: float) -> dict | None:
    """Ganho de views por hora entre a última leitura <= t e a futura."""
    atual = db["metrics"].find_one({"product_id": product_id, "ts": {"$lte": t}}, sort=[("ts", -1)])
    futura = db["metrics"].find_one(
        {
            "product_id": product_id,
            "ts": {
                "$gte": t + dt.timedelta(hours=horizon_hours),
                "$lte": t + dt.timedelta(hours=2 * horizon_hours),
            },
        },
        sort=[("ts", 1)],
    )
    if not atual or not futura:
        return None
    horas = _horas(ensure_utc(futura["ts"]), ensure_utc(atual["ts"]))
    ganho = max(int(futura.get("views") or 0) - int(atual.get("views") or 0), 0) / horas
    return {"ganho_views_h": ganho}


def _corte(db, t: dt.datetime, *, horizon_hours, window_hours, limit_products, k, min_pool):
    since = t - dt.timedelta(hours=window_hours)
    ativos = tasks_trend._produtos_ativos(db, since, limit_products, until=t)
    brutas = [tasks_trend._build_input(db, pid, since, until=t) for pid in ativos]
    entradas = [e for e in brutas if e not in (None, tasks_trend.VELHO, tasks_trend.SEM_PRODUTO)]
    contexto = (
        PercentileContext(
            [ti for ti, *_ in entradas], min_group=settings.SCORE_PERCENTILE_MIN_GROUP
        )
        if settings.SCORE_NORMALIZATION == "percentile" and entradas
        else None
    )
    estrategia = NumericScoreStrategy(settings.score_weights())

    itens = []
    for ti, *_ in entradas:
        fut = _desfecho(db, ti.product_id, t, horizon_hours)
        if fut is None:
            continue
        vph = ti.views_per_hour or 0.0
        norm = contexto.normalize(ti) if contexto else None
        itens.append(
            {
                "product_id": ti.product_id,
                "title": (ti.title or "")[:60],
                "score": estrategia.compute(ti, norm).score_0_100,
                "views_por_hora": vph,
                "views_total": float(ti.views_24h),
                "ganho_views_h": fut["ganho_views_h"],
                # Sem ritmo de vida (vídeo sem data) não há como comparar
                "aceleracao": fut["ganho_views_h"] / vph if vph > 0 else None,
            }
        )

    if len(itens) < min_pool:
        return None

    preditores = {}
    for p in PREDITORES:
        preditores[p] = {}
        for d in DESFECHOS:
            pares = [(i[p], i[d]) for i in itens if i[d] is not None]
            if len(pares) < min_pool:
                preditores[p][d] = {"spearman": None, "precision_at_k": None}
                continue
            preditores[p][d] = {
                "spearman": spearman([a for a, _ in pares], [b for _, b in pares]),
                "precision_at_k": precision_at_k(
                    {str(n): a for n, (a, _) in enumerate(pares)},
                    {str(n): b for n, (_, b) in enumerate(pares)},
                    k=k,
                ),
            }
    return {"T": t.isoformat(), "n": len(itens), "preditores": preditores, "itens": itens}


def run_backtest(
    db,
    *,
    horizon_hours: float = 6.0,
    window_hours: int = 72,
    limit_products: int = 50,
    k: int = 3,
    min_pool: int = 5,
) -> dict:
    cortes_ts = sorted({ensure_utc(t) for t in db["metrics"].distinct("ts")})
    cortes, pulados = [], 0
    for t in cortes_ts:
        c = _corte(
            db,
            t,
            horizon_hours=horizon_hours,
            window_hours=window_hours,
            limit_products=limit_products,
            k=k,
            min_pool=min_pool,
        )
        if c is None:
            pulados += 1
        else:
            cortes.append(c)

    resumo = {
        p: {
            d: {
                m: summarize([c["preditores"][p][d][m] for c in cortes])
                for m in ("spearman", "precision_at_k")
            }
            for d in DESFECHOS
        }
        for p in PREDITORES
    }
    return {
        "params": {
            "horizon_hours": horizon_hours,
            "window_hours": window_hours,
            "limit_products": limit_products,
            "k": k,
            "min_pool": min_pool,
            "normalizacao": settings.SCORE_NORMALIZATION,
        },
        "cortes": cortes,
        "cortes_pulados": pulados,
        "resumo": resumo,
    }


def _fmt(v) -> str:
    return "  —  " if v is None else f"{v:+.2f}"


def _tabela(rel: dict) -> str:
    linhas = [
        f"Cortes avaliados: {len(rel['cortes'])} (pulados: {rel['cortes_pulados']}); "
        f"vídeos por corte: {sorted(c['n'] for c in rel['cortes'])}",
        f"Horizonte {rel['params']['horizon_hours']} h, top-{rel['params']['k']}",
    ]
    menor = min((c["n"] for c in rel["cortes"]), default=0)
    if rel["cortes"] and menor <= 2 * rel["params"]["k"]:
        # Com pool <= 2k, qualquer ordem acerta boa parte do top-k por acaso
        linhas.append(
            f"AVISO: corte com só {menor} vídeos para top-{rel['params']['k']}: "
            "prec@k diz pouco; olhe o spearman"
        )
    linhas += [
        "",
        f"{'preditor':<16}{'desfecho':<15}{'spearman':>10}{'prec@k':>10}{'cortes':>8}",
    ]
    for p, por_d in rel["resumo"].items():
        for d, m in por_d.items():
            linhas.append(
                f"{p:<16}{d:<15}{_fmt(m['spearman']['media']):>10}"
                f"{_fmt(m['precision_at_k']['media']):>10}{m['spearman']['n']:>8}"
            )
    return "\n".join(linhas)


def main() -> int:
    from src.infrastructure.db.mongo import get_db

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--horizon-hours", type=float, default=6.0)
    parser.add_argument("--window-hours", type=int, default=72)
    parser.add_argument("--limit-products", type=int, default=50)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--min-pool", type=int, default=5)
    parser.add_argument("--json", action="store_true", help="relatório completo em JSON")
    args = parser.parse_args()

    rel = run_backtest(
        get_db(),
        horizon_hours=args.horizon_hours,
        window_hours=args.window_hours,
        limit_products=args.limit_products,
        k=args.k,
        min_pool=args.min_pool,
    )
    print(json.dumps(rel, ensure_ascii=False, default=str) if args.json else _tabela(rel))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
