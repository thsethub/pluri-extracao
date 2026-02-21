"""Schemas Pydantic para o sistema de classificação manual por usuários"""

from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional
from datetime import datetime


# ========================
# AUTENTICAÇÃO
# ========================

class CadastroRequest(BaseModel):
    """Request para cadastro de novo usuário"""
    nome: str = Field(..., min_length=3, description="Nome completo")
    email: EmailStr = Field(..., description="Email do usuário")
    senha: str = Field(..., min_length=6, description="Senha (min 6 caracteres)")
    disciplina: str = Field(..., description="Disciplina que leciona")


class LoginRequest(BaseModel):
    """Request para login"""
    email: EmailStr = Field(..., description="Email do usuário")
    senha: str = Field(..., description="Senha")


class TokenResponse(BaseModel):
    """Response com token JWT"""
    access_token: str
    token_type: str = "bearer"
    usuario: "UsuarioSchema"


class UsuarioSchema(BaseModel):
    """Schema do usuário (sem senha)"""
    id: int
    nome: str
    email: str
    disciplina: str
    is_admin: bool = False
    ativo: bool = True
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ========================
# MÓDULOS (consulta)
# ========================

class HabilidadeModuloSchema(BaseModel):
    """Schema de um mapeamento habilidade → módulo"""
    id: int
    habilidade_id: Optional[int] = None
    habilidade_descricao: str
    area: str
    disciplina: str
    modulo: str
    descricao: str
    ordenacao: Optional[int] = None

    model_config = {"from_attributes": True}


class ModulosResponse(BaseModel):
    """Response com módulos possíveis para uma habilidade"""
    habilidade_id: int
    modulos: List[HabilidadeModuloSchema]
    total: int


# ========================
# QUESTÃO PARA CLASSIFICAR
# ========================

class AlternativaClassifSchema(BaseModel):
    """Alternativa de questão"""
    ordem: int = 0
    conteudo: str = ""
    conteudo_html: Optional[str] = None
    correta: bool = False


class QuestaoClassifResponse(BaseModel):
    """Questão para classificação/verificação pelo usuário"""
    id: int = Field(..., description="ID da questão no MySQL")
    questao_id: str = Field(..., description="questao_id (string UUID)")
    enunciado: str = Field(..., description="Enunciado tratado")
    enunciado_html: Optional[str] = Field(None, description="Enunciado original (HTML)")
    disciplina_id: Optional[int] = None
    disciplina_nome: Optional[str] = None
    habilidade_id: Optional[int] = None
    habilidade_descricao: Optional[str] = None
    tipo: Optional[str] = None
    alternativas: List[AlternativaClassifSchema] = []

    # Classificação existente (se já foi extraída)
    classificacao_extracao: Optional[List[str]] = None
    classificacao_nao_enquadrada: Optional[List[str]] = None
    similaridade: Optional[float] = None
    tem_extracao: bool = False

    # Módulos possíveis (via habilidade_modulos)
    modulos_possiveis: List[HabilidadeModuloSchema] = []


# ========================
# SALVAR CLASSIFICAÇÃO
# ========================

class SalvarClassificacaoRequest(BaseModel):
    """Request para salvar decisão do usuário"""
    questao_id: int = Field(..., description="ID da questão no MySQL")
    habilidade_modulo_id: Optional[int] = Field(
        None, description="ID do registro em habilidade_modulos escolhido"
    )
    modulo_escolhido: Optional[str] = Field(None, description="Nome do módulo escolhido")
    classificacao_trieduc: Optional[str] = Field(
        None, description="Texto da classificação TRIEDUC escolhida"
    )
    descricao_assunto: Optional[str] = Field(None, description="Descrição detalhada do assunto")
    tipo_acao: str = Field(
        ..., description="Tipo: 'classificacao_nova', 'confirmacao', 'correcao'"
    )
    observacao: Optional[str] = Field(None, description="Observação opcional")


class SalvarClassificacaoResponse(BaseModel):
    """Response após salvar classificação"""
    success: bool
    id: int
    questao_id: int
    tipo_acao: str
    message: str


# ========================
# ESTATÍSTICAS
# ========================

class ClassificacaoStatsResponse(BaseModel):
    """Estatísticas de classificação manual"""
    total_classificacoes: int = 0
    classificacoes_novas: int = 0
    confirmacoes: int = 0
    correcoes: int = 0
    usuarios_ativos: int = 0
    total_manuais: int = 0
    total_pendentes: int = 0
    por_disciplina: dict = {}
    por_usuario: dict = {}


# ========================
# HISTÓRICO (para ML)
# ========================

class ClassificacaoHistoricoSchema(BaseModel):
    """Schema de um registro de classificação para exportação ML"""
    id: int
    usuario_id: int
    usuario_nome: Optional[str] = None
    questao_id: int
    habilidade_id: Optional[int] = None
    modulo_escolhido: Optional[str] = None
    classificacao_trieduc: Optional[str] = None
    descricao_assunto: Optional[str] = None
    classificacao_extracao: Optional[List[str]] = None
    tipo_acao: str
    observacao: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class HistoricoListResponse(BaseModel):
    """Response paginada do histórico"""
    data: List[ClassificacaoHistoricoSchema]
    total: int
    page: int
    per_page: int
    pages: int


# Resolve forward reference
TokenResponse.model_rebuild()
