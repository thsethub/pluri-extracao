"""Router com endpoints do sistema de classificação manual por usuários.

Rotas separadas do sistema de extração/conferência.
Protegidas por autenticação JWT.
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
from ..database import get_db
from ..database.models import QuestaoModel, HabilidadeModel, DisciplinaModel
from ..database.pg_models import QuestaoAssuntoModel
from ..database.pg_modulo_models import HabilidadeModuloModel
from ..database.pg_usuario_models import UsuarioModel, ClassificacaoUsuarioModel
from ..database.pg_pular_models import QuestaoPuladaModel
from ..database.pg_usuario_models import (
    QuestaoSuperprofessorModel,
    AlternativaSuperprofessorModel,
)
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
    QuestaoSuperprofessorResponse,
    AlternativaSuperprofessorSchema,
    SuperprofessorStatsResponse,
    SalvarSuperprofessorRequest,
    PularSuperprofessorRequest,
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


def _pick_first_column(available: set[str], candidates: list[str]) -> Optional[str]:
    """Retorna a primeira coluna existente na lista de candidatos."""
    for col in candidates:
        if col in available:
            return col
    return None


def _sql_ident(name: str) -> str:
    """Valida e escapa identificadores SQL simples (tabelas/colunas)."""
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise HTTPException(
            status_code=500, detail=f"Nome de coluna inválido detectado: {name}"
        )
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


def _resolver_habilidade_mysql_ids(
    trieduc_habilidade_id: int,
    pg_db: Session,
    db: Session,
) -> list[int]:
    """Resolve um habilidade_id TRIEDUC para os IDs correspondentes no MySQL.

    Os dois sistemas (PostgreSQL/TRIEDUC e MySQL) usam IDs independentes para a
    mesma habilidade. O /habilidades usa mapeamento via descrição para contar
    corretamente; este helper aplica a mesma lógica nos endpoints /proxima.
    """
    hab_desc_row = (
        pg_db.query(HabilidadeModuloModel.habilidade_descricao)
        .filter(HabilidadeModuloModel.habilidade_id == trieduc_habilidade_id)
        .first()
    )
    if not hab_desc_row or not hab_desc_row[0]:
        return [trieduc_habilidade_id]

    mysql_ids = [
        r[0]
        for r in db.query(HabilidadeModel.id)
        .filter(func.lower(HabilidadeModel.descricao) == hab_desc_row[0].lower())
        .all()
    ]
    return mysql_ids if mysql_ids else [trieduc_habilidade_id]


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
    "Artes",
    "Biologia",
    "Ciências",
    "Educação Física",
    "Espanhol",
    "Filosofia",
    "Física",
    "Geografia",
    "História",
    "Língua Inglesa",
    "Língua Portuguesa",
    "Literatura",
    "Matemática",
    "Natureza e Sociedade",
    "Química",
    "Redação",
    "Sociologia",
    # Áreas
    "Humanas",
    "Linguagens",
    "Natureza",
]

# Mapeamento para o MySQL (onde os nomes podem ser diferentes do Postgres/Planilha)
MAP_DISCIPLINAS_MYSQL = {
    "Artes": "Artes",
    "Língua Inglesa": "Língua Inglesa",
    "Língua Portuguesa": "Língua Portuguesa",
    "Literatura": None,  # Não existe no MySQL
    "Redação": None,  # Não existe no MySQL
}

# Mapeamento de áreas para filtro
AREAS_DISCIPLINAS = {
    "Humanas": ["Filosofia", "Geografia", "História", "Sociologia"],
    "Linguagens": [
        "Artes",
        "Educação Física",
        "Espanhol",
        "Língua Inglesa",
        "Língua Portuguesa",
        "Literatura",
        "Redação",
    ],
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
    return bcrypt.checkpw(senha_plain.encode("utf-8"), senha_hash.encode("utf-8"))


def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


async def get_usuario_atual(
    token: str = Depends(oauth2_scheme),
    pg_db: Session = Depends(get_db),
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
    pg_db: Session = Depends(get_db),
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
    existente = (
        pg_db.query(UsuarioModel).filter(UsuarioModel.email == request.email).first()
    )
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
    pg_db: Session = Depends(get_db),
):
    """Autentica o usuário e retorna um token JWT."""
    usuario = (
        pg_db.query(UsuarioModel).filter(UsuarioModel.email == request.email).first()
    )
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
    disciplina: Optional[str] = Query(
        None, description="Filtrar por nome da disciplina"
    ),
    pg_db: Session = Depends(get_db),
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
        HabilidadeModuloModel.habilidade_id, HabilidadeModuloModel.habilidade_descricao
    ).distinct()

    if area:
        query = query.filter(HabilidadeModuloModel.area == area)
    if disciplina:
        mapping = {
            "Artes": ["Artes", "Arte"],
            "Língua Inglesa": ["Língua Inglesa", "Inglês"],
            "Língua Portuguesa": [
                "Língua Portuguesa",
                "Lingua Portuguesa",
                "Literatura",
                "Redação",
            ],
        }
        mapped_names = mapping.get(disciplina, [disciplina])
        query = query.filter(HabilidadeModuloModel.disciplina.in_(mapped_names))

    results = query.order_by(HabilidadeModuloModel.habilidade_descricao).all()

    # Montar mapa de habilidades válidas
    hab_ids = [r.habilidade_id for r in results if r.habilidade_id is not None]

    # Mapa descrição (lowercase) → habilidade_id TRIEDUC
    desc_lower_to_trieduc: dict[str, int] = {}
    for r in results:
        if r.habilidade_id is not None and r.habilidade_descricao:
            desc_lower_to_trieduc[r.habilidade_descricao.lower()] = r.habilidade_id

    # Bridge: buscar IDs MySQL correspondentes via descrição (case-insensitive)
    # habilidade_modulos usa IDs TRIEDUC; questoes usa IDs MySQL — sistemas diferentes
    mysql_to_trieduc: dict[int, int] = {}
    if desc_lower_to_trieduc:
        mysql_hab_rows = (
            db.query(HabilidadeModel.id, HabilidadeModel.descricao)
            .filter(
                func.lower(HabilidadeModel.descricao).in_(
                    list(desc_lower_to_trieduc.keys())
                )
            )
            .all()
        )
        for mysql_id, mysql_desc in mysql_hab_rows:
            if mysql_desc:
                trieduc_id = desc_lower_to_trieduc.get(mysql_desc.lower())
                if trieduc_id:
                    mysql_to_trieduc[mysql_id] = trieduc_id

    # Fallback: só usar TRIEDUC ID como MySQL ID quando não houve mapeamento via descrição.
    # Se já existe um MySQL ID mapeado para este TRIEDUC ID, não adicionar o TRIEDUC ID
    # como MySQL ID extra (evita dupla contagem de questões com IDs distintos).
    trieduc_ids_ja_mapeados = set(mysql_to_trieduc.values())
    for trieduc_id in hab_ids:
        if trieduc_id not in trieduc_ids_ja_mapeados:
            mysql_to_trieduc[trieduc_id] = trieduc_id

    mysql_hab_ids = list(mysql_to_trieduc.keys())

    # IDs excluídos no PG (queries leves, sem IN gigante)
    ids_excluir: set[int] = set()

    # 2a. Já classificadas manualmente, com low-match, ou pelo SuperPro
    for r in (
        pg_db.query(QuestaoAssuntoModel.questao_id)
        .filter(
            (QuestaoAssuntoModel.classificado_manualmente == True)
            | (
                (QuestaoAssuntoModel.classificacao_nao_enquadrada.isnot(None))
                & (
                    func.json_length(QuestaoAssuntoModel.classificacao_nao_enquadrada)
                    > 0
                )
            )
            | (
                (QuestaoAssuntoModel.extracao_feita == True)
                & (QuestaoAssuntoModel.classificacoes.isnot(None))
                & (func.json_length(QuestaoAssuntoModel.classificacoes) > 0)
            )
        )
        .all()
    ):
        ids_excluir.add(r[0])

    # 2b. Já classificadas por este usuário
    for r in (
        pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.usuario_id == usuario.id)
        .all()
    ):
        ids_excluir.add(r[0])

    # 2c. Puladas por qualquer usuário — só aparecem em Pendentes, nunca em /proxima
    for r in pg_db.query(QuestaoPuladaModel.questao_id).all():
        ids_excluir.add(r[0])

    counts_map: dict[int, int] = {}  # trieduc_id → pendentes
    if mysql_hab_ids:
        # Etapa 1: total de questões por habilidade MySQL (GROUP BY)
        rows_total = (
            db.query(
                QuestaoModel.habilidade_id,
                func.count(QuestaoModel.id).label("total"),
            )
            .filter(
                QuestaoModel.habilidade_id.in_(mysql_hab_ids),
                QuestaoModel.ano_id == 3,
            )
            .group_by(QuestaoModel.habilidade_id)
            .all()
        )

        # Agrupar por trieduc_id (vários MySQL IDs podem mapear para o mesmo)
        total_por_trieduc: dict[int, int] = {}
        for mysql_id, count in rows_total:
            trieduc_id = mysql_to_trieduc.get(mysql_id)
            if trieduc_id:
                total_por_trieduc[trieduc_id] = (
                    total_por_trieduc.get(trieduc_id, 0) + count
                )

        # Etapa 2: contagem de excluídas por habilidade no MySQL
        excluido_por_trieduc: dict[int, int] = {}
        if ids_excluir and total_por_trieduc:
            rows_excluido = (
                db.query(
                    QuestaoModel.habilidade_id,
                    func.count(QuestaoModel.id).label("excluidos"),
                )
                .filter(
                    QuestaoModel.id.in_(list(ids_excluir)),
                    QuestaoModel.habilidade_id.in_(mysql_hab_ids),
                    QuestaoModel.ano_id == 3,
                )
                .group_by(QuestaoModel.habilidade_id)
                .all()
            )
            for mysql_id, count in rows_excluido:
                trieduc_id = mysql_to_trieduc.get(mysql_id)
                if trieduc_id:
                    excluido_por_trieduc[trieduc_id] = (
                        excluido_por_trieduc.get(trieduc_id, 0) + count
                    )

        # Etapa 3: calcular pendentes (Python puro, O(n))
        for trieduc_id, total in total_por_trieduc.items():
            excluidos = excluido_por_trieduc.get(trieduc_id, 0)
            pendentes = total - excluidos
            if pendentes > 0:
                counts_map[trieduc_id] = pendentes

    if not counts_map:
        res = HabilidadesFiltroResponse(habilidades=[], total=0)
        set_to_cache(cache_key, res)
        return res

    habilidades = []
    for r in results:
        if r.habilidade_id is not None:
            pendentes = counts_map.get(r.habilidade_id, 0)
            if pendentes > 0:
                habilidades.append(
                    HabilidadeFiltroSchema(
                        habilidade_id=r.habilidade_id,
                        habilidade_descricao=r.habilidade_descricao,
                        pendentes=pendentes,
                    )
                )

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
    disciplina: Optional[str] = Query(
        None, description="Filtrar por nome da disciplina"
    ),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna apenas os assuntos (habilidades) que possuem questões puladas (pendentes),
    com a contagem de quantas existem. Respeita o filtro de área/disciplina do usuário.
    """
    effective_area = area or (usuario.disciplina if not usuario.is_admin else None)

    # IDs já classificados por este usuário (excluir das contagens)
    ids_classificadas: set[int] = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
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
            disc_id_row = (
                db.query(DisciplinaModel.id)
                .filter(DisciplinaModel.descricao == mysql_name)
                .first()
            )
            if disc_id_row:
                query_puladas = query_puladas.filter(
                    QuestaoPuladaModel.disciplina_id == disc_id_row[0]
                )
            else:
                return HabilidadesFiltroResponse(habilidades=[], total=0)
        else:
            habilidade_ids_custom = [
                row[0]
                for row in pg_db.query(HabilidadeModuloModel.habilidade_id)
                .filter(HabilidadeModuloModel.disciplina == disciplina)
                .filter(HabilidadeModuloModel.habilidade_id.isnot(None))
                .distinct()
                .all()
            ]
            if habilidade_ids_custom:
                query_puladas = query_puladas.filter(
                    QuestaoPuladaModel.habilidade_id.in_(habilidade_ids_custom)
                )
            else:
                return HabilidadesFiltroResponse(habilidades=[], total=0)
    elif effective_area and effective_area in AREAS_DISCIPLINAS:
        nomes = AREAS_DISCIPLINAS[effective_area]
        discs_ids = [
            d[0]
            for d in db.query(DisciplinaModel.id)
            .filter(DisciplinaModel.descricao.in_(nomes))
            .all()
        ]
        if discs_ids:
            query_puladas = query_puladas.filter(
                QuestaoPuladaModel.disciplina_id.in_(discs_ids)
            )
    elif effective_area:
        query_puladas = query_puladas.filter(QuestaoPuladaModel.area == effective_area)

    rows = query_puladas.group_by(QuestaoPuladaModel.habilidade_id).all()
    if not rows:
        return HabilidadesFiltroResponse(habilidades=[], total=0)

    counts: dict[int, int] = {r[0]: r[1] for r in rows}
    hab_ids = list(counts.keys())

    # Buscar descrições no habilidade_modulos (PostgreSQL)
    desc_rows = (
        pg_db.query(
            HabilidadeModuloModel.habilidade_id,
            HabilidadeModuloModel.habilidade_descricao,
        )
        .filter(HabilidadeModuloModel.habilidade_id.in_(hab_ids))
        .distinct()
        .all()
    )

    # Mapa de descrições encontradas no HabilidadeModuloModel
    desc_map: dict[int, str] = {}
    for r in desc_rows:
        if r.habilidade_id not in desc_map:
            desc_map[r.habilidade_id] = r.habilidade_descricao

    # Fallback: buscar no MySQL (HabilidadeModel) os IDs que não existem em habilidade_modulos
    ids_sem_modulo = [hid for hid in hab_ids if hid not in desc_map]
    if ids_sem_modulo:
        fallback_rows = (
            db.query(HabilidadeModel.id, HabilidadeModel.descricao)
            .filter(HabilidadeModel.id.in_(ids_sem_modulo))
            .all()
        )
        for r in fallback_rows:
            if r.descricao:
                desc_map[r.id] = r.descricao

    habilidades = []
    for hab_id, descricao in sorted(desc_map.items(), key=lambda x: x[1]):
        habilidades.append(
            HabilidadeFiltroSchema(
                habilidade_id=hab_id,
                habilidade_descricao=descricao,
                pendentes=counts[hab_id],
            )
        )

    return HabilidadesFiltroResponse(habilidades=habilidades, total=len(habilidades))


