"""Router com endpoints do sistema de classificação manual por usuários.

Rotas separadas do sistema de extração/conferência.
Protegidas por autenticação JWT.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from typing import Optional, List
from math import ceil
from datetime import datetime, timedelta, timezone
from loguru import logger

from jose import JWTError, jwt
import bcrypt

from ..config import settings
from ..database import get_db, get_pg_db
from ..database.models import QuestaoModel, HabilidadeModel, DisciplinaModel
from ..database.pg_models import QuestaoAssuntoModel
from ..database.pg_modulo_models import HabilidadeModuloModel
from ..database.pg_usuario_models import UsuarioModel, ClassificacaoUsuarioModel
from ..database.pg_pular_models import QuestaoPuladaModel
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
    ClassificacaoManualResumoSchema,
    QuestaoClassifResponse,
    SalvarClassificacaoRequest,
    SalvarClassificacaoResponse,
    PularQuestaoRequest,
    PularQuestaoResponse,
    ClassificacaoStatsResponse,
    ClassificacaoHistoricoSchema,
    HistoricoListResponse,
)

import time

# ========================
# CACHE EM MEMÓRIA (TTL)
# ========================
_api_cache = {}

def get_from_cache(key: str, ttl: int = 300):
    if key in _api_cache:
        val, ts = _api_cache[key]
        if time.time() - ts < ttl:
            return val
    return None

def set_to_cache(key: str, val):
    _api_cache[key] = (val, time.time())

# ========================
# CONFIG
# ========================
SECRET_KEY = settings.jwt_secret_key
ALGORITHM = settings.jwt_algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = settings.jwt_expire_minutes

# bcrypt 5.x — usar diretamente (passlib não compatível)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/classificacao/login")

router = APIRouter(prefix="/classificacao", tags=["Classificação Manual"])

# Disciplinas válidas para cadastro (incluindo Áreas)
DISCIPLINAS_VALIDAS = [
    "Artes", "Biologia", "Ciências", "Educação Física", "Espanhol",
    "Filosofia", "Física", "Geografia", "História", "Língua Inglesa",
    "Língua Portuguesa", "Literatura", "Matemática", "Natureza e Sociedade", 
    "Química", "Redação", "Sociologia",
    # Áreas
    "Humanas", "Linguagens", "Natureza"
]

# Mapeamento para o MySQL (onde os nomes podem ser diferentes do Postgres/Planilha)
MAP_DISCIPLINAS_MYSQL = {
    "Artes": "Artes",
    "Língua Inglesa": "Língua Inglesa",
    "Língua Portuguesa": "Língua Portuguesa",
    "Literatura": None, # Não existe no MySQL
    "Redação": None,    # Não existe no MySQL
}

# Mapeamento de áreas para filtro
AREAS_DISCIPLINAS = {
    "Humanas": ["Filosofia", "Geografia", "História", "Sociologia"],
    "Linguagens": ["Artes", "Educação Física", "Espanhol", "Língua Inglesa", "Língua Portuguesa", "Literatura", "Redação"],
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
    db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna a lista de assuntos únicos (habilidade_id + habilidade_descricao)
    para popular o dropdown de filtros no frontend.
    Inclui quantidade de pendentes (cacheado por 5m).
    """
    cache_key = f"habilidades_filtro_{area}_{disciplina}_{usuario.id}"
    cached_data = get_from_cache(cache_key, ttl=300)
    if cached_data:
        return cached_data

    query = pg_db.query(
        HabilidadeModuloModel.habilidade_id,
        HabilidadeModuloModel.habilidade_descricao
    ).distinct()

    if area:
        query = query.filter(HabilidadeModuloModel.area == area)
    if disciplina:
        mapping = {
            "Artes": ["Artes", "Arte"],
            "Língua Inglesa": ["Língua Inglesa", "Inglês"],
            "Língua Portuguesa": ["Língua Portuguesa", "Lingua Portuguesa", "Literatura", "Redação"],
        }
        mapped_names = mapping.get(disciplina, [disciplina])
        query = query.filter(HabilidadeModuloModel.disciplina.in_(mapped_names))

    results = query.order_by(HabilidadeModuloModel.habilidade_descricao).all()
    
    # Montar mapa de habilidades válidas
    hab_ids = [r.habilidade_id for r in results if r.habilidade_id is not None]
    
    counts_map = {}
    if hab_ids:
        # —————————————————————————————————————————————————————————————
        # ABORDAGEM EFICIENTE v2: dois GROUP BY no MySQL (sem carregar
        # linhas individuais) + IDs excluídos do PG como set
        #
        # 1. MySQL  → SELECT habilidade_id, COUNT(*) GROUP BY  (29 linhas)
        # 2. PG     → 3 queries para obter IDs excluídos como set
        # 3. MySQL  → SELECT habilidade_id, COUNT(*) WHERE id IN(excluídos)
        #             GROUP BY  (≤ 29 linhas agrupadas)
        # 4. Python → total - excluído por habilidade
        # —————————————————————————————————————————————————————————————

        # Etapa 1: total de questões por habilidade (MySQL GROUP BY, 29 linhas)
        rows_total = db.query(
            QuestaoModel.habilidade_id,
            func.count(QuestaoModel.id).label("total"),
        ).filter(
            QuestaoModel.habilidade_id.in_(hab_ids),
            QuestaoModel.ano_id == 3,
        ).group_by(QuestaoModel.habilidade_id).all()

        if not rows_total:
            habilidades = []
            res = HabilidadesFiltroResponse(habilidades=habilidades, total=0)
            set_to_cache(cache_key, res)
            return res

        total_por_hab: dict[int, int] = {r[0]: r[1] for r in rows_total}

        # Etapa 2: IDs excluídos no PG (queries leves, sem IN gigante)
        ids_excluir: set[int] = set()

        # 2a. Já classificadas manualmente, com low-match, ou pelo SuperPro
        for r in pg_db.query(QuestaoAssuntoModel.questao_id).filter(
            (QuestaoAssuntoModel.classificado_manualmente == True) |
            (
                (QuestaoAssuntoModel.classificacao_nao_enquadrada.isnot(None)) &
                (func.json_length(QuestaoAssuntoModel.classificacao_nao_enquadrada) > 0)
            ) |
            (
                (QuestaoAssuntoModel.extracao_feita == True) &
                (QuestaoAssuntoModel.classificacoes.isnot(None)) &
                (func.json_length(QuestaoAssuntoModel.classificacoes) > 0)
            )
        ).all():
            ids_excluir.add(r[0])

        # 2b. Já classificadas por este usuário
        for r in pg_db.query(ClassificacaoUsuarioModel.questao_id).filter(
            ClassificacaoUsuarioModel.usuario_id == usuario.id
        ).all():
            ids_excluir.add(r[0])

        # 2c. Puladas por qualquer usuário — só aparecem em Pendentes, nunca em /proxima
        for r in pg_db.query(QuestaoPuladaModel.questao_id).all():
            ids_excluir.add(r[0])

        # Etapa 3: contagem de excluídas por habilidade no MySQL
        # (IN por PK é eficiente; resposta = ≤ 29 linhas agrupadas)
        excluido_por_hab: dict[int, int] = {}
        if ids_excluir:
            rows_excluido = db.query(
                QuestaoModel.habilidade_id,
                func.count(QuestaoModel.id).label("excluidos"),
            ).filter(
                QuestaoModel.id.in_(list(ids_excluir)),
                QuestaoModel.habilidade_id.in_(hab_ids),
                QuestaoModel.ano_id == 3,
            ).group_by(QuestaoModel.habilidade_id).all()
            excluido_por_hab = {r[0]: r[1] for r in rows_excluido}

        # Etapa 4: calcular pendentes (Python puro, O(n) em hab_ids)
        for hab_id, total in total_por_hab.items():
            excluidos = excluido_por_hab.get(hab_id, 0)
            pendentes = total - excluidos
            if pendentes > 0:
                counts_map[hab_id] = pendentes

    habilidades = []
    for r in results:
        if r.habilidade_id is not None:
            pendentes = counts_map.get(r.habilidade_id, 0)
            if pendentes > 0:
                habilidades.append(HabilidadeFiltroSchema(
                    habilidade_id=r.habilidade_id,
                    habilidade_descricao=r.habilidade_descricao,
                    pendentes=pendentes
                ))

    res = HabilidadesFiltroResponse(habilidades=habilidades, total=len(habilidades))
    set_to_cache(cache_key, res)
    return res


