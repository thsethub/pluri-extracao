"""Router com endpoints de consulta ao banco de dados"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from typing import Optional
from math import ceil

from ..database import get_db
from ..database.models import (
    AnoModel,
    DisciplinaModel,
    HabilidadeModel,
    QuestaoModel,
    QuestaoAlternativaModel,
)
from .db_schemas import (
    AnoSchema,
    AnosListResponse,
    DisciplinaDBSchema,
    DisciplinasListResponse,
    HabilidadeDBSchema,
    HabilidadesListResponse,
    QuestaoResumoSchema,
    QuestaoDetalhadaSchema,
    QuestoesListResponse,
    AlternativaSchema,
)

router = APIRouter(prefix="/db", tags=["Database"])


# ========================
# ANOS
# ========================
@router.get(
    "/anos",
    response_model=AnosListResponse,
    summary="📅 Listar anos",
    response_description="Lista de todos os anos cadastrados",
)
async def listar_anos(db: Session = Depends(get_db)):
    """Retorna todos os anos cadastrados no banco."""
    anos = db.query(AnoModel).order_by(AnoModel.id).all()
    return AnosListResponse(
        data=[AnoSchema.model_validate(a) for a in anos],
        total=len(anos),
    )


@router.get(
    "/anos/{ano_id}",
    response_model=AnoSchema,
    summary="📅 Buscar ano por ID",
)
async def buscar_ano(ano_id: int, db: Session = Depends(get_db)):
    """Retorna um ano específico pelo ID."""
    ano = db.query(AnoModel).filter(AnoModel.id == ano_id).first()
    if not ano:
        raise HTTPException(status_code=404, detail="Ano não encontrado")
    return AnoSchema.model_validate(ano)


# ========================
# DISCIPLINAS
# ========================
@router.get(
    "/disciplinas",
    response_model=DisciplinasListResponse,
    summary="📚 Listar disciplinas do banco",
    response_description="Lista de todas as disciplinas cadastradas",
)
async def listar_disciplinas_db(db: Session = Depends(get_db)):
    """Retorna todas as disciplinas cadastradas no banco."""
    disciplinas = db.query(DisciplinaModel).order_by(DisciplinaModel.id).all()
    return DisciplinasListResponse(
        data=[DisciplinaDBSchema.model_validate(d) for d in disciplinas],
        total=len(disciplinas),
    )


@router.get(
    "/disciplinas/{disciplina_id}",
    response_model=DisciplinaDBSchema,
    summary="📚 Buscar disciplina por ID",
)
async def buscar_disciplina(disciplina_id: int, db: Session = Depends(get_db)):
    """Retorna uma disciplina específica pelo ID."""
    disciplina = (
        db.query(DisciplinaModel).filter(DisciplinaModel.id == disciplina_id).first()
    )
    if not disciplina:
        raise HTTPException(status_code=404, detail="Disciplina não encontrada")
    return DisciplinaDBSchema.model_validate(disciplina)


# ========================
# HABILIDADES
# ========================
@router.get(
    "/habilidades",
    response_model=HabilidadesListResponse,
    summary="🎯 Listar habilidades do banco",
    response_description="Lista de todas as habilidades cadastradas",
)
async def listar_habilidades_db(
    ano: Optional[str] = Query(None, description="Filtrar por ano"),
    sigla: Optional[str] = Query(None, description="Filtrar por sigla (busca parcial)"),
    db: Session = Depends(get_db),
):
    """Retorna todas as habilidades cadastradas no banco com filtros opcionais."""
    query = db.query(HabilidadeModel)

    if ano:
        query = query.filter(HabilidadeModel.ano.like(f"%{ano}%"))
    if sigla:
        query = query.filter(HabilidadeModel.sigla.like(f"%{sigla}%"))

    habilidades = query.order_by(HabilidadeModel.id).all()
    return HabilidadesListResponse(
        data=[HabilidadeDBSchema.model_validate(h) for h in habilidades],
        total=len(habilidades),
    )


@router.get(
    "/habilidades/{habilidade_id}",
    response_model=HabilidadeDBSchema,
    summary="🎯 Buscar habilidade por ID",
)
async def buscar_habilidade(habilidade_id: int, db: Session = Depends(get_db)):
    """Retorna uma habilidade específica pelo ID."""
    habilidade = (
        db.query(HabilidadeModel).filter(HabilidadeModel.id == habilidade_id).first()
    )
    if not habilidade:
        raise HTTPException(status_code=404, detail="Habilidade não encontrada")
    return HabilidadeDBSchema.model_validate(habilidade)


# ========================
# QUESTÕES
# ========================
@router.get(
    "/questoes",
    response_model=QuestoesListResponse,
    summary="📝 Listar questões",
    response_description="Lista paginada de questões",
)
async def listar_questoes(
    page: int = Query(1, ge=1, description="Página"),
    per_page: int = Query(20, ge=1, le=100, description="Itens por página"),
    disciplina_id: Optional[int] = Query(None, description="Filtrar por disciplina"),
    ano_id: Optional[int] = Query(None, description="Filtrar por ano"),
    habilidade_id: Optional[int] = Query(None, description="Filtrar por habilidade"),
    origem: Optional[str] = Query(None, description="Filtrar por origem"),
    tipo: Optional[str] = Query(None, description="Filtrar por tipo"),
    busca: Optional[str] = Query(None, description="Buscar no enunciado"),
    db: Session = Depends(get_db),
):
    """
    Retorna uma lista paginada de questões com filtros opcionais.

    - **page**: Número da página (default: 1)
    - **per_page**: Quantidade por página (default: 20, max: 100)
    - **disciplina_id**: Filtra por ID da disciplina
    - **ano_id**: Filtra por ID do ano
    - **habilidade_id**: Filtra por ID da habilidade
    - **origem**: Filtra por origem
    - **tipo**: Filtra por tipo
    - **busca**: Busca parcial no enunciado
    """
    query = db.query(QuestaoModel)

    if disciplina_id:
        query = query.filter(QuestaoModel.disciplina_id == disciplina_id)
    if ano_id:
        query = query.filter(QuestaoModel.ano_id == ano_id)
    if habilidade_id:
        query = query.filter(QuestaoModel.habilidade_id == habilidade_id)
    if origem:
        query = query.filter(QuestaoModel.origem.like(f"%{origem}%"))
    if tipo:
        query = query.filter(QuestaoModel.tipo.like(f"%{tipo}%"))
    if busca:
        query = query.filter(QuestaoModel.enunciado.like(f"%{busca}%"))

    total = query.count()
    pages = ceil(total / per_page) if total > 0 else 1
    offset = (page - 1) * per_page

    questoes = query.order_by(QuestaoModel.id).offset(offset).limit(per_page).all()

    return QuestoesListResponse(
        data=[QuestaoResumoSchema.model_validate(q) for q in questoes],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


@router.get(
    "/questoes/{questao_id}",
    response_model=QuestaoDetalhadaSchema,
    summary="📝 Buscar questão por ID",
    response_description="Questão detalhada com alternativas e relacionamentos",
)
async def buscar_questao(questao_id: int, db: Session = Depends(get_db)):
    """
    Retorna uma questão completa com todas as informações:
    - Dados da questão (enunciado, texto base, resolução)
    - Ano, disciplina e habilidade (com dados completos)
    - Alternativas
    """
    questao = (
        db.query(QuestaoModel)
        .options(
            joinedload(QuestaoModel.ano),
            joinedload(QuestaoModel.disciplina),
            joinedload(QuestaoModel.habilidade),
            joinedload(QuestaoModel.alternativas),
        )
        .filter(QuestaoModel.id == questao_id)
        .first()
    )
    if not questao:
        raise HTTPException(status_code=404, detail="Questão não encontrada")
    return QuestaoDetalhadaSchema.model_validate(questao)


@router.get(
    "/questoes/by-questao-id/{questao_id_str}",
    response_model=QuestaoDetalhadaSchema,
    summary="📝 Buscar questão pelo questao_id (string)",
    response_description="Questão detalhada buscada pelo campo questao_id",
)
async def buscar_questao_por_questao_id(
    questao_id_str: str, db: Session = Depends(get_db)
):
    """Busca uma questão pelo campo questao_id (string identificadora única)."""
    questao = (
        db.query(QuestaoModel)
        .options(
            joinedload(QuestaoModel.ano),
            joinedload(QuestaoModel.disciplina),
            joinedload(QuestaoModel.habilidade),
            joinedload(QuestaoModel.alternativas),
        )
        .filter(QuestaoModel.questao_id == questao_id_str)
        .first()
    )
    if not questao:
        raise HTTPException(status_code=404, detail="Questão não encontrada")
    return QuestaoDetalhadaSchema.model_validate(questao)


# ========================
# ALTERNATIVAS
# ========================
@router.get(
    "/questoes/{questao_id}/alternativas",
    response_model=list[AlternativaSchema],
    summary="🔤 Listar alternativas de uma questão",
)
async def listar_alternativas(questao_id: int, db: Session = Depends(get_db)):
    """Retorna todas as alternativas de uma questão específica."""
    questao = db.query(QuestaoModel).filter(QuestaoModel.id == questao_id).first()
    if not questao:
        raise HTTPException(status_code=404, detail="Questão não encontrada")

    alternativas = (
        db.query(QuestaoAlternativaModel)
        .filter(QuestaoAlternativaModel.questao_id == questao_id)
        .order_by(QuestaoAlternativaModel.ordem)
        .all()
    )
    return [AlternativaSchema.model_validate(a) for a in alternativas]


# ========================
# ESTATÍSTICAS
# ========================
@router.get(
    "/stats",
    summary="📊 Estatísticas do banco",
    response_description="Contagens gerais das tabelas",
)
async def estatisticas(db: Session = Depends(get_db)):
    """Retorna estatísticas gerais do banco de dados."""
    return {
        "anos": db.query(AnoModel).count(),
        "disciplinas": db.query(DisciplinaModel).count(),
        "habilidades": db.query(HabilidadeModel).count(),
        "questoes": db.query(QuestaoModel).count(),
        "alternativas": db.query(QuestaoAlternativaModel).count(),
    }
