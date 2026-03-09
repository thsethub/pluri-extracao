"""Router com endpoints do sistema de classificaÃ§Ã£o manual por usuÃ¡rios.

Rotas separadas do sistema de extraÃ§Ã£o/conferÃªncia.
Protegidas por autenticaÃ§Ã£o JWT.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, inspect, text as sql_text
from typing import Optional, List
from math import ceil
from datetime import datetime, timedelta, timezone
from loguru import logger
import re

from jose import JWTError, jwt
import bcrypt

from ..config import settings
from ..database import get_db, get_pg_db, get_shared_db
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
    AssuntoVinculadoSchema,
    ModuloComAssuntosSchema,
    ModulosAssuntosResponse,
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
# CACHE EM MEMÃ“RIA (TTL)
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


def _pick_first_column(available: set[str], candidates: list[str]) -> Optional[str]:
    """Retorna a primeira coluna existente na lista de candidatos."""
    for col in candidates:
        if col in available:
            return col
    return None


def _sql_ident(name: str) -> str:
    """Valida e escapa identificadores SQL simples (tabelas/colunas)."""
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise HTTPException(status_code=500, detail=f"Nome de coluna invÃ¡lido detectado: {name}")
    return f"`{name}`"


def _normalize_text(value: Optional[str]) -> str:
    return " ".join((value or "").strip().lower().split())


def _normalize_disc_modu_id(value: Optional[object]) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    if normalized.endswith(".0"):
        normalized = normalized[:-2]
    return normalized

# ========================
# CONFIG
# ========================
SECRET_KEY = settings.jwt_secret_key
ALGORITHM = settings.jwt_algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = settings.jwt_expire_minutes

# bcrypt 5.x â€” usar diretamente (passlib nÃ£o compatÃ­vel)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/classificacao/login")

router = APIRouter(prefix="/classificacao", tags=["ClassificaÃ§Ã£o Manual"])

# Disciplinas vÃ¡lidas para cadastro (incluindo Ãreas)
DISCIPLINAS_VALIDAS = [
    "Artes", "Biologia", "CiÃªncias", "EducaÃ§Ã£o FÃ­sica", "Espanhol",
    "Filosofia", "FÃ­sica", "Geografia", "HistÃ³ria", "LÃ­ngua Inglesa",
    "LÃ­ngua Portuguesa", "Literatura", "MatemÃ¡tica", "Natureza e Sociedade", 
    "QuÃ­mica", "RedaÃ§Ã£o", "Sociologia",
    # Ãreas
    "Humanas", "Linguagens", "Natureza"
]

# Mapeamento para o MySQL (onde os nomes podem ser diferentes do Postgres/Planilha)
MAP_DISCIPLINAS_MYSQL = {
    "Artes": "Artes",
    "LÃ­ngua Inglesa": "LÃ­ngua Inglesa",
    "LÃ­ngua Portuguesa": "LÃ­ngua Portuguesa",
    "Literatura": None, # NÃ£o existe no MySQL
    "RedaÃ§Ã£o": None,    # NÃ£o existe no MySQL
}

# Mapeamento de Ã¡reas para filtro
AREAS_DISCIPLINAS = {
    "Humanas": ["Filosofia", "Geografia", "HistÃ³ria", "Sociologia"],
    "Linguagens": ["Artes", "EducaÃ§Ã£o FÃ­sica", "Espanhol", "LÃ­ngua Inglesa", "LÃ­ngua Portuguesa", "Literatura", "RedaÃ§Ã£o"],
    "MatemÃ¡tica": ["MatemÃ¡tica"],
    "Natureza": ["Biologia", "CiÃªncias", "FÃ­sica", "Natureza e Sociedade", "QuÃ­mica"],
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
    """Dependency: extrai e valida o usuÃ¡rio do token JWT."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token invÃ¡lido ou expirado",
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
# AUTENTICAÃ‡ÃƒO
# ========================

@router.post(
    "/cadastro",
    response_model=TokenResponse,
    summary="ðŸ“ Cadastrar novo usuÃ¡rio",
    status_code=status.HTTP_201_CREATED,
)
async def cadastrar_usuario(
    request: CadastroRequest,
    pg_db: Session = Depends(get_pg_db),
):
    """
    Cadastra um novo usuÃ¡rio para classificaÃ§Ã£o manual.
    O campo `disciplina` deve ser uma das disciplinas vÃ¡lidas do sistema.
    """
    # Validar disciplina
    if request.disciplina not in DISCIPLINAS_VALIDAS:
        raise HTTPException(
            status_code=400,
            detail=f"Disciplina invÃ¡lida. OpÃ§Ãµes: {', '.join(DISCIPLINAS_VALIDAS)}",
        )

    # Verificar email duplicado
    existente = pg_db.query(UsuarioModel).filter(UsuarioModel.email == request.email).first()
    if existente:
        raise HTTPException(status_code=400, detail="Email jÃ¡ cadastrado")

    # Criar usuÃ¡rio
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
    logger.info(f"Novo usuÃ¡rio cadastrado: {usuario.nome} ({usuario.disciplina})")

    return TokenResponse(
        access_token=token,
        usuario=UsuarioSchema.model_validate(usuario),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="ðŸ”‘ Login",
)
async def login(
    request: LoginRequest,
    pg_db: Session = Depends(get_pg_db),
):
    """Autentica o usuÃ¡rio e retorna um token JWT."""
    usuario = pg_db.query(UsuarioModel).filter(UsuarioModel.email == request.email).first()
    if not usuario or not verificar_senha(request.senha, usuario.senha_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou senha incorretos",
        )
    if not usuario.ativo:
        raise HTTPException(status_code=403, detail="UsuÃ¡rio desativado")

    token = criar_token({"sub": usuario.id})
    logger.info(f"Login: {usuario.nome}")

    return TokenResponse(
        access_token=token,
        usuario=UsuarioSchema.model_validate(usuario),
    )


