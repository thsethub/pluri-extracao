"""Modelo SQLAlchemy para a tabela questao_assuntos no PostgreSQL"""

from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime, timezone
from ..database import PgBase


class QuestaoAssuntoModel(PgBase):
    """Tabela questao_assuntos (PostgreSQL local)

    Armazena as classificações de assunto extraídas via webscraping.
    Cada questão pode ter múltiplas classificações (array de strings).

    Exemplo de classificacoes:
    [
        "História > Brasil > Sistema Colonial > Relações Socioeconômicas e Culturais",
        "História > Brasil > Escravidão"
    ]
    """

    __tablename__ = "questao_assuntos"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    questao_id = Column(Integer, nullable=False, unique=True, index=True)
    questao_id_str = Column(String(100), nullable=False, index=True)
    superpro_id = Column(Integer, nullable=True, index=True)
    disciplina_id = Column(Integer, nullable=True)
    disciplina_nome = Column(String(100), nullable=True)
    classificacoes = Column(JSONB, nullable=True, default=[])
    enunciado_original = Column(Text, nullable=True)
    enunciado_tratado = Column(Text, nullable=True)
    extracao_feita = Column(Boolean, nullable=False, default=False)
    contem_imagem = Column(Boolean, nullable=False, default=False)
    motivo_erro = Column(String(255), nullable=True)
    created_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
