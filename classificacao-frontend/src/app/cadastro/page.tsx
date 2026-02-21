'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { apiRequest } from '@/lib/api';
import { setAuth } from '@/lib/auth';
import Dropdown from '@/components/Dropdown';
import styles from '../login/Login.module.css'; // Reaproveitando estilos bases

export default function CadastroPage() {
    const [nome, setNome] = useState('');
    const [email, setEmail] = useState('');
    const [senha, setSenha] = useState('');
    const [disciplina, setDisciplina] = useState(''); // Armazena a Área selecionada
    const [areasMapping, setAreasMapping] = useState<Record<string, string[]>>({});
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const router = useRouter();

    useEffect(() => {
        async function fetchFilters() {
            try {
                const data = await apiRequest('/disciplinas');
                setAreasMapping(data.areas);
            } catch (err) {
                console.error('Erro ao carregar filtros', err);
            }
        }
        fetchFilters();
    }, []);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setLoading(true);
        setError('');

        if (!disciplina) {
            setError('Selecione uma disciplina');
            setLoading(false);
            return;
        }

        try {
            const data = await apiRequest('/cadastro', {
                method: 'POST',
                body: JSON.stringify({ nome, email, senha, disciplina }),
            });

            setAuth(data.access_token, data.usuario);
            router.push('/classificar');
        } catch (err: any) {
            setError(err.message || 'Erro ao realizar cadastro.');
        } finally {
            setLoading(false);
        }
    };

    const areaOptions = Object.keys(areasMapping).map(a => ({ value: a, label: a }));

    return (
        <div className={styles.container}>
            <div className={`${styles.card} glass fade-in`}>
                <div className={styles.header}>
                    <h1>Educação</h1>
                    <p>Cadastro de Professor</p>
                </div>

                <form onSubmit={handleSubmit} className={styles.form}>
                    {error && <div className={styles.error}>{error}</div>}

                    <div className={styles.inputGroup}>
                        <label htmlFor="nome">Nome Completo</label>
                        <input
                            id="nome"
                            type="text"
                            placeholder="Professor Exemplo"
                            value={nome}
                            onChange={(e) => setNome(e.target.value)}
                            required
                        />
                    </div>

                    <div className={styles.inputGroup}>
                        <label htmlFor="email">Email</label>
                        <input
                            id="email"
                            type="email"
                            placeholder="seu@email.com"
                            value={email}
                            onChange={(e) => setEmail(e.target.value)}
                            required
                        />
                    </div>

                    <div className={styles.inputGroup}>
                        <Dropdown
                            label="Área de Conhecimento"
                            options={areaOptions}
                            value={disciplina} // Usando o estado 'disciplina' para armazenar a área
                            onChange={(val: any) => setDisciplina(val)}
                            placeholder="Selecione sua área..."
                        />
                    </div>

                    <div className={styles.inputGroup}>
                        <label htmlFor="senha">Senha</label>
                        <input
                            id="senha"
                            type="password"
                            placeholder="••••••••"
                            value={senha}
                            onChange={(e) => setSenha(e.target.value)}
                            required
                            minLength={6}
                        />
                    </div>

                    <button type="submit" disabled={loading} className={styles.button}>
                        {loading ? 'Cadastrando...' : 'Criar Conta'}
                    </button>
                </form>

                <div className={styles.footer}>
                    Já tem conta? <Link href="/login">Entre aqui</Link>
                </div>
            </div>
        </div>
    );
}
