// src/api.test.js — mensagens de erro úteis (TIE-28): nunca tela branca
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api.js";

afterEach(() => vi.unstubAllGlobals());

function stubFetch(impl) {
  vi.stubGlobal("fetch", vi.fn(impl));
}

describe("httpGet", () => {
  it("API fora do ar vira mensagem com a URL e a dica de CORS", async () => {
    stubFetch(() => Promise.reject(new TypeError("Failed to fetch")));
    await expect(api.rankingsLatest({ hours: 72, limit: 20, source: "all" })).rejects.toThrow(
      /Não foi possível falar com a API.*CORS_ORIGINS/s
    );
  });

  it("401 explica a API_KEY", async () => {
    stubFetch(() => Promise.resolve(new Response("", { status: 401 })));
    await expect(api.rankingsLatest({ hours: 72, limit: 20, source: "all" })).rejects.toThrow(/X-API-Key/);
  });

  it("429 pede para esperar", async () => {
    stubFetch(() => Promise.resolve(new Response("", { status: 429 })));
    await expect(api.rankingsLatest({ hours: 72, limit: 20, source: "all" })).rejects.toThrow(/Muitas requisições/);
  });

  it("5xx usa o detail da API quando existe", async () => {
    stubFetch(() =>
      Promise.resolve(new Response(JSON.stringify({ detail: "Índice de busca ausente" }), { status: 503 }))
    );
    await expect(api.rankingsLatest({ hours: 72, limit: 20, source: "all" })).rejects.toThrow(
      /503.*Índice de busca ausente/
    );
  });

  it("monta a query com cursor só quando há cursor", async () => {
    stubFetch(() => Promise.resolve(new Response("{}", { status: 200 })));
    await api.rankingsLatest({ hours: 24, limit: 10, source: "tiktok" });
    await api.rankingsLatest({ hours: 24, limit: 10, source: "tiktok", cursor: "abc" });
    const urls = fetch.mock.calls.map((c) => c[0]);
    expect(urls[0]).toMatch(/\/rankings\/latest\?hours=24&limit=10&source=tiktok$/);
    expect(urls[1]).toMatch(/&cursor=abc$/);
  });
});

describe("productInsightLatest", () => {
  it("404 (produto ainda sem análise) vira null, não erro", async () => {
    stubFetch(() => Promise.resolve(new Response(JSON.stringify({ detail: "Insight não encontrado" }), { status: 404 })));
    await expect(api.productInsightLatest({ productId: "p1" })).resolves.toBeNull();
  });

  it("outros erros continuam sendo erro", async () => {
    stubFetch(() => Promise.resolve(new Response("", { status: 500 })));
    await expect(api.productInsightLatest({ productId: "p1" })).rejects.toThrow(/HTTP 500/);
  });
});
