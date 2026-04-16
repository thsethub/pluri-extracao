"use client";

import { useState } from "react";
import { Search } from "lucide-react";
import AppLayout from "@/components/AppLayout";
import { apiRequest } from "@/lib/api";
import styles from "./OcrAdmin.module.css";

interface RevisorResumo {
  revisor_id: number;
  revisor_nome: string;
  revisado: number;
  pulou_trechos_sim: number;
  trocou_palavras_sim: number;
  trocou_caracteres_sim: number;
}

interface StatusContador {
  redacao_status_id: number;
  status_label: string;
  total: number;
  validado: number;
  restante: number;
}

interface AdminResumo {
  revisores: RevisorResumo[];
  status_contadores: StatusContador[];
}

interface Filtros {
  teste_prova_id: string;
  ocr_confianca_min: string;
  ocr_confianca_max: string;
}

export default function OcrAdminPage() {
  const [filtros, setFiltros] = useState<Filtros>({
    teste_prova_id: "",
    ocr_confianca_min: "",
    ocr_confianca_max: "",
  });

  const [resumo, setResumo] = useState<AdminResumo | null>(null);
  const [loading, setLoading] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  const handleBuscar = async () => {
    setLoading(true);
    setErro(null);

    try {
      const query = new URLSearchParams();
      query.set("teste_prova_id", filtros.teste_prova_id);

      const clampOcr = (val: string, fallback: number) => {
        const n = parseFloat(val);
        return String(isNaN(n) ? fallback : Math.min(1, Math.max(0, n)));
      };
      query.set("ocr_confianca_min", clampOcr(filtros.ocr_confianca_min, 0));
      query.set("ocr_confianca_max", clampOcr(filtros.ocr_confianca_max, 1));

      const resp: AdminResumo = await apiRequest(
        `/ocr-confianca/admin/resumo?${query.toString()}`,
      );
      setResumo(resp);
    } catch {
      setResumo(null);
      setErro("Erro ao buscar dados administrativos.");
    } finally {
      setLoading(false);
    }
  };

  const totais = resumo?.revisores.reduce(
    (acc, r) => ({
      revisado: acc.revisado + r.revisado,
      pulou_trechos_sim: acc.pulou_trechos_sim + r.pulou_trechos_sim,
      trocou_palavras_sim: acc.trocou_palavras_sim + r.trocou_palavras_sim,
      trocou_caracteres_sim:
        acc.trocou_caracteres_sim + r.trocou_caracteres_sim,
    }),
    {
      revisado: 0,
      pulou_trechos_sim: 0,
      trocou_palavras_sim: 0,
      trocou_caracteres_sim: 0,
    },
  );

  return (
    <AppLayout>
      <div className={styles.page}>
        <div className={styles.header}>
          <h1>OCR Admin</h1>
          <p>Dashboard de validações OCR por revisor e status de redação</p>
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
          </div>

          <button
            className={styles.btnBuscar}
            onClick={handleBuscar}
            disabled={!filtros.teste_prova_id || loading}
          >
            <Search size={16} />
            {loading ? "Buscando..." : "Buscar"}
          </button>
        </section>

        {/* ERRO */}
        {erro && <div className={styles.erro}>{erro}</div>}

        {/* CARDS DE STATUS */}
        {resumo && (
          <div className={styles.statusGrid}>
            {resumo.status_contadores.map((s) => (
              <div key={s.redacao_status_id} className={styles.statusCard}>
                <h3>{s.status_label}</h3>
                <div className={styles.statusNumeros}>
                  <div className={styles.statusItem}>
                    <span>{s.total}</span>
                    <span>Total</span>
                  </div>
                  <div className={styles.statusItem}>
                    <span>{s.validado}</span>
                    <span>Validado</span>
                  </div>
                  <div className={styles.statusItem}>
                    <span className={styles.destaque}>{s.restante}</span>
                    <span>Restante</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* TABELA DE REVISORES */}
        {resumo && (
          <section className={styles.tabelaCard}>
            <h3>Validações por Revisor</h3>

            {resumo.revisores.length === 0 ? (
              <p className={styles.semDados}>
                Nenhuma validação registrada para este teste de prova.
              </p>
            ) : (
              <table className={styles.tabela}>
                <thead>
                  <tr>
                    <th>Revisor</th>
                    <th>Revisados</th>
                    <th>Pulou Trechos</th>
                    <th>Trocou Palavras</th>
                    <th>Trocou Caracteres</th>
                  </tr>
                </thead>
                <tbody>
                  {resumo.revisores.map((r) => (
                    <tr key={r.revisor_id}>
                      <td>{r.revisor_nome}</td>
                      <td>{r.revisado}</td>
                      <td>{r.pulou_trechos_sim}</td>
                      <td>{r.trocou_palavras_sim}</td>
                      <td>{r.trocou_caracteres_sim}</td>
                    </tr>
                  ))}
                  {totais && (
                    <tr className={styles.totalRow}>
                      <td>TOTAL</td>
                      <td>{totais.revisado}</td>
                      <td>{totais.pulou_trechos_sim}</td>
                      <td>{totais.trocou_palavras_sim}</td>
                      <td>{totais.trocou_caracteres_sim}</td>
                    </tr>
                  )}
                </tbody>
              </table>
            )}
          </section>
        )}
      </div>
    </AppLayout>
  );
}
