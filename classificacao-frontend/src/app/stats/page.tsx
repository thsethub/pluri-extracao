'use client';

import { useState, useEffect } from 'react';
import { apiRequest } from '@/lib/api';
import AppLayout from '@/components/AppLayout';
import styles from './Stats.module.css';

export default function StatsPage() {
    const [stats, setStats] = useState<any>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        async function loadStats() {
            try {
                const data = await apiRequest('/stats');
                setStats(data);
            } catch (err) {
                console.error(err);
            } finally {
                setLoading(false);
            }
        }
        loadStats();
    }, []);

    if (loading) return <AppLayout><div className={styles.loading}>Carregando estatísticas...</div></AppLayout>;

    return (
        <AppLayout>
            <div className={styles.header}>
                <div>
                    <h1>Progresso da Classificação</h1>
                    <p>Filtro Ativo: <b>Ensino Médio</b> com Assunto</p>
                </div>
            </div>

            {/* Cards Compactos e Diretos */}
            <div className={styles.cards}>
                <div className={styles.card}>
                    <span className={styles.cardLabel}>Classificadas (Manual)</span>
                    <h2 className={styles.cardValue} style={{ color: 'var(--success)' }}>
                        {stats?.total_manuais?.toLocaleString() || 0}
                    </h2>
                </div>
                <div className={styles.card}>
                    <span className={styles.cardLabel}>Classificadas (Auto)</span>
                    <h2 className={styles.cardValue} style={{ color: 'var(--yellow)' }}>
                        {stats?.total_auto_superpro?.toLocaleString() || 0}
                    </h2>
                </div>
                <div className={styles.card}>
                    <span className={styles.cardLabel}>Pendentes</span>
                    <h2 className={styles.cardValue} style={{ color: 'var(--primary)' }}>
                        {stats?.total_pendentes?.toLocaleString() || 0}
                    </h2>
                </div>
                <div className={styles.card}>
                    <span className={styles.cardLabel}>Faltam Verificar</span>
                    <h2 className={styles.cardValue} style={{ color: 'var(--orange)' }}>
                        {stats?.total_precisa_verificar?.toLocaleString() || 0}
                    </h2>
                </div>
                <div className={styles.card}>
                    <span className={styles.cardLabel}>Puladas</span>
                    <h2 className={styles.cardValue} style={{ color: 'var(--text-muted)', opacity: 0.7 }}>
                        {stats?.total_puladas?.toLocaleString() || 0}
                    </h2>
                </div>
                <div className={styles.card} style={{ borderLeft: '4px solid var(--text-muted)' }}>
                    <span className={styles.cardLabel}>Total do Sistema</span>
                    <h2 className={styles.cardValue}>
                        {stats?.total_sistema?.toLocaleString() || 0}
                    </h2>
                </div>
            </div>

            <div className={styles.bottomSection}>
                {/* Tabela de Disciplinas - Ocupando mais espaço */}
                <div className={styles.tableCard}>
                    <h3>Detalhamento por Disciplina</h3>
                    <div className={styles.tableContainer}>
                        <table className={styles.table}>
                            <thead>
                                <tr>
                                    <th>Disciplina</th>
                                    <th>Faltam</th>
                                    <th>Qtd. Total</th>
                                    <th style={{ textAlign: 'right' }}>Progresso</th>
                                </tr>
                            </thead>
                            <tbody>
                                {Object.entries(stats?.por_disciplina || {})
                                    .sort((a: any, b: any) => b[1].total - a[1].total)
                                    .map(([nome, d]: [string, any]) => {
                                        const pct = d.total > 0 ? ((d.feitas / d.total) * 100).toFixed(0) : "0";
                                        return (
                                            <tr key={nome}>
                                                <td style={{ fontWeight: 600 }}>{nome}</td>
                                                <td style={{ color: 'var(--error)', fontWeight: 600 }}>
                                                    {d.faltam?.toLocaleString()}
                                                </td>
                                                <td style={{ color: 'var(--text-muted)' }}>
                                                    {d.total?.toLocaleString()}
                                                </td>
                                                <td style={{ textAlign: 'right' }}>
                                                    <div className={styles.progressContainer}>
                                                        <div
                                                            className={styles.progressFill}
                                                            style={{ width: `${pct}%` }}
                                                        />
                                                    </div>
                                                    <span className={styles.pctLabel}>{pct}%</span>
                                                </td>
                                            </tr>
                                        );
                                    })}
                            </tbody>
                        </table>
                    </div>
                </div>

                {/* Rank de Atividade - Agora secundário */}
                <div className={styles.tableCard}>
                    <h3>Liderança de Atividade</h3>
                    <div className={styles.tableContainer}>
                        <table className={styles.table}>
                            <thead>
                                <tr>
                                    <th>Nome</th>
                                    <th style={{ textAlign: 'right' }}>Ações</th>
                                </tr>
                            </thead>
                            <tbody>
                                {Object.entries(stats?.por_usuario || {})
                                    .sort((a: any, b: any) => b[1] - a[1])
                                    .map(([nome, count]: [string, any]) => (
                                        <tr key={nome}>
                                            <td>{nome}</td>
                                            <td className={styles.tableCount} style={{ textAlign: 'right', color: 'var(--primary)' }}>
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
