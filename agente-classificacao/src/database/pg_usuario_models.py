"""Modelo SQLAlchemy para autenticação e classificação manual de usuários no banco thsethub"""

from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, Float, JSON, func
from ..database import PgBase


class UsuarioModel(PgBase):
    """Tabela usuarios do banco thsethub

    Armazena usuários do sistema de classificação manual.
    Cada usuário é professor de uma disciplina específica.
    """

    __tablename__ = "usuarios"
    __table_args__ = {"schema": "thsethub", "extend_existing": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String(200), nullable=False)
    email = Column(String(255), nullable=False, unique=True, index=True)
    senha_hash = Column(String(255), nullable=False)
    disciplina = Column(String(100), nullable=False)  # Disciplina que leciona
    is_admin = Column(Boolean, nullable=False, default=False)
    ativo = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ClassificacaoUsuarioModel(PgBase):
    """Tabela classificacao_usuario do banco thsethub

    Armazena todas as decisões de classificação feitas por usuários.
    Cada registro = uma decisão (classificação nova, confirmação ou correção).
    Dados usados para treino futuro de modelo ML.
    """

    __tablename__ = "classificacao_usuario"
    __table_args__ = {"schema": "thsethub", "extend_existing": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    usuario_id = Column(Integer, nullable=False, index=True)  # FK → usuarios.id
    questao_id = Column(Integer, nullable=False, index=True)  # FK → questoes.id (MySQL)
    habilidade_id = Column(Integer, nullable=True)  # habilidade TriEduc da questão

    # Classificação escolhida pelo usuário (campos legados - single)
    modulo_escolhido = Column(String(255), nullable=True)
    classificacao_trieduc = Column(String(255), nullable=True)
    descricao_assunto = Column(String(500), nullable=True)  # Descrição detalhada do assunto/módulo
    habilidade_modulo_id = Column(Integer, nullable=True)  # FK → habilidade_modulos.id

    # Classificação múltipla (novos campos JSONB)
    modulos_escolhidos = Column(JSON, nullable=True)  # Lista de nomes dos módulos
    classificacoes_trieduc_list = Column(JSON, nullable=True)  # Lista de classificações TRIEDUC
    descricoes_assunto_list = Column(JSON, nullable=True)  # Lista de descrições
    habilidade_modulo_ids = Column(JSON, nullable=True)  # Lista de IDs de habilidade_modulos

    # Classificação da extração automática (para comparação no ML)
    classificacao_extracao = Column(JSON, nullable=True)

    # Metadados para ML
    tipo_acao = Column(
        String(50), nullable=False
    )  # "classificacao_nova", "confirmacao", "correcao"
    observacao = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Controle de migração
    migrada = Column(Boolean, nullable=False, default=False, server_default="0", index=True)
