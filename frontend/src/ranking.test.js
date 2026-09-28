// src/ranking.test.js — lógica da tela de ranking (TIE-28)
import { describe, expect, it } from "vitest";
import { sortItems, viewState, RISK_ORDER, CLASS_ORDER } from "./ranking.js";

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
