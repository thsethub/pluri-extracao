'use client';

import { useState, useEffect } from 'react';
import { apiRequest } from '@/lib/api';
import { getUsuario } from '@/lib/auth';
import { Filter, Layers } from 'lucide-react';
import Dropdown from './Dropdown';
import styles from './FilterBar.module.css';

interface FilterBarProps {
    onFilterChange: (area: string, disciplinaId: string) => void;
}

export default function FilterBar({ onFilterChange }: FilterBarProps) {
    const [area, setArea] = useState('');
    const [disciplinaId, setDisciplinaId] = useState('');
    const [areasMapping, setAreasMapping] = useState<Record<string, string[]>>({});
    const usuario = getUsuario();

    useEffect(() => {
        async function loadFilters() {
            try {
                const data = await apiRequest('/disciplinas');
                setAreasMapping(data.areas);

                // Se o usuário tem uma disciplina (que agora é área), descobrimos a área dele
                if (usuario?.disciplina) {
                    // No novo sistema, usuario.disciplina já é o nome da área
                    if (data.areas[usuario.disciplina]) {
                        setArea(usuario.disciplina);
                    } else {
                        // Caso legado ou erro, tenta encontrar em qual área a disciplina dele está
                        for (const [areaName, discs] of Object.entries(data.areas) as any) {
                            if (discs.includes(usuario.disciplina)) {
                                setArea(areaName);
                                break;
                            }
                        }
                    }
                }
            } catch (err) {
                console.error(err);
            }
        }
        loadFilters();
    }, []);

    // Notifica o pai quando os filtros mudam
    useEffect(() => {
        // Só dispara se houver pelo menos área ou se for flexível
        onFilterChange(area, disciplinaId);
    }, [area, disciplinaId]);

    // Disciplinas dentro da área atual
    const disciplinasDaArea = area ? (areasMapping[area] || []) : [];

    // Se o usuário tem uma área definida, ele só pode ver essa área (exceto se for admin)
    const areaOptions = Object.keys(areasMapping)
        .filter(a => !usuario?.disciplina || usuario.is_admin || a === area)
        .map(a => ({ value: a, label: a }));

    const disciplinaOptions = disciplinasDaArea.map(d => ({ value: d, label: d }));

    return (
        <div className={`${styles.filterBar} glass fade-in`}>
            <div className={styles.filterGroup}>
                <Dropdown
                    label="Sua Área de Atuação"
                    options={areaOptions}
                    value={area}
                    onChange={(val: any) => {
                        if (!usuario?.disciplina || usuario.is_admin) {
                            setArea(val);
                            setDisciplinaId('');
                        }
                    }}
                    placeholder="Selecione a Área"
                />
            </div>

            <div className={styles.filterGroup}>
                <Dropdown
                    label="Filtrar por Disciplina"
                    options={disciplinaOptions}
                    value={disciplinaId}
                    onChange={(val: any) => setDisciplinaId(val)}
                    placeholder="Todas as disciplinas da área"
                />
            </div>

            <div className={styles.info}>
                <p><strong>{area || 'Área não definida'}</strong></p>
                <span>{disciplinaId || 'Todas as disciplinas'}</span>
            </div>
        </div>
    );
}
