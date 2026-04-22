"""Schemas Pydantic para o sistema de novas questões"""

from pydantic import BaseModel, Field, validator
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum

# ========================
# Enums
# ========================


class StatusQuestaoEnum(str, Enum):
    NAO_CLASSIFICADA = "nao_classificada"
    EM_PROGRESSO = "em_progresso"
    CLASSIFICADA = "classificada"
    REJEITADA = "rejeitada"
    DUPLICADA = "duplicada"


class AcaoHistoricoEnum(str, Enum):
    CRIADA = "criada"
    ATUALIZADA = "atualizada"
    DELETADA = "deletada"


# ========================
# Alternativas
# ========================


class AlternativaNovaBase(BaseModel):
    """Base para alternativa"""

    letra: str = Field(
        ..., min_length=1, max_length=1, description="Letra da alternativa (A-Z)"
    )
    texto: str = Field(..., min_length=1, description="Texto da alternativa")

    @validator("letra")
    def validar_letra(cls, v):
        if not v.isalpha() or not v.isupper():
            raise ValueError("Letra deve ser maiúscula (A-Z)")
        return v


class AlternativaNovaSchema(AlternativaNovaBase):
    """Schema completo de alternativa"""

    id: int
    questao_nova_id: int
    created_at: datetime

    class Config:
        from_attributes = True


# ========================
# Questões Novas
# ========================


class QuestaoNovaListaSchema(BaseModel):
    """Schema para lista de questões (resumido)"""

    id: int
    sp_id: int
    disciplina_sp: Optional[str]
    tipo_questao: Optional[str]
    enunciado: str = Field(..., description="Primeiros 300 caracteres do enunciado")
    status: StatusQuestaoEnum
    contem_imagem: bool
    created_at: datetime
    data_classificacao: Optional[datetime] = None
    classificador: Optional[str] = Field(
        None, description="Nome do usuário que classificou"
    )

    class Config:
        from_attributes = True


class QuestaoNovaDetalheSchema(BaseModel):
    """Schema completo de questão com detalhes"""

    id: int
    sp_id: int

    # Dados da questão
    disciplina_sp: Optional[str]
    tipo_questao: Optional[str]
    enunciado: str
    gabarito: Optional[str]
    resolucao: Optional[str]
    classif_sp_breadcrumb: Optional[str]
    fonte_vestibular: Optional[str]
    ano: Optional[str]
    contem_imagem: bool
    disciplinas_libro: Optional[Dict[str, Any]]
    assuntos_libro: Optional[Dict[str, Any]]
    assunto_sp: Optional[str]

    # Status
    status: StatusQuestaoEnum
    created_at: datetime
    updated_at: datetime

    # Alternativas
    alternativas: List[AlternativaNovaSchema] = Field(default_factory=list)

    # Classificação atual
    classificacao: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


# ========================
# Classificações
# ========================


class ClassificacaoNovaRequest(BaseModel):
    """Request para salvar classificação de questão"""

    habilidades_identificadas: List[int] = Field(
        ..., description="IDs das habilidades identificadas"
    )
    disciplinas_classificadas: List[int] = Field(
        ..., min_items=1, description="IDs das disciplinas (mínimo 1)"
    )
    justificativa: str = Field(
        ...,
        min_length=10,
        max_length=1000,
        description="Justificativa da classificação",
    )
    scores_confianca: Dict[int, float] = Field(
        default_factory=dict, description="Scores de confiança por disciplina (0-1)"
    )

    @validator("scores_confianca")
    def validar_scores(cls, v):
        for disciplina_id, score in v.items():
            if not 0 <= score <= 1:
                raise ValueError(f"Score deve estar entre 0 e 1, recebido: {score}")
        return v


class ClassificacaoNovaResponse(BaseModel):
    """Response após salvar classificação"""

    id: int
    questao_nova_id: int
    status: str = "classificada"
    data_criacao: datetime
    mensagem: str = "Classificação salva com sucesso"

    class Config:
        from_attributes = True


