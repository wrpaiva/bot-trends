
const API_BASE =
  import.meta.env.VITE_API_BASE || "http://localhost:8000";

// Todas as rotas do router exigem X-API-Key (require_api_key no include_router).
// A chave é inlinada no bundle pelo Vite em tempo de build — ou seja, é pública
// para quem abrir o DevTools. Aceitável enquanto o dashboard for de uso pessoal;
// a autenticação de verdade está na TIE-27.
const API_KEY = import.meta.env.VITE_API_KEY;

// Mensagem que diz o que fazer, não só o status (TIE-28): o dashboard nunca
// pode virar tela branca ou "HTTP 500" sem contexto.
async function erroHttp(r, path) {
  if (r.status === 401) {
    return new Error(
      `401 em ${path}: X-API-Key ausente ou incorreta. ` +
        `Confira API_KEY no .env da raiz e refaça o build do frontend.`
    );
  }
  if (r.status === 429) {
    return new Error("Muitas requisições em pouco tempo (rate limit). Espere um minuto e tente de novo.");
  }
  let detalhe = "";
  try {
    const corpo = await r.json();
    detalhe = typeof corpo?.detail === "string" ? corpo.detail : "";
  } catch {
    // corpo não-JSON: fica só o status
  }
  return new Error(`HTTP ${r.status} em ${path}${detalhe ? `: ${detalhe}` : ""}`);
}

async function httpGet(path) {
  const headers = {};
  if (API_KEY) {
    headers["X-API-Key"] = API_KEY;
  }

  let r;
  try {
    r = await fetch(`${API_BASE}${path}`, { headers });
  } catch {
    // fetch só rejeita em falha de rede ou CORS bloqueado — o browser não diz qual
    throw new Error(
      `Não foi possível falar com a API em ${API_BASE}. Ela está no ar? ` +
        `Se estiver, confira se a origem deste dashboard está em CORS_ORIGINS.`
    );
  }
  if (!r.ok) {
    const erro = await erroHttp(r, path);
    erro.status = r.status; // quem chama decide o que é "esperado" (ex.: 404)
    throw erro;
  }
  return r.json();
}

export const api = {
  // cursor: `next_cursor` da página anterior (TIE-32); omitido na primeira página
  rankingsLatest: ({ hours, limit, source, cursor }) => {
    const q = new URLSearchParams({ hours, limit, source });
    if (cursor) q.set("cursor", cursor);
    return httpGet(`/rankings/latest?${q}`);
  },

  productCurve: ({ productId, hours }) =>
    httpGet(`/products/${encodeURIComponent(productId)}/curve?hours=${hours}`),

  // 404 = produto coletado mas ainda não analisado: não é erro, é "sem análise" (TIE-29)
  productInsightLatest: async ({ productId }) => {
    try {
      return await httpGet(`/products/${encodeURIComponent(productId)}/insight/latest`);
    } catch (e) {
      if (e.status === 404) return null;
      throw e;
    }
  },
};
