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
- desfechos: `posicao_futura`, `ganho_posicoes` (posições ganhas),
  `entrou_top5` (0/1, só para quem estava fora do top 5 em T) e
  `subida_forte` (0/1, ganhou `--subida` posições ou mais e sustentou o ganho
  na coleta seguinte, sempre dentro da mesma categoria — o "viralizou" do
  ML, TIE-23/TIE-59; só para quem estava abaixo dessa posição em T). Desfecho 0/1 é
  medido como acerto no top-k contra a `taxa_base` e como AUC (0,5 = acaso).
  Quem some do ranking na coleta seguinte conta como 21º — mas só se a
  categoria dele foi coletada; sem isso, sumir pode ser falha do /highlights.

Todo preditor e desfecho é "maior = melhor" (posição e preço entram com
sinal trocado). "O score não bate o baseline" é resultado válido (D4).

`comparacao`: score − cada baseline, corte a corte, com intervalo de 95% por
bootstrap de DIAS inteiros (cortes do mesmo dia veem quase o mesmo ranking).
Intervalo que não cruza o zero = diferença que não é sorte. `eventos` (ML):
cada subida forte, uma vez por produto (a primeira), com data e o percentil
do produto no corte segundo o score e segundo a posição.

Limites: o LLM fica de fora (não dá para refazer as chamadas do passado sem
custo nem garantir o mesmo modelo); cortes vizinhos compartilham produtos,
então as médias não são amostras independentes. Só lê o banco.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math

from apps.worker import tasks_trend
from src.domain.alerting import AlertState, decide_alert
from src.domain.backtest import (
    acerto_top_k,
    auc,
    ic_bootstrap_por_bloco,
    precision_at_k,
    spearman,
    summarize,
)
from src.domain.percentile import PercentileContext
from src.domain.scoring import FALLBACK_CORTES, NumericScoreStrategy
from src.infrastructure.config import settings
from src.infrastructure.utils.datetime_utils import ensure_utc

FONTES = {
    "tiktok": {
        "preditores": ("score", "views_por_hora", "views_total"),
        "desfechos": ("ganho_views_h", "aceleracao"),
    },
    "mercadolivre": {
        "preditores": ("score", "posicao_atual", "preco"),
        "desfechos": ("posicao_futura", "ganho_posicoes", "entrou_top5", "subida_forte"),
    },
}
# Desfecho 0/1: prec@k vira acerto no top-k, comparado com a taxa base, e AUC
BINARIOS = {"entrou_top5", "subida_forte"}
# Posições ganhas de uma coleta para a seguinte que contam como subida forte
SUBIDA_FORTE = 10
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


def _itens_ml(db, pontuados, t, t_futuro, t_confirmacao, subida: int) -> list[dict]:
    if t_futuro is None:
        return []
    agora, depois = _ranking_em(db, t), _ranking_em(db, t_futuro)
    confirmacao = _ranking_em(db, t_confirmacao) if t_confirmacao is not None else {}
    coletadas = {cat for _, cat in depois.values()}
    itens = []
    for ti, score in pontuados:
        if ti.product_id not in agora:
            continue
        pos, categoria = agora[ti.product_id]
        if ti.product_id in depois:
            if depois[ti.product_id][1] != categoria:
                continue
            pos_fut = depois[ti.product_id][0]
        elif categoria in coletadas:
            pos_fut = FORA_DO_RANKING
        else:
            continue
        subida_forte = None
        if pos > subida and t_confirmacao is not None:
            confirmado = confirmacao.get(ti.product_id)
            if confirmado is not None:
                pos_confirmada = confirmado[0] if confirmado[1] == categoria else None
            elif categoria in {cat for _, cat in confirmacao.values()}:
                pos_confirmada = FORA_DO_RANKING
            else:
                pos_confirmada = None
            if pos_confirmada is not None:
                subida_forte = int(pos - pos_fut >= subida and pos - pos_confirmada >= subida)
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
                # Do 1º ao `subida`-ésimo não dá para ganhar `subida` posições
                "subida_forte": subida_forte,
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
        m["auc"] = auc([a for a, _ in pares], [int(b) for _, b in pares])
    else:
        m["precision_at_k"] = precision_at_k(previsto, real, k=k)
    return m


