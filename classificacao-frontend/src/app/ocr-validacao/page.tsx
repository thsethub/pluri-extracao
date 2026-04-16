"use client";

import { useState } from "react";
import { Search } from "lucide-react";
import AppLayout from "@/components/AppLayout";
import OcrImageViewer from "@/components/OcrImageViewer";
import Dropdown from "@/components/Dropdown";
import { apiRequest } from "@/lib/api";
import styles from "./OcrValidacao.module.css";

interface RedacaoOcrConfianca {
  redacao_id: number;
  teste_prova_id: number | null;
  redacao_status_id: number;
  ocr_confianca: number | null;
  tema: string | null;
  redacao_texto: string | null;
  arquivo_anonimo_nome_armazenamento: string | null;
}

interface ApiResponse {
  data: RedacaoOcrConfianca[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

interface Filtros {
  teste_prova_id: string;
  ocr_confianca_min: string;
  ocr_confianca_max: string;
  redacao_status_id: string;
}

interface Respostas {
  pulou_trechos: boolean | null;
  trocou_palavras: boolean | null;
  trocou_letras: boolean | null;
}

const RESPOSTAS_INICIAIS: Respostas = {
  pulou_trechos: null,
  trocou_palavras: null,
  trocou_letras: null,
};

const STATUS_REDACAO = [
  { value: "4", label: "Corrigida" },
  { value: "10", label: "OCR inválido" },
  { value: "11", label: "Correção inválida" },
];

export default function OcrValidacaoPage() {
  const [filtros, setFiltros] = useState<Filtros>({
    teste_prova_id: "",
    ocr_confianca_min: "",
    ocr_confianca_max: "",
    redacao_status_id: "4", // obrigatório — default: Corrigida
  });

  const [filtrosAplicados, setFiltrosAplicados] = useState<Filtros | null>(
    null,
  );
  const [redacao, setRedacao] = useState<RedacaoOcrConfianca | null>(null);
  const [total, setTotal] = useState(0);
  const [paginaAtual, setPaginaAtual] = useState(1);
  const [respostas, setRespostas] = useState<Respostas>(RESPOSTAS_INICIAIS);
  const [loading, setLoading] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  const buscar = async (filtrosAtivos: Filtros, pagina: number) => {
    setLoading(true);
    setErro(null);

    try {
      const query = new URLSearchParams();
      query.set("teste_prova_id", filtrosAtivos.teste_prova_id);
      query.set("page", String(pagina));
      query.set("per_page", "1");

      // Clampeia entre 0 e 1 e aplica fallback se vazio
      const clampOcr = (val: string, fallback: number) => {
        const n = parseFloat(val);
        return String(isNaN(n) ? fallback : Math.min(1, Math.max(0, n)));
      };
      query.set(
        "ocr_confianca_min",
        clampOcr(filtrosAtivos.ocr_confianca_min, 0),
      );
      query.set(
        "ocr_confianca_max",
        clampOcr(filtrosAtivos.ocr_confianca_max, 1),
      );
      query.set("redacao_status_id", filtrosAtivos.redacao_status_id);

      const resp: ApiResponse = await apiRequest(
        `/ocr-confianca/redacoes?${query.toString()}`,
      );

      setTotal(resp.total);
      setPaginaAtual(pagina);
      setRedacao(resp.data[0] ?? null);
      setRespostas(RESPOSTAS_INICIAIS);

      if (resp.data.length === 0) {
        setErro("Nenhuma redação encontrada com os filtros informados.");
      }
    } catch {
      setRedacao(null);
    } finally {
      setLoading(false);
    }
  };

  const handleBuscar = () => {
    setFiltrosAplicados(filtros);
    buscar(filtros, 1);
  };

  const handlePular = async () => {
    if (!redacao || !filtrosAplicados) return;
    const clampOcr = (val: string, fallback: number) => {
      const n = parseFloat(val);
      return isNaN(n) ? fallback : Math.min(1, Math.max(0, n));
    };
    try {
      await apiRequest("/ocr-confianca/pular", {
        method: "POST",
        body: JSON.stringify({
          redacao_id: redacao.redacao_id,
          teste_prova_id: Number(filtrosAplicados.teste_prova_id),
          redacao_status_id: filtrosAplicados.redacao_status_id
            ? Number(filtrosAplicados.redacao_status_id)
            : null,
          ocr_confianca_min: clampOcr(filtrosAplicados.ocr_confianca_min, 0),
          ocr_confianca_max: clampOcr(filtrosAplicados.ocr_confianca_max, 1),
        }),
      });
    } catch {
      // ignora erro no pular
    }
    buscar(filtrosAplicados, 1);
  };

  const handleSalvar = async () => {
    if (!redacao || !todasRespondidas) return;
    setLoading(true);
    try {
      await apiRequest("/ocr-confianca/validacoes", {
        method: "POST",
        body: JSON.stringify({
          redacao_id: redacao.redacao_id,
          ocr_pulou_trechos: respostas.pulou_trechos,
          ocr_trocou_palavras: respostas.trocou_palavras,
          ocr_trocou_caracteres: respostas.trocou_letras,
        }),
      });
      // Avança automaticamente para a próxima redação
      if (filtrosAplicados) {
        buscar(filtrosAplicados, 1);
      }
    } catch {
      setErro("Erro ao salvar a validação.");
    } finally {
      setLoading(false);
    }
  };

  const setResposta = (campo: keyof Respostas, valor: boolean) => {
    setRespostas((prev) => ({ ...prev, [campo]: valor }));
  };

  const todasRespondidas =
    respostas.pulou_trechos !== null &&
    respostas.trocou_palavras !== null &&
    respostas.trocou_letras !== null;

  return (
    <AppLayout>
      <div className={styles.page}>
        <div className={styles.header}>
          <h1>OCR Validação</h1>
          <p>Avalie a qualidade do OCR nas redações de um teste de prova</p>
        </div>

        {/* FILTROS */}
        <section className={styles.filtrosCard}>
          <div className={styles.filtrosGrid}>
            <div className={styles.field}>
              <label>ID do Teste de Prova *</label>
              <input
                type="number"
                placeholder="Ex: 1234"
                value={filtros.teste_prova_id}
                onChange={(e) =>
                  setFiltros((f) => ({ ...f, teste_prova_id: e.target.value }))
                }
              />
            </div>

            <div className={styles.field}>
              <label>OCR Confiança Mín.</label>
              <input
                type="number"
                placeholder="0.00"
                min={0}
                max={1}
                step={0.01}
                value={filtros.ocr_confianca_min}
                onChange={(e) =>
                  setFiltros((f) => ({
                    ...f,
                    ocr_confianca_min: e.target.value,
                  }))
                }
              />
            </div>

            <div className={styles.field}>
              <label>OCR Confiança Máx.</label>
              <input
                type="number"
                placeholder="1.00"
                min={0}
                max={1}
                step={0.01}
                value={filtros.ocr_confianca_max}
                onChange={(e) =>
                  setFiltros((f) => ({
                    ...f,
                    ocr_confianca_max: e.target.value,
                  }))
                }
              />
            </div>

            <div className={styles.field}>
              <label>Status da Redação</label>
              <Dropdown
                options={STATUS_REDACAO}
                value={filtros.redacao_status_id}
                onChange={(v) =>
                  setFiltros((f) => ({ ...f, redacao_status_id: String(v) }))
                }
                placeholder="Selecione um status..."
              />
            </div>
          </div>

          <button
            className={styles.btnBuscar}
            onClick={handleBuscar}
            disabled={
              !filtros.teste_prova_id || !filtros.redacao_status_id || loading
            }
          >
            <Search size={16} />
            {loading ? "Buscando..." : "Buscar"}
          </button>
        </section>

        {/* ERRO */}
        {erro && <div className={styles.erro}>{erro}</div>}

        {/* Imagem + Texto OCR  |  Avaliação — lado a lado */}
        {redacao && (
          <div className={styles.mainGrid}>
            <section className={styles.validacaoCard}>
              <div className={styles.contador}>
                <span>
                  Redação <strong>{paginaAtual}</strong> de{" "}
                  <strong>{total}</strong>
                </span>
                <span className={styles.separador}>•</span>
                <span>
                  ID: <strong>{redacao.redacao_id}</strong>
                </span>
                <span className={styles.separador}>•</span>
                <span>
                  Confiança OCR:{" "}
                  <strong className={styles.confiancaValor}>
                    {redacao.ocr_confianca != null
                      ? `${(redacao.ocr_confianca * 100).toFixed(1)}%`
                      : "—"}
                  </strong>
                </span>
                {redacao.tema && (
                  <>
                    <span className={styles.separador}>•</span>
                    <span>
                      Tema: <strong>{redacao.tema}</strong>
                    </span>
                  </>
                )}
              </div>

              <div className={styles.conteudo}>
                {/* Coluna 1 — Imagem */}
                <div className={styles.coluna}>
                  <h3 className={styles.colunaTitle}>Imagem da Redação</h3>
                  <OcrImageViewer
                    arquivo={redacao.arquivo_anonimo_nome_armazenamento}
                    textoOcr={redacao.redacao_texto}
                  />
                </div>

                {/* Coluna 2 — Texto OCR */}
                <div className={styles.coluna}>
                  <h3 className={styles.colunaTitle}>
                    Texto Extraído pelo OCR
                  </h3>
                  <div className={styles.textoOcr}>
                    {redacao.redacao_texto ? (
                      redacao.redacao_texto
                    ) : (
                      <em className={styles.semTexto}>
                        Nenhum texto extraído pelo OCR
                      </em>
                    )}
                  </div>
                </div>
              </div>
            </section>

            <section className={styles.perguntasCard}>
              <h3 className={styles.perguntasTitle}>Avaliação da Qualidade</h3>

              <div className={styles.perguntasGrid}>
                <div className={styles.pergunta}>
                  <p>OCR pulou trechos?</p>
                  <div className={styles.opcoes}>
                    <label className={styles.opcao}>
                      <input
                        type="radio"
                        name="pulou_trechos"
                        checked={respostas.pulou_trechos === true}
                        onChange={() => setResposta("pulou_trechos", true)}
                      />
                      Sim
                    </label>
                    <label className={styles.opcao}>
                      <input
                        type="radio"
                        name="pulou_trechos"
                        checked={respostas.pulou_trechos === false}
                        onChange={() => setResposta("pulou_trechos", false)}
                      />
                      Não
                    </label>
                  </div>
                </div>

                <div className={styles.pergunta}>
                  <p>OCR trocou palavras?</p>
                  <div className={styles.opcoes}>
                    <label className={styles.opcao}>
                      <input
                        type="radio"
                        name="trocou_palavras"
                        checked={respostas.trocou_palavras === true}
                        onChange={() => setResposta("trocou_palavras", true)}
                      />
                      Sim
                    </label>
                    <label className={styles.opcao}>
                      <input
                        type="radio"
                        name="trocou_palavras"
                        checked={respostas.trocou_palavras === false}
                        onChange={() => setResposta("trocou_palavras", false)}
                      />
                      Não
                    </label>
                  </div>
                </div>

                <div className={styles.pergunta}>
                  <p>OCR trocou letras, acentos ou pontuação?</p>
                  <div className={styles.opcoes}>
                    <label className={styles.opcao}>
                      <input
                        type="radio"
                        name="trocou_letras"
                        checked={respostas.trocou_letras === true}
                        onChange={() => setResposta("trocou_letras", true)}
                      />
                      Sim
                    </label>
                    <label className={styles.opcao}>
                      <input
                        type="radio"
                        name="trocou_letras"
                        checked={respostas.trocou_letras === false}
                        onChange={() => setResposta("trocou_letras", false)}
                      />
                      Não
                    </label>
                  </div>
                </div>
              </div>

              <div className={styles.acoes}>
                <button
                  className={styles.btnPular}
                  onClick={handlePular}
                  disabled={paginaAtual >= total || loading}
                >
                  Pular
                </button>
                <button
                  className={styles.btnSalvar}
                  onClick={handleSalvar}
                  disabled={!todasRespondidas || loading}
                >
                  Salvar Validação
                </button>
              </div>
            </section>
          </div>
        )}
      </div>
    </AppLayout>
  );
}
