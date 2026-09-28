import React from "react";
import { viewState } from "../ranking.js";

// Cores por gravidade: o que precisa de atenção salta aos olhos (TIE-28)
const CLASS_COLOR = {
  VIRALIZANDO: "#d9480f",
  SUBINDO: "#2b8a3e",
  PICO_TEMPORARIO: "#e67700",
  ESTAVEL: "#495057",
  EM_QUEDA: "#868e96",
};
const RISK_COLOR = { ALTO: "#c92a2a", MEDIO: "#e67700", BAIXO: "#2b8a3e" };

function badge(text, color) {
  return (
    <span
      style={{
        padding: "2px 8px",
        borderRadius: 999,
        fontSize: 12,
        whiteSpace: "nowrap",
        border: `1px solid ${color || "#ddd"}`,
        color: color || "inherit",
      }}
    >
      {text}
    </span>
  );
}

const COLUNAS = [
  { key: "final_score", label: "Score", width: 110 },
  { key: "trend_classification", label: "Status", width: 150 },
  { key: "risk_level", label: "Risco", width: 90 },
  { key: "title", label: "Produto" },
  { key: "category", label: "Categoria", width: 120, sortable: false },
  { key: "sources", label: "Fontes", width: 110, sortable: false },
];

const cel = { padding: 10, borderBottom: "1px solid #f1f1f1", verticalAlign: "top" };

function Aviso({ children, tom = "neutro" }) {
  const cores = {
    neutro: { background: "#fafafa", color: "#555" },
    erro: { background: "#ffecec", color: "#a61e1e" },
  };
  return <div style={{ padding: 24, textAlign: "center", ...cores[tom] }}>{children}</div>;
}

export default function RankingTable({ items, loading, error, sort, onSort, onSelectProduct, onRetry }) {
  const estado = viewState({ loading, error, items });

  return (
    <div style={{ border: "1px solid #e5e5e5", borderRadius: 8, overflow: "hidden" }}>
      <div style={{ padding: 12, borderBottom: "1px solid #eee", background: "#fafafa" }}>
        <b>Ranking</b>{" "}
        {loading ? <span style={{ opacity: 0.6 }}>carregando...</span> : null}
        {estado === "ok" ? <span style={{ opacity: 0.6 }}>· {items.length} produtos</span> : null}
      </div>

      {estado === "error" && (
        <Aviso tom="erro">
          <div style={{ marginBottom: 12 }}>{error}</div>
          {onRetry && (
            <button onClick={onRetry} style={{ padding: "6px 12px", cursor: "pointer" }}>
              Tentar de novo
            </button>
          )}
        </Aviso>
      )}

      {estado === "loading" && <Aviso>Carregando ranking…</Aviso>}

      {estado === "empty" && (
        <Aviso>
          Nenhum produto analisado neste período e fonte. Aumente o período, troque a fonte, ou
          confira se a coleta e a análise estão rodando (<code>docker compose logs worker</code>).
        </Aviso>
      )}

      {estado === "ok" && (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", tableLayout: "fixed" }}>
            <thead>
              <tr style={{ textAlign: "left", background: "#fff" }}>
                {COLUNAS.map((c) => {
                  const ativa = sort.key === c.key;
                  const ordenavel = c.sortable !== false;
                  return (
                    <th
                      key={c.key}
                      onClick={ordenavel ? () => onSort(c.key) : undefined}
                      style={{
                        padding: 10,
                        borderBottom: "1px solid #eee",
                        width: c.width,
                        cursor: ordenavel ? "pointer" : "default",
                        userSelect: "none",
                        whiteSpace: "nowrap",
                      }}
                      title={ordenavel ? "Ordenar" : undefined}
                    >
                      {c.label} {ativa ? (sort.dir === "desc" ? "▼" : "▲") : ""}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr
                  key={it.product_id}
                  style={{ cursor: "pointer" }}
                  onClick={() => onSelectProduct(it.product_id)}
                >
                  <td style={cel}>
                    <b>{Number(it.final_score ?? 0).toFixed(1)}</b>
                    <div style={{ fontSize: 12, opacity: 0.7 }}>
                      num {Number(it.numeric_score ?? 0).toFixed(1)} / ia{" "}
                      {Number(it.llm_score ?? 0).toFixed(1)}
                    </div>
                  </td>
                  <td style={cel}>
                    {badge(it.trend_classification || "-", CLASS_COLOR[it.trend_classification])}
                  </td>
                  <td style={cel}>{badge(it.risk_level || "-", RISK_COLOR[it.risk_level])}</td>
                  <td style={{ ...cel, overflowWrap: "anywhere" }}>
                    {/* Legendas do TikTok são longas: corta em 3 linhas */}
                    <div
                      style={{
                        display: "-webkit-box",
                        WebkitLineClamp: 3,
                        WebkitBoxOrient: "vertical",
                        overflow: "hidden",
                      }}
                      title={it.title || it.product_id}
                    >
                      {it.title || it.product_id}
                    </div>
                  </td>
                  <td style={cel}>{it.category || "-"}</td>
                  <td style={cel}>{(it.sources || []).join(", ") || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div style={{ padding: 10, fontSize: 12, opacity: 0.7, background: "#fafafa" }}>
        Clique em um produto para ver o detalhe. A ordenação por coluna vale para os itens já
        carregados.
      </div>
    </div>
  );
}