def _corte(
    db,
    t: dt.datetime,
    *,
    source,
    t_futuro,
    t_confirmacao,
    horizon_hours,
    window_hours,
    limit_products,
    k,
    min_pool,
    subida,
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
        itens = _itens_ml(db, pontuados, t, t_futuro, t_confirmacao, subida)
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
                preditores[p][d] = dict(vazio, taxa_base=None, auc=None) if d in BINARIOS else vazio
                continue
            preditores[p][d] = _metricas(pares, d, k)
    return {"T": t.isoformat(), "n": len(itens), "preditores": preditores, "itens": itens}


def _metricas_de(desfecho: str) -> tuple[str, ...]:
    return ("spearman", "precision_at_k") + (("taxa_base", "auc") if desfecho in BINARIOS else ())


def _comparacao(cortes: list[dict], source: str) -> dict:
    """score − baseline em cada corte, agrupado por dia (UTC) para o IC."""
    preditores = FONTES[source]["preditores"]
    saida = {}
    for base in preditores[1:]:
        saida[base] = {}
        for d in FONTES[source]["desfechos"]:
            saida[base][d] = {}
            for m in _metricas_de(d):
                if m == "taxa_base":
                    continue
                por_dia: dict[str, list[float]] = {}
                for c in cortes:
                    a = c["preditores"][preditores[0]][d][m]
                    b = c["preditores"][base][d][m]
                    if a is not None and b is not None:
                        por_dia.setdefault(c["T"][:10], []).append(a - b)
                saida[base][d][m] = ic_bootstrap_por_bloco(por_dia)
    return saida


def _quantil(valores: list[float], q: float) -> float | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    pos = (len(ordenados) - 1) * q
    baixo, alto = math.floor(pos), math.ceil(pos)
    if baixo == alto:
        return ordenados[baixo]
    peso = pos - baixo
    return ordenados[baixo] * (1 - peso) + ordenados[alto] * peso


def _classificacao_calibracao_tiktok(score: float, threshold: float) -> str:
    """Classificação de fallback coerente com o limiar que está sendo calibrado.

    ``SUBINDO`` começa no limiar candidato, para que dois scores elegíveis não
    aparentem uma subida de faixa apenas porque o corte fixo de produção é 85.
    ``VIRALIZANDO`` nunca desce do corte absoluto calibrado de 92; para limiar
    acima de 85, acompanha-o mantendo a distância histórica de 7 pontos. Isso
    evita transformar o cruzamento de 85 em escalada quando se calibra abaixo
    dele, sem permitir que VIRALIZANDO fique no mesmo nível de SUBINDO.
    """
    subindo_padrao, viralizando_padrao = FALLBACK_CORTES["tiktok"]
    viralizando = max(viralizando_padrao, threshold + viralizando_padrao - subindo_padrao)
    if score >= viralizando:
        return "VIRALIZANDO"
    if score >= threshold:
        return "SUBINDO"
    return "ESTAVEL"


def _simula_alertas_tiktok(cortes: list[dict], threshold: float) -> int:
    """Reproduz a decisão pura de alerta, sem ler nem escrever ``alert_state``."""
    estados: dict[str, AlertState] = {}
    cooldown = dt.timedelta(hours=settings.ALERT_COOLDOWN_HOURS)
    alertas = 0
    for corte in sorted(cortes, key=lambda c: c["T"]):
        agora = ensure_utc(dt.datetime.fromisoformat(corte["T"]))
        for item in corte["itens"]:
            score = float(item["score"])
            product_id = item["product_id"]
            classificacao = _classificacao_calibracao_tiktok(score, threshold)
            decisao = decide_alert(
                final_score=score,
                classification=classificacao,
                last=estados.get(product_id),
                now=agora,
                threshold=threshold,
                cooldown=cooldown,
            )
            if decisao.send:
                alertas += 1
                estados[product_id] = AlertState(agora, classificacao)
    return alertas


def _calibracao_tiktok(cortes: list[dict], thresholds: tuple[float, ...]) -> dict:
    scores = [float(item["score"]) for corte in cortes for item in corte["itens"]]
    dias = sorted({corte["T"][:10] for corte in cortes})
    intervalo = {
        "primeiro_T": cortes[0]["T"] if cortes else None,
        "ultimo_T": cortes[-1]["T"] if cortes else None,
        "dias": len(dias),
    }
    por_limiar = {}
    for threshold in thresholds:
        candidatos = [
            item for corte in cortes for item in corte["itens"] if float(item["score"]) >= threshold
        ]
        alertas = _simula_alertas_tiktok(cortes, threshold)
        por_limiar[f"{threshold:g}"] = {
            "candidatos": len(candidatos),
            "videos_distintos": len({item["product_id"] for item in candidatos}),
            "percentual": len(candidatos) / len(scores) if scores else None,
            "candidatos_por_dia": len(candidatos) / len(dias) if dias else None,
            "alertas_simulados": alertas,
            "alertas_simulados_por_dia": alertas / len(dias) if dias else None,
        }
    return {
        "n_scores": len(scores),
        # Mantido para consumidores antigos; o intervalo explícito é canônico.
        "dias": len(dias),
        "intervalo_observado": intervalo,
        "percentis": {
            nome: _quantil(scores, q)
            for nome, q in (("p50", 0.50), ("p90", 0.90), ("p95", 0.95), ("p99", 0.99))
        },
        "limiares": por_limiar,
        "limitacoes": [
            "o replay usa apenas o score numerico e nao refaz chamadas ao LLM",
            "conta apenas cortes com pool minimo e desfecho futuro no horizonte",
            "a simulacao alinha SUBINDO ao limiar candidato; VIRALIZANDO nao desce de 92 e, acima de 85, preserva a distancia de 7 pontos; usa a mesma regra de cooldown do produto",
        ],
    }


def _percentil(itens: list[dict], campo: str, valor: float) -> float:
    """Fração do corte com `campo` abaixo de `valor` (empate conta meio)."""
    vals = [i[campo] for i in itens if i[campo] is not None]
    abaixo = sum(1 for v in vals if v < valor) + 0.5 * sum(1 for v in vals if v == valor)
    return abaixo / len(vals)


def _eventos(cortes: list[dict]) -> list[dict]:
    """Cada subida forte, uma vez por produto: a primeira vez que aparece."""
    vistos, eventos = set(), []
    for c in cortes:
        for i in c["itens"]:
            if i.get("subida_forte") != 1 or i["product_id"] in vistos:
                continue
            vistos.add(i["product_id"])
            eventos.append(
                {
                    "T": c["T"],
                    "product_id": i["product_id"],
                    "title": i["title"],
                    "category": i["category"],
                    "pos_T": i["pos_T"],
                    "pos_futura": i["pos_futura"],
                    # Onde o produto estava no corte antes de subir, por preditor
                    "pct_score": _percentil(c["itens"], "score", i["score"]),
                    "pct_posicao": _percentil(c["itens"], "posicao_atual", i["posicao_atual"]),
                }
            )
    return eventos


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
    subida: int = SUBIDA_FORTE,
    calibration_thresholds: tuple[float, ...] = (75.0, 80.0, 85.0, 90.0, 92.0),
) -> dict:
    if limit_products is None:
        limit_products = settings.TREND_MAX_PRODUCTS
    if source not in FONTES:
        raise ValueError(f"fonte sem backtest: {source}")
    # Cortes nos instantes de coleta da própria fonte
    cortes_ts = sorted({ensure_utc(t) for t in db["metrics"].distinct("ts", {"source": source})})
    cortes, pulados = [], 0
    for t in cortes_ts:
        t_futuro = _proximo(cortes_ts, t, horizon_hours)
        c = _corte(
            db,
            t,
            source=source,
            t_futuro=t_futuro,
            t_confirmacao=(
                _proximo(cortes_ts, t_futuro, horizon_hours)
                if source == "mercadolivre" and t_futuro is not None
                else None
            ),
            horizon_hours=horizon_hours,
            window_hours=window_hours,
            limit_products=limit_products,
            k=k,
            min_pool=min_pool,
            subida=subida,
        )
        if c is None:
            pulados += 1
        else:
            cortes.append(c)

    resumo = {
        p: {
            d: {m: summarize([c["preditores"][p][d][m] for c in cortes]) for m in _metricas_de(d)}
            for d in FONTES[source]["desfechos"]
        }
        for p in FONTES[source]["preditores"]
    }
    relatorio = {
        "params": {
            "source": source,
            "horizon_hours": horizon_hours,
            "window_hours": window_hours,
            "limit_products": limit_products,
            "k": k,
            "min_pool": min_pool,
            "subida": subida,
            "normalizacao": settings.SCORE_NORMALIZATION,
        },
        "cortes": cortes,
        "cortes_pulados": pulados,
        "resumo": resumo,
        "comparacao": _comparacao(cortes, source),
        "eventos": _eventos(cortes) if source == "mercadolivre" else [],
    }
    if source == "tiktok":
        relatorio["calibracao"] = _calibracao_tiktok(cortes, calibration_thresholds)
    return relatorio


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
        f"{'base':>8}{'auc':>8}",
    ]
    for p, por_d in rel["resumo"].items():
        for d, m in por_d.items():
            # Desfecho 0/1: prec@k é o acerto no top-k; compare com a taxa base.
            # AUC 0,5 = acaso
            base = _fmt(m["taxa_base"]["media"]) if "taxa_base" in m else ""
            area = _fmt(m["auc"]["media"]) if "auc" in m else ""
            linhas.append(
                f"{p:<16}{d:<16}{_fmt(m['spearman']['media']):>10}"
                f"{_fmt(m['precision_at_k']['media']):>10}{m['spearman']['n']:>8}{base:>8}"
                f"{area:>8}"
            )
    linhas += _tabela_comparacao(rel) + _tabela_calibracao(rel) + _tabela_eventos(rel)
    return "\n".join(linhas)


