"use client";

import { useState, useEffect, useRef } from "react";
import { apiRequest } from "@/lib/api";
import AppLayout from "@/components/AppLayout";
import Dropdown from "@/components/Dropdown";
import styles from "./Stats.module.css";

export default function StatsPage() {
  const [stats, setStats] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [selectedDisc, setSelectedDisc] = useState<string>("");
  const [hoveredBar, setHoveredBar] = useState<{
    label: string;
    value: number;
    pct: string;
    color: string;
    x: number;
    y: number;
  } | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    async function loadStats() {
      try {
        const data = await apiRequest("/stats");
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

  // Totais globais (trieduc + Superprofessor)
  const totalSistema = Number(stats?.total_sistema || 0);
  const totalManuais = Number(stats?.total_manuais || 0);
  const totalPendentes = Number(stats?.total_pendentes || 0);
  const totalPuladas = Number(stats?.total_puladas || 0);

  // Breakdown trieduc vs SP
  const tTrieduc = Number(stats?.total_trieduc || 0);
  const tTrieducClass = Number(stats?.total_trieduc_classificadas || 0);
  const tTrieducPend = Number(stats?.total_trieduc_pendentes || 0);
  const tTrieducPuladas = Number(stats?.total_trieduc_puladas || 0);
  const tSP = Number(stats?.total_superprofessor || 0);
  const tSPClass = Number(stats?.total_superprofessor_classificadas || 0);
  const tSPPend = Number(stats?.total_superprofessor_pendentes || 0);
  const tSPPuladas = Number(stats?.total_superprofessor_puladas || 0);

  const fmt = (n: number) => n.toLocaleString("pt-BR");

  const cards = [
    {
      key: "manual",
      label: "Classificadas (Manual)",
      value: fmt(totalManuais),
      color: "var(--success)",
      borderLeft: undefined,
      tooltip: `Questões finalizadas (trieduc + Superprofessor). Contribuição por base:\n• Trieduc: ${fmt(tTrieducClass)} (nova, correção, libro, auto)\n• Superprofessor: ${fmt(tSPClass)} (classificacao_superprofessor)\n\nConfirmações sem libro NÃO entram aqui.`,
    },
    {
      key: "alta_sim",
      label: "Alta Similaridade",
      value: fmt(Number(stats?.total_alta_similaridade || 0)),
      color: "var(--yellow)",
      borderLeft: undefined,
      tooltip:
        "Questões trieduc com similaridade ≥ 80% com o banco SuperProfessor, sem nenhuma ação ainda. Acessíveis na aba 'Alta Similaridade' da tela de classificação por similaridade.\n\n(SuperProfessor não tem este conceito.)",
    },
    {
      key: "confirmacoes",
      label: "Confirmações Pendentes",
      value: fmt(Number(stats?.total_confirmacoes_pendentes || 0)),
      color: "var(--primary)",
      borderLeft: undefined,
      tooltip:
        "Questões trieduc cujo assunto foi confirmado no fluxo Verificar, mas que ainda aguardam seleção de módulos libro. Acessíveis na aba 'Confirmações' da tela de classificação por similaridade.\n\n(SuperProfessor não tem este conceito.)",
    },
    {
      key: "verificar",
      label: "Faltam Verificar",
      value: fmt(Number(stats?.total_precisa_verificar || 0)),
      color: "var(--orange)",
      borderLeft: undefined,
      tooltip:
        "Questões trieduc com similaridade entre 1% e 79% com o SuperProfessor, sem ação nem confirmação. Acessíveis na tela Verificar (exigem classificacao_nao_enquadrada preenchida pelo agente).\n\n(SuperProfessor não tem este conceito.)",
    },
    {
      key: "pendentes",
      label: "Pendentes",
      value: fmt(totalPendentes),
      color: "var(--text-muted)",
      borderLeft: undefined,
      tooltip: `Questões sem ação alguma (trieduc + Superprofessor). Contribuição por base:\n• Trieduc: ${fmt(tTrieducPend)} (sem registro de similaridade)\n• Superprofessor: ${fmt(tSPPend)} (aguardam classificação no fluxo SP)`,
    },
    {
      key: "puladas",
      label: "Puladas",
      value: fmt(totalPuladas),
      color: "var(--text-muted)",
      borderLeft: undefined,
      opacity: 0.7,
      tooltip: `Questões puladas (trieduc + Superprofessor). Contribuição por base:\n• Trieduc: ${fmt(tTrieducPuladas)}\n• Superprofessor: ${fmt(tSPPuladas)}`,
    },
    {
      key: "quatro_alt",
      label: "4 Alternativas",
      value: fmt(Number(stats?.total_4_alternativas || 0)),
      color: "var(--text-muted)",
      borderLeft: undefined,
      opacity: 0.7,
      tooltip:
        "Questões trieduc com exatamente 4 alternativas — excluídas do funil de classificação e não contabilizadas no Total do Sistema.",
    },
    {
      key: "total",
      label: "Total do Sistema",
      value: fmt(totalSistema),
      color: "var(--text)",
      borderLeft: "4px solid var(--text-muted)",
      tooltip: `Total global elegível para classificação. Contribuição por base:\n• Trieduc: ${fmt(tTrieduc)} questões (Ensino Médio com habilidade, sem 4-alt)\n• Superprofessor: ${fmt(tSP)} questões (universo independente)\n\nA soma de Classificadas + Alta Sim + Confirmações + Verificar + Puladas + Pendentes deve igualar este valor.`,
    },
  ];

  const disciplinasDetalhado = Object.entries(stats?.por_disciplina || {})
    .map(([nome, d]: [string, any]) => ({
      nome,
      total: Number(d?.total || 0),
      feitas: Number(d?.feitas || 0),
      faltam: Number(d?.faltam || 0),
      manuais: Number(d?.manuais || 0),
      confirmacoes: Number(d?.confirmacoes || 0),
      alta_sim: Number(d?.alta_sim || d?.auto || 0),
      verificar: Number(d?.verificar || 0),
      pendentes: Number(d?.pendentes || 0),
      puladas: Number(d?.puladas || 0),
      // Breakdown por base
      trieduc_total: Number(d?.trieduc_total || 0),
      trieduc_classificadas: Number(d?.trieduc_classificadas || 0),
      trieduc_pendentes: Number(d?.trieduc_pendentes || 0),
      trieduc_puladas: Number(d?.trieduc_puladas || 0),
      sp_total: Number(d?.sp_total || 0),
      sp_classificadas: Number(d?.sp_classificadas || 0),
      sp_pendentes: Number(d?.sp_pendentes || 0),
      sp_puladas: Number(d?.sp_puladas || 0),
      total_modulos: Number(d?.total_modulos || 0),
      total_habilidades: Number(d?.total_habilidades || 0),
      total_assuntos: Number(d?.total_assuntos || 0),
    }))
    .sort((a, b) => b.total - a.total);

  // Progresso preciso: TRUNCA (Math.floor) em vez de arredondar para nunca
  // exibir 100% quando ainda faltam questões. Se < 1% restante mas > 0, mostra
  // com 1 decimal (também truncado, não arredondado).
  const calcPct = (feitas: number, total: number): string => {
    if (total <= 0) return "0";
    if (feitas >= total) return "100";
    const raw = (feitas / total) * 100;
    // Quando raw > 99 e < 100, mostra com 1 decimal truncado (ex: 99.95 → 99.9)
    if (raw >= 99) {
      const truncated = Math.floor(raw * 10) / 10;
      // Garante que nunca mostre "100.0" se ainda falta algo
      return truncated >= 100 ? "99.9" : truncated.toFixed(1);
    }
    return String(Math.floor(raw));
  };

  const statusSegments = [
    { key: "manuais", label: "Manual", color: "#38a169" },
    { key: "alta_sim", label: "Alta Sim.", color: "#ecc94b" },
    { key: "confirmacoes", label: "Confirmações", color: "#3182ce" },
    { key: "verificar", label: "Verificar", color: "#ed8936" },
    { key: "pendentes", label: "Pendentes", color: "#718096" },
    { key: "puladas", label: "Puladas", color: "#a0aec0" },
  ] as const;

  const disc = selectedDisc
    ? disciplinasDetalhado.find((d) => d.nome === selectedDisc)
    : disciplinasDetalhado[0];

  return (
    <AppLayout>
      <div className={styles.header}>
        <div>
          <h1>Progresso da Classificação</h1>
          <p>
            Filtro Ativo: <b>Ensino Médio</b> com Assunto
          </p>
        </div>
      </div>

      {/* Cards Compactos e Diretos */}
      <div className={styles.cards}>
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


      {/* Tabela de Progresso por Disciplina */}
      <div className={styles.progressTable}>
        <h3>Progresso por Disciplina</h3>
        <div className={styles.tableContainer}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Disciplina</th>
                <th style={{ textAlign: "right" }}>Total</th>
                <th
                  style={{ textAlign: "right", color: "var(--success)" }}
                  title="Questões com classificação finalizada"
                >
                  Classif.
                </th>
                <th
                  style={{ textAlign: "right", color: "#ecc94b" }}
                  title="Aguardando ação na fila de Alta Similaridade"
                >
                  Alta Sim.
                </th>
                <th
                  style={{ textAlign: "right", color: "#3182ce" }}
                  title="Confirmadas mas sem módulos libro selecionados"
                >
                  Confirm.
                </th>
                <th
                  style={{ textAlign: "right", color: "var(--orange)" }}
                  title="Baixa similaridade aguardando verificação"
                >
                  Verificar
                </th>
                <th
                  style={{ textAlign: "right", color: "var(--text-muted)" }}
                  title="Sem dados de similaridade (agente não processou)"
                >
                  Pendentes
                </th>
                <th style={{ width: "180px" }}>Progresso</th>
              </tr>
            </thead>
            <tbody>
              {disciplinasDetalhado.map((disc) => {
                const pct = calcPct(disc.feitas, disc.total);
                const pctNum = parseFloat(pct);
                const totalTooltip =
                  disc.sp_total > 0
                    ? `Trieduc: ${fmt(disc.trieduc_total)} • Superpro: ${fmt(disc.sp_total)}`
                    : `Trieduc: ${fmt(disc.trieduc_total)}`;
                const feitasTooltip =
                  disc.sp_classificadas > 0
                    ? `Trieduc: ${fmt(disc.trieduc_classificadas)} • Superpro: ${fmt(disc.sp_classificadas)}`
                    : `Trieduc: ${fmt(disc.trieduc_classificadas)}`;
                const pendentesTooltip =
                  disc.sp_pendentes > 0
                    ? `Trieduc: ${fmt(disc.trieduc_pendentes)} • Superpro: ${fmt(disc.sp_pendentes)}`
                    : `Trieduc: ${fmt(disc.trieduc_pendentes)}`;
                return (
                  <tr key={disc.nome}>
                    <td className={styles.disciplineName}>{disc.nome}</td>
                    <td
                      className={styles.tableCount}
                      style={{
                        textAlign: "right",
                        color: "var(--text)",
                        fontWeight: 600,
                      }}
                      title={totalTooltip}
                    >
                      {disc.total.toLocaleString()}
                    </td>
                    <td
                      className={styles.tableCount}
                      style={{ textAlign: "right", color: "var(--success)" }}
                      title={feitasTooltip}
                    >
                      {disc.feitas.toLocaleString()}
                    </td>
                    <td
                      className={styles.tableCount}
                      style={{ textAlign: "right", color: "#ecc94b" }}
                      title="Aguardando ação na fila de Alta Similaridade (trieduc)"
                    >
                      {disc.alta_sim.toLocaleString()}
                    </td>
                    <td
                      className={styles.tableCount}
                      style={{ textAlign: "right", color: "#3182ce" }}
                      title="Confirmadas sem módulos libro (aba Confirmações)"
                    >
                      {disc.confirmacoes.toLocaleString()}
                    </td>
                    <td
                      className={styles.tableCount}
                      style={{ textAlign: "right", color: "var(--orange)" }}
                      title="Baixa similaridade aguardando verificação"
                    >
                      {disc.verificar.toLocaleString()}
                    </td>
                    <td
                      className={styles.tableCount}
                      style={{
                        textAlign: "right",
                        color: "var(--text-muted)",
                      }}
                      title={pendentesTooltip}
                    >
                      {disc.pendentes.toLocaleString()}
                    </td>
                    <td>
                      <div className={styles.progressBarCell}>
                        <div className={styles.progressBarContainer}>
                          <div
                            className={styles.progressBarFill}
                            style={{ width: `${Math.min(pctNum, 100)}%` }}
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
      </div>

      {/* Composição + Liderança lado a lado */}
      <div className={styles.bottomSection}>
        {/* Composição por Disciplina */}
        <div className={styles.compCard}>
          <div className={styles.compHeader}>
            <h3>Composição por Disciplina</h3>
            <Dropdown
              options={disciplinasDetalhado.map((d) => ({
                value: d.nome,
                label: d.nome,
              }))}
              value={disc?.nome || ""}
              onChange={(v) => setSelectedDisc(v as string)}
              searchable
              placeholder="Selecione a disciplina"
            />
          </div>

          {disc ? (
            (() => {
              const pct =
                disc.total > 0
                  ? Math.round((disc.feitas / disc.total) * 100)
                  : 0;

              /* ── waterfall data ── */
              const segments: {
                label: string;
                value: number;
                color: string;
              }[] = statusSegments.map((seg) => ({
                label: seg.label,
                value: disc[seg.key] as number,
                color: seg.color,
              }));
              let cum = 0;
              const wfBars = segments.map((s) => {
                const bottom = cum;
                cum += s.value;
                return { ...s, bottom, top: cum };
              });
              wfBars.push({
                label: "Total",
                value: disc.total,
                color: "#e53e3e",
                bottom: 0,
                top: disc.total,
              });

              const maxVal = disc.total || 1;
              const roughStep = maxVal / 5;
              const pow10 = Math.pow(
                10,
                Math.floor(Math.log10(roughStep || 1)),
              );
              const norm = roughStep / pow10;
              const step =
                (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * pow10;
              const chartMax = Math.ceil(maxVal / step) * step || 1;

              const cW = 560,
                cH = 320;
              const pL = 52,
                pR = 12,
                pT = 14,
                pB = 38;
              const plotW = cW - pL - pR;
              const plotH = cH - pT - pB;
              const barGap = plotW / wfBars.length;
              const barW = barGap * 0.58;

              const y = (v: number) => pT + plotH * (1 - v / chartMax);

              const yTicks: number[] = [];
              for (let v = 0; v <= chartMax; v += step) yTicks.push(v);

              return (
                <div className={styles.chartWrap} ref={chartRef}>
                  <div className={styles.compSummary}>
                    <span className={styles.compPctDetail}>
                      {disc.feitas.toLocaleString()} de{" "}
                      {disc.total.toLocaleString()} concluídas
                    </span>
                    <span className={styles.compPct}>{pct}%</span>
                  </div>

                  <svg
                    viewBox={`0 0 ${cW} ${cH}`}
                    className={styles.waterfallSvg}
                  >
                    {/* grid */}
                    {yTicks.map((tick) => (
                      <g key={tick}>
                        <line
                          x1={pL}
                          x2={cW - pR}
                          y1={y(tick)}
                          y2={y(tick)}
                          stroke="#e2e8f0"
                          strokeWidth="0.8"
                        />
                        <text
                          x={pL - 6}
                          y={y(tick)}
                          textAnchor="end"
                          dominantBaseline="middle"
                          fontSize="9"
                          fill="#718096"
                          fontWeight="600"
                        >
                          {tick.toLocaleString()}
                        </text>
                      </g>
                    ))}

                    {/* bars */}
                    {wfBars.map((bar, i) => {
                      const x = pL + i * barGap + (barGap - barW) / 2;
                      const yTop = y(bar.top);
                      const yBot = y(bar.bottom);
                      const h = yBot - yTop;

                      return (
                        <g key={bar.label}>
                          <rect
                            x={x}
                            y={yTop}
                            width={barW}
                            height={Math.max(h, 2)}
                            fill={bar.color}
                            rx="3"
                            className={styles.waterfallBar}
                            onMouseEnter={(e) => {
                              const svg = e.currentTarget.ownerSVGElement;
                              const wrap = chartRef.current;
                              if (!svg || !wrap) return;
                              const svgRect = svg.getBoundingClientRect();
                              const wrapRect = wrap.getBoundingClientRect();
                              const sx = svgRect.width / cW;
                              const sy = svgRect.height / cH;
                              setHoveredBar({
                                label: bar.label,
                                value: bar.value,
                                pct:
                                  disc.total > 0
                                    ? ((bar.value / disc.total) * 100).toFixed(
                                        1,
                                      )
                                    : "0",
                                color: bar.color,
                                x:
                                  svgRect.left -
                                  wrapRect.left +
                                  (x + barW / 2) * sx,
                                y: svgRect.top - wrapRect.top + yTop * sy - 8,
                              });
                            }}
                            onMouseLeave={() => setHoveredBar(null)}
                          />
                          {/* connector to next */}
                          {i < wfBars.length - 2 && (
                            <line
                              x1={x + barW}
                              x2={pL + (i + 1) * barGap + (barGap - barW) / 2}
                              y1={y(bar.top)}
                              y2={y(bar.top)}
                              stroke="#cbd5e0"
                              strokeWidth="0.8"
                              strokeDasharray="3,2"
                            />
                          )}
                          {/* x-axis label */}
                          <text
                            x={x + barW / 2}
                            y={cH - pB + 16}
                            textAnchor="middle"
                            dominantBaseline="central"
                            fontSize="9"
                            fill="#718096"
                            fontWeight="600"
                          >
                            {bar.label}
                          </text>
                        </g>
                      );
                    })}

                    {/* x-axis line */}
                    <line
                      x1={pL}
                      x2={cW - pR}
                      y1={pT + plotH}
                      y2={pT + plotH}
                      stroke="#cbd5e0"
                      strokeWidth="1"
                    />
                  </svg>

                  {hoveredBar && (
                    <div
                      className={styles.wfTooltip}
                      style={{
                        left: hoveredBar.x,
                        top: hoveredBar.y,
                      }}
                    >
                      <span
                        className={styles.wfTooltipDot}
                        style={{ backgroundColor: hoveredBar.color }}
                      />
                      <span className={styles.wfTooltipLabel}>
                        {hoveredBar.label}
                      </span>
                      <span className={styles.wfTooltipVal}>
                        {hoveredBar.value.toLocaleString()}
                      </span>
                      <span className={styles.wfTooltipPct}>
                        {hoveredBar.pct}%
                      </span>
                    </div>
                  )}
                </div>
              );
            })()
          ) : (
            <p className={styles.chartEmpty}>Sem dados.</p>
          )}
        </div>

        {/* Liderança de Atividade */}
        <div className={styles.tableCard}>
          <h3>Liderança de Atividade</h3>
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
      </div>
    </AppLayout>
  );
}
