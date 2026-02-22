"""Router com endpoints do sistema de classificação manual por usuários.

Rotas separadas do sistema de extração/conferência.
Protegidas por autenticação JWT.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from typing import Optional
from math import ceil
from datetime import datetime, timedelta, timezone
from loguru import logger

from jose import JWTError, jwt
import bcrypt

from ..database import get_db, get_pg_db
from ..database.models import QuestaoModel, HabilidadeModel
from ..database.pg_models import QuestaoAssuntoModel
from ..database.pg_modulo_models import HabilidadeModuloModel
from ..database.pg_usuario_models import UsuarioModel, ClassificacaoUsuarioModel
from ..services.enunciado_cleaner import tratar_enunciado
from .classificacao_schemas import (
    CadastroRequest,
    LoginRequest,
    TokenResponse,
    UsuarioSchema,
    HabilidadeModuloSchema,
    ModulosResponse,
    HabilidadeFiltroSchema,
    HabilidadesFiltroResponse,
    AlternativaClassifSchema,
    QuestaoClassifResponse,
    SalvarClassificacaoRequest,
    SalvarClassificacaoResponse,
    ClassificacaoStatsResponse,
    ClassificacaoHistoricoSchema,
    HistoricoListResponse,
)

# ========================
# CONFIG
# ========================
SECRET_KEY = "pluri-classificacao-secret-key-2026"  # Em produção, usar variável de ambiente
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480  # 8 horas

# bcrypt 5.x — usar diretamente (passlib não compatível)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/classificacao/login")

router = APIRouter(prefix="/classificacao", tags=["Classificação Manual"])

# Disciplinas válidas para cadastro (incluindo Áreas)
DISCIPLINAS_VALIDAS = [
    "Artes", "Biologia", "Ciências", "Educação Física", "Espanhol",
    "Filosofia", "Física", "Geografia", "História", "Língua Inglesa",
    "Língua Portuguesa", "Matemática", "Natureza e Sociedade", "Química", "Sociologia",
    # Áreas
    "Humanas", "Linguagens", "Natureza"
]

# Mapeamento de áreas para filtro
AREAS_DISCIPLINAS = {
    "Humanas": ["Filosofia", "Geografia", "História", "Sociologia"],
    "Linguagens": ["Artes", "Educação Física", "Espanhol", "Língua Inglesa", "Língua Portuguesa"],
    "Matemática": ["Matemática"],
    "Natureza": ["Biologia", "Ciências", "Física", "Natureza e Sociedade", "Química"],
}


# ========================
# HELPERS
# ========================

def criar_token(data: dict) -> str:
    """Cria um token JWT."""
    to_encode = data.copy()
    # JWT spec requires 'sub' to be a string
    if "sub" in to_encode:
        to_encode["sub"] = str(to_encode["sub"])
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verificar_senha(senha_plain: str, senha_hash: str) -> bool:
    return bcrypt.checkpw(
        senha_plain.encode("utf-8"), senha_hash.encode("utf-8")
    )


def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(
        senha.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")


async def get_usuario_atual(
    token: str = Depends(oauth2_scheme),
    pg_db: Session = Depends(get_pg_db),
) -> UsuarioModel:
    """Dependency: extrai e valida o usuário do token JWT."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido ou expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub_value = payload.get("sub")
        if sub_value is None:
            raise credentials_exception
        usuario_id = int(sub_value)
    except (JWTError, ValueError):
        raise credentials_exception

    usuario = pg_db.query(UsuarioModel).filter(UsuarioModel.id == usuario_id).first()
    if usuario is None or not usuario.ativo:
        raise credentials_exception
    return usuario


# ========================
# AUTENTICAÇÃO
# ========================