def _ic(r: dict | None) -> str:
    if r is None:
        return "  sem 2 dias"
    sinal = "  ← não é sorte" if r["ic_sup"] < 0 or r["ic_inf"] > 0 else ""
    return (
        f"{_fmt(r['media'])} [{_fmt(r['ic_inf'])}, {_fmt(r['ic_sup'])}] ({r['blocos']} dias){sinal}"
    )


def _tabela_comparacao(rel: dict) -> list[str]:
    linhas = ["", "score − baseline (média por dia, IC 95% por bootstrap de dias):"]
    for base, por_d in rel["comparacao"].items():
        for d, por_m in por_d.items():
            for m, r in por_m.items():
                linhas.append(f"  − {base:<14}{d:<16}{m:<16}{_ic(r)}")
    return linhas


def _tabela_calibracao(rel: dict) -> list[str]:
    calibracao = rel.get("calibracao")
    if calibracao is None:
        return []
    percentis = calibracao["percentis"]
    linhas = [
        "",
        f"Calibracao TikTok: {calibracao['n_scores']} scores em {calibracao['dias']} dias",
        "  intervalo observado: "
        f"{calibracao['intervalo_observado']['primeiro_T'] or '-'} até "
        f"{calibracao['intervalo_observado']['ultimo_T'] or '-'}",
        "  " + "  ".join(f"{nome}={_fmt(valor)}" for nome, valor in percentis.items()),
        "  limiar  candidatos  vídeos distintos  candidatos/dia  alertas/dia (sim.)",
    ]
    for threshold, dados in calibracao["limiares"].items():
        por_dia = dados["candidatos_por_dia"]
        dia_txt = "-" if por_dia is None else f"{por_dia:.1f}"
        alertas_dia = dados["alertas_simulados_por_dia"]
        alertas_txt = "-" if alertas_dia is None else f"{alertas_dia:.1f}"
        linhas.append(
            f"  {threshold:>6}  {dados['candidatos']:>10}  {dados['videos_distintos']:>17}"
            f"  {dia_txt:>15}  {alertas_txt:>18}"
        )
    linhas.extend(f"  AVISO: {texto}" for texto in calibracao["limitacoes"])
    return linhas