@router.get(
    "/habilidades-verificar",
    response_model=HabilidadesFiltroResponse,
    summary="🔍 Assuntos com questões de baixa similaridade para verificar",
)
async def listar_habilidades_verificar(
    area: Optional[str] = Query(None, description="Filtrar por área"),
    disciplina: Optional[str] = Query(
        None, description="Filtrar por nome da disciplina"
    ),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna apenas assuntos TRIEDUC com pendências de verificação (low-match)."""
    cache_key = f"habilidades_verificar_{area}_{disciplina}_{usuario.id}"
    cached_data = get_from_cache(cache_key, ttl=300)
    if cached_data:
        return cached_data

    effective_area = area or (usuario.disciplina if not usuario.is_admin else None)

    ids_verificadas_usuario: set[int] = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.usuario_id == usuario.id)
        .all()
    }

    query_low_match_ids = pg_db.query(QuestaoAssuntoModel.questao_id).filter(
        QuestaoAssuntoModel.classificado_manualmente == False,
        QuestaoAssuntoModel.classificacao_nao_enquadrada.isnot(None),
        func.json_length(QuestaoAssuntoModel.classificacao_nao_enquadrada) > 0,
        QuestaoAssuntoModel.similaridade.isnot(None),
        QuestaoAssuntoModel.similaridade > 0,
        QuestaoAssuntoModel.similaridade < 0.8,
    )

    if ids_verificadas_usuario:
        query_low_match_ids = query_low_match_ids.filter(
            ~QuestaoAssuntoModel.questao_id.in_(list(ids_verificadas_usuario))
        )

    questao_ids_candidatas = [r[0] for r in query_low_match_ids.all()]
    if not questao_ids_candidatas:
        empty_res = HabilidadesFiltroResponse(habilidades=[], total=0)
        set_to_cache(cache_key, empty_res)
        return empty_res

    query_mysql = db.query(
        QuestaoModel.habilidade_id,
        func.count(func.distinct(QuestaoModel.id)).label("total"),
    ).filter(
        QuestaoModel.id.in_(questao_ids_candidatas),
        QuestaoModel.ano_id == 3,
        QuestaoModel.habilidade_id.isnot(None),
    )

    # Filtro por disciplina
    if disciplina:
        mysql_name = MAP_DISCIPLINAS_MYSQL.get(disciplina, disciplina)
        if mysql_name:
            disc_id_row = (
                db.query(DisciplinaModel.id)
                .filter(DisciplinaModel.descricao == mysql_name)
                .first()
            )
            if not disc_id_row:
                empty_res = HabilidadesFiltroResponse(habilidades=[], total=0)
                set_to_cache(cache_key, empty_res)
                return empty_res
            query_mysql = query_mysql.filter(
                QuestaoModel.disciplina_id == disc_id_row[0]
            )
        else:
            habilidade_ids_custom = [
                row[0]
                for row in pg_db.query(HabilidadeModuloModel.habilidade_id)
                .filter(HabilidadeModuloModel.disciplina == disciplina)
                .filter(HabilidadeModuloModel.habilidade_id.isnot(None))
                .distinct()
                .all()
            ]
            if not habilidade_ids_custom:
                empty_res = HabilidadesFiltroResponse(habilidades=[], total=0)
                set_to_cache(cache_key, empty_res)
                return empty_res
            query_mysql = query_mysql.filter(
                QuestaoModel.habilidade_id.in_(habilidade_ids_custom)
            )
    elif effective_area and effective_area in AREAS_DISCIPLINAS:
        nomes = AREAS_DISCIPLINAS[effective_area]
        disciplinas_ids = [
            d[0]
            for d in db.query(DisciplinaModel.id)
            .filter(DisciplinaModel.descricao.in_(nomes))
            .all()
        ]
        if not disciplinas_ids:
            empty_res = HabilidadesFiltroResponse(habilidades=[], total=0)
            set_to_cache(cache_key, empty_res)
            return empty_res
        query_mysql = query_mysql.filter(
            QuestaoModel.disciplina_id.in_(disciplinas_ids)
        )
    elif effective_area:
        habilidade_ids_area = [
            row[0]
            for row in pg_db.query(HabilidadeModuloModel.habilidade_id)
            .filter(HabilidadeModuloModel.area == effective_area)
            .filter(HabilidadeModuloModel.habilidade_id.isnot(None))
            .distinct()
            .all()
        ]
        if not habilidade_ids_area:
            empty_res = HabilidadesFiltroResponse(habilidades=[], total=0)
            set_to_cache(cache_key, empty_res)
            return empty_res
        query_mysql = query_mysql.filter(
            QuestaoModel.habilidade_id.in_(habilidade_ids_area)
        )

    rows = query_mysql.group_by(QuestaoModel.habilidade_id).all()
    if not rows:
        empty_res = HabilidadesFiltroResponse(habilidades=[], total=0)
        set_to_cache(cache_key, empty_res)
        return empty_res

    counts_map: dict[int, int] = {r[0]: int(r[1]) for r in rows if r[0] is not None}
    hab_ids = list(counts_map.keys())

    descricao_map: dict[int, str] = {}
    descricao_rows = (
        pg_db.query(
            HabilidadeModuloModel.habilidade_id,
            HabilidadeModuloModel.habilidade_descricao,
        )
        .filter(HabilidadeModuloModel.habilidade_id.in_(hab_ids))
        .distinct()
        .all()
    )
    for row in descricao_rows:
        if row.habilidade_id not in descricao_map and row.habilidade_descricao:
            descricao_map[row.habilidade_id] = row.habilidade_descricao

    faltantes = [hid for hid in hab_ids if hid not in descricao_map]
    if faltantes:
        fallback_rows = (
            db.query(HabilidadeModel.id, HabilidadeModel.descricao)
            .filter(HabilidadeModel.id.in_(faltantes))
            .all()
        )
        for hid, desc in fallback_rows:
            if hid not in descricao_map and desc:
                descricao_map[hid] = desc

    habilidades = [
        HabilidadeFiltroSchema(
            habilidade_id=hid,
            habilidade_descricao=descricao_map.get(hid, f"Habilidade {hid}"),
            pendentes=counts_map[hid],
        )
        for hid in sorted(
            hab_ids,
            key=lambda h: descricao_map.get(h, f"Habilidade {h}").lower(),
        )
    ]

    res = HabilidadesFiltroResponse(habilidades=habilidades, total=len(habilidades))
    set_to_cache(cache_key, res)
    return res


# ========================
# CONTAGEM POR DISCIPLINA (filas alta sim + confirmações)
# ========================


@router.get(
    "/contagem-filas",
    summary="📊 Contagem de pendentes por disciplina (alta similaridade e confirmações)",
)
async def contagem_filas_por_disciplina(
    db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna contagem de questões pendentes por disciplina para as duas filas:
    alta_similaridade (similaridade >= 0.8, não classificadas, sem 4 alt) e
    confirmacoes (tipo_acao='confirmacao' sem reclassificação posterior, sem 4 alt).
    """
    cache_key = "contagem_filas_v1"
    cached = get_from_cache(cache_key, ttl=120)
    if cached is not None:
        return cached

    rows_sim = db.execute(sql_text("""
        SELECT d.descricao, COUNT(*) AS total
        FROM thsethub.questao_assuntos qa
        JOIN trieduc.questoes q ON q.id = qa.questao_id
        JOIN trieduc.disciplinas d ON d.id = q.disciplina_id
        LEFT JOIN (
            SELECT questao_id, COUNT(*) AS n
            FROM trieduc.questao_alternativas GROUP BY questao_id
        ) alt ON alt.questao_id = q.id
        WHERE qa.similaridade >= 0.8
          AND qa.extracao_feita = 1
          AND qa.classificado_manualmente = 0
          AND (alt.n IS NULL OR alt.n != 4)
        GROUP BY d.descricao
    """)).fetchall()

    rows_conf = db.execute(sql_text("""
        SELECT d.descricao, COUNT(DISTINCT cu.questao_id) AS total
        FROM thsethub.classificacao_usuario cu
        JOIN trieduc.questoes q ON q.id = cu.questao_id
        JOIN trieduc.disciplinas d ON d.id = q.disciplina_id
        LEFT JOIN (
            SELECT questao_id, COUNT(*) AS n
            FROM trieduc.questao_alternativas GROUP BY questao_id
        ) alt ON alt.questao_id = q.id
        WHERE cu.tipo_acao = 'confirmacao'
          AND cu.questao_id NOT IN (
              SELECT questao_id FROM thsethub.classificacao_usuario
              WHERE tipo_acao IN ('classificacao_nova', 'correcao')
          )
          AND (alt.n IS NULL OR alt.n != 4)
        GROUP BY d.descricao
    """)).fetchall()

    result = {
        "alta_similaridade": {r[0]: r[1] for r in rows_sim},
        "confirmacoes": {r[0]: r[1] for r in rows_conf},
    }
    set_to_cache(cache_key, result)
    return result


# ========================
# MÓDULOS LIBRO DIRETO (compartilhados)
# ========================


@router.get(
    "/modulos-libro-direto",
    summary="📚 Módulos Libro com assuntos direto do banco compartilhados",
)
async def listar_modulos_libro_direto(
    db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna todos os módulos Libro com seus assuntos, diretamente das tabelas
    compartilhados.disciplinas_modulos e compartilhados.assuntos.
    Exclui itens prefixados com [RM]. Usado na classificação de alta similaridade.
    """
    cache_key = "modulos_libro_direto_v1"
    cached = get_from_cache(cache_key, ttl=600)
    if cached is not None:
        return cached

    rows = db.execute(sql_text("""
        SELECT
            d.disc_id,
            d.disc_descricao AS disciplina,
            dm.disc_modu_id,
            dm.disc_modu_descricao AS modulo,
            a.assu_id,
            a.assu_descricao AS assunto
        FROM compartilhados.disciplinas_modulos dm
        JOIN compartilhados.disciplinas d ON d.disc_id = dm.disc_id
        JOIN compartilhados.assuntos a ON a.disc_modu_id = dm.disc_modu_id
        WHERE dm.disc_modu_descricao NOT LIKE '[RM]%'
          AND a.assu_descricao NOT LIKE '[RM]%'
        ORDER BY d.disc_descricao, dm.disc_modu_descricao, a.assu_descricao
    """)).fetchall()

    # Montar estrutura: disciplina → módulos → assuntos
    disc_map: dict = {}
    for row in rows:
        disc_id = row[0]
        disciplina = row[1]
        disc_modu_id = row[2]
        modulo = row[3]
        assu_id = row[4]
        assunto = row[5]

        if disc_id not in disc_map:
            disc_map[disc_id] = {
                "disc_id": disc_id,
                "disciplina": disciplina,
                "modulos": {},
            }

        if disc_modu_id not in disc_map[disc_id]["modulos"]:
            disc_map[disc_id]["modulos"][disc_modu_id] = {
                "disc_modu_id": disc_modu_id,
                "modulo": modulo,
                "assuntos": [],
            }

        disc_map[disc_id]["modulos"][disc_modu_id]["assuntos"].append(
            {
                "assu_id": assu_id,
                "assunto": assunto,
            }
        )

    result = [
        {
            "disc_id": d["disc_id"],
            "disciplina": d["disciplina"],
            "modulos": list(d["modulos"].values()),
        }
        for d in disc_map.values()
    ]

    set_to_cache(cache_key, result)
    return result


# ========================
# ALTA SIMILARIDADE (>= 0.8)
# ========================


@router.get(
    "/assuntos-superpro",
    summary="📋 Assuntos SuperProfessor disponíveis (similaridade >= 0.8)",
)
async def listar_assuntos_superpro(
    disciplina_id: Optional[str] = Query(
        None, description="Nome da disciplina para filtrar"
    ),
    pg_db: Session = Depends(get_db),
    db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna assuntos únicos (primeiro elemento de classificacoes[]) das questões
    com similaridade >= 0.8, não classificadas manualmente, sem 4 alternativas.
    Usado para popular o dropdown de filtros na página de alta similaridade.
    """
    cache_key = f"assuntos_superpro_{disciplina_id}"
    cached = get_from_cache(cache_key, ttl=300)
    if cached is not None:
        return cached

    disc_mysql_id = None
    if disciplina_id:
        if str(disciplina_id).isdigit():
            disc_mysql_id = int(disciplina_id)
        else:
            mysql_name = MAP_DISCIPLINAS_MYSQL.get(disciplina_id, disciplina_id)
            if mysql_name:
                disc_row = (
                    db.query(DisciplinaModel.id)
                    .filter(DisciplinaModel.descricao == mysql_name)
                    .first()
                )
                disc_mysql_id = disc_row[0] if disc_row else None

    disc_filter = f"AND q.disciplina_id = {disc_mysql_id}" if disc_mysql_id else ""

    raw_sql = sql_text(f"""
        SELECT
            JSON_UNQUOTE(JSON_EXTRACT(qa.classificacoes, '$[0]')) AS assunto,
            COUNT(*) AS total
        FROM thsethub.questao_assuntos qa
        JOIN trieduc.questoes q ON q.id = qa.questao_id
        LEFT JOIN (
            SELECT questao_id, COUNT(*) AS n_alt
            FROM trieduc.questao_alternativas
            GROUP BY questao_id
        ) alt_cnt ON alt_cnt.questao_id = qa.questao_id
        WHERE qa.similaridade >= 0.8
          AND qa.extracao_feita = 1
          AND qa.classificado_manualmente = 0
          AND JSON_LENGTH(qa.classificacoes) > 0
          AND (alt_cnt.n_alt IS NULL OR alt_cnt.n_alt != 4)
          {disc_filter}
        GROUP BY assunto
        HAVING assunto IS NOT NULL AND assunto != 'null'
        ORDER BY total DESC
        LIMIT 500
    """)

    rows = db.execute(raw_sql).fetchall()
    result = [{"assunto": r[0], "total": r[1]} for r in rows]
    set_to_cache(cache_key, result)
    return result


@router.get(
    "/proxima-alta-similaridade",
    response_model=QuestaoClassifResponse,
    summary="🔍 Próxima questão com similaridade >= 0.8 para classificar",
)
async def proxima_questao_alta_similaridade(
    assunto_superpro: Optional[str] = Query(
        None, description="Filtrar pelo primeiro assunto superprofessor"
    ),
    disciplina_id: Optional[str] = Query(None, description="Nome ou ID da disciplina"),
    last_questao_id: Optional[int] = Query(
        0, description="Último questao_id visto (seek)"
    ),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna a próxima questão com similaridade >= 0.8 ainda não classificada manualmente.
    Exclui questões com 4 alternativas (múltipla escolha com 4 opções).
    """
    LIMIT_CANDIDATES = 100
    MAX_LOOP_TRIES = 50

    disc_mysql_id = None
    if disciplina_id:
        if str(disciplina_id).isdigit():
            disc_mysql_id = int(disciplina_id)
        else:
            mysql_name = MAP_DISCIPLINAS_MYSQL.get(disciplina_id, disciplina_id)
            if mysql_name:
                disc_row = (
                    db.query(DisciplinaModel.id)
                    .filter(DisciplinaModel.descricao == mysql_name)
                    .first()
                )
                disc_mysql_id = disc_row[0] if disc_row else None

    last_id = last_questao_id or 0

    # IDs que o usuário atual já pulou — serão ignorados na fila
    puladas_usuario = {
        r[0]
        for r in pg_db.query(QuestaoPuladaModel.questao_id)
        .filter(QuestaoPuladaModel.usuario_id == usuario.id)
        .all()
    }

    qa_query = (
        pg_db.query(QuestaoAssuntoModel.questao_id)
        .filter(QuestaoAssuntoModel.similaridade >= 0.8)
        .filter(QuestaoAssuntoModel.extracao_feita == True)
        .filter(QuestaoAssuntoModel.classificado_manualmente == False)
        .filter(QuestaoAssuntoModel.classificacoes.isnot(None))
        .filter(func.json_length(QuestaoAssuntoModel.classificacoes) > 0)
    )

    if assunto_superpro:
        qa_query = qa_query.filter(
            func.json_unquote(
                func.json_extract(QuestaoAssuntoModel.classificacoes, "$[0]")
            )
            == assunto_superpro
        )

    qa_query = qa_query.order_by(QuestaoAssuntoModel.questao_id)

    questao_final = None

    for _ in range(MAX_LOOP_TRIES):
        candidates_qa = (
            qa_query.filter(QuestaoAssuntoModel.questao_id > last_id)
            .limit(LIMIT_CANDIDATES)
            .all()
        )
        if not candidates_qa:
            break

        candidate_ids = [c[0] for c in candidates_qa]
        last_id = candidate_ids[-1]

        # Filtrar por disciplina no MySQL
        if disc_mysql_id:
            valid_mysql = {
                r[0]
                for r in db.query(QuestaoModel.id)
                .filter(QuestaoModel.id.in_(candidate_ids))
                .filter(QuestaoModel.disciplina_id == disc_mysql_id)
                .all()
            }
            candidate_ids = [c for c in candidate_ids if c in valid_mysql]

        if not candidate_ids:
            continue

        # Excluir questões com 4 alternativas
        from ..database.models import QuestaoAlternativaModel

        alt_counts = {
            r[0]: r[1]
            for r in db.query(
                QuestaoAlternativaModel.questao_id,
                func.count(QuestaoAlternativaModel.id),
            )
            .filter(QuestaoAlternativaModel.questao_id.in_(candidate_ids))
            .group_by(QuestaoAlternativaModel.questao_id)
            .all()
        }
        candidate_ids = [c for c in candidate_ids if alt_counts.get(c, 0) != 4]

        # Excluir questões que o usuário já pulou
        if puladas_usuario:
            candidate_ids = [c for c in candidate_ids if c not in puladas_usuario]

        if not candidate_ids:
            continue

        valid_id = candidate_ids[0]

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
            break

    if not questao_final:
        raise HTTPException(
            status_code=404,
            detail="Nenhuma questão de alta similaridade pendente encontrada.",
        )

    questao = questao_final
    enunciado_tratado, _, _ = tratar_enunciado(questao.enunciado)
    texto_base_tratado = None
    if questao.texto_base:
        texto_base_tratado, _, _ = tratar_enunciado(questao.texto_base)

    extracao = (
        pg_db.query(QuestaoAssuntoModel)
        .filter(QuestaoAssuntoModel.questao_id == questao.id)
        .first()
    )

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
    hab_descricao = questao.habilidade.descricao if questao.habilidade else None

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
        classificacao_extracao=extracao.classificacoes if extracao else None,
        classificacao_nao_enquadrada=(
            extracao.classificacao_nao_enquadrada if extracao else None
        ),
        similaridade=extracao.similaridade if extracao else None,
        tem_extracao=bool(extracao and extracao.extracao_feita),
        modulos_possiveis=[],
    )


@router.get(
    "/proxima-confirmacao",
    response_model=QuestaoClassifResponse,
    summary="🔁 Próxima questão com apenas confirmação (sem módulos libro)",
)
async def proxima_questao_confirmacao(
    disciplina_id: Optional[str] = Query(None, description="Nome ou ID da disciplina"),
    last_questao_id: Optional[int] = Query(
        0, description="Último questao_id visto (seek)"
    ),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna questões que foram apenas 'confirmadas' sem módulos libro selecionados,
    e que não tiveram reclassificação posterior (classificacao_nova ou correcao).
    Exclui questões com 4 alternativas.
    """
    LIMIT_CANDIDATES = 100
    MAX_LOOP_TRIES = 50

    disc_mysql_id = None
    if disciplina_id:
        if str(disciplina_id).isdigit():
            disc_mysql_id = int(disciplina_id)
        else:
            mysql_name = MAP_DISCIPLINAS_MYSQL.get(disciplina_id, disciplina_id)
            if mysql_name:
                disc_row = (
                    db.query(DisciplinaModel.id)
                    .filter(DisciplinaModel.descricao == mysql_name)
                    .first()
                )
                disc_mysql_id = disc_row[0] if disc_row else None

    # IDs com reclassificação posterior (excluir) — inclui classificacao_libro
    reclassificados = {
        r[0]
        for r in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(
            ClassificacaoUsuarioModel.tipo_acao.in_(
                ["classificacao_nova", "correcao", "classificacao_libro"]
            )
        )
        .all()
    }

    # IDs que o usuário atual já pulou — serão ignorados na fila
    puladas_usuario = {
        r[0]
        for r in pg_db.query(QuestaoPuladaModel.questao_id)
        .filter(QuestaoPuladaModel.usuario_id == usuario.id)
        .all()
    }

    # IDs confirmados (candidatos)
    confirmados_query = (
        pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.tipo_acao == "confirmacao")
        .filter(ClassificacaoUsuarioModel.questao_id.notin_(reclassificados))
        .distinct()
        .order_by(ClassificacaoUsuarioModel.questao_id)
    )

    last_id = last_questao_id or 0
    questao_final = None

    for _ in range(MAX_LOOP_TRIES):
        candidates = (
            confirmados_query.filter(ClassificacaoUsuarioModel.questao_id > last_id)
            .limit(LIMIT_CANDIDATES)
            .all()
        )
        if not candidates:
            break

        candidate_ids = [c[0] for c in candidates]
        last_id = candidate_ids[-1]

        # Filtrar por disciplina no MySQL
        if disc_mysql_id:
            valid_mysql = {
                r[0]
                for r in db.query(QuestaoModel.id)
                .filter(QuestaoModel.id.in_(candidate_ids))
                .filter(QuestaoModel.disciplina_id == disc_mysql_id)
                .all()
            }
            candidate_ids = [c for c in candidate_ids if c in valid_mysql]

        if not candidate_ids:
            continue

        # Excluir questões com 4 alternativas
        from ..database.models import QuestaoAlternativaModel

        alt_counts = {
            r[0]: r[1]
            for r in db.query(
                QuestaoAlternativaModel.questao_id,
                func.count(QuestaoAlternativaModel.id),
            )
            .filter(QuestaoAlternativaModel.questao_id.in_(candidate_ids))
            .group_by(QuestaoAlternativaModel.questao_id)
            .all()
        }
        candidate_ids = [c for c in candidate_ids if alt_counts.get(c, 0) != 4]

        # Excluir questões que o usuário já pulou
        if puladas_usuario:
            candidate_ids = [c for c in candidate_ids if c not in puladas_usuario]

        if not candidate_ids:
            continue

        valid_id = candidate_ids[0]

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
            break

    if not questao_final:
        raise HTTPException(
            status_code=404,
            detail="Nenhuma questão de confirmação pendente encontrada.",
        )

    questao = questao_final
    enunciado_tratado, _, _ = tratar_enunciado(questao.enunciado)
    texto_base_tratado = None
    if questao.texto_base:
        texto_base_tratado, _, _ = tratar_enunciado(questao.texto_base)

    extracao = (
        pg_db.query(QuestaoAssuntoModel)
        .filter(QuestaoAssuntoModel.questao_id == questao.id)
        .first()
    )

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
    hab_descricao = questao.habilidade.descricao if questao.habilidade else None

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
        classificacao_extracao=extracao.classificacoes if extracao else None,
        classificacao_nao_enquadrada=(
            extracao.classificacao_nao_enquadrada if extracao else None
        ),
        similaridade=extracao.similaridade if extracao else None,
        tem_extracao=bool(extracao and extracao.extracao_feita),
        modulos_possiveis=[],
    )


# ========================
# MÓDULOS (consulta)
# ========================


@router.get(
    "/modulos",
    response_model=List[HabilidadeModuloSchema],
    summary="📦 Todos os módulos disponíveis para seleção manual",
)
async def listar_todos_modulos(
    disciplina: Optional[str] = Query(
        None, description="Filtrar por nome da disciplina"
    ),
    area: Optional[str] = Query(None, description="Filtrar por área"),
    pg_db: Session = Depends(get_db),
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

    modulos = query.order_by(
        HabilidadeModuloModel.area,
        HabilidadeModuloModel.disciplina,
        HabilidadeModuloModel.modulo,
        HabilidadeModuloModel.descricao,
    ).all()
    result = [HabilidadeModuloSchema.model_validate(m) for m in modulos]
    set_to_cache(cache_key, result)
    return result


@router.get(
    "/modulos-assuntos",
    response_model=ModulosAssuntosResponse,
    summary="📚 Módulos com assuntos relacionados (sem prefixo [RM])",
)
async def listar_modulos_com_assuntos(
    shared_db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna módulos do banco compartilhados com os assuntos relacionados válidos.

    Retorna apenas os módulos do LibroStudio sem relacionamento atual com o TriEduc.
    """
    cache_key = "modulos_assuntos_compartilhados_v4"
    cached = get_from_cache(cache_key, ttl=600)
    if cached is not None:
        return cached

    try:
        inspector = inspect(shared_db.get_bind())
        table_names = inspector.get_table_names(schema="compartilhados")
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

        disciplinas_table = None
        for candidate in ["disciplinas", "disciplina"]:
            if candidate in table_names_lower:
                disciplinas_table = table_names_lower[candidate]
                break

        if not disciplinas_table:
            for t in table_names:
                t_norm = t.lower()
                if "disciplina" in t_norm and "modul" not in t_norm:
                    disciplinas_table = t
                    break

        if not assuntos_table:
            raise HTTPException(
                status_code=500,
                detail="Tabela de assuntos não encontrada em compartilhados.",
            )
        if not modulos_table:
            raise HTTPException(
                status_code=500,
                detail="Tabela de módulos de disciplina não encontrada em compartilhados.",
            )

        assuntos_cols = {
            c["name"]
            for c in inspector.get_columns(assuntos_table, schema="compartilhados")
        }
        modulos_cols = {
            c["name"]
            for c in inspector.get_columns(modulos_table, schema="compartilhados")
        }
        disciplinas_cols = (
            {
                c["name"]
                for c in inspector.get_columns(
                    disciplinas_table, schema="compartilhados"
                )
            }
            if disciplinas_table
            else set()
        )

        assunto_desc_col = _pick_first_column(
            assuntos_cols, ["assu_descricao", "descricao", "nome"]
        )
        if not assunto_desc_col:
            raise HTTPException(
                status_code=500,
                detail="Coluna de descrição de assunto não encontrada na tabela 'assuntos'.",
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
                detail=(
                    "Coluna de nome de módulo não encontrada. "
                    f"Colunas disponíveis: {sorted(modulos_cols)}"
                ),
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

        disciplina_id_fk_col = _pick_first_column(
            modulos_cols,
            [
                "disc_id",
                "disciplina_id",
                "id_disciplina",
                "disciplinaid",
                "id_disc",
            ],
        )
        disciplina_id_col = _pick_first_column(
            disciplinas_cols,
            [
                "disc_id",
                "id",
                "disciplina_id",
                "id_disciplina",
            ],
        )
        if not disciplina_id_col and disciplinas_cols:
            disciplina_id_col = next(
                (
                    c
                    for c in disciplinas_cols
                    if "disc" in c.lower() and c.lower().endswith("id")
                ),
                None,
            )
        if not disciplina_id_col and disciplinas_cols:
            disciplina_id_col = next(
                (c for c in disciplinas_cols if c.lower().endswith("id")),
                None,
            )
        disciplina_nome_from_table_col = (
            _pick_first_column(
                disciplinas_cols,
                [
                    "disc_descricao",
                    "disc_nome",
                    "descricao",
                    "nome",
                    "disciplina_nome",
                    "nome_disciplina",
                ],
            )
            if disciplinas_cols
            else None
        )
        if not disciplina_nome_from_table_col and disciplinas_cols:
            disciplina_nome_from_table_col = next(
                (
                    c
                    for c in disciplinas_cols
                    if "disc" in c.lower()
                    and ("desc" in c.lower() or "nome" in c.lower())
                ),
                None,
            )

        join_disciplinas = bool(
            disciplinas_table
            and disciplina_id_fk_col
            and disciplina_id_col
            and disciplina_nome_from_table_col
        )

        join_candidates = [
            ("disc_modu_id", "disc_modu_id"),
            ("disciplina_modulo_id", "id"),
            ("dimo_id", "dimo_id"),
            ("dimo_id", "id"),
            ("disc_modu_id", "id"),
        ]
        join_cols = next(
            (
                (a_col, dm_col)
                for a_col, dm_col in join_candidates
                if a_col in assuntos_cols and dm_col in modulos_cols
            ),
            None,
        )
        if not join_cols:
            raise HTTPException(
                status_code=500,
                detail="Não foi possível identificar o relacionamento entre 'assuntos' e 'disciplina_modulos'.",
            )

        assunto_join_col, modulo_join_col = join_cols
        modulo_id_col = (
            _pick_first_column(
                modulos_cols,
                [
                    modulo_join_col,
                    "disc_modu_id",
                    "dimo_id",
                    "id",
                ],
            )
            or modulo_join_col
        )
        modulo_disc_modu_col = _pick_first_column(
            modulos_cols,
            [
                modulo_join_col,
                "disc_modu_id",
                "dimo_id",
                "id",
            ],
        )

        assunto_id_select = (
            f", a.{_sql_ident(assunto_id_col)} AS assunto_id" if assunto_id_col else ""
        )
        modulo_disc_modu_select = (
            f", dm.{_sql_ident(modulo_disc_modu_col)} AS modulo_disc_modu_id"
            if modulo_disc_modu_col
            else ""
        )

        disciplina_nome_select = ""
        disciplina_id_select = ", NULL AS disciplina_id"
        disciplina_join_sql = ""
        if join_disciplinas:
            disciplina_nome_select = (
                f", d.{_sql_ident(disciplina_nome_from_table_col)} AS disciplina_nome"
            )
            disciplina_id_select = (
                f", dm.{_sql_ident(disciplina_id_fk_col)} AS disciplina_id"
            )
            disciplina_join_sql = f"""
                LEFT JOIN compartilhados.{_sql_ident(disciplinas_table)} d
                    ON dm.{_sql_ident(disciplina_id_fk_col)} = d.{_sql_ident(disciplina_id_col)}
            """
        else:
            disciplina_nome_select = (
                f", dm.{_sql_ident(disciplina_nome_col)} AS disciplina_nome"
                if disciplina_nome_col
                else ", NULL AS disciplina_nome"
            )

        trieduc_pairs = set()
        trieduc_disc_modu_ids = set()
        trieduc_triplets = set()  # (disciplina, modulo, assunto)

        for disciplina, modulo, disc_modu_id, assunto_descricao in pg_db.query(
            HabilidadeModuloModel.disciplina,
            HabilidadeModuloModel.modulo,
            HabilidadeModuloModel.disc_modu_id,
            HabilidadeModuloModel.descricao,
        ).all():
            disc_norm = _normalize_text(disciplina)
            mod_norm = _normalize_text(modulo)
            assunto_norm = _normalize_text(assunto_descricao)

            if disc_norm and mod_norm:
                trieduc_pairs.add((disc_norm, mod_norm))

            if disc_norm and mod_norm and assunto_norm:
                trieduc_triplets.add((disc_norm, mod_norm, assunto_norm))

            disc_modu_norm = _normalize_disc_modu_id(disc_modu_id)
            if disc_modu_norm:
                trieduc_disc_modu_ids.add(disc_modu_norm)

        sql = f"""
            SELECT
                dm.{_sql_ident(modulo_id_col)} AS modulo_id,
                dm.{_sql_ident(modulo_nome_col)} AS modulo_nome,
                a.{_sql_ident(assunto_desc_col)} AS assunto_descricao
                {assunto_id_select}
                {disciplina_id_select}
                {disciplina_nome_select}
                {modulo_disc_modu_select}
            FROM compartilhados.{_sql_ident(assuntos_table)} a
            INNER JOIN compartilhados.{_sql_ident(modulos_table)} dm
                ON a.{_sql_ident(assunto_join_col)} = dm.{_sql_ident(modulo_join_col)}
            {disciplina_join_sql}
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
            detail=f"Erro ao consultar módulos/assuntos em compartilhados: {exc}",
        ) from exc

    grouped = {}
    for row in rows:
        modulo_nome = (row.get("modulo_nome") or "").strip()
        if not modulo_nome:
            continue

        disciplina_nome = (row.get("disciplina_nome") or "").strip()
        disciplina_id = row.get("disciplina_id")
        disciplina_norm = _normalize_text(disciplina_nome)
        modulo_norm = _normalize_text(modulo_nome)

        modulo_disc_modu_norm = _normalize_disc_modu_id(row.get("modulo_disc_modu_id"))

        # Não pular mais o módulo inteiro aqui
        # has_relacionamento_trieduc = False
        # if disciplina_norm and modulo_norm and (disciplina_norm, modulo_norm) in trieduc_pairs:
        #     has_relacionamento_trieduc = True
        # elif modulo_disc_modu_norm and modulo_disc_modu_norm in trieduc_disc_modu_ids:
        #     has_relacionamento_trieduc = True
        #
        # if has_relacionamento_trieduc:
        #     continue

        modulo_id = (
            row.get("modulo_id") if row.get("modulo_id") is not None else modulo_nome
        )
        disciplina_id_key = _normalize_disc_modu_id(disciplina_id)
        group_key = (
            f"{disciplina_id_key or disciplina_norm}::{modulo_id}::{modulo_norm}"
        )

        if group_key not in grouped:
            grouped[group_key] = {
                "id": modulo_id,
                "disciplina_id": disciplina_id,
                "nome": modulo_nome,
                "disciplina": disciplina_nome,
                "assuntos": [],
                "_seen": set(),
            }

        assunto_descricao = (row.get("assunto_descricao") or "").strip()
        if not assunto_descricao:
            continue

        # Verificar se este par (módulo + assunto) específico já existe no relacionamento trieduc
        assunto_norm = _normalize_text(assunto_descricao)
        triplet_exists = (
            disciplina_norm,
            modulo_norm,
            assunto_norm,
        ) in trieduc_triplets

        if triplet_exists:
            # Pular apenas este assunto específico, não o módulo inteiro
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

        # Não incluir módulos que ficaram sem assuntos após filtrar
        if not assuntos:
            continue

        total_assuntos += len(assuntos)
        modulos.append(
            ModuloComAssuntosSchema(
                id=module_data["id"],
                disciplina_id=module_data["disciplina_id"],
                disciplina=module_data["disciplina"],
                nome=module_data["nome"],
                assuntos=assuntos,
                total_assuntos=len(assuntos),
                fonte="librostudio",
                has_relacionamento_trieduc=False,
            )
        )

    modulos.sort(
        key=lambda m: (
            str(m.disciplina_id or ""),
            (m.disciplina or "").lower(),
            m.nome.lower(),
        )
    )

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
    summary="📦 Módulos possíveis para uma habilidade",
)
async def listar_modulos_por_habilidade(
    habilidade_id: int,
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna os módulos possíveis para um dado habilidade_id do TriEduc."""
    modulos = (
        pg_db.query(HabilidadeModuloModel)
        .filter(HabilidadeModuloModel.habilidade_id == habilidade_id)
        .order_by(
            HabilidadeModuloModel.area,
            HabilidadeModuloModel.disciplina,
            HabilidadeModuloModel.modulo,
        )
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
    area: Optional[str] = Query(
        None, description="Filtrar por área (Humanas, Linguagens, Matemática, Natureza)"
    ),
    disciplina_id: Optional[str] = Query(None, description="ID ou Nome da disciplina"),
    habilidade_id: Optional[int] = Query(None, description="ID da habilidade TRIEDUC"),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
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

    logger.info(
        f"Busca Próxima: usuario={usuario.nome}, area={area}, disciplina={disciplina_id}, habilidade={habilidade_id}"
    )

    # Resolver filtro de área → disciplinas (Otimizado: apenas IDs)
    disciplina_ids_filtro = None
    if area and area in AREAS_DISCIPLINAS:
        nomes = AREAS_DISCIPLINAS[area]
        discs_ids = (
            db.query(DisciplinaModel.id)
            .filter(DisciplinaModel.descricao.in_(nomes))
            .all()
        )
        disciplina_ids_filtro = [d[0] for d in discs_ids]

    # OPTIMIZATION: 'Seek Method' (ID > last_id) instead of OFFSET.
    # This is much faster for large tables as it uses the Primary Key index directly.

    LIMIT_CANDIDATES = 200
    MAX_LOOP_TRIES = 50  # Total candidates to check = 10,000

    # Base query for candidate IDs in MySQL
    # We select ONLY the ID to keep it lightweight
    candidate_query = (
        db.query(QuestaoModel.id)
        .filter(QuestaoModel.habilidade_id.isnot(None))
        .filter(QuestaoModel.ano_id == 3)  # Ensino Médio
    )

    if habilidade_id:
        # Resolver TRIEDUC habilidade_id → MySQL habilidade_id(s) via descrição.
        # Os dois sistemas usam IDs diferentes; sem esta resolução o filtro retorna
        # zero questões mesmo quando o dropdown mostra pendentes.
        resolved_mysql_ids = _resolver_habilidade_mysql_ids(habilidade_id, pg_db, db)
        candidate_query = candidate_query.filter(
            QuestaoModel.habilidade_id.in_(resolved_mysql_ids)
        )

    if disciplina_id:
        if str(disciplina_id).isdigit():
            candidate_query = candidate_query.filter(
                QuestaoModel.disciplina_id == int(disciplina_id)
            )
        else:
            # Tentar mapeamento para MySQL
            mysql_name = MAP_DISCIPLINAS_MYSQL.get(disciplina_id, disciplina_id)

            if mysql_name:
                disc_id_row = (
                    db.query(DisciplinaModel.id)
                    .filter(DisciplinaModel.descricao == mysql_name)
                    .first()
                )
                if disc_id_row:
                    candidate_query = candidate_query.filter(
                        QuestaoModel.disciplina_id == disc_id_row[0]
                    )
                else:
                    # Se nome exato não existe no MySQL, falhar para não mostrar tudo
                    candidate_query = candidate_query.filter(QuestaoModel.id == -1)
            else:
                # Disciplina Virtual (Literatura/Redação): Buscar IDs de habilidade no Postgres
                habilidade_ids_custom = [
                    row[0]
                    for row in pg_db.query(HabilidadeModuloModel.habilidade_id)
                    .filter(HabilidadeModuloModel.disciplina == disciplina_id)
                    .filter(HabilidadeModuloModel.habilidade_id.isnot(None))
                    .distinct()
                    .all()
                ]
                if habilidade_ids_custom:
                    candidate_query = candidate_query.filter(
                        QuestaoModel.habilidade_id.in_(habilidade_ids_custom)
                    )
                else:
                    candidate_query = candidate_query.filter(QuestaoModel.id == -1)
    elif disciplina_ids_filtro:
        candidate_query = candidate_query.filter(
            QuestaoModel.disciplina_id.in_(disciplina_ids_filtro)
        )

    # Pre-filter: exclui questões já processadas no sistema usando NOT EXISTS cross-schema.
    # Evita o loop de 50 iterações quando todas as questões já estão em questao_assuntos.
    # pg_db e db usam o mesmo servidor MySQL (thsethub e trieduc são schemas diferentes).
    candidate_query = candidate_query.filter(
        sql_text(
            "NOT EXISTS ("
            "  SELECT 1 FROM thsethub.questao_assuntos qa_pre"
            "  WHERE qa_pre.questao_id = questoes.id"
            "  AND ("
            "    qa_pre.classificado_manualmente = 1"
            "    OR (qa_pre.classificacao_nao_enquadrada IS NOT NULL"
            "        AND JSON_LENGTH(qa_pre.classificacao_nao_enquadrada) > 0)"
            "    OR (qa_pre.extracao_feita = 1"
            "        AND qa_pre.classificacoes IS NOT NULL"
            "        AND JSON_LENGTH(qa_pre.classificacoes) > 0)"
            "  )"
            ")"
        )
    )

    candidate_query = candidate_query.order_by(QuestaoModel.id)

    last_id = 0
    questao_final = None

    for _ in range(MAX_LOOP_TRIES):
        # Fetch next block of candidate IDs starting from last_id
        candidates = (
            candidate_query.filter(QuestaoModel.id > last_id)
            .limit(LIMIT_CANDIDATES)
            .all()
        )
        if not candidates:
            break

        candidate_ids = [c[0] for c in candidates]
        last_id = candidate_ids[-1]  # Update for next possible loop

        # Check in PostgreSQL which candidates of THIS block are already processed
        # 1. Already classified by this user
        classified_by_user = {
            row[0]
            for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
            .filter(ClassificacaoUsuarioModel.questao_id.in_(candidate_ids))
            .filter(ClassificacaoUsuarioModel.usuario_id == usuario.id)
            .all()
        }

        # classified_in_system: substituído pelo NOT EXISTS pre-filter na candidate_query.
        classified_in_system = set()

        # 3. Puladas por qualquer usuário — só aparecem na aba Pendentes, nunca em /proxima
        skipped_any_user = {
            row[0]
            for row in pg_db.query(QuestaoPuladaModel.questao_id)
            .filter(QuestaoPuladaModel.questao_id.in_(candidate_ids))
            .all()
        }

        ids_excluir = classified_by_user.union(classified_in_system).union(
            skipped_any_user
        )

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
                enunciado_tratado, contem_imagem, motivo_erro = tratar_enunciado(
                    questao_final.enunciado
                )
                if not motivo_erro:
                    # Success!
                    break
                else:
                    # Mark as invalid in PG so we don't try it again
                    if valid_id not in classified_in_system:
                        disc_nome = (
                            questao_final.disciplina.descricao
                            if questao_final.disciplina
                            else None
                        )
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
                            motivo_erro=motivo_erro,
                        )
                        pg_db.add(reg)
                        pg_db.commit()
                    questao_final = None  # Keep looking in the same or next block

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
                .filter(
                    func.lower(HabilidadeModuloModel.habilidade_descricao)
                    == hab_descricao.lower()
                )
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
        classificacao_extracao=(
            extracao.classificacoes if extracao and extracao.extracao_feita else None
        ),
        tem_extracao=bool(
            extracao and extracao.extracao_feita and extracao.classificacoes
        ),
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
    pg_db: Session = Depends(get_db),
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
        .order_by(
            ClassificacaoUsuarioModel.created_at.desc(),
            ClassificacaoUsuarioModel.id.desc(),
        )
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
                .filter(
                    func.lower(HabilidadeModuloModel.habilidade_descricao)
                    == hab_descricao.lower()
                )
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
            [classificacao_manual.modulo_escolhido]
            if classificacao_manual.modulo_escolhido
            else []
        )
        manual_descricoes = classificacao_manual.descricoes_assunto_list or (
            [classificacao_manual.descricao_assunto]
            if classificacao_manual.descricao_assunto
            else []
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
        classificacao_extracao=(
            extracao.classificacoes if extracao and extracao.extracao_feita else None
        ),
        classificacao_nao_enquadrada=(
            extracao.classificacao_nao_enquadrada
            if extracao and extracao.classificacao_nao_enquadrada
            else None
        ),
        similaridade=extracao.similaridade if extracao else None,
        tem_extracao=bool(
            extracao and extracao.extracao_feita and extracao.classificacoes
        ),
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
    pg_db: Session = Depends(get_db),
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
        discs = (
            db.query(DisciplinaModel).filter(DisciplinaModel.descricao.in_(nomes)).all()
        )
        disciplina_ids_filtro = [d.id for d in discs]

    # Query Base no PG: extraídas pelo Superpro com baixa similaridade (precisa verificação humana)
    query_pg = pg_db.query(QuestaoAssuntoModel).filter(
        QuestaoAssuntoModel.extracao_feita == True,
        QuestaoAssuntoModel.classificacoes.isnot(None),
        QuestaoAssuntoModel.similaridade > 0,
        QuestaoAssuntoModel.similaridade < 0.8,
    )

    if habilidade_id:
        # Resolver TRIEDUC habilidade_id → MySQL habilidade_id(s)
        resolved_mysql_ids = _resolver_habilidade_mysql_ids(habilidade_id, pg_db, db)
        questao_ids_habilidade = [
            row[0]
            for row in db.query(QuestaoModel.id)
            .filter(
                QuestaoModel.habilidade_id.in_(resolved_mysql_ids),
                QuestaoModel.ano_id == 3,
            )
            .all()
        ]
        if questao_ids_habilidade:
            query_pg = query_pg.filter(
                QuestaoAssuntoModel.questao_id.in_(questao_ids_habilidade)
            )
        else:
            query_pg = query_pg.filter(QuestaoAssuntoModel.id == -1)

    if ids_verificadas:
        query_pg = query_pg.filter(~QuestaoAssuntoModel.questao_id.in_(ids_verificadas))

    if disciplina_id:
        if str(disciplina_id).isdigit():
            query_pg = query_pg.filter(
                QuestaoAssuntoModel.disciplina_id == int(disciplina_id)
            )
        else:
            # Tentar mapeamento para MySQL
            mysql_name = MAP_DISCIPLINAS_MYSQL.get(disciplina_id, disciplina_id)

            disc_target_id = None
            if mysql_name:
                disc = (
                    db.query(DisciplinaModel)
                    .filter(DisciplinaModel.descricao == mysql_name)
                    .first()
                )
                if disc:
                    disc_target_id = disc.id

            if disc_target_id:
                query_pg = query_pg.filter(
                    QuestaoAssuntoModel.disciplina_id == disc_target_id
                )
            else:
                # Disciplina Virtual (Literatura/Redação): Buscar IDs de habilidade no Postgres
                habilidade_ids_custom = [
                    row[0]
                    for row in pg_db.query(HabilidadeModuloModel.habilidade_id)
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
                    questao_ids_custom = [
                        row[0]
                        for row in db.query(QuestaoModel.id)
                        .filter(
                            QuestaoModel.habilidade_id.in_(habilidade_ids_custom),
                            QuestaoModel.ano_id == 3,
                        )
                        .all()
                    ]
                    if questao_ids_custom:
                        query_pg = query_pg.filter(
                            QuestaoAssuntoModel.questao_id.in_(questao_ids_custom)
                        )
                    else:
                        query_pg = query_pg.filter(QuestaoAssuntoModel.id == -1)
                else:
                    query_pg = query_pg.filter(QuestaoAssuntoModel.id == -1)
    elif disciplina_ids_filtro:
        query_pg = query_pg.filter(
            QuestaoAssuntoModel.disciplina_id.in_(disciplina_ids_filtro)
        )

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
            query_pg = query_pg.filter(
                QuestaoAssuntoModel.questao_id != registro_pg.questao_id
            )
            continue

        # Se chegou aqui, temos a questão!
        break
    else:
        raise HTTPException(
            status_code=404,
            detail="Não foram encontradas questões de Ensino Médio para verificar.",
        )

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

        hab = (
            db.query(HabilidadeModel)
            .filter(HabilidadeModel.id == questao.habilidade_id)
            .first()
        )
        if hab:
            hab_descricao = hab.descricao

        # FALLBACK: Se não achou módulos por ID, tenta por descrição (Case Insensitive)
        if not modulos and hab_descricao:
            from sqlalchemy import func

            modulos_q = (
                pg_db.query(HabilidadeModuloModel)
                .filter(
                    func.lower(HabilidadeModuloModel.habilidade_descricao)
                    == hab_descricao.lower()
                )
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
    pg_db: Session = Depends(get_db),
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
        discs = (
            db.query(DisciplinaModel).filter(DisciplinaModel.descricao.in_(nomes)).all()
        )
        disciplina_ids_filtro = [d.id for d in discs]

    # Query no PG: questões com classificacao_nao_enquadrada preenchida
    query_pg = pg_db.query(QuestaoAssuntoModel).filter(
        QuestaoAssuntoModel.classificacao_nao_enquadrada.isnot(None),
        func.json_length(QuestaoAssuntoModel.classificacao_nao_enquadrada) > 0,
        QuestaoAssuntoModel.classificado_manualmente == False,
        QuestaoAssuntoModel.similaridade.isnot(None),
        QuestaoAssuntoModel.similaridade > 0,
        QuestaoAssuntoModel.similaridade < 0.8,
    )

    if habilidade_id:
        # Resolver TRIEDUC habilidade_id → MySQL habilidade_id(s)
        resolved_mysql_ids = _resolver_habilidade_mysql_ids(habilidade_id, pg_db, db)
        questao_ids_habilidade = [
            row[0]
            for row in db.query(QuestaoModel.id)
            .filter(
                QuestaoModel.habilidade_id.in_(resolved_mysql_ids),
                QuestaoModel.ano_id == 3,
            )
            .all()
        ]
        if questao_ids_habilidade:
            query_pg = query_pg.filter(
                QuestaoAssuntoModel.questao_id.in_(questao_ids_habilidade)
            )
        else:
            query_pg = query_pg.filter(QuestaoAssuntoModel.id == -1)

    if ids_verificadas:
        query_pg = query_pg.filter(~QuestaoAssuntoModel.questao_id.in_(ids_verificadas))

    if disciplina_id:
        if str(disciplina_id).isdigit():
            query_pg = query_pg.filter(
                QuestaoAssuntoModel.disciplina_id == int(disciplina_id)
            )
        else:
            from ..database.models import DisciplinaModel

            disc = (
                db.query(DisciplinaModel)
                .filter(DisciplinaModel.descricao == disciplina_id)
                .first()
            )
            if disc:
                query_pg = query_pg.filter(QuestaoAssuntoModel.disciplina_id == disc.id)
    elif disciplina_ids_filtro:
        query_pg = query_pg.filter(
            QuestaoAssuntoModel.disciplina_id.in_(disciplina_ids_filtro)
        )

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
            query_pg = query_pg.filter(
                QuestaoAssuntoModel.questao_id != registro_pg.questao_id
            )
            continue

        break
    else:
        raise HTTPException(
            status_code=404,
            detail="Não foram encontradas questões de baixa similaridade para verificar.",
        )

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

        hab = (
            db.query(HabilidadeModel)
            .filter(HabilidadeModel.id == questao.habilidade_id)
            .first()
        )
        if hab:
            hab_descricao = hab.descricao

        if not modulos and hab_descricao:
            from sqlalchemy import func as sqlfunc

            modulos_q = (
                pg_db.query(HabilidadeModuloModel)
                .filter(
                    sqlfunc.lower(HabilidadeModuloModel.habilidade_descricao)
                    == hab_descricao.lower()
                )
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
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Salva a decisão de classificação do usuário.
    Tipos de ação:
    - **classificacao_nova**: Questão que não tinha classificação
    - **confirmacao**: Usuário confirmou classificação existente
    - **correcao**: Usuário corrigiu classificação existente
    - **classificacao_libro**: Classificação realizada pelo sistema Libro
    """
    if request.tipo_acao not in (
        "classificacao_nova",
        "confirmacao",
        "correcao",
        "classificacao_libro",
        "classificacao_superprofessor",
    ):
        raise HTTPException(status_code=400, detail="tipo_acao inválido")

    # Buscar habilidade_id da questão (Apenas o necessário)
    questao_data = (
        db.query(
            QuestaoModel.id,
            QuestaoModel.habilidade_id,
            QuestaoModel.questao_id,
            QuestaoModel.disciplina_id,
        )
        .filter(QuestaoModel.id == request.questao_id)
        .first()
    )
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
            disc_row = (
                db.query(DisciplinaModel.descricao)
                .filter(DisciplinaModel.id == questao_data.disciplina_id)
                .first()
            )
            disc_nome = disc_row[0] if disc_row else None

        # Criar registro básico para marcar como manual
        extracao = QuestaoAssuntoModel(
            questao_id=questao_data.id,
            questao_id_str=questao_data.questao_id,
            disciplina_id=questao_data.disciplina_id,
            disciplina_nome=disc_nome,
            classificacoes=[],
            classificado_manualmente=True,
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

    modulos_info = (
        request.modulos_escolhidos or [request.modulo_escolhido]
        if request.modulo_escolhido
        else []
    )
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
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Marca uma questão como pulada pelo usuário.
    A questão aparecerá na aba 'Pendentes' para classificação posterior.
    """
    # Verificar se a questão existe
    questao_data = (
        db.query(
            QuestaoModel.id, QuestaoModel.disciplina_id, QuestaoModel.habilidade_id
        )
        .filter(QuestaoModel.id == request.questao_id)
        .first()
    )

    if not questao_data:
        raise HTTPException(status_code=404, detail="Questão não encontrada")

    # Verificar se já foi pulada (evitar duplicata)
    existente = (
        pg_db.query(QuestaoPuladaModel)
        .filter(
            QuestaoPuladaModel.usuario_id == usuario.id,
            QuestaoPuladaModel.questao_id == request.questao_id,
        )
        .first()
    )

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
    pg_db: Session = Depends(get_db),
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
        query_puladas = query_puladas.filter(
            QuestaoPuladaModel.habilidade_id == habilidade_id
        )

    if disciplina_id:
        if str(disciplina_id).isdigit():
            query_puladas = query_puladas.filter(
                QuestaoPuladaModel.disciplina_id == int(disciplina_id)
            )
        else:
            # Tentar mapeamento para MySQL
            mysql_name = MAP_DISCIPLINAS_MYSQL.get(disciplina_id, disciplina_id)

            if mysql_name:
                disc_id_row = (
                    db.query(DisciplinaModel.id)
                    .filter(DisciplinaModel.descricao == mysql_name)
                    .first()
                )
                if disc_id_row:
                    query_puladas = query_puladas.filter(
                        QuestaoPuladaModel.disciplina_id == disc_id_row[0]
                    )
                else:
                    # Se não existe no MySQL, falhar filtro
                    query_puladas = query_puladas.filter(QuestaoPuladaModel.id == -1)
            else:
                # Disciplina Virtual (Literatura/Redação): Buscar IDs de habilidade no Postgres
                habilidade_ids_custom = [
                    row[0]
                    for row in pg_db.query(HabilidadeModuloModel.habilidade_id)
                    .filter(HabilidadeModuloModel.disciplina == disciplina_id)
                    .filter(HabilidadeModuloModel.habilidade_id.isnot(None))
                    .distinct()
                    .all()
                ]
                if habilidade_ids_custom:
                    query_puladas = query_puladas.filter(
                        QuestaoPuladaModel.habilidade_id.in_(habilidade_ids_custom)
                    )
                else:
                    query_puladas = query_puladas.filter(QuestaoPuladaModel.id == -1)
    elif effective_area and effective_area in AREAS_DISCIPLINAS:
        nomes = AREAS_DISCIPLINAS[effective_area]
        discs_ids = (
            db.query(DisciplinaModel.id)
            .filter(DisciplinaModel.descricao.in_(nomes))
            .all()
        )
        disciplina_ids_filtro = [d[0] for d in discs_ids]
        if disciplina_ids_filtro:
            query_puladas = query_puladas.filter(
                QuestaoPuladaModel.disciplina_id.in_(disciplina_ids_filtro)
            )
    elif effective_area:
        # Fallback: filtrar diretamente pelo campo area salvo no momento do pulo
        query_puladas = query_puladas.filter(QuestaoPuladaModel.area == effective_area)

    # LOG para depuração
    logger.info(
        f"Filtro Pendentes: usuario={usuario.nome}, effective_area={effective_area}, disciplina={disciplina_id}"
    )
    count_antes = query_puladas.count()
    logger.info(f"Total pendentes com filtros aplicados: {count_antes}")

    # IDs já classificadas por este usuário (excluir das pendentes)
    ids_classificadas = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.usuario_id == usuario.id)
        .all()
    }

    if ids_classificadas:
        query_puladas = query_puladas.filter(
            ~QuestaoPuladaModel.questao_id.in_(ids_classificadas)
        )

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
        raise HTTPException(
            status_code=404, detail="Questão pendente não encontrada no banco de dados."
        )

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
                .filter(
                    sqlfunc.lower(HabilidadeModuloModel.habilidade_descricao)
                    == hab_descricao.lower()
                )
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
        classificacao_extracao=(
            extracao.classificacoes if extracao and extracao.extracao_feita else None
        ),
        tem_extracao=bool(
            extracao and extracao.extracao_feita and extracao.classificacoes
        ),
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
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """Retorna estatísticas do sistema de classificação manual (Cache 5m)."""
    cache_key = "estatisticas_gerais"
    cached_data = get_from_cache(cache_key, ttl=300)
    if cached_data:
        return cached_data

    total = pg_db.query(ClassificacaoUsuarioModel).count()
    novas = (
        pg_db.query(ClassificacaoUsuarioModel)
        .filter(ClassificacaoUsuarioModel.tipo_acao == "classificacao_nova")
        .count()
    )
    confirmacoes = (
        pg_db.query(ClassificacaoUsuarioModel)
        .filter(ClassificacaoUsuarioModel.tipo_acao == "confirmacao")
        .count()
    )
    correcoes = (
        pg_db.query(ClassificacaoUsuarioModel)
        .filter(ClassificacaoUsuarioModel.tipo_acao == "correcao")
        .count()
    )
    usuarios_ativos = (
        pg_db.query(UsuarioModel).filter(UsuarioModel.ativo == True).count()
    )

    # Filtro Base: Ensino Médio + Habilidade ID
    # Join com DisciplinaModel para garantir integridade (opcional mas mantido para consistência)
    from ..database.models import DisciplinaModel

    em_query = db.query(QuestaoModel.id).filter(
        QuestaoModel.ano_id == 3, QuestaoModel.habilidade_id.isnot(None)
    )
    em_ids = [r[0] for r in em_query.all()]
    total_sistema = len(em_ids)

    if not em_ids:
        res = ClassificacaoStatsResponse(
            total_sistema=0, por_usuario={}, por_disciplina={}
        )
        set_to_cache(cache_key, res)
        return res

    # 0. Questões com 4 alternativas — excluídas do funil de classificação
    from ..database.models import QuestaoAlternativaModel as _QAlt

    quatro_alt_ids = {
        r[0]
        for r in db.query(_QAlt.questao_id)
        .filter(_QAlt.questao_id.in_(em_ids))
        .group_by(_QAlt.questao_id)
        .having(func.count(_QAlt.id) == 4)
        .all()
    }
    total_4_alternativas = len(quatro_alt_ids)

    # Funil elegível = EM com habilidade, sem 4 alternativas
    eligible_ids = list(set(em_ids) - quatro_alt_ids)
    total_sistema = len(eligible_ids)

    # 1. Manual (Prioridade Máxima)
    manuais_ids = {
        r[0]
        for r in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.questao_id.in_(eligible_ids))
        .all()
    }
    total_manuais = len(manuais_ids)

    # 2. Alta Similaridade Pendente (sim >= 0.8, ainda sem ação manual)
    #    Estas questões NÃO estão classificadas — aguardam módulos libro.
    alta_sim_query = (
        pg_db.query(QuestaoAssuntoModel.questao_id)
        .filter(
            QuestaoAssuntoModel.questao_id.in_(eligible_ids),
            QuestaoAssuntoModel.similaridade >= 0.8,
        )
        .all()
    )
    alta_sim_ids = {r[0] for r in alta_sim_query} - manuais_ids
    total_alta_similaridade = len(alta_sim_ids)
    total_auto_superpro = 0  # mantido por retrocompatibilidade, agora sempre 0

    # 3. Faltam Verificar (0 < sim < 80%, não tocadas)
    verificar_query = (
        pg_db.query(QuestaoAssuntoModel.questao_id)
        .filter(
            QuestaoAssuntoModel.questao_id.in_(eligible_ids),
            QuestaoAssuntoModel.similaridade < 0.8,
            QuestaoAssuntoModel.similaridade > 0,
        )
        .all()
    )
    verificar_ids = {r[0] for r in verificar_query} - manuais_ids - alta_sim_ids
    total_precisa_verificar = len(verificar_ids)

    # 4. Puladas
    from ..database.pg_pular_models import QuestaoPuladaModel

    puladas_query = (
        pg_db.query(QuestaoPuladaModel.questao_id)
        .filter(QuestaoPuladaModel.questao_id.in_(eligible_ids))
        .all()
    )
    all_puladas_ids = {r[0] for r in puladas_query}
    puladas_ids_disjoint = all_puladas_ids - manuais_ids - alta_sim_ids - verificar_ids
    total_puladas = len(puladas_ids_disjoint)

    # 5. Pendentes (resto matemático)
    total_pendentes = max(
        0,
        total_sistema
        - total_manuais
        - total_alta_similaridade
        - total_precisa_verificar
        - total_puladas,
    )

    # Por disciplina (Dashboard style)
    mysql_rows = (
        db.query(QuestaoModel.disciplina_id, func.count(QuestaoModel.id))
        .filter(QuestaoModel.ano_id == 3, QuestaoModel.habilidade_id.isnot(None))
        .group_by(QuestaoModel.disciplina_id)
        .all()
    )
    mysql_counts = {r[0]: r[1] for r in mysql_rows}

    # Feitas = apenas as classificadas manualmente (alta sim pendente não conta como feita)
    ids_finalizados = manuais_ids

    # Mapa questao_id → disciplina_id (MySQL) para detalhar por disciplina
    q_disc_rows = (
        db.query(QuestaoModel.id, QuestaoModel.disciplina_id)
        .filter(QuestaoModel.id.in_(em_ids))
        .all()
    )
    disc_ids_map: dict[int, set] = {}  # disciplina_id → set de questao_ids
    for qid, did in q_disc_rows:
        if did is None:
            continue
        disc_ids_map.setdefault(did, set()).add(qid)

    disc_names = {d.id: d.descricao for d in db.query(DisciplinaModel).all()}

    # Contagem de módulos e assuntos por disciplina (banco compartilhados)
    from sqlalchemy import text as sql_text

    try:
        # Query SQL exata fornecida pelo usuário
        sql = """
            SELECT
                d.disc_id,
                d.disc_descricao,
                COUNT(DISTINCT dm.disc_modu_id) AS total_modulos,
                COUNT(a.assu_id) AS total_assuntos
            FROM compartilhados.disciplinas d
            INNER JOIN compartilhados.disciplinas_modulos dm
                ON dm.disc_id = d.disc_id
            LEFT JOIN compartilhados.assuntos a
                ON a.disc_modu_id = dm.disc_modu_id
               AND TRIM(a.assu_descricao) NOT LIKE '[RM]%%'
            WHERE TRIM(dm.disc_modu_descricao) NOT LIKE '[RM]%%'
            GROUP BY d.disc_id, d.disc_descricao
            ORDER BY d.disc_id
        """

        result = db.execute(sql_text(sql)).fetchall()

        logger.info(f"Total de linhas retornadas da query: {len(result)}")

        # Mapeamento de sinônimos entre bancos (case-insensitive)
        nome_sinonimos = {
            "inglês": "Língua Inglesa",
            "espanhol": "Língua Espanhola",
            "arte": "Artes",
        }

        # Processa resultados e soma Literatura + Redação com Língua Portuguesa
        modulos_por_disc = {}
        assuntos_por_disc = {}

        # Primeiro passo: coleta todos os valores
        lingua_port_modulos = 0
        lingua_port_assuntos = 0

        for row in result:
            disc_descricao_orig = row[1]
            disc_descricao = disc_descricao_orig.strip() if disc_descricao_orig else ""
            total_modulos = row[2] or 0
            total_assuntos = row[3] or 0

            logger.info(
                f"Linha: '{disc_descricao}' -> Módulos: {total_modulos}, Assuntos: {total_assuntos}"
            )

            # Aplica mapeamento de sinônimos (case-insensitive)
            disc_lower = disc_descricao.lower()
            disc_nome_final = nome_sinonimos.get(disc_lower, disc_descricao)

            logger.info(f"  Mapeado: '{disc_descricao}' -> '{disc_nome_final}'")

            # Acumula Literatura, Redação e Língua Portuguesa
            if disc_descricao in ["Literatura", "Redação", "Língua Portuguesa"]:
                lingua_port_modulos += total_modulos
                lingua_port_assuntos += total_assuntos
                logger.info(
                    f"  Acumulado LP: módulos={lingua_port_modulos}, assuntos={lingua_port_assuntos}"
                )
            else:
                # Armazena SEPARADAMENTE módulos e assuntos
                modulos_por_disc[disc_nome_final] = total_modulos
                assuntos_por_disc[disc_nome_final] = total_assuntos
                logger.info(
                    f"  Armazenado: ['{disc_nome_final}'] mod={total_modulos}, ass={total_assuntos}"
                )

        # Segundo passo: atribui os valores consolidados para Língua Portuguesa
        modulos_por_disc["Língua Portuguesa"] = lingua_port_modulos
        assuntos_por_disc["Língua Portuguesa"] = lingua_port_assuntos

        logger.info(f"FINAL - Módulos por disciplina: {modulos_por_disc}")
        logger.info(f"FINAL - Assuntos por disciplina: {assuntos_por_disc}")

    except Exception as e:
        logger.error(f"Erro ao contar módulos/assuntos: {e}", exc_info=True)
        modulos_por_disc = {}
        assuntos_por_disc = {}

    # Contagem de habilidades únicas por disciplina (from questoes com habilidade_id)
    try:
        # Mapeamento de nomes de disciplinas do trieduc
        nome_sinonimos_trieduc = {
            "Inglês": "Língua Inglesa",
            "Espanhol": "Língua Espanhola",
            "Arte": "Artes",
        }

        hab_rows = (
            db.query(
                QuestaoModel.disciplina_id,
                func.count(func.distinct(QuestaoModel.habilidade_id)),
            )
            .filter(QuestaoModel.ano_id == 3, QuestaoModel.habilidade_id.isnot(None))
            .group_by(QuestaoModel.disciplina_id)
            .all()
        )

        habs_por_disc = {}
        lingua_port_habs = 0

        for d_id, count in hab_rows:
            if d_id in disc_names:
                nome_original = disc_names[d_id]
                # Aplica mapeamento de sinônimos
                nome_final = nome_sinonimos_trieduc.get(nome_original, nome_original)

                # Acumula Literatura, Redação e Língua Portuguesa
                if nome_original in ["Literatura", "Redação", "Língua Portuguesa"]:
                    lingua_port_habs += count
                else:
                    habs_por_disc[nome_final] = count

        # Atribui o valor consolidado para Língua Portuguesa
        habs_por_disc["Língua Portuguesa"] = lingua_port_habs

        logger.info(f"Habilidades por disciplina: {habs_por_disc}")
    except Exception as e:
        logger.warning(f"Erro ao contar habilidades: {e}")
        habs_por_disc = {}

    por_disciplina = {}

    # Mapeamento para padronizar nomes entre bancos
    nome_padrao_map = {
        "Inglês": "Língua Inglesa",
        "Espanhol": "Língua Espanhola",
        "Arte": "Artes",
    }

    for d_id, total_mysql in mysql_counts.items():
        if d_id is None:
            continue
        nome_original = disc_names.get(d_id, f"ID {d_id}")

        # Aplica mapeamento de padronização
        nome = nome_padrao_map.get(nome_original, nome_original)

        d_set = disc_ids_map.get(d_id, set())
        d_quatro_alt = len(quatro_alt_ids & d_set)
        total_disc = max(0, total_mysql - d_quatro_alt)
        d_manuais = len(manuais_ids & d_set)
        d_alta_sim = len(alta_sim_ids & d_set)
        d_verificar = len(verificar_ids & d_set)
        d_puladas = len(puladas_ids_disjoint & d_set)
        d_feitas = d_manuais
        d_pendentes = max(
            0, total_disc - d_manuais - d_alta_sim - d_verificar - d_puladas
        )
        por_disciplina[nome] = {
            "total": total_disc,
            "feitas": d_feitas,
            "faltam": max(0, total_disc - d_feitas),
            "manuais": d_manuais,
            "auto": d_alta_sim,
            "alta_sim": d_alta_sim,
            "verificar": d_verificar,
            "pendentes": d_pendentes,
            "puladas": d_puladas,
            "total_modulos": modulos_por_disc.get(nome, 0),
            "total_habilidades": habs_por_disc.get(nome, 0),
            "total_assuntos": assuntos_por_disc.get(nome, 0),
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
        total_alta_similaridade=total_alta_similaridade,
        total_4_alternativas=total_4_alternativas,
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
    pg_db: Session = Depends(get_db),
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

    registros = (
        query.order_by(ClassificacaoUsuarioModel.id)
        .offset(offset)
        .limit(per_page)
        .all()
    )

    # Buscar nomes dos usuários
    usuario_ids = {r.usuario_id for r in registros}
    if usuario_ids:
        users = pg_db.query(UsuarioModel).filter(UsuarioModel.id.in_(usuario_ids)).all()
        user_map = {u.id: u.nome for u in users}
    else:
        user_map = {}

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


# ========================
# SUPERPROFESSOR
# ========================


@router.get(
    "/superprofessor/disciplinas",
    summary="Disciplinas SP disponiveis",
)
async def listar_disciplinas_superprofessor(
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna a lista de disciplinas do superprofessor com contagem de questões.
    """
    rows = (
        pg_db.query(
            QuestaoSuperprofessorModel.disciplina_sp,
            func.count(QuestaoSuperprofessorModel.sp_id)
        )
        .filter(QuestaoSuperprofessorModel.disciplina_sp.isnot(None))
        .group_by(QuestaoSuperprofessorModel.disciplina_sp)
        .order_by(QuestaoSuperprofessorModel.disciplina_sp)
        .all()
    )
    return {
        "disciplinas": [
            {"nome": r[0], "total": r[1]} for r in rows if r[0]
        ]
    }



@router.get(
    "/superprofessor/assuntos",
    summary="Assuntos SP disponiveis",
)

async def listar_assuntos_superprofessor(
    disciplina: Optional[str] = Query(None, description="Filtrar por disciplina SP"),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna a lista de assuntos (assunto_sp) do superprofessor com contagem.
    """
    query = pg_db.query(
        QuestaoSuperprofessorModel.assunto_sp,
        func.count(QuestaoSuperprofessorModel.sp_id)
    ).filter(
        QuestaoSuperprofessorModel.assunto_sp.isnot(None),
        QuestaoSuperprofessorModel.assunto_sp != "",
    )
    if disciplina:
        query = query.filter(
            QuestaoSuperprofessorModel.disciplina_sp == disciplina
        )
    rows = (
        query.group_by(QuestaoSuperprofessorModel.assunto_sp)
        .order_by(QuestaoSuperprofessorModel.assunto_sp)
        .all()
    )
    return {
        "assuntos": [
            {"nome": r[0], "total": r[1]} for r in rows if r[0]
        ]
    }



@router.get(
    "/superprofessor/stats",
    response_model=SuperprofessorStatsResponse,
    summary="Estatisticas do superprofessor",
)

async def stats_superprofessor(
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna estatísticas do módulo superprofessor.
    """
    total_questoes = pg_db.query(QuestaoSuperprofessorModel).count()

    classificadas_ids = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.tipo_acao == "classificacao_superprofessor")
        .distinct()
        .all()
    }
    total_classificadas = len(classificadas_ids)

    puladas_ids = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.tipo_acao == "pular_superprofessor")
        .distinct()
        .all()
    } - classificadas_ids
    total_puladas = len(puladas_ids)

    total_pendentes = max(0, total_questoes - total_classificadas - total_puladas)

    # Por disciplina SP
    disc_rows = (
        pg_db.query(
            QuestaoSuperprofessorModel.disciplina_sp,
            func.count(QuestaoSuperprofessorModel.sp_id),
        )
        .filter(QuestaoSuperprofessorModel.disciplina_sp.isnot(None))
        .group_by(QuestaoSuperprofessorModel.disciplina_sp)
        .all()
    )

    # Mapa sp_id → disciplina_sp para contagens
    sp_disc_rows = (
        pg_db.query(
            QuestaoSuperprofessorModel.sp_id,
            QuestaoSuperprofessorModel.disciplina_sp,
        ).all()
    )
    sp_disc_map: dict[int, str] = {r[0]: r[1] for r in sp_disc_rows if r[1]}

    por_disciplina = {}
    for disc_name, total in disc_rows:
        if not disc_name:
            continue
        sp_ids_disc = {sp_id for sp_id, d in sp_disc_map.items() if d == disc_name}
        classif_disc = len(classificadas_ids & sp_ids_disc)
        puladas_disc = len(puladas_ids & sp_ids_disc)
        pend_disc = max(0, total - classif_disc - puladas_disc)
        por_disciplina[disc_name] = {
            "total": total,
            "classificadas": classif_disc,
            "puladas": puladas_disc,
            "pendentes": pend_disc,
        }

    # Por usuário
    user_rows = (
        pg_db.query(
            UsuarioModel.nome,
            func.count(ClassificacaoUsuarioModel.id),
        )
        .join(UsuarioModel, UsuarioModel.id == ClassificacaoUsuarioModel.usuario_id)
        .filter(
            ClassificacaoUsuarioModel.tipo_acao.in_(
                ["classificacao_superprofessor", "pular_superprofessor"]
            )
        )
        .group_by(UsuarioModel.nome)
        .all()
    )
    por_usuario = {row[0]: row[1] for row in user_rows}

    return SuperprofessorStatsResponse(
        total_questoes=total_questoes,
        total_classificadas=total_classificadas,
        total_puladas=total_puladas,
        total_pendentes=total_pendentes,
        por_disciplina=por_disciplina,
        por_usuario=por_usuario,
    )


