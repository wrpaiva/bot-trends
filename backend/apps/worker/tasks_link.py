# apps/worker/tasks_link.py
"""
Vínculo vídeo do TikTok → produto do catálogo do ML (TIE-18).

O ranking do ML e os vídeos do TikTok quase não se cruzam: em 2026-10-16, 25
pares com duas palavras em comum entre 501 vídeos e 124 produtos, quase todos
falsos. Então o fluxo é o inverso: para cada vídeo comercial em português, o LLM
diz qual produto ele vende (ou que não nomeia nenhum), a consulta vai para
`/products/search` e `escolhe_candidato` decide se o primeiro da busca é mesmo
aquele produto. Na dúvida, não vincula.

Cada vídeo é processado uma vez: o resultado fica em `ml_match.status`
(`vinculado`, `sem_produto`, `sem_candidato`); só `erro` volta a ser tentado.
O produto do ML vinculado guarda os vídeos em `linked_videos`, e a análise
passa a listar `tiktok` nas `sources` dele.

Onde comprar (TIE-52): nenhum produto vinculado estava no `/highlights` (0 de
7 em 2026-10-18, conferido da folha à raiz da categoria), então o vídeo não
entra no score do ML. O vínculo serve para dizer onde comprar: `ml_offers` no
produto do ML (menor preço, buy box, nº de anúncios, link), gravado ao
vincular e renovado por `refresh_ml_offers`. Não vira leitura em `metrics`: o
produto não entra no ranking nem no percentil do ML.
"""

import datetime as dt
import logging

from celery import shared_task

from apps.worker.tasks import _circuito_aberto, _registra
from apps.worker.tasks import _resultado as _resultado_coleta
from src.domain.commercial import commercial_marker
from src.domain.interfaces import ProductExtractor
from src.domain.product_match import escolhe_candidato
from src.infrastructure.circuit_breaker import CircuitOpenError, breaker_for
from src.infrastructure.collectors.mercado_livre import MercadoLivreCollector
from src.infrastructure.collectors.ml_auth import MercadoLivreAuth, MLAuthError
from src.infrastructure.config import settings
from src.infrastructure.db.mongo import get_db
from src.infrastructure.db.repos import ProductRepo
from src.infrastructure.llm.openai_compatible import OpenAICompatibleLLMClient
from src.infrastructure.llm.product_extractor import (
    EXTRACTION_PROMPT_VERSION,
    LLMProductExtractor,
)
from src.infrastructure.utils.datetime_utils import utcnow

logger = logging.getLogger(__name__)

# Indireção para os testes trocarem o breaker (Redis) por um falso
_breaker = breaker_for

# Candidatos avaliados por consulta: a busca ordena por relevância
CANDIDATOS_POR_BUSCA = 5


def _build_extractor() -> ProductExtractor:
    # Temperatura 0: a mesma legenda tem que dar a mesma consulta
    return LLMProductExtractor(OpenAICompatibleLLMClient(temperature=0.0), _breaker("llm"))


def _pendentes(db, limite: int) -> list[dict]:
    """
    Vídeos comerciais em português ainda sem resultado (ou com `erro`), do mais
    novo para o mais velho. A busca é no catálogo brasileiro: vídeo em outro
    idioma não vende produto do ML.

    Vídeo com mais de `TREND_MAX_AGE_DAYS` fica de fora (TIE-63): a análise o
    descarta (`is_too_old`), então o vínculo nunca chegaria ao ranking e só
    gastaria LLM. Ele não ganha marca, para voltar se o corte mudar. Sem
    `published_at` segue elegível, como na análise.
    """
    corte = utcnow() - dt.timedelta(days=settings.TREND_MAX_AGE_DAYS)
    cursor = (
        db["products"]
        .find(
            {
                "source": "tiktok",
                "language": "pt",
                "$and": [
                    {"$or": [{"ml_match": {"$exists": False}}, {"ml_match.status": "erro"}]},
                    {"$or": [{"published_at": {"$gte": corte}}, {"published_at": None}]},
                ],
            },
            {"_id": 0, "product_id": 1, "title": 1, "has_shop_product": 1},
        )
        .sort([("published_at", -1), ("product_id", 1)])
    )
    pendentes = []
    for v in cursor:
        if commercial_marker(v.get("title"), has_shop_product=bool(v.get("has_shop_product"))):
            pendentes.append(v)
            if len(pendentes) >= limite:
                break
    return pendentes


