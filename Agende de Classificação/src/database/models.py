"""Modelos SQLAlchemy mapeando as tabelas do banco trieduc"""

from sqlalchemy import Column, Integer, String, Text, ForeignKey
from sqlalchemy.orm import relationship
from ..database import Base


class AnoModel(Base):
    """Tabela anos"""

    __tablename__ = "anos"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    descricao = Column(String(100), nullable=True)

    questoes = relationship("QuestaoModel", back_populates="ano")


class DisciplinaModel(Base):
    """Tabela disciplinas"""

    __tablename__ = "disciplinas"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    descricao = Column(String(100), nullable=True)

    questoes = relationship("QuestaoModel", back_populates="disciplina")


class HabilidadeModel(Base):
    """Tabela habilidades"""

    __tablename__ = "habilidades"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, autoincrement=False)
    hab_id = Column(String(50), nullable=True)
    sigla = Column(String(100), nullable=True)
    descricao = Column(String(255), nullable=True)
    ano = Column(String(100), nullable=True)

    questoes = relationship("QuestaoModel", back_populates="habilidade")


class QuestaoModel(Base):
    """Tabela questoes"""

    __tablename__ = "questoes"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    questao_id = Column(String(100), unique=True, nullable=False)
    enunciado = Column(Text, nullable=True)
    texto_base = Column(Text, nullable=True)
    resolucao = Column(Text, nullable=True)
    ano_id = Column(Integer, ForeignKey("anos.id"), nullable=True)
    disciplina_id = Column(Integer, ForeignKey("disciplinas.id"), nullable=True)
    habilidade_id = Column(Integer, ForeignKey("habilidades.id"), nullable=True)
    origem = Column(String(100), nullable=True)
    tipo = Column(String(100), nullable=True)

    ano = relationship("AnoModel", back_populates="questoes")
    disciplina = relationship("DisciplinaModel", back_populates="questoes")
    habilidade = relationship("HabilidadeModel", back_populates="questoes")
    alternativas = relationship("QuestaoAlternativaModel", back_populates="questao")


class QuestaoAlternativaModel(Base):
    """Tabela questao_alternativas"""

    __tablename__ = "questao_alternativas"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    qa_id = Column(String(100), unique=True, nullable=False)
    ordem = Column(Integer, nullable=True)
    conteudo = Column(Text, nullable=True)
    correta = Column(Integer, nullable=True)
    questao_id = Column(Integer, ForeignKey("questoes.id"), nullable=True)

    questao = relationship("QuestaoModel", back_populates="alternativas")
