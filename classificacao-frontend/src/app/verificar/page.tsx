'use client';

import { useState, useEffect } from 'react';
import { apiRequest } from '@/lib/api';
import AppLayout from '@/components/AppLayout';
import FilterBar from '@/components/FilterBar';
import {
    CheckCircle,
    Pencil,
    FastForward,
    Save,
    AlertTriangle,
    ArrowLeft,
    Info,
    Zap,
    Bot
} from 'lucide-react';
import styles from '../classificar/Classificar.module.css';

function MatchBadge({ score }: { score: number | null | undefined }) {
    if (score == null) return null;
    const pct = Math.round(score * 100);
    let color = '#991b1b';
    let bg = '#fecaca';
    let borderColor = '#f87171';
    if (pct >= 80) { color = '#166534'; bg = '#bbf7d0'; borderColor = '#4ade80'; }
    else if (pct >= 60) { color = '#854d0e'; bg = '#fef08a'; borderColor = '#facc15'; }

    return (
        <span style={{
            display: 'inline-flex', alignItems: 'center', gap: '0.3rem',
            padding: '0.3rem 0.7rem', borderRadius: '12px', fontSize: '0.8rem',
            fontWeight: 700, color, background: bg, border: `1.5px solid ${borderColor}`,
        }}>
            <Zap size={12} />
            {pct}%
        </span>
    );
}