def _marca(db, product_id: str, status: str, **campos) -> None:
    db["products"].update_one(
        {"product_id": product_id},
        {
            "$set": {
                "ml_match": {
                    "status": status,
                    "ts": utcnow(),
                    "prompt_version": EXTRACTION_PROMPT_VERSION,
                    **campos,
                }
            }
        },
    )


def _produto_ml(db, candidato) -> str:
    """
    `product_id` do produto do ML, criando-o se ainda não foi coletado. Produto
    que já veio do ranking não é sobrescrito: título e categoria são os do
    `/highlights`, e a categoria é o grupo do percentil.
    """
    existente = db["products"].find_one(
        {"source": "mercadolivre", "source_product_id": candidato.product_id},
        {"_id": 0, "product_id": 1},
    )
    if existente and existente.get("product_id"):
        return existente["product_id"]
    return ProductRepo(db).upsert(
        {
            "source": "mercadolivre",
            "source_product_id": candidato.product_id,
            "canonical_id": candidato.product_id,
            "title": candidato.nome,
        }
    )


def _grava_ofertas(db, ml, ml_pid: str, catalog_id: str) -> bool:
    """
    Grava em `ml_offers` onde comprar o produto. False se a leitura falhou (o
    valor anterior fica); `MLAuthError` sobe, porque sem token nada mais anda.
    """
    try:
        oferta = ml.ofertas(catalog_id)
    except MLAuthError:
        raise
    except Exception as e:
        logger.warning("Ofertas de %s não lidas: %s", catalog_id, e)
        return False
    doc = {"catalog_id": catalog_id, **(oferta or {}), "disponivel": oferta is not None}
    db["products"].update_one(
        {"product_id": ml_pid}, {"$set": {"ml_offers": {**doc, "ts": utcnow()}}}
    )
    return True


def _resultado(contagem: dict, errors: int) -> dict:
    """Status honesto (TIE-17): `error` quando nenhum vídeo saiu sem erro."""
    processados = contagem["processed"]
    if not errors:
        status = "ok"
    elif processados > errors:
        status = "partial"
    else:
        status = "error"
    return {"status": status, "inserted": contagem["linked"], "errors": errors, **contagem}


