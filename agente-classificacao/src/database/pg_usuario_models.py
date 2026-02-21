"""Modelo SQLAlchemy para autenticação e classificação manual de usuários no PostgreSQL"""

from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, Float, func
from sqlalchemy.dialects.postgresql import JSONB
from ..database import PgBase


class UsuarioModel(PgBase):
    """Tabela usuarios (PostgreSQL local)

    Armazena usuários do sistema de classificação manual.
    Cada usuário é professor de uma disciplina específica.
    """

    __tablename__ = "usuarios"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String(200), nullable=False)
    email = Column(String(255), nullable=False, unique=True, index=True)
    senha_hash = Column(String(255), nullable=False)
    disciplina = Column(String(100), nullable=False)  # Disciplina que leciona
    is_admin = Column(Boolean, nullable=False, default=False)
    ativo = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ClassificacaoUsuarioModel(PgBase):
    """Tabela classificacao_usuario (PostgreSQL local)

    Armazena todas as decisões de classificação feitas por usuários.
    Cada registro = uma decisão (classificação nova, confirmação ou correção).
    Dados usados para treino futuro de modelo ML.
    """

    __tablename__ = "classificacao_usuario"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    usuario_id = Column(Integer, nullable=False, index=True)  # FK → usuarios.id
    questao_id = Column(Integer, nullable=False, index=True)  # FK → questoes.id (MySQL)
    habilidade_id = Column(Integer, nullable=True)  # habilidade TriEduc da questão

    # Classificação escolhida pelo usuário
    modulo_escolhido = Column(String(255), nullable=True)
    classificacao_trieduc = Column(String(255), nullable=True)
    descricao_assunto = Column(String(500), nullable=True)  # Descrição detalhada do assunto/módulo
    habilidade_modulo_id = Column(Integer, nullable=True)  # FK → habilidade_modulos.id

    # Classificação da extração automática (para comparação no ML)
    classificacao_extracao = Column(JSONB, nullable=True)

    # Metadados para ML
    tipo_acao = Column(
        String(50), nullable=False
    )  # "classificacao_nova", "confirmacao", "correcao"
    observacao = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
