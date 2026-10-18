// src/api.test.js — mensagens de erro úteis (TIE-28): nunca tela branca
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api.js";

afterEach(() => vi.unstubAllGlobals());

function stubFetch(impl) {
  vi.stubGlobal("fetch", vi.fn(impl));
}

describe("httpGet", () => {
  it("API fora do ar vira mensagem que aponta o proxy /api", async () => {
    stubFetch(() => Promise.reject(new TypeError("Failed to fetch")));
    await expect(api.rankingsLatest({ hours: 72, limit: 20, source: "all" })).rejects.toThrow(
      /Não foi possível falar com a API.*\/api/s
    );
  });

  it("401 aponta a API_KEY do proxy, não o build", async () => {
    stubFetch(() => Promise.resolve(new Response("", { status: 401 })));
    await expect(api.rankingsLatest({ hours: 72, limit: 20, source: "all" })).rejects.toThrow(
      /API_KEY.*container web/s
    );
  });

  // TIE-27: a chave fica no servidor (nginx / proxy do Vite), nunca no browser
  it("chama /api na mesma origem e não manda X-API-Key", async () => {
    stubFetch(() => Promise.resolve(new Response("{}", { status: 200 })));
    await api.rankingsLatest({ hours: 24, limit: 10, source: "all" });
    const [url, opts] = fetch.mock.calls[0];
    expect(url).toMatch(/^\/api\/rankings\/latest\?/);
    const headers = new Headers(opts?.headers);
    expect(headers.has("X-API-Key")).toBe(false);
  });

  it("filtro de onde comprar só vai quando ligado (TIE-53)", async () => {
    stubFetch(() => Promise.resolve(new Response("{}", { status: 200 })));
    await api.rankingsLatest({ hours: 72, limit: 20, source: "all", withOffer: true });
    await api.rankingsLatest({ hours: 72, limit: 20, source: "all", withOffer: false });
    const [ligado, desligado] = fetch.mock.calls.map(([url]) => new URL(url, "http://x").searchParams);
    expect(ligado.get("with_offer")).toBe("true");
    expect(desligado.has("with_offer")).toBe(false);
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

describe("proxy sem API", () => {
  // QA 2026-10-14: com a API fora o nginx devolve 502 em HTML e a tela só dizia "HTTP 502"
  it.each([502, 503, 504])("%i sem detail diz que a API está fora e o que olhar", async (status) => {
    stubFetch(() => Promise.resolve(new Response("<html>Bad Gateway</html>", { status })));
    await expect(api.rankingsLatest({ hours: 72, limit: 20, source: "all" })).rejects.toThrow(
      new RegExp(`${status}.*API não respondeu.*docker compose`, "s")
    );
  });

  it("cancelamento chega como AbortError, não como API fora", async () => {
    stubFetch((_url, opts) =>
      opts?.signal?.aborted
        ? Promise.reject(new DOMException("aborted", "AbortError"))
        : Promise.resolve(new Response("{}", { status: 200 }))
    );
    const c = new AbortController();
    c.abort();
    await expect(
      api.rankingsLatest({ hours: 72, limit: 20, source: "all", signal: c.signal })
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(fetch.mock.calls[0][1].signal).toBe(c.signal);
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

describe("categorias e busca (TIE-45)", () => {
  it("ranking manda category só quando escolhida", async () => {
    stubFetch(() => Promise.resolve(new Response("{}", { status: 200 })));
    await api.rankingsLatest({ hours: 72, limit: 20, source: "all", category: "MLB1618" });
    await api.rankingsLatest({ hours: 72, limit: 20, source: "all", category: "" });
    expect(new URL(fetch.mock.calls[0][0], "http://x").searchParams.get("category")).toBe("MLB1618");
    expect(new URL(fetch.mock.calls[1][0], "http://x").searchParams.has("category")).toBe(false);
  });

  it("cadastrar categoria faz POST com JSON e sem X-API-Key", async () => {
    stubFetch(() => Promise.resolve(new Response('{"created": true}', { status: 200 })));
    await api.addCategory({ mlCategoryId: "MLB1618" });
    const [url, opts] = fetch.mock.calls[0];
    expect(url).toBe("/api/categories");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body)).toEqual({ ml_category_id: "MLB1618", enabled: true });
    expect(new Headers(opts.headers).has("X-API-Key")).toBe(false);
  });

  it("404 do cadastro traz o detail da API", async () => {
    stubFetch(() =>
      Promise.resolve(
        new Response(JSON.stringify({ detail: "Categoria MLB9 não existe no ML." }), { status: 404 })
      )
    );
    await expect(api.addCategory({ mlCategoryId: "MLB9" })).rejects.toThrow(/não existe no ML/);
  });

  it("busca codifica o texto", async () => {
    stubFetch(() => Promise.resolve(new Response("{}", { status: 200 })));
    await api.search({ q: "pote & tampa" });
    expect(new URL(fetch.mock.calls[0][0], "http://x").searchParams.get("q")).toBe("pote & tampa");
  });
});
