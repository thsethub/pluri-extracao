"""Modelo SQLAlchemy para questões puladas no PostgreSQL"""

from sqlalchemy import Column, Integer, String, DateTime, func, UniqueConstraint
from ..database import PgBase

class QuestaoPuladaModel(PgBase):
    """Tabela questoes_puladas (PostgreSQL local)
    Armazena as questões que o usuário pulou para não mostrar novamente no fluxo normal.
    """
    __tablename__ = "questoes_puladas"
    __table_args__ = (
        UniqueConstraint('usuario_id', 'questao_id', name='uq_usuario_questao_pulo'),
        {"schema": "thsethub", "extend_existing": True}
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    usuario_id = Column(Integer, nullable=False, index=True)
    questao_id = Column(Integer, nullable=False, index=True)
    area = Column(String(100), nullable=True)
    disciplina_id = Column(Integer, nullable=True)
    habilidade_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
