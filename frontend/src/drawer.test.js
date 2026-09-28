// src/drawer.test.js — lógica do drawer de produto (TIE-29)
import { describe, expect, it } from "vitest";
import { buildSeries, historyState, scoreBreakdown, formatTs } from "./drawer.js";

const pontos = [
  { ts: "2026-09-26T13:05:00+00:00", views: 100, engagement: 10, price: null },
  { ts: "2026-09-27T13:05:00+00:00", views: 250, engagement: 30 },
];

describe("buildSeries", () => {
  it("usa a métrica escolhida", () => {
    expect(buildSeries(pontos, "views").values).toEqual([100, 250]);
  });

  it("métrica ausente vira lacuna (null), não zero", () => {
    expect(buildSeries(pontos, "price").values).toEqual([null, null]);
  });

  it("rótulo tem dia e hora: em 72 h, 13:05 se repete em dias diferentes", () => {
    const { labels } = buildSeries(pontos, "views");
    expect(labels[0]).not.toEqual(labels[1]);
    expect(labels[0]).toMatch(/^\d{2}\/\d{2} \d{2}:\d{2}$/);
  });
});

describe("formatTs", () => {
  it("valor inválido não quebra", () => {
    expect(formatTs("lixo")).toBe("lixo");
  });
});

describe("historyState", () => {
  it("menos de 2 pontos não forma curva", () => {
    expect(historyState([])).toBe("vazio");
    expect(historyState(null)).toBe("vazio");
    expect(historyState([pontos[0]])).toBe("vazio");
  });
  it("com 2+ pontos há curva", () => {
    expect(historyState(pontos)).toBe("ok");
  });
  it("todos os valores da métrica ausentes também é vazio", () => {
    expect(historyState(pontos, "mentions")).toBe("vazio");
  });
});

describe("scoreBreakdown", () => {
  const insight = {
    numeric_score: 40,
    llm_score: 80,
    final_score: 56,
    score_weights: { hybrid: { numeric: 0.6, llm: 0.4 } },
    debug: {},
  };

  it("numérico, IA e final lado a lado, com os pesos usados", () => {
    const b = scoreBreakdown(insight);
    expect(b.partes.map((p) => [p.label, p.value, p.peso])).toEqual([
      ["Numérico", 40, 0.6],
      ["IA", 80, 0.4],
      ["Final", 56, null],
    ]);
    expect(b.llmIndisponivel).toBe(false);
  });

  it("insight antigo sem pesos gravados usa o padrão 0.6/0.4", () => {
    const b = scoreBreakdown({ ...insight, score_weights: undefined });
    expect(b.partes[0].peso).toBe(0.6);
  });

  it("sinaliza quando o LLM caiu e o score é só numérico", () => {
    const b = scoreBreakdown({ ...insight, llm_score: 0, debug: { llm_error: "timeout" } });
    expect(b.llmIndisponivel).toBe(true);
    expect(b.motivo).toBe("timeout");
  });

  it("sem insight não quebra", () => {
    expect(scoreBreakdown(null)).toBeNull();
  });
});