@router.get(
    "/superprofessor/proxima",
    response_model=QuestaoSuperprofessorResponse,
    summary="Proxima questao superprofessor",
)

async def proxima_questao_superprofessor(
    disciplina: Optional[str] = Query(None, description="Filtrar por disciplina SP"),
    assunto_sp: Optional[str] = Query(None, description="Filtrar por assunto SP"),
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Retorna a próxima questão do superprofessor que ainda não foi revisada pelo usuário.
    Mostra a classificação SP original e o mapeamento libro já feito.
    """
    # sp_id já tratados por este usuário neste fluxo
    classificados_sp_ids: set[int] = {
        row[0]
        for row in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(
            ClassificacaoUsuarioModel.usuario_id == usuario.id,
            ClassificacaoUsuarioModel.tipo_acao.in_(
                ["classificacao_superprofessor", "pular_superprofessor"]
            ),
        )
        .all()
    }

    query = pg_db.query(QuestaoSuperprofessorModel)

    if disciplina:
        query = query.filter(
            QuestaoSuperprofessorModel.disciplina_sp == disciplina
        )

    if assunto_sp:
        query = query.filter(
            QuestaoSuperprofessorModel.assunto_sp == assunto_sp
        )

    if classificados_sp_ids:
        query = query.filter(
            ~QuestaoSuperprofessorModel.sp_id.in_(list(classificados_sp_ids))
        )

    total_pendentes = query.count()
    questao = query.order_by(QuestaoSuperprofessorModel.sp_id.asc()).first()

    if not questao:
        raise HTTPException(
            status_code=404, detail="Nenhuma questão pendente encontrada"
        )

    # Buscar módulos e assuntos de compartilhados, filtrados pelas disciplinas_libro
    modulos_possiveis = []
    disciplinas_libro = questao.disciplinas_libro or []

    if disciplinas_libro:
        # Expandir "Língua Portuguesa" para incluir Literatura e Redação
        disciplinas_expandidas = []
        for disc in disciplinas_libro:
            disciplinas_expandidas.append(disc)
            if disc == "Língua Portuguesa":
                disciplinas_expandidas.extend(["Literatura", "Redação"])

        # Remover duplicatas e manter ordem
        disciplinas_expandidas = list(dict.fromkeys(disciplinas_expandidas))

        placeholders = ", ".join(f":{f'disc{i}'}" for i in range(len(disciplinas_expandidas)))
        params = {f"disc{i}": v for i, v in enumerate(disciplinas_expandidas)}
        sql = sql_text(f"""
            SELECT
                a.assu_id          AS id,
                d.disc_descricao   AS disciplina,
                dm.disc_modu_descricao AS modulo,
                a.assu_descricao   AS descricao
            FROM compartilhados.disciplinas d
            JOIN compartilhados.disciplinas_modulos dm
                ON dm.disc_id = d.disc_id
            JOIN compartilhados.assuntos a
                ON a.disc_modu_id = dm.disc_modu_id
            WHERE d.disc_descricao IN ({placeholders})
              AND TRIM(dm.disc_modu_descricao) NOT LIKE '[RM]%%'
              AND TRIM(a.assu_descricao) NOT LIKE '[RM]%%'
            ORDER BY d.disc_descricao, dm.disc_modu_descricao, a.assu_descricao
        """)
        rows = pg_db.execute(sql, params).fetchall()
        modulos_possiveis = [
            HabilidadeModuloSchema(
                id=row.id,
                habilidade_id=None,
                habilidade_descricao="",
                area="",
                disciplina=row.disciplina,
                modulo=row.modulo,
                descricao=row.descricao,
                ordenacao=None,
            )
            for row in rows
        ]

    # Buscar alternativas
    alternativas = []
    alt_rows = (
        pg_db.query(AlternativaSuperprofessorModel)
        .filter(AlternativaSuperprofessorModel.sp_id == questao.sp_id)
        .order_by(AlternativaSuperprofessorModel.letra)
        .all()
    )
    gabarito = (questao.gabarito or "").strip().upper()
    for alt in alt_rows:
        letra = (alt.letra or "").strip().upper()
        alternativas.append(
            AlternativaSuperprofessorSchema(
                letra=alt.letra,
                texto=alt.texto or "",
                correta=bool(gabarito and letra == gabarito),
            )
        )

    return QuestaoSuperprofessorResponse(
        id=questao.sp_id,
        sp_id=questao.sp_id,
        enunciado=questao.enunciado,
        disciplina_sp=questao.disciplina_sp,
        classif_sp_breadcrumb=questao.classif_sp_breadcrumb,
        assunto_sp=questao.assunto_sp,
        disciplinas_libro=disciplinas_libro,
        assuntos_libro=questao.assuntos_libro,
        alternativas=alternativas,
        gabarito=questao.gabarito,
        modulos_possiveis=modulos_possiveis,
        total_pendentes=total_pendentes,
    )


@router.post(
    "/superprofessor/salvar",
    response_model=SalvarClassificacaoResponse,
    summary="Salvar revisao superprofessor",
)

async def salvar_superprofessor(
    request: SalvarSuperprofessorRequest,
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Salva a revisão de uma questão superprofessor.
    Registra em classificacao_usuario com tipo_acao='classificacao_superprofessor'.
    O questao_id armazenado é o sp_id (ID original no banco superprofessor).
    """
    questao = (
        pg_db.query(QuestaoSuperprofessorModel)
        .filter(QuestaoSuperprofessorModel.sp_id == request.questao_nova_id)
        .first()
    )

    if not questao:
        raise HTTPException(status_code=404, detail="Questão não encontrada")

    classificacao = ClassificacaoUsuarioModel(
        usuario_id=usuario.id,
        questao_id=questao.sp_id,
        habilidade_id=None,
        modulo_escolhido=request.modulo_escolhido,
        classificacao_trieduc=request.classificacao_trieduc,
        descricao_assunto=request.descricao_assunto,
        habilidade_modulo_id=request.habilidade_modulo_id,
        modulos_escolhidos=request.modulos_escolhidos,
        classificacoes_trieduc_list=request.classificacoes_trieduc,
        descricoes_assunto_list=request.descricoes_assunto,
        habilidade_modulo_ids=request.habilidade_modulo_ids,
        classificacao_extracao=None,
        tipo_acao="classificacao_superprofessor",
        observacao=request.observacao,
    )
    pg_db.add(classificacao)
    pg_db.commit()

    logger.info(
        f"Superprofessor salvo: usuario={usuario.nome}, sp_id={questao.sp_id}, "
        f"modulos={request.modulos_escolhidos or [request.modulo_escolhido]}"
    )

    return SalvarClassificacaoResponse(
        success=True,
        id=classificacao.id,
        questao_id=questao.sp_id,
        tipo_acao="classificacao_superprofessor",
        message="Classificação superprofessor salva com sucesso",
    )


@router.post(
    "/superprofessor/pular",
    summary="Pular questao superprofessor",
)

async def pular_superprofessor(
    request: PularSuperprofessorRequest,
    pg_db: Session = Depends(get_db),
    usuario: UsuarioModel = Depends(get_usuario_atual),
):
    """
    Pula uma questão superprofessor para o usuário atual.
    Registra em classificacao_usuario com tipo_acao='pular_superprofessor'.
    """
    questao = (
        pg_db.query(QuestaoSuperprofessorModel)
        .filter(QuestaoSuperprofessorModel.sp_id == request.questao_nova_id)
        .first()
    )

    if not questao:
        raise HTTPException(status_code=404, detail="Questão não encontrada")

    pulo = ClassificacaoUsuarioModel(
        usuario_id=usuario.id,
        questao_id=questao.sp_id,
        habilidade_id=None,
        tipo_acao="pular_superprofessor",
    )
    pg_db.add(pulo)
    pg_db.commit()

    return {"success": True, "message": "Questão pulada"}
