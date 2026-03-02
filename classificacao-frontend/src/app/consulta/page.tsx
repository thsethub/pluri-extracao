'use client';

import { FormEvent, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import AppLayout from '@/components/AppLayout';
import { apiRequest } from '@/lib/api';
import { getUsuario } from '@/lib/auth';
import { Search, AlertCircle, Info } from 'lucide-react';
import styles from './Consulta.module.css';

type QuestaoConsulta = {
    id: number;
    questao_id: string;
    enunciado: string;
    enunciado_html?: string | null;
    texto_base?: string | null;
    texto_base_html?: string | null;
    disciplina_nome?: string | null;
    habilidade_id?: number | null;
    habilidade_descricao?: string | null;
    alternativas?: Array<{
        ordem: number;
        conteudo: string;
        conteudo_html?: string | null;
        correta?: boolean;
    }>;
    classificacao_extracao?: string[] | null;
    classificacao_nao_enquadrada?: string[] | null;
    similaridade?: number | null;
    tem_extracao?: boolean;
    classificacao_manual?: {
        usuario_id: number;
        tipo_acao: string;
        modulos: string[];
        descricoes: string[];
        observacao?: string | null;
        created_at?: string | null;
    } | null;
    modulos_possiveis?: Array<{
        id: number;
        modulo: string;
        descricao: string;
        habilidade_descricao: string;
    }>;
};

export default function ConsultaPage() {
    const router = useRouter();
    const [isAuthorized, setIsAuthorized] = useState(false);
    const [questaoId, setQuestaoId] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [questao, setQuestao] = useState<QuestaoConsulta | null>(null);
    const [modulosSelecionados, setModulosSelecionados] = useState<number[]>([]);

    useEffect(() => {
        const usuario = getUsuario();
        if (!usuario || !usuario.is_admin) {
            router.push('/classificar');
            return;
        }
        setIsAuthorized(true);
    }, [router]);

    const buscarQuestao = async (e: FormEvent) => {
        e.preventDefault();
        setError('');
        setQuestao(null);
        setModulosSelecionados([]);

        const id = Number(questaoId);
        if (!Number.isInteger(id) || id <= 0) {
            setError('Informe um ID numerico valido.');
            return;
        }

        setLoading(true);
        try {
            const data = await apiRequest(`/consulta/${id}`);
            setQuestao(data);
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : 'Nao foi possivel consultar a questao.';
            setError(message);
        } finally {
            setLoading(false);
        }
    };

    const toggleModulo = (moduloId: number) => {
        setModulosSelecionados((prev) =>
            prev.includes(moduloId) ? prev.filter((id) => id !== moduloId) : [...prev, moduloId]
        );
    };

    if (!isAuthorized) return null;

    return (
        <AppLayout>
            <div className={styles.container}>
                <div className={styles.header}>
                    <h1>Consulta</h1>
                    <p>Busca administrativa por ID da questao com o mesmo payload usado no classificar.</p>
                </div>

                <form className={styles.searchCard} onSubmit={buscarQuestao}>
                    <label htmlFor="questaoId">ID da questao</label>
                    <div className={styles.searchRow}>
                        <input
                            id="questaoId"
                            type="number"
                            min={1}
                            value={questaoId}
                            onChange={(e) => setQuestaoId(e.target.value)}
                            placeholder="Ex: 10854"
                        />
                        <button type="submit" disabled={loading}>
                            <Search size={18} />
                            {loading ? 'Buscando...' : 'Buscar'}
                        </button>
                    </div>
                </form>

                {error && (
                    <div className={styles.errorBox}>
                        <AlertCircle size={18} />
                        <span>{error}</span>
                    </div>
                )}

                {questao && (
                    <>
                        <div className={styles.content}>
                            <div className={styles.questaoCard}>
                                <div className={styles.questaoMeta}>
                                    <span className={styles.tag}>{questao.disciplina_nome || '-'}</span>
                                    <span className={styles.habTag}>{questao.habilidade_descricao || '-'}</span>
                                    <span className={styles.idTag}>ID: {questao.id}</span>
                                </div>

                                {questao.texto_base && (
                                    <div
                                        className={styles.textoBase}
                                        dangerouslySetInnerHTML={{ __html: questao.texto_base_html || questao.texto_base }}
                                    />
                                )}

                                <div
                                    className={styles.enunciado}
                                    dangerouslySetInnerHTML={{ __html: questao.enunciado_html || questao.enunciado }}
                                />

                                {questao.alternativas && questao.alternativas.length > 0 && (
                                    <div className={styles.alternativas}>
                                        {questao.alternativas.map((alt, index) => (
                                            <div key={`${alt.ordem}-${index}`} className={`${styles.altItem} ${alt.correta ? styles.altCorreta : ''}`}>
                                                <span className={styles.altLetra}>{String.fromCharCode(97 + index)})</span>
                                                <span dangerouslySetInnerHTML={{ __html: alt.conteudo_html || alt.conteudo }} />
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>

                            <div className={styles.sideCard}>
                                <div className={styles.sideHeader}>
                                    <Info size={18} />
                                    <h3>Escolha de Modulo e Assunto</h3>
                                </div>

                                <div className={styles.sideSection}>
                                    <h4>Selecione um ou mais modulos</h4>
                                    <p className={styles.helperText}>Consulta somente visual (nao salva classificacao).</p>
                                    {questao.modulos_possiveis && questao.modulos_possiveis.length > 0 ? (
                                        <div className={styles.moduloChoiceList}>
                                            {questao.modulos_possiveis.map((m) => (
                                                <label
                                                    key={m.id}
                                                    className={`${styles.moduloChoiceItem} ${modulosSelecionados.includes(m.id) ? styles.moduloChoiceSelected : ''}`}
                                                >
                                                    <input
                                                        type="checkbox"
                                                        checked={modulosSelecionados.includes(m.id)}
                                                        onChange={() => toggleModulo(m.id)}
                                                    />
                                                    <div className={styles.moduloChoiceText}>
                                                        <strong>{m.modulo}</strong>
                                                        <span>{m.descricao}</span>
                                                    </div>
                                                </label>
                                            ))}
                                        </div>
                                    ) : (
                                        <p>-</p>
                                    )}
                                </div>

                                <div className={styles.sideSection}>
                                    <h4>Selecao atual</h4>
                                    {modulosSelecionados.length > 0 ? (
                                        <div className={styles.moduloList}>
                                            {questao.modulos_possiveis
                                                ?.filter((m) => modulosSelecionados.includes(m.id))
                                                .map((m) => (
                                                    <div key={`selected-${m.id}`} className={styles.moduloItem}>
                                                        <strong>{m.modulo}</strong>
                                                        <span>{m.descricao}</span>
                                                    </div>
                                                ))}
                                        </div>
                                    ) : (
                                        <p>Nenhum modulo selecionado.</p>
                                    )}
                                </div>

                                <div className={styles.sideSection}>
                                    <h4>Classificacao manual</h4>
                                    {questao.classificacao_manual ? (
                                        <div className={styles.manualBox}>
                                            <p><strong>Usuario ID:</strong> {questao.classificacao_manual.usuario_id}</p>
                                            <p><strong>Tipo de acao:</strong> {questao.classificacao_manual.tipo_acao}</p>
                                            <p>
                                                <strong>Data:</strong>{' '}
                                                {questao.classificacao_manual.created_at
                                                    ? new Date(questao.classificacao_manual.created_at).toLocaleString()
                                                    : '-'}
                                            </p>
                                            <p>
                                                <strong>Modulos:</strong>{' '}
                                                {questao.classificacao_manual.modulos?.length
                                                    ? questao.classificacao_manual.modulos.join(' | ')
                                                    : '-'}
                                            </p>
                                            <p>
                                                <strong>Assuntos:</strong>{' '}
                                                {questao.classificacao_manual.descricoes?.length
                                                    ? questao.classificacao_manual.descricoes.join(' | ')
                                                    : '-'}
                                            </p>
                                            <p>
                                                <strong>Observacao:</strong>{' '}
                                                {questao.classificacao_manual.observacao || '-'}
                                            </p>
                                        </div>
                                    ) : (
                                        <p>Sem classificacao manual em classificacao_usuario.</p>
                                    )}
                                </div>

                                <div className={styles.sideSection}>
                                    <h4>Extracao</h4>
                                    <p><strong>Tem extracao:</strong> {questao.tem_extracao ? 'Sim' : 'Nao'}</p>
                                    <p><strong>Similaridade:</strong> {questao.similaridade ?? '-'}</p>
                                    <p><strong>Classificacao extracao:</strong> {questao.classificacao_extracao?.length ? questao.classificacao_extracao.join(' | ') : '-'}</p>
                                    <p><strong>Nao enquadrada:</strong> {questao.classificacao_nao_enquadrada?.length ? questao.classificacao_nao_enquadrada.join(' | ') : '-'}</p>
                                </div>
                            </div>
                        </div>

                        <div className={styles.rawCard}>
                            <h3>Resposta bruta da API</h3>
                            <pre>{JSON.stringify(questao, null, 2)}</pre>
                        </div>
                    </>
                )}
            </div>
        </AppLayout>
    );
}
