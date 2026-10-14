# apps/backtest/main.py
"""
Backtest do score contra baselines burros (TIE-23; ranking do ML na TIE-46).

    docker compose run --rm api python -m apps.backtest.main
    docker compose run --rm api python -m apps.backtest.main --source mercadolivre --k 5

Em cada corte T (cada instante de coleta da fonte), monta as entradas como a
análise as veria em T — só leituras até T, mesmo filtro comercial, mesmo corte
de idade, mesmo percentil — e pontua com o motor numérico atual. Depois compara
o score e os baselines com o que aconteceu entre T e a primeira leitura a
partir de 0,9× o horizonte (até 2× o horizonte). A folga existe porque a
coleta de "6 em 6 h" sai com 5,99 h.

TikTok (`--source tiktok`, padrão):
- preditores: `score`, `views_por_hora` (views por hora de vida em T),
  `views_total` (popularidade pura);
- desfechos: `ganho_views_h` (views ganhas por hora depois de T) e
  `aceleracao` (esse ritmo ÷ o ritmo de vida em T; > 1 = acelerou).

Mercado Livre (`--source mercadolivre`): o ML não dá vendas, mas o
/highlights é o ranking de mais vendidos — subir nele é o resultado
observável. Entram os produtos que estavam no ranking em T.
- preditores: `score`, `posicao_atual` e `preco` (o mais barato primeiro);
- desfechos: `posicao_futura`, `ganho_posicoes` (posições ganhas) e
  `entrou_top5` (0/1, só para quem estava fora do top 5 em T; medido como
  acerto no top-k contra a `taxa_base`, a fração que entrou).
  Quem some do ranking na coleta seguinte conta como 21º — mas só se a
  categoria dele foi coletada; sem isso, sumir pode ser falha do /highlights.

Todo preditor e desfecho é "maior = melhor" (posição e preço entram com
sinal trocado). "O score não bate o baseline" é resultado válido (D4).

Limites: o LLM fica de fora (não dá para refazer as chamadas do passado sem
custo nem garantir o mesmo modelo); cortes vizinhos compartilham produtos,
então as médias não são amostras independentes. Só lê o banco.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json

from apps.worker import tasks_trend
from src.domain.backtest import acerto_top_k, precision_at_k, spearman, summarize
from src.domain.percentile import PercentileContext
from src.domain.scoring import NumericScoreStrategy
from src.infrastructure.config import settings
from src.infrastructure.utils.datetime_utils import ensure_utc

FONTES = {
    "tiktok": {
        "preditores": ("score", "views_por_hora", "views_total"),
        "desfechos": ("ganho_views_h", "aceleracao"),
    },
    "mercadolivre": {
        "preditores": ("score", "posicao_atual", "preco"),
        "desfechos": ("posicao_futura", "ganho_posicoes", "entrou_top5"),
    },
}
# Desfecho 0/1: prec@k vira acerto no top-k, comparado com a taxa base
BINARIOS = {"entrou_top5"}
# O collector lê o top 20 de cada categoria (`max_items_per_category`)
FORA_DO_RANKING = 21
TOP_N = 5
# A coleta "de 6 em 6 h" sai com 5,99 h: sem folga, metade dos cortes some
FOLGA_HORIZONTE = 0.9


def _horas(a: dt.datetime, b: dt.datetime) -> float:
    return (a - b).total_seconds() / 3600


def _desfecho(db, product_id: str, t: dt.datetime, horizon_hours: float) -> dict | None:
    """Ganho de views por hora entre a última leitura <= t e a futura."""
    atual = db["metrics"].find_one({"product_id": product_id, "ts": {"$lte": t}}, sort=[("ts", -1)])
    futura = db["metrics"].find_one(
        {
            "product_id": product_id,
            "ts": {
                "$gte": t + dt.timedelta(hours=FOLGA_HORIZONTE * horizon_hours),
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


def _itens_tiktok(db, pontuados, t, horizon_hours) -> list[dict]:
    itens = []
    for ti, score in pontuados:
        fut = _desfecho(db, ti.product_id, t, horizon_hours)
        if fut is None:
            continue
        vph = ti.views_per_hour or 0.0
        itens.append(
            {
                "product_id": ti.product_id,
                "title": (ti.title or "")[:60],
                "score": score,
                "views_por_hora": vph,
                "views_total": float(ti.views_24h),
                "ganho_views_h": fut["ganho_views_h"],
                # Sem ritmo de vida (vídeo sem data) não há como comparar
                "aceleracao": fut["ganho_views_h"] / vph if vph > 0 else None,
            }
        )
    return itens


def _ranking_em(db, ts: dt.datetime) -> dict[str, tuple[int, str | None]]:
    """product_id → (posição, categoria) na coleta do ML feita em `ts`."""
    leituras = list(
        db["metrics"].find(
            {"source": "mercadolivre", "ts": ts, "rank_position": {"$ne": None}},
            {"_id": 0, "product_id": 1, "rank_position": 1, "category": 1},
        )
    )
    # Leitura de antes da TIE-44 não grava a categoria: vale a do produto
    sem_categoria = [m["product_id"] for m in leituras if not m.get("category")]
    do_produto = {
        p["product_id"]: p.get("category")
        for p in db["products"].find(
            {"product_id": {"$in": sem_categoria}}, {"_id": 0, "product_id": 1, "category": 1}
        )
    }
    return {
        m["product_id"]: (
            int(m["rank_position"]),
            m.get("category") or do_produto.get(m["product_id"]),
        )
        for m in leituras
    }


def _itens_ml(db, pontuados, t, t_futuro) -> list[dict]:
    if t_futuro is None:
        return []
    agora, depois = _ranking_em(db, t), _ranking_em(db, t_futuro)
    coletadas = {cat for _, cat in depois.values()}
    itens = []
    for ti, score in pontuados:
        if ti.product_id not in agora:
            continue
        pos, categoria = agora[ti.product_id]
        if ti.product_id in depois:
            pos_fut = depois[ti.product_id][0]
        elif categoria in coletadas:
            pos_fut = FORA_DO_RANKING
        else:
            continue
        itens.append(
            {
                "product_id": ti.product_id,
                "title": (ti.title or "")[:60],
                "category": categoria,
                "pos_T": pos,
                "pos_futura": pos_fut,
                "score": score,
                "posicao_atual": -pos,
                "preco": -ti.price if ti.price is not None else None,
                "posicao_futura": -pos_fut,
                "ganho_posicoes": pos - pos_fut,
                "entrou_top5": int(pos_fut <= TOP_N) if pos > TOP_N else None,
            }
        )
    return itens


def _metricas(pares: list[tuple[float, float]], desfecho: str, k: int) -> dict:
    previsto = {str(n): a for n, (a, _) in enumerate(pares)}
    real = {str(n): b for n, (_, b) in enumerate(pares)}
    m = {"spearman": spearman([a for a, _ in pares], [b for _, b in pares])}
    if desfecho in BINARIOS:
        m["precision_at_k"] = acerto_top_k(previsto, real, k=k)
        m["taxa_base"] = sum(real.values()) / len(real)
    else:
        m["precision_at_k"] = precision_at_k(previsto, real, k=k)
    return m


def _corte(
    db,
    t: dt.datetime,
    *,
    source,
    t_futuro,
    horizon_hours,
    window_hours,
    limit_products,
    k,
    min_pool,
):
    since = t - dt.timedelta(hours=window_hours)
    ativos = tasks_trend._produtos_ativos(db, since, limit_products, until=t)
    brutas = [tasks_trend._build_input(db, pid, since, until=t) for pid in ativos]
    entradas = [e for e in brutas if e not in (None, tasks_trend.VELHO, tasks_trend.SEM_PRODUTO)]
    # O percentil vê o ciclo inteiro, todas as fontes, como a análise vê
    contexto = (
        PercentileContext(
            [ti for ti, *_ in entradas], min_group=settings.SCORE_PERCENTILE_MIN_GROUP
        )
        if settings.SCORE_NORMALIZATION == "percentile" and entradas
        else None
    )
    estrategia = NumericScoreStrategy(settings.score_weights())
    pontuados = [
        (ti, estrategia.compute(ti, contexto.normalize(ti) if contexto else None).score_0_100)
        for ti, *_ in entradas
        if ti.source == source
    ]

    if source == "mercadolivre":
        itens = _itens_ml(db, pontuados, t, t_futuro)
    else:
        itens = _itens_tiktok(db, pontuados, t, horizon_hours)
    if len(itens) < min_pool:
        return None

    vazio = {"spearman": None, "precision_at_k": None}
    preditores = {}
    for p in FONTES[source]["preditores"]:
        preditores[p] = {}
        for d in FONTES[source]["desfechos"]:
            pares = [(i[p], i[d]) for i in itens if i[p] is not None and i[d] is not None]
            if len(pares) < min_pool:
                preditores[p][d] = dict(vazio, taxa_base=None) if d in BINARIOS else vazio
                continue
            preditores[p][d] = _metricas(pares, d, k)
    return {"T": t.isoformat(), "n": len(itens), "preditores": preditores, "itens": itens}


def _proximo(cortes_ts: list[dt.datetime], t: dt.datetime, horizon_hours: float):
    """Primeira coleta da fonte dentro da tolerância do horizonte."""
    de = t + dt.timedelta(hours=FOLGA_HORIZONTE * horizon_hours)
    ate = t + dt.timedelta(hours=2 * horizon_hours)
    return next((c for c in cortes_ts if de <= c <= ate), None)


def run_backtest(
    db,
    *,
    source: str = "tiktok",
    horizon_hours: float = 6.0,
    window_hours: int = 72,
    limit_products: int | None = None,
    k: int = 3,
    min_pool: int = 5,
) -> dict:
    if limit_products is None:
        limit_products = settings.TREND_MAX_PRODUCTS
    if source not in FONTES:
        raise ValueError(f"fonte sem backtest: {source}")
    # Cortes nos instantes de coleta da própria fonte
    cortes_ts = sorted({ensure_utc(t) for t in db["metrics"].distinct("ts", {"source": source})})
    cortes, pulados = [], 0
    for t in cortes_ts:
        c = _corte(
            db,
            t,
            source=source,
            t_futuro=_proximo(cortes_ts, t, horizon_hours),
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
                for m in ("spearman", "precision_at_k") + (("taxa_base",) if d in BINARIOS else ())
            }
            for d in FONTES[source]["desfechos"]
        }
        for p in FONTES[source]["preditores"]
    }
    return {
        "params": {
            "source": source,
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
        f"Fonte: {rel['params']['source']}",
        f"Cortes avaliados: {len(rel['cortes'])} (pulados: {rel['cortes_pulados']}); "
        f"itens por corte: {sorted(c['n'] for c in rel['cortes'])}",
        f"Horizonte {rel['params']['horizon_hours']} h, top-{rel['params']['k']}",
    ]
    menor = min((c["n"] for c in rel["cortes"]), default=0)
    if rel["cortes"] and menor <= 2 * rel["params"]["k"]:
        # Com pool <= 2k, qualquer ordem acerta boa parte do top-k por acaso
        linhas.append(
            f"AVISO: corte com só {menor} itens para top-{rel['params']['k']}: "
            "prec@k diz pouco; olhe o spearman"
        )
    linhas += [
        "",
        f"{'preditor':<16}{'desfecho':<16}{'spearman':>10}{'prec@k':>10}{'cortes':>8}"
        f"{'base':>8}",
    ]
    for p, por_d in rel["resumo"].items():
        for d, m in por_d.items():
            # Desfecho 0/1: prec@k é o acerto no top-k; compare com a taxa base
            base = _fmt(m["taxa_base"]["media"]) if "taxa_base" in m else ""
            linhas.append(
                f"{p:<16}{d:<16}{_fmt(m['spearman']['media']):>10}"
                f"{_fmt(m['precision_at_k']['media']):>10}{m['spearman']['n']:>8}{base:>8}"
            )
    return "\n".join(linhas)


def main() -> int:
    from src.infrastructure.db.mongo import get_db

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--source", choices=sorted(FONTES), default="tiktok")
    parser.add_argument("--horizon-hours", type=float, default=6.0)
    parser.add_argument("--window-hours", type=int, default=72)
    # Sem valor, o mesmo teto da análise (TREND_MAX_PRODUCTS)
    parser.add_argument("--limit-products", type=int, default=None)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--min-pool", type=int, default=5)
    parser.add_argument("--json", action="store_true", help="relatório completo em JSON")
    args = parser.parse_args()

    rel = run_backtest(
        get_db(),
        source=args.source,
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