export default function VerificarPage() {
    const [questao, setQuestao] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const usuario = typeof window !== 'undefined' ? JSON.parse(localStorage.getItem('usuario') || '{}') : null;
    const [area, setArea] = useState(usuario?.disciplina || '');
    const [disciplinaFiltro, setDisciplinaFiltro] = useState('');
    const [saving, setSaving] = useState(false);
    const [observacao, setObservacao] = useState('');
    const [moduloSelecionado, setModuloSelecionado] = useState<any>(null);
    const [isCorrecting, setIsCorrecting] = useState(false);

    const fetchProxima = async (areaFiltro?: string, discFiltro?: string) => {
        setLoading(true);
        setError('');
        setQuestao(null);
        setModuloSelecionado(null);
        setObservacao('');
        setIsCorrecting(false);

        try {
            const query = new URLSearchParams();
            if (areaFiltro) query.append('area', areaFiltro);
            if (discFiltro) query.append('disciplina_id', discFiltro);

            const params = query.toString() ? `?${query.toString()}` : '';
            const data = await apiRequest(`/proxima-low-match${params}`);
            setQuestao(data);
        } catch (err: any) {
            setError(err.message || 'Nenhuma questão de baixa similaridade pendente.');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchProxima(area, disciplinaFiltro);
    }, [area, disciplinaFiltro]);

    const handleConfirmar = async () => {
        setSaving(true);
        try {
            await apiRequest('/salvar', {
                method: 'POST',
                body: JSON.stringify({
                    questao_id: questao.id,
                    tipo_acao: 'confirmacao',
                    observacao
                })
            });
            fetchProxima(area, disciplinaFiltro);
        } catch (err: any) {
            alert(err.message);
        } finally {
            setSaving(false);
        }
    };

    const handleSalvarCorrecao = async () => {
        if (!moduloSelecionado) return;
        setSaving(true);
        try {
            await apiRequest('/salvar', {
                method: 'POST',
                body: JSON.stringify({
                    questao_id: questao.id,
                    habilidade_modulo_id: moduloSelecionado.id,
                    modulo_escolhido: moduloSelecionado.modulo,
                    classificacao_trieduc: moduloSelecionado.habilidade_descricao,
                    descricao_assunto: moduloSelecionado.descricao,
                    tipo_acao: 'correcao',
                    observacao
                })
            });
            fetchProxima(area, disciplinaFiltro);
        } catch (err: any) {
            alert(err.message);
        } finally {
            setSaving(false);
        }
    };

    const lowMatchTags = questao?.classificacao_nao_enquadrada || [];

    return (
        <AppLayout>
            <div className={styles.header}>
                <div className={styles.headerInfo}>
                    <h1>Verificação de Baixa Similaridade</h1>
                    <p>Revise classificações do SuperProfessor com match inferior a 80%</p>
                </div>
            </div>

            <FilterBar onFilterChange={(a, d) => {
                setArea(a);
                setDisciplinaFiltro(d);
            }} />

            {loading ? (
                <div className={styles.loading}>
                    <div className={styles.spinner}></div>
                    <span>Buscando questão pendente...</span>
                </div>
            ) : error ? (
                <div className={styles.empty}>
                    <CheckCircle size={48} color="var(--success)" />
                    <p>{error}</p>
                    <button onClick={() => fetchProxima(area, disciplinaFiltro)}>Tentar Novamente</button>
                </div>
            ) : questao && (
                <div className={styles.content}>
                    {/* Left: Question Card */}
                    <div className={`${styles.questaoCard} glass fade-in`}>
                        <div className={styles.questaoMeta}>
                            <span className={styles.tag}>{questao.disciplina_nome}</span>
                            <span className={styles.habTag}>{questao.habilidade_descricao}</span>
                            <span className={styles.idTag}>ID: {questao.id}</span>
                        </div>

                        <div
                            className={styles.enunciado}
                            dangerouslySetInnerHTML={{ __html: questao.enunciado_html || questao.enunciado }}
                        />

                        {questao.alternativas && questao.alternativas.length > 0 && (
                            <div className={styles.alternativas}>
                                {questao.alternativas.map((alt: any, index: number) => (
                                    <div key={index} className={`${styles.altItem} ${alt.correta ? styles.altCorreta : ''}`}>
                                        <span className={styles.altLetra}>{String.fromCharCode(97 + index)})</span>
                                        <span dangerouslySetInnerHTML={{ __html: alt.conteudo_html || alt.conteudo }} />
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* Right: Action Card */}
                    <div className={`${styles.moduloCard} glass fade-in`}>
                        {!isCorrecting ? (
                            <>
                                <div className={styles.moduloHeader}>
                                    <AlertTriangle size={18} />
                                    <h3>Validação Necessária</h3>
                                </div>

                                {/* SuperProfessor classification box */}
                                <div className="superpro-banner">
                                    <div className="superpro-header">
                                        <div className="superpro-title">
                                            <Bot size={16} />
                                            <strong>Classificação SuperProfessor</strong>
                                        </div>
                                        <MatchBadge score={questao.similaridade} />
                                    </div>
                                    {lowMatchTags.length > 0 && (
                                        <div className="superpro-tags">
                                            {lowMatchTags.map((tag: string, i: number) => (
                                                <span key={i} className="low-match-tag">{tag}</span>
                                            ))}
                                        </div>
                                    )}
                                </div>

                                <p className={styles.moduloHint}>
                                    A classificação do SuperProfessor está correta para esta questão?
                                </p>

                                <div className="confirm-actions">
                                    <button
                                        onClick={handleConfirmar}
                                        disabled={saving}
                                        className="confirm-btn"
                                    >
                                        <CheckCircle size={20} />
                                        {saving ? 'Confirmando...' : 'Sim, está correta'}
                                    </button>
                                    <button
                                        onClick={() => setIsCorrecting(true)}
                                        className="correct-btn"
                                    >
                                        <Pencil size={18} />
                                        Não, quero corrigir
                                    </button>
                                </div>

                                <div className={styles.actionArea}>
                                    <textarea
                                        placeholder="Descreva brevemente o motivo da confirmação ou correção"
                                        value={observacao}
                                        onChange={(e) => setObservacao(e.target.value)}
                                    />
                                    <div className={styles.buttons}>
                                        <button
                                            onClick={() => fetchProxima(area, disciplinaFiltro)}
                                            className={styles.skipBtn}
                                        >
                                            <FastForward size={18} />
                                            Pular
                                        </button>
                                    </div>
                                </div>
                            </>
                        ) : (
                            <>
                                {/* SuperPro classification context in correction mode too */}
                                <div className="superpro-banner superpro-banner-compact">
                                    <div className="superpro-header">
                                        <Bot size={14} />
                                        <strong>SuperProfessor sugeriu:</strong>
                                    </div>
                                    {lowMatchTags.length > 0 && (
                                        <div className="superpro-tags">
                                            {lowMatchTags.map((tag: string, i: number) => (
                                                <span key={i} className="low-match-tag">{tag}</span>
                                            ))}
                                        </div>
                                    )}
                                </div>

                                <div className={styles.moduloHeader}>
                                    <Pencil size={18} />
                                    <h3>Corrigir Classificação</h3>
                                </div>
                                <p className={styles.moduloHint}>Selecione o módulo correto abaixo:</p>
                                <div className={styles.moduloList}>
                                    {questao.modulos_possiveis.map((m: any) => (
                                        <label
                                            key={m.id}
                                            className={`${styles.moduloItem} ${moduloSelecionado?.id === m.id ? styles.moduloSelected : ''}`}
                                        >
                                            <input
                                                type="radio"
                                                name="modulo"
                                                checked={moduloSelecionado?.id === m.id}
                                                onChange={() => setModuloSelecionado(m)}
                                            />
                                            <div className={styles.moduloText}>
                                                <strong>{m.modulo}</strong>
                                                <span>{m.descricao}</span>
                                            </div>
                                        </label>
                                    ))}
                                </div>

                                <div className={styles.actionArea}>
                                    <textarea
                                        placeholder="Descreva brevemente o motivo da correção"
                                        value={observacao}
                                        onChange={(e) => setObservacao(e.target.value)}
                                    />
                                    <div className={styles.buttons}>
                                        <button onClick={() => setIsCorrecting(false)} className={styles.skipBtn}>
                                            <ArrowLeft size={18} />
                                            Voltar
                                        </button>
                                        <button
                                            onClick={handleSalvarCorrecao}
                                            disabled={!moduloSelecionado || saving}
                                            className={styles.saveBtn}
                                        >
                                            <Save size={18} />
                                            {saving ? 'Salvando...' : 'Salvar Correção'}
                                        </button>
                                    </div>
                                </div>
                            </>
                        )}
                    </div>
                </div>
            )}

            <style jsx>{`
                .superpro-banner {
                    background: linear-gradient(135deg, #eff6ff, #e0f0ff);
                    border: 1px solid #bfdbfe;
                    padding: 1rem 1.2rem;
                    border-radius: 12px;
                    margin-bottom: 1.2rem;
                }
                .superpro-banner-compact {
                    padding: 0.75rem 1rem;
                    margin-bottom: 0.8rem;
                }
                .superpro-header {
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    font-size: 0.88rem;
                    color: #1e40af;
                    margin-bottom: 0.6rem;
                }
                .superpro-title {
                    display: flex;
                    align-items: center;
                    gap: 0.5rem;
                }
                .superpro-banner-compact .superpro-header {
                    font-size: 0.8rem;
                }
                .superpro-tags {
                    display: flex;
                    flex-wrap: wrap;
                    gap: 0.4rem;
                }
                .low-match-tag {
                    background-color: #dbeafe;
                    color: #1e3a8a;
                    padding: 0.25rem 0.7rem;
                    border-radius: 6px;
                    font-size: 0.75rem;
                    font-weight: 600;
                    border: 1px solid #93c5fd60;
                }
                .confirm-actions {
                    display: flex;
                    flex-direction: column;
                    gap: 0.8rem;
                    margin: 1rem 0;
                }
                .confirm-btn {
                    background: var(--success);
                    color: white;
                    border: none;
                    padding: 0.85rem;
                    border-radius: 10px;
                    font-weight: 700;
                    font-size: 0.95rem;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    gap: 0.6rem;
                    cursor: pointer;
                    transition: all 0.2s;
                    box-shadow: 0 2px 8px rgba(56, 161, 105, 0.25);
                }
                .confirm-btn:hover {
                    background: #2f855a;
                    transform: translateY(-1px);
                    box-shadow: 0 4px 12px rgba(56, 161, 105, 0.35);
                }
                .confirm-btn:disabled {
                    opacity: 0.5;
                    cursor: not-allowed;
                    transform: none;
                }
                .correct-btn {
                    background: none;
                    border: 1.5px solid var(--primary);
                    color: var(--primary);
                    padding: 0.75rem;
                    border-radius: 10px;
                    font-weight: 600;
                    font-size: 0.9rem;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    gap: 0.6rem;
                    cursor: pointer;
                    transition: all 0.2s;
                }
                .correct-btn:hover {
                    background: var(--primary-light);
                    transform: translateY(-1px);
                }
            `}</style>
        </AppLayout>
    );
}
