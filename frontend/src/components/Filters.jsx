import React from "react";
import { opcoesDoFiltro, rotuloCategoria } from "../categorias.js";

export default function Filters({
  hours,
  setHours,
  source,
  setSource,
  limit,
  setLimit,
  category,
  setCategory,
  categorias,
  withOffer,
  setWithOffer,
}) {
  return (
    <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
      <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
        {/* Recência: insights gerados neste período (TIE-28) */}
        Analisados nas últimas:
        <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
          <option value={24}>24 h</option>
          <option value={48}>48 h</option>
          <option value={72}>72 h</option>
          <option value={168}>7 dias</option>
          <option value={720}>30 dias</option>
        </select>
      </label>

      <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
        Fonte:
        <select value={source} onChange={(e) => setSource(e.target.value)}>
          <option value="all">Todas</option>
          <option value="mercadolivre">Mercado Livre</option>
          <option value="tiktok">TikTok</option>
        </select>
      </label>

      <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
        {/* A categoria é do ML: escolher uma tira o TikTok do ranking (TIE-45) */}
        Categoria:
        <select value={category} onChange={(e) => setCategory(e.target.value)} style={{ maxWidth: 260 }}>
          <option value="">Todas</option>
          {opcoesDoFiltro(categorias).map((c) => (
            <option key={c.ml_category_id} value={c.ml_category_id}>
              {rotuloCategoria(c)}
              {c.enabled ? "" : " (não coletada)"}
            </option>
          ))}
        </select>
      </label>

      <label
        style={{ display: "flex", gap: 6, alignItems: "center" }}
        title="Vídeos do TikTok ligados a um produto do Mercado Livre"
      >
        {/* TIE-53: os vinculados ficam espalhados entre centenas de vídeos */}
        <input type="checkbox" checked={withOffer} onChange={(e) => setWithOffer(e.target.checked)} />
        Só com onde comprar 🛒
      </label>

      <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
        Por página:
        <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
          <option value={10}>10</option>
          <option value={20}>20</option>
          <option value={50}>50</option>
          <option value={100}>100</option>
        </select>
      </label>
    </div>
  );
}