@router.post(
    "/cadastro",
    response_model=TokenResponse,
    summary="📝 Cadastrar novo usuário",
    status_code=status.HTTP_201_CREATED,
)
async def cadastrar_usuario(
    request: CadastroRequest,
    pg_db: Session = Depends(get_pg_db),
):
    """
    Cadastra um novo usuário para classificação manual.
    O campo `disciplina` deve ser uma das disciplinas válidas do sistema.
    """
    # Validar disciplina
    if request.disciplina not in DISCIPLINAS_VALIDAS:
        raise HTTPException(
            status_code=400,
            detail=f"Disciplina inválida. Opções: {', '.join(DISCIPLINAS_VALIDAS)}",
        )

    # Verificar email duplicado
    existente = pg_db.query(UsuarioModel).filter(UsuarioModel.email == request.email).first()
    if existente:
        raise HTTPException(status_code=400, detail="Email já cadastrado")

    # Criar usuário
    usuario = UsuarioModel(
        nome=request.nome,
        email=request.email,
        senha_hash=hash_senha(request.senha),
        disciplina=request.disciplina,
    )
    pg_db.add(usuario)
    pg_db.commit()
    pg_db.refresh(usuario)

    # Gerar token
    token = criar_token({"sub": usuario.id})
    logger.info(f"Novo usuário cadastrado: {usuario.nome} ({usuario.disciplina})")

    return TokenResponse(
        access_token=token,
        usuario=UsuarioSchema.model_validate(usuario),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="🔑 Login",
)
async def login(
    request: LoginRequest,
    pg_db: Session = Depends(get_pg_db),
):
    """Autentica o usuário e retorna um token JWT."""
    usuario = pg_db.query(UsuarioModel).filter(UsuarioModel.email == request.email).first()
    if not usuario or not verificar_senha(request.senha, usuario.senha_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou senha incorretos",
        )
    if not usuario.ativo:
        raise HTTPException(status_code=403, detail="Usuário desativado")

    token = criar_token({"sub": usuario.id})
    logger.info(f"Login: {usuario.nome}")

    return TokenResponse(
        access_token=token,
        usuario=UsuarioSchema.model_validate(usuario),
    )


@router.get(
    "/me",
    response_model=UsuarioSchema,
    summary="👤 Dados do usuário atual",
)
async def dados_usuario(usuario: UsuarioModel = Depends(get_usuario_atual)):
    """Retorna os dados do usuário autenticado."""
    return UsuarioSchema.model_validate(usuario)


@router.get(
    "/disciplinas",
    summary="📚 Disciplinas disponíveis",
)
async def listar_disciplinas():
    """Retorna as disciplinas disponíveis para cadastro e as áreas para filtro."""
    return {
        "disciplinas": DISCIPLINAS_VALIDAS,
        "areas": AREAS_DISCIPLINAS,
    }


