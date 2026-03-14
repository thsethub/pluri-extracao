from sqlalchemy import Column, Integer, Float, String, DateTime, JSON, func, Boolean, Text
from datetime import datetime, timezone
from ..database import PgBase

class ClassificacaoAgenteIaModel(PgBase):
    """
    Tabela classificacoes_agente_ia (PostgreSQL local)
    
    Armazena as classificacoes inferidas automaticamente pelo nosso Agente Classificador (LLM + Prompts por Disciplina).
    Isolado do historico oficial para permitir dry-runs e avaliacoes.
    """
    __tablename__ = "classificacoes_agente_ia"
    __table_args__ = {"schema": "thsethub", "extend_existing": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    questao_id = Column(Integer, nullable=False, index=True, unique=True)
    enunciado = Column(Text, nullable=True)
    modulos_sugeridos = Column(JSON, nullable=False, default=[])       # ["Coerencia", "Morfologia"]
    justificativas = Column(JSON, nullable=True)                      # {"Modulo A": "Porque...", "Modulo B": "..."}
    modulos_possiveis = Column(JSON, nullable=True)                   # Lista de módulos válidos para a habilidade
    assuntos_sugeridos = Column(JSON, nullable=True)                  # {"Modulo": "Descricao..."}
    habilidade_trieduc = Column(JSON, nullable=True)                  # {id, sigla, descricao}
    disciplina = Column(String(100), nullable=True)                    # Disciplina identificada
    categorias_preditas = Column(JSON, nullable=False, default=[])    # ["Gramatica - Semantica"]
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
    __table_args__ = {"schema": "thsethub", "extend_existing": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    questao_id = Column(Integer, nullable=False, index=True, unique=True)
    embedding = Column(JSON, nullable=False)                          # Armazena o vetor 1536d como JSON array
    modelo_embedding = Column(String(50), nullable=False, default="text-embedding-3-small")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ClassificacaoAgenteIaErroModel(PgBase):
    """
    Tabela classificacoes_agente_ia_erros (PostgreSQL local)

    Armazena falhas de processamento da classificacao IA para diagnostico
    e reprocessamento de questoes problemáticas.
    """
    __tablename__ = "classificacoes_agente_ia_erros"
    __table_args__ = {"schema": "thsethub", "extend_existing": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    questao_id = Column(Integer, nullable=True, index=True)
    etapa = Column(String(80), nullable=False, index=True)
    erro = Column(Text, nullable=False)
    stacktrace = Column(Text, nullable=True)
    payload = Column(JSON, nullable=True)
    modelo_utilizado = Column(String(100), nullable=True)
    prompt_version = Column(String(50), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

