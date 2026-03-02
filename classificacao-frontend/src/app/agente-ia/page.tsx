"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import AppLayout from "@/components/AppLayout";
import { apiRequest, API_BASE_URL } from "@/lib/api";
import { getUsuario } from "@/lib/auth";
import { showToast } from "@/components/Toast";
import {
  Play,
  RotateCcw,
  Activity,
  Terminal as TerminalIcon,
  Search,
  Eye,
  X,
} from "lucide-react";
import Dropdown from "@/components/Dropdown";
import styles from "./AgenteIA.module.css";

interface LogLine {
  time: string;
  level: string;
  message: string;
  type: "info" | "success" | "error" | "warning";
}

interface WorkerStatus {
  job_id: string | null;
  status: "idle" | "running" | "stopping" | "stopped" | "completed" | "error";
  limit: number;
  workers_requested: number;
  workers_active: number;
  total: number;
  processed: number;
  sucesso: number;
  erros: number;
  queue_remaining: number;
  total_tokens: number;
  total_cost: number;
  last_questao_id: number | null;
  last_error: string | null;
  logs: Array<{ time: string; level: string; message: string }>;
}

interface ClassificacaoItem {
  questao_id: number;
  modulos_sugeridos: string[];
  disciplina: string;
  confianca_media: number;
  modelo_utilizado: string;
  usou_llm: boolean;
  tem_justificativa: boolean;
  created_at: string;
}

interface ClassificacaoDetail {
  questao_id: number;
  enunciado: string;
  enunciado_html?: string;
  texto_base_html?: string | null;
  has_images?: boolean;
  alternativas?: Array<{
    ordem: number;
    conteudo_html: string;
    conteudo: string;
  }>;
  disciplina: string;
  habilidade_trieduc: any;
  ia: {
    modulos_sugeridos: string[];
    justificativas: Record<string, string>;
    modulos_possiveis: string[];
    assuntos_sugeridos: Record<string, string>;
    analise_imagem?: string | null;
    confianca_media: number;
    modelo_utilizado: string;
    usou_llm: boolean;
  };
  manual: {
    modulos: string[];
    descricoes: string[];
  } | null;
  comparacao: {
    match_status: "exact" | "partial" | "none";
    modulos_extra: string[];
    modulos_faltando: string[];
  } | null;
}

