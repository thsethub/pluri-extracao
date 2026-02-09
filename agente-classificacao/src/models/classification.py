"""Modelo de Classificação"""

from datetime import datetime
from typing import Dict, List, Optional
from pydantic import BaseModel, Field
from uuid import uuid4, UUID


class Classification(BaseModel):
    """Modelo de resultado de classificação"""

    id: UUID = Field(default_factory=uuid4)
    question_id: UUID = Field(..., description="ID da questão classificada")
    categories: List[str] = Field(..., description="Categorias identificadas")
    confidence_scores: Dict[str, float] = Field(
        default_factory=dict, description="Scores de confiança por categoria"
    )
    habilidades: List[Dict[str, str]] = Field(
        default_factory=list, description="Habilidades identificadas por disciplina"
    )
    reasoning: Optional[str] = Field(None, description="Raciocínio da classificação")
    model_used: str = Field(..., description="Modelo OpenAI utilizado")
    tokens_used: int = Field(0, description="Total de tokens consumidos")
    processing_time_ms: int = Field(0, description="Tempo de processamento em ms")
    timestamp: datetime = Field(default_factory=datetime.now)

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat(), UUID: lambda v: str(v)}
