"use client";

import { useState, useEffect, useRef, useMemo } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import {
  Search,
  X,
  ChevronDown,
  ChevronRight,
  CheckCircle,
  AlertTriangle,
  Pencil,
} from "lucide-react";
import { apiRequest } from "@/lib/api";
import styles from "./CorrigirClassificacaoModal.module.css";

type FonteModulo = "trieduc" | "librostudio";

export type HabilidadeModulo = {
  key: string;
  id: number | string;
  habilidade_id?: number;
  habilidade_descricao: string;
  area: string;
  disciplina: string;
  modulo: string;
  descricao: string;
  ordenacao?: number;
  fonte: FonteModulo;
  assunto_id?: number | string | null;
};

type AssuntoVinculado = {
  id?: number | string | null;
  descricao: string;
};

type ModuloComAssuntosResponse = {
  id: number | string;
  disciplina?: string | null;
  nome: string;
  assuntos: AssuntoVinculado[];
  total_assuntos: number;
  fonte?: string;
  has_relacionamento_trieduc?: boolean;
};

interface CorrigirClassificacaoModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirmar: (selecionados: HabilidadeModulo[]) => void;
  saving?: boolean;
}

const toStringSafe = (value: string | null | undefined): string =>
  (value || "").trim();

export default function CorrigirClassificacaoModal({
  isOpen,
  onClose,
  onConfirmar,
  saving = false,
}: CorrigirClassificacaoModalProps) {
  const [modulos, setModulos] = useState<HabilidadeModulo[]>([]);
  const [loadingModulos, setLoadingModulos] = useState(false);
  const [busca, setBusca] = useState("");
  const [expandidas, setExpandidas] = useState<Set<string>>(new Set());
  const [selecionados, setSelecionados] = useState<HabilidadeModulo[]>([]);
  const [showConfirmDialog, setShowConfirmDialog] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  const sections: Array<{ key: FonteModulo; title: string; className: string }> = [
    { key: "trieduc", title: "TriEduc", className: styles.sectionSourceTrieduc },
    { key: "librostudio", title: "LibroStudio", className: styles.sectionSourceLibro },
  ];

  useEffect(() => {
    if (!isOpen) return;

    setBusca("");
    setSelecionados([]);
    setShowConfirmDialog(false);
    setExpandidas(new Set());
    if (modulos.length === 0) {
      fetchModulos();
    }
    setTimeout(() => searchRef.current?.focus(), 150);
  }, [isOpen]);

  const fetchModulos = async () => {
    setLoadingModulos(true);
    try {
      const [trieducData, librostudioData] = await Promise.all([
        apiRequest("/modulos") as Promise<
          Array<HabilidadeModulo & { key?: string }>
        >,
        apiRequest("/modulos-assuntos") as Promise<{
          modulos: ModuloComAssuntosResponse[];
        }>,
      ]);

      const trieducItens = trieducData.map(
        (m: HabilidadeModulo & { key?: string }) => ({
          ...m,
          key: `trieduc::${m.id}`,
          area: m.area || m.disciplina || "",
          fonte: "trieduc" as const,
          habilidade_descricao: m.habilidade_descricao || "TriEduc",
        }),
      );

      const dedupedTrieduc = new Map<string, HabilidadeModulo>();
      for (const item of trieducItens) {
        const dedupKey = `${item.disciplina}||${item.modulo}||${item.descricao}||${item.habilidade_descricao}||${item.id}`;
        if (!dedupedTrieduc.has(dedupKey)) {
          dedupedTrieduc.set(dedupKey, item);
        }
      }

      const livroItens = (librostudioData.modulos || [])
        .filter((m: ModuloComAssuntosResponse) => !m.has_relacionamento_trieduc)
        .flatMap((m: ModuloComAssuntosResponse) => {
          const disciplina = toStringSafe(m.disciplina);
          const assuntos = m.assuntos || [];
          return assuntos
            .filter((a: AssuntoVinculado) => toStringSafe(a.descricao) !== "")
            .map((assunto: AssuntoVinculado, index: number) => ({
              key: `librostudio::${m.id}::${assunto.id ?? index}`,
              id: m.id,
              area: disciplina || "LibroStudio",
              disciplina,
              modulo: toStringSafe(m.nome),
              descricao: toStringSafe(assunto.descricao),
              habilidade_descricao: "LibroStudio",
              habilidade_id: undefined,
              fonte: "librostudio" as const,
              assunto_id: assunto.id ?? index,
              ordenacao: undefined,
            }));
        });

      const dedupedLivro = new Map<string, HabilidadeModulo>();
      for (const item of livroItens) {
        const dedupKey = `${item.fonte}||${item.disciplina}||${item.modulo}||${item.id}||${item.assunto_id}||${item.descricao}`;
        if (!dedupedLivro.has(dedupKey)) {
          dedupedLivro.set(dedupKey, item);
        }
      }

      setModulos([
        ...dedupedTrieduc.values(),
        ...dedupedLivro.values(),
      ]);
    } catch {
      // tratado pelo apiRequest
    } finally {
      setLoadingModulos(false);
    }
  };

  const buscaLower = busca.toLowerCase().trim();

  const modulosFiltrados = useMemo(() => {
    if (!buscaLower) return modulos;

    return modulos.filter((m) => {
      return (
        m.modulo.toLowerCase().includes(buscaLower) ||
        m.descricao.toLowerCase().includes(buscaLower) ||
        m.disciplina.toLowerCase().includes(buscaLower) ||
        m.habilidade_descricao.toLowerCase().includes(buscaLower)
      );
    });
  }, [modulos, buscaLower]);

  const getItensFonte = (fonte: FonteModulo) =>
    modulosFiltrados.filter((m) => m.fonte === fonte);

  const isDisciplinaExpandida = (source: FonteModulo, disciplina: string) =>
    buscaLower !== "" || expandidas.has(`${source}::${disciplina}`);

  const toggleDisciplina = (source: FonteModulo, disciplina: string) => {
    const id = `${source}::${disciplina}`;
    setExpandidas((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const getDisciplinasPorFonte = (itens: HabilidadeModulo[]) =>
    Array.from(new Set(itens.map((m) => m.disciplina))).sort((a, b) =>
      a.localeCompare(b, "pt-BR"),
    );

  const toggleModulo = (modulo: HabilidadeModulo) => {
    setSelecionados((prev) => {
      const isSelected = prev.some((m) => m.key === modulo.key);
      if (isSelected) {
        return prev.filter((m) => m.key !== modulo.key);
      }
      return [...prev, modulo];
    });
  };

  const handleOverlayClick = (e: ReactMouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget && !saving) onClose();
  };

  if (!isOpen) return null;

  return (
    <div className={styles.overlay} onClick={handleOverlayClick}>
      <div className={styles.modal}>
        <div className={styles.header}>
          <div className={styles.headerTitle}>
            <Pencil size={18} />
            <h2>Corrigir Classificação</h2>
          </div>
          <button
            className={styles.closeBtn}
            onClick={onClose}
            disabled={saving}
            aria-label="Fechar"
          >
            <X size={20} />
          </button>
        </div>

        <div className={styles.searchArea}>
          <div className={styles.searchWrap}>
            <Search size={18} className={styles.searchIcon} />
            <input
              ref={searchRef}
              type="text"
              placeholder="Pesquisar por módulo, assunto, disciplina ou fonte"
              value={busca}
              onChange={(e) => setBusca(e.target.value)}
              className={styles.searchInput}
            />
            {busca && (
              <button
                className={styles.clearSearch}
                onClick={() => setBusca("")}
                aria-label="Limpar busca"
              >
                <X size={14} />
              </button>
            )}
          </div>
          {buscaLower && (
            <span className={styles.searchResultCount}>
              {modulosFiltrados.length} resultado{modulosFiltrados.length !== 1 ? "s" : ""}
            </span>
          )}
        </div>

        {selecionados.length > 0 && (
          <div className={styles.selectedBanner}>
            <CheckCircle size={14} />
            <span>
              {selecionados.length} módulo{selecionados.length > 1 ? "s" : ""} selecionado
              {selecionados.length > 1 ? "s" : ""}:
            </span>
            <div className={styles.selectedTags}>
              {selecionados.map((s) => (
                <span key={s.key} className={styles.selectedTag}>
                  {s.modulo}
                  {s.fonte === "librostudio" && (
                    <span className={styles.selectedTagInner}>LibroStudio</span>
                  )}
                  <button
                    onClick={() => toggleModulo(s)}
                    className={styles.removeTag}
                    aria-label="Remover"
                  >
                    <X size={10} />
                  </button>
                </span>
              ))}
            </div>
          </div>
        )}

        <div className={styles.content}>
          {loadingModulos ? (
            <div className={styles.loadingState}>
              <div className={styles.spinner} />
              <span>Carregando módulos...</span>
            </div>
          ) : sections.every((sec) => getItensFonte(sec.key).length === 0) ? (
            <div className={styles.emptyState}>
              <Search size={32} />
              <p>
                Nenhum módulo encontrado para{" "}
                <strong>{busca || "a busca atual"}</strong>
              </p>
            </div>
          ) : (
            <div className={styles.sectionsList}>
              {sections.map((section) => {
                const itens = getItensFonte(section.key);
                if (itens.length === 0) return null;

                const disciplinas = getDisciplinasPorFonte(itens);

                return (
                  <div key={section.key} className={styles.sectionBlock}>
                    <div className={`${styles.sectionHeader} ${section.className}`}>
                      <h3>{section.title}</h3>
                      <span className={styles.sectionCount}>{itens.length}</span>
                    </div>

                    <div className={styles.disciplinasList}>
                      {disciplinas.map((disc) => {
                        const mods = itens.filter((m) => m.disciplina === disc);
                        const expandido = isDisciplinaExpandida(section.key, disc);
                        const selecionadosNaDisc = mods.filter((m) =>
                          selecionados.some((s) => s.key === m.key),
                        ).length;

                        return (
                          <div key={`${section.key}::${disc}`} className={styles.disciplinaGroup}>
                            <button
                              className={`${styles.disciplinaHeader} ${expandido ? styles.disciplinaHeaderOpen : ""}`}
                              onClick={() => toggleDisciplina(section.key, disc)}
                            >
                              <div className={styles.disciplinaLeft}>
                                {expandido ? (
                                  <ChevronDown size={16} />
                                ) : (
                                  <ChevronRight size={16} />
                                )}
                                <span className={styles.disciplinaNome}>{disc || "Sem disciplina"}</span>
                              </div>
                              <div className={styles.disciplinaRight}>
                                <span className={styles.countBadge}>{mods.length}</span>
                                {selecionadosNaDisc > 0 && (
                                  <span className={styles.selectedBadge}>
                                    <CheckCircle size={11} />
                                    {selecionadosNaDisc}
                                  </span>
                                )}
                              </div>
                            </button>

                            {expandido && (
                              <div className={styles.moduloList}>
                                {mods.map((m) => {
                                  const isSel = selecionados.some((s) => s.key === m.key);
                                  return (
                                    <label
                                      key={m.key}
                                      className={`${styles.moduloItem} ${isSel ? styles.moduloSelected : ""}`}
                                    >
                                      <input
                                        type="checkbox"
                                        checked={isSel}
                                        onChange={() => toggleModulo(m)}
                                      />
                                      <div className={styles.moduloText}>
                                        <strong>{m.modulo}</strong>
                                        <span>{m.descricao}</span>
                                        <div className={styles.moduloMeta}>
                                          {m.fonte === "librostudio" ? (
                                            <span className={styles.libroTag}>LibroStudio</span>
                                          ) : (
                                            <span className={styles.habTag}>
                                              {m.habilidade_descricao}
                                            </span>
                                          )}
                                          {m.area && m.fonte !== "librostudio" && (
                                            <span className={styles.areaTag}>{m.area}</span>
                                          )}
                                        </div>
                                      </div>
                                    </label>
                                  );
                                })}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className={styles.footer}>
          <button
            className={styles.cancelBtn}
            onClick={onClose}
            disabled={saving}
          >
            Cancelar
          </button>
          <button
            className={styles.confirmBtn}
            onClick={() => setShowConfirmDialog(true)}
            disabled={selecionados.length === 0 || saving}
          >
            <CheckCircle size={16} />
            Confirmar seleção
          </button>
        </div>
      </div>

      {showConfirmDialog && (
        <div className={styles.confirmOverlay}>
          <div className={styles.confirmDialog}>
            <div className={styles.confirmIconWrap}>
              <AlertTriangle size={28} />
            </div>
            <h3>Concluir classificação?</h3>
            <p>Tem certeza que gostaria de concluir esta classificação?</p>
            {selecionados.length > 0 && (
              <div className={styles.confirmModulosList}>
                {selecionados.map((s) => (
                  <span key={s.key} className={styles.confirmModuloTag}>
                    {s.disciplina} · {s.modulo}
                    {s.fonte === "librostudio" && " · LibroStudio"}
                  </span>
                ))}
              </div>
            )}
            <div className={styles.confirmActions}>
              <button
                className={styles.confirmCancelBtn}
                onClick={() => setShowConfirmDialog(false)}
                disabled={saving}
              >
                Cancelar
              </button>
              <button
                className={styles.confirmSaveBtn}
                onClick={() => onConfirmar(selecionados)}
                disabled={saving}
              >
                {saving ? "Salvando..." : "Sim, confirmar"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
