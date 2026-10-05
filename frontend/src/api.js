// A API é chamada na MESMA origem, em /api (TIE-27). Quem fala com a API de
// verdade é o servidor — o nginx do container `web` em produção, o proxy do
// Vite em desenvolvimento — e é ele que põe a X-API-Key. A chave nunca chega
// ao browser nem ao bundle; e, sendo a mesma origem, não há CORS no caminho.
const API_BASE = "/api";

// Mensagem que diz o que fazer, não só o status (TIE-28): o dashboard nunca
// pode virar tela branca ou "HTTP 500" sem contexto.
async function erroHttp(r, path) {
  if (r.status === 401) {
    return new Error(
      `401 em ${path}: o proxy enviou uma X-API-Key que a API não aceitou. ` +
        `Confira API_KEY no .env da raiz e recrie o container web (docker compose up -d web).`
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
  let r;
  try {
    r = await fetch(`${API_BASE}${path}`);
  } catch {
    // Mesma origem: aqui só cai falha de rede até o próprio servidor do dashboard
    throw new Error(
      `Não foi possível falar com a API em ${API_BASE}. O dashboard está no ar? ` +
        `Se estiver, a API pode estar fora (o proxy devolve 502).`
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
