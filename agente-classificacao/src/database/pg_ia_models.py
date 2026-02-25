from sqlalchemy import Column, Integer, Float, String, DateTime, func, ARRAY, Boolean, Text
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime, timezone
from ..database import PgBase

class ClassificacaoAgenteIaModel(PgBase):
    """
    Tabela classificacoes_agente_ia (PostgreSQL local)
    
    Armazena as classificacoes inferidas automaticamente pelo nosso Agente Classificador (LLM + Prompts por Disciplina).
    Isolado do historico oficial para permitir dry-runs e avaliacoes.
    """
    __tablename__ = "classificacoes_agente_ia"

    id = Column(Integer, primary_key=True, autoincrement=True)
    questao_id = Column(Integer, nullable=False, index=True, unique=True)
    enunciado = Column(Text, nullable=True)
    modulos_preditos = Column(JSONB, nullable=False, default=[])       # ["Coerencia", "Morfologia"]
    justificativas = Column(JSONB, nullable=True)                      # {"Modulo A": "Porque...", "Modulo B": "..."}
    modulos_possiveis = Column(JSONB, nullable=True)                   # Lista de módulos válidos para a habilidade
    descricoes_modulos = Column(JSONB, nullable=True)                  # {"Modulo": "Descricao..."}
    habilidade_trieduc = Column(JSONB, nullable=True)                  # {id, sigla, descricao}
    disciplina = Column(String(100), nullable=True)                    # Disciplina identificada
    categorias_preditas = Column(JSONB, nullable=False, default=[])    # ["Gramatica - Semantica"]
    confianca_media = Column(Float, nullable=False)                    # Confianca calculada (1.0 para LLM)
    modelo_utilizado = Column(String(100), nullable=False)             # "gpt-4o-mini_prompt_v1", etc.
    prompt_version = Column(String(50), nullable=True)                 # Versão do arquivo de prompt usado
    usou_llm = Column(Boolean, nullable=False, default=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )



class QuestaoEmbeddingModel(PgBase):
    """
    Tabela questao_embeddings (PostgreSQL local)
    
    Armazena o vetor denso (embedding) do texto das questoes gerado pela OpenAI.
    Usando ARRAY(Float) em vez de pgvector pois a extensao nao esta disponivel no momento.
    """
    __tablename__ = "questao_embeddings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    questao_id = Column(Integer, nullable=False, index=True, unique=True)
    embedding = Column(ARRAY(Float), nullable=False)                   # Armazena o vetor 1536d
    modelo_embedding = Column(String(50), nullable=False, default="text-embedding-3-small")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
