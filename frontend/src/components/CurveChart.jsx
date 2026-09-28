import React, { useMemo, useState } from "react";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Tooltip,
  Legend,
} from "chart.js";
import { Line } from "react-chartjs-2";
import { buildSeries, historyState } from "../drawer.js";

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend);

const METRICAS = {
  views: "Views",
  engagement: "Engajamento",
  mentions: "Menções",
  price: "Preço",
};

export default function CurveChart({ points, hours }) {
  const [metric, setMetric] = useState("views");

  const { labels, values } = useMemo(() => buildSeries(points, metric), [points, metric]);
  const estado = historyState(points, metric);

  const data = useMemo(
    () => ({
      labels,
      datasets: [{ label: METRICAS[metric], data: values, tension: 0.25, spanGaps: false }],
    }),
    [labels, values, metric]
  );

  const options = useMemo(
    () => ({
      responsive: true,
      plugins: { legend: { display: false }, tooltip: { enabled: true } },
      scales: { y: { beginAtZero: metric !== "price" } },
    }),
    [metric]
  );

  return (
    <div style={{ border: "1px solid #eee", borderRadius: 8, padding: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}>
        <b>Curva</b>
        <span style={{ opacity: 0.6, fontSize: 13 }}>
          últimas {hours}h · {(points || []).length} leituras
        </span>
        <select value={metric} onChange={(e) => setMetric(e.target.value)} style={{ marginLeft: "auto" }}>
          {Object.entries(METRICAS).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
      </div>
      {estado === "vazio" ? (
        <div style={{ padding: 24, textAlign: "center", opacity: 0.7 }}>
          Histórico insuficiente para desenhar a curva de {METRICAS[metric].toLowerCase()} — são
          necessárias ao menos 2 leituras com esse dado no período. Aumente o período ou espere as
          próximas coletas.
        </div>
      ) : (
        <Line data={data} options={options} />
      )}
    </div>
  );
}
