
const API_BASE =
  import.meta.env.VITE_API_BASE || "http://localhost:8000";

async function httpGet(path) {
  const r = await fetch(`${API_BASE}${path}`);
  if (!r.ok) {
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