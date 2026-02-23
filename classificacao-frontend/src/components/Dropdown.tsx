'use client';

import { useState, useRef, useEffect } from 'react';
import { ChevronDown, Search } from 'lucide-react';
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
    disabled?: boolean;
    searchable?: boolean;
}

export default function Dropdown({
    label,
    options,
    value,
    onChange,
    placeholder = 'Selecione...',
    disabled = false,
    searchable = false
}: DropdownProps) {
    const [isOpen, setIsOpen] = useState(false);
    const [searchTerm, setSearchTerm] = useState('');
    const dropdownRef = useRef<HTMLDivElement>(null);
    const searchInputRef = useRef<HTMLInputElement>(null);

    const selectedOption = options.find(opt => opt.value === value);

    const filteredOptions = searchable && searchTerm
        ? options.filter(opt => opt.label.toLowerCase().includes(searchTerm.toLowerCase()))
        : options;

    useEffect(() => {
        function handleClickOutside(event: MouseEvent) {
            if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
                setIsOpen(false);
                setSearchTerm('');
            }
        }
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    useEffect(() => {
        if (isOpen && searchable && searchInputRef.current) {
            searchInputRef.current.focus();
        }
        if (!isOpen) {
            setSearchTerm('');
        }
    }, [isOpen, searchable]);

    return (
        <div className={styles.container} ref={dropdownRef}>
            {label && <label className={styles.label}>{label}</label>}
            <div
                className={`${styles.select} ${isOpen ? styles.active : ''} ${disabled ? styles.disabled : ''}`}
                onClick={() => !disabled && setIsOpen(!isOpen)}
            >
                <span className={selectedOption ? styles.value : styles.placeholder}>
                    {selectedOption ? selectedOption.label : placeholder}
                </span>
                <span className={styles.arrow}><ChevronDown size={18} /></span>
            </div>

            {isOpen && (
                <div className={styles.menu}>
                    {searchable && (
                        <div className={styles.searchContainer}>
                            <Search size={14} className={styles.searchIcon} />
                            <input
                                ref={searchInputRef}
                                type="text"
                                className={styles.searchInput}
                                placeholder="Buscar..."
                                value={searchTerm}
                                onChange={(e) => setSearchTerm(e.target.value)}
                                onClick={(e) => e.stopPropagation()}
                            />
                        </div>
                    )}
                    <div className={searchable ? styles.optionsList : undefined}>
                        {filteredOptions.map((option) => (
                            <div
                                key={option.value}
                                className={`${styles.option} ${value === option.value ? styles.selected : ''}`}
                                onClick={() => {
                                    onChange(option.value);
                                    setIsOpen(false);
                                    setSearchTerm('');
                                }}
                            >
                                {option.label}
                            </div>
                        ))}
                        {filteredOptions.length === 0 && (
                            <div className={styles.noOptions}>
                                {searchTerm ? 'Nenhum resultado encontrado' : 'Nenhuma opção disponível'}
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
