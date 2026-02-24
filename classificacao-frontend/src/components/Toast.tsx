'use client';

import { useEffect, useState, useCallback } from 'react';
import styles from './Toast.module.css';

export interface ToastMessage {
    id: string;
    text: string;
    type: 'error' | 'success' | 'warning';
}

// Helper para disparar Toasts de qualquer lugar (inclusive fora do React)
export function showToast(text: string, type: 'error' | 'success' | 'warning' = 'error') {
    if (typeof window !== 'undefined') {
        const event = new CustomEvent('app-toast', {
            detail: { text, type }
        });
        window.dispatchEvent(event);
    }
}

export default function ToastContainer() {
    const [toasts, setToasts] = useState<ToastMessage[]>([]);

    const addToast = useCallback(({ text, type }: { text: string; type: ToastMessage['type'] }) => {
        const id = Math.random().toString(36).substring(2, 9);
        setToasts(prev => [...prev, { id, text, type }]);

        setTimeout(() => {
            setToasts(prev => prev.filter(t => t.id !== id));
        }, 5000);
    }, []);

    useEffect(() => {
        const handler = (e: Event) => {
            const detail = (e as CustomEvent).detail;
            if (detail) {
                addToast(detail);
            }
        };

        window.addEventListener('app-toast', handler);
        return () => window.removeEventListener('app-toast', handler);
    }, [addToast]);

    if (toasts.length === 0) return null;

    return (
        <div className={styles.container}>
            {toasts.map(toast => (
                <div key={toast.id} className={`${styles.toast} ${styles[toast.type]}`}>
                    <span>{toast.text}</span>
                    <button
                        onClick={() => setToasts(prev => prev.filter(t => t.id !== toast.id))}
                        style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', marginLeft: '10px' }}
                    >
                        ✕
                    </button>
                </div>
            ))}
        </div>
    );
}
