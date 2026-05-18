"use client";

import { useState, useEffect, useMemo } from "react";
import { apiRequest } from "@/lib/api";
import AppLayout from "@/components/AppLayout";
import Dropdown from "@/components/Dropdown";
import styles from "../../stats/Stats.module.css";

type CobItem = {
  disc_id: number;
  disciplina: string;
  disc_modu_id: number;
  modulo: string;
  assu_id: number;
  assunto: string;
  total_sp: number;
};

type Cobertura = {
  totais: {
    total_assuntos: number;
    com_classificacoes_sp: number;
    sem_classificacoes_sp: number;
    abaixo_5_sp: number;
  };
  items: CobItem[];
};

export default function SuperprofessorStatsPage() {
  const [stats, setStats] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [cobertura, setCobertura] = useState<Cobertura | null>(null);
  const [loadingCobertura, setLoadingCobertura] = useState(true);
  const [filtroDisc, setFiltroDisc] = useState<string>("");
  const [filtroModulo, setFiltroModulo] = useState<string>("");
  const [busca, setBusca] = useState<string>("");
  const [thresholdSP, setThresholdSP] = useState<string>("");

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

  useEffect(() => {
    async function loadCobertura() {
      try {
        const data = await apiRequest("/superprofessor/cobertura-libro");
        setCobertura(data);
      } catch (err) {
        console.error(err);
      } finally {
        setLoadingCobertura(false);
      }
    }
    loadCobertura();
  }, []);

  const itemsBase = useMemo(() => cobertura?.items ?? [], [cobertura]);

  const coberturaPorDisc = useMemo(() => {
    const map = new Map<
      string,
      { total: number; com: number; sem: number; sp: number }
    >();
    for (const it of itemsBase) {
      const e = map.get(it.disciplina) ?? { total: 0, com: 0, sem: 0, sp: 0 };
      e.total++;
      if (it.total_sp > 0) e.com++;
      else e.sem++;
      e.sp += it.total_sp;
      map.set(it.disciplina, e);
    }
    return Array.from(map.entries())
      .map(([disciplina, v]) => ({ disciplina, ...v }))
      .sort((a, b) => b.sem - a.sem || b.total - a.total);
  }, [itemsBase]);

  const disciplinasFiltroOpcoes = useMemo(
    () => coberturaPorDisc.map((d) => d.disciplina).sort(),
    [coberturaPorDisc],
  );

  const modulosFiltroOpcoes = useMemo(() => {
    const base = filtroDisc
      ? itemsBase.filter((i) => i.disciplina === filtroDisc)
      : itemsBase;
    return Array.from(new Set(base.map((i) => i.modulo))).sort();
  }, [itemsBase, filtroDisc]);

  const itemsFiltrados = useMemo(() => {
    let arr = itemsBase;
    if (filtroDisc) arr = arr.filter((i) => i.disciplina === filtroDisc);
    if (filtroModulo) arr = arr.filter((i) => i.modulo === filtroModulo);
    if (thresholdSP === "0") arr = arr.filter((i) => i.total_sp === 0);
    if (thresholdSP === "5") arr = arr.filter((i) => i.total_sp < 5);
    if (busca.trim()) {
      const q = busca.trim().toLowerCase();
      arr = arr.filter(
        (i) =>
          i.assunto.toLowerCase().includes(q) ||
          i.modulo.toLowerCase().includes(q) ||
          i.disciplina.toLowerCase().includes(q),
      );
    }
    return [...arr].sort(
      (a, b) =>
        a.total_sp - b.total_sp ||
        a.disciplina.localeCompare(b.disciplina) ||
        a.modulo.localeCompare(b.modulo),
    );
  }, [itemsBase, filtroDisc, filtroModulo, thresholdSP, busca]);

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
      label: "Total SP",
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

  const temFiltroAtivo = !!(
    filtroDisc ||
    filtroModulo ||
    busca.trim() ||
    thresholdSP
  );

  return (
    <AppLayout>
      <div className={styles.header}>
        <div>
          <h1>Estatísticas do Superprofessor</h1>
          <p>
            Acompanhamento da revisão e mapeamento das questões importadas do
            Superprofessor
          </p>
        </div>
      </div>

      <div
        className={styles.cards}
        style={{ gridTemplateColumns: "repeat(4, minmax(0, 1fr))" }}
      >
        {cards.map((card) => (
          <div
            key={card.key}
            className={styles.card}
            style={
              card.borderLeft ? { borderLeft: card.borderLeft } : undefined
            }
          >
            <div className={styles.cardLabelRow}>
              <span className={styles.cardLabel}>{card.label}</span>
              <span className={styles.tooltipText} role="tooltip">
                {card.tooltip}
              </span>
            </div>
            <h2
              className={styles.cardValue}
              style={{ color: card.color, opacity: card.opacity ?? 1 }}
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

      {/* ============ Cobertura de Assuntos ============ */}
      <div className={styles.progressTable} style={{ marginTop: "2rem" }}>
        <div className={styles.coberturaHeader}>
          <h3>Cobertura de Assuntos Libro</h3>
          {cobertura && (
            <div className={styles.coberturaBadges}>
              <span className={styles.coberturaBadge}>
                {cobertura.totais.total_assuntos.toLocaleString()} assuntos
                mapeados
              </span>
              <span
                className={`${styles.coberturaBadge} ${styles.coberturaBadgeCom}`}
              >
                {cobertura.totais.com_classificacoes_sp.toLocaleString()} com
                classif. SP
              </span>
              <span
                className={`${styles.coberturaBadge} ${styles.coberturaBadgeSem}`}
              >
                {cobertura.totais.sem_classificacoes_sp.toLocaleString()} sem
                classif.
              </span>
              {cobertura.totais.abaixo_5_sp > 0 && (
                <span
                  className={`${styles.coberturaBadge}`}
                  style={{
                    background: "#fef9c3",
                    borderColor: "#fde047",
                    color: "#854d0e",
                  }}
                >
                  {cobertura.totais.abaixo_5_sp.toLocaleString()} com menos de 5
                </span>
              )}
            </div>
          )}
        </div>

        {loadingCobertura ? (
          <div className={styles.loading}>Carregando cobertura...</div>
        ) : !cobertura ? (
          <div className={styles.loading}>Sem dados de cobertura.</div>
        ) : (
          <>
            {/* Resumo por disciplina */}
            <div
              className={styles.tableContainer}
              style={{ marginBottom: "1.5rem" }}
            >
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Disciplina</th>
                    <th style={{ textAlign: "right" }}>Assuntos</th>
                    <th style={{ textAlign: "right" }}>Com classif. SP</th>
                    <th style={{ textAlign: "right" }}>Sem classif.</th>
                    <th style={{ width: "150px" }}>Cobertura SP</th>
                  </tr>
                </thead>
                <tbody>
                  {coberturaPorDisc.map((d) => {
                    const pct =
                      d.total > 0
                        ? Math.floor((d.com / d.total) * 1000) / 10
                        : 0;
                    return (
                      <tr key={d.disciplina}>
                        <td className={styles.disciplineName}>
                          {d.disciplina}
                        </td>
                        <td
                          className={styles.tableCount}
                          style={{
                            textAlign: "right",
                            color: "var(--text-muted)",
                          }}
                        >
                          {d.total.toLocaleString()}
                        </td>
                        <td
                          className={styles.tableCount}
                          style={{
                            textAlign: "right",
                            color: "var(--success)",
                          }}
                        >
                          {d.com > 0 ? d.com.toLocaleString() : "—"}
                        </td>
                        <td
                          className={styles.tableCount}
                          style={{
                            textAlign: "right",
                            color:
                              d.sem > 0 ? "var(--error)" : "var(--text-muted)",
                            fontWeight: d.sem > 0 ? 700 : 400,
                          }}
                        >
                          {d.sem > 0 ? d.sem.toLocaleString() : "—"}
                        </td>
                        <td>
                          <div className={styles.progressBarCell}>
                            <div className={styles.progressBarContainer}>
                              <div
                                className={styles.progressBarFill}
                                style={{
                                  width: `${Math.min(pct, 100)}%`,
                                  background:
                                    d.sem > 0
                                      ? "linear-gradient(90deg, var(--orange), #fb923c)"
                                      : undefined,
                                }}
                              />
                            </div>
                            <span className={styles.progressBarLabel}>
                              {pct}%
                            </span>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Filtros */}
            <div className={styles.coberturaFilters}>
              <div style={{ minWidth: 200 }}>
                <Dropdown
                  label="Disciplina"
                  searchable
                  placeholder="Todas"
                  value={filtroDisc || "__all__"}
                  onChange={(v) => {
                    setFiltroDisc(v === "__all__" ? "" : v);
                    setFiltroModulo("");
                  }}
                  options={[
                    { value: "__all__", label: "Todas" },
                    ...disciplinasFiltroOpcoes.map((d) => ({
                      value: d,
                      label: d,
                    })),
                  ]}
                />
              </div>

              <div style={{ minWidth: 200 }}>
                <Dropdown
                  label="Módulo"
                  searchable
                  placeholder="Todos"
                  value={filtroModulo || "__all__"}
                  onChange={(v) => setFiltroModulo(v === "__all__" ? "" : v)}
                  disabled={!filtroDisc}
                  options={[
                    { value: "__all__", label: "Todos" },
                    ...modulosFiltroOpcoes.map((m) => ({ value: m, label: m })),
                  ]}
                />
              </div>

              <div style={{ minWidth: 160 }}>
                <Dropdown
                  label="Classif. SP"
                  placeholder="Todos"
                  value={thresholdSP || "__all__"}
                  onChange={(v) => setThresholdSP(v === "__all__" ? "" : v)}
                  options={[
                    { value: "__all__", label: "Todos" },
                    { value: "0", label: "Zerados (= 0)" },
                    { value: "5", label: "Menos de 5 (< 5)" },
                  ]}
                />
              </div>

              <div style={{ minWidth: 180 }}>
                <div
                  style={{
                    fontSize: "0.85rem",
                    fontWeight: 700,
                    color: "var(--text-muted)",
                    textTransform: "uppercase",
                    letterSpacing: "0.5px",
                    marginBottom: "0.5rem",
                  }}
                >
                  Busca
                </div>
                <input
                  type="text"
                  className={styles.coberturaInput}
                  placeholder="Filtrar por assunto..."
                  value={busca}
                  onChange={(e) => setBusca(e.target.value)}
                />
              </div>

              {temFiltroAtivo && (
                <button
                  className={styles.coberturaResetBtn}
                  onClick={() => {
                    setFiltroDisc("");
                    setFiltroModulo("");
                    setBusca("");
                    setThresholdSP("");
                  }}
                >
                  Limpar filtros
                </button>
              )}
            </div>

            <div className={styles.coberturaCount}>
              {itemsFiltrados.length.toLocaleString()} de{" "}
              {itemsBase.length.toLocaleString()} assuntos
            </div>

            <div className={styles.tableContainer}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Disciplina</th>
                    <th>Módulo</th>
                    <th>Assunto</th>
                    <th style={{ textAlign: "right" }}>Classif. SP</th>
                  </tr>
                </thead>
                <tbody>
                  {itemsFiltrados.map((it) => (
                    <tr key={`${it.disc_modu_id}-${it.assu_id}`}>
                      <td className={styles.disciplineName}>{it.disciplina}</td>
                      <td
                        style={{
                          fontSize: "0.82rem",
                          color: "var(--text-muted)",
                        }}
                      >
                        {it.modulo}
                      </td>
                      <td style={{ fontSize: "0.85rem" }}>{it.assunto}</td>
                      <td
                        className={styles.tableCount}
                        style={{
                          textAlign: "right",
                          color:
                            it.total_sp === 0
                              ? "var(--error)"
                              : it.total_sp < 5
                                ? "var(--orange)"
                                : "var(--success)",
                          fontWeight: it.total_sp < 5 ? 700 : 600,
                        }}
                      >
                        {it.total_sp === 0 ? "—" : it.total_sp.toLocaleString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </AppLayout>
  );
}
