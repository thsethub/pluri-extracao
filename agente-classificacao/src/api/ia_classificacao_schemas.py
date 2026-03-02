from pydantic import BaseModel, Field
from typing import List, Optional, Dict

class IAClassificarRequest(BaseModel):
    questao_id: int
    texto: Optional[str] = Field(None, description="Texto opcional para classificar. Se não provido, busca no banco pelo questao_id")
    threshold: float = Field(0.6, description="Limiar de confiança (legado, não usado no novo pipeline)")
    force_fallback_on_empty: bool = Field(
        False,
        description="Se true, aplica fallback automatico quando o LLM nao retornar modulo valido.",
    )

class IAClassificarResponse(BaseModel):
    questao_id: int
    modulos_sugeridos: List[str]
    justificativas: Optional[Dict[str, str]] = None
    disciplina: Optional[str] = None
    modulos_possiveis: Optional[List[str]] = None
    categorias_preditas: List[str] = []
    confianca_media: float
    modelo_utilizado: str
    usou_llm: bool
    tempo_processamento: float
    tokens_usados: int = 0
    custo_estimado_usd: float = 0.0

class IARetreinarResponse(BaseModel):
    message: str
    status: str