class ClassificacaoNovaDetalheSchema(BaseModel):
    """Schema detalhado de classificação"""

    id: int
    questao_nova_id: int
    habilidades_identificadas: List[int]
    disciplinas_classificadas: List[int]
    scores_confianca: Dict[int, float]
    justificativa: str
    classificado_por_id: int
    data_criacao: datetime
    data_atualizacao: datetime

    class Config:
        from_attributes = True


# ========================
# Paginação
# ========================


class QuestoesNovasListaResponse(BaseModel):
    """Response com lista paginada de questões"""

    total: int = Field(..., description="Total de questões")
    pagina: int = Field(..., description="Página atual (1-based)")
    tamanho: int = Field(..., description="Itens por página")
    total_paginas: int = Field(..., description="Total de páginas")
    itens: List[QuestaoNovaListaSchema]


class PaginacaoParams(BaseModel):
    """Parâmetros de paginação"""

    pagina: int = Field(default=1, ge=1, description="Número da página")
    tamanho: int = Field(default=20, ge=1, le=100, description="Itens por página")


# ========================
# Filtros
# ========================


class FiltrosQuestoes(BaseModel):
    """Filtros disponíveis para listar questões"""

    status: Optional[StatusQuestaoEnum] = None
    disciplina_sp: Optional[str] = None
    contem_imagem: Optional[bool] = None
    data_inicio: Optional[datetime] = None
    data_fim: Optional[datetime] = None
    classificador_id: Optional[int] = None


# ========================
# Sincronização
# ========================


class SincronizarResponse(BaseModel):
    """Response após sincronização"""

    sucesso: bool
    questoes_sincronizadas: int
    questoes_adicionadas: int
    questoes_atualizadas: int
    questoes_com_erro: int
    timestamp: datetime
    mensagem: str


class SincronizarRequest(BaseModel):
    """Request para sincronização"""

    apenas_nao_classificadas: bool = Field(
        default=True, description="Sincronizar apenas questões não classificadas"
    )
    limite: Optional[int] = Field(
        default=None,
        description="Limite de questões para sincronizar (None = sem limite)",
    )


# ========================
# Estatísticas
# ========================


class EstatisticasResponse(BaseModel):
    """Estatísticas gerais de classificação"""

    total_questoes: int
    nao_classificadas: int
    em_progresso: int
    classificadas: int
    rejeitadas: int
    duplicadas: int
    percentual_concluido: float = Field(..., ge=0, le=100)
    tempo_medio_classificacao_minutos: Optional[float]
    usuarios_ativos: int


class StatsPorClassificador(BaseModel):
    """Estatísticas por classificador"""

    classificador_id: int
    classificador_nome: str
    total_classificadas: int
    primeira_classificacao: Optional[datetime]
    ultima_classificacao: Optional[datetime]


# ========================
# Histórico
# ========================


class ClassificacaoHistoricoSchema(BaseModel):
    """Schema para histórico de mudanças"""

    id: int
    questao_nova_id: int
    acao: AcaoHistoricoEnum
    dados_anterior: Optional[Dict[str, Any]]
    dados_novo: Optional[Dict[str, Any]]
    alterado_por_id: Optional[int]
    alterado_por_nome: Optional[str]
    data_alteracao: datetime


class HistoricoQuestaoResponse(BaseModel):
    """Response com histórico de questão"""

    questao_id: int
    historico: List[ClassificacaoHistoricoSchema]


# ========================
# Respostas de Erro
# ========================


class ErroResponse(BaseModel):
    """Schema para respostas de erro"""

    sucesso: bool = False
    erro: str
    detalhe: Optional[str] = None
    timestamp: datetime


class ValidationErrorDetail(BaseModel):
    """Detalhe de erro de validação"""

    campo: str
    mensagem: str
    valor_recebido: Optional[Any] = None


class ValidationErrorResponse(ErroResponse):
    """Response com erros de validação"""

    erros: List[ValidationErrorDetail]
