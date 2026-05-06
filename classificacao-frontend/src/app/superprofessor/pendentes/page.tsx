"use client";

import { useState, useEffect, useMemo } from "react";
import { apiRequest } from "@/lib/api";
import AppLayout from "@/components/AppLayout";
import Dropdown from "@/components/Dropdown";
import ClassificarSuperprofessorModal, {
  ModuloSuperprofessor,
} from "@/components/ClassificarSuperprofessorModal";
import {
  Save,
  Info,
  AlertCircle,
  BookOpen,
  Tag,
  ChevronDown,
  ChevronUp,
  ChevronLeft,
  ChevronRight,
  Pencil,
} from "lucide-react";
import styles from "../../classificar/Classificar.module.css";
import spStyles from "../Superprofessor.module.css";
import { sanitizeEnunciado } from "@/lib/sanitizeHtml";

const ITEMS_PER_PAGE = 10;

export default function PendentesPage() {
  const [todasQuestoes, setTodasQuestoes] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const [disciplinaFiltro, setDisciplinaFiltro] = useState("");
  const [assuntoFiltro, setAssuntoFiltro] = useState("");
  const [paginaAtual, setPaginaAtual] = useState(1);

  const [modulosSelecionados, setModulosSelecionados] = useState<{ [key: number]: any[] }>({});
  const [searchTerms, setSearchTerms] = useState<{ [key: number]: string }>({});

  const [questaoModal, setQuestaoModal] = useState<any | null>(null);

  useEffect(() => {
    loadPendentes();
  }, []);

  const loadPendentes = async () => {
    setLoading(true);
    setError("");
    setTodasQuestoes([]);
    try {
      const data = await apiRequest("/superprofessor/pendentes");
      setTodasQuestoes(data);
    } catch (err: any) {
      setError(err.message || "Nenhuma questão pendente encontrada");
    } finally {
      setLoading(false);
    }
  };

  // Contagens para os labels dos dropdowns
  const disciplinasDisponiveis = useMemo(() => {
    const contagem: Record<string, number> = {};
    todasQuestoes.forEach((q) => {
      if (q.disciplina_sp) contagem[q.disciplina_sp] = (contagem[q.disciplina_sp] || 0) + 1;
    });
    return Object.entries(contagem)
      .sort(([a], [b]) => a.localeCompare(b, "pt-BR"))
      .map(([d, total]) => ({ value: d, label: `${d} (${total})` }));
  }, [todasQuestoes]);

  const assuntosDisponiveis = useMemo(() => {
    const contagem: Record<string, number> = {};
    todasQuestoes
      .filter((q) => !disciplinaFiltro || q.disciplina_sp === disciplinaFiltro)
      .forEach((q) => {
        if (q.assunto_sp) contagem[q.assunto_sp] = (contagem[q.assunto_sp] || 0) + 1;
      });
    return Object.entries(contagem)
      .sort(([a], [b]) => a.localeCompare(b, "pt-BR"))
      .map(([a, total]) => ({ value: a, label: `${a} (${total})` }));
  }, [todasQuestoes, disciplinaFiltro]);

  const questoesFiltradas = useMemo(() => {
    return todasQuestoes.filter((q) => {
      if (disciplinaFiltro && q.disciplina_sp !== disciplinaFiltro) return false;
      if (assuntoFiltro && q.assunto_sp !== assuntoFiltro) return false;
      return true;
    });
  }, [todasQuestoes, disciplinaFiltro, assuntoFiltro]);

  const totalPaginas = Math.max(1, Math.ceil(questoesFiltradas.length / ITEMS_PER_PAGE));

  const questoesPagina = useMemo(() => {
    const inicio = (paginaAtual - 1) * ITEMS_PER_PAGE;
    return questoesFiltradas.slice(inicio, inicio + ITEMS_PER_PAGE);
  }, [questoesFiltradas, paginaAtual]);

  const handleFiltroChange = (tipo: "disciplina" | "assunto", valor: string) => {
    setPaginaAtual(1);
    setExpandedId(null);
    if (tipo === "disciplina") {
      setDisciplinaFiltro(valor);
      setAssuntoFiltro("");
    } else {
      setAssuntoFiltro(valor);
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

  const buildPayload = (questaoId: number, selectedModulos: any[]) => {
    return {
      questao_nova_id: questaoId,
      modulos_escolhidos: selectedModulos.map((m) => m.modulo ?? ""),
      descricoes_assunto: selectedModulos.map((m) => m.descricao ?? ""),
      modulo_escolhido: selectedModulos[0]?.modulo ?? null,
      descricao_assunto: selectedModulos[0]?.descricao ?? null,
      observacao: "",
    };
  };

  const handleSalvar = async (questao: any) => {
    const selectedModulos = modulosSelecionados[questao.id] || [];
    if (selectedModulos.length === 0) return;
    setSaving(true);
    try {
      await apiRequest("/superprofessor/salvar", {
        method: "POST",
        body: JSON.stringify(buildPayload(questao.id, selectedModulos)),
      });
      setTodasQuestoes((prev) => prev.filter((q) => q.id !== questao.id));
      setModulosSelecionados({});
    } catch (err: any) {
      alert(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleSalvarModal = async (selecionados: ModuloSuperprofessor[]) => {
    if (!questaoModal || selecionados.length === 0) return;
    setSaving(true);
    try {
      await apiRequest("/superprofessor/salvar", {
        method: "POST",
        body: JSON.stringify(buildPayload(questaoModal.id, selecionados)),
      });
      setTodasQuestoes((prev) => prev.filter((q) => q.id !== questaoModal.id));
      setModulosSelecionados({});
      setQuestaoModal(null);
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
            {questoesFiltradas.length > 0 && (
              <span className={spStyles.pendentesTag}>
                {questoesFiltradas.length} questão{questoesFiltradas.length !== 1 ? "s" : ""}
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
            onChange={(val: any) => handleFiltroChange("disciplina", val)}
            placeholder="Todas as disciplinas"
            searchable
          />
        </div>
        <div className={spStyles.filterGroup}>
          <Dropdown
            label="Assunto SP"
            options={assuntosDisponiveis}
            value={assuntoFiltro}
            onChange={(val: any) => handleFiltroChange("assunto", val)}
            placeholder="Todos os assuntos"
            searchable
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
      ) : questoesFiltradas.length === 0 ? (
        <div className={styles.empty}>
          <AlertCircle size={48} color="var(--primary)" />
          <p>Nenhuma questão pendente para classificar</p>
        </div>
      ) : (
        <>
          <div className={spStyles.pendentesContainer}>
            {questoesPagina.map((questao) => (
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
                      {questao.assunto_sp && (
                        <span className={styles.tag} style={{ opacity: 0.75 }}>
                          {questao.assunto_sp}
                        </span>
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
                      {questao.enunciado
                        .replace(/<[^>]*>/g, " ")
                        .replace(/\s+/g, " ")
                        .trim()
                        .substring(0, 120)}
                      ...
                    </div>
                  </div>
                  {expandedId === questao.id ? (
                    <ChevronUp size={24} />
                  ) : (
                    <ChevronDown size={24} />
                  )}
                </div>

                {expandedId === questao.id && (
                  <div className={spStyles.expandedLayout}>
                    {/* Coluna esquerda: questão */}
                    <div className={spStyles.expandedQuestao}>
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
                        dangerouslySetInnerHTML={{
                          __html: sanitizeEnunciado(questao.enunciado),
                        }}
                      />

                      {questao.alternativas && questao.alternativas.length > 0 && (
                        <div className={styles.alternativas}>
                          {questao.alternativas.map((alt: any, index: number) => (
                            <div
                              key={index}
                              className={`${styles.altItem} ${alt.correta ? styles.altCorreta : ""}`}
                            >
                              <span className={styles.altLetra}>
                                {alt.letra
                                  ? `${alt.letra})`
                                  : `${String.fromCharCode(97 + index)})`}
                              </span>
                              <span dangerouslySetInnerHTML={{ __html: alt.texto }} />
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* Coluna direita: módulos */}
                    <div className={spStyles.expandedModulos}>
                      <div className={styles.moduloHeader}>
                        <Info size={18} />
                        <h3>Módulos Libro</h3>
                      </div>

                      <div className={spStyles.searchContainer}>
                        <input
                          type="text"
                          placeholder="Buscar módulo ou assunto..."
                          value={searchTerms[questao.id] || ""}
                          onChange={(e) =>
                            setSearchTerms((prev) => ({
                              ...prev,
                              [questao.id]: e.target.value,
                            }))
                          }
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
                            .filter((m: any) => {
                              const term = (
                                searchTerms[questao.id] || ""
                              ).toLowerCase();
                              return (
                                m.modulo.toLowerCase().includes(term) ||
                                m.descricao.toLowerCase().includes(term)
                              );
                            })
                            .map((m: any) => (
                              <label
                                key={m.id}
                                className={`${styles.moduloItem} ${
                                  (modulosSelecionados[questao.id] || []).some(
                                    (s) => s.id === m.id,
                                  )
                                    ? styles.moduloSelected
                                    : ""
                                }`}
                              >
                                <input
                                  type="checkbox"
                                  name="modulo"
                                  checked={(
                                    modulosSelecionados[questao.id] || []
                                  ).some((s) => s.id === m.id)}
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
                          disabled={
                            (modulosSelecionados[questao.id] || []).length === 0 ||
                            saving
                          }
                          className={styles.saveBtn}
                          style={{ width: "100%" }}
                        >
                          <Save size={18} />
                          {saving ? "Gravando..." : "Salvar Classificação"}
                        </button>
                        <div className={styles.corrigirBtnWrap}>
                          <button
                            className={styles.corrigirBtn}
                            onClick={(e) => {
                              e.stopPropagation();
                              setQuestaoModal(questao);
                            }}
                          >
                            <Pencil size={15} />
                            Corrigir classificação
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* Paginação */}
          {totalPaginas > 1 && (
            <div className={spStyles.paginacao}>
              <button
                className={spStyles.paginacaoBtn}
                onClick={() => setPaginaAtual((p) => Math.max(1, p - 1))}
                disabled={paginaAtual === 1}
              >
                <ChevronLeft size={18} />
              </button>
              <span className={spStyles.paginacaoInfo}>
                Página {paginaAtual} de {totalPaginas}
                <span className={spStyles.paginacaoTotal}>
                  &nbsp;({questoesFiltradas.length} questões)
                </span>
              </span>
              <button
                className={spStyles.paginacaoBtn}
                onClick={() => setPaginaAtual((p) => Math.min(totalPaginas, p + 1))}
                disabled={paginaAtual === totalPaginas}
              >
                <ChevronRight size={18} />
              </button>
            </div>
          )}
        </>
      )}

      <ClassificarSuperprofessorModal
        isOpen={questaoModal !== null}
        onClose={() => setQuestaoModal(null)}
        onConfirmar={handleSalvarModal}
        saving={saving}
      />
    </AppLayout>
  );
}
