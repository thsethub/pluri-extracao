from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Float,
    DateTime,
    Boolean,
    ForeignKey,
    func,
)
from sqlalchemy.orm import relationship, declarative_base, deferred

Base = declarative_base()


class RedacaoModel(Base):
    __tablename__ = "co_redacoes"
    __table_args__ = {"schema": "corrigeai", "extend_existing": True}

    redacao_id = Column(Integer, primary_key=True)
    teste_prova_id = Column(Integer)
    redacao_status_id = Column(Integer)
    arquivo_id = Column(Integer, ForeignKey("corrigeai.co_arquivos.arquivo_id"))
    tema = Column(String(255))
    redacao_texto = deferred(Column(Text))
    ocr_confianca = Column(Float)
    deleted_at = Column(DateTime)


class ArquivoModel(Base):
    __tablename__ = "co_arquivos"
    __table_args__ = {"schema": "corrigeai", "extend_existing": True}

    arquivo_id = Column(Integer, primary_key=True)
    arquivo_anonimo_nome_armazenamento = Column(String(255))

    redacoes = relationship("RedacaoModel", backref="arquivo")


class ValidacaoOcrModel(Base):
    __tablename__ = "co_validacao_ocr"
    __table_args__ = {"schema": "corrigeai", "extend_existing": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    revisor_id = Column(Integer, nullable=False)
    redacao_id = Column(
        Integer, ForeignKey("corrigeai.co_redacoes.redacao_id"), nullable=False
    )
    ocr_pulou_trechos = Column(Boolean, nullable=False, default=False)
    ocr_trocou_palavras = Column(Boolean, nullable=False, default=False)
    ocr_trocou_caracteres = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    redacao = relationship("RedacaoModel")
