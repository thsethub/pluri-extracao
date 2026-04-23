"use client";

import { useState, useEffect } from "react";
import { apiRequest } from "@/lib/api";
import AppLayout from "@/components/AppLayout";
import Dropdown from "@/components/Dropdown";
import {
  Save,
  Info,
  AlertCircle,
  BookOpen,
  Tag,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import styles from "../classificar/Classificar.module.css";
import spStyles from "../Superprofessor.module.css";

export default function PendentesPage() {
  const [questoes, setQuestoes] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const [disciplinaFiltro, setDisciplinaFiltro] = useState("");
  const [assuntoFiltro, setAssuntoFiltro] = useState("");
  const [disciplinasDisponiveis, setDisciplinasDisponiveis] = useState<{value: string, label: string}[]>([]);
  const [assuntosDisponiveis, setAssuntosDisponiveis] = useState<{value: string, label: string}[]>([]);
  const [loadingFiltros, setLoadingFiltros] = useState(false);

  const [modulosSelecionados, setModulosSelecionados] = useState<{[key: number]: any[]}>({});

  useEffect(() => {
    async function loadDisciplinas() {
      try {
        const data = await apiRequest("/superprofessor/disciplinas");
        const list = (data.disciplinas || []).map((d: any) => {
          if (typeof d === "string") return { value: d, label: d };
          const nome = d.nome || d.disciplina_sp || "Desconhecido";
          const total = d.total !== undefined ? ` (${d.total})` : "";
          return { value: nome, label: `${nome}${total}` };
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

        const list = (data.assuntos || []).map((a: any) => {
          if (typeof a === "string") return { value: a, label: a };
          const nome = a.nome || a.assunto_sp || "Desconhecido";
          const total = a.total !== undefined ? ` (${a.total})` : "";
          return { value: nome, label: `${nome}${total}` };
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

  useEffect(() => {
    loadPendentes();
  }, [disciplinaFiltro, assuntoFiltro]);

  const loadPendentes = async () => {
    setLoading(true);
    setError("");
    setQuestoes([]);

    try {
      const params = new URLSearchParams();
      if (disciplinaFiltro) params.append("disciplina", disciplinaFiltro);
      if (assuntoFiltro) params.append("assunto_sp", assuntoFiltro);
      const qs = params.toString() ? `?${params.toString()}` : "";
      const data = await apiRequest(`/superprofessor/pendentes${qs}`);
      setQuestoes(data);
    } catch (err: any) {
      setError(err.message || "Nenhuma questão pendente encontrada");
    } finally {
      setLoading(false);
    }
  };

  const toggleModulo = (questaoId: number, modulo: any) => {
    setModulosSelecionados((prev) => {
      const current = prev[questaoId] || [];
      const isSelected = current.some((m) => m.id === modulo.id);
      return {
        ...prev,
        [questaoId]: isSelected
          ? current.filter((m) => m.id !== modulo.id)
          : [...current, modulo],
      };
    });
  };

  const handleSalvar = async (questao: any) => {
    const selectedModulos = modulosSelecionados[questao.id] || [];
    if (selectedModulos.length === 0) return;

    setSaving(true);
    const trieducSelecionados = selectedModulos.filter(
      (m) => m.habilidade_id != null,
    );

    try {
      await apiRequest("/superprofessor/salvar", {
        method: "POST",
        body: JSON.stringify({
          questao_nova_id: questao.id,
          habilidade_modulo_ids: trieducSelecionados.map((m) => m.id),
          modulos_escolhidos: selectedModulos.map((m) => m.modulo),
          classificacoes_trieduc: selectedModulos.map(
            (m) => m.habilidade_descricao,
          ),
          descricoes_assunto: selectedModulos.map((m) => m.descricao),
          habilidade_modulo_id: trieducSelecionados[0]?.id ?? null,
          modulo_escolhido: selectedModulos[0]?.modulo ?? null,
          classificacao_trieduc:
            selectedModulos[0]?.habilidade_descricao ?? null,
          descricao_assunto: selectedModulos[0]?.descricao ?? null,
          observacao: "",
        }),
      });
      loadPendentes();
      setModulosSelecionados({});
    } catch (err: any) {
      alert(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <AppLayout>
      <div className={styles.header}>
        <div className={styles.headerInfo}>
          <h1>Pendentes Superprofessor</h1>
          <p>
            Questões que foram puladas e podem ser reclassificadas
            {questoes.length > 0 && (
              <span className={spStyles.pendentesTag}>
                {questoes.length} questão{questoes.length !== 1 ? "s" : ""}
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
          <span>Carregando questões pendentes...</span>
        </div>
      ) : error ? (
        <div className={styles.empty}>
          <AlertCircle size={48} color="var(--primary)" />
          <p>{error}</p>
          <button onClick={() => loadPendentes()}>Tentar Novamente</button>
        </div>
      ) : questoes.length === 0 ? (
        <div className={styles.empty}>
          <AlertCircle size={48} color="var(--primary)" />
          <p>Nenhuma questão pendente para classificar</p>
        </div>
      ) : (
        <div className={styles.content}>
          <div className={spStyles.pendentesContainer}>
            {questoes.map((questao) => (
              <div
                key={questao.id}
                className={`${styles.questaoCard} glass fade-in`}
                style={{ marginBottom: "1rem" }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    cursor: "pointer",
                    padding: "1rem",
                  }}
                  onClick={() =>
                    setExpandedId(expandedId === questao.id ? null : questao.id)
                  }
                >
                  <div>
                    <div className={styles.questaoMeta}>
                      {questao.disciplina_sp && (
                        <span className={styles.tag}>{questao.disciplina_sp}</span>
                      )}
                      <span className={styles.idTag}>SP ID: {questao.sp_id}</span>
                    </div>
                    <div
                      style={{
                        color: "var(--text-secondary)",
                        fontSize: "0.9rem",
                        marginTop: "0.5rem",
                      }}
                    >
                      {questao.enunciado.substring(0, 100)}...
                    </div>
                  </div>
                  {expandedId === questao.id ? (
                    <ChevronUp size={24} />
                  ) : (
                    <ChevronDown size={24} />
                  )}
                </div>

                {expandedId === questao.id && (
                  <div style={{ paddingLeft: "1rem", paddingRight: "1rem", paddingBottom: "1rem" }}>
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
                              <> › {questao.assuntos_libro.join(", ")}</>
                            )}
                          </span>
                        </div>
                      )}
                    </div>

                    <div
                      className={styles.enunciado}
                      dangerouslySetInnerHTML={{ __html: questao.enunciado }}
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

                    <div className={`${styles.moduloCard}`}>
                      <div className={styles.moduloHeader}>
                        <Info size={18} />
                        <h3>Módulos Libro</h3>
                      </div>

                      {questao.modulos_possiveis.length === 0 ? (
                        <p className={spStyles.semModulos}>
                          Nenhum módulo libro encontrado para esta disciplina.
                        </p>
                      ) : (
                        <div className={styles.moduloList}>
                          {questao.modulos_possiveis.map((m: any) => (
                            <label
                              key={m.id}
                              className={`${styles.moduloItem} ${
                                (modulosSelecionados[questao.id] || []).some((s) => s.id === m.id)
                                  ? styles.moduloSelected
                                  : ""
                              }`}
                            >
                              <input
                                type="checkbox"
                                name="modulo"
                                checked={(modulosSelecionados[questao.id] || []).some(
                                  (s) => s.id === m.id,
                                )}
                                onChange={() => toggleModulo(questao.id, m)}
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
                        <button
                          onClick={() => handleSalvar(questao)}
                          disabled={(modulosSelecionados[questao.id] || []).length === 0 || saving}
                          className={styles.saveBtn}
                          style={{ width: "100%" }}
                        >
                          <Save size={18} />
                          {saving ? "Gravando..." : "Salvar Classificação"}
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </AppLayout>
  );
}