@router.get(
    "/habilidades",
    response_model=HabilidadesFiltroResponse,
    summary="🔍 Listar assuntos (habilidades) para filtro",
)
async def listar_habilidades_filtro(
    area: Optional[str] = Query(None, description="Filtrar por área"),
    disciplina: Optional[str] = Query(None, description="Filtrar por nome da disciplina"),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna a lista de assuntos únicos (habilidade_id + habilidade_descricao)
    para popular o dropdown de filtros no frontend.
    """
    query = pg_db.query(
        HabilidadeModuloModel.habilidade_id,
        HabilidadeModuloModel.habilidade_descricao
    ).distinct()

    if area:
        query = query.filter(HabilidadeModuloModel.area == area)
    if disciplina:
        query = query.filter(HabilidadeModuloModel.disciplina == disciplina)

    # Ordenar por descrição para facilitar a busca do usuário
    results = query.order_by(HabilidadeModuloModel.habilidade_descricao).all()

    habilidades = [
        HabilidadeFiltroSchema(
            habilidade_id=r.habilidade_id,
            habilidade_descricao=r.habilidade_descricao
        )
        for r in results if r.habilidade_id is not None
    ]

    return HabilidadesFiltroResponse(habilidades=habilidades, total=len(habilidades))


# ========================
# MÓDULOS (consulta)
# ========================

@router.get(
    "/modulos/{habilidade_id}",
    response_model=ModulosResponse,
    summary="📦 Módulos possíveis para uma habilidade",
)
async def listar_modulos_por_habilidade(
    habilidade_id: int,
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna os módulos possíveis para um dado habilidade_id do TriEduc."""
    modulos = (
        pg_db.query(HabilidadeModuloModel)
        .filter(HabilidadeModuloModel.habilidade_id == habilidade_id)
        .order_by(HabilidadeModuloModel.area, HabilidadeModuloModel.disciplina, HabilidadeModuloModel.modulo)
        .all()
    )

    return ModulosResponse(
        habilidade_id=habilidade_id,
        modulos=[HabilidadeModuloSchema.model_validate(m) for m in modulos],
        total=len(modulos),
    )


# ========================
# QUESTÕES PARA CLASSIFICAR
# ========================

@router.get(
    "/proxima",
    response_model=QuestaoClassifResponse,
    summary="🔍 Próxima questão para classificar",
)
async def proxima_questao_classificar(
    area: Optional[str] = Query(None, description="Filtrar por área (Humanas, Linguagens, Matemática, Natureza)"),
    disciplina_id: Optional[str] = Query(None, description="ID ou Nome da disciplina"),
    habilidade_id: Optional[int] = Query(None, description="ID da habilidade TRIEDUC"),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna a próxima questão que ainda NÃO foi classificada manualmente pelo usuário.
    Prioriza questões sem extração automática.

    Filtros:
    - **area**: "Humanas", "Linguagens", "Matemática", "Natureza"
    - **disciplina_id**: ID numérico da disciplina
    - **habilidade_id**: ID da habilidade TRIEDUC
    """
    # IDs a excluir (já classificadas por este usuário OU já possuem classificação no sistema)
    # Forçar área do usuário se não enviada
    if not area:
        area = usuario.disciplina

    # Resolver filtro de área → disciplinas (Otimizado: apenas IDs)
    disciplina_ids_filtro = None
    if area and area in AREAS_DISCIPLINAS:
        from ..database.models import DisciplinaModel
        nomes = AREAS_DISCIPLINAS[area]
        discs_ids = db.query(DisciplinaModel.id).filter(DisciplinaModel.descricao.in_(nomes)).all()
        disciplina_ids_filtro = [d[0] for d in discs_ids]

    # OPTIMIZATION: 'Seek Method' (ID > last_id) instead of OFFSET.
    # This is much faster for large tables as it uses the Primary Key index directly.
    
    LIMIT_CANDIDATES = 200
    MAX_LOOP_TRIES = 50 # Total candidates to check = 10,000
    
    # Base query for candidate IDs in MySQL
    # We select ONLY the ID to keep it lightweight
    candidate_query = (
        db.query(QuestaoModel.id)
        .filter(QuestaoModel.habilidade_id.isnot(None))
        .filter(QuestaoModel.ano_id == 3) # Ensino Médio
    )
    
    if habilidade_id:
        candidate_query = candidate_query.filter(QuestaoModel.habilidade_id == habilidade_id)
    
    if disciplina_id:
        if str(disciplina_id).isdigit():
            candidate_query = candidate_query.filter(QuestaoModel.disciplina_id == int(disciplina_id))
        else:
            from ..database.models import DisciplinaModel
            disc_id_row = db.query(DisciplinaModel.id).filter(DisciplinaModel.descricao == disciplina_id).first()
            if disc_id_row:
                candidate_query = candidate_query.filter(QuestaoModel.disciplina_id == disc_id_row[0])
    elif disciplina_ids_filtro:
        candidate_query = candidate_query.filter(QuestaoModel.disciplina_id.in_(disciplina_ids_filtro))

    candidate_query = candidate_query.order_by(QuestaoModel.id)
    
    last_id = 0
    questao_final = None
    
    for _ in range(MAX_LOOP_TRIES):
        # Fetch next block of candidate IDs starting from last_id
        candidates = candidate_query.filter(QuestaoModel.id > last_id).limit(LIMIT_CANDIDATES).all()
        if not candidates:
            break
            
        candidate_ids = [c[0] for c in candidates]
        last_id = candidate_ids[-1] # Update for next possible loop
        
        # Check in PostgreSQL which candidates of THIS block are already processed
        # 1. Already classified by this user
        classified_by_user = {
            row[0] for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
            .filter(ClassificacaoUsuarioModel.questao_id.in_(candidate_ids))
            .filter(ClassificacaoUsuarioModel.usuario_id == usuario.id)
            .all()
        }
        
        # 2. Already has manual or automatic classification in PG
        classified_in_system = {
            row[0] for row in pg_db.query(QuestaoAssuntoModel.questao_id)
            .filter(QuestaoAssuntoModel.questao_id.in_(candidate_ids))
            .filter(
                (QuestaoAssuntoModel.classificado_manualmente == True) |
                ((QuestaoAssuntoModel.classificacoes.isnot(None)) & (QuestaoAssuntoModel.classificacoes != []))
            )
            .all()
        }
        
        ids_excluir = classified_by_user.union(classified_in_system)
        
        # Find first candidate not in exclude set
        valid_id = None
        for cid in candidate_ids:
            if cid not in ids_excluir:
                valid_id = cid
                break
        
        if valid_id:
            # Fetch FULL details only for the 1 question found
            questao_final = (
                db.query(QuestaoModel)
                .options(
                    joinedload(QuestaoModel.disciplina),
                    joinedload(QuestaoModel.habilidade),
                    joinedload(QuestaoModel.alternativas),
                )
                .filter(QuestaoModel.id == valid_id)
                .first()
            )
            
            if questao_final:
                # Basic text check
                enunciado_tratado, contem_imagem, motivo_erro = tratar_enunciado(questao_final.enunciado)
                if not motivo_erro:
                    # Success!
                    break
                else:
                    # Mark as invalid in PG so we don't try it again
                    if valid_id not in classified_in_system:
                        disc_nome = questao_final.disciplina.descricao if questao_final.disciplina else None
                        reg = QuestaoAssuntoModel(
                            questao_id=questao_final.id,
                            questao_id_str=questao_final.questao_id,
                            disciplina_id=questao_final.disciplina_id,
                            disciplina_nome=disc_nome,
                            classificacoes=[],
                            enunciado_original=questao_final.enunciado,
                            enunciado_tratado=enunciado_tratado,
                            extracao_feita=False,
                            contem_imagem=contem_imagem,
                            motivo_erro=motivo_erro
                        )
                        pg_db.add(reg)
                        pg_db.commit()
                    questao_final = None # Keep looking in the same or next block
    
    if not questao_final:
        raise HTTPException(
            status_code=404,
            detail="Nenhuma questão pendente para classificação encontrada.",
        )

    # Re-use details
    questao = questao_final
    enunciado_tratado, contem_imagem, motivo_erro = tratar_enunciado(questao.enunciado)

    # Check for suggested extraction to display
    extracao = (
        pg_db.query(QuestaoAssuntoModel)
        .filter(QuestaoAssuntoModel.questao_id == questao.id)
        .first()
    )

    hab_descricao = None
    if questao.habilidade:
        hab_descricao = questao.habilidade.descricao

    # Módulos possíveis
    modulos = []
    if questao.habilidade_id:
        from ..database.pg_modulo_models import HabilidadeModuloModel
        from .classificacao_schemas import HabilidadeModuloSchema

        modulos_q = (
            pg_db.query(HabilidadeModuloModel)
            .filter(HabilidadeModuloModel.habilidade_id == questao.habilidade_id)
            .order_by(HabilidadeModuloModel.modulo)
            .all()
        )
        modulos = [HabilidadeModuloSchema.model_validate(m) for m in modulos_q]

        # FALLBACK: Se não achou módulos por ID, tenta por descrição (Case Insensitive)
        if not modulos and hab_descricao:
            from sqlalchemy import func
            modulos_q = (
                pg_db.query(HabilidadeModuloModel)
                .filter(func.lower(HabilidadeModuloModel.habilidade_descricao) == hab_descricao.lower())
                .order_by(HabilidadeModuloModel.modulo)
                .all()
            )
            modulos = [HabilidadeModuloSchema.model_validate(m) for m in modulos_q]

    alternativas = []
    if questao.tipo == "Múltipla Escolha" and questao.alternativas:
        for alt in sorted(questao.alternativas, key=lambda a: a.ordem or 0):
            conteudo_limpo, _, _ = tratar_enunciado(alt.conteudo or "")
            alternativas.append(
                AlternativaClassifSchema(
                    ordem=alt.ordem or 0,
                    conteudo=conteudo_limpo,
                    conteudo_html=alt.conteudo,
                    correta=bool(alt.correta),
                )
            )

    disc_nome = questao.disciplina.descricao if questao.disciplina else None

    return QuestaoClassifResponse(
        id=questao.id,
        questao_id=questao.questao_id,
        enunciado=enunciado_tratado,
        enunciado_html=questao.enunciado,
        disciplina_id=questao.disciplina_id,
        disciplina_nome=disc_nome,
        habilidade_id=questao.habilidade_id,
        habilidade_descricao=hab_descricao,
        tipo=questao.tipo,
        alternativas=alternativas,
        classificacao_extracao=extracao.classificacoes if extracao and extracao.extracao_feita else None,
        tem_extracao=bool(extracao and extracao.extracao_feita and extracao.classificacoes),
        modulos_possiveis=modulos,
    )


@router.get(
    "/proxima-verificar",
    response_model=QuestaoClassifResponse,
    summary="🔄 Próxima questão para verificar (já classificada)",
)
async def proxima_questao_verificar(
    area: Optional[str] = Query(None, description="Filtrar por área"),
    disciplina_id: Optional[int] = Query(None, description="ID da disciplina"),
    habilidade_id: Optional[int] = Query(None, description="ID da habilidade TRIEDUC"),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna a próxima questão que JÁ tem classificação automática
    para o usuário verificar se está correta.
    """
    # IDs já verificadas por este usuário
    ids_verificadas = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.usuario_id == usuario.id)
        .all()
    }

    # Forçar área do usuário se não enviada
    if not area:
        area = usuario.disciplina

    # Resolver filtro de área
    disciplina_ids_filtro = None
    if area and area in AREAS_DISCIPLINAS:
        from ..database.models import DisciplinaModel
        nomes = AREAS_DISCIPLINAS[area]
        discs = db.query(DisciplinaModel).filter(DisciplinaModel.descricao.in_(nomes)).all()
        disciplina_ids_filtro = [d.id for d in discs]

    # Query Base no PG (extraídas)
    query_pg = pg_db.query(QuestaoAssuntoModel).filter(
        QuestaoAssuntoModel.extracao_feita == True,
        QuestaoAssuntoModel.classificacoes.isnot(None)
    )

    if habilidade_id:
        query_pg = query_pg.filter(QuestaoAssuntoModel.habilidade_id == habilidade_id)

    if ids_verificadas:
        query_pg = query_pg.filter(~QuestaoAssuntoModel.questao_id.in_(ids_verificadas))

    if disciplina_id:
        disc_target_id = None
        if str(disciplina_id).isdigit():
            disc_target_id = int(disciplina_id)
        else:
            from ..database.models import DisciplinaModel
            disc = db.query(DisciplinaModel).filter(DisciplinaModel.descricao == disciplina_id).first()
            if disc:
                disc_target_id = disc.id
        
        if disc_target_id:
            query_pg = query_pg.filter(QuestaoAssuntoModel.disciplina_id == disc_target_id)
    elif disciplina_ids_filtro:
        query_pg = query_pg.filter(QuestaoAssuntoModel.disciplina_id.in_(disciplina_ids_filtro))

    # Tentar encontrar uma questão que seja efetivamente de Ensino Médio no MySQL
    MAX_TENTATIVAS = 100
    for _ in range(MAX_TENTATIVAS):
        registro_pg = query_pg.order_by(QuestaoAssuntoModel.id).first()
        
        if not registro_pg:
            raise HTTPException(
                status_code=404,
                detail="Nenhuma questão pendente de verificação com os filtros aplicados",
            )

        # Verificar nível no MySQL
        questao = (
            db.query(QuestaoModel)
            .options(
                joinedload(QuestaoModel.disciplina),
                joinedload(QuestaoModel.habilidade),
                joinedload(QuestaoModel.alternativas),
            )
            .filter(QuestaoModel.id == registro_pg.questao_id)
            .first()
        )

        if not questao or questao.ano_id != 3:
            # Pula esta e marca como "inválida para este fluxo" temporariamente na query
            ids_verificadas.add(registro_pg.questao_id)
            query_pg = query_pg.filter(QuestaoAssuntoModel.questao_id != registro_pg.questao_id)
            continue

        # Se chegou aqui, temos a questão!
        break
    else:
        raise HTTPException(status_code=404, detail="Não foram encontradas questões de Ensino Médio para verificar.")

    # Tratar enunciado
    enunciado_tratado, contem_imagem, motivo_erro = tratar_enunciado(questao.enunciado)

    # Módulos possíveis
    modulos = []
    hab_descricao = None
    if questao.habilidade_id:
        modulos_q = (
            pg_db.query(HabilidadeModuloModel)
            .filter(HabilidadeModuloModel.habilidade_id == questao.habilidade_id)
            .order_by(HabilidadeModuloModel.modulo)
            .all()
        )
        modulos = [HabilidadeModuloSchema.model_validate(m) for m in modulos_q]

        from ..database.models import HabilidadeModel
        hab = db.query(HabilidadeModel).filter(HabilidadeModel.id == questao.habilidade_id).first()
        if hab:
            hab_descricao = hab.descricao

        # FALLBACK: Se não achou módulos por ID, tenta por descrição (Case Insensitive)
        if not modulos and hab_descricao:
            from sqlalchemy import func
            modulos_q = (
                pg_db.query(HabilidadeModuloModel)
                .filter(func.lower(HabilidadeModuloModel.habilidade_descricao) == hab_descricao.lower())
                .order_by(HabilidadeModuloModel.modulo)
                .all()
            )
            modulos = [HabilidadeModuloSchema.model_validate(m) for m in modulos_q]

    # Alternativas
    alternativas = []
    if questao.tipo == "Múltipla Escolha" and questao.alternativas:
        for alt in sorted(questao.alternativas, key=lambda a: a.ordem or 0):
            conteudo_limpo, _, _ = tratar_enunciado(alt.conteudo or "")
            alternativas.append(
                AlternativaClassifSchema(
                    ordem=alt.ordem or 0,
                    conteudo=conteudo_limpo,
                    conteudo_html=alt.conteudo,
                    correta=bool(alt.correta),
                )
            )

    disc_nome = questao.disciplina.descricao if questao.disciplina else None

    return QuestaoClassifResponse(
        id=questao.id,
        questao_id=questao.questao_id,
        enunciado=enunciado_tratado or "",
        enunciado_html=questao.enunciado,
        disciplina_id=questao.disciplina_id,
        disciplina_nome=disc_nome,
        habilidade_id=questao.habilidade_id,
        habilidade_descricao=hab_descricao,
        tipo=questao.tipo,
        alternativas=alternativas,
        classificacao_extracao=registro_pg.classificacoes,
        tem_extracao=True,
        modulos_possiveis=modulos,
    )


