import React, { useEffect, useMemo, useState } from "react";
import { api } from "./api.js";
import { sortItems } from "./ranking.js";
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
  // Paginação por cursor (TIE-32): null = não há próxima página
  const [nextCursor, setNextCursor] = useState(null);

  const [selectedProductId, setSelectedProductId] = useState(null);
  // Ordenação por coluna (TIE-28): clicar na mesma coluna inverte a direção
  const [sort, setSort] = useState({ key: "final_score", dir: "desc" });
  const sorted = useMemo(() => sortItems(items, sort.key, sort.dir), [items, sort]);

  function onSort(key) {
    setSort((s) => (s.key === key ? { key, dir: s.dir === "desc" ? "asc" : "desc" } : { key, dir: "desc" }));
  }

  const params = useMemo(() => ({ hours, limit, source }), [hours, limit, source]);

  // append=false recomeça do topo (filtros mudaram ou "Atualizar");
  // append=true busca a próxima página e acrescenta
  async function load(append = false) {
    setLoading(true);
    setError("");
    try {
      const data = await api.rankingsLatest({ ...params, cursor: append ? nextCursor : null });
      setItems((prev) => (append ? [...prev, ...(data.items || [])] : data.items || []));
      setNextCursor(data.next_cursor || null);
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
        <button onClick={() => load()} disabled={loading} style={{ padding: "10px 14px", cursor: "pointer" }}>
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

      <section style={{ marginTop: 16 }}>
        <RankingTable
          items={sorted}
          loading={loading}
          error={error}
          sort={sort}
          onSort={onSort}
          onRetry={() => load()}
          onSelectProduct={(pid) => setSelectedProductId(pid)}
        />
        {nextCursor && !error && (
          <div style={{ textAlign: "center", marginTop: 12 }}>
            <button
              onClick={() => load(true)}
              disabled={loading}
              style={{ padding: "8px 14px", cursor: "pointer" }}
            >
              {loading ? "Carregando..." : `Carregar mais ${limit}`}
            </button>
          </div>
        )}
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