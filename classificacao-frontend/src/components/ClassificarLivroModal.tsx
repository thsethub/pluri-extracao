"use client";

import { useState, useEffect, useRef, useMemo } from "react";
import { createPortal } from "react-dom";
import {
  Search, X, ChevronDown, ChevronRight, CheckCircle, AlertTriangle,
} from "lucide-react";
import { apiRequest } from "@/lib/api";
import styles from "./CorrigirClassificacaoModal.module.css";

type Assunto = { assu_id: number; assunto: string };
type Modulo = { disc_modu_id: number; modulo: string; assuntos: Assunto[] };
type Disciplina = { disc_id: number; disciplina: string; modulos: Modulo[] };

export type ItemSelecionado = {
  disc_modu_id: number;
  modulo: string;
  assu_id: number;
  assunto: string;
  disciplina: string;
};

interface Props {
  isOpen: boolean;
  onClose: () => void;
  onConfirmar: (selecionados: ItemSelecionado[]) => void;
  saving?: boolean;
}

export default function ClassificarLivroModal({ isOpen, onClose, onConfirmar, saving = false }: Props) {
  const [dados, setDados] = useState<Disciplina[]>([]);
  const [loading, setLoading] = useState(false);
  const [busca, setBusca] = useState("");
  const [discExpandidas, setDiscExpandidas] = useState<Set<number>>(new Set());
  const [modExpandidos, setModExpandidos] = useState<Set<number>>(new Set());
  const [selecionados, setSelecionados] = useState<ItemSelecionado[]>([]);
  const [showConfirm, setShowConfirm] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    setBusca("");
    setSelecionados([]);
    setShowConfirm(false);
    setDiscExpandidas(new Set());
    setModExpandidos(new Set());
    if (dados.length === 0) fetchDados();
    setTimeout(() => searchRef.current?.focus(), 150);
  }, [isOpen]);

  const fetchDados = async () => {
    setLoading(true);
    try {
      const data: Disciplina[] = await apiRequest("/modulos-libro-direto");
      setDados(data || []);
    } catch {
      // tratado pelo apiRequest
    } finally {
      setLoading(false);
    }
  };

  const buscaLower = busca.toLowerCase().trim();

  const dadosFiltrados = useMemo(() => {
    if (!buscaLower) return dados;
    return dados
      .map((disc) => ({
        ...disc,
        modulos: disc.modulos
          .map((mod) => ({
            ...mod,
            assuntos: mod.assuntos.filter(
              (a) =>
                a.assunto.toLowerCase().includes(buscaLower) ||
                mod.modulo.toLowerCase().includes(buscaLower) ||
                disc.disciplina.toLowerCase().includes(buscaLower),
            ),
          }))
          .filter((mod) => mod.assuntos.length > 0),
      }))
      .filter((disc) => disc.modulos.length > 0);
  }, [dados, buscaLower]);

  const totalResultados = useMemo(
    () => dadosFiltrados.reduce((s, d) => s + d.modulos.reduce((sm, m) => sm + m.assuntos.length, 0), 0),
    [dadosFiltrados],
  );

  const isDiscExpandida = (discId: number) => buscaLower !== "" || discExpandidas.has(discId);
  const isModExpandido = (modId: number) => buscaLower !== "" || modExpandidos.has(modId);

  const toggleDisc = (discId: number) => {
    setDiscExpandidas((prev) => {
      const next = new Set(prev);
      next.has(discId) ? next.delete(discId) : next.add(discId);
      return next;
    });
  };

  const toggleMod = (modId: number) => {
    setModExpandidos((prev) => {
      const next = new Set(prev);
      next.has(modId) ? next.delete(modId) : next.add(modId);
      return next;
    });
  };

  const isSelecionado = (assuId: number) => selecionados.some((s) => s.assu_id === assuId);

  const toggleAssunto = (item: ItemSelecionado) => {
    setSelecionados((prev) => {
      const existe = prev.some((s) => s.assu_id === item.assu_id);
      return existe ? prev.filter((s) => s.assu_id !== item.assu_id) : [...prev, item];
    });
  };

  if (!isOpen) return null;

  return createPortal(
    <div className={styles.overlay} onClick={(e) => { if (e.target === e.currentTarget && !saving) onClose(); }}>
      <div className={styles.modal}>

        {/* Header */}
        <div className={styles.header}>
          <div className={styles.headerTitle}>
            <CheckCircle size={18} />
            <h2>Classificar</h2>
          </div>
          <button className={styles.closeBtn} onClick={onClose} disabled={saving} aria-label="Fechar">
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
              placeholder="Pesquisar por disciplina, módulo ou assunto"
              value={busca}
              onChange={(e) => setBusca(e.target.value)}
              className={styles.searchInput}
            />
            {busca && (
              <button className={styles.clearSearch} onClick={() => setBusca("")} aria-label="Limpar">
                <X size={14} />
              </button>
            )}
          </div>
          {buscaLower && (
            <span className={styles.searchResultCount}>
              {totalResultados} resultado{totalResultados !== 1 ? "s" : ""}
            </span>
          )}
        </div>

        {/* Selecionados */}
        {selecionados.length > 0 && (
          <div className={styles.selectedBanner}>
            <CheckCircle size={14} />
            <span>{selecionados.length} assunto{selecionados.length > 1 ? "s" : ""} selecionado{selecionados.length > 1 ? "s" : ""}:</span>
            <div className={styles.selectedTags}>
              {selecionados.map((s) => (
                <span key={s.assu_id} className={styles.selectedTag}>
                  {s.modulo} · {s.assunto}
                  <button onClick={() => toggleAssunto(s)} className={styles.removeTag} aria-label="Remover">
                    <X size={10} />
                  </button>
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Conteúdo */}
        <div className={styles.content}>
          {loading ? (
            <div className={styles.loadingState}>
              <div className={styles.spinner} />
              <span>Carregando módulos...</span>
            </div>
          ) : dadosFiltrados.length === 0 ? (
            <div className={styles.emptyState}>
              <Search size={32} />
              <p>Nenhum resultado para <strong>{busca || "a busca atual"}</strong></p>
            </div>
          ) : (
            <div className={styles.sectionsList}>
              {dadosFiltrados.map((disc) => {
                const discAberta = isDiscExpandida(disc.disc_id);
                const selNaDisc = disc.modulos.reduce(
                  (s, m) => s + m.assuntos.filter((a) => isSelecionado(a.assu_id)).length, 0
                );
                const totalAssuntosDisc = disc.modulos.reduce((s, m) => s + m.assuntos.length, 0);

                return (
                  <div key={disc.disc_id} className={styles.sectionBlock}>
                    {/* Nível 1: Disciplina */}
                    <button
                      className={`${styles.disciplinaHeader} ${discAberta ? styles.disciplinaHeaderOpen : ""}`}
                      style={{ background: "var(--surface)", borderRadius: "8px", marginBottom: "2px" }}
                      onClick={() => toggleDisc(disc.disc_id)}
                    >
                      <div className={styles.disciplinaLeft}>
                        {discAberta ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                        <span className={styles.disciplinaNome} style={{ fontWeight: 700 }}>
                          {disc.disciplina}
                        </span>
                      </div>
                      <div className={styles.disciplinaRight}>
                        <span className={styles.countBadge}>{totalAssuntosDisc}</span>
                        {selNaDisc > 0 && (
                          <span className={styles.selectedBadge}>
                            <CheckCircle size={11} />{selNaDisc}
                          </span>
                        )}
                      </div>
                    </button>

                    {/* Nível 2: Módulos */}
                    {discAberta && (
                      <div className={styles.disciplinasList} style={{ paddingLeft: "1rem" }}>
                        {disc.modulos.map((mod) => {
                          const modAberto = isModExpandido(mod.disc_modu_id);
                          const selNoMod = mod.assuntos.filter((a) => isSelecionado(a.assu_id)).length;

                          return (
                            <div key={mod.disc_modu_id} className={styles.disciplinaGroup}>
                              <button
                                className={`${styles.disciplinaHeader} ${modAberto ? styles.disciplinaHeaderOpen : ""}`}
                                onClick={() => toggleMod(mod.disc_modu_id)}
                              >
                                <div className={styles.disciplinaLeft}>
                                  {modAberto ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                                  <span className={styles.disciplinaNome}>{mod.modulo}</span>
                                </div>
                                <div className={styles.disciplinaRight}>
                                  <span className={styles.countBadge}>{mod.assuntos.length}</span>
                                  {selNoMod > 0 && (
                                    <span className={styles.selectedBadge}>
                                      <CheckCircle size={11} />{selNoMod}
                                    </span>
                                  )}
                                </div>
                              </button>

                              {/* Nível 3: Assuntos */}
                              {modAberto && (
                                <div className={styles.moduloList}>
                                  {mod.assuntos.map((a) => {
                                    const sel = isSelecionado(a.assu_id);
                                    return (
                                      <label
                                        key={a.assu_id}
                                        className={`${styles.moduloItem} ${sel ? styles.moduloSelected : ""}`}
                                      >
                                        <input
                                          type="checkbox"
                                          checked={sel}
                                          onChange={() =>
                                            toggleAssunto({
                                              disc_modu_id: mod.disc_modu_id,
                                              modulo: mod.modulo,
                                              assu_id: a.assu_id,
                                              assunto: a.assunto,
                                              disciplina: disc.disciplina,
                                            })
                                          }
                                        />
                                        <div className={styles.moduloText}>
                                          <strong>{a.assunto}</strong>
                                          <div className={styles.moduloMeta}>
                                            <span className={styles.libroTag}>LibroStudio</span>
                                            <span className={styles.areaTag}>{mod.modulo}</span>
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
            onClick={() => setShowConfirm(true)}
            disabled={selecionados.length === 0 || saving}
          >
            <CheckCircle size={16} />
            Confirmar seleção ({selecionados.length})
          </button>
        </div>
      </div>

      {/* Dialog de confirmação */}
      {showConfirm && (
        <div className={styles.confirmOverlay}>
          <div className={styles.confirmDialog}>
            <div className={styles.confirmIconWrap}><AlertTriangle size={28} /></div>
            <h3>Concluir classificação?</h3>
            <p>Tem certeza que gostaria de concluir esta classificação?</p>
            <div className={styles.confirmModulosList}>
              {selecionados.map((s) => (
                <span key={s.assu_id} className={styles.confirmModuloTag}>
                  {s.disciplina} · {s.modulo} · {s.assunto}
                </span>
              ))}
            </div>
            <div className={styles.confirmActions}>
              <button className={styles.confirmCancelBtn} onClick={() => setShowConfirm(false)} disabled={saving}>
                Cancelar
              </button>
              <button className={styles.confirmSaveBtn} onClick={() => onConfirmar(selecionados)} disabled={saving}>
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
