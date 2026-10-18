import React, { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import { sortItems, ultimaRequisicao } from "./ranking.js";
import Filters from "./components/Filters.jsx";
import RankingTable from "./components/RankingTable.jsx";
import ProductDrawer from "./components/ProductDrawer.jsx";
import CategoriesPanel from "./components/CategoriesPanel.jsx";
import SearchResults from "./components/SearchResults.jsx";

export default function App() {
  const [hours, setHours] = useState(72);
  const [source, setSource] = useState("all"); // all|mercadolivre|tiktok
  const [limit, setLimit] = useState(20);
  const [category, setCategory] = useState(""); // "" = todas (TIE-45)
  const [withOffer, setWithOffer] = useState(false); // só com onde comprar (TIE-53)
  const [aba, setAba] = useState("ranking"); // ranking|categorias

  // Categorias: alimentam o filtro e a aba de categorias
  const [categorias, setCategorias] = useState([]);
  const [catCarregando, setCatCarregando] = useState(false);
  const [catErro, setCatErro] = useState("");
  async function carregaCategorias() {
    setCatCarregando(true);
    setCatErro("");
    try {
      setCategorias((await api.categories()).items || []);
    } catch (e) {
      setCatErro(e.message || "Erro ao carregar categorias");
    } finally {
      setCatCarregando(false);
    }
  }
  useEffect(() => {
    carregaCategorias();
  }, []);

  // Busca por título (GET /search): só a mais nova grava, como no ranking
  const [busca, setBusca] = useState("");
  const [resultado, setResultado] = useState(null);
  const [buscando, setBuscando] = useState(false);
  const [buscaErro, setBuscaErro] = useState("");
  const buscas = useRef(null);
  if (buscas.current === null) buscas.current = ultimaRequisicao();
  async function buscar(page = 1) {
    const q = busca.trim();
    if (q.length < 2) {
      setBuscaErro("Digite ao menos 2 letras para buscar.");
      setResultado(null);
      return;
    }
    const req = buscas.current.inicia();
    setBuscando(true);
    setBuscaErro("");
    try {
      const data = await api.search({ q, page, signal: req.signal });
      if (req.valida()) setResultado(data);
    } catch (e) {
      if (req.valida()) setBuscaErro(e.message || "Erro na busca");
    } finally {
      if (req.valida()) setBuscando(false);
    }
  }
  function limparBusca() {
    buscas.current.cancela();
    setBusca("");
    setResultado(null);
    setBuscaErro("");
    setBuscando(false);
  }

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

  const params = useMemo(
    () => ({ hours, limit, source, category, withOffer }),
    [hours, limit, source, category, withOffer]
  );

  // Só a requisição mais nova grava na tela (ver ultimaRequisicao)
  const requisicoes = useRef(null);
  if (requisicoes.current === null) requisicoes.current = ultimaRequisicao();

  // append=false recomeça do topo (filtros mudaram ou "Atualizar");
  // append=true busca a próxima página e acrescenta
  async function load(append = false) {
    const req = requisicoes.current.inicia();
    setLoading(true);
    setError("");
    try {
      const data = await api.rankingsLatest({
        ...params,
        cursor: append ? nextCursor : null,
        signal: req.signal,
      });
      if (!req.valida()) return;
      setItems((prev) => (append ? [...prev, ...(data.items || [])] : data.items || []));
      setNextCursor(data.next_cursor || null);
    } catch (e) {
      if (!req.valida()) return; // abortada por uma mais nova: não é erro
      setError(e.message || "Erro ao carregar ranking");
    } finally {
      // Só a mais nova reabilita os botões
      if (req.valida()) setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hours, limit, source, category, withOffer]);

  useEffect(
    () => () => {
      requisicoes.current.cancela();
      buscas.current.cancela();
    },
    []
  );

  const abaBotao = (id, rotulo) => (
    <button
      onClick={() => setAba(id)}
      aria-pressed={aba === id}
      style={{
        padding: "8px 14px",
        cursor: "pointer",
        border: "1px solid #ddd",
        borderBottom: aba === id ? "2px solid #1c7ed6" : "1px solid #ddd",
        background: aba === id ? "#fff" : "#f8f9fa",
        fontWeight: aba === id ? 600 : 400,
      }}
    >
      {rotulo}
    </button>
  );

  return (
    <div style={{ fontFamily: "system-ui", padding: 16, maxWidth: 1200, margin: "0 auto" }}>
      <header style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12 }}>
        <div>
          <h1 style={{ margin: 0 }}>Trends Dashboard</h1>
          <div style={{ opacity: 0.7, marginTop: 4 }}>
            Ranking por janela (hours) com score híbrido (numérico + IA)
          </div>
        </div>
        {aba === "ranking" && (
          <button onClick={() => load()} disabled={loading} style={{ padding: "10px 14px", cursor: "pointer" }}>
            {loading ? "Atualizando..." : "Atualizar"}
          </button>
        )}
      </header>

      <nav style={{ marginTop: 16, display: "flex", gap: 4 }}>
        {abaBotao("ranking", "Ranking")}
        {abaBotao("categorias", "Categorias")}
      </nav>

      {aba === "categorias" && (
        <section style={{ marginTop: 16 }}>
          <CategoriesPanel
            categorias={categorias}
            carregando={catCarregando}
            erro={catErro}
            onChange={carregaCategorias}
          />
        </section>
      )}

      {aba === "ranking" && (
      <>
      <section style={{ marginTop: 16 }}>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            buscar(1);
          }}
          style={{ display: "flex", gap: 8, marginBottom: 12 }}
        >
          <input
            type="search"
            placeholder="Buscar produto pelo título (ex.: garrafa térmica)"
            value={busca}
            onChange={(e) => setBusca(e.target.value)}
            style={{ padding: 8, flex: 1, maxWidth: 480 }}
          />
          <button type="submit" disabled={buscando} style={{ padding: "8px 14px", cursor: "pointer" }}>
            Buscar
          </button>
        </form>
        <SearchResults
          resultado={resultado}
          carregando={buscando}
          erro={buscaErro}
          onSelectProduct={(pid) => setSelectedProductId(pid)}
          onLimpar={limparBusca}
          onPagina={(p) => buscar(p)}
        />
        <Filters
          hours={hours}
          setHours={setHours}
          source={source}
          setSource={setSource}
          limit={limit}
          setLimit={setLimit}
          category={category}
          setCategory={setCategory}
          categorias={categorias}
          withOffer={withOffer}
          setWithOffer={setWithOffer}
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
      </>
      )}

      <ProductDrawer
        open={!!selectedProductId}
        productId={selectedProductId}
        hours={hours}
        onClose={() => setSelectedProductId(null)}
      />
    </div>
  );
}