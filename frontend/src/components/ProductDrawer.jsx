import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import CurveChart from "./CurveChart.jsx";

export default function ProductDrawer({ open, productId, hours, onClose }) {
  const [loading, setLoading] = useState(false);
  const [curve, setCurve] = useState(null);
  const [insight, setInsight] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open || !productId) return;

    async function load() {
      setLoading(true);
      setError("");
      try {
        const [c, i] = await Promise.all([
          api.productCurve({ productId, hours }),
          api.productInsightLatest({ productId }),
        ]);
        setCurve(c);
        setInsight(i);
      } catch (e) {
        setError(e.message || "Erro ao carregar detalhe");
      } finally {
        setLoading(false);
      }
    }

    load();
  }, [open, productId, hours]);

  if (!open) return null;

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
          <div>
            <h2 style={{ margin: "0 0 6px 0" }}>{curve?.product?.title || productId}</h2>
            <div style={{ opacity: 0.7 }}>{curve?.product?.category || "-"}</div>
          </div>
          <button onClick={onClose} style={{ padding: "10px 14px", cursor: "pointer" }}>
            Fechar
          </button>
        </div>

        {loading && <div style={{ marginTop: 12, opacity: 0.7 }}>Carregando...</div>}
        {error && (
          <div style={{ marginTop: 12, padding: 12, background: "#ffecec", border: "1px solid #ffb3b3" }}>
            {error}
          </div>
        )}

        {insight && (
          <div style={{ marginTop: 16, padding: 12, border: "1px solid #eee", borderRadius: 8 }}>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              <b>Final:</b> {Number(insight.final_score ?? 0).toFixed(1)}
              <span style={{ opacity: 0.6 }}>|</span>
              <b>Status:</b> {insight.trend_classification}
              <span style={{ opacity: 0.6 }}>|</span>
              <b>Risco:</b> {insight.risk_level}
              <span style={{ opacity: 0.6 }}>|</span>
              <b>Janela:</b> {insight.window_hours}h
            </div>
            <div style={{ marginTop: 10 }}>
              <b>Análise:</b>
              <div style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>{insight.analysis}</div>
            </div>
            <div style={{ marginTop: 10 }}>
              <b>Recomendação:</b>
              <div style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>{insight.recommendation}</div>
            </div>
          </div>
        )}

        {curve?.points && (
          <div style={{ marginTop: 16 }}>
            <CurveChart points={curve.points} />
          </div>
        )}
      </div>
    </div>
  );
}