# src/infrastructure/db/repos.py


import uuid
from typing import Any

from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from src.infrastructure.utils.datetime_utils import utcnow


class ProductRepo:
    """
    Identificadores de `products` (TIE-19):

    - `(source, source_product_id)` — **chave de deduplicação**, índice único
      `ux_source_sourceProduct`. É o id na origem: item do ML (`MLB123`) ou
      id numérico do vídeo do TikTok.
    - `product_id` — UUID interno, estável. É o que `metrics`, `trend_insights`
      e `alert_state` referenciam. Gerado uma vez, no insert.
    - `canonical_id` — reservado para casar o mesmo produto **entre fontes**
      (TIE-18). Hoje repete o `source_product_id`; em documentos antigos é um
      slug de marca/categoria/título (migração v002). Não é chave de dedupe.

    Não existe campo `uuid`: a migração "v003_add_product_uuid" preenche
    `product_id`.
    """

    def __init__(self, db: Database):
        self.col = db["products"]
        self.col.create_index(
            [("source", 1), ("source_product_id", 1)], unique=True, name="ux_source_sourceProduct"
        )
        self.col.create_index("product_id", unique=True, name="ux_product_uuid")
        self.col.create_index("canonical_id", name="ix_canonical")

    def upsert(self, item: dict[str, Any]) -> str:
        """Grava o item e devolve o `product_id` **que está no banco**."""
        source = item.get("source")
        spid = item.get("source_product_id")
        if not source or not spid:
            raise ValueError("item sem source/source_product_id: não há como deduplicar")

        now = utcnow()
        filt = {"source": source, "source_product_id": spid}
        update = {
            "$setOnInsert": {
                "created_at": now,
                "product_id": str(uuid.uuid4()),
                "source": source,
                "source_product_id": spid,
            },
            "$set": {
                "updated_at": now,
                "title": item.get("title"),
                "brand": item.get("brand"),
                "category": item.get("category"),
                "canonical_id": item.get("canonical_id"),
                "permalink": item.get("permalink"),
                "schema_version": 3,
            },
        }
        # Só grava quando veio: uma coleta sem o dado não apaga o que já existe
        for campo in ("published_at", "has_shop_product", "language"):
            if item.get(campo) is not None:
                update["$set"][campo] = item[campo]

        # Atômico: ler-e-depois-gravar deixava a coleta que perdia uma corrida
        # devolver um UUID que nunca foi gravado — métricas órfãs, série partida.
        for tentativa in range(2):
            try:
                doc = self.col.find_one_and_update(
                    filt,
                    update,
                    upsert=True,
                    return_document=ReturnDocument.AFTER,
                    projection={"_id": 0, "product_id": 1},
                )
                break
            except DuplicateKeyError:
                # Duas coletas inseriram o mesmo item novo ao mesmo tempo; na
                # segunda tentativa o filtro encontra o documento da outra.
                if tentativa:
                    raise

        if not doc.get("product_id"):
            # Documento anterior à v003, sem product_id: completa agora
            self.col.update_one(
                {**filt, "product_id": {"$exists": False}},
                {"$set": {"product_id": str(uuid.uuid4())}},
            )
            doc = self.col.find_one(filt, {"_id": 0, "product_id": 1})
        return doc["product_id"]

    def get(self, product_id: str) -> dict[str, Any] | None:
        return self.col.find_one({"product_id": product_id}, {"_id": 0})


class MetricsRepo:
    def __init__(self, db: Database):
        self.col = db["metrics"]
        self.col.create_index([("product_id", 1), ("ts", -1)], name="ix_metrics_product_ts")
        self.col.create_index([("ts", -1)], name="ix_metrics_ts")
        self.col.create_index([("source", 1), ("ts", -1)], name="ix_metrics_source_ts")

    def previous_reviews_total(self, product_id: str) -> int | None:
        """Total de avaliações da leitura imediatamente anterior."""
        metric = self.col.find_one(
            {"product_id": product_id},
            # Duas leituras podem compartilhar o mesmo `ts` (o worker calcula
            # um instante por coleta). `_id` desempata pela ordem de inserção.
            sort=[("ts", -1), ("_id", -1)],
            projection={"_id": 0, "reviews_total": 1},
        )
        return metric.get("reviews_total") if metric else None

    def insert(self, metric: dict[str, Any]) -> None:
        self.col.insert_one(metric)


def onde_comprar(db: Database, produtos: dict[str, dict]) -> dict[str, dict]:
    """
    product_id do vídeo → produto do ML vinculado, com preço e link (TIE-52).
    Só entra quem já tem `ml_offers` lido; o preço é o do buy box quando
    existe, senão a menor oferta (que pode ser anúncio fora da curva).
    """
    vinculo = {
        pid: (p.get("ml_match") or {}).get("ml_product_id")
        for pid, p in produtos.items()
        if (p.get("ml_match") or {}).get("status") == "vinculado"
    }
    if not vinculo:
        return {}
    ofertas = {
        p["product_id"]: p["ml_offers"]
        for p in db["products"].find(
            {"product_id": {"$in": sorted({v for v in vinculo.values() if v})}},
            {"_id": 0, "product_id": 1, "ml_offers": 1},
        )
        if p.get("ml_offers")
    }
    saida = {}
    for video, ml_pid in vinculo.items():
        o = ofertas.get(ml_pid)
        if o is None:
            continue
        preco = o.get("preco_buybox") if o.get("preco_buybox") is not None else o.get("preco_min")
        # Sem página de produto (vendido fora do catálogo), a busca pelo nome
        link = o.get("permalink") or o.get("busca")
        if not link:
            continue
        saida[video] = {
            "nome": o.get("nome"),
            "link": link,
            "link_tipo": "produto" if o.get("permalink") else "busca",
            "preco": preco,
            "preco_min": o.get("preco_min"),
            "moeda": o.get("moeda"),
            "n_ofertas": o.get("n_ofertas"),
            "disponivel": o.get("disponivel", True),
            "atualizado_em": o.get("ts"),
        }
    return saida
