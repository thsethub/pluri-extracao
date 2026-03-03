"use client";

import { useState, useEffect, useRef } from "react";
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

export type HabilidadeModulo = {
  id: number;
  habilidade_id?: number;
  habilidade_descricao: string;
  area: string;
  disciplina: string;
  modulo: string;
  descricao: string;
  ordenacao?: number;
};

interface CorrigirClassificacaoModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirmar: (selecionados: HabilidadeModulo[]) => void;
  saving?: boolean;
}

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

  useEffect(() => {
    if (isOpen) {
      setBusca("");
      setSelecionados([]);
      setShowConfirmDialog(false);
      setExpandidas(new Set());
      if (modulos.length === 0) {
        fetchModulos();
      }
      setTimeout(() => searchRef.current?.focus(), 150);
    }
  }, [isOpen]);

  const fetchModulos = async () => {
    setLoadingModulos(true);
    try {
      const data = await apiRequest("/modulos");
      setModulos(data);
    } catch {
      // tratado pelo apiRequest
    } finally {
      setLoadingModulos(false);
    }
  };

  const buscaLower = busca.toLowerCase().trim();
  const filtrados = buscaLower
    ? modulos.filter(
        (m) =>
          m.modulo.toLowerCase().includes(buscaLower) ||
          m.descricao.toLowerCase().includes(buscaLower) ||
          m.disciplina.toLowerCase().includes(buscaLower),
      )
    : modulos;

  const disciplinas = Array.from(
    new Set(filtrados.map((m) => m.disciplina)),
  ).sort();

  const modulosPorDisciplina = (disc: string) =>
    filtrados.filter((m) => m.disciplina === disc);

  const toggleDisciplina = (disc: string) => {
    setExpandidas((prev) => {
      const next = new Set(prev);
      if (next.has(disc)) next.delete(disc);
      else next.add(disc);
      return next;
    });
  };

  const toggleModulo = (modulo: HabilidadeModulo) => {
    setSelecionados((prev) => {
      const isSelected = prev.some((m) => m.id === modulo.id);
      if (isSelected) return prev.filter((m) => m.id !== modulo.id);
      return [...prev, modulo];
    });
  };

  const isDisciplinaExpandida = (disc: string) =>
    buscaLower !== "" || expandidas.has(disc);

  const handleOverlayClick = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget && !saving) onClose();
  };

  if (!isOpen) return null;

  return (
    <div className={styles.overlay} onClick={handleOverlayClick}>
      <div className={styles.modal}>
        {/* Header */}
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

        {/* Barra de pesquisa destacada */}
        <div className={styles.searchArea}>
          <div className={styles.searchWrap}>
            <Search size={18} className={styles.searchIcon} />
            <input
              ref={searchRef}
              type="text"
              placeholder="Pesquisar por módulo, assunto ou disciplina..."
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
              {filtrados.length} resultado{filtrados.length !== 1 ? "s" : ""}
            </span>
          )}
        </div>

        {/* Banner de selecionados */}
        {selecionados.length > 0 && (
          <div className={styles.selectedBanner}>
            <CheckCircle size={14} />
            <span>
              {selecionados.length} módulo{selecionados.length > 1 ? "s" : ""}{" "}
              selecionado{selecionados.length > 1 ? "s" : ""}:
            </span>
            <div className={styles.selectedTags}>
              {selecionados.map((s) => (
                <span key={s.id} className={styles.selectedTag}>
                  {s.modulo}
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

        {/* Conteúdo scrollável */}
        <div className={styles.content}>
          {loadingModulos ? (
            <div className={styles.loadingState}>
              <div className={styles.spinner} />
              <span>Carregando módulos...</span>
            </div>
          ) : disciplinas.length === 0 ? (
            <div className={styles.emptyState}>
              <Search size={32} />
              <p>
                Nenhum módulo encontrado para "<strong>{busca}</strong>"
              </p>
            </div>
          ) : (
            <div className={styles.disciplinasList}>
              {disciplinas.map((disc) => {
                const mods = modulosPorDisciplina(disc);
                const expandido = isDisciplinaExpandida(disc);
                const selecionadosNaDisc = selecionados.filter(
                  (s) => s.disciplina === disc,
                ).length;
                return (
                  <div key={disc} className={styles.disciplinaGroup}>
                    <button
                      className={`${styles.disciplinaHeader} ${expandido ? styles.disciplinaHeaderOpen : ""}`}
                      onClick={() => toggleDisciplina(disc)}
                    >
                      <div className={styles.disciplinaLeft}>
                        {expandido ? (
                          <ChevronDown size={16} />
                        ) : (
                          <ChevronRight size={16} />
                        )}
                        <span className={styles.disciplinaNome}>{disc}</span>
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
                          const isSel = selecionados.some((s) => s.id === m.id);
                          return (
                            <label
                              key={m.id}
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

        {/* Footer */}
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
                  <span key={s.id} className={styles.confirmModuloTag}>
                    {s.disciplina} · {s.modulo}
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
