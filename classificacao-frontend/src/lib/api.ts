import { showToast } from "@/components/Toast";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/classificacao";

export async function apiRequest(endpoint: string, options: RequestInit = {}) {
    const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null;

    const headers = {
        'Content-Type': 'application/json',
        ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
        ...options.headers,
    };

    try {
        const response = await fetch(`${API_BASE_URL}${endpoint}`, {
            ...options,
            headers,
        });

        if (response.status === 401) {
            if (typeof window !== 'undefined') {
                localStorage.removeItem('token');
                localStorage.removeItem('usuario');
                window.location.href = '/login';
            }
        }

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            const msg = errorData.detail || 'Erro na requisição';
            showToast(msg, 'error');
            throw new Error(msg);
        }

        return response.json();
    } catch (error) {
        if (error instanceof TypeError && error.message.includes('fetch')) {
            showToast('API indisponível: Erro de conexão com o servidor', 'error');
        } else if (!(error instanceof Error && error.message === 'Erro na requisição')) {
            // Se for outro erro que não foi disparado acima
            console.error('API Error:', error);
        }
        throw error;
    }
}
