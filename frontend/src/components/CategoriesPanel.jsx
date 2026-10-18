import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { filtraCategorias, normalizaIdCategoria, opcoesDoFiltro, rotuloCategoria } from "../categorias.js";

// Tela de categorias (TIE-45): habilitar/desabilitar e cadastrar subcategoria
// pelo ID do ML sem curl nem Mongo. `onChange` avisa o App para recarregar o filtro.
export default function CategoriesPanel({ categorias, carregando, erro, onChange }) {
  const [texto, setTexto] = useState("");
  const [novoId, setNovoId] = useState("");
  const [ocupado, setOcupado] = useState(null); // id em gravação, ou "novo"
  const [mensagem, setMensagem] = useState(null); // { tom, texto }

  const lista = useMemo(() => filtraCategorias(opcoesDoFiltro(categorias), texto), [categorias, texto]);
  const ativas = (categorias || []).filter((c) => c.enabled).length;

  async function alterna(cat) {
    setOcupado(cat.ml_category_id);
    setMensagem(null);
    try {
      if (cat.enabled) await api.disableCategories([cat.ml_category_id]);
      else await api.enableCategories([cat.ml_category_id]);
      await onChange();
    } catch (e) {
      setMensagem({ tom: "erro", texto: e.message });
    } finally {
      setOcupado(null);
    }
  }

  async function adiciona(ev) {
    ev.preventDefault();
    const id = normalizaIdCategoria(novoId);
    if (!id) {
      setMensagem({ tom: "erro", texto: "ID do ML no formato MLB1618 (letras do site + número)." });
      return;
    }
    setOcupado("novo");
    setMensagem(null);
    try {
      const r = await api.addCategory({ mlCategoryId: id });
      setMensagem({
        tom: "ok",
        texto: `${rotuloCategoria(r.category)} ${r.created ? "cadastrada" : "atualizada"} e habilitada. Entra na próxima coleta do ML.`,
      });
      setNovoId("");
      await onChange();
    } catch (e) {
      setMensagem({ tom: "erro", texto: e.message });
    } finally {
      setOcupado(null);
    }
  }

  return (
    <div style={{ border: "1px solid #e5e5e5", borderRadius: 8, overflow: "hidden" }}>
      <div style={{ padding: 12, borderBottom: "1px solid #eee", background: "#fafafa" }}>
        <b>Categorias do Mercado Livre</b>{" "}
        <span style={{ opacity: 0.6 }}>
          · {ativas} habilitada{ativas === 1 ? "" : "s"} de {(categorias || []).length}
        </span>
        <div style={{ fontSize: 12, opacity: 0.7, marginTop: 4 }}>
          Só as habilitadas são coletadas. Cada uma custa requisições ao ML a cada ciclo.
        </div>
      </div>

      <div style={{ padding: 12, display: "flex", gap: 12, flexWrap: "wrap", borderBottom: "1px solid #eee" }}>
        <input
          placeholder="Filtrar por nome ou ID"
          value={texto}
          onChange={(e) => setTexto(e.target.value)}
          style={{ padding: 6, minWidth: 220 }}
        />
        <form onSubmit={adiciona} style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input
            placeholder="ID do ML (ex.: MLB1618)"
            value={novoId}
            onChange={(e) => setNovoId(e.target.value)}
            style={{ padding: 6, width: 190 }}
          />
          <button type="submit" disabled={ocupado === "novo"} style={{ padding: "6px 12px", cursor: "pointer" }}>
            {ocupado === "novo" ? "Buscando no ML..." : "Adicionar"}
          </button>
        </form>
      </div>

      {mensagem && (
        <div
          role="status"
          style={{
            padding: 10,
            background: mensagem.tom === "erro" ? "#ffecec" : "#ebfbee",
            color: mensagem.tom === "erro" ? "#a61e1e" : "#2b8a3e",
          }}
        >
          {mensagem.texto}
        </div>
      )}

      {erro && <div style={{ padding: 24, textAlign: "center", background: "#ffecec", color: "#a61e1e" }}>{erro}</div>}
      {!erro && carregando && !lista.length && <div style={{ padding: 24, textAlign: "center" }}>Carregando…</div>}
      {!erro && !carregando && !lista.length && (
        <div style={{ padding: 24, textAlign: "center", opacity: 0.7 }}>
          {texto ? "Nenhuma categoria com esse filtro." : "Nenhuma categoria. Rode o bootstrap (README, passo 3)."}
        </div>
      )}

      {lista.length > 0 && (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ textAlign: "left" }}>
              <th style={{ padding: 10, width: 90 }}>Coleta</th>
              <th style={{ padding: 10 }}>Categoria</th>
              <th style={{ padding: 10, width: 130 }}>ID</th>
            </tr>
          </thead>
          <tbody>
            {lista.map((c) => (
              <tr key={c.ml_category_id} style={{ borderTop: "1px solid #f1f1f1" }}>
                <td style={{ padding: 10 }}>
                  <label style={{ cursor: "pointer", display: "flex", gap: 6, alignItems: "center" }}>
                    <input
                      type="checkbox"
                      checked={!!c.enabled}
                      disabled={ocupado === c.ml_category_id}
                      onChange={() => alterna(c)}
                    />
                    {c.enabled ? "sim" : "não"}
                  </label>
                </td>
                <td style={{ padding: 10 }}>{rotuloCategoria(c)}</td>
                <td style={{ padding: 10, fontFamily: "monospace" }}>{c.ml_category_id}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
