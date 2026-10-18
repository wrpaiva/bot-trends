// src/ranking.test.js — lógica da tela de ranking (TIE-28)
import { describe, expect, it } from "vitest";
import { sortItems, viewState, ultimaRequisicao, ofertaML, RISK_ORDER, CLASS_ORDER } from "./ranking.js";

const itens = [
  { product_id: "a", title: "Beta", final_score: 50, trend_classification: "SUBINDO", risk_level: "ALTO" },
  { product_id: "b", title: "alfa", final_score: 90, trend_classification: "VIRALIZANDO", risk_level: "BAIXO" },
  { product_id: "c", title: null, final_score: null, trend_classification: "EM_QUEDA", risk_level: "MEDIO" },
];

describe("sortItems", () => {
  it("ordena por score desc com nulos no fim", () => {
    expect(sortItems(itens, "final_score", "desc").map((i) => i.product_id)).toEqual(["b", "a", "c"]);
  });

  it("nulos ficam no fim também em ordem asc", () => {
    expect(sortItems(itens, "final_score", "asc").map((i) => i.product_id)).toEqual(["a", "b", "c"]);
  });

  it("texto ignora maiúsculas e cai para o product_id quando não há título", () => {
    expect(sortItems(itens, "title", "asc").map((i) => i.product_id)).toEqual(["b", "a", "c"]);
  });

  it("classificação e risco seguem a ordem de gravidade, não a alfabética", () => {
    expect(sortItems(itens, "trend_classification", "desc").map((i) => i.trend_classification)).toEqual([
      "VIRALIZANDO",
      "SUBINDO",
      "EM_QUEDA",
    ]);
    expect(sortItems(itens, "risk_level", "desc").map((i) => i.risk_level)).toEqual(["ALTO", "MEDIO", "BAIXO"]);
    expect(RISK_ORDER.ALTO).toBeGreaterThan(RISK_ORDER.BAIXO);
    expect(CLASS_ORDER.VIRALIZANDO).toBeGreaterThan(CLASS_ORDER.ESTAVEL);
  });

  it("não altera a lista original", () => {
    const copia = [...itens];
    sortItems(itens, "final_score", "asc");
    expect(itens).toEqual(copia);
  });
});

describe("viewState", () => {
  it("erro vence tudo", () => {
    expect(viewState({ loading: false, error: "x", items: [1] })).toBe("error");
  });
  it("carregando sem itens", () => {
    expect(viewState({ loading: true, error: "", items: [] })).toBe("loading");
  });
  it("carregando mais páginas mantém a tabela", () => {
    expect(viewState({ loading: true, error: "", items: [1] })).toBe("ok");
  });
  it("vazio", () => {
    expect(viewState({ loading: false, error: "", items: [] })).toBe("empty");
  });
});

// QA 2026-10-14: trocar o período e logo a fonte fazia a resposta antiga
// (source=all, mais lenta) sobrescrever o vazio do TikTok
describe("ultimaRequisicao", () => {
  it("só a requisição mais nova continua valendo", () => {
    const seq = ultimaRequisicao();
    const antiga = seq.inicia();
    const nova = seq.inicia();
    expect(antiga.valida()).toBe(false);
    expect(nova.valida()).toBe(true);
  });

  it("começar outra cancela a anterior", () => {
    const seq = ultimaRequisicao();
    const antiga = seq.inicia();
    seq.inicia();
    expect(antiga.signal.aborted).toBe(true);
  });

  it("resposta fora de ordem é descartada", async () => {
    const seq = ultimaRequisicao();
    let tela = null;
    const carrega = async (valor, atraso) => {
      const req = seq.inicia();
      await new Promise((r) => setTimeout(r, atraso));
      if (req.valida()) tela = valor;
    };
    // "all" sai primeiro e chega por último
    await Promise.all([carrega("all", 20), carrega("tiktok", 1)]);
    expect(tela).toBe("tiktok");
  });

  it("cancela() invalida a pendente (desmontagem)", () => {
    const seq = ultimaRequisicao();
    const req = seq.inicia();
    seq.cancela();
    expect(req.valida()).toBe(false);
    expect(req.signal.aborted).toBe(true);
  });
});

describe("ofertaML (TIE-52)", () => {
  const base = {
    nome: "Kit Brocas 9 Peças",
    link: "https://www.mercadolivre.com.br/p/MLB77",
    link_tipo: "produto",
    preco: 1299.9,
    n_ofertas: 4,
    disponivel: true,
  };

  it("resume preço em reais e nº de ofertas", () => {
    const o = ofertaML({ ml_offer: base });
    expect(o.nome).toBe("Kit Brocas 9 Peças");
    expect(o.link).toBe(base.link);
    expect(o.resumo.replace(/\s/g, " ")).toBe("R$ 1.299,90 · 4 ofertas");
  });

  it("singular com uma oferta e aviso sem anúncio", () => {
    expect(ofertaML({ ml_offer: { ...base, preco: null, n_ofertas: 1 } }).resumo).toBe("1 oferta");
    expect(ofertaML({ ml_offer: { ...base, preco: null, n_ofertas: 0 } }).resumo).toBe(
      "sem anúncio ativo",
    );
  });

  it("fora do catálogo vira busca no ML, sem preço", () => {
    const o = ofertaML({
      ml_offer: { ...base, link_tipo: "busca", link: "https://lista.mercadolivre.com.br/kit", preco: null, n_ofertas: 0 },
    });
    expect(o.nome).toBe("buscar Kit Brocas 9 Peças");
    expect(o.resumo).toBe("ver anúncios");
  });

  it("nada sem vínculo, sem link ou fora do catálogo", () => {
    expect(ofertaML({})).toBeNull();
    expect(ofertaML({ ml_offer: null })).toBeNull();
    expect(ofertaML({ ml_offer: { ...base, link: null } })).toBeNull();
    expect(ofertaML({ ml_offer: { ...base, disponivel: false } })).toBeNull();
  });
});
