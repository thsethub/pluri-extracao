"""
Cliente para a API local de extração (FastAPI rodando em localhost:8000).
"""

import httpx
from loguru import logger
from .config import settings


class LocalApiClient:
    """Cliente assíncrono para a API local de extração."""

    def __init__(self):
        self.base_url = settings.API_BASE_URL
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def start(self):
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=30.0,
        )
        logger.debug(f"API client local iniciado ({self.base_url})")

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def proxima_questao(self, disciplina_id: int, ano_id: int = 3) -> dict | None:
        """Obtém a próxima questão pendente de extração."""
        try:
            resp = await self._client.get(
                "/extracao/proxima",
                params={"disciplina_id": disciplina_id, "ano_id": ano_id},
            )
            if resp.status_code == 404:
                return None  # Nenhuma questão pendente
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Erro ao buscar próxima questão: {e.response.status_code}")
            return None

    async def salvar_extracao(
        self,
        questao_id: int,
        classificacoes: list[str],
        superpro_id: int | None = None,
        enunciado_tratado: str | None = None,
        similaridade: float | None = None,
        enunciado_superpro: str | None = None,
        classificacao_nao_enquadrada: list[str] = [],
    ) -> bool:
        """Salva o resultado da extração."""
        try:
            payload = {
                "questao_id": questao_id,
                "classificacoes": classificacoes,
                "classificacao_nao_enquadrada": classificacao_nao_enquadrada,
            }
            if superpro_id:
                payload["superpro_id"] = superpro_id
            if enunciado_tratado:
                payload["enunciado_tratado"] = enunciado_tratado
            if similaridade is not None:
                payload["similaridade"] = similaridade
            if enunciado_superpro:
                payload["enunciado_superpro"] = enunciado_superpro
            resp = await self._client.post(
                "/extracao/salvar",
                json=payload,
            )
            resp.raise_for_status()
            result = resp.json()
            return result.get("success", False)
        except Exception as e:
            logger.error(f"Erro ao salvar extração: {e}")
            return False

    async def stats(self) -> dict | None:
        """Obtém estatísticas de extração."""
        try:
            resp = await self._client.get("/extracao/stats")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Erro ao obter stats: {e}")
            return None

    async def disciplinas(self) -> list[dict]:
        """Lista disciplinas disponíveis."""
        try:
            resp = await self._client.get("/db/disciplinas")
            resp.raise_for_status()
            return resp.json().get("data", [])
        except Exception as e:
            logger.error(f"Erro ao listar disciplinas: {e}")
            return []

    async def limpar_enunciado(self, enunciado: str) -> str | None:
        """Usa IA para extrair o enunciado real de questões com imagem/lixo."""
        try:
            resp = await self._client.post(
                "/extracao/limpar-enunciado",
                json={"enunciado": enunciado},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("sucesso"):
                return data.get("enunciado_limpo")
            return None
        except Exception as e:
            logger.error(f"Erro ao limpar enunciado com IA: {e}")
            return None
