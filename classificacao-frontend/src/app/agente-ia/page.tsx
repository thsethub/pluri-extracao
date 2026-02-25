'use client';

import { useState, useEffect, useRef } from 'react';
import AppLayout from '@/components/AppLayout';
import { apiRequest, API_BASE_URL } from '@/lib/api';
import { showToast } from '@/components/Toast';
import { Play, RotateCcw, Activity, Terminal as TerminalIcon, Search, Eye, X } from 'lucide-react';
import Dropdown from '@/components/Dropdown';
import styles from './AgenteIA.module.css';

interface LogLine {
    time: string;
    level: string;
    message: string;
    type: 'info' | 'success' | 'error' | 'warning';
}

interface ClassificacaoItem {
    questao_id: number;
    modulos_preditos: string[];
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
    disciplina: string;
    habilidade_trieduc: any;
    ia: {
        modulos_preditos: string[];
        justificativas: Record<string, string>;
        modulos_possiveis: string[];
        descricoes_modulos: Record<string, string>;
        confianca_media: number;
        modelo_utilizado: string;
        usou_llm: boolean;
    };
    manual: {
        modulos: string[];
        descricoes: string[];
    } | null;
    comparacao: {
        match_status: 'exact' | 'partial' | 'none';
        modulos_extra: string[];
        modulos_faltando: string[];
    } | null;
}

