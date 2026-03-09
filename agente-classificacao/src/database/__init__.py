"""Configuração do banco de dados com SQLAlchemy (MySQL + PostgreSQL)"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from ..config import settings

# ========================
# MySQL (leitura - questões)
# ========================
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency para injeção de sessão do banco MySQL"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ========================
# MySQL RDS (escrita - assuntos, ex-PostgreSQL)
# ========================
pg_engine = create_engine(
    settings.pg_database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False,
)

PgSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)

PgBase = declarative_base()


def get_pg_db():
    """Dependency para injeção de sessão do banco MySQL RDS (assuntos)"""
    db = PgSessionLocal()
    try:
        yield db
    finally:
        db.close()


# ========================
# MySQL compartilhados (leitura - assuntos e disciplina_modulos)
# ========================
shared_engine = create_engine(
    settings.shared_database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False,
)

SharedSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=shared_engine)


def get_shared_db():
    """Dependency para injeção de sessão do banco MySQL compartilhados."""
    db = SharedSessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_pg_tables():
    """Cria as tabelas no PostgreSQL se não existirem"""
    from .pg_models import QuestaoAssuntoModel  # noqa: F401
    from .pg_modulo_models import HabilidadeModuloModel  # noqa: F401
    from .pg_usuario_models import UsuarioModel, ClassificacaoUsuarioModel  # noqa: F401
    from .pg_pular_models import QuestaoPuladaModel  # noqa: F401
    from .pg_ia_models import (
        ClassificacaoAgenteIaModel,
        QuestaoEmbeddingModel,
        ClassificacaoAgenteIaErroModel,
    )  # noqa: F401

    PgBase.metadata.create_all(bind=pg_engine)
