'use client';

import { useState, useRef, useEffect } from 'react';
import { ChevronDown } from 'lucide-react';
import styles from './Dropdown.module.css';

interface Option {
    value: string | number;
    label: string;
}

interface DropdownProps {
    label?: string;
    options: Option[];
    value: string | number;
    onChange: (value: any) => void;
    placeholder?: string;
}

export default function Dropdown({ label, options, value, onChange, placeholder = 'Selecione...' }: DropdownProps) {
    const [isOpen, setIsOpen] = useState(false);
    const dropdownRef = useRef<HTMLDivElement>(null);

    const selectedOption = options.find(opt => opt.value === value);

    useEffect(() => {
        function handleClickOutside(event: MouseEvent) {
            if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
                setIsOpen(false);
            }
        }
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    return (
        <div className={styles.container} ref={dropdownRef}>
            {label && <label className={styles.label}>{label}</label>}
            <div
                className={`${styles.select} ${isOpen ? styles.active : ''}`}
                onClick={() => setIsOpen(!isOpen)}
            >
                <span className={selectedOption ? styles.value : styles.placeholder}>
                    {selectedOption ? selectedOption.label : placeholder}
                </span>
                <span className={styles.arrow}><ChevronDown size={18} /></span>
            </div>

            {isOpen && (
                <div className={styles.menu}>
                    {options.map((option) => (
                        <div
                            key={option.value}
                            className={`${styles.option} ${value === option.value ? styles.selected : ''}`}
                            onClick={() => {
                                onChange(option.value);
                                setIsOpen(false);
                            }}
                        >
                            {option.label}
                        </div>
                    ))}
                    {options.length === 0 && <div className={styles.noOptions}>Nenhuma opção disponível</div>}
                </div>
            )}
        </div>
    );
}
