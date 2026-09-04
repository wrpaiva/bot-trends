
const API_BASE =
  import.meta.env.VITE_API_BASE || "http://localhost:8000";

// Todas as rotas do router exigem X-API-Key (require_api_key no include_router).
// A chave é inlinada no bundle pelo Vite em tempo de build — ou seja, é pública
// para quem abrir o DevTools. Aceitável enquanto o dashboard for de uso pessoal;
// a autenticação de verdade está na TIE-27.
const API_KEY = import.meta.env.VITE_API_KEY;

async function httpGet(path) {
  const headers = {};
  if (API_KEY) {
    headers["X-API-Key"] = API_KEY;
  }

  const r = await fetch(`${API_BASE}${path}`, { headers });
  if (!r.ok) {
    if (r.status === 401) {
      throw new Error(
        `401 em ${path}: X-API-Key ausente ou incorreta. ` +
          `Confira API_KEY no .env da raiz e refaça o build do frontend.`
      );
    }
    const text = await r.text().catch(() => "");
    throw new Error(`HTTP ${r.status} ${path} ${text}`);
  }
  return r.json();
}

export const api = {
  rankingsLatest: ({ hours, limit, source }) =>
    httpGet(`/rankings/latest?hours=${hours}&limit=${limit}&source=${source}`),

  productCurve: ({ productId, hours }) =>
    httpGet(`/products/${encodeURIComponent(productId)}/curve?hours=${hours}`),

  productInsightLatest: ({ productId }) =>
    httpGet(`/products/${encodeURIComponent(productId)}/insight/latest`),
};
