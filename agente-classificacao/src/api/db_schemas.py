"""Schemas Pydantic para os endpoints do banco de dados"""

from pydantic import BaseModel, Field
from typing import List, Optional


# === Anos ===
class AnoSchema(BaseModel):
    id: int
    descricao: Optional[str] = None

    model_config = {"from_attributes": True}


class AnosListResponse(BaseModel):
    data: List[AnoSchema]
    total: int


# === Disciplinas ===
class DisciplinaDBSchema(BaseModel):
    id: int
    descricao: Optional[str] = None

    model_config = {"from_attributes": True}


class DisciplinasListResponse(BaseModel):
    data: List[DisciplinaDBSchema]
    total: int


# === Habilidades ===
class HabilidadeDBSchema(BaseModel):
    id: int
    hab_id: Optional[str] = None
    sigla: Optional[str] = None
    descricao: Optional[str] = None
    ano: Optional[str] = None

    model_config = {"from_attributes": True}


class HabilidadesListResponse(BaseModel):
    data: List[HabilidadeDBSchema]
    total: int


# === Alternativas ===
class AlternativaSchema(BaseModel):
    id: int
    qa_id: str
    ordem: Optional[int] = None
    conteudo: Optional[str] = None
    correta: Optional[int] = None
    questao_id: Optional[int] = None

    model_config = {"from_attributes": True}


# === Questões ===
class QuestaoResumoSchema(BaseModel):
    """Schema resumido de questão (para listagem)"""

    id: int
    questao_id: str
    enunciado: Optional[str] = None
    ano_id: Optional[int] = None
    disciplina_id: Optional[int] = None
    habilidade_id: Optional[int] = None
    origem: Optional[str] = None
    tipo: Optional[str] = None

    model_config = {"from_attributes": True}


class QuestaoDetalhadaSchema(BaseModel):
    """Schema completo de questão com relacionamentos"""

    id: int
    questao_id: str
    enunciado: Optional[str] = None
    texto_base: Optional[str] = None
    resolucao: Optional[str] = None
    origem: Optional[str] = None
    tipo: Optional[str] = None
    ano: Optional[AnoSchema] = None
    disciplina: Optional[DisciplinaDBSchema] = None
    habilidade: Optional[HabilidadeDBSchema] = None
    alternativas: List[AlternativaSchema] = []

    model_config = {"from_attributes": True}


class QuestoesListResponse(BaseModel):
    data: List[QuestaoResumoSchema]
    total: int
    page: int
    per_page: int
    pages: int
