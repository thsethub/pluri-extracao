"""Modelo de Questão"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from uuid import uuid4, UUID


class Question(BaseModel):
    """Modelo de uma questão a ser classificada"""

    id: UUID = Field(default_factory=uuid4)
    content: str = Field(..., min_length=1, description="Conteúdo da questão")
    metadata: dict = Field(default_factory=dict, description="Metadados adicionais")
    status: str = Field(default="pending", description="Status do processamento")
    created_at: datetime = Field(default_factory=datetime.now)

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat(), UUID: lambda v: str(v)}
