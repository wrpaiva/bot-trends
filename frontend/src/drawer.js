// src/drawer.js — lógica pura do drawer de produto (TIE-29), testada em drawer.test.js

const pad = (n) => String(n).padStart(2, "0");

// "27/09 14:05" no fuso do browser. Só hora não basta: numa janela de 72 h
// o mesmo horário aparece em dias diferentes.
export function formatTs(ts) {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const valorDe = (p, metric) => {
  const v = p?.[metric];
  return v === null || v === undefined ? null : Number(v);
};

// Métrica ausente vira null (lacuna no gráfico), nunca 0: o TikTok não tem
// preço, e zero desenharia uma queda que não existiu.
export function buildSeries(points, metric) {
  const lista = points || [];
  return { labels: lista.map((p) => formatTs(p.ts)), values: lista.map((p) => valorDe(p, metric)) };
}

// Curva precisa de 2+ pontos com valor na métrica (se informada)
export function historyState(points, metric = null) {
  if (!points || points.length < 2) return "vazio";
  if (metric && points.every((p) => valorDe(p, metric) === null)) return "vazio";
  return "ok";
}

const PESOS_PADRAO = { numeric: 0.6, llm: 0.4 };

// Numérico, IA e final lado a lado, com os pesos gravados no insight (TIE-22)
export function scoreBreakdown(insight) {
  if (!insight) return null;
  const pesos = insight.score_weights?.hybrid || PESOS_PADRAO;
  const motivo = insight.debug?.llm_error || null;
  return {
    partes: [
      { label: "Numérico", value: insight.numeric_score, peso: pesos.numeric },
      { label: "IA", value: insight.llm_score, peso: pesos.llm },
      { label: "Final", value: insight.final_score, peso: null },
    ],
    llmIndisponivel: Boolean(motivo),
    motivo,
    normalizacao: insight.debug?.numeric_components?.normalization || null,
  };
}
