"""Modelos SQLAlchemy para o sistema de novas questões"""

from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    LargeBinary,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    CheckConstraint,
    UniqueConstraint,
    JSON,
)
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


class QuestaoNovaModel(Base):
    """Modelo para questões extraídas de superpro_db"""

    __tablename__ = "questoes_novas"

    id = Column(Integer, primary_key=True, index=True)

    # Identificador do banco original
    sp_id = Column(Integer, unique=True, nullable=False, index=True)

    # Dados da questão (copiados do superpro_db)
    disciplina_sp = Column(String(100))
    tipo_questao = Column(String(100))
    enunciado = Column(Text, nullable=False)
    gabarito = Column(String(50))
    resolucao = Column(Text)
    classif_sp_breadcrumb = Column(Text)
    fonte_vestibular = Column(String(255))
    ano = Column(String(20))
    contem_imagem = Column(Boolean, default=False)
    disciplinas_libro = Column(JSON)  # Array de disciplinas
    assuntos_libro = Column(JSON)  # Array de assuntos
    assunto_sp = Column(String(255))

    # Rastreamento de classificação
    status = Column(
        String(50),
        default="nao_classificada",
        nullable=False,
        index=True,
        comment="Status: nao_classificada, em_progresso, classificada, rejeitada, duplicada",
    )
    classificado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    data_classificacao = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    alternativas = relationship(
        "AlternativaNovaModel", back_populates="questao", cascade="all, delete-orphan"
    )
    classificacao = relationship(
        "ClassificacaoNovaModel", back_populates="questao", uselist=False
    )
    classificador = relationship("UsuarioModel", foreign_keys=[classificado_por_id])
    historico = relationship(
        "ClassificacaoNovaHistoricoModel",
        back_populates="questao",
        cascade="all, delete-orphan",
    )

    # Índices
    __table_args__ = (
        Index("idx_status", "status"),
        Index("idx_created_at", "created_at"),
        Index("idx_classificado_por", "classificado_por_id"),
        CheckConstraint(
            "status IN ('nao_classificada', 'em_progresso', 'classificada', 'rejeitada', 'duplicada')",
            name="ck_questao_status",
        ),
    )

    def __repr__(self):
        return f"<QuestaoNova(id={self.id}, sp_id={self.sp_id}, status={self.status})>"


class AlternativaNovaModel(Base):
    """Modelo para alternativas de questões novas"""

    __tablename__ = "alternativas_novas"

    id = Column(Integer, primary_key=True, index=True)
    questao_nova_id = Column(
        Integer, ForeignKey("questoes_novas.id", ondelete="CASCADE"), nullable=False
    )

    # Dados da alternativa
    letra = Column(String(1), nullable=False)
    texto = Column(Text, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    questao = relationship("QuestaoNovaModel", back_populates="alternativas")

    # Constraints
    __table_args__ = (
        UniqueConstraint("questao_nova_id", "letra", name="uk_questao_letra"),
        Index("idx_questao_nova", "questao_nova_id"),
    )

    def __repr__(self):
        return f"<AlternativaNova(id={self.id}, letra={self.letra})>"


class ClassificacaoNovaModel(Base):
    """Modelo para classificações de novas questões"""

    __tablename__ = "classificacoes_novas"

    id = Column(Integer, primary_key=True, index=True)
    questao_nova_id = Column(
        Integer,
        ForeignKey("questoes_novas.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # Informações da classificação
    habilidades_identificadas = Column(JSON)  # Array com IDs das habilidades
    disciplinas_classificadas = Column(JSON)  # Array com IDs das disciplinas
    scores_confianca = Column(JSON)  # {disciplina_id: score, ...}
    justificativa = Column(Text)

    # Auditoria
    classificado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    data_criacao = Column(DateTime(timezone=True), server_default=func.now())
    data_atualizacao = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    questao = relationship("QuestaoNovaModel", back_populates="classificacao")
    classificador = relationship("UsuarioModel", foreign_keys=[classificado_por_id])

    # Índices
    __table_args__ = (
        Index("idx_questao", "questao_nova_id"),
        Index("idx_classificado_por", "classificado_por_id"),
        Index("idx_data_criacao", "data_criacao"),
    )

    def __repr__(self):
        return (
            f"<ClassificacaoNova(id={self.id}, questao_nova_id={self.questao_nova_id})>"
        )


class ClassificacaoNovaHistoricoModel(Base):
    """Modelo para auditoria de mudanças em classificações"""

    __tablename__ = "classificacoes_novas_historico"

    id = Column(Integer, primary_key=True, index=True)
    classificacao_nova_id = Column(
        Integer,
        ForeignKey("classificacoes_novas.id", ondelete="CASCADE"),
        nullable=True,
    )
    questao_nova_id = Column(
        Integer, ForeignKey("questoes_novas.id", ondelete="CASCADE"), nullable=False
    )

    # O que mudou
    acao = Column(String(50), nullable=False)  # 'criada', 'atualizada', 'deletada'
    dados_anterior = Column(JSON)
    dados_novo = Column(JSON)

    # Quem e quando
    alterado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    data_alteracao = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    questao = relationship("QuestaoNovaModel", back_populates="historico")
    alterado_por = relationship("UsuarioModel", foreign_keys=[alterado_por_id])

    # Índices
    __table_args__ = (
        Index("idx_questao", "questao_nova_id"),
        Index("idx_data", "data_alteracao"),
        Index("idx_acao", "acao"),
    )

    def __repr__(self):
        return f"<ClassificacaoNovaHistorico(id={self.id}, acao={self.acao})>"
