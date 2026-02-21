'use client';

import { useState, useEffect } from 'react';
import { apiRequest } from '@/lib/api';
import AppLayout from '@/components/AppLayout';
import FilterBar from '@/components/FilterBar';
import { FastForward, Save, Info, AlertCircle } from 'lucide-react';
import styles from './Classificar.module.css';

export default function ClassificarPage() {
    const [questao, setQuestao] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const usuario = typeof window !== 'undefined' ? JSON.parse(localStorage.getItem('usuario') || '{}') : null;
    const [area, setArea] = useState(usuario?.disciplina || '');
    const [disciplinaFiltro, setDisciplinaFiltro] = useState('');
    const [saving, setSaving] = useState(false);
    const [observacao, setObservacao] = useState('');
    const [moduloSelecionado, setModuloSelecionado] = useState<any>(null);

    const fetchProxima = async (areaFiltro?: string, discFiltro?: string) => {
        setLoading(true);
        setError('');
        setQuestao(null);
        setModuloSelecionado(null);
        setObservacao('');

        try {
            const query = new URLSearchParams();
            if (areaFiltro) query.append('area', areaFiltro);
            if (discFiltro) query.append('disciplina_id', discFiltro);

            const params = query.toString() ? `?${query.toString()}` : '';
            const data = await apiRequest(`/proxima${params}`);
            setQuestao(data);
        } catch (err: any) {
            setError(err.message || 'Nenhuma questão encontrada com esses filtros.');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchProxima(area, disciplinaFiltro);
    }, [area, disciplinaFiltro]);

    const handleSalvar = async () => {
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
                    tipo_acao: 'classificacao_nova',
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

    return (
        <AppLayout>
            <div className={styles.header}>
                <div className={styles.headerInfo}>
                    <h1>Classificação Manual</h1>
                    <p>Associe a questão ao módulo educacional correto</p>
                </div>
            </div>

            <FilterBar onFilterChange={(a, d) => {
                setArea(a);
                setDisciplinaFiltro(d);
            }} />

            {loading ? (
                <div className={styles.loading}>
                    <div className={styles.spinner}></div>
                    <span>Carregando próxima questão...</span>
                </div>
            ) : error ? (
                <div className={styles.empty}>
                    <AlertCircle size={48} color="var(--primary)" />
                    <p>{error}</p>
                    <button onClick={() => fetchProxima(area, disciplinaFiltro)}>Tentar Novamente</button>
                </div>
            ) : questao && (
                <div className={styles.content}>
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

                    <div className={`${styles.moduloCard} glass fade-in`}>
                        <div className={styles.moduloHeader}>
                            <Info size={18} />
                            <h3>Sugestões de Módulos</h3>
                        </div>
                        <p className={styles.moduloHint}>Selecione o módulo mais adequado:</p>

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
                                placeholder="Descreva brevemente o motivo da classificação"
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
                                <button
                                    onClick={handleSalvar}
                                    disabled={!moduloSelecionado || saving}
                                    className={styles.saveBtn}
                                >
                                    <Save size={18} />
                                    {saving ? 'Gravando...' : 'Salvar Classificação'}
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </AppLayout>
    );
}
