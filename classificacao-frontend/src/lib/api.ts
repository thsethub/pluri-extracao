import { showToast } from "@/components/Toast";

// export const API_BASE_URL = "/classificacao";
export const API_BASE_URL = "/api-proxy/classificacao";

export async function apiRequest(
  endpoint: string,
  options: RequestInit & { customBaseUrl?: string } = {},
) {
  const token =
    typeof window !== "undefined" ? localStorage.getItem("token") : null;

  const { customBaseUrl, ...fetchOptions } = options;
  const baseUrl = customBaseUrl !== undefined ? customBaseUrl : API_BASE_URL;

  const headers = {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...fetchOptions.headers,
  };

  try {
    const response = await fetch(`${baseUrl}${endpoint}`, {
      ...fetchOptions,
      headers,
    });

    if (response.status === 401) {
      if (typeof window !== "undefined") {
        localStorage.removeItem("token");
        localStorage.removeItem("usuario");
        window.location.href = "/login";
      }
    }

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      const msg = errorData.detail || "Erro na requisição";
      showToast(msg, "error");
      throw new Error(msg);
    }

    return response.json();
  } catch (error) {
    if (error instanceof TypeError && error.message.includes("fetch")) {
      showToast("API indisponível: Erro de conexão com o servidor", "error");
    } else if (
      !(error instanceof Error && error.message === "Erro na requisição")
    ) {
      // Se for outro erro que não foi disparado acima
      console.error("API Error:", error);
    }
    throw error;
  }
}

export async function getContagemFilas(): Promise<{
  alta_similaridade: Record<string, number>;
  confirmacoes: Record<string, number>;
}> {
  return apiRequest("/contagem-filas");
}

export async function getAssuntosSuperpro(disciplinaId?: string) {
  const params = new URLSearchParams();
  if (disciplinaId) params.append("disciplina_id", disciplinaId);
  const qs = params.toString() ? `?${params.toString()}` : "";
  return apiRequest(`/assuntos-superpro${qs}`);
}

export async function getAssuntosSuproConfirmacoes(disciplinaId?: string) {
  const params = new URLSearchParams();
  if (disciplinaId) params.append("disciplina_id", disciplinaId);
  const qs = params.toString() ? `?${params.toString()}` : "";
  return apiRequest(`/assuntos-superpro-confirmacoes${qs}`);
}

export async function getProximaAltaSimilaridade(opts: {
  assuntoSuperpro?: string;
  disciplinaId?: string;
  lastQuestaoId?: number;
}) {
  const params = new URLSearchParams();
  if (opts.assuntoSuperpro) params.append("assunto_superpro", opts.assuntoSuperpro);
  if (opts.disciplinaId) params.append("disciplina_id", opts.disciplinaId);
  if (opts.lastQuestaoId) params.append("last_questao_id", String(opts.lastQuestaoId));
  const qs = params.toString() ? `?${params.toString()}` : "";
  return apiRequest(`/proxima-alta-similaridade${qs}`);
}

export async function getProximaConfirmacao(opts: {
  disciplinaId?: string;
  assuntoSuperpro?: string;
  lastQuestaoId?: number;
}) {
  const params = new URLSearchParams();
  if (opts.disciplinaId) params.append("disciplina_id", opts.disciplinaId);
  if (opts.assuntoSuperpro) params.append("assunto_superpro", opts.assuntoSuperpro);
  if (opts.lastQuestaoId) params.append("last_questao_id", String(opts.lastQuestaoId));
  const qs = params.toString() ? `?${params.toString()}` : "";
  return apiRequest(`/proxima-confirmacao${qs}`);
}
