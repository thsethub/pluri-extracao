"use client";

import { useState, useEffect } from "react";
import { apiRequest } from "@/lib/api";
import AppLayout from "@/components/AppLayout";
import Dropdown from "@/components/Dropdown";
import {
  FastForward,
  Save,
  Info,
  AlertCircle,
  BookOpen,
  Tag,
} from "lucide-react";
import styles from "../classificar/Classificar.module.css";
import spStyles from "./Superprofessor.module.css";
import { sanitizeEnunciado } from "@/lib/sanitizeHtml";

export default function SuperprofessorPage() {
  const [questao, setQuestao] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [skipping, setSkipping] = useState(false);
  const [observacao, setObservacao] = useState("");
  const [modulosSelecionados, setModulosSelecionados] = useState<any[]>([]);

  const [disciplinaFiltro, setDisciplinaFiltro] = useState("");
  const [assuntoFiltro, setAssuntoFiltro] = useState("");
  const [disciplinasDisponiveis, setDisciplinasDisponiveis] = useState<{value: string, label: string}[]>([]);
  const [assuntosDisponiveis, setAssuntosDisponiveis] = useState<{value: string, label: string}[]>([]);
  const [loadingFiltros, setLoadingFiltros] = useState(false);
  const [searchTermLibro, setSearchTermLibro] = useState("");

  useEffect(() => {
    async function loadDisciplinas() {
      try {
        const data = await apiRequest("/superprofessor/disciplinas");
        console.log("Superprofessor - Disciplinas recebidas:", data);
        
        const list = (data.disciplinas || []).map((d: any) => {
          // Se d for string
          if (typeof d === "string") return { value: d, label: d };
          // Se d for objeto {nome: string, total: number}
          const nome = d.nome || d.disciplina_sp || "Desconhecido";
          const total = d.total !== undefined ? ` (${d.total})` : "";
          return {
            value: nome,
            label: `${nome}${total}`,
          };
        });
        
        setDisciplinasDisponiveis(list);
      } catch (err) {
        console.error("Erro ao carregar disciplinas:", err);
      }
    }
    loadDisciplinas();
  }, []);

  useEffect(() => {
    async function loadAssuntos() {
      setLoadingFiltros(true);
      try {
        const params = new URLSearchParams();
        if (disciplinaFiltro) params.append("disciplina", disciplinaFiltro);
        const data = await apiRequest(`/superprofessor/assuntos?${params.toString()}`);
        console.log("Superprofessor - Assuntos recebidos:", data);

        const list = (data.assuntos || []).map((a: any) => {
          if (typeof a === "string") return { value: a, label: a };
          const nome = a.nome || a.assunto_sp || "Desconhecido";
          const total = a.total !== undefined ? ` (${a.total})` : "";
          return {
            value: nome,
            label: `${nome}${total}`,
          };
        });
        
        setAssuntosDisponiveis(list);
      } catch (err) {
        console.error("Erro ao carregar assuntos:", err);
      } finally {
        setLoadingFiltros(false);
      }
    }
    loadAssuntos();
  }, [disciplinaFiltro]);

  const fetchProxima = async (discFiltro?: string, assFiltro?: string) => {
    setLoading(true);
    setError("");
    setQuestao(null);
    setModulosSelecionados([]);
    setObservacao("");

    try {
      const params = new URLSearchParams();
      if (discFiltro) params.append("disciplina", discFiltro);
      if (assFiltro) params.append("assunto_sp", assFiltro);
      const qs = params.toString() ? `?${params.toString()}` : "";
      const data = await apiRequest(`/superprofessor/proxima${qs}`);
      setQuestao(data);

      if (data.assuntos_libro && data.modulos_possiveis) {
        const assuntosLower = (data.assuntos_libro as string[]).map((a) =>
          a.toLowerCase().trim(),
        );
        const preSelected = data.modulos_possiveis.filter((m: any) =>
          assuntosLower.some(
            (a) =>
              m.descricao.toLowerCase().includes(a) ||
              a.includes(m.modulo.toLowerCase()),
          ),
        );
        if (preSelected.length > 0) setModulosSelecionados(preSelected);
      }
    } catch (err: any) {
      setError(err.message || "Nenhuma questão superprofessor pendente.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchProxima(disciplinaFiltro, assuntoFiltro);
  }, [disciplinaFiltro, assuntoFiltro]);

  const toggleModulo = (modulo: any) => {
    setModulosSelecionados((prev) => {
      const isSelected = prev.some((m) => m.id === modulo.id);
      return isSelected ? prev.filter((m) => m.id !== modulo.id) : [...prev, modulo];
    });
  };

  const handleSalvar = async () => {
    if (modulosSelecionados.length === 0 || !questao) return;
    setSaving(true);

    const trieducSelecionados = modulosSelecionados.filter(
      (m) => m.habilidade_id != null,
    );

    try {
      await apiRequest("/superprofessor/salvar", {
        method: "POST",
        body: JSON.stringify({
          questao_nova_id: questao.id,
          habilidade_modulo_ids: trieducSelecionados.map((m) => m.id),
          modulos_escolhidos: modulosSelecionados.map((m) => m.modulo),
          classificacoes_trieduc: modulosSelecionados.map(
            (m) => m.habilidade_descricao,
          ),
          descricoes_assunto: modulosSelecionados.map((m) => m.descricao),
          habilidade_modulo_id: trieducSelecionados[0]?.id ?? null,
          modulo_escolhido: modulosSelecionados[0]?.modulo ?? null,
          classificacao_trieduc:
            modulosSelecionados[0]?.habilidade_descricao ?? null,
          descricao_assunto: modulosSelecionados[0]?.descricao ?? null,
          observacao,
        }),
      });
      fetchProxima(disciplinaFiltro, assuntoFiltro);
    } catch (err: any) {
      alert(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handlePular = async () => {
    if (!questao) return;
    setSkipping(true);
    try {
      await apiRequest("/superprofessor/pular", {
        method: "POST",
        body: JSON.stringify({ questao_nova_id: questao.id }),
      });
      fetchProxima(disciplinaFiltro, assuntoFiltro);
    } catch (err: any) {
      alert(err.message);
    } finally {
      setSkipping(false);
    }
  };

  return (
    <AppLayout>
      <div className={styles.header}>
        <div className={styles.headerInfo}>
          <h1>Superprofessor</h1>
          <p>
            Revise o mapeamento de questões do superprofessor para módulos libro
            {questao?.total_pendentes != null && (
              <span className={spStyles.pendentesTag}>
                {questao.total_pendentes} pendente
                {questao.total_pendentes !== 1 ? "s" : ""}
              </span>
            )}
          </p>
        </div>
      </div>

      <div className={`${spStyles.filterBar} glass fade-in`}>
        <div className={spStyles.filterGroup}>
          <Dropdown
            label="Disciplina SP"
            options={disciplinasDisponiveis}
            value={disciplinaFiltro}
            onChange={(val: any) => {
              setDisciplinaFiltro(val);
              setAssuntoFiltro("");
            }}
            placeholder="Todas as disciplinas"
            searchable
          />
        </div>
        <div className={spStyles.filterGroup}>
          <Dropdown
            label="Assunto SP"
            options={assuntosDisponiveis}
            value={assuntoFiltro}
            onChange={(val: any) => setAssuntoFiltro(val)}
            placeholder={loadingFiltros ? "Carregando..." : "Todos os assuntos"}
            searchable
            disabled={loadingFiltros}
          />
        </div>
      </div>

      {loading ? (
        <div className={styles.loading}>
          <div className={styles.spinner}></div>
          <span>Carregando próxima questão...</span>
        </div>
      ) : error ? (
        <div className={styles.empty}>
          <AlertCircle size={48} color="var(--primary)" />
          <p>{error}</p>
          <button onClick={() => fetchProxima(disciplinaFiltro, assuntoFiltro)}>Tentar Novamente</button>
        </div>
      ) : (
        questao && (
          <div className={styles.content}>
            <div className={`${styles.questaoCard} glass fade-in`}>
              <div className={styles.questaoMeta}>
                {questao.disciplina_sp && (
                  <span className={styles.tag}>{questao.disciplina_sp}</span>
                )}
                <span className={styles.idTag}>SP ID: {questao.sp_id}</span>
              </div>

              <div className={spStyles.spClassif}>
                <div className={spStyles.spClassifRow}>
                  <BookOpen size={14} />
                  <span className={spStyles.spLabel}>Classificação SP:</span>
                  <span className={spStyles.spValue}>
                    {questao.classif_sp_breadcrumb || questao.assunto_sp || "—"}
                  </span>
                </div>

                {questao.disciplinas_libro && questao.disciplinas_libro.length > 0 && (
                  <div className={spStyles.spClassifRow}>
                    <Tag size={14} />
                    <span className={spStyles.spLabel}>Mapeamento libro:</span>
                    <span className={spStyles.spValue}>
                      {questao.disciplinas_libro.join(", ")}
                      {questao.assuntos_libro && questao.assuntos_libro.length > 0 && (
                        <> &rsaquo; {questao.assuntos_libro.join(", ")}</>
                      )}
                    </span>
                  </div>
                )}
              </div>

              <div
                className={styles.enunciado}
                dangerouslySetInnerHTML={{ __html: sanitizeEnunciado(questao.enunciado) }}
              />

              {questao.alternativas && questao.alternativas.length > 0 && (
                <div className={styles.alternativas}>
                  {questao.alternativas.map((alt: any, index: number) => (
                    <div
                      key={index}
                      className={`${styles.altItem} ${alt.correta ? styles.altCorreta : ""}`}
                    >
                      <span className={styles.altLetra}>
                        {alt.letra ? `${alt.letra})` : `${String.fromCharCode(97 + index)})`}
                      </span>
                      <span dangerouslySetInnerHTML={{ __html: alt.texto }} />
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className={`${styles.moduloCard} glass fade-in`}>
              <div className={styles.moduloHeader}>
                <Info size={18} />
                <h3>Módulos Libro</h3>
              </div>

              <div className={spStyles.searchContainer}>
                <input
                  type="text"
                  placeholder="Buscar módulo ou assunto libro..."
                  value={searchTermLibro}
                  onChange={(e) => setSearchTermLibro(e.target.value)}
                  className={spStyles.searchInput}
                />
              </div>

              {questao.modulos_possiveis.length === 0 ? (
                <p className={spStyles.semModulos}>
                  Nenhum módulo libro encontrado para esta disciplina.
                </p>
              ) : (
                <div className={styles.moduloList}>
                  {questao.modulos_possiveis
                    .filter(
                      (m: any) =>
                        m.modulo.toLowerCase().includes(searchTermLibro.toLowerCase()) ||
                        m.descricao.toLowerCase().includes(searchTermLibro.toLowerCase()),
                    )
                    .map((m: any) => (
                      <label
                        key={m.id}
                        className={`${styles.moduloItem} ${
                          modulosSelecionados.some((s) => s.id === m.id)
                            ? styles.moduloSelected
                            : ""
                        }`}
                      >
                        <input
                          type="checkbox"
                          name="modulo"
                          checked={modulosSelecionados.some((s) => s.id === m.id)}
                          onChange={() => toggleModulo(m)}
                        />
                        <div className={styles.moduloText}>
                          <strong>{m.modulo}</strong>
                          <span>{m.descricao}</span>
                        </div>
                      </label>
                    ))}
                </div>
              )}

              <div className={styles.actionArea}>
                <textarea
                  placeholder="Observação opcional sobre a classificação"
                  value={observacao}
                  onChange={(e) => setObservacao(e.target.value)}
                />
                <div className={styles.buttons}>
                  <button
                    onClick={handlePular}
                    disabled={skipping}
                    className={styles.skipBtn}
                  >
                    <FastForward size={18} />
                    {skipping ? "Pulando..." : "Pular"}
                  </button>
                  <button
                    onClick={handleSalvar}
                    disabled={modulosSelecionados.length === 0 || saving}
                    className={styles.saveBtn}
                  >
                    <Save size={18} />
                    {saving ? "Gravando..." : "Salvar Classificação"}
                  </button>
                </div>
              </div>
            </div>
          </div>
        )
      )}
    </AppLayout>
  );
}
