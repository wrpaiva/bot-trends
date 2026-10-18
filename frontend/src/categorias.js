// src/categorias.js — lógica pura da tela de categorias e do filtro (TIE-45),
// testada em categorias.test.js

// Mesmo formato que a API aceita (`^[A-Z]{3}\d{1,12}$`). Aceita "mlb 1618".
export function normalizaIdCategoria(texto) {
  const id = String(texto || "").toUpperCase().replace(/\s+/g, "");
  return /^[A-Z]{3}\d{1,12}$/.test(id) ? id : null;
}

// Nome para a tabela: o cadastrado, senão o ID, senão "-" (vídeo do TikTok)
export function nomeCategoria(item) {
  return item?.category_name || item?.category || "-";
}

// "Casa, Móveis e Decoração › Cozinha" quando a subcategoria tem caminho
export function rotuloCategoria(cat) {
  const caminho = (cat?.path || []).filter(Boolean);
  if (caminho.length > 1) return caminho.join(" › ");
  return cat?.name || cat?.ml_category_id || "";
}

// Opções do filtro do ranking: habilitadas primeiro (são as coletadas), por nome
export function opcoesDoFiltro(categorias) {
  return [...(categorias || [])]
    .filter((c) => c?.ml_category_id)
    .sort((a, b) => {
      if (!!a.enabled !== !!b.enabled) return a.enabled ? -1 : 1;
      return rotuloCategoria(a).localeCompare(rotuloCategoria(b), "pt-BR");
    });
}

// Filtro de texto da lista de categorias: ignora acento e caixa
const semAcento = (s) =>
  String(s || "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();

export function filtraCategorias(categorias, texto) {
  const t = semAcento(texto).trim();
  if (!t) return categorias || [];
  return (categorias || []).filter(
    (c) => semAcento(rotuloCategoria(c)).includes(t) || semAcento(c.ml_category_id).includes(t)
  );
}
