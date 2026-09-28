// src/ranking.js — lógica pura da tela de ranking (TIE-28), testada em ranking.test.js

// Ordem de gravidade: "desc" põe o mais forte/arriscado primeiro
export const CLASS_ORDER = { EM_QUEDA: 0, ESTAVEL: 1, PICO_TEMPORARIO: 2, SUBINDO: 3, VIRALIZANDO: 4 };
export const RISK_ORDER = { BAIXO: 0, MEDIO: 1, ALTO: 2 };

function valor(item, key) {
  if (key === "trend_classification") return CLASS_ORDER[item[key]];
  if (key === "risk_level") return RISK_ORDER[item[key]];
  if (key === "title") return (item.title || item.product_id || "").toLowerCase();
  return item[key];
}

// Ordena sem mutar; nulos/desconhecidos sempre no fim, em qualquer direção
export function sortItems(items, key, dir = "desc") {
  const sinal = dir === "asc" ? 1 : -1;
  return [...(items || [])].sort((a, b) => {
    const va = valor(a, key);
    const vb = valor(b, key);
    const na = va === null || va === undefined;
    const nb = vb === null || vb === undefined;
    if (na || nb) return na === nb ? 0 : na ? 1 : -1;
    if (va < vb) return -1 * sinal;
    if (va > vb) return 1 * sinal;
    return 0;
  });
}

// Qual tela mostrar. Carregando a próxima página mantém a tabela visível.
export function viewState({ loading, error, items }) {
  if (error) return "error";
  if (items && items.length) return "ok";
  return loading ? "loading" : "empty";
}
