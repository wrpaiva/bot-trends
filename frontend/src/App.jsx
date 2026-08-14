import React, { useEffect, useMemo, useState } from "react";
import { api } from "./api.js";
import Filters from "./components/Filters.jsx";
import RankingTable from "./components/RankingTable.jsx";
import ProductDrawer from "./components/ProductDrawer.jsx";

export default function App() {
  const [hours, setHours] = useState(72);
  const [source, setSource] = useState("all"); // all|mercadolivre|tiktok
  const [limit, setLimit] = useState(20);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [items, setItems] = useState([]);

  const [selectedProductId, setSelectedProductId] = useState(null);

  const params = useMemo(() => ({ hours, limit, source }), [hours, limit, source]);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const data = await api.rankingsLatest(params);
      setItems(data.items || []);
    } catch (e) {
      setError(e.message || "Erro ao carregar ranking");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hours, limit, source]);

  return (
    <div style={{ fontFamily: "system-ui", padding: 16, maxWidth: 1200, margin: "0 auto" }}>
      <header style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12 }}>
        <div>
          <h1 style={{ margin: 0 }}>Trends Dashboard</h1>
          <div style={{ opacity: 0.7, marginTop: 4 }}>
            Ranking por janela (hours) com score híbrido (numérico + IA)
          </div>
        </div>
        <button onClick={load} disabled={loading} style={{ padding: "10px 14px", cursor: "pointer" }}>
          {loading ? "Atualizando..." : "Atualizar"}
        </button>
      </header>

      <section style={{ marginTop: 16 }}>
        <Filters
          hours={hours}
          setHours={setHours}
          source={source}
          setSource={setSource}
          limit={limit}
          setLimit={setLimit}
        />
      </section>

      {error && (
        <div style={{ marginTop: 16, padding: 12, background: "#ffecec", border: "1px solid #ffb3b3" }}>
          {error}
        </div>
      )}

      <section style={{ marginTop: 16 }}>
        <RankingTable
          items={items}
          loading={loading}
          onSelectProduct={(pid) => setSelectedProductId(pid)}
        />
      </section>

      <ProductDrawer
        open={!!selectedProductId}
        productId={selectedProductId}
        hours={hours}
        onClose={() => setSelectedProductId(null)}
      />
    </div>
  );
}