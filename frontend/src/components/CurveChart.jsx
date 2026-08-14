import React, { useMemo, useState } from "react";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  TimeScale,
  Tooltip,
  Legend
} from "chart.js";
import { Line } from "react-chartjs-2";

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend);

function toLabel(ts) {
  try {
    const d = new Date(ts);
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  } catch {
    return String(ts);
  }
}

export default function CurveChart({ points }) {
  const [metric, setMetric] = useState("views"); // views|engagement|mentions|price

  const { labels, values } = useMemo(() => {
    const lbls = [];
    const vals = [];
    (points || []).forEach((p) => {
      lbls.push(toLabel(p.ts));
      vals.push(Number(p[metric] ?? 0));
    });
    return { labels: lbls, values: vals };
  }, [points, metric]);

  const data = useMemo(() => ({
    labels,
    datasets: [
      {
        label: metric,
        data: values,
        tension: 0.25
      }
    ]
  }), [labels, values, metric]);

  const options = useMemo(() => ({
    responsive: true,
    plugins: {
      legend: { display: true },
      tooltip: { enabled: true }
    },
    scales: {
      y: { beginAtZero: true }
    }
  }), []);

  return (
    <div style={{ border: "1px solid #eee", borderRadius: 8, padding: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}>
        <b>Curva</b>
        <select value={metric} onChange={(e) => setMetric(e.target.value)}>
          <option value="views">views</option>
          <option value="engagement">engagement</option>
          <option value="mentions">mentions</option>
          <option value="price">price</option>
        </select>
      </div>
      <Line data={data} options={options} />
    </div>
  );
}