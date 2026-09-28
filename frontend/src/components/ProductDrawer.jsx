import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { scoreBreakdown } from "../drawer.js";
import CurveChart from "./CurveChart.jsx";

const caixa = { marginTop: 16, padding: 12, border: "1px solid #eee", borderRadius: 8 };

function Breakdown({ insight }) {
  const b = scoreBreakdown(insight);
  if (!b) return null;
  return (
    <div style={caixa}>
      <div style={{ display: "flex", gap: 12 }}>
        {b.partes.map((p) => (
          <div
            key={p.label}
            style={{
              flex: 1,
              padding: 10,
              borderRadius: 8,
              background: p.label === "Final" ? "#f1f8f3" : "#fafafa",
              textAlign: "center",
            }}
          >
            <div style={{ fontSize: 12, opacity: 0.7 }}>
              {p.label}
              {p.peso != null ? ` · peso ${p.peso}` : ""}
            </div>
            <div style={{ fontSize: 24, fontWeight: 700 }}>
              {p.value == null ? "—" : Number(p.value).toFixed(1)}
            </div>
          </div>
        ))}
      </div>
      {b.llmIndisponivel && (
        <div style={{ marginTop: 10, fontSize: 13, color: "#a15c00" }}>
          ⚠ IA indisponível nesta análise — score só numérico. Motivo: {b.motivo}
        </div>
      )}
      <div style={{ marginTop: 10, display: "flex", gap: 12, flexWrap: "wrap", fontSize: 14 }}>
        <span>
          <b>Status:</b> {insight.trend_classification || "-"}
        </span>
        <span>
          <b>Risco:</b> {insight.risk_level || "-"}
        </span>
        <span>
          <b>Janela:</b> {insight.window_hours ?? "-"}h
        </span>
        {b.normalizacao && (
          <span style={{ opacity: 0.7 }}>
            <b>Normalização:</b> {b.normalizacao}
          </span>
        )}
      </div>
    </div>
  );
}

export default function ProductDrawer({ open, productId, hours, onClose }) {
  const [curve, setCurve] = useState(null);
  const [insight, setInsight] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open || !productId) return undefined;
    // Resposta de um produto anterior não pode sobrescrever a do atual
    let cancelado = false;

    setCurve(null);
    setInsight(null);
    setError("");
    setLoading(true);

    // Independentes: produto sem análise (404 → null) ainda mostra a curva
    Promise.allSettled([
      api.productCurve({ productId, hours }),
      api.productInsightLatest({ productId }),
    ]).then(([c, i]) => {
      if (cancelado) return;
      if (c.status === "fulfilled") setCurve(c.value);
      if (i.status === "fulfilled") setInsight(i.value);
      const falhas = [c, i].filter((r) => r.status === "rejected").map((r) => r.reason?.message);
      setError(falhas.join(" · "));
      setLoading(false);
    });

    return () => {
      cancelado = true;
    };
  }, [open, productId, hours]);

  useEffect(() => {
    if (!open) return undefined;
    const esc = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [open, onClose]);

  if (!open) return null;

  const titulo = curve?.product?.title || productId;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.3)",
        display: "flex",
        justifyContent: "flex-end",
      }}
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-label={`Detalhe de ${titulo}`}
        style={{
          width: "min(720px, 92vw)",
          height: "100%",
          background: "#fff",
          padding: 16,
          overflow: "auto",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
          <div style={{ minWidth: 0 }}>
            <h2 style={{ margin: "0 0 6px 0", fontSize: 18, overflowWrap: "anywhere" }}>{titulo}</h2>
            <div style={{ opacity: 0.7, fontSize: 14 }}>
              {curve?.product?.category || "sem categoria"}
              {curve?.product?.permalink && (
                <>
                  {" · "}
                  <a href={curve.product.permalink} target="_blank" rel="noreferrer">
                    abrir na origem
                  </a>
                </>
              )}
            </div>
          </div>
          <button onClick={onClose} style={{ padding: "10px 14px", cursor: "pointer", alignSelf: "start" }}>
            Fechar
          </button>
        </div>

        {loading && <div style={{ marginTop: 12, opacity: 0.7 }}>Carregando...</div>}
        {error && (
          <div style={{ marginTop: 12, padding: 12, background: "#ffecec", border: "1px solid #ffb3b3" }}>
            {error}
          </div>
        )}

        {!loading && insight === null && !error && (
          <div style={{ ...caixa, opacity: 0.8 }}>
            Este produto ainda não foi analisado. A análise roda a cada 30 minutos.
          </div>
        )}

        {insight && <Breakdown insight={insight} />}

        {insight && (
          <div style={caixa}>
            <b>Análise</b>
            <div style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>{insight.analysis || "—"}</div>
            <div style={{ marginTop: 12 }}>
              <b>Recomendação</b>
              <div style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>{insight.recommendation || "—"}</div>
            </div>
          </div>
        )}

        {curve && (
          <div style={{ marginTop: 16 }}>
            <CurveChart points={curve.points} hours={hours} />
          </div>
        )}
      </div>
    </div>
  );
}
