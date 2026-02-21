"""Modelos SQLAlchemy para tabelas de módulos e classificação no PostgreSQL"""

from sqlalchemy import Column, Integer, String, Text, DateTime, func, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from ..database import PgBase


class HabilidadeModuloModel(PgBase):
    """Tabela habilidade_modulos (PostgreSQL local)

    Mapeamento plano: cada linha = um par (habilidade TriEduc → módulo da planilha).
    Relacionamento N:N — uma habilidade pode ter vários módulos e vice-versa.

    Exemplo:
        habilidade_id=70, habilidade_descricao="Introdução à Biologia - Método científico"
        → area="Natureza", disciplina="Biologia", modulo="Ciência da vida",
          descricao="O método científico e suas etapas"
    """

    __tablename__ = "habilidade_modulos"
    __table_args__ = (
        UniqueConstraint(
            "habilidade_descricao", "area", "disciplina", "modulo", "descricao",
            name="uq_hab_modulo_descricao"
        ),
        {"extend_existing": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    habilidade_id = Column(Integer, nullable=True, index=True)
    habilidade_descricao = Column(String(255), nullable=False, index=True)
    area = Column(String(100), nullable=False)
    disciplina = Column(String(100), nullable=False)
    modulo = Column(String(255), nullable=False)
    descricao = Column(String(500), nullable=False)
    ordenacao = Column(Integer, nullable=True)
    disc_modu_id = Column(String(100), nullable=True)
    status = Column(String(50), nullable=True)  # "novo", "modificado", "unificado", None
    created_at = Column(DateTime(timezone=True), server_default=func.now())