@router.get(
    "/me",
    response_model=UsuarioSchema,
    summary="ðŸ‘¤ Dados do usuÃ¡rio atual",
)
async def dados_usuario(usuario: UsuarioModel = Depends(get_usuario_atual)):
    """Retorna os dados do usuÃ¡rio autenticado."""
    return UsuarioSchema.model_validate(usuario)


@router.get(
    "/disciplinas",
    summary="ðŸ“š Disciplinas disponÃ­veis",
)
async def listar_disciplinas():
    """Retorna as disciplinas disponÃ­veis para cadastro e as Ã¡reas para filtro."""
    return {
        "disciplinas": DISCIPLINAS_VALIDAS,
        "areas": AREAS_DISCIPLINAS,
    }


@router.get(
    "/habilidades",
    response_model=HabilidadesFiltroResponse,
    summary="ðŸ” Listar assuntos (habilidades) para filtro",
)
async def listar_habilidades_filtro(
    area: Optional[str] = Query(None, description="Filtrar por Ã¡rea"),
    disciplina: Optional[str] = Query(None, description="Filtrar por nome da disciplina"),
    pg_db: Session = Depends(get_pg_db),
    db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna a lista de assuntos Ãºnicos (habilidade_id + habilidade_descricao)
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
            "LÃ­ngua Inglesa": ["LÃ­ngua Inglesa", "InglÃªs"],
            "LÃ­ngua Portuguesa": ["LÃ­ngua Portuguesa", "Lingua Portuguesa", "Literatura", "RedaÃ§Ã£o"],
        }
        mapped_names = mapping.get(disciplina, [disciplina])
        query = query.filter(HabilidadeModuloModel.disciplina.in_(mapped_names))

    results = query.order_by(HabilidadeModuloModel.habilidade_descricao).all()
    
    # Montar mapa de habilidades vÃ¡lidas
    hab_ids = [r.habilidade_id for r in results if r.habilidade_id is not None]
    
    counts_map = {}
    if hab_ids:
        # â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”
        # ABORDAGEM EFICIENTE v2: dois GROUP BY no MySQL (sem carregar
        # linhas individuais) + IDs excluÃ­dos do PG como set
        #
        # 1. MySQL  â†’ SELECT habilidade_id, COUNT(*) GROUP BY  (29 linhas)
        # 2. PG     â†’ 3 queries para obter IDs excluÃ­dos como set
        # 3. MySQL  â†’ SELECT habilidade_id, COUNT(*) WHERE id IN(excluÃ­dos)
        #             GROUP BY  (â‰¤ 29 linhas agrupadas)
        # 4. Python â†’ total - excluÃ­do por habilidade
        # â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”

        # Etapa 1: total de questÃµes por habilidade (MySQL GROUP BY, 29 linhas)
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

        # Etapa 2: IDs excluÃ­dos no PG (queries leves, sem IN gigante)
        ids_excluir: set[int] = set()

        # 2a. JÃ¡ classificadas manualmente, com low-match, ou pelo SuperPro
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

        # 2b. JÃ¡ classificadas por este usuÃ¡rio
        for r in pg_db.query(ClassificacaoUsuarioModel.questao_id).filter(
            ClassificacaoUsuarioModel.usuario_id == usuario.id
        ).all():
            ids_excluir.add(r[0])

        # 2c. Puladas por qualquer usuÃ¡rio â€” sÃ³ aparecem em Pendentes, nunca em /proxima
        for r in pg_db.query(QuestaoPuladaModel.questao_id).all():
            ids_excluir.add(r[0])

        # Etapa 3: contagem de excluÃ­das por habilidade no MySQL
        # (IN por PK Ã© eficiente; resposta = â‰¤ 29 linhas agrupadas)
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
    summary="ðŸ” Assuntos com questÃµes pendentes (puladas)",
)
async def listar_habilidades_pendentes(
    area: Optional[str] = Query(None, description="Filtrar por Ã¡rea"),
    disciplina: Optional[str] = Query(None, description="Filtrar por nome da disciplina"),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna apenas os assuntos (habilidades) que possuem questÃµes puladas (pendentes),
    com a contagem de quantas existem. Respeita o filtro de Ã¡rea/disciplina do usuÃ¡rio.
    """
    effective_area = area or (usuario.disciplina if not usuario.is_admin else None)

    # IDs jÃ¡ classificados por este usuÃ¡rio (excluir das contagens)
    ids_classificadas: set[int] = {
        row[0] for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.usuario_id == usuario.id)
        .all()
    }

    # Base: questÃµes puladas com habilidade definida
    query_puladas = pg_db.query(
        QuestaoPuladaModel.habilidade_id,
        func.count(func.distinct(QuestaoPuladaModel.questao_id)).label("total"),
    ).filter(QuestaoPuladaModel.habilidade_id.isnot(None))

    if ids_classificadas:
        query_puladas = query_puladas.filter(
            ~QuestaoPuladaModel.questao_id.in_(list(ids_classificadas))
        )

    # Filtro de disciplina explÃ­cito
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

    # Buscar descriÃ§Ãµes no habilidade_modulos
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
# MÃ“DULOS (consulta)
# ========================

@router.get(
    "/modulos",
    response_model=List[HabilidadeModuloSchema],
    summary="ðŸ“¦ Todos os mÃ³dulos disponÃ­veis para seleÃ§Ã£o manual",
)
async def listar_todos_modulos(
    disciplina: Optional[str] = Query(None, description="Filtrar por nome da disciplina"),
    area: Optional[str] = Query(None, description="Filtrar por Ã¡rea"),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna todos os mÃ³dulos do TriEduc, opcionalmente filtrados por disciplina ou Ã¡rea.
    Usado no modal de correÃ§Ã£o de classificaÃ§Ã£o para permitir busca livre por qualquer mÃ³dulo.
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
    "/modulos-assuntos",
    response_model=ModulosAssuntosResponse,
    summary="ðŸ“š MÃ³dulos com assuntos relacionados (sem prefixo [RM])",
)
async def listar_modulos_com_assuntos(
    shared_db: Session = Depends(get_shared_db),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna mÃ³dulos do banco compartilhados com os assuntos relacionados vÃ¡lidos.

    Retorna apenas os mÃ³dulos do LivroStudio sem relacionamento atual com o TriEduc.
    """
    cache_key = "modulos_assuntos_compartilhados_v2"
    cached = get_from_cache(cache_key, ttl=600)
    if cached is not None:
        return cached

    try:
        inspector = inspect(shared_db.get_bind())
        table_names = inspector.get_table_names()
        table_names_lower = {t.lower(): t for t in table_names}

        assuntos_table = None
        for candidate in ["assuntos", "assunto"]:
            if candidate in table_names_lower:
                assuntos_table = table_names_lower[candidate]
                break

        modulos_table = None
        for candidate in [
            "disciplina_modulos",
            "disciplinas_modulos",
            "disciplina_modulo",
            "disciplinas_modulo",
            "modulos_disciplina",
            "modulo_disciplina",
        ]:
            if candidate in table_names_lower:
                modulos_table = table_names_lower[candidate]
                break

        if not modulos_table:
            for t in table_names:
                t_norm = t.lower()
                if "modul" in t_norm and "disc" in t_norm:
                    modulos_table = t
                    break

        if not assuntos_table:
            raise HTTPException(status_code=500, detail="Tabela de assuntos nÃ£o encontrada em compartilhados.")
        if not modulos_table:
            raise HTTPException(status_code=500, detail="Tabela de mÃ³dulos de disciplina nÃ£o encontrada em compartilhados.")

        assuntos_cols = {c["name"] for c in inspector.get_columns(assuntos_table)}
        modulos_cols = {c["name"] for c in inspector.get_columns(modulos_table)}

        assunto_desc_col = _pick_first_column(assuntos_cols, ["assu_descricao", "descricao", "nome"])
        if not assunto_desc_col:
            raise HTTPException(
                status_code=500,
                detail="Coluna de descriÃ§Ã£o de assunto nÃ£o encontrada na tabela 'assuntos'.",
            )

        assunto_id_col = _pick_first_column(assuntos_cols, ["assu_id", "id"])
        modulo_nome_col = _pick_first_column(
            modulos_cols,
            [
                "modulo",
                "dimo_descricao",
                "descricao",
                "nome",
                "disc_modu_descricao",
                "disc_modulo_descricao",
                "disc_modu_nome",
                "disc_modulo_nome",
                "dimo_nome",
            ],
        )
        if not modulo_nome_col:
            modulo_nome_col = next(
                (
                    c
                    for c in modulos_cols
                    if "modu" in c.lower()
                    and (
                        "descr" in c.lower()
                        or "nome" in c.lower()
                        or "titulo" in c.lower()
                    )
                ),
                None,
            )
        if not modulo_nome_col:
            raise HTTPException(
                status_code=500,
                detail=f"Coluna de nome de mÃ³dulo nÃ£o encontrada. Colunas disponÃ­veis: {sorted(modulos_cols)}",
            )

        disciplina_nome_col = _pick_first_column(
            modulos_cols,
            [
                "disciplina",
                "disciplina_nome",
                "disciplina_descricao",
                "nome_disciplina",
            ],
        )
        if not disciplina_nome_col:
            disciplina_nome_col = next(
                (
                    c
                    for c in modulos_cols
                    if c != modulo_nome_col
                    and "disc" in c.lower()
                    and ("nome" in c.lower() or "desc" in c.lower())
                ),
                None,
            )

        join_candidates = [
            ("disc_modu_id", "disc_modu_id"),
            ("disciplina_modulo_id", "id"),
            ("dimo_id", "dimo_id"),
            ("dimo_id", "id"),
            ("disc_modu_id", "id"),
        ]
        join_cols = next(
            ((a_col, dm_col) for a_col, dm_col in join_candidates if a_col in assuntos_cols and dm_col in modulos_cols),
            None,
        )
        if not join_cols:
            raise HTTPException(
                status_code=500,
                detail="NÃ£o foi possÃ­vel identificar o relacionamento entre 'assuntos' e 'disciplina_modulos'.",
            )

        assunto_join_col, modulo_join_col = join_cols
        modulo_id_col = _pick_first_column(modulos_cols, [modulo_join_col, "disc_modu_id", "dimo_id", "id"]) or modulo_join_col
        modulo_disc_modu_col = _pick_first_column(modulos_cols, [modulo_join_col, "disc_modu_id", "dimo_id", "id"])
        assunto_id_select = f", a.{_sql_ident(assunto_id_col)} AS assunto_id" if assunto_id_col else ""
        disciplina_select = (
            f", dm.{_sql_ident(disciplina_nome_col)} AS disciplina_nome"
            if disciplina_nome_col
            else ", NULL AS disciplina_nome"
        )
        modulo_disc_modu_select = (
            f", dm.{_sql_ident(modulo_disc_modu_col)} AS modulo_disc_modu_id"
            if modulo_disc_modu_col
            else ""
        )

        trieduc_pairs = set()
        trieduc_disc_modu_ids = set()
        for disciplina, modulo, disc_modu_id in pg_db.query(
            HabilidadeModuloModel.disciplina,
            HabilidadeModuloModel.modulo,
            HabilidadeModuloModel.disc_modu_id,
        ).all():
            disc_norm = _normalize_text(disciplina)
            mod_norm = _normalize_text(modulo)
            if disc_norm and mod_norm:
                trieduc_pairs.add((disc_norm, mod_norm))

            disc_modu_norm = _normalize_disc_modu_id(disc_modu_id)
            if disc_modu_norm:
                trieduc_disc_modu_ids.add(disc_modu_norm)

        sql = f"""
            SELECT
                dm.{_sql_ident(modulo_id_col)} AS modulo_id,
                dm.{_sql_ident(modulo_nome_col)} AS modulo_nome,
                a.{_sql_ident(assunto_desc_col)} AS assunto_descricao
                {assunto_id_select}
                {disciplina_select}
                {modulo_disc_modu_select}
            FROM {_sql_ident(assuntos_table)} a
            INNER JOIN {_sql_ident(modulos_table)} dm
                ON a.{_sql_ident(assunto_join_col)} = dm.{_sql_ident(modulo_join_col)}
            WHERE a.{_sql_ident(assunto_desc_col)} IS NOT NULL
                AND TRIM(a.{_sql_ident(assunto_desc_col)}) <> ''
                AND TRIM(a.{_sql_ident(assunto_desc_col)}) NOT LIKE :rm_prefix
            ORDER BY dm.{_sql_ident(modulo_nome_col)}, a.{_sql_ident(assunto_desc_col)}
        """

        rows = shared_db.execute(sql_text(sql), {"rm_prefix": "[RM]%"}).mappings().all()

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao consultar mÃ³dulos/assuntos em compartilhados: {exc}",
        ) from exc

    grouped = {}
    for row in rows:
        modulo_nome = (row.get("modulo_nome") or "").strip()
        if not modulo_nome:
            continue

        disciplina_nome = (row.get("disciplina_nome") or "").strip()
        disciplina_norm = _normalize_text(disciplina_nome)
        modulo_norm = _normalize_text(modulo_nome)

        modulo_disc_modu_norm = _normalize_disc_modu_id(row.get("modulo_disc_modu_id"))

        has_relacionamento_trieduc = False
        if disciplina_norm and modulo_norm and (disciplina_norm, modulo_norm) in trieduc_pairs:
            has_relacionamento_trieduc = True
        elif modulo_disc_modu_norm and modulo_disc_modu_norm in trieduc_disc_modu_ids:
            has_relacionamento_trieduc = True

        if has_relacionamento_trieduc:
            continue

        modulo_id = row.get("modulo_id") if row.get("modulo_id") is not None else modulo_nome
        group_key = f"{disciplina_norm}::{modulo_id}::{modulo_norm}"

        if group_key not in grouped:
            grouped[group_key] = {
                "id": modulo_id,
                "nome": modulo_nome,
                "disciplina": disciplina_nome,
                "assuntos": [],
                "_seen": set(),
            }

        assunto_descricao = (row.get("assunto_descricao") or "").strip()
        if not assunto_descricao:
            continue

        assunto_id = row.get("assunto_id")
        assunto_key = (assunto_id, assunto_descricao)
        if assunto_key in grouped[group_key]["_seen"]:
            continue

        grouped[group_key]["_seen"].add(assunto_key)
        grouped[group_key]["assuntos"].append(
            AssuntoVinculadoSchema(
                id=assunto_id,
                descricao=assunto_descricao,
            )
        )

    modulos = []
    total_assuntos = 0
    for module_data in grouped.values():
        assuntos = module_data["assuntos"]
        total_assuntos += len(assuntos)
        modulos.append(
            ModuloComAssuntosSchema(
                id=module_data["id"],
                disciplina=module_data["disciplina"],
                nome=module_data["nome"],
                assuntos=assuntos,
                total_assuntos=len(assuntos),
                fonte="librostudio",
                has_relacionamento_trieduc=False,
            )
        )

    modulos.sort(key=lambda m: ((m.disciplina or "").lower(), m.nome.lower()))

    response = ModulosAssuntosResponse(
        modulos=modulos,
        total_modulos=len(modulos),
        total_assuntos=total_assuntos,
    )
    set_to_cache(cache_key, response)
    return response
