import React from "react";

function badge(text) {
  const base = { padding: "2px 8px", borderRadius: 999, fontSize: 12, border: "1px solid #ddd" };
  return <span style={base}>{text}</span>;
}

export default function RankingTable({ items, loading, onSelectProduct }) {
  return (
    <div style={{ border: "1px solid #e5e5e5", borderRadius: 8, overflow: "hidden" }}>
      <div style={{ padding: 12, borderBottom: "1px solid #eee", background: "#fafafa" }}>
        <b>Ranking</b> {loading ? <span style={{ opacity: 0.6 }}>carregando...</span> : null}
      </div>

      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ textAlign: "left", background: "#fff" }}>
              <th style={{ padding: 10, borderBottom: "1px solid #eee" }}>Score</th>
              <th style={{ padding: 10, borderBottom: "1px solid #eee" }}>Status</th>
              <th style={{ padding: 10, borderBottom: "1px solid #eee" }}>Risco</th>
              <th style={{ padding: 10, borderBottom: "1px solid #eee" }}>Produto</th>
              <th style={{ padding: 10, borderBottom: "1px solid #eee" }}>Categoria</th>
              <th style={{ padding: 10, borderBottom: "1px solid #eee" }}>Sources</th>
            </tr>
          </thead>
          <tbody>
            {(items || []).map((it) => (
              <tr
                key={it.product_id}
                style={{ cursor: "pointer" }}
                onClick={() => onSelectProduct(it.product_id)}
              >
                <td style={{ padding: 10, borderBottom: "1px solid #f1f1f1" }}>
                  <b>{Number(it.final_score ?? 0).toFixed(1)}</b>
                  <div style={{ fontSize: 12, opacity: 0.7 }}>
                    num {Number(it.numeric_score ?? 0).toFixed(1)} / ia {Number(it.llm_score ?? 0).toFixed(1)}
                  </div>
                </td>
                <td style={{ padding: 10, borderBottom: "1px solid #f1f1f1" }}>
                  {badge(it.trend_classification || "-")}
                </td>
                <td style={{ padding: 10, borderBottom: "1px solid #f1f1f1" }}>
                  {badge(it.risk_level || "-")}
                </td>
                <td style={{ padding: 10, borderBottom: "1px solid #f1f1f1" }}>
                  {it.title || it.product_id}
                </td>
                <td style={{ padding: 10, borderBottom: "1px solid #f1f1f1" }}>
                  {it.category || "-"}
                </td>
                <td style={{ padding: 10, borderBottom: "1px solid #f1f1f1" }}>
                  {(it.sources || []).join(", ") || "-"}
                </td>
              </tr>
            ))}
            {(!items || items.length === 0) && !loading && (
              <tr>
                <td colSpan={6} style={{ padding: 12, opacity: 0.7 }}>
                  Sem dados ainda. Rode bootstrap/collect/trend.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div style={{ padding: 10, fontSize: 12, opacity: 0.7, background: "#fafafa" }}>
        Clique em um item para ver detalhe.
      </div>
    </div>
  );
}