# ========================
# PRÓXIMA QUESTÃO LOW MATCH
# ========================

@router.get(
    "/proxima-low-match",
    response_model=QuestaoClassifResponse,
    summary="⚠️ Próxima questão com classificação de baixa similaridade",
)
async def proxima_questao_low_match(
    area: Optional[str] = Query(None, description="Filtrar por área"),
    disciplina_id: Optional[str] = Query(None, description="ID ou Nome da disciplina"),
    habilidade_id: Optional[int] = Query(None, description="ID da habilidade TRIEDUC"),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna a próxima questão que possui classificacao_nao_enquadrada
    (match baixo do SuperProfessor) para revisão pelo professor.
    """
    # IDs já verificadas por este usuário
    ids_verificadas = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.usuario_id == usuario.id)
        .all()
    }

    # Forçar área do usuário se não enviada
    if not area:
        area = usuario.disciplina

    # Resolver filtro de área
    disciplina_ids_filtro = None
    if area and area in AREAS_DISCIPLINAS:
        from ..database.models import DisciplinaModel
        nomes = AREAS_DISCIPLINAS[area]
        discs = db.query(DisciplinaModel).filter(DisciplinaModel.descricao.in_(nomes)).all()
        disciplina_ids_filtro = [d.id for d in discs]

    # Query no PG: questões com classificacao_nao_enquadrada preenchida
    query_pg = pg_db.query(QuestaoAssuntoModel).filter(
        QuestaoAssuntoModel.classificacao_nao_enquadrada.isnot(None),
        func.jsonb_array_length(QuestaoAssuntoModel.classificacao_nao_enquadrada) > 0,
        QuestaoAssuntoModel.classificado_manualmente == False,
    )

    if habilidade_id:
        query_pg = query_pg.filter(QuestaoAssuntoModel.habilidade_id == habilidade_id)

    if ids_verificadas:
        query_pg = query_pg.filter(~QuestaoAssuntoModel.questao_id.in_(ids_verificadas))

    if disciplina_id:
        if str(disciplina_id).isdigit():
            query_pg = query_pg.filter(QuestaoAssuntoModel.disciplina_id == int(disciplina_id))
        else:
            from ..database.models import DisciplinaModel
            disc = db.query(DisciplinaModel).filter(DisciplinaModel.descricao == disciplina_id).first()
            if disc:
                query_pg = query_pg.filter(QuestaoAssuntoModel.disciplina_id == disc.id)
    elif disciplina_ids_filtro:
        query_pg = query_pg.filter(QuestaoAssuntoModel.disciplina_id.in_(disciplina_ids_filtro))

    # Tentar encontrar uma questão válida de Ensino Médio
    MAX_TENTATIVAS = 100
    for _ in range(MAX_TENTATIVAS):
        registro_pg = query_pg.order_by(QuestaoAssuntoModel.id).first()

        if not registro_pg:
            raise HTTPException(
                status_code=404,
                detail="Nenhuma questão de baixa similaridade pendente com os filtros aplicados",
            )

        # Verificar nível no MySQL
        questao = (
            db.query(QuestaoModel)
            .options(
                joinedload(QuestaoModel.disciplina),
                joinedload(QuestaoModel.habilidade),
                joinedload(QuestaoModel.alternativas),
            )
            .filter(QuestaoModel.id == registro_pg.questao_id)
            .first()
        )

        if not questao or questao.ano_id != 3:
            ids_verificadas.add(registro_pg.questao_id)
            query_pg = query_pg.filter(QuestaoAssuntoModel.questao_id != registro_pg.questao_id)
            continue

        break
    else:
        raise HTTPException(status_code=404, detail="Não foram encontradas questões de baixa similaridade para verificar.")

    # Tratar enunciado
    enunciado_tratado, contem_imagem, motivo_erro = tratar_enunciado(questao.enunciado)

    # Módulos possíveis
    modulos = []
    hab_descricao = None
    if questao.habilidade_id:
        modulos_q = (
            pg_db.query(HabilidadeModuloModel)
            .filter(HabilidadeModuloModel.habilidade_id == questao.habilidade_id)
            .order_by(HabilidadeModuloModel.modulo)
            .all()
        )
        modulos = [HabilidadeModuloSchema.model_validate(m) for m in modulos_q]

        from ..database.models import HabilidadeModel
        hab = db.query(HabilidadeModel).filter(HabilidadeModel.id == questao.habilidade_id).first()
        if hab:
            hab_descricao = hab.descricao

        if not modulos and hab_descricao:
            from sqlalchemy import func as sqlfunc
            modulos_q = (
                pg_db.query(HabilidadeModuloModel)
                .filter(sqlfunc.lower(HabilidadeModuloModel.habilidade_descricao) == hab_descricao.lower())
                .order_by(HabilidadeModuloModel.modulo)
                .all()
            )
            modulos = [HabilidadeModuloSchema.model_validate(m) for m in modulos_q]

    # Alternativas
    alternativas = []
    if questao.tipo == "Múltipla Escolha" and questao.alternativas:
        for alt in sorted(questao.alternativas, key=lambda a: a.ordem or 0):
            conteudo_limpo, _, _ = tratar_enunciado(alt.conteudo or "")
            alternativas.append(
                AlternativaClassifSchema(
                    ordem=alt.ordem or 0,
                    conteudo=conteudo_limpo,
                    conteudo_html=alt.conteudo,
                    correta=bool(alt.correta),
                )
            )

    disc_nome = questao.disciplina.descricao if questao.disciplina else None

    return QuestaoClassifResponse(
        id=questao.id,
        questao_id=questao.questao_id,
        enunciado=enunciado_tratado or "",
        enunciado_html=questao.enunciado,
        disciplina_id=questao.disciplina_id,
        disciplina_nome=disc_nome,
        habilidade_id=questao.habilidade_id,
        habilidade_descricao=hab_descricao,
        tipo=questao.tipo,
        alternativas=alternativas,
        classificacao_extracao=registro_pg.classificacoes,
        classificacao_nao_enquadrada=registro_pg.classificacao_nao_enquadrada,
        similaridade=registro_pg.similaridade,
        tem_extracao=bool(registro_pg.classificacoes),
        modulos_possiveis=modulos,
    )


# ========================
# SALVAR CLASSIFICAÇÃO
# ========================

@router.post(
    "/salvar",
    response_model=SalvarClassificacaoResponse,
    summary="💾 Salvar classificação do usuário",
)
async def salvar_classificacao(
    request: SalvarClassificacaoRequest,
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Salva a decisão de classificação do usuário.
    Tipos de ação:
    - **classificacao_nova**: Questão que não tinha classificação
    - **confirmacao**: Usuário confirmou classificação existente
    - **correcao**: Usuário corrigiu classificação existente
    """
    if request.tipo_acao not in ("classificacao_nova", "confirmacao", "correcao"):
        raise HTTPException(status_code=400, detail="tipo_acao inválido")

    # Buscar habilidade_id da questão (Apenas o necessário)
    questao_data = db.query(QuestaoModel.id, QuestaoModel.habilidade_id, QuestaoModel.questao_id, QuestaoModel.disciplina_id).filter(QuestaoModel.id == request.questao_id).first()
    if not questao_data:
        raise HTTPException(status_code=404, detail="Questão não encontrada")

    # Buscar classificação da extração (se existir)
    extracao = (
        pg_db.query(QuestaoAssuntoModel)
        .filter(QuestaoAssuntoModel.questao_id == request.questao_id)
        .first()
    )

    # 1. Atualizar flag de classificação manual na tabela questao_assuntos
    if not extracao:
        # Buscar nome da disciplina se for criar
        from ..database.models import DisciplinaModel
        disc_nome = None
        if questao_data.disciplina_id:
            disc_row = db.query(DisciplinaModel.descricao).filter(DisciplinaModel.id == questao_data.disciplina_id).first()
            disc_nome = disc_row[0] if disc_row else None

        # Criar registro básico para marcar como manual
        extracao = QuestaoAssuntoModel(
            questao_id=questao_data.id,
            questao_id_str=questao_data.questao_id,
            disciplina_id=questao_data.disciplina_id,
            disciplina_nome=disc_nome,
            classificacoes=[],
            classificado_manualmente=True
        )
        pg_db.add(extracao)
    else:
        extracao.classificado_manualmente = True

    # Criar registro de histórico
    classificacao = ClassificacaoUsuarioModel(
        usuario_id=usuario.id,
        questao_id=request.questao_id,
        habilidade_id=questao_data.habilidade_id,
        modulo_escolhido=request.modulo_escolhido,
        classificacao_trieduc=request.classificacao_trieduc,
        descricao_assunto=request.descricao_assunto,
        habilidade_modulo_id=request.habilidade_modulo_id,
        classificacao_extracao=extracao.classificacoes if extracao else None,
        tipo_acao=request.tipo_acao,
        observacao=request.observacao,
    )
    pg_db.add(classificacao)
    pg_db.commit()

    logger.info(
        f"Classificação salva: usuario={usuario.nome}, questao={request.questao_id}, "
        f"acao={request.tipo_acao}, modulo={request.modulo_escolhido}"
    )

    return SalvarClassificacaoResponse(
        success=True,
        id=classificacao.id, # O ID já está disponível após o commit sem precisar de refresh
        questao_id=request.questao_id,
        tipo_acao=request.tipo_acao,
        message=f"Classificação ({request.tipo_acao}) salva com sucesso",
    )


# ========================
# ESTATÍSTICAS
# ========================

@router.get(
    "/stats",
    response_model=ClassificacaoStatsResponse,
    summary="📊 Estatísticas de classificação manual",
)
async def estatisticas_classificacao(
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna estatísticas do sistema de classificação manual."""
    total = pg_db.query(ClassificacaoUsuarioModel).count()
    novas = pg_db.query(ClassificacaoUsuarioModel).filter(
        ClassificacaoUsuarioModel.tipo_acao == "classificacao_nova"
    ).count()
    confirmacoes = pg_db.query(ClassificacaoUsuarioModel).filter(
        ClassificacaoUsuarioModel.tipo_acao == "confirmacao"
    ).count()
    correcoes = pg_db.query(ClassificacaoUsuarioModel).filter(
        ClassificacaoUsuarioModel.tipo_acao == "correcao"
    ).count()
    usuarios_ativos = pg_db.query(UsuarioModel).filter(UsuarioModel.ativo == True).count()

    # Total de questões únicas classificadas manualmente
    total_manuais = pg_db.query(func.count(func.distinct(ClassificacaoUsuarioModel.questao_id))).scalar()

    # Total pendente para Ensino Médio (Simplificado: as que não estão no PG como já classificadas)
    # Primeiro contamos as elegíveis no MySQL
    total_elegiveis = db.query(QuestaoModel).filter(
        QuestaoModel.habilidade_id.isnot(None),
        QuestaoModel.ano_id == 3
    ).count()

    # Agora as que já "saíram da fila" no PG
    processadas = pg_db.query(QuestaoAssuntoModel).filter(
        (QuestaoAssuntoModel.classificado_manualmente == True) |
        ((QuestaoAssuntoModel.classificacoes.isnot(None)) & (QuestaoAssuntoModel.classificacoes != [])) |
        (QuestaoAssuntoModel.precisa_verificar == True)
    ).count()

    total_pendentes = max(0, total_elegiveis - processadas)

    # Por usuário
    por_usuario_rows = (
        pg_db.query(
            UsuarioModel.nome,
            func.count(ClassificacaoUsuarioModel.id),
        )
        .join(UsuarioModel, UsuarioModel.id == ClassificacaoUsuarioModel.usuario_id)
        .group_by(UsuarioModel.nome)
        .all()
    )
    por_usuario = {row[0]: row[1] for row in por_usuario_rows}

    return ClassificacaoStatsResponse(
        total_classificacoes=total,
        classificacoes_novas=novas,
        confirmacoes=confirmacoes,
        correcoes=correcoes,
        usuarios_ativos=usuarios_ativos,
        total_manuais=total_manuais or 0,
        total_pendentes=total_pendentes,
        por_usuario=por_usuario,
    )


# ========================
# HISTÓRICO (para ML)
# ========================

@router.get(
    "/historico",
    response_model=HistoricoListResponse,
    summary="📋 Histórico de classificações (dados para ML)",
)
async def historico_classificacoes(
    page: int = Query(1, ge=1, description="Página"),
    per_page: int = Query(50, ge=1, le=200, description="Itens por página"),
    tipo_acao: Optional[str] = Query(None, description="Filtrar por tipo de ação"),
    usuario_id: Optional[int] = Query(None, description="Filtrar por usuário"),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna histórico paginado de todas as classificações feitas por usuários.
    Usado para exportação de dados de treino ML.
    """
    query = pg_db.query(ClassificacaoUsuarioModel)

    if tipo_acao:
        query = query.filter(ClassificacaoUsuarioModel.tipo_acao == tipo_acao)
    if usuario_id:
        query = query.filter(ClassificacaoUsuarioModel.usuario_id == usuario_id)

    total = query.count()
    pages = ceil(total / per_page) if total > 0 else 1
    offset = (page - 1) * per_page

    registros = query.order_by(ClassificacaoUsuarioModel.id).offset(offset).limit(per_page).all()

    # Buscar nomes dos usuários
    usuario_ids = {r.usuario_id for r in registros}
    if usuario_ids:
        users = pg_db.query(UsuarioModel).filter(UsuarioModel.id.in_(usuario_ids)).all()
        user_map = {u.id: u.nome for u in users}
    else:
        user_map = {}

    data = []
    for r in registros:
        data.append(
            ClassificacaoHistoricoSchema(
                id=r.id,
                usuario_id=r.usuario_id,
                usuario_nome=user_map.get(r.usuario_id),
                questao_id=r.questao_id,
                habilidade_id=r.habilidade_id,
                modulo_escolhido=r.modulo_escolhido,
                classificacao_trieduc=r.classificacao_trieduc,
                classificacao_extracao=r.classificacao_extracao,
                tipo_acao=r.tipo_acao,
                observacao=r.observacao,
                created_at=r.created_at,
            )
        )

    return HistoricoListResponse(
        data=data,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )
