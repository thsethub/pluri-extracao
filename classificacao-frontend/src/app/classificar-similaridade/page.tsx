"use client";

import { useState, useEffect, useCallback } from "react";
import {
  getAssuntosSuperpro,
  getAssuntosSuproConfirmacoes,
  getProximaAltaSimilaridade,
  getProximaConfirmacao,
  getContagemFilas,
  apiRequest,
} from "@/lib/api";
import { pickRenderableHtml } from "@/lib/html-content";
import AppLayout from "@/components/AppLayout";
import Dropdown from "@/components/Dropdown";
import ClassificarLivroModal, {
  ItemSelecionado,
} from "@/components/ClassificarLivroModal";
import { getUsuario } from "@/lib/auth";
import {
  CheckCircle,
  FastForward,
  Bot,
  Zap,
  RefreshCw,
  History,
} from "lucide-react";
import styles from "../classificar/Classificar.module.css";
import filterStyles from "@/components/FilterBar.module.css";

type Tab = "alta-similaridade" | "confirmacoes";

const AREAS_DISCIPLINAS: Record<string, string[]> = {
  Humanas: ["Filosofia", "Geografia", "História", "Sociologia"],
  Linguagens: [
    "Artes",
    "Educação Física",
    "Espanhol",
    "Língua Inglesa",
    "Língua Portuguesa",
    "Literatura",
    "Redação",
  ],
  Matemática: ["Matemática"],
  Natureza: ["Biologia", "Ciências", "Física", "Natureza e Sociedade", "Química"],
};

function MatchBadge({ score }: { score: number | null | undefined }) {
  if (score == null) return null;
  const pct = Math.round(score * 100);
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "0.3rem",
        padding: "0.3rem 0.7rem",
        borderRadius: "12px",
        fontSize: "0.8rem",
        fontWeight: 700,
        color: "#166534",
        background: "#bbf7d0",
        border: "1.5px solid #4ade80",
      }}
    >
      <Zap size={12} />
      {pct}%
    </span>
  );
}

export default function ClassificarSimilaridadePage() {
  const usuario = getUsuario();
  const [tab, setTab] = useState<Tab>("alta-similaridade");

  // Filtros
  const [area, setArea] = useState(usuario?.disciplina || "");
  const [disciplina, setDisciplina] = useState("");
  const [assuntoSuperpro, setAssuntoSuperpro] = useState("");
  const [assuntosDisponiveis, setAssuntosDisponiveis] = useState<
    { assunto: string; total: number }[]
  >([]);
  const [loadingAssuntos, setLoadingAssuntos] = useState(false);
  const [contagem, setContagem] = useState<{
    alta_similaridade: Record<string, number>;
    confirmacoes: Record<string, number>;
  }>({ alta_similaridade: {}, confirmacoes: {} });

  // Questão atual
  const [questao, setQuestao] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Ações
  const [saving, setSaving] = useState(false);
  const [showModal, setShowModal] = useState(false);

  // Carrega contagem por disciplina ao montar
  useEffect(() => {
    getContagemFilas()
      .then(setContagem)
      .catch(() => {});
  }, []);

  const contagemAtual = tab === "alta-similaridade"
    ? contagem.alta_similaridade
    : contagem.confirmacoes;

  // Dropdown de áreas e disciplinas com contagem
  const areaOptions = Object.keys(AREAS_DISCIPLINAS)
    .filter((a) => !usuario?.disciplina || usuario.is_admin || a === area)
    .map((a) => {
      const total = (AREAS_DISCIPLINAS[a] || []).reduce(
        (sum, d) => sum + (contagemAtual[d] ?? 0),
        0,
      );
      return { value: a, label: total > 0 ? `${a} (${total})` : a, _total: total };
    })
    .filter((a) => tab !== "confirmacoes" || a._total > 0)
    .map(({ value, label }) => ({ value, label }));

  const disciplinaOptions = (area ? AREAS_DISCIPLINAS[area] || [] : [])
    .filter((d) => tab !== "confirmacoes" || (contagemAtual[d] ?? 0) > 0)
    .map((d) => {
      const n = contagemAtual[d];
      return { value: d, label: n != null ? `${d} (${n})` : d };
    });

  // Carrega assuntos superpro quando disciplina ou aba muda
  useEffect(() => {
    setAssuntoSuperpro("");
    setAssuntosDisponiveis([]);
    if (!disciplina) return;

    let cancelled = false;
    setLoadingAssuntos(true);
    const fetchFn =
      tab === "alta-similaridade"
        ? getAssuntosSuperpro(disciplina)
        : getAssuntosSuproConfirmacoes(disciplina);

    fetchFn
      .then((data: { assunto: string; total: number }[]) => {
        if (!cancelled) setAssuntosDisponiveis(data || []);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoadingAssuntos(false);
      });
    return () => {
      cancelled = true;
    };
  }, [disciplina, tab]);

  const fetchProxima = useCallback(async () => {
    setLoading(true);
    setError("");
    setQuestao(null);

    try {
      let data: any;
      if (tab === "alta-similaridade") {
        data = await getProximaAltaSimilaridade({
          assuntoSuperpro: assuntoSuperpro || undefined,
          disciplinaId: disciplina || undefined,
        });
      } else {
        data = await getProximaConfirmacao({
          disciplinaId: disciplina || undefined,
          assuntoSuperpro: assuntoSuperpro || undefined,
        });
      }
      setQuestao(data);
    } catch (err: any) {
      setError(
        err.message ||
          (tab === "alta-similaridade"
            ? "Nenhuma questão de alta similaridade pendente."
            : "Nenhuma questão de confirmação pendente."),
      );
    } finally {
      setLoading(false);
    }
  }, [tab, disciplina, assuntoSuperpro]);

  // Busca ao trocar filtros ou aba
  useEffect(() => {
    fetchProxima();
  }, [fetchProxima]);

  const handleSalvar = async (selecionados: ItemSelecionado[]) => {
    if (selecionados.length === 0 || !questao) return;
    setSaving(true);
    try {
      await apiRequest("/salvar", {
        method: "POST",
        body: JSON.stringify({
          questao_id: questao.id,
          modulos_escolhidos: selecionados.map((s) => s.modulo),
          descricoes_assunto: selecionados.map((s) => s.assunto),
          classificacoes_trieduc: selecionados.map((s) => s.disciplina),
          modulo_escolhido: selecionados[0]?.modulo,
          descricao_assunto: selecionados[0]?.assunto,
          classificacao_trieduc: "LibroStudio",
          tipo_acao: "classificacao_libro",
        }),
      });
      setShowModal(false);
      fetchProxima();
    } catch (err: any) {
      alert(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handlePular = async () => {
    if (!questao) return;
    setSaving(true);
    try {
      await apiRequest("/pular", {
        method: "POST",
        body: JSON.stringify({ questao_id: questao.id }),
      });
      fetchProxima();
    } catch (err: any) {
      alert(err.message);
    } finally {
      setSaving(false);
    }
  };

  const textoBaseHtml = pickRenderableHtml(
    questao?.texto_base_html,
    questao?.texto_base,
  );
  const enunciadoHtml = pickRenderableHtml(
    questao?.enunciado_html,
    questao?.enunciado,
  );
  const assuntosExtracao: string[] = questao?.classificacao_extracao || [];

  return (
    <AppLayout>
      <div className={styles.header}>
        <div className={styles.headerInfo}>
          <h1>Classificar — Alta Similaridade</h1>
          <p>Questões com similaridade ≥ 80% e confirmações sem módulos libro</p>
        </div>
      </div>

      {/* Tabs + badge total */}
      <div className="tab-bar">
        <div className="tab-btns">
          <button
            className={`tab-btn ${tab === "alta-similaridade" ? "active" : ""}`}
            onClick={() => {
              setTab("alta-similaridade");
              setDisciplina("");
              setAssuntoSuperpro("");
            }}
          >
            <Zap size={15} />
            Alta Similaridade
          </button>
          <button
            className={`tab-btn ${tab === "confirmacoes" ? "active" : ""}`}
            onClick={() => {
              setTab("confirmacoes");
              setDisciplina("");
              setAssuntoSuperpro("");
            }}
          >
            <History size={15} />
            Confirmações
          </button>
        </div>
        {(() => {
          const total = Object.values(contagemAtual).reduce((a, b) => a + b, 0);
          return (
            <div className="fila-stat-badge">
              <span className="fila-stat-label">
                {tab === "alta-similaridade" ? "Alta Similaridade" : "Confirmações"}
              </span>
              <span className="fila-stat-value">{total.toLocaleString("pt-BR")}</span>
              <span className="fila-stat-sub">pendentes</span>
            </div>
          );
        })()}
      </div>

      {/* Filtros */}
      <div className={`${filterStyles.filterBar} glass fade-in`}>
        <div className={filterStyles.filterGroup}>
          <Dropdown
            label="Sua Área de Atuação"
            options={areaOptions}
            value={area}
            onChange={(val: string) => {
              if (!usuario?.disciplina || usuario.is_admin) {
                setArea(val);
                setDisciplina("");
                setAssuntoSuperpro("");
              }
            }}
            placeholder="Selecione a Área"
          />
        </div>
        <div className={filterStyles.filterGroup}>
          <Dropdown
            label="Filtrar por Disciplina"
            options={disciplinaOptions}
            value={disciplina}
            onChange={(val: string) => {
              setDisciplina(val);
              setAssuntoSuperpro("");
            }}
            placeholder="Todas as disciplinas da área"
          />
        </div>
        <div className={filterStyles.filterGroup}>
          <Dropdown
            label="Assunto SuperProfessor"
            options={assuntosDisponiveis.map((a) => ({
              value: a.assunto,
              label: `${a.assunto} (${a.total})`,
            }))}
            value={assuntoSuperpro}
            onChange={(val: string) => setAssuntoSuperpro(val)}
            placeholder={
              loadingAssuntos
                ? "Carregando assuntos..."
                : disciplina
                  ? "Todos os assuntos"
                  : "Selecione a disciplina primeiro"
            }
            disabled={!disciplina || loadingAssuntos}
            searchable={true}
          />
        </div>
        <div className={filterStyles.info}>
          <p><strong>{area || "Área não definida"}</strong></p>
          <span>{disciplina || "Todas as disciplinas"}</span>
        </div>
      </div>

      {/* Conteúdo */}
      {loading ? (
        <div className={styles.loading}>
          <div className={styles.spinner} />
          <span>Buscando questão...</span>
        </div>
      ) : error ? (
        <div className={styles.empty}>
          <CheckCircle size={48} color="var(--success)" />
          <p>{error}</p>
          <button onClick={fetchProxima}>
            <RefreshCw size={16} /> Tentar Novamente
          </button>
        </div>
      ) : (
        questao && (
          <div className={styles.content}>
            {/* Card da questão */}
            <div className={`${styles.questaoCard} glass fade-in`}>
              <div className={styles.questaoMeta}>
                <span className={styles.tag}>{questao.disciplina_nome}</span>
                {questao.habilidade_descricao && (
                  <span className={styles.habTag}>
                    {questao.habilidade_descricao}
                  </span>
                )}
                <span className={styles.idTag}>ID: {questao.id}</span>
                {questao.similaridade != null && (
                  <MatchBadge score={questao.similaridade} />
                )}
              </div>

              {textoBaseHtml && (
                <div
                  className={styles.textoBase}
                  dangerouslySetInnerHTML={{ __html: textoBaseHtml }}
                />
              )}

              <div
                className={styles.enunciado}
                dangerouslySetInnerHTML={{ __html: enunciadoHtml }}
              />

              {questao.alternativas?.length > 0 && (
                <div className={styles.alternativas}>
                  {questao.alternativas.map((alt: any, i: number) => (
                    <div
                      key={i}
                      className={`${styles.altItem} ${alt.correta ? styles.altCorreta : ""}`}
                    >
                      <span className={styles.altLetra}>
                        {String.fromCharCode(97 + i)})
                      </span>
                      <span
                        dangerouslySetInnerHTML={{
                          __html: pickRenderableHtml(
                            alt.conteudo_html,
                            alt.conteudo,
                          ),
                        }}
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Card de ação */}
            <div className={`${styles.moduloCard} glass fade-in`}>
              {/* Banner assuntos superpro */}
              {assuntosExtracao.length > 0 && (
                <div className="superpro-banner">
                  <div className="superpro-header">
                    <div className="superpro-title">
                      <Bot size={16} />
                      <strong>Classificação SuperProfessor</strong>
                    </div>
                    {questao.similaridade != null && (
                      <MatchBadge score={questao.similaridade} />
                    )}
                  </div>
                  <div className="superpro-tags">
                    {assuntosExtracao.map((tag: string, i: number) => (
                      <span key={i} className="superpro-tag">
                        {tag}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {tab === "confirmacoes" && (
                <div className="confirmacao-notice">
                  <History size={15} />
                  <span>
                    Esta questão foi confirmada anteriormente sem módulos libro.
                    Selecione os módulos agora.
                  </span>
                </div>
              )}

              <p className={styles.moduloHint}>
                Selecione os módulos libro que classificam esta questão.
              </p>

              <button
                className="classify-btn"
                onClick={() => setShowModal(true)}
                disabled={saving}
              >
                <CheckCircle size={18} />
                Classificar
              </button>

              <div className={styles.actionArea}>
                <div className={styles.buttons}>
                  <button
                    onClick={handlePular}
                    disabled={saving}
                    className={styles.skipBtn}
                  >
                    <FastForward size={18} />
                    Pular
                  </button>
                </div>
              </div>
            </div>
          </div>
        )
      )}

      <ClassificarLivroModal
        isOpen={showModal}
        onClose={() => setShowModal(false)}
        onConfirmar={handleSalvar}
        saving={saving}
      />

      <style jsx>{`
        .tab-bar {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-bottom: 1.2rem;
        }
        .tab-btns {
          display: flex;
          gap: 0.5rem;
        }
        .fila-stat-badge {
          display: flex;
          flex-direction: column;
          align-items: flex-end;
          padding: 0.55rem 1rem;
          background: var(--card-bg);
          border: 1.5px solid var(--border);
          border-radius: 10px;
          min-width: 120px;
        }
        .fila-stat-label {
          font-size: 0.68rem;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.5px;
          color: var(--text-muted);
        }
        .fila-stat-value {
          font-size: 1.4rem;
          font-weight: 800;
          color: var(--primary);
          line-height: 1.1;
        }
        .fila-stat-sub {
          font-size: 0.7rem;
          color: var(--text-muted);
        }
        .tab-btn {
          display: flex;
          align-items: center;
          gap: 0.4rem;
          padding: 0.55rem 1.1rem;
          border-radius: 10px;
          border: 1.5px solid var(--border);
          background: var(--card-bg);
          color: var(--text-secondary);
          font-size: 0.88rem;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.15s;
        }
        .tab-btn:hover {
          border-color: var(--primary);
          color: var(--primary);
        }
        .tab-btn.active {
          background: var(--primary);
          border-color: var(--primary);
          color: white;
        }
        .superpro-banner {
          background: linear-gradient(135deg, #eff6ff, #e0f0ff);
          border: 1px solid #bfdbfe;
          padding: 1rem 1.2rem;
          border-radius: 12px;
          margin-bottom: 1.2rem;
        }
        .superpro-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          font-size: 0.88rem;
          color: #1e40af;
          margin-bottom: 0.6rem;
        }
        .superpro-title {
          display: flex;
          align-items: center;
          gap: 0.5rem;
        }
        .superpro-tags {
          display: flex;
          flex-wrap: wrap;
          gap: 0.4rem;
        }
        .superpro-tag {
          background-color: #dbeafe;
          color: #1e3a8a;
          padding: 0.25rem 0.7rem;
          border-radius: 6px;
          font-size: 0.75rem;
          font-weight: 600;
          border: 1px solid #93c5fd60;
        }
        .confirmacao-notice {
          display: flex;
          align-items: center;
          gap: 0.5rem;
          background: #fef9c3;
          border: 1px solid #fde047;
          color: #713f12;
          padding: 0.65rem 1rem;
          border-radius: 10px;
          font-size: 0.85rem;
          margin-bottom: 1rem;
        }
        .classify-btn {
          width: 100%;
          background: var(--primary);
          color: white;
          border: none;
          padding: 0.9rem;
          border-radius: 10px;
          font-weight: 700;
          font-size: 0.95rem;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 0.6rem;
          cursor: pointer;
          transition: all 0.2s;
          box-shadow: 0 2px 8px rgba(99, 102, 241, 0.25);
          margin-bottom: 0.8rem;
        }
        .classify-btn:hover:not(:disabled) {
          background: var(--primary-dark, #4338ca);
          transform: translateY(-1px);
        }
        .classify-btn:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }
      `}</style>
    </AppLayout>
  );
}