@router.get(
    "/modulos/{habilidade_id}",
    response_model=ModulosResponse,
    summary="ðŸ“¦ MÃ³dulos possÃ­veis para uma habilidade",
)
async def listar_modulos_por_habilidade(
    habilidade_id: int,
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna os mÃ³dulos possÃ­veis para um dado habilidade_id do TriEduc."""
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
# QUESTÃ•ES PARA CLASSIFICAR
# ========================

@router.get(
    "/proxima",
    response_model=QuestaoClassifResponse,
    summary="ðŸ” PrÃ³xima questÃ£o para classificar",
)
async def proxima_questao_classificar(
    area: Optional[str] = Query(None, description="Filtrar por Ã¡rea (Humanas, Linguagens, MatemÃ¡tica, Natureza)"),
    disciplina_id: Optional[str] = Query(None, description="ID ou Nome da disciplina"),
    habilidade_id: Optional[int] = Query(None, description="ID da habilidade TRIEDUC"),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna a prÃ³xima questÃ£o que ainda NÃƒO foi classificada manualmente pelo usuÃ¡rio.
    Prioriza questÃµes sem extraÃ§Ã£o automÃ¡tica.

    Filtros:
    - **area**: "Humanas", "Linguagens", "MatemÃ¡tica", "Natureza"
    - **disciplina_id**: ID numÃ©rico da disciplina
    - **habilidade_id**: ID da habilidade TRIEDUC
    """
    # IDs a excluir (jÃ¡ classificadas por este usuÃ¡rio OU jÃ¡ possuem classificaÃ§Ã£o no sistema)
    # ForÃ§ar Ã¡rea do usuÃ¡rio se nÃ£o enviada
    if not area:
        area = usuario.disciplina

    logger.info(f"Busca PrÃ³xima: usuario={usuario.nome}, area={area}, disciplina={disciplina_id}, habilidade={habilidade_id}")

    # Resolver filtro de Ã¡rea â†’ disciplinas (Otimizado: apenas IDs)
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
        .filter(QuestaoModel.ano_id == 3) # Ensino MÃ©dio
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
                    # Se nome exato nÃ£o existe no MySQL, falhar para nÃ£o mostrar tudo
                    candidate_query = candidate_query.filter(QuestaoModel.id == -1)
            else:
                # Disciplina Virtual (Literatura/RedaÃ§Ã£o): Buscar IDs de habilidade no Postgres
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
        # QuestÃµes que jÃ¡ tÃªm qualquer tipo de classificaÃ§Ã£o nÃ£o devem aparecer para classificar.
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

        # 3. Puladas por qualquer usuÃ¡rio â€” sÃ³ aparecem na aba Pendentes, nunca em /proxima
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
            detail="Nenhuma questÃ£o pendente para classificaÃ§Ã£o encontrada.",
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

    # MÃ³dulos possÃ­veis
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

        # FALLBACK: Se nÃ£o achou mÃ³dulos por ID, tenta por descriÃ§Ã£o (Case Insensitive)
        if not modulos and hab_descricao:
            modulos_q = (
                pg_db.query(HabilidadeModuloModel)
                .filter(func.lower(HabilidadeModuloModel.habilidade_descricao) == hab_descricao.lower())
                .order_by(HabilidadeModuloModel.modulo)
                .all()
            )
            modulos = [HabilidadeModuloSchema.model_validate(m) for m in modulos_q]

    alternativas = []
    if questao.tipo == "MÃºltipla Escolha" and questao.alternativas:
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
    summary="Consultar questÃ£o por ID (admin)",
)
async def consultar_questao_por_id(
    questao_id: int,
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna uma questÃ£o especÃ­fica no mesmo formato da rota /proxima."""
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
        raise HTTPException(status_code=404, detail="QuestÃ£o nÃ£o encontrada")

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
    if questao.tipo == "MÃºltipla Escolha" and questao.alternativas:
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
    summary="ðŸ”„ PrÃ³xima questÃ£o para verificar (jÃ¡ classificada)",
)
async def proxima_questao_verificar(
    area: Optional[str] = Query(None, description="Filtrar por Ã¡rea"),
    disciplina_id: Optional[str] = Query(None, description="ID ou Nome da disciplina"),
    habilidade_id: Optional[int] = Query(None, description="ID da habilidade TRIEDUC"),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna a prÃ³xima questÃ£o que JÃ tem classificaÃ§Ã£o automÃ¡tica
    para o usuÃ¡rio verificar se estÃ¡ correta.
    """
    # IDs jÃ¡ verificadas por este usuÃ¡rio
    ids_verificadas = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.usuario_id == usuario.id)
        .all()
    }

    # ForÃ§ar Ã¡rea do usuÃ¡rio se nÃ£o enviada
    if not area:
        area = usuario.disciplina

    # Resolver filtro de Ã¡rea
    disciplina_ids_filtro = None
    if area and area in AREAS_DISCIPLINAS:
        from ..database.models import DisciplinaModel
        nomes = AREAS_DISCIPLINAS[area]
        discs = db.query(DisciplinaModel).filter(DisciplinaModel.descricao.in_(nomes)).all()
        disciplina_ids_filtro = [d.id for d in discs]

    # Query Base no PG: extraÃ­das pelo Superpro com baixa similaridade (precisa verificaÃ§Ã£o humana)
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
                # Disciplina Virtual (Literatura/RedaÃ§Ã£o): Buscar IDs de habilidade no Postgres
                habilidade_ids_custom = [
                    row[0] for row in pg_db.query(HabilidadeModuloModel.habilidade_id)
                    .filter(HabilidadeModuloModel.disciplina == disciplina_id)
                    .filter(HabilidadeModuloModel.habilidade_id.isnot(None))
                    .distinct()
                    .all()
                ]
                if habilidade_ids_custom:
                    # No QuestaoAssuntoModel, habilidade_id pode nÃ£o estar preenchido se veio do scraping
                    # Mas se tivermos o ID TRIEDUC no MySQL (QuestaoModel), podemos filtrar lÃ¡.
                    # No entanto, a query base Ã© sobre QuestaoAssuntoModel. 
                    # Se salvamos a extraÃ§Ã£o, populamos habilidade_id? Geralmente sim.
                    query_pg = query_pg.filter(QuestaoAssuntoModel.habilidade_id.in_(habilidade_ids_custom))
                else:
                    query_pg = query_pg.filter(QuestaoAssuntoModel.id == -1)
    elif disciplina_ids_filtro:
        query_pg = query_pg.filter(QuestaoAssuntoModel.disciplina_id.in_(disciplina_ids_filtro))

    # Tentar encontrar uma questÃ£o que seja efetivamente de Ensino MÃ©dio no MySQL
    MAX_TENTATIVAS = 100
    for _ in range(MAX_TENTATIVAS):
        registro_pg = query_pg.order_by(QuestaoAssuntoModel.id).first()
        
        if not registro_pg:
            raise HTTPException(
                status_code=404,
                detail="Nenhuma questÃ£o pendente de verificaÃ§Ã£o com os filtros aplicados",
            )

        # Verificar nÃ­vel no MySQL
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
            # Pula esta e marca como "invÃ¡lida para este fluxo" temporariamente na query
            ids_verificadas.add(registro_pg.questao_id)
            query_pg = query_pg.filter(QuestaoAssuntoModel.questao_id != registro_pg.questao_id)
            continue

        # Se chegou aqui, temos a questÃ£o!
        break
    else:
        raise HTTPException(status_code=404, detail="NÃ£o foram encontradas questÃµes de Ensino MÃ©dio para verificar.")

    # Tratar enunciado
    enunciado_tratado, contem_imagem, motivo_erro = tratar_enunciado(questao.enunciado)
    
    texto_base_tratado = None
    if questao.texto_base:
        texto_base_tratado, _, _ = tratar_enunciado(questao.texto_base)

    # MÃ³dulos possÃ­veis
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

        # FALLBACK: Se nÃ£o achou mÃ³dulos por ID, tenta por descriÃ§Ã£o (Case Insensitive)
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
    if questao.tipo == "MÃºltipla Escolha" and questao.alternativas:
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
# PRÃ“XIMA QUESTÃƒO LOW MATCH
# ========================

