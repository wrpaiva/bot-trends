// src/ranking.js — lógica pura da tela de ranking (TIE-28), testada em ranking.test.js

// Ordem de gravidade: "desc" põe o mais forte/arriscado primeiro
export const CLASS_ORDER = { EM_QUEDA: 0, ESTAVEL: 1, PICO_TEMPORARIO: 2, SUBINDO: 3, VIRALIZANDO: 4 };
export const RISK_ORDER = { BAIXO: 0, MEDIO: 1, ALTO: 2 };

function valor(item, key) {
  if (key === "trend_classification") return CLASS_ORDER[item[key]];
  if (key === "risk_level") return RISK_ORDER[item[key]];
  if (key === "title") return (item.title || item.product_id || "").toLowerCase();
  return item[key];
}

// Ordena sem mutar; nulos/desconhecidos sempre no fim, em qualquer direção
export function sortItems(items, key, dir = "desc") {
  const sinal = dir === "asc" ? 1 : -1;
  return [...(items || [])].sort((a, b) => {
    const va = valor(a, key);
    const vb = valor(b, key);
    const na = va === null || va === undefined;
    const nb = vb === null || vb === undefined;
    if (na || nb) return na === nb ? 0 : na ? 1 : -1;
    if (va < vb) return -1 * sinal;
    if (va > vb) return 1 * sinal;
    return 0;
  });
}

// Qual tela mostrar. Carregando a próxima página mantém a tabela visível.
export function viewState({ loading, error, items }) {
  if (error) return "error";
  if (items && items.length) return "ok";
  return loading ? "loading" : "empty";
}

// Só a requisição mais nova pode mexer na tela. Sem isso, trocar o período e
// logo a fonte fazia a resposta antiga, mais lenta, sobrescrever a nova (QA de
// 2026-10-14), e o "Carregar mais" anexava a página antiga à lista nova.
// `inicia()` aborta a anterior; quem chama confere `valida()` antes de gravar.
export function ultimaRequisicao() {
  let atual = 0;
  let controle = null;
  return {
    inicia() {
      controle?.abort();
      controle = new AbortController();
      const id = ++atual;
      return { signal: controle.signal, valida: () => id === atual };
    },
    cancela() {
      controle?.abort();
      controle = null;
      atual++;
    },
  };
}

// Vídeo vinculado: onde comprar no ML (TIE-52). null sem vínculo, sem oferta
// lida ou com o produto fora do catálogo — a tela simplesmente não mostra.
const BRL = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

export function ofertaML(item) {
  const o = item?.ml_offer;
  if (!o || o.disponivel === false || !o.link) return null;
  const partes = [];
  if (o.preco != null) partes.push(BRL.format(o.preco));
  if (o.n_ofertas) partes.push(`${o.n_ofertas} ${o.n_ofertas === 1 ? "oferta" : "ofertas"}`);
  // Vendido fora do catálogo: o link é a busca do ML pelo nome, sem preço
  const busca = o.link_tipo === "busca";
  if (!partes.length) partes.push(busca ? "ver anúncios" : "sem anúncio ativo");
  const nome = o.nome || "produto no Mercado Livre";
  return { nome: busca ? `buscar ${nome}` : nome, link: o.link, resumo: partes.join(" · ") };
}
