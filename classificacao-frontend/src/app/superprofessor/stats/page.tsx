"use client";

import { useState, useEffect } from "react";
import { apiRequest } from "@/lib/api";
import AppLayout from "@/components/AppLayout";
import styles from "../../stats/Stats.module.css";

export default function SuperprofessorStatsPage() {
  const [stats, setStats] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadStats() {
      try {
        const data = await apiRequest("/superprofessor/stats");
        setStats(data);
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    }
    loadStats();
  }, []);

  if (loading)
    return (
      <AppLayout>
        <div className={styles.loading}>Carregando estatísticas...</div>
      </AppLayout>
    );

  const cards = [
    {
      key: "classificadas",
      label: "Classificadas",
      value: stats?.total_classificadas?.toLocaleString() || 0,
      color: "var(--success)",
      tooltip: "Questões do superprofessor que já foram revisadas e mapeadas.",
    },
    {
      key: "pendentes",
      label: "Pendentes",
      value: stats?.total_pendentes?.toLocaleString() || 0,
      color: "var(--primary)",
      tooltip: "Questões do superprofessor que ainda aguardam revisão.",
    },
    {
      key: "puladas",
      label: "Puladas",
      value: stats?.total_puladas?.toLocaleString() || 0,
      color: "var(--text-muted)",
      opacity: 0.7,
      tooltip: "Questões puladas pelo revisor.",
    },
    {
      key: "total",
      label: "Total de Questões SP",
      value: stats?.total_questoes?.toLocaleString() || 0,
      color: "var(--text)",
      borderLeft: "4px solid var(--text-muted)",
      tooltip: "Total geral de questões vindas do Superprofessor.",
    },
  ];

  const disciplinasDetalhado = Object.entries(stats?.por_disciplina || {})
    .map(([nome, d]: [string, any]) => ({
      nome,
      total: Number(d?.total || 0),
      classificadas: Number(d?.classificadas || 0),
      puladas: Number(d?.puladas || 0),
      pendentes: Number(d?.pendentes || 0),
    }))
    .sort((a, b) => b.total - a.total);

  return (
    <AppLayout>
      <div className={styles.header}>
        <div>
          <h1>Estatísticas do Superprofessor</h1>
          <p>
            Acompanhamento da revisão e mapeamento das questões importadas do Superprofessor
          </p>
        </div>
      </div>

      <div className={styles.cards} style={{ gridTemplateColumns: 'repeat(4, minmax(0, 1fr))' }}>
        {cards.map((card) => (
          <div
            key={card.key}
            className={styles.card}
            style={card.borderLeft ? { borderLeft: card.borderLeft } : undefined}
          >
            <div className={styles.cardLabelRow}>
              <span className={styles.cardLabel}>{card.label}</span>
              <span className={styles.tooltipText} role="tooltip">
                {card.tooltip}
              </span>
            </div>
            <h2
              className={styles.cardValue}
              style={{
                color: card.color,
                opacity: card.opacity ?? 1,
              }}
            >
              {card.value}
            </h2>
          </div>
        ))}
      </div>

      <div className={styles.progressTable}>
        <h3>Progresso por Disciplina SP</h3>
        <div className={styles.tableContainer}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Disciplina SP</th>
                <th style={{ textAlign: "right" }}>Total</th>
                <th style={{ textAlign: "right" }}>Classificadas</th>
                <th style={{ textAlign: "right" }}>Pendentes</th>
                <th style={{ width: "200px" }}>Progresso</th>
              </tr>
            </thead>
            <tbody>
              {disciplinasDetalhado.map((disc) => {
                const pct =
                  disc.total > 0
                    ? Math.round((disc.classificadas / disc.total) * 100)
                    : 0;
                return (
                  <tr key={disc.nome}>
                    <td className={styles.disciplineName}>{disc.nome}</td>
                    <td
                      className={styles.tableCount}
                      style={{ textAlign: "right", color: "var(--text-muted)" }}
                    >
                      {disc.total.toLocaleString()}
                    </td>
                    <td
                      className={styles.tableCount}
                      style={{ textAlign: "right", color: "var(--success)" }}
                    >
                      {disc.classificadas.toLocaleString()}
                    </td>
                    <td
                      className={styles.tableCount}
                      style={{ textAlign: "right", color: "var(--primary)" }}
                    >
                      {disc.pendentes.toLocaleString()}
                    </td>
                    <td>
                      <div className={styles.progressBarCell}>
                        <div className={styles.progressBarContainer}>
                          <div
                            className={styles.progressBarFill}
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                        <span className={styles.progressBarLabel}>{pct}%</span>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className={styles.tableCard}>
        <h3>Liderança de Atividade (Revisão SP)</h3>
        <div className={styles.tableContainer}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Nome</th>
                <th style={{ textAlign: "right" }}>Ações</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(stats?.por_usuario || {})
                .sort((a: any, b: any) => b[1] - a[1])
                .map(([nome, count]: [string, any]) => (
                  <tr key={nome}>
                    <td>{nome}</td>
                    <td
                      className={styles.tableCount}
                      style={{ textAlign: "right", color: "var(--primary)" }}
                    >
                      {count}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </div>
    </AppLayout>
  );
}
