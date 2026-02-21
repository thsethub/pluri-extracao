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
                <h1>Monitoramento</h1>
                <p>Visão geral do progresso da classificação manual</p>
            </div>

            <div className={styles.cards}>
                <div className={`${styles.card} glass`}>
                    <span className={styles.cardLabel}>Classificadas Manualmente</span>
                    <h2 className={styles.cardValue} style={{ color: 'var(--success)' }}>
                        {stats?.total_manuais || 0}
                    </h2>
                </div>
                <div className={`${styles.card} glass`}>
                    <span className={styles.cardLabel}>Pendentes (Ensino Médio)</span>
                    <h2 className={styles.cardValue} style={{ color: 'var(--primary)' }}>
                        {stats?.total_pendentes || 0}
                    </h2>
                </div>
            </div>

            <div className={styles.bottomSection}>
                <div className={`${styles.tableCard} glass`}>
                    <h3>Atividades</h3>
                    <table className={styles.table}>
                        <thead>
                            <tr>
                                <th>Professor</th>
                                <th>Total de Ações</th>
                            </tr>
                        </thead>
                        <tbody>
                            {Object.entries(stats?.por_usuario || {}).map(([nome, count]: [string, any]) => (
                                <tr key={nome}>
                                    <td>{nome}</td>
                                    <td className={styles.tableCount}>{count}</td>
                                </tr>
                            ))}
                            {Object.keys(stats?.por_usuario || {}).length === 0 && (
                                <tr>
                                    <td colSpan={2} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>Nenhuma atividade registrada</td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>
            </div>
        </AppLayout>
    );
}
