import { showToast } from "@/components/Toast";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/classificacao";

export async function apiRequest(endpoint: string, options: RequestInit & { customBaseUrl?: string } = {}) {
    const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null;

    const { customBaseUrl, ...fetchOptions } = options;
    const baseUrl = customBaseUrl !== undefined ? customBaseUrl : API_BASE_URL;

    const headers = {
        'Content-Type': 'application/json',
        ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
        ...fetchOptions.headers,
    };

    try {
        const response = await fetch(`${baseUrl}${endpoint}`, {
            ...fetchOptions,
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