@shared_task(name="tasks.link_tiktok_ml")
def link_tiktok_ml(limit: int | None = None):
    db = get_db()
    limite = settings.TIKTOK_ML_LINK_MAX_PER_RUN if limit is None else limit
    contagem = {"processed": 0, "linked": 0, "no_product": 0, "no_candidate": 0}

    videos = _pendentes(db, limite)
    if not videos:
        return _resultado(contagem, 0)

    # Sem LLM ou sem credencial do ML é config, não falha do serviço: devolve o
    # motivo e não mexe em circuito nem nos vídeos.
    try:
        extrator = _build_extractor()
        auth = MercadoLivreAuth.from_settings(db)
    except (RuntimeError, MLAuthError) as e:
        logger.error("Vínculo TikTok → ML sem configuração: %s", e)
        return {"status": "error", "inserted": 0, "errors": 1, "reason": str(e)}

    breaker = _breaker("mercadolivre")
    try:
        if aberto := _circuito_aberto(breaker, "mercadolivre"):
            return aberto

        erros = 0
        with MercadoLivreCollector(categories=[], auth=auth) as ml:
            for v in videos:
                pid = v["product_id"]
                try:
                    consulta = extrator.extract_product(v.get("title") or "")
                except CircuitOpenError as e:
                    # LLM fora: o resto do ciclo daria o mesmo; tenta no próximo
                    logger.warning("Vínculo TikTok → ML interrompido: %s", e)
                    return {
                        **_resultado(contagem, erros),
                        "status": "circuit_open",
                        "service": "llm",
                    }
                except Exception as e:
                    logger.warning("Extração do produto do vídeo %s falhou: %s", pid, e)
                    _marca(db, pid, "erro", motivo="llm")
                    contagem["processed"] += 1
                    erros += 1
                    continue

                contagem["processed"] += 1
                if consulta is None:
                    _marca(db, pid, "sem_produto")
                    contagem["no_product"] += 1
                    continue

                antes = ml.errors
                candidatos = ml.buscar_catalogo(consulta)
                dominio = ml.dominio_sugerido(consulta) if candidatos is not None else None
                if ml.errors > antes:
                    # Sem o domínio a regra perde o filtro: melhor tentar de novo
                    _marca(db, pid, "erro", motivo="busca", consulta=consulta)
                    erros += 1
                    continue

                topo = candidatos[:CANDIDATOS_POR_BUSCA]
                escolha = escolhe_candidato(
                    consulta,
                    topo,
                    minimo=settings.TIKTOK_ML_MATCH_MIN_SCORE,
                    dominio=dominio,
                )
                if escolha is None:
                    # O melhor candidato fica gravado para auditar o limiar
                    _marca(
                        db,
                        pid,
                        "sem_candidato",
                        consulta=consulta,
                        dominio=dominio,
                        melhor_candidato=topo[0].nome if topo else None,
                    )
                    contagem["no_candidate"] += 1
                    continue

                ml_pid = _produto_ml(db, escolha.candidato)
                db["products"].update_one(
                    {"product_id": ml_pid}, {"$addToSet": {"linked_videos": pid}}
                )
                _marca(
                    db,
                    pid,
                    "vinculado",
                    consulta=consulta,
                    dominio=dominio,
                    ml_catalog_id=escolha.candidato.product_id,
                    ml_product_id=ml_pid,
                    nome_ml=escolha.candidato.nome,
                    score=round(escolha.score, 3),
                )
                contagem["linked"] += 1
                # Onde comprar já no vínculo; se falhar, a renovação refaz
                try:
                    _grava_ofertas(db, ml, ml_pid, escolha.candidato.product_id)
                except MLAuthError as e:
                    logger.warning("Ofertas de %s sem token: %s", escolha.candidato.product_id, e)
            ml_erros = ml.errors
    finally:
        auth.close()

    resultado = _resultado(contagem, erros)
    # O circuito do ML só sente as falhas do ML: resposta ruim do LLM não é dele
    if ml_erros and ml_erros >= contagem["processed"] - contagem["no_product"]:
        breaker.record_failure()
    else:
        breaker.record_success()
    logger.info("vinculo.tiktok_ml", extra=resultado)
    return resultado


@shared_task(name="tasks.refresh_ml_offers")
def refresh_ml_offers():
    """Renova `ml_offers` dos produtos do ML com vídeo vinculado (TIE-52)."""
    db = get_db()
    alvos = list(
        db["products"].find(
            {"source": "mercadolivre", "linked_videos": {"$exists": True, "$ne": []}},
            {"_id": 0, "product_id": 1, "source_product_id": 1},
        )
    )
    if not alvos:
        return _resultado_coleta(0, 0)

    try:
        auth = MercadoLivreAuth.from_settings(db)
    except MLAuthError as e:
        logger.error("Ofertas do ML sem credenciais: %s", e)
        return {"status": "error", "inserted": 0, "errors": 1, "reason": str(e)}

    breaker = _breaker("mercadolivre")
    try:
        if aberto := _circuito_aberto(breaker, "mercadolivre"):
            return aberto
        lidos = erros = 0
        with MercadoLivreCollector(categories=[], auth=auth) as ml:
            for a in alvos:
                try:
                    ok = _grava_ofertas(db, ml, a["product_id"], a["source_product_id"])
                except MLAuthError as e:
                    # Token é config, não falha do serviço: não mexe no circuito
                    logger.error("Ofertas do ML interrompidas: %s", e)
                    return {
                        "status": "error",
                        "inserted": lidos,
                        "errors": erros + 1,
                        "reason": str(e),
                    }
                lidos += ok
                erros += not ok
    finally:
        auth.close()
    resultado = _registra(breaker, _resultado_coleta(lidos, erros))
    logger.info("ofertas.ml", extra=resultado)
    return resultado
