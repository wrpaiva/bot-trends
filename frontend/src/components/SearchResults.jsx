import React from "react";
import { nomeCategoria } from "../categorias.js";

// Resultado do GET /search (TIE-45): título, fonte, categoria e último score
export default function SearchResults({ resultado, carregando, erro, onSelectProduct, onLimpar, onPagina }) {
  if (!resultado && !carregando && !erro) return null;
  const itens = resultado?.items || [];
  const total = resultado?.total ?? 0;
  const pagina = resultado?.page ?? 1;
  const porPagina = resultado?.limit ?? 20;

  return (
    <div style={{ border: "1px solid #e5e5e5", borderRadius: 8, overflow: "hidden", marginBottom: 16 }}>
      <div style={{ padding: 12, borderBottom: "1px solid #eee", background: "#fafafa", display: "flex", justifyContent: "space-between" }}>
        <span>
          <b>Busca</b>{" "}
          {carregando ? (
            <span style={{ opacity: 0.6 }}>buscando...</span>
          ) : resultado ? (
            <span style={{ opacity: 0.6 }}>
              · {total} resultado{total === 1 ? "" : "s"} para “{resultado.q}”
            </span>
          ) : null}
        </span>
        <button onClick={onLimpar} style={{ cursor: "pointer" }}>
          Fechar busca
        </button>
      </div>

      {erro && <div style={{ padding: 24, textAlign: "center", background: "#ffecec", color: "#a61e1e" }}>{erro}</div>}
      {!erro && resultado && !itens.length && (
        <div style={{ padding: 24, textAlign: "center", opacity: 0.7 }}>Nenhum produto com esse título.</div>
      )}

      {itens.length > 0 && (
        <table style={{ width: "100%", borderCollapse: "collapse", tableLayout: "fixed" }}>
          <thead>
            <tr style={{ textAlign: "left" }}>
              <th style={{ padding: 10, width: 90 }}>Score</th>
              <th style={{ padding: 10 }}>Produto</th>
              <th style={{ padding: 10, width: 160 }}>Categoria</th>
              <th style={{ padding: 10, width: 110 }}>Fonte</th>
            </tr>
          </thead>
          <tbody>
            {itens.map((it) => (
              <tr
                key={it.product_id}
                onClick={() => onSelectProduct(it.product_id)}
                style={{ cursor: "pointer", borderTop: "1px solid #f1f1f1" }}
              >
                <td style={{ padding: 10 }}>
                  {it.latest ? <b>{Number(it.latest.final_score ?? 0).toFixed(1)}</b> : <span style={{ opacity: 0.6 }}>sem análise</span>}
                </td>
                <td style={{ padding: 10, overflowWrap: "anywhere" }}>
                  <div style={{ display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
                    {it.title || it.product_id}
                  </div>
                </td>
                <td style={{ padding: 10 }}>{nomeCategoria(it)}</td>
                <td style={{ padding: 10 }}>{it.source || "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {total > porPagina && (
        <div style={{ padding: 10, display: "flex", gap: 8, justifyContent: "center", alignItems: "center" }}>
          <button disabled={carregando || pagina <= 1} onClick={() => onPagina(pagina - 1)}>
            ‹ Anterior
          </button>
          <span style={{ fontSize: 12 }}>
            página {pagina} de {Math.ceil(total / porPagina)}
          </span>
          <button disabled={carregando || pagina * porPagina >= total} onClick={() => onPagina(pagina + 1)}>
            Próxima ›
          </button>
        </div>
      )}
    </div>
  );
}
