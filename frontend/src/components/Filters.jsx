import React from "react";

export default function Filters({ hours, setHours, source, setSource, limit, setLimit }) {
  return (
    <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
      <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
        Janela (hours):
        <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
          <option value={24}>24</option>
          <option value={48}>48</option>
          <option value={72}>72</option>
          <option value={168}>168</option>
        </select>
      </label>

      <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
        Fonte:
        <select value={source} onChange={(e) => setSource(e.target.value)}>
          <option value="all">all</option>
          <option value="mercadolivre">mercadolivre</option>
          <option value="tiktok">tiktok</option>
        </select>
      </label>

      <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
        Limite:
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