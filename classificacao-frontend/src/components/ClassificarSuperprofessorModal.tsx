"use client";

import { useState, useEffect, useRef, useMemo } from "react";
import { createPortal } from "react-dom";
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

export type ModuloSuperprofessor = {
  id: number;       // assu_id
  disciplina: string;
  modulo: string;
  descricao: string;
  habilidade_id?: number | null;
  habilidade_descricao?: string;
};

type AssuntoRaw = { assu_id: number; assunto: string };
type ModuloRaw  = { disc_modu_id: number; modulo: string; assuntos: AssuntoRaw[] };
type DiscRaw    = { disc_id: number; disciplina: string; modulos: ModuloRaw[] };

interface Props {
  isOpen: boolean;
  onClose: () => void;
  onConfirmar: (selecionados: ModuloSuperprofessor[]) => void;
  saving?: boolean;
}

// Cache singleton para não re-buscar a cada abertura
let _cache: ModuloSuperprofessor[] | null = null;

export default function ClassificarSuperprofessorModal({
  isOpen,
  onClose,
  onConfirmar,
  saving = false,
}: Props) {
  const [modulos, setModulos] = useState<ModuloSuperprofessor[]>(_cache ?? []);
  const [loadingModulos, setLoadingModulos] = useState(false);
  const [busca, setBusca] = useState("");
  const [expandidas, setExpandidas] = useState<Set<string>>(new Set());
  const [selecionados, setSelecionados] = useState<ModuloSuperprofessor[]>([]);
  const [showConfirmDialog, setShowConfirmDialog] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    setBusca("");
    setSelecionados([]);
    setShowConfirmDialog(false);
    setExpandidas(new Set());
    setTimeout(() => searchRef.current?.focus(), 150);

    if (_cache) return;
    setLoadingModulos(true);
    apiRequest("/modulos-libro-direto")
      .then((data: DiscRaw[]) => {
        const flat: ModuloSuperprofessor[] = [];
        for (const disc of data) {
          for (const mod of disc.modulos) {
            for (const ass of mod.assuntos) {
              flat.push({
                id: ass.assu_id,
                disciplina: disc.disciplina,
                modulo: mod.modulo,
                descricao: ass.assunto,
                habilidade_id: null,
                habilidade_descricao: "",
              });
            }
          }
        }
        _cache = flat;
        setModulos(flat);
      })
      .catch(() => {})
      .finally(() => setLoadingModulos(false));
  }, [isOpen]);

  const buscaLower = busca.toLowerCase().trim();

  const modulosFiltrados = useMemo(() => {
    if (!buscaLower) return modulos;
    return modulos.filter(
      (m) =>
        m.modulo.toLowerCase().includes(buscaLower) ||
        m.descricao.toLowerCase().includes(buscaLower) ||
        m.disciplina.toLowerCase().includes(buscaLower),
    );
  }, [modulos, buscaLower]);

  // Agrupa: disciplina → modulo → assuntos
  const arvore = useMemo(() => {
    const disc = new Map<string, Map<string, ModuloSuperprofessor[]>>();
    for (const m of modulosFiltrados) {
      if (!disc.has(m.disciplina)) disc.set(m.disciplina, new Map());
      const modMap = disc.get(m.disciplina)!;
      if (!modMap.has(m.modulo)) modMap.set(m.modulo, []);
      modMap.get(m.modulo)!.push(m);
    }
    return Array.from(disc.entries())
      .sort(([a], [b]) => a.localeCompare(b, "pt-BR"))
      .map(([disciplina, modMap]) => ({
        disciplina,
        modulos: Array.from(modMap.entries())
          .sort(([a], [b]) => a.localeCompare(b, "pt-BR"))
          .map(([modulo, assuntos]) => ({ modulo, assuntos })),
      }));
  }, [modulosFiltrados]);

  const isExpandido = (key: string) => buscaLower !== "" || expandidas.has(key);

  const toggle = (key: string) => {
    setExpandidas((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const isSelecionado = (m: ModuloSuperprofessor) =>
    selecionados.some((s) => s.id === m.id && s.descricao === m.descricao);

  const toggleAssunto = (m: ModuloSuperprofessor) => {
    setSelecionados((prev) =>
      isSelecionado(m)
        ? prev.filter((s) => !(s.id === m.id && s.descricao === m.descricao))
        : [...prev, m],
    );
  };

  const handleOverlayClick = (e: ReactMouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget && !saving) onClose();
  };

  if (!isOpen) return null;

  return createPortal(
    <div className={styles.overlay} onClick={handleOverlayClick}>
      <div className={styles.modal}>
        {/* Header */}
        <div className={styles.header}>
          <div className={styles.headerTitle}>
            <Pencil size={18} />
            <h2>Classificar questão</h2>
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

        {/* Busca */}
        <div className={styles.searchArea}>
          <div className={styles.searchWrap}>
            <Search size={18} className={styles.searchIcon} />
            <input
              ref={searchRef}
              type="text"
              placeholder="Pesquisar por módulo, assunto ou disciplina"
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
              {modulosFiltrados.length} resultado
              {modulosFiltrados.length !== 1 ? "s" : ""}
            </span>
          )}
        </div>

        {/* Selecionados */}
        {selecionados.length > 0 && (
          <div className={styles.selectedBanner}>
            <CheckCircle size={14} />
            <span>
              {selecionados.length} assunto
              {selecionados.length > 1 ? "s" : ""} selecionado
              {selecionados.length > 1 ? "s" : ""}:
            </span>
            <div className={styles.selectedTags}>
              {selecionados.map((s) => (
                <span key={`${s.id}-${s.descricao}`} className={styles.selectedTag}>
                  {s.descricao}
                  <button
                    onClick={() => toggleAssunto(s)}
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

        {/* Conteúdo: disciplina → módulo → assuntos */}
        <div className={styles.content}>
          {loadingModulos ? (
            <div className={styles.loadingState}>
              <div className={styles.spinner} />
              <span>Carregando módulos...</span>
            </div>
          ) : arvore.length === 0 ? (
            <div className={styles.emptyState}>
              <Search size={32} />
              <p>
                Nenhum módulo encontrado para{" "}
                <strong>{busca || "a busca atual"}</strong>
              </p>
            </div>
          ) : null}
          {!loadingModulos && arvore.length > 0 && (
            <div className={styles.sectionsList}>
              {arvore.map(({ disciplina, modulos: modList }) => {
                const discKey = `disc::${disciplina}`;
                const discExpandido = isExpandido(discKey);
                const selNaDisc = selecionados.filter((s) => s.disciplina === disciplina).length;

                return (
                  <div key={discKey} className={styles.sectionBlock}>
                    {/* Header da disciplina */}
                    <button
                      className={`${styles.disciplinaHeader} ${discExpandido ? styles.disciplinaHeaderOpen : ""}`}
                      onClick={() => toggle(discKey)}
                    >
                      <div className={styles.disciplinaLeft}>
                        {discExpandido ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                        <span className={styles.disciplinaNome}>{disciplina}</span>
                      </div>
                      <div className={styles.disciplinaRight}>
                        <span className={styles.countBadge}>
                          {modList.reduce((acc, m) => acc + m.assuntos.length, 0)}
                        </span>
                        {selNaDisc > 0 && (
                          <span className={styles.selectedBadge}>
                            <CheckCircle size={11} />
                            {selNaDisc}
                          </span>
                        )}
                      </div>
                    </button>

                    {discExpandido && (
                      <div className={styles.disciplinasList}>
                        {modList.map(({ modulo, assuntos }) => {
                          const modKey = `mod::${disciplina}::${modulo}`;
                          const modExpandido = isExpandido(modKey);
                          const selNoMod = selecionados.filter(
                            (s) => s.disciplina === disciplina && s.modulo === modulo,
                          ).length;

                          return (
                            <div key={modKey} className={styles.disciplinaGroup}>
                              <button
                                className={`${styles.disciplinaHeader} ${modExpandido ? styles.disciplinaHeaderOpen : ""}`}
                                style={{ paddingLeft: "1.75rem", fontSize: "0.88rem" }}
                                onClick={() => toggle(modKey)}
                              >
                                <div className={styles.disciplinaLeft}>
                                  {modExpandido ? (
                                    <ChevronDown size={14} />
                                  ) : (
                                    <ChevronRight size={14} />
                                  )}
                                  <span className={styles.disciplinaNome}>{modulo}</span>
                                </div>
                                <div className={styles.disciplinaRight}>
                                  <span className={styles.countBadge}>{assuntos.length}</span>
                                  {selNoMod > 0 && (
                                    <span className={styles.selectedBadge}>
                                      <CheckCircle size={11} />
                                      {selNoMod}
                                    </span>
                                  )}
                                </div>
                              </button>

                              {modExpandido && (
                                <div className={styles.moduloList}>
                                  {assuntos.map((assunto) => {
                                    const sel = isSelecionado(assunto);
                                    return (
                                      <label
                                        key={`${assunto.id}-${assunto.descricao}`}
                                        className={`${styles.moduloItem} ${sel ? styles.moduloSelected : ""}`}
                                      >
                                        <input
                                          type="checkbox"
                                          checked={sel}
                                          onChange={() => toggleAssunto(assunto)}
                                        />
                                        <div className={styles.moduloText}>
                                          <strong>{assunto.descricao}</strong>
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
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className={styles.footer}>
          <button className={styles.cancelBtn} onClick={onClose} disabled={saving}>
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

      {/* Diálogo de confirmação */}
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
                  <span key={`${s.id}-${s.descricao}`} className={styles.confirmModuloTag}>
                    {s.disciplina} · {s.modulo} · {s.descricao}
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
    </div>,
    document.body,
  );
}