export default function AgenteIAPage() {
    const [logs, setLogs] = useState<LogLine[]>([]);
    const [isRunning, setIsRunning] = useState(false);

    // Filtros
    const [filterModelo, setFilterModelo] = useState<string>('');
    const [filterDisciplina, setFilterDisciplina] = useState<string>('');
    const [filterMatch, setFilterMatch] = useState<string>('');

    // Lista de classificações
    const [classificacoes, setClassificacoes] = useState<ClassificacaoItem[]>([]);
    const [disciplinaOptions, setDisciplinaOptions] = useState<{ value: string, label: string }[]>([]);
    const [page, setPage] = useState(1);
    const [totalPages, setTotalPages] = useState(1);
    const [totalItems, setTotalItems] = useState(0);
    const [loadingList, setLoadingList] = useState(false);

    // Modal
    const [selectedQid, setSelectedQid] = useState<number | null>(null);
    const [detailData, setDetailData] = useState<ClassificacaoDetail | null>(null);
    const [loadingDetail, setLoadingDetail] = useState(false);

    const logEndRef = useRef<HTMLDivElement>(null);
    const eventSourceRef = useRef<EventSource | null>(null);

    useEffect(() => {
        async function loadDisciplinas() {
            try {
                const data = await apiRequest('/disciplinas');
                const options = Object.values(data.areas).flat().map((d: any) => ({ value: d, label: d }));
                setDisciplinaOptions(options);
            } catch (err) {
                console.error(err);
            }
        }
        loadDisciplinas();
    }, []);

    useEffect(() => {
        if (logEndRef.current) {
            logEndRef.current.scrollIntoView({ behavior: 'smooth' });
        }
    }, [logs]);

    useEffect(() => {
        fetchClassificacoes(page);
    }, [page, filterModelo, filterDisciplina, filterMatch]);

    const addLog = (message: string, type: LogLine['type'] = 'info') => {
        const time = new Date().toLocaleTimeString();
        setLogs(prev => [...prev.slice(-200), { time, level: type.toUpperCase(), message, type }]); // maintain last 200 logs
    };

    const fetchClassificacoes = async (pageNum: number) => {
        setLoadingList(true);
        try {
            const baseUrlIA = API_BASE_URL.replace('/classificacao', '/classificacao-ia');
            const data = await apiRequest(`/classificacoes?page=${pageNum}&per_page=15&modelo_filter=${filterModelo}&disciplina_filter=${filterDisciplina}&match_filter=${filterMatch}`, { customBaseUrl: baseUrlIA });
            setClassificacoes(data.items || []);
            setTotalPages(data.pages || 1);
            setTotalItems(data.total || 0);
        } catch (err: any) {
            console.error('Erro ao buscar lista:', err);
        } finally {
            setLoadingList(false);
        }
    };

    const openDetailModal = async (questao_id: number) => {
        setSelectedQid(questao_id);
        setLoadingDetail(true);
        setDetailData(null);
        try {
            const baseUrlIA = API_BASE_URL.replace('/classificacao', '/classificacao-ia');
            const data = await apiRequest(`/classificacao/${questao_id}`, { customBaseUrl: baseUrlIA });
            setDetailData(data);
        } catch (err: any) {
            showToast('Erro ao carregar detalhes', 'error');
            setSelectedQid(null);
        } finally {
            setLoadingDetail(false);
        }
    };

    const handleStartValidation = () => {
        if (isRunning) return;
        setIsRunning(true);
        setLogs([]);
        addLog('Iniciando validação streaming via SSE...', 'info');

        const baseUrl = API_BASE_URL.replace('/classificacao', '');
        const eventSource = new EventSource(`${baseUrl}/classificacao-ia/validar-stream`);
        eventSourceRef.current = eventSource;

        eventSource.onopen = () => addLog('Conexão SSE estabelecida.', 'success');

        eventSource.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);

                if (data.type === 'start') {
                    addLog(`Total de questões a validar: ${data.total}`, 'info');
                } else if (data.type === 'progress') {
                    addLog(`[${data.index}/${data.total}] QID ${data.questao_id} | Manual: [${data.manual?.join(',')}] | IA: [${data.modulos_preditos?.join(',')}] | Match: ${data.match}`, data.match === 'exact' ? 'success' : 'warning');
                } else if (data.type === 'error') {
                    addLog(`Erro na QID ${data.questao_id}: ${data.error}`, 'error');
                } else if (data.type === 'done') {
                    addLog(`Validação CONCLUÍDA! Sucesso: ${data.sucesso}, Erros: ${data.erros}`, 'success');
                    addLog(`Custo total: $${data.total_cost} | Tokens: ${data.total_tokens}`, 'info');
                    eventSource.close();
                    setIsRunning(false);
                    fetchClassificacoes(1); // recarrega a lista
                } else if (data.type === 'fatal_error') {
                    addLog(`Erro fatal: ${data.error}`, 'error');
                    eventSource.close();
                    setIsRunning(false);
                }
            } catch (err) {
                console.error("Erro parse SSE", err);
            }
        };

        eventSource.onerror = (err) => {
            addLog('Conexão SSE perdida ou erro.', 'error');
            eventSource.close();
            setIsRunning(false);
        };
    };

    const stopValidation = () => {
        if (eventSourceRef.current) {
            eventSourceRef.current.close();
            addLog('Validação parada pelo usuário.', 'warning');
            setIsRunning(false);
            fetchClassificacoes(1);
        }
    };

    const handleReloadPrompts = async () => {
        try {
            const baseUrlIA = API_BASE_URL.replace('/classificacao', '/classificacao-ia');
            const res = await apiRequest('/reload-prompts', { method: 'POST', customBaseUrl: baseUrlIA });
            showToast(res.message, 'success');
            addLog('Prompts recarregados com sucesso.', 'success');
        } catch (err: any) {
            showToast(err.message, 'error');
            addLog('Erro ao recarregar prompts: ' + err.message, 'error');
        }
    };

    return (
        <AppLayout>
            <div className={styles.container}>
                <div className={styles.header}>
                    <div>
                        <h1>Agente de IA</h1>
                        <p>Validação massiva do modelo LLM e histórico de classificações.</p>
                    </div>
                    <div className={styles.actions}>
                        <button className={styles.btnSecondary} onClick={handleReloadPrompts}>
                            <RotateCcw size={18} />
                            Recarregar Prompts
                        </button>
                        {!isRunning ? (
                            <button className={styles.btnPrimary} onClick={handleStartValidation}>
                                <Play size={18} />
                                Validar Base (Stream)
                            </button>
                        ) : (
                            <button className={styles.btnPrimary} style={{ backgroundColor: '#f44336' }} onClick={stopValidation}>
                                Parar Validação
                            </button>
                        )}
                    </div>
                </div>

                <div className={styles.grid}>
                    <div className={styles.card}>
                        <h3>
                            <TerminalIcon size={20} />
                            Console do Stream de Validação
                        </h3>
                        <div className={styles.logContainer}>
                            {logs.length === 0 && (
                                <div className={styles.logEntry} style={{ opacity: 0.5 }}>
                                    Aguardando validação SSE...
                                </div>
                            )}
                            {logs.map((log, i) => (
                                <div key={i} className={styles.logEntry}>
                                    <span style={{ color: '#888' }}>[{log.time}]</span>{' '}
                                    <span className={styles[`log${log.type.charAt(0).toUpperCase() + log.type.slice(1)}`]}>
                                        {log.level}:
                                    </span>{' '}
                                    {log.message}
                                </div>
                            ))}
                            <div ref={logEndRef} />
                        </div>
                    </div>

                    <div className={styles.card}>
                        <h3>Últimas Classificações ({totalItems})</h3>

                        {/* Filtros */}
                        <div style={{ display: 'flex', gap: '1rem', marginBottom: '1.5rem', flexWrap: 'wrap', zIndex: 10 }}>
                            <div style={{ minWidth: '220px' }}>
                                <Dropdown
                                    label="Filtro de Modelo Web"
                                    options={[
                                        { value: '', label: 'Todos os Modelos' },
                                        { value: 'gpt-4o-mini_prompt_v2', label: 'GPT-4o-mini (V2)' },
                                        { value: 'gpt-4o-mini_prompt_v1', label: 'GPT-4o-mini (V1)' },
                                        { value: 'logistic_regression', label: 'Regressão Logística' }
                                    ]}
                                    value={filterModelo}
                                    onChange={(v: any) => { setFilterModelo(v); setPage(1); }}
                                    placeholder="Todos os Modelos"
                                />
                            </div>

                            <div style={{ minWidth: '220px' }}>
                                <Dropdown
                                    label="Status do Match"
                                    options={[
                                        { value: '', label: 'Qualquer Status (Match)' },
                                        { value: 'exact', label: 'Match Exato' },
                                        { value: 'partial', label: 'Match Parcial' },
                                        { value: 'none', label: 'Sem Match' },
                                        { value: 'pending', label: 'Não classificada manual' }
                                    ]}
                                    value={filterMatch}
                                    onChange={(v: any) => { setFilterMatch(v); setPage(1); }}
                                    placeholder="Qualquer Status"
                                />
                            </div>

                            <div style={{ minWidth: '220px' }}>
                                <Dropdown
                                    label="Disciplina"
                                    options={[{ value: '', label: 'Todas as Disciplinas' }, ...disciplinaOptions]}
                                    value={filterDisciplina}
                                    onChange={(v: any) => { setFilterDisciplina(v); setPage(1); }}
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
                                        <tr><td colSpan={5} style={{ textAlign: 'center', opacity: 0.5 }}>Carregando...</td></tr>
                                    ) : classificacoes.length === 0 ? (
                                        <tr><td colSpan={5} style={{ textAlign: 'center', opacity: 0.5 }}>Nenhuma classificação encontrada.</td></tr>
                                    ) : classificacoes.map((item) => (
                                        <tr key={item.questao_id} className={styles.tableBodyRow} onClick={() => openDetailModal(item.questao_id)}>
                                            <td><strong>{item.questao_id}</strong></td>
                                            <td>{item.disciplina || '-'}</td>
                                            <td>
                                                <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                                                    {item.modulos_preditos?.map(m => (
                                                        <span key={m} style={{ fontSize: '0.8rem' }}>• {m}</span>
                                                    )) || '-'}
                                                </div>
                                            </td>
                                            <td>
                                                <span className={`${styles.badge} ${item.usou_llm ? styles.badgeLLM : styles.badgeLegacy}`}>
                                                    {item.modelo_utilizado}
                                                </span>
                                            </td>
                                            <td>{item.created_at ? new Date(item.created_at).toLocaleString() : '-'}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>

                            {totalPages > 1 && (
                                <div className={styles.pagination}>
                                    <span>Página {page} de {totalPages}</span>
                                    <div className={styles.pageControls}>
                                        <button
                                            className={styles.pageBtn}
                                            disabled={page === 1}
                                            onClick={() => setPage(p => p - 1)}
                                        >Anterior</button>
                                        <button
                                            className={styles.pageBtn}
                                            disabled={page === totalPages}
                                            onClick={() => setPage(p => p + 1)}
                                        >Próxima</button>
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>
                </div>

                {/* Modal de Detalhes */}
                {selectedQid !== null && (
                    <div className={styles.modalOverlay} onClick={() => setSelectedQid(null)}>
                        <div className={styles.modalContent} onClick={e => e.stopPropagation()}>
                            <button className={styles.closeButton} onClick={() => setSelectedQid(null)}>
                                <X size={24} />
                            </button>

                            <h2>Detalhes da Questão: {selectedQid}</h2>
                            <p style={{ color: 'var(--text-muted)' }}>Classificação e Justificativas da IA.</p>

                            {loadingDetail ? (
                                <p style={{ marginTop: '2rem' }}>Carregando dados da questão...</p>
                            ) : detailData ? (
                                <>
                                    <div className={styles.statsGrid}>
                                        <div className={styles.statCard}>
                                            <span className={styles.statValue}>{detailData.disciplina || '-'}</span>
                                            <span className={styles.statLabel}>Disciplina</span>
                                        </div>
                                        <div className={styles.statCard}>
                                            <span className={styles.statValue}>
                                                <span className={`${styles.badge} ${detailData.ia?.usou_llm ? styles.badgeLLM : styles.badgeLegacy}`}>
                                                    {detailData.ia?.modelo_utilizado}
                                                </span>
                                            </span>
                                            <span className={styles.statLabel}>Modelo Utilizado</span>
                                        </div>
                                        {detailData.comparacao && (
                                            <div className={styles.statCard}>
                                                <span className={styles.statValue}>
                                                    <span className={`${styles.badge} ${detailData.comparacao.match_status === 'exact' ? styles.badgeMatchExact :
                                                        detailData.comparacao.match_status === 'partial' ? styles.badgeMatchPartial : styles.badgeMatchNone
                                                        }`}>
                                                        {detailData.comparacao.match_status === 'exact' ? 'MATCH EXATO' :
                                                            detailData.comparacao.match_status === 'partial' ? 'PARCIAL' : 'SEM MATCH'}
                                                    </span>
                                                </span>
                                                <span className={styles.statLabel}>Comparação com Manual</span>
                                            </div>
                                        )}
                                    </div>

                                    <div className={styles.detailSection}>
                                        <h4>Classificação do Professor (Manual)</h4>
                                        {detailData.manual && detailData.manual.modulos?.length > 0 ? (
                                            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                                                {detailData.manual.modulos.map((mod, i) => (
                                                    <div key={i} className={styles.justificativaBox}>
                                                        <strong>[{mod}]</strong>
                                                        {detailData.manual?.descricoes?.[i] ? (
                                                            <div style={{ marginTop: '0.25rem', fontSize: '0.85rem', opacity: 0.9 }}>
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
                                        <p>Módulos sugeridos: <strong>{detailData.ia?.modulos_preditos?.join(', ') || '-'}</strong></p>

                                        {detailData.ia?.modulos_preditos && detailData.ia.modulos_preditos.length > 0 && (
                                            <div style={{ marginTop: '1rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                                                {detailData.ia.modulos_preditos.map((mod) => (
                                                    <div key={mod} className={styles.justificativaBox}>
                                                        <div style={{ marginBottom: '0.5rem' }}>
                                                            <strong>[{mod}]</strong>
                                                            {detailData.ia?.descricoes_modulos?.[mod] && (
                                                                <span style={{ fontSize: '0.85rem', opacity: 0.9, marginLeft: '0.5rem' }}>
                                                                    - {detailData.ia.descricoes_modulos[mod]}
                                                                </span>
                                                            )}
                                                        </div>
                                                        {detailData.ia?.justificativas?.[mod] && (
                                                            <div>
                                                                <strong>Justificativa da IA:</strong> {detailData.ia.justificativas[mod] as string}
                                                            </div>
                                                        )}
                                                    </div>
                                                ))}
                                            </div>
                                        )}
                                    </div>

                                    <div className={styles.detailSection}>
                                        <h4>Enunciado da Questão</h4>
                                        <div className={styles.enunciadoBox} dangerouslySetInnerHTML={{ __html: detailData.enunciado || 'Enunciado não disponível' }} />
                                    </div>
                                </>
                            ) : (
                                <p style={{ marginTop: '2rem', color: '#ff6c6b' }}>Erro ao carregar detalhes.</p>
                            )}
                        </div>
                    </div>
                )}
            </div>
        </AppLayout>
    );
}
