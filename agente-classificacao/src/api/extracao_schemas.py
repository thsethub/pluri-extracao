"""Schemas Pydantic para os endpoints de extração de assuntos"""

from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class QuestaoAssuntoSchema(BaseModel):
    """Schema de um registro de assunto extraído"""

    id: int
    questao_id: int
    questao_id_str: str
    superpro_id: Optional[int] = None
    disciplina_id: Optional[int] = None
    disciplina_nome: Optional[str] = None
    classificacoes: List[str] = []
    enunciado_original: Optional[str] = None
    enunciado_tratado: Optional[str] = None
    similaridade: Optional[float] = None
    extracao_feita: bool = False
    contem_imagem: bool = False
    precisa_verificar: bool = False
    enunciado_superpro: Optional[str] = None
    motivo_erro: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class QuestaoAssuntoListResponse(BaseModel):
    data: List[QuestaoAssuntoSchema]
    total: int
    page: int
    per_page: int
    pages: int


class ProximaQuestaoResponse(BaseModel):
    """Response da próxima questão a ser extraída"""

    id: int = Field(..., description="ID da questão no banco")
    questao_id: str = Field(..., description="questao_id (string UUID)")
    enunciado_original: Optional[str] = Field(None, description="Enunciado bruto")
    enunciado_tratado: str = Field(..., description="Enunciado limpo (sem HTML)")
    disciplina_id: Optional[int] = None
    disciplina_nome: Optional[str] = None
    habilidade_id: Optional[int] = None
    ano_id: Optional[int] = None
    ano_nome: Optional[str] = None
    contem_imagem: bool = Field(False, description="Se contém imagem e foi pulada")
    motivo_erro: Optional[str] = Field(
        None, description="Motivo caso tenha sido pulada"
    )

    model_config = {"from_attributes": True}


class SalvarAssuntoRequest(BaseModel):
    """Request para salvar o resultado da extração de assunto"""

    questao_id: int = Field(..., description="ID da questão no banco MySQL")
    superpro_id: Optional[int] = Field(
        None, description="ID da questão no SuperProfessor"
    )
    classificacoes: List[str] = Field(
        ...,
        description="Array de classificações extraídas. Ex: ['História > Brasil > Escravidão']",
    )
    similaridade: Optional[float] = Field(
        None, description="Taxa de similaridade do match (0.0 a 1.0)"
    )
    enunciado_tratado: Optional[str] = Field(
        None,
        description="Enunciado limpo pela IA (sobrescreve o enunciado_tratado do banco)",
    )
    enunciado_superpro: Optional[str] = Field(
        None,
        description="Enunciado completo da questão encontrada no SuperProfessor",
    )


class SalvarAssuntoResponse(BaseModel):
    """Response após salvar a extração"""

    success: bool
    questao_id: int
    classificacoes: List[str]
    message: str


class ExtracaoStatsResponse(BaseModel):
    """Estatísticas de extração por disciplina"""

    disciplina_id: Optional[int] = None
    disciplina_nome: Optional[str] = None
    total_questoes: int = 0
    extraidas: int = 0
    com_imagem: int = 0
    com_erro: int = 0
    pendentes: int = 0


class LimparEnunciadoRequest(BaseModel):
    """Request para limpar enunciado com imagem usando IA"""

    enunciado: str = Field(
        ...,
        description="Enunciado bruto (já tratado de HTML, mas com lixo de referências de imagem)",
    )


class LimparEnunciadoResponse(BaseModel):
    """Response com o enunciado limpo pela IA"""

    enunciado_limpo: str = Field(..., description="Apenas o enunciado da questão")
    sucesso: bool = True
    mensagem: Optional[str] = None
    percentual_concluido: float = 0.0


class TratarEnunciadoRequest(BaseModel):
    """Request para tratar enunciado - remoção de HTML, Unicode e caracteres especiais"""

    enunciado: str = Field(
        ...,
        description="Enunciado bruto com HTML, Unicode e caracteres especiais",
    )


class TratarEnunciadoResponse(BaseModel):
    """Response com o enunciado tratado programaticamente (sem IA)"""

    enunciado_original: str = Field(..., description="Enunciado original recebido")
    enunciado_tratado: str = Field(
        ..., description="Enunciado limpo (sem HTML, sem caracteres especiais)"
    )
    contem_imagem: bool = Field(
        False, description="Se o enunciado original contém imagens"
    )
    caracteres_removidos: int = Field(
        0, description="Quantidade de caracteres removidos na limpeza"
    )
    sucesso: bool = True
    motivo_erro: Optional[str] = None