def _tabela_eventos(rel: dict) -> list[str]:
    if rel["params"]["source"] != "mercadolivre":
        return []
    ev = rel["eventos"]
    linhas = [
        "",
        f"Subidas fortes (≥ {rel['params']['subida']} posições até a coleta seguinte): "
        f"{len(ev)} produtos",
    ]
    if ev:
        media = sum(e["pct_score"] for e in ev) / len(ev)
        media_pos = sum(e["pct_posicao"] for e in ev) / len(ev)
        linhas.append(
            f"  no corte de antes, percentil médio pelo score {media:.2f} e pela posição "
            f"{media_pos:.2f} (0,5 = no meio do ranking)"
        )
    for e in ev[:10]:
        linhas.append(
            f"  {e['T'][:16]}  {e['pos_T']:>2}º → {e['pos_futura']:>2}º  "
            f"score p{100 * e['pct_score']:.0f}  {e['title']}"
        )
    return linhas


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
    parser.add_argument(
        "--subida", type=int, default=SUBIDA_FORTE, help="posições que contam como subida forte"
    )
    parser.add_argument(
        "--calibration-thresholds",
        type=float,
        nargs="+",
        default=[75, 80, 85, 90, 92],
        help="limiares candidatos para o relatório TikTok",
    )
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
        subida=args.subida,
        calibration_thresholds=tuple(args.calibration_thresholds),
    )
    print(json.dumps(rel, ensure_ascii=False, default=str) if args.json else _tabela(rel))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
