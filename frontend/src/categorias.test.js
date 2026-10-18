import { describe, expect, it } from "vitest";
import {
  filtraCategorias,
  nomeCategoria,
  normalizaIdCategoria,
  opcoesDoFiltro,
  rotuloCategoria,
} from "./categorias.js";

describe("normalizaIdCategoria", () => {
  it("aceita o ID com caixa e espaço trocados", () => {
    expect(normalizaIdCategoria(" mlb 1618 ")).toBe("MLB1618");
  });
  it("recusa o que a API recusaria", () => {
    for (const ruim of ["", "1618", "MLB", "MLB16x", "MLB-1618", null]) {
      expect(normalizaIdCategoria(ruim)).toBeNull();
    }
  });
});

describe("nomeCategoria", () => {
  it("prefere o nome, depois o ID, depois traço", () => {
    expect(nomeCategoria({ category: "MLB1618", category_name: "Cozinha" })).toBe("Cozinha");
    expect(nomeCategoria({ category: "MLB1618", category_name: null })).toBe("MLB1618");
    expect(nomeCategoria({ category: null })).toBe("-");
  });
});

describe("rotuloCategoria", () => {
  it("mostra o caminho da subcategoria", () => {
    const cat = { name: "Cozinha", path: ["Casa, Móveis e Decoração", "Cozinha"] };
    expect(rotuloCategoria(cat)).toBe("Casa, Móveis e Decoração › Cozinha");
  });
  it("categoria de primeiro nível ou sem caminho mostra só o nome", () => {
    expect(rotuloCategoria({ name: "Agro", path: ["Agro"] })).toBe("Agro");
    expect(rotuloCategoria({ name: "Agro" })).toBe("Agro");
  });
});

describe("opcoesDoFiltro", () => {
  it("habilitadas primeiro, depois por nome", () => {
    const cats = [
      { ml_category_id: "MLB3", name: "Beleza", enabled: false },
      { ml_category_id: "MLB2", name: "Cozinha", enabled: true },
      { ml_category_id: "MLB1", name: "Agro", enabled: false },
      { ml_category_id: "MLB4", name: "Áudio", enabled: true },
      { name: "sem id", enabled: true },
    ];
    expect(opcoesDoFiltro(cats).map((c) => c.ml_category_id)).toEqual(["MLB4", "MLB2", "MLB1", "MLB3"]);
  });
});

describe("filtraCategorias", () => {
  const cats = [
    { ml_category_id: "MLB1000", name: "Eletrônicos, Áudio e Vídeo" },
    { ml_category_id: "MLB1618", name: "Cozinha" },
  ];
  it("ignora acento e caixa", () => {
    expect(filtraCategorias(cats, "eletronicos").map((c) => c.ml_category_id)).toEqual(["MLB1000"]);
  });
  it("acha pelo ID", () => {
    expect(filtraCategorias(cats, "1618").map((c) => c.ml_category_id)).toEqual(["MLB1618"]);
  });
  it("texto vazio devolve tudo", () => {
    expect(filtraCategorias(cats, "  ")).toHaveLength(2);
  });
});
