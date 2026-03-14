"""Configuração do banco de dados com SQLAlchemy (MySQL)"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from ..config import settings

# ========================
# MySQL - Conexão única com usuário thsethub
# Acessa múltiplos bancos: trieduc, thsethub, compartilhados, homologacao
# ========================
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False,
)

# Session padrão - acessa todos os bancos do usuário
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Aliases para compatibilidade com código legado
PgSessionLocal = SessionLocal  # Antes era conexão separada, agora usa a mesma
SharedSessionLocal = SessionLocal  # Antes era conexão separada, agora usa a mesma

Base = declarative_base()
PgBase = Base  # Alias para compatibilidade


def get_db():
    """Dependency para injeção de sessão do banco MySQL"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_pg_db():
    """Dependency para injeção de sessão (compatibilidade - usa mesma conexão)"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_shared_db():
    """Dependency para injeção de sessão (compatibilidade - usa mesma conexão)"""
    db = SessionLocal()
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

    PgBase.metadata.create_all(bind=engine)