export default function AgenteIAPage() {
  const router = useRouter();
  const [isAuthorized, setIsAuthorized] = useState(false);

  useEffect(() => {
    const usuario = getUsuario();
    if (!usuario || !usuario.is_admin) {
      router.push("/classificar");
    } else {
      setIsAuthorized(true);
    }
  }, [router]);

  const [logs, setLogs] = useState<LogLine[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [workerCount, setWorkerCount] = useState<number>(2);
  const [workerStatus, setWorkerStatus] = useState<WorkerStatus | null>(null);

  // Filtros
  const [filterModelo, setFilterModelo] = useState<string>("");
  const [filterDisciplina, setFilterDisciplina] = useState<string>("");
  const [filterMatch, setFilterMatch] = useState<string>("");

  // Lista de classificações
  const [classificacoes, setClassificacoes] = useState<ClassificacaoItem[]>([]);
  const [disciplinaOptions, setDisciplinaOptions] = useState<
    { value: string; label: string }[]
  >([]);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalItems, setTotalItems] = useState(0);
  const [loadingList, setLoadingList] = useState(false);

  // Modal
  const [selectedQid, setSelectedQid] = useState<number | null>(null);
  const [detailData, setDetailData] = useState<ClassificacaoDetail | null>(
    null,
  );
  const [loadingDetail, setLoadingDetail] = useState(false);

  const logEndRef = useRef<HTMLDivElement>(null);
  const lastWorkerStatusRef = useRef<string>("idle");

  useEffect(() => {
    async function loadDisciplinas() {
      try {
        const data = await apiRequest("/disciplinas");
        const options = Object.values(data.areas)
          .flat()
          .map((d: any) => ({ value: d, label: d }));
        setDisciplinaOptions(options);
      } catch (err) {
        console.error(err);
      }
    }
    loadDisciplinas();
  }, []);

  useEffect(() => {
    fetchClassificacoes(page);
  }, [page, filterModelo, filterDisciplina, filterMatch]);

  const addLog = (message: string, type: LogLine["type"] = "info") => {
    const time = new Date().toLocaleTimeString();
    setLogs((prev) => [
      ...prev.slice(-200),
      { time, level: type.toUpperCase(), message, type },
    ]); // maintain last 200 logs
  };

  const fetchClassificacoes = async (pageNum: number) => {
    setLoadingList(true);
    try {
      const baseUrlIA = API_BASE_URL.replace(
        "/classificacao",
        "/classificacao-ia",
      );
      const data = await apiRequest(
        `/classificacoes?page=${pageNum}&per_page=15&modelo_filter=${filterModelo}&disciplina_filter=${filterDisciplina}&match_filter=${filterMatch}`,
        { customBaseUrl: baseUrlIA },
      );
      setClassificacoes(data.items || []);
      setTotalPages(data.pages || 1);
      setTotalItems(data.total || 0);
    } catch (err: any) {
      console.error("Erro ao buscar lista:", err);
    } finally {
      setLoadingList(false);
    }
  };

  const openDetailModal = async (questao_id: number) => {
    setSelectedQid(questao_id);
    setLoadingDetail(true);
    setDetailData(null);
    try {
      const baseUrlIA = API_BASE_URL.replace(
        "/classificacao",
        "/classificacao-ia",
      );
      const data = await apiRequest(`/classificacao/${questao_id}`, {
        customBaseUrl: baseUrlIA,
      });
      setDetailData(data);
    } catch (err: any) {
      showToast("Erro ao carregar detalhes", "error");
      setSelectedQid(null);
    } finally {
      setLoadingDetail(false);
    }
  };

  const syncWorkerStatus = async (showErrors = false) => {
    try {
      const baseUrlIA = API_BASE_URL.replace(
        "/classificacao",
        "/classificacao-ia",
      );
      const data: WorkerStatus = await apiRequest("/validar-workers/status", {
        customBaseUrl: baseUrlIA,
      });
      setWorkerStatus(data);

      const mappedLogs: LogLine[] = (data.logs || []).map((l) => {
        const lvl = (l.level || "info").toLowerCase();
        const type: LogLine["type"] =
          lvl === "success"
            ? "success"
            : lvl === "error"
              ? "error"
              : lvl === "warning"
                ? "warning"
                : "info";
        return {
          time: l.time || new Date().toLocaleTimeString(),
          level: (l.level || "INFO").toUpperCase(),
          message: l.message || "",
          type,
        };
      });
      setLogs(mappedLogs.slice(-200));

      const running = data.status === "running" || data.status === "stopping";
      const previous = lastWorkerStatusRef.current;
      setIsRunning(running);
      lastWorkerStatusRef.current = data.status;

      if ((previous === "running" || previous === "stopping") && !running) {
        fetchClassificacoes(1);
        if (data.status === "completed")
          showToast("Classificacao concluida", "success");
        if (data.status === "stopped")
          showToast("Classificacao interrompida", "warning");
        if (data.status === "error")
          showToast("Classificacao finalizada com erro", "error");
      }
    } catch (err) {
      if (showErrors)
        console.error("Erro ao sincronizar status do worker:", err);
    }
  };

  const handleStartValidation = async () => {
    if (isRunning) return;
    try {
      const baseUrlIA = API_BASE_URL.replace(
        "/classificacao",
        "/classificacao-ia",
      );
      await apiRequest(
        `/validar-workers/start?limit=5000&workers=${workerCount}&prepare_lote_before_run=true&reset_before_run=true`,
        {
          method: "POST",
          customBaseUrl: baseUrlIA,
        },
      );
      showToast(
        `Classificacao iniciada com ${workerCount} worker(s)`,
        "success",
      );
      await syncWorkerStatus(true);
    } catch (err) {
      console.error("Erro ao iniciar classificacao paralela:", err);
    }
  };

  const stopValidation = async () => {
    try {
      const baseUrlIA = API_BASE_URL.replace(
        "/classificacao",
        "/classificacao-ia",
      );
      await apiRequest("/validar-workers/stop", {
        method: "POST",
        customBaseUrl: baseUrlIA,
      });
      showToast("Parada solicitada", "warning");
      await syncWorkerStatus(true);
    } catch (e) {
      console.error("Erro ao solicitar parada:", e);
    }
  };

  const handleReloadPrompts = async () => {
    try {
      const baseUrlIA = API_BASE_URL.replace(
        "/classificacao",
        "/classificacao-ia",
      );
      const res = await apiRequest("/reload-prompts", {
        method: "POST",
        customBaseUrl: baseUrlIA,
      });
      showToast(res.message, "success");
      addLog("Prompts recarregados com sucesso.", "success");
    } catch (err: any) {
      showToast(err.message, "error");
      addLog("Erro ao recarregar prompts: " + err.message, "error");
    }
  };

  // Verificação única ao entrar na página (sincroniza estado inicial do worker)
  useEffect(() => {
    if (!isAuthorized) return;
    syncWorkerStatus(false);
  }, [isAuthorized]);

  // Polling de 3s apenas enquanto o worker estiver rodando (running ou stopping)
  useEffect(() => {
    if (!isAuthorized || !isRunning) return;
    const timer = setInterval(() => {
      syncWorkerStatus(false);
    }, 3000);
    return () => clearInterval(timer);
  }, [isAuthorized, isRunning]);

  if (!isAuthorized) return null;

  return (
    <AppLayout>
      <div className={styles.container}>
        <div className={styles.header}>
          <div>
            <h1>Agente de IA</h1>
            <p>
              Validação massiva do modelo LLM e histórico de classificações.
            </p>
          </div>

          <div className={styles.actions}>
            <button
              className={styles.btnSecondary}
              onClick={handleReloadPrompts}
            >
              <RotateCcw size={18} />
              Recarregar Prompts
            </button>
            {!isRunning && (
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.5rem",
                  fontSize: "0.85rem",
                }}
              >
                Workers
                <select
                  value={workerCount}
                  onChange={(e) => setWorkerCount(Number(e.target.value))}
                  style={{
                    padding: "0.5rem",
                    borderRadius: "8px",
                    border: "1px solid #ddd",
                  }}
                >
                  {[1, 2, 3, 4, 6, 8].map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {!isRunning ? (
              <button
                className={styles.btnPrimary}
                onClick={handleStartValidation}
              >
                <Play size={18} />
                Iniciar Classificacao
              </button>
            ) : (
              <button
                className={styles.btnPrimary}
                style={{ backgroundColor: "#f44336" }}
                onClick={stopValidation}
              >
                Parar Validacao
              </button>
            )}
          </div>
        </div>

        <div className={styles.grid}>
          <div className={styles.card}>
            <h3>
              <TerminalIcon size={20} />
              Console da Classificacao (Workers)
            </h3>
            <div
              style={{
                marginBottom: "0.75rem",
                fontSize: "0.85rem",
                opacity: 0.9,
              }}
            >
              <strong>Status:</strong> {workerStatus?.status || "idle"} |{" "}
              <strong>Progresso:</strong> {workerStatus?.processed || 0}/
              {workerStatus?.total || 0} | <strong>Sucesso:</strong>{" "}
              {workerStatus?.sucesso || 0} | <strong>Erros:</strong>{" "}
              {workerStatus?.erros || 0} | <strong>Workers ativos:</strong>{" "}
              {workerStatus?.workers_active || 0}
            </div>
            <div className={styles.logContainer}>
              {logs.length === 0 && (
                <div className={styles.logEntry} style={{ opacity: 0.5 }}>
                  Aguardando execucao de classificacao...
                </div>
              )}
              {logs.map((log, i) => (
                <div key={i} className={styles.logEntry}>
                  <span style={{ color: "#888" }}>[{log.time}]</span>{" "}
                  <span
                    className={
                      styles[
                        `log${log.type.charAt(0).toUpperCase() + log.type.slice(1)}`
                      ]
                    }
                  >
                    {log.level}:
                  </span>{" "}
                  {log.message}
                </div>
              ))}
              <div ref={logEndRef} />
            </div>
          </div>

          <div className={styles.card}>
            <h3>Últimas Classificações ({totalItems})</h3>

            {/* Filtros */}
            <div
              style={{
                display: "flex",
                gap: "1rem",
                marginBottom: "1.5rem",
                flexWrap: "wrap",
                zIndex: 10,
              }}
            >
              <div style={{ minWidth: "220px" }}>
                <Dropdown
                  label="Filtro de Modelo Web"
                  options={[
                    { value: "", label: "Todos os Modelos" },
                    { value: "gpt-5.2", label: "GPT-5.2" },
                    // { value: 'gpt-4o', label: 'GPT-4o (Vision)' },
                    { value: "gpt-4o-mini", label: "GPT-4o-mini" },
                    { value: "logistic_regression", label: "Embeddings" },
                  ]}
                  value={filterModelo}
                  onChange={(v: any) => {
                    setFilterModelo(v);
                    setPage(1);
                  }}
                  placeholder="Todos os Modelos"
                />
              </div>

              <div style={{ minWidth: "220px" }}>
                <Dropdown
                  label="Status do Match"
                  options={[
                    { value: "", label: "Qualquer Status" },
                    { value: "exact", label: "Match Exato" },
                    { value: "partial", label: "Match Parcial" },
                    { value: "none", label: "Sem Match" },
                    { value: "pending", label: "Não classificada manual" },
                  ]}
                  value={filterMatch}
                  onChange={(v: any) => {
                    setFilterMatch(v);
                    setPage(1);
                  }}
                  placeholder="Qualquer Status"
                />
              </div>

              <div style={{ minWidth: "220px" }}>
                <Dropdown
                  label="Disciplina"
                  options={[
                    { value: "", label: "Todas as Disciplinas" },
                    ...disciplinaOptions,
                  ]}
                  value={filterDisciplina}
                  onChange={(v: any) => {
                    setFilterDisciplina(v);
                    setPage(1);
                  }}
                  placeholder="Todas as Disciplinas"
                  searchable={true}
                />
              </div>
            </div>

            <div className={styles.tableContainer}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>QID</th>
                    <th>Disciplina</th>
                    <th>Classificação da IA</th>
                    <th>Modelo</th>
                    <th>Data</th>
                  </tr>
                </thead>
                <tbody>
                  {loadingList ? (
                    <tr>
                      <td
                        colSpan={5}
                        style={{ textAlign: "center", opacity: 0.5 }}
                      >
                        Carregando...
                      </td>
                    </tr>
                  ) : classificacoes.length === 0 ? (
                    <tr>
                      <td
                        colSpan={5}
                        style={{ textAlign: "center", opacity: 0.5 }}
                      >
                        Nenhuma classificação encontrada.
                      </td>
                    </tr>
                  ) : (
                    classificacoes.map((item) => (
                      <tr
                        key={item.questao_id}
                        className={styles.tableBodyRow}
                        onClick={() => openDetailModal(item.questao_id)}
                      >
                        <td>
                          <strong>{item.questao_id}</strong>
                        </td>
                        <td>{item.disciplina || "-"}</td>
                        <td>
                          <div
                            style={{
                              display: "flex",
                              flexDirection: "column",
                              gap: "4px",
                            }}
                          >
                            {item.modulos_sugeridos?.map((m) => (
                              <span key={m} style={{ fontSize: "0.8rem" }}>
                                • {m}
                              </span>
                            )) || "-"}
                          </div>
                        </td>
                        <td>
                          <span
                            className={`${styles.badge} ${item.usou_llm ? styles.badgeLLM : styles.badgeLegacy}`}
                          >
                            {item.modelo_utilizado}
                          </span>
                        </td>
                        <td>
                          {item.created_at
                            ? new Date(item.created_at).toLocaleString()
                            : "-"}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>

              {totalPages > 1 && (
                <div className={styles.pagination}>
                  <span>
                    Página {page} de {totalPages}
                  </span>
                  <div className={styles.pageControls}>
                    <button
                      className={styles.pageBtn}
                      disabled={page === 1}
                      onClick={() => setPage((p) => p - 1)}
                    >
                      Anterior
                    </button>
                    <button
                      className={styles.pageBtn}
                      disabled={page === totalPages}
                      onClick={() => setPage((p) => p + 1)}
                    >
                      Próxima
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Modal de Detalhes */}
        {selectedQid !== null && (
          <div
            className={styles.modalOverlay}
            onClick={() => setSelectedQid(null)}
          >
            <div
              className={styles.modalContent}
              onClick={(e) => e.stopPropagation()}
            >
              <button
                className={styles.closeButton}
                onClick={() => setSelectedQid(null)}
              >
                <X size={24} />
              </button>

              <h2>Detalhes da Questão: {selectedQid}</h2>
              <p style={{ color: "var(--text-muted)" }}>
                Classificação e Justificativas da IA.
              </p>

              {loadingDetail ? (
                <p style={{ marginTop: "2rem" }}>
                  Carregando dados da questão...
                </p>
              ) : detailData ? (
                <>
                  <div className={styles.statsGrid}>
                    <div className={styles.statCard}>
                      <span className={styles.statValue}>
                        {detailData.disciplina || "-"}
                      </span>
                      <span className={styles.statLabel}>Disciplina</span>
                    </div>
                    <div className={styles.statCard}>
                      <span className={styles.statValue}>
                        <span
                          className={`${styles.badge} ${detailData.ia?.usou_llm ? styles.badgeLLM : styles.badgeLegacy}`}
                        >
                          {detailData.ia?.modelo_utilizado}
                        </span>
                      </span>
                      <span className={styles.statLabel}>Modelo Utilizado</span>
                    </div>
                    {detailData.comparacao && (
                      <div className={styles.statCard}>
                        <span className={styles.statValue}>
                          <span
                            className={`${styles.badge} ${
                              detailData.comparacao.match_status === "exact"
                                ? styles.badgeMatchExact
                                : detailData.comparacao.match_status ===
                                    "partial"
                                  ? styles.badgeMatchPartial
                                  : styles.badgeMatchNone
                            }`}
                          >
                            {detailData.comparacao.match_status === "exact"
                              ? "MATCH EXATO"
                              : detailData.comparacao.match_status === "partial"
                                ? "PARCIAL"
                                : "SEM MATCH"}
                          </span>
                        </span>
                        <span className={styles.statLabel}>
                          Comparação com Manual
                        </span>
                      </div>
                    )}
                  </div>

                  <div className={styles.detailSection}>
                    <h4>Classificação do Professor (Manual)</h4>
                    {detailData.manual &&
                    detailData.manual.modulos?.length > 0 ? (
                      <div
                        style={{
                          display: "flex",
                          flexDirection: "column",
                          gap: "0.5rem",
                        }}
                      >
                        {detailData.manual.modulos.map((mod, i) => (
                          <div key={i} className={styles.justificativaBox}>
                            <strong>[{mod}]</strong>
                            {detailData.manual?.descricoes?.[i] ? (
                              <div
                                style={{
                                  marginTop: "0.25rem",
                                  fontSize: "0.85rem",
                                  opacity: 0.9,
                                }}
                              >
                                {detailData.manual.descricoes[i]}
                              </div>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p>Sem classificação manual</p>
                    )}
                  </div>

                  <div className={styles.detailSection}>
                    <h4>Classificação da IA</h4>
                    <p>
                      Módulos sugeridos:{" "}
                      <strong>
                        {detailData.ia?.modulos_sugeridos?.join(", ") || "-"}
                      </strong>
                    </p>

                    {detailData.ia?.modulos_sugeridos &&
                      detailData.ia.modulos_sugeridos.length > 0 && (
                        <div
                          style={{
                            marginTop: "1rem",
                            display: "flex",
                            flexDirection: "column",
                            gap: "0.5rem",
                          }}
                        >
                          {detailData.ia.modulos_sugeridos.map((mod) => (
                            <div key={mod} className={styles.justificativaBox}>
                              <div style={{ marginBottom: "0.5rem" }}>
                                <strong>[{mod}]</strong>
                                {detailData.ia?.assuntos_sugeridos?.[mod] && (
                                  <span
                                    style={{
                                      fontSize: "0.85rem",
                                      opacity: 0.9,
                                      marginLeft: "0.5rem",
                                    }}
                                  >
                                    - {detailData.ia.assuntos_sugeridos[mod]}
                                  </span>
                                )}
                              </div>
                              {detailData.ia?.justificativas?.[mod] && (
                                <div>
                                  <strong>Justificativa da IA:</strong>{" "}
                                  {detailData.ia.justificativas[mod] as string}
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                  </div>

                  <div className={styles.detailSection}>
                    <h4>Enunciado da Questão</h4>
                    {detailData.texto_base_html && (
                      <div
                        className={styles.textoBaseBox}
                        dangerouslySetInnerHTML={{
                          __html: detailData.texto_base_html,
                        }}
                      />
                    )}
                    <div
                      className={styles.enunciadoBox}
                      dangerouslySetInnerHTML={{
                        __html:
                          detailData.enunciado_html ||
                          detailData.enunciado ||
                          "Enunciado não disponível",
                      }}
                    />

                    {detailData.has_images && (
                      <div className={styles.imageAnalysisBox}>
                        <h5>Análise da IA sobre a imagem</h5>
                        <p>
                          {detailData.ia?.analise_imagem ||
                            "A IA recebeu imagem, mas não retornou descrição específica."}
                        </p>
                      </div>
                    )}

                    {detailData.alternativas &&
                      detailData.alternativas.length > 0 && (
                        <div className={styles.alternativasBox}>
                          <h5>Alternativas</h5>
                          <div className={styles.alternativasList}>
                            {detailData.alternativas.map((alt) => (
                              <div
                                key={`${alt.ordem}-${alt.conteudo}`}
                                className={styles.alternativaItem}
                              >
                                <span className={styles.altLabel}>
                                  {alt.ordem})
                                </span>
                                <div
                                  className={styles.altConteudo}
                                  dangerouslySetInnerHTML={{
                                    __html: alt.conteudo_html || alt.conteudo,
                                  }}
                                />
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                  </div>
                </>
              ) : (
                <p style={{ marginTop: "2rem", color: "#ff6c6b" }}>
                  Erro ao carregar detalhes.
                </p>
              )}
            </div>
          </div>
        )}
      </div>
    </AppLayout>
  );
}