@router.get(
    "/proxima-low-match",
    response_model=QuestaoClassifResponse,
    summary="âš ï¸ PrÃ³xima questÃ£o com classificaÃ§Ã£o de baixa similaridade",
)
async def proxima_questao_low_match(
    area: Optional[str] = Query(None, description="Filtrar por Ã¡rea"),
    disciplina_id: Optional[str] = Query(None, description="ID ou Nome da disciplina"),
    habilidade_id: Optional[int] = Query(None, description="ID da habilidade TRIEDUC"),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna a prÃ³xima questÃ£o que possui classificacao_nao_enquadrada
    (match baixo do SuperProfessor) para revisÃ£o pelo professor.
    """
    # IDs jÃ¡ verificadas por este usuÃ¡rio
    ids_verificadas = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.usuario_id == usuario.id)
        .all()
    }

    # ForÃ§ar Ã¡rea do usuÃ¡rio se nÃ£o enviada
    if not area:
        area = usuario.disciplina

    # Resolver filtro de Ã¡rea
    disciplina_ids_filtro = None
    if area and area in AREAS_DISCIPLINAS:
        from ..database.models import DisciplinaModel
        nomes = AREAS_DISCIPLINAS[area]
        discs = db.query(DisciplinaModel).filter(DisciplinaModel.descricao.in_(nomes)).all()
        disciplina_ids_filtro = [d.id for d in discs]

    # Query no PG: questÃµes com classificacao_nao_enquadrada preenchida
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

    # Tentar encontrar uma questÃ£o vÃ¡lida de Ensino MÃ©dio
    MAX_TENTATIVAS = 100
    for _ in range(MAX_TENTATIVAS):
        registro_pg = query_pg.order_by(QuestaoAssuntoModel.id).first()

        if not registro_pg:
            raise HTTPException(
                status_code=404,
                detail="Nenhuma questÃ£o de baixa similaridade pendente com os filtros aplicados",
            )

        # Verificar nÃ­vel no MySQL
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
        raise HTTPException(status_code=404, detail="NÃ£o foram encontradas questÃµes de baixa similaridade para verificar.")

    # Tratar enunciado
    enunciado_tratado, contem_imagem, motivo_erro = tratar_enunciado(questao.enunciado)
    
    texto_base_tratado = None
    if questao.texto_base:
        texto_base_tratado, _, _ = tratar_enunciado(questao.texto_base)

    # MÃ³dulos possÃ­veis
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
    if questao.tipo == "MÃºltipla Escolha" and questao.alternativas:
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
# SALVAR CLASSIFICAÃ‡ÃƒO
# ========================

@router.post(
    "/salvar",
    response_model=SalvarClassificacaoResponse,
    summary="ðŸ’¾ Salvar classificaÃ§Ã£o do usuÃ¡rio",
)
async def salvar_classificacao(
    request: SalvarClassificacaoRequest,
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Salva a decisÃ£o de classificaÃ§Ã£o do usuÃ¡rio.
    Tipos de aÃ§Ã£o:
    - **classificacao_nova**: QuestÃ£o que nÃ£o tinha classificaÃ§Ã£o
    - **confirmacao**: UsuÃ¡rio confirmou classificaÃ§Ã£o existente
    - **correcao**: UsuÃ¡rio corrigiu classificaÃ§Ã£o existente
    """
    if request.tipo_acao not in ("classificacao_nova", "confirmacao", "correcao"):
        raise HTTPException(status_code=400, detail="tipo_acao invÃ¡lido")

    # Buscar habilidade_id da questÃ£o (Apenas o necessÃ¡rio)
    questao_data = db.query(QuestaoModel.id, QuestaoModel.habilidade_id, QuestaoModel.questao_id, QuestaoModel.disciplina_id).filter(QuestaoModel.id == request.questao_id).first()
    if not questao_data:
        raise HTTPException(status_code=404, detail="QuestÃ£o nÃ£o encontrada")

    # Buscar classificaÃ§Ã£o da extraÃ§Ã£o (se existir)
    extracao = (
        pg_db.query(QuestaoAssuntoModel)
        .filter(QuestaoAssuntoModel.questao_id == request.questao_id)
        .first()
    )

    # 1. Atualizar flag de classificaÃ§Ã£o manual na tabela questao_assuntos
    if not extracao:
        # Buscar nome da disciplina se for criar
        from ..database.models import DisciplinaModel
        disc_nome = None
        if questao_data.disciplina_id:
            disc_row = db.query(DisciplinaModel.descricao).filter(DisciplinaModel.id == questao_data.disciplina_id).first()
            disc_nome = disc_row[0] if disc_row else None

        # Criar registro bÃ¡sico para marcar como manual
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

    # Criar registro de histÃ³rico
    classificacao = ClassificacaoUsuarioModel(
        usuario_id=usuario.id,
        questao_id=request.questao_id,
        habilidade_id=questao_data.habilidade_id,
        # Campos legados (single) - retrocompatibilidade
        modulo_escolhido=request.modulo_escolhido,
        classificacao_trieduc=request.classificacao_trieduc,
        descricao_assunto=request.descricao_assunto,
        habilidade_modulo_id=request.habilidade_modulo_id,
        # Campos novos (mÃºltiplos mÃ³dulos JSONB)
        modulos_escolhidos=request.modulos_escolhidos,
        classificacoes_trieduc_list=request.classificacoes_trieduc,
        descricoes_assunto_list=request.descricoes_assunto,
        habilidade_modulo_ids=request.habilidade_modulo_ids,
        # ExtraÃ§Ã£o e metadados
        classificacao_extracao=extracao.classificacoes if extracao else None,
        tipo_acao=request.tipo_acao,
        observacao=request.observacao,
    )
    pg_db.add(classificacao)

    # Auto-remover da lista de questÃµes puladas (se existir para qualquer usuÃ¡rio)
    # Motivo: Se foi classificada, nÃ£o estÃ¡ mais pendente para ninguÃ©m.
    pg_db.query(QuestaoPuladaModel).filter(
        QuestaoPuladaModel.questao_id == request.questao_id,
    ).delete()

    pg_db.commit()

    modulos_info = request.modulos_escolhidos or [request.modulo_escolhido] if request.modulo_escolhido else []
    logger.info(
        f"ClassificaÃ§Ã£o salva: usuario={usuario.nome}, questao={request.questao_id}, "
        f"acao={request.tipo_acao}, modulos={modulos_info}"
    )

    return SalvarClassificacaoResponse(
        success=True,
        id=classificacao.id,
        questao_id=request.questao_id,
        tipo_acao=request.tipo_acao,
        message=f"ClassificaÃ§Ã£o ({request.tipo_acao}) salva com sucesso",
    )


# ========================
# PULAR QUESTÃƒO (PENDENTES)
# ========================

@router.post(
    "/pular",
    response_model=PularQuestaoResponse,
    summary="â­ï¸ Pular questÃ£o (marcar como pendente)",
)
async def pular_questao(
    request: PularQuestaoRequest,
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Marca uma questÃ£o como pulada pelo usuÃ¡rio.
    A questÃ£o aparecerÃ¡ na aba 'Pendentes' para classificaÃ§Ã£o posterior.
    """
    # Verificar se a questÃ£o existe
    questao_data = db.query(
        QuestaoModel.id, QuestaoModel.disciplina_id, QuestaoModel.habilidade_id
    ).filter(QuestaoModel.id == request.questao_id).first()

    if not questao_data:
        raise HTTPException(status_code=404, detail="QuestÃ£o nÃ£o encontrada")

    # Verificar se jÃ¡ foi pulada (evitar duplicata)
    existente = pg_db.query(QuestaoPuladaModel).filter(
        QuestaoPuladaModel.usuario_id == usuario.id,
        QuestaoPuladaModel.questao_id == request.questao_id,
    ).first()

    if existente:
        return PularQuestaoResponse(
            success=True,
            message="QuestÃ£o jÃ¡ estava marcada como pendente",
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

    logger.info(f"QuestÃ£o pulada: usuario={usuario.nome}, questao={request.questao_id}")

    return PularQuestaoResponse(
        success=True,
        message="QuestÃ£o marcada como pendente",
    )


@router.get(
    "/proxima-pendente",
    response_model=QuestaoClassifResponse,
    summary="ðŸ“‹ PrÃ³xima questÃ£o pendente (pulada)",
)
async def proxima_questao_pendente(
    area: Optional[str] = Query(None, description="Filtrar por Ã¡rea"),
    disciplina_id: Optional[str] = Query(None, description="ID ou Nome da disciplina"),
    habilidade_id: Optional[int] = Query(None, description="ID da habilidade TRIEDUC"),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna a prÃ³xima questÃ£o pendente (pulada por qualquer usuÃ¡rio).
    Restrito Ã  Ã¡rea/disciplina do usuÃ¡rio por padrÃ£o.
    """
    # Base query: questÃµes puladas por qualquer usuÃ¡rio
    query_puladas = pg_db.query(QuestaoPuladaModel)

    # Ãrea efetiva: usa o filtro explÃ­cito, ou cai na disciplina do prÃ³prio usuÃ¡rio (nÃ£o-admin)
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
                    # Se nÃ£o existe no MySQL, falhar filtro
                    query_puladas = query_puladas.filter(QuestaoPuladaModel.id == -1)
            else:
                # Disciplina Virtual (Literatura/RedaÃ§Ã£o): Buscar IDs de habilidade no Postgres
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

    # LOG para depuraÃ§Ã£o
    logger.info(f"Filtro Pendentes: usuario={usuario.nome}, effective_area={effective_area}, disciplina={disciplina_id}")
    count_antes = query_puladas.count()
    logger.info(f"Total pendentes com filtros aplicados: {count_antes}")

    # IDs jÃ¡ classificadas por este usuÃ¡rio (excluir das pendentes)
    ids_classificadas = {
        row[0] for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.usuario_id == usuario.id)
        .all()
    }

    if ids_classificadas:
        query_puladas = query_puladas.filter(~QuestaoPuladaModel.questao_id.in_(ids_classificadas))

    # Buscar prÃ³xima pendente (ordem de inserÃ§Ã£o)
    registro_pulado = query_puladas.order_by(QuestaoPuladaModel.id).first()

    if not registro_pulado:
        raise HTTPException(
            status_code=404,
            detail="Nenhuma questÃ£o pendente encontrada com os filtros aplicados.",
        )

    # Carregar detalhes completos da questÃ£o do MySQL
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
        # QuestÃ£o nÃ£o existe mais no MySQL, remover da lista de puladas
        pg_db.delete(registro_pulado)
        pg_db.commit()
        raise HTTPException(status_code=404, detail="QuestÃ£o pendente nÃ£o encontrada no banco de dados.")

    # Tratar enunciado
    enunciado_tratado, contem_imagem, motivo_erro = tratar_enunciado(questao.enunciado)
    
    texto_base_tratado = None
    if questao.texto_base:
        texto_base_tratado, _, _ = tratar_enunciado(questao.texto_base)

    # Verificar classificaÃ§Ã£o existente
    extracao = (
        pg_db.query(QuestaoAssuntoModel)
        .filter(QuestaoAssuntoModel.questao_id == questao.id)
        .first()
    )

    hab_descricao = None
    if questao.habilidade:
        hab_descricao = questao.habilidade.descricao

    # MÃ³dulos possÃ­veis
    modulos = []
    if questao.habilidade_id:
        modulos_q = (
            pg_db.query(HabilidadeModuloModel)
            .filter(HabilidadeModuloModel.habilidade_id == questao.habilidade_id)
            .order_by(HabilidadeModuloModel.modulo)
            .all()
        )
        modulos = [HabilidadeModuloSchema.model_validate(m) for m in modulos_q]

        # FALLBACK por descriÃ§Ã£o
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
    if questao.tipo == "MÃºltipla Escolha" and questao.alternativas:
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
# ESTATÃSTICAS
# ========================

@router.get(
    "/stats",
    response_model=ClassificacaoStatsResponse,
    summary="ðŸ“Š EstatÃ­sticas de classificaÃ§Ã£o manual",
)
async def estatisticas_classificacao(
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna estatÃ­sticas do sistema de classificaÃ§Ã£o manual (Cache 5m)."""
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

    # Filtro Base: Ensino MÃ©dio + Habilidade ID
    # Join com DisciplinaModel para garantir integridade (opcional mas mantido para consistÃªncia)
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

    # 1. Manual (Prioridade MÃ¡xima)
    manuais_ids = {r[0] for r in pg_db.query(ClassificacaoUsuarioModel.questao_id)\
                   .filter(ClassificacaoUsuarioModel.questao_id.in_(em_ids)).all()}
    total_manuais = len(manuais_ids)

    # 2. AutomÃ¡ticas (Match >= 80% e que nÃ£o foram tocadas manualmente)
    auto_query = pg_db.query(QuestaoAssuntoModel.questao_id).filter(
        QuestaoAssuntoModel.questao_id.in_(em_ids),
        QuestaoAssuntoModel.similaridade >= 0.8
    ).all()
    auto_ids = {r[0] for r in auto_query} - manuais_ids
    total_auto_superpro = len(auto_ids)

    # 3. Faltam Verificar (Match < 80% e que nÃ£o foram tocadas nem sÃ£o automÃ¡ticas)
    verificar_query = pg_db.query(QuestaoAssuntoModel.questao_id).filter(
        QuestaoAssuntoModel.questao_id.in_(em_ids),
        QuestaoAssuntoModel.similaridade < 0.8,
        QuestaoAssuntoModel.similaridade > 0
    ).all()
    verificar_ids = {r[0] for r in verificar_query} - manuais_ids - auto_ids
    total_precisa_verificar = len(verificar_ids)

    # 4. Puladas (Volume que nÃ£o estÃ¡ em nenhum dos estados acima)
    from ..database.pg_pular_models import QuestaoPuladaModel
    puladas_query = pg_db.query(QuestaoPuladaModel.questao_id).filter(
        QuestaoPuladaModel.questao_id.in_(em_ids)
    ).all()
    # Para a matemÃ¡tica do Pendentes, usamos apenas as que nÃ£o foram classificadas de outra forma
    all_puladas_ids = {r[0] for r in puladas_query}
    puladas_ids_disjoint = all_puladas_ids - manuais_ids - auto_ids - verificar_ids
    total_puladas = len(puladas_ids_disjoint) # Agora usamos apenas as exclusivas para nÃ£o estourar a soma do Total

    # 5. Pendentes (O resto matemÃ¡tico restrito)
    # A soma de (manuais + auto + verificar + puladas + pendentes) = total_sistema
    total_pendentes = max(0, total_sistema - total_manuais - total_auto_superpro - total_precisa_verificar - total_puladas)

    # Por disciplina (Dashboard style)
    mysql_rows = db.query(QuestaoModel.disciplina_id, func.count(QuestaoModel.id))\
        .filter(QuestaoModel.ano_id == 3, QuestaoModel.habilidade_id.isnot(None))\
        .group_by(QuestaoModel.disciplina_id).all()
    mysql_counts = {r[0]: r[1] for r in mysql_rows}
    
    # Contabilizamos como "feitas" para o progresso: Manual + AutomÃ¡tica (Finalizadas)
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

    # Por usuÃ¡rio (Atividades Recentes)
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
# HISTÃ“RICO (para ML)
# ========================

@router.get(
    "/historico",
    response_model=HistoricoListResponse,
    summary="ðŸ“‹ HistÃ³rico de classificaÃ§Ãµes (dados para ML)",
)
async def historico_classificacoes(
    page: int = Query(1, ge=1, description="PÃ¡gina"),
    per_page: int = Query(50, ge=1, le=200, description="Itens por pÃ¡gina"),
    tipo_acao: Optional[str] = Query(None, description="Filtrar por tipo de aÃ§Ã£o"),
    usuario_id: Optional[int] = Query(None, description="Filtrar por usuÃ¡rio"),
    pg_db: Session = Depends(get_pg_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna histÃ³rico paginado de todas as classificaÃ§Ãµes feitas por usuÃ¡rios.
    Usado para exportaÃ§Ã£o de dados de treino ML.
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

    # Buscar nomes dos usuÃ¡rios
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