# ========================
# HABILIDADES PENDENTES (filtro da aba Pendentes)
# ========================

@router.get(
    "/habilidades-pendentes",
    response_model=HabilidadesFiltroResponse,
    summary="🔍 Assuntos com questões pendentes (puladas)",
)
async def listar_habilidades_pendentes(
    area: Optional[str] = Query(None, description="Filtrar por área"),
    disciplina: Optional[str] = Query(None, description="Filtrar por nome da disciplina"),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna apenas os assuntos (habilidades) que possuem questões puladas (pendentes),
    com a contagem de quantas existem. Respeita o filtro de área/disciplina do usuário.
    """
    effective_area = area or (usuario.disciplina if not usuario.is_admin else None)

    # IDs já classificados por este usuário (excluir das contagens)
    ids_classificadas: set[int] = {
        row[0] for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.usuario_id == usuario.id)
        .all()
    }

    # Base: questões puladas com habilidade definida
    query_puladas = pg_db.query(
        QuestaoPuladaModel.habilidade_id,
        func.count(func.distinct(QuestaoPuladaModel.questao_id)).label("total"),
    ).filter(QuestaoPuladaModel.habilidade_id.isnot(None))

    if ids_classificadas:
        query_puladas = query_puladas.filter(
            ~QuestaoPuladaModel.questao_id.in_(list(ids_classificadas))
        )

    # Filtro de disciplina explícito
    if disciplina:
        mysql_name = MAP_DISCIPLINAS_MYSQL.get(disciplina, disciplina)
        if mysql_name:
            disc_id_row = db.query(DisciplinaModel.id).filter(DisciplinaModel.descricao == mysql_name).first()
            if disc_id_row:
                query_puladas = query_puladas.filter(QuestaoPuladaModel.disciplina_id == disc_id_row[0])
            else:
                return HabilidadesFiltroResponse(habilidades=[], total=0)
        else:
            habilidade_ids_custom = [
                row[0] for row in pg_db.query(HabilidadeModuloModel.habilidade_id)
                .filter(HabilidadeModuloModel.disciplina == disciplina)
                .filter(HabilidadeModuloModel.habilidade_id.isnot(None))
                .distinct().all()
            ]
            if habilidade_ids_custom:
                query_puladas = query_puladas.filter(QuestaoPuladaModel.habilidade_id.in_(habilidade_ids_custom))
            else:
                return HabilidadesFiltroResponse(habilidades=[], total=0)
    elif effective_area and effective_area in AREAS_DISCIPLINAS:
        nomes = AREAS_DISCIPLINAS[effective_area]
        discs_ids = [d[0] for d in db.query(DisciplinaModel.id).filter(DisciplinaModel.descricao.in_(nomes)).all()]
        if discs_ids:
            query_puladas = query_puladas.filter(QuestaoPuladaModel.disciplina_id.in_(discs_ids))
    elif effective_area:
        query_puladas = query_puladas.filter(QuestaoPuladaModel.area == effective_area)

    rows = query_puladas.group_by(QuestaoPuladaModel.habilidade_id).all()
    if not rows:
        return HabilidadesFiltroResponse(habilidades=[], total=0)

    counts: dict[int, int] = {r[0]: r[1] for r in rows}
    hab_ids = list(counts.keys())

    # Buscar descrições no habilidade_modulos
    desc_rows = (
        pg_db.query(HabilidadeModuloModel.habilidade_id, HabilidadeModuloModel.habilidade_descricao)
        .filter(HabilidadeModuloModel.habilidade_id.in_(hab_ids))
        .distinct()
        .all()
    )
    seen: set[int] = set()
    habilidades = []
    for r in sorted(desc_rows, key=lambda x: x.habilidade_descricao):
        if r.habilidade_id not in seen:
            seen.add(r.habilidade_id)
            habilidades.append(HabilidadeFiltroSchema(
                habilidade_id=r.habilidade_id,
                habilidade_descricao=r.habilidade_descricao,
                pendentes=counts[r.habilidade_id],
            ))

    return HabilidadesFiltroResponse(habilidades=habilidades, total=len(habilidades))


# ========================
# MÓDULOS (consulta)
# ========================

@router.get(
    "/modulos",
    response_model=List[HabilidadeModuloSchema],
    summary="📦 Todos os módulos disponíveis para seleção manual",
)
async def listar_todos_modulos(
    disciplina: Optional[str] = Query(None, description="Filtrar por nome da disciplina"),
    area: Optional[str] = Query(None, description="Filtrar por área"),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna todos os módulos do TriEduc, opcionalmente filtrados por disciplina ou área.
    Usado no modal de correção de classificação para permitir busca livre por qualquer módulo.
    """
    cache_key = f"todos_modulos_{area}_{disciplina}"
    cached = get_from_cache(cache_key, ttl=600)
    if cached is not None:
        return cached

    query = pg_db.query(HabilidadeModuloModel)
    if area:
        query = query.filter(HabilidadeModuloModel.area == area)
    if disciplina:
        query = query.filter(HabilidadeModuloModel.disciplina == disciplina)

    modulos = (
        query
        .order_by(
            HabilidadeModuloModel.area,
            HabilidadeModuloModel.disciplina,
            HabilidadeModuloModel.modulo,
            HabilidadeModuloModel.descricao,
        )
        .all()
    )
    result = [HabilidadeModuloSchema.model_validate(m) for m in modulos]
    set_to_cache(cache_key, result)
    return result


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

    logger.info(f"Busca Próxima: usuario={usuario.nome}, area={area}, disciplina={disciplina_id}, habilidade={habilidade_id}")

    # Resolver filtro de área → disciplinas (Otimizado: apenas IDs)
    disciplina_ids_filtro = None
    if area and area in AREAS_DISCIPLINAS:
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
            # Tentar mapeamento para MySQL
            mysql_name = MAP_DISCIPLINAS_MYSQL.get(disciplina_id, disciplina_id)
            
            if mysql_name:
                disc_id_row = db.query(DisciplinaModel.id).filter(DisciplinaModel.descricao == mysql_name).first()
                if disc_id_row:
                    candidate_query = candidate_query.filter(QuestaoModel.disciplina_id == disc_id_row[0])
                else:
                    # Se nome exato não existe no MySQL, falhar para não mostrar tudo
                    candidate_query = candidate_query.filter(QuestaoModel.id == -1)
            else:
                # Disciplina Virtual (Literatura/Redação): Buscar IDs de habilidade no Postgres
                habilidade_ids_custom = [
                    row[0] for row in pg_db.query(HabilidadeModuloModel.habilidade_id)
                    .filter(HabilidadeModuloModel.disciplina == disciplina_id)
                    .filter(HabilidadeModuloModel.habilidade_id.isnot(None))
                    .distinct()
                    .all()
                ]
                if habilidade_ids_custom:
                    candidate_query = candidate_query.filter(QuestaoModel.habilidade_id.in_(habilidade_ids_custom))
                else:
                    candidate_query = candidate_query.filter(QuestaoModel.id == -1)
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
        
        # 2. Already has any classification: manual, low-match, or SuperPro.
        # Questões que já têm qualquer tipo de classificação não devem aparecer para classificar.
        classified_in_system = {
            row[0] for row in pg_db.query(QuestaoAssuntoModel.questao_id)
            .filter(QuestaoAssuntoModel.questao_id.in_(candidate_ids))
            .filter(
                (QuestaoAssuntoModel.classificado_manualmente == True) |
                ((QuestaoAssuntoModel.classificacao_nao_enquadrada.isnot(None)) & (func.json_length(QuestaoAssuntoModel.classificacao_nao_enquadrada) > 0)) |
                ((QuestaoAssuntoModel.extracao_feita == True) & (QuestaoAssuntoModel.classificacoes.isnot(None)) & (func.json_length(QuestaoAssuntoModel.classificacoes) > 0))
            )
            .all()
        }

        # 3. Puladas por qualquer usuário — só aparecem na aba Pendentes, nunca em /proxima
        skipped_any_user = {
            row[0] for row in pg_db.query(QuestaoPuladaModel.questao_id)
            .filter(QuestaoPuladaModel.questao_id.in_(candidate_ids))
            .all()
        }

        ids_excluir = classified_by_user.union(classified_in_system).union(skipped_any_user)
        
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
    
    texto_base_tratado = None
    if questao.texto_base:
        texto_base_tratado, _, _ = tratar_enunciado(questao.texto_base)

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
        texto_base=texto_base_tratado,
        texto_base_html=questao.texto_base,
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
    "/consulta/{questao_id}",
    response_model=QuestaoClassifResponse,
    summary="Consultar questão por ID (admin)",
)
async def consultar_questao_por_id(
    questao_id: int,
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna uma questão específica no mesmo formato da rota /proxima."""
    if not usuario.is_admin:
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores")

    questao = (
        db.query(QuestaoModel)
        .options(
            joinedload(QuestaoModel.disciplina),
            joinedload(QuestaoModel.habilidade),
            joinedload(QuestaoModel.alternativas),
        )
        .filter(QuestaoModel.id == questao_id)
        .first()
    )
    if not questao:
        raise HTTPException(status_code=404, detail="Questão não encontrada")

    enunciado_tratado, contem_imagem, motivo_erro = tratar_enunciado(questao.enunciado)

    texto_base_tratado = None
    if questao.texto_base:
        texto_base_tratado, _, _ = tratar_enunciado(questao.texto_base)

    extracao = (
        pg_db.query(QuestaoAssuntoModel)
        .filter(QuestaoAssuntoModel.questao_id == questao.id)
        .first()
    )

    classificacao_manual = (
        pg_db.query(ClassificacaoUsuarioModel)
        .filter(
            ClassificacaoUsuarioModel.questao_id == questao.id,
            ClassificacaoUsuarioModel.usuario_id != 0,
        )
        .order_by(ClassificacaoUsuarioModel.created_at.desc(), ClassificacaoUsuarioModel.id.desc())
        .first()
    )

    hab_descricao = None
    if questao.habilidade:
        hab_descricao = questao.habilidade.descricao

    modulos = []
    if questao.habilidade_id:
        modulos_q = (
            pg_db.query(HabilidadeModuloModel)
            .filter(HabilidadeModuloModel.habilidade_id == questao.habilidade_id)
            .order_by(HabilidadeModuloModel.modulo)
            .all()
        )
        modulos = [HabilidadeModuloSchema.model_validate(m) for m in modulos_q]

        if not modulos and hab_descricao:
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

    manual_payload = None
    if classificacao_manual:
        manual_modulos = classificacao_manual.modulos_escolhidos or (
            [classificacao_manual.modulo_escolhido] if classificacao_manual.modulo_escolhido else []
        )
        manual_descricoes = classificacao_manual.descricoes_assunto_list or (
            [classificacao_manual.descricao_assunto] if classificacao_manual.descricao_assunto else []
        )
        manual_payload = ClassificacaoManualResumoSchema(
            usuario_id=classificacao_manual.usuario_id,
            tipo_acao=classificacao_manual.tipo_acao,
            modulos=[m for m in manual_modulos if m],
            descricoes=[d for d in manual_descricoes if d],
            observacao=classificacao_manual.observacao,
            created_at=classificacao_manual.created_at,
        )

    return QuestaoClassifResponse(
        id=questao.id,
        questao_id=questao.questao_id,
        enunciado=enunciado_tratado or "",
        enunciado_html=questao.enunciado,
        texto_base=texto_base_tratado,
        texto_base_html=questao.texto_base,
        disciplina_id=questao.disciplina_id,
        disciplina_nome=disc_nome,
        habilidade_id=questao.habilidade_id,
        habilidade_descricao=hab_descricao,
        tipo=questao.tipo,
        alternativas=alternativas,
        classificacao_extracao=extracao.classificacoes if extracao and extracao.extracao_feita else None,
        classificacao_nao_enquadrada=extracao.classificacao_nao_enquadrada if extracao and extracao.classificacao_nao_enquadrada else None,
        similaridade=extracao.similaridade if extracao else None,
        tem_extracao=bool(extracao and extracao.extracao_feita and extracao.classificacoes),
        classificacao_manual=manual_payload,
        modulos_possiveis=modulos,
    )


@router.get(
    "/proxima-verificar",
    response_model=QuestaoClassifResponse,
    summary="🔄 Próxima questão para verificar (já classificada)",
)
async def proxima_questao_verificar(
    area: Optional[str] = Query(None, description="Filtrar por área"),
    disciplina_id: Optional[str] = Query(None, description="ID ou Nome da disciplina"),
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

    # Query Base no PG: extraídas pelo Superpro com baixa similaridade (precisa verificação humana)
    query_pg = pg_db.query(QuestaoAssuntoModel).filter(
        QuestaoAssuntoModel.extracao_feita == True,
        QuestaoAssuntoModel.classificacoes.isnot(None),
        QuestaoAssuntoModel.similaridade > 0,
        QuestaoAssuntoModel.similaridade < 0.8,
    )

    if habilidade_id:
        query_pg = query_pg.filter(QuestaoAssuntoModel.habilidade_id == habilidade_id)

    if ids_verificadas:
        query_pg = query_pg.filter(~QuestaoAssuntoModel.questao_id.in_(ids_verificadas))

    if disciplina_id:
        if str(disciplina_id).isdigit():
            query_pg = query_pg.filter(QuestaoAssuntoModel.disciplina_id == int(disciplina_id))
        else:
            # Tentar mapeamento para MySQL
            mysql_name = MAP_DISCIPLINAS_MYSQL.get(disciplina_id, disciplina_id)
            
            disc_target_id = None
            if mysql_name:
                disc = db.query(DisciplinaModel).filter(DisciplinaModel.descricao == mysql_name).first()
                if disc:
                    disc_target_id = disc.id
            
            if disc_target_id:
                query_pg = query_pg.filter(QuestaoAssuntoModel.disciplina_id == disc_target_id)
            else:
                # Disciplina Virtual (Literatura/Redação): Buscar IDs de habilidade no Postgres
                habilidade_ids_custom = [
                    row[0] for row in pg_db.query(HabilidadeModuloModel.habilidade_id)
                    .filter(HabilidadeModuloModel.disciplina == disciplina_id)
                    .filter(HabilidadeModuloModel.habilidade_id.isnot(None))
                    .distinct()
                    .all()
                ]
                if habilidade_ids_custom:
                    # No QuestaoAssuntoModel, habilidade_id pode não estar preenchido se veio do scraping
                    # Mas se tivermos o ID TRIEDUC no MySQL (QuestaoModel), podemos filtrar lá.
                    # No entanto, a query base é sobre QuestaoAssuntoModel. 
                    # Se salvamos a extração, populamos habilidade_id? Geralmente sim.
                    query_pg = query_pg.filter(QuestaoAssuntoModel.habilidade_id.in_(habilidade_ids_custom))
                else:
                    query_pg = query_pg.filter(QuestaoAssuntoModel.id == -1)
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
    
    texto_base_tratado = None
    if questao.texto_base:
        texto_base_tratado, _, _ = tratar_enunciado(questao.texto_base)

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
        texto_base=texto_base_tratado,
        texto_base_html=questao.texto_base,
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
        func.json_length(QuestaoAssuntoModel.classificacao_nao_enquadrada) > 0,
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
    
    texto_base_tratado = None
    if questao.texto_base:
        texto_base_tratado, _, _ = tratar_enunciado(questao.texto_base)

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
        texto_base=texto_base_tratado,
        texto_base_html=questao.texto_base,
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
        # Campos legados (single) - retrocompatibilidade
        modulo_escolhido=request.modulo_escolhido,
        classificacao_trieduc=request.classificacao_trieduc,
        descricao_assunto=request.descricao_assunto,
        habilidade_modulo_id=request.habilidade_modulo_id,
        # Campos novos (múltiplos módulos JSONB)
        modulos_escolhidos=request.modulos_escolhidos,
        classificacoes_trieduc_list=request.classificacoes_trieduc,
        descricoes_assunto_list=request.descricoes_assunto,
        habilidade_modulo_ids=request.habilidade_modulo_ids,
        # Extração e metadados
        classificacao_extracao=extracao.classificacoes if extracao else None,
        tipo_acao=request.tipo_acao,
        observacao=request.observacao,
    )
    pg_db.add(classificacao)

    # Auto-remover da lista de questões puladas (se existir para qualquer usuário)
    # Motivo: Se foi classificada, não está mais pendente para ninguém.
    pg_db.query(QuestaoPuladaModel).filter(
        QuestaoPuladaModel.questao_id == request.questao_id,
    ).delete()

    pg_db.commit()

    modulos_info = request.modulos_escolhidos or [request.modulo_escolhido] if request.modulo_escolhido else []
    logger.info(
        f"Classificação salva: usuario={usuario.nome}, questao={request.questao_id}, "
        f"acao={request.tipo_acao}, modulos={modulos_info}"
    )

    return SalvarClassificacaoResponse(
        success=True,
        id=classificacao.id,
        questao_id=request.questao_id,
        tipo_acao=request.tipo_acao,
        message=f"Classificação ({request.tipo_acao}) salva com sucesso",
    )


# ========================
# PULAR QUESTÃO (PENDENTES)
# ========================

@router.post(
    "/pular",
    response_model=PularQuestaoResponse,
    summary="⏭️ Pular questão (marcar como pendente)",
)
async def pular_questao(
    request: PularQuestaoRequest,
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Marca uma questão como pulada pelo usuário.
    A questão aparecerá na aba 'Pendentes' para classificação posterior.
    """
    # Verificar se a questão existe
    questao_data = db.query(
        QuestaoModel.id, QuestaoModel.disciplina_id, QuestaoModel.habilidade_id
    ).filter(QuestaoModel.id == request.questao_id).first()

    if not questao_data:
        raise HTTPException(status_code=404, detail="Questão não encontrada")

    # Verificar se já foi pulada (evitar duplicata)
    existente = pg_db.query(QuestaoPuladaModel).filter(
        QuestaoPuladaModel.usuario_id == usuario.id,
        QuestaoPuladaModel.questao_id == request.questao_id,
    ).first()

    if existente:
        return PularQuestaoResponse(
            success=True,
            message="Questão já estava marcada como pendente",
        )

    # Registrar como pulada
    pulada = QuestaoPuladaModel(
        usuario_id=usuario.id,
        questao_id=request.questao_id,
        area=usuario.disciplina,
        disciplina_id=questao_data.disciplina_id,
        habilidade_id=questao_data.habilidade_id,
    )
    pg_db.add(pulada)
    pg_db.commit()

    logger.info(f"Questão pulada: usuario={usuario.nome}, questao={request.questao_id}")

    return PularQuestaoResponse(
        success=True,
        message="Questão marcada como pendente",
    )


@router.get(
    "/proxima-pendente",
    response_model=QuestaoClassifResponse,
    summary="📋 Próxima questão pendente (pulada)",
)
async def proxima_questao_pendente(
    area: Optional[str] = Query(None, description="Filtrar por área"),
    disciplina_id: Optional[str] = Query(None, description="ID ou Nome da disciplina"),
    habilidade_id: Optional[int] = Query(None, description="ID da habilidade TRIEDUC"),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna a próxima questão pendente (pulada por qualquer usuário).
    Restrito à área/disciplina do usuário por padrão.
    """
    # Base query: questões puladas por qualquer usuário
    query_puladas = pg_db.query(QuestaoPuladaModel)

    # Área efetiva: usa o filtro explícito, ou cai na disciplina do próprio usuário (não-admin)
    effective_area = area or (usuario.disciplina if not usuario.is_admin else None)

    # Aplicar filtros
    if habilidade_id:
        query_puladas = query_puladas.filter(QuestaoPuladaModel.habilidade_id == habilidade_id)

    if disciplina_id:
        if str(disciplina_id).isdigit():
            query_puladas = query_puladas.filter(QuestaoPuladaModel.disciplina_id == int(disciplina_id))
        else:
            # Tentar mapeamento para MySQL
            mysql_name = MAP_DISCIPLINAS_MYSQL.get(disciplina_id, disciplina_id)
            
            if mysql_name:
                disc_id_row = db.query(DisciplinaModel.id).filter(DisciplinaModel.descricao == mysql_name).first()
                if disc_id_row:
                    query_puladas = query_puladas.filter(QuestaoPuladaModel.disciplina_id == disc_id_row[0])
                else:
                    # Se não existe no MySQL, falhar filtro
                    query_puladas = query_puladas.filter(QuestaoPuladaModel.id == -1)
            else:
                # Disciplina Virtual (Literatura/Redação): Buscar IDs de habilidade no Postgres
                habilidade_ids_custom = [
                    row[0] for row in pg_db.query(HabilidadeModuloModel.habilidade_id)
                    .filter(HabilidadeModuloModel.disciplina == disciplina_id)
                    .filter(HabilidadeModuloModel.habilidade_id.isnot(None))
                    .distinct()
                    .all()
                ]
                if habilidade_ids_custom:
                    query_puladas = query_puladas.filter(QuestaoPuladaModel.habilidade_id.in_(habilidade_ids_custom))
                else:
                    query_puladas = query_puladas.filter(QuestaoPuladaModel.id == -1)
    elif effective_area and effective_area in AREAS_DISCIPLINAS:
        nomes = AREAS_DISCIPLINAS[effective_area]
        discs_ids = db.query(DisciplinaModel.id).filter(DisciplinaModel.descricao.in_(nomes)).all()
        disciplina_ids_filtro = [d[0] for d in discs_ids]
        if disciplina_ids_filtro:
            query_puladas = query_puladas.filter(QuestaoPuladaModel.disciplina_id.in_(disciplina_ids_filtro))
    elif effective_area:
        # Fallback: filtrar diretamente pelo campo area salvo no momento do pulo
        query_puladas = query_puladas.filter(QuestaoPuladaModel.area == effective_area)

    # LOG para depuração
    logger.info(f"Filtro Pendentes: usuario={usuario.nome}, effective_area={effective_area}, disciplina={disciplina_id}")
    count_antes = query_puladas.count()
    logger.info(f"Total pendentes com filtros aplicados: {count_antes}")

    # IDs já classificadas por este usuário (excluir das pendentes)
    ids_classificadas = {
        row[0] for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.usuario_id == usuario.id)
        .all()
    }

    if ids_classificadas:
        query_puladas = query_puladas.filter(~QuestaoPuladaModel.questao_id.in_(ids_classificadas))

    # Buscar próxima pendente (ordem de inserção)
    registro_pulado = query_puladas.order_by(QuestaoPuladaModel.id).first()

    if not registro_pulado:
        raise HTTPException(
            status_code=404,
            detail="Nenhuma questão pendente encontrada com os filtros aplicados.",
        )

    # Carregar detalhes completos da questão do MySQL
    questao = (
        db.query(QuestaoModel)
        .options(
            joinedload(QuestaoModel.disciplina),
            joinedload(QuestaoModel.habilidade),
            joinedload(QuestaoModel.alternativas),
        )
        .filter(QuestaoModel.id == registro_pulado.questao_id)
        .first()
    )

    if not questao:
        # Questão não existe mais no MySQL, remover da lista de puladas
        pg_db.delete(registro_pulado)
        pg_db.commit()
        raise HTTPException(status_code=404, detail="Questão pendente não encontrada no banco de dados.")

    # Tratar enunciado
    enunciado_tratado, contem_imagem, motivo_erro = tratar_enunciado(questao.enunciado)
    
    texto_base_tratado = None
    if questao.texto_base:
        texto_base_tratado, _, _ = tratar_enunciado(questao.texto_base)

    # Verificar classificação existente
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
        modulos_q = (
            pg_db.query(HabilidadeModuloModel)
            .filter(HabilidadeModuloModel.habilidade_id == questao.habilidade_id)
            .order_by(HabilidadeModuloModel.modulo)
            .all()
        )
        modulos = [HabilidadeModuloSchema.model_validate(m) for m in modulos_q]

        # FALLBACK por descrição
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
        texto_base=texto_base_tratado,
        texto_base_html=questao.texto_base,
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
    """Retorna estatísticas do sistema de classificação manual (Cache 5m)."""
    cache_key = "estatisticas_gerais"
    cached_data = get_from_cache(cache_key, ttl=300)
    if cached_data:
        return cached_data

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

    # Filtro Base: Ensino Médio + Habilidade ID
    # Join com DisciplinaModel para garantir integridade (opcional mas mantido para consistência)
    from ..database.models import DisciplinaModel
    
    em_query = db.query(QuestaoModel.id).filter(
        QuestaoModel.ano_id == 3,
        QuestaoModel.habilidade_id.isnot(None)
    )
    em_ids = [r[0] for r in em_query.all()]
    total_sistema = len(em_ids)

    if not em_ids:
        res = ClassificacaoStatsResponse(total_sistema=0, por_usuario={}, por_disciplina={})
        set_to_cache(cache_key, res)
        return res

    # 1. Manual (Prioridade Máxima)
    manuais_ids = {r[0] for r in pg_db.query(ClassificacaoUsuarioModel.questao_id)\
                   .filter(ClassificacaoUsuarioModel.questao_id.in_(em_ids)).all()}
    total_manuais = len(manuais_ids)

    # 2. Automáticas (Match >= 80% e que não foram tocadas manualmente)
    auto_query = pg_db.query(QuestaoAssuntoModel.questao_id).filter(
        QuestaoAssuntoModel.questao_id.in_(em_ids),
        QuestaoAssuntoModel.similaridade >= 0.8
    ).all()
    auto_ids = {r[0] for r in auto_query} - manuais_ids
    total_auto_superpro = len(auto_ids)

    # 3. Faltam Verificar (Match < 80% e que não foram tocadas nem são automáticas)
    verificar_query = pg_db.query(QuestaoAssuntoModel.questao_id).filter(
        QuestaoAssuntoModel.questao_id.in_(em_ids),
        QuestaoAssuntoModel.similaridade < 0.8,
        QuestaoAssuntoModel.similaridade > 0
    ).all()
    verificar_ids = {r[0] for r in verificar_query} - manuais_ids - auto_ids
    total_precisa_verificar = len(verificar_ids)

    # 4. Puladas (Volume que não está em nenhum dos estados acima)
    from ..database.pg_pular_models import QuestaoPuladaModel
    puladas_query = pg_db.query(QuestaoPuladaModel.questao_id).filter(
        QuestaoPuladaModel.questao_id.in_(em_ids)
    ).all()
    # Para a matemática do Pendentes, usamos apenas as que não foram classificadas de outra forma
    all_puladas_ids = {r[0] for r in puladas_query}
    puladas_ids_disjoint = all_puladas_ids - manuais_ids - auto_ids - verificar_ids
    total_puladas = len(puladas_ids_disjoint) # Agora usamos apenas as exclusivas para não estourar a soma do Total

    # 5. Pendentes (O resto matemático restrito)
    # A soma de (manuais + auto + verificar + puladas + pendentes) = total_sistema
    total_pendentes = max(0, total_sistema - total_manuais - total_auto_superpro - total_precisa_verificar - total_puladas)

    # Por disciplina (Dashboard style)
    mysql_rows = db.query(QuestaoModel.disciplina_id, func.count(QuestaoModel.id))\
        .filter(QuestaoModel.ano_id == 3, QuestaoModel.habilidade_id.isnot(None))\
        .group_by(QuestaoModel.disciplina_id).all()
    mysql_counts = {r[0]: r[1] for r in mysql_rows}
    
    # Contabilizamos como "feitas" para o progresso: Manual + Automática (Finalizadas)
    ids_finalizados = manuais_ids | auto_ids
    pg_rows = pg_db.query(QuestaoAssuntoModel.disciplina_id, func.count(QuestaoAssuntoModel.questao_id))\
        .filter(QuestaoAssuntoModel.questao_id.in_(ids_finalizados))\
        .group_by(QuestaoAssuntoModel.disciplina_id).all()
    pg_counts = {r[0]: r[1] for r in pg_rows}
    
    disc_names = {d.id: d.descricao for d in db.query(DisciplinaModel).all()}
    
    por_disciplina = {}
    for d_id, total_mysql in mysql_counts.items():
        if d_id is None: continue
        nome = disc_names.get(d_id, f"ID {d_id}")
        feitas = pg_counts.get(d_id, 0)
        por_disciplina[nome] = {
            "total": total_mysql,
            "feitas": feitas,
            "faltam": max(0, total_mysql - feitas)
        }

    # Por usuário (Atividades Recentes)
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

    res = ClassificacaoStatsResponse(
        total_classificacoes=total_sistema,
        classificacoes_novas=novas,
        confirmacoes=confirmacoes,
        correcoes=correcoes,
        usuarios_ativos=usuarios_ativos,
        total_manuais=total_manuais,
        total_pendentes=total_pendentes,
        total_sistema=total_sistema,
        total_precisa_verificar=total_precisa_verificar,
        total_auto_superpro=total_auto_superpro,
        total_puladas=total_puladas,
        por_disciplina=por_disciplina,
        por_usuario=por_usuario,
    )
    set_to_cache(cache_key, res)
    return res


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
