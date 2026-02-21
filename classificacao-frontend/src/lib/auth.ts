export interface Usuario {
    id: number;
    nome: string;
    email: string;
    disciplina: string;
    is_admin: boolean;
    ativo: boolean;
    created_at?: string;
}

export function getToken(): string | null {
    if (typeof window === 'undefined') return null;
    return localStorage.getItem('token');
}

export function setAuth(token: string, usuario: any) {
    if (typeof window === 'undefined') return;
    localStorage.setItem('token', token);
    localStorage.setItem('usuario', JSON.stringify(usuario));
}

export function clearAuth() {
    if (typeof window === 'undefined') return;
    localStorage.removeItem('token');
    localStorage.removeItem('usuario');
}

export function getUsuario(): Usuario | null {
    if (typeof window === 'undefined') return null;
    const userStr = localStorage.getItem('usuario');
    if (!userStr) return null;
    try {
        return JSON.parse(userStr);
    } catch {
        return null;
    }
}

export function isAuthenticated(): boolean {
    return !!getToken();
}
