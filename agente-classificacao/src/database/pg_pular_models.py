"""Modelo SQLAlchemy para questões puladas (pendentes) no PostgreSQL"""

from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint, func
from ..database import PgBase


class QuestaoPuladaModel(PgBase):
    """Tabela questoes_puladas (PostgreSQL local)

    Registra questões que o usuário pulou durante a classificação.
    Essas questões aparecem na aba "Pendentes" para revisão posterior.
    Ao classificar a questão, o registro é automaticamente removido.
    """

    __tablename__ = "questoes_puladas"
    __table_args__ = (
        UniqueConstraint("usuario_id", "questao_id", name="uq_usuario_questao_pulada"),
        {"extend_existing": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    usuario_id = Column(Integer, nullable=False, index=True)  # FK → usuarios.id
    questao_id = Column(Integer, nullable=False, index=True)  # FK → questoes.id (MySQL)
    area = Column(String(100), nullable=True)
    disciplina_id = Column(Integer, nullable=True)
    habilidade_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
