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

  const cards = [
    {
      key: "manual",
      label: "Classificadas (Manual)",
      value: stats?.total_manuais?.toLocaleString() || 0,
      color: "var(--success)",
      borderLeft: undefined,
      tooltip:
        "Quantidade de questões de Ensino Médio com assunto que já receberam alguma ação manual de usuário (classificação nova, auto-classificação, confirmação ou correção).",
    },
    {
      key: "sistema",
      label: "Classificadas (Sistemas)",
      value: stats?.total_auto_superpro?.toLocaleString() || 0,
      color: "var(--yellow)",
      borderLeft: undefined,
      tooltip:
        "Quantidade classificada automaticamente pelo sistema com similaridade maior ou igual a 80%, excluindo as que já tiveram ação manual. Origem: Superprofessor",
    },
    {
      key: "pendentes",
      label: "Pendentes",
      value: stats?.total_pendentes?.toLocaleString() || 0,
      color: "var(--primary)",
      borderLeft: undefined,
      tooltip:
        "Quantidade restante no funil de questões que ainda não receberam alguma classificação.",
    },
    {
      key: "verificar",
      label: "Faltam Verificar",
      value: stats?.total_precisa_verificar?.toLocaleString() || 0,
      color: "var(--orange)",
      borderLeft: undefined,
      tooltip:
        "Questões com classificação automática de baixa similaridade (maior que 0 e menor que 80%) que ainda não foram validadas. Origem: Superprofessor",
    },
    {
      key: "puladas",
      label: "Puladas",
      value: stats?.total_puladas?.toLocaleString() || 0,
      color: "var(--text-muted)",
      borderLeft: undefined,
      opacity: 0.7,
      tooltip:
        "Questões que foram puladas pelo especialista por possuirem classificação incorreta Trieduc.",
    },
    {
      key: "total",
      label: "Total do Sistema",
      value: stats?.total_sistema?.toLocaleString() || 0,
      color: "var(--text)",
      borderLeft: "4px solid var(--text-muted)",
      tooltip:
        "Total de questões do recorte atual: somente Ensino Médio com habilidade/assunto definido.",
    },
  ];

  const disciplinasDetalhado = Object.entries(stats?.por_disciplina || {})
    .map(([nome, d]: [string, any]) => ({
      nome,
      total: Number(d?.total || 0),
      feitas: Number(d?.feitas || 0),
      faltam: Number(d?.faltam || 0),
      manuais: Number(d?.manuais || 0),
      auto: Number(d?.auto || 0),
      verificar: Number(d?.verificar || 0),
      pendentes: Number(d?.pendentes || 0),
      puladas: Number(d?.puladas || 0),
    }))
    .sort((a, b) => b.total - a.total);

  const statusSegments = [
    { key: "manuais", label: "Manual", color: "#38a169" },
    { key: "auto", label: "Auto", color: "#ecc94b" },
    { key: "verificar", label: "Verificar", color: "#ed8936" },
    { key: "pendentes", label: "Pendentes", color: "#2b6cb0" },
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
                                pct: disc.total > 0 ? ((bar.value / disc.total) * 100).toFixed(1) : "0",
                                color: bar.color,
                                x: svgRect.left - wrapRect.left + (x + barW / 2) * sx,
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
