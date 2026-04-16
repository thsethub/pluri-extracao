from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload, undefer
from sqlalchemy import func, Integer as SaInteger
from typing import Optional
from math import ceil
from pydantic import BaseModel
import requests as http_requests

from ..database import get_ocr_db
from ..database.corrigeai_models import RedacaoModel, ValidacaoOcrModel
from ..database.pg_usuario_models import UsuarioModel
from .ocr_confianca_schemas import (
    RedacaoOcrConfiancaSchema,
    RedacaoOcrConfiancaListResponse,
    ValidacaoOcrCreateSchema,
    ValidacaoOcrResponseSchema,
    RevisorResumoSchema,
    StatusContadorSchema,
    OcrAdminResumoResponse,
)
from .classificacao_router import get_usuario_atual
from .ocr_lock import get_session

router = APIRouter(prefix="/classificacao/ocr-confianca", tags=["OCR Confiança"])


@router.get(
    "/redacoes",
    response_model=RedacaoOcrConfiancaListResponse,
    summary="Listar redações com avaliação de confiança OCR",
    response_description="Lista paginada de redações com dados de confiança OCR",
)
async def listar_redacoes_ocr_confianca(
    teste_prova_id: int = Query(..., description="ID do teste de prova"),
    redacao_status_id: Optional[int] = Query(
        None, description="Filtrar por status da redação"
    ),
    incluir_ocr_nulo: bool = Query(
        False, description="Se True, inclui redações com ocr_confianca nulo"
    ),
    ocr_confianca_min: Optional[float] = Query(
        None, ge=0.0, le=1.0, description="Valor mínimo de confiança OCR (0 a 1)"
    ),
    ocr_confianca_max: Optional[float] = Query(
        None, ge=0.0, le=1.0, description="Valor máximo de confiança OCR (0 a 1)"
    ),
    page: int = Query(1, ge=1, description="Página"),
    per_page: int = Query(20, ge=1, le=100, description="Itens por página"),
    db: Session = Depends(get_ocr_db),
    _usuario=Depends(get_usuario_atual),
):
    # ── Query base de filtros (sem joinedload, sem texto) ──
    base_query = db.query(RedacaoModel.redacao_id).filter(
        RedacaoModel.teste_prova_id == teste_prova_id,
        RedacaoModel.deleted_at.is_(None),
    )

    if not incluir_ocr_nulo:
        base_query = base_query.filter(RedacaoModel.ocr_confianca.isnot(None))

    if ocr_confianca_min is not None:
        base_query = base_query.filter(RedacaoModel.ocr_confianca >= ocr_confianca_min)

    if ocr_confianca_max is not None:
        base_query = base_query.filter(RedacaoModel.ocr_confianca <= ocr_confianca_max)

    if redacao_status_id is not None:
        base_query = base_query.filter(
            RedacaoModel.redacao_status_id == redacao_status_id
        )

    # ── C: Excluir já validadas via LEFT JOIN anti-pattern ──
    base_query = base_query.outerjoin(
        ValidacaoOcrModel, ValidacaoOcrModel.redacao_id == RedacaoModel.redacao_id
    ).filter(ValidacaoOcrModel.id.is_(None))

    # ── Lock: excluir redações claimed/puladas por outros revisores (in-memory) ──
    session = get_session(
        teste_prova_id, redacao_status_id, ocr_confianca_min, ocr_confianca_max
    )
    excluded = session.get_excluded_ids(_usuario.id)
    if excluded:
        base_query = base_query.filter(RedacaoModel.redacao_id.notin_(excluded))

    # ── B: Count leve (só IDs, sem joinedload, sem TEXT) ──
    total = base_query.count()
    pages = ceil(total / per_page) if total > 0 else 1
    offset = (page - 1) * per_page

    # ── Buscar IDs da página ──
    redacao_ids = [
        rid
        for (rid,) in base_query.order_by(RedacaoModel.redacao_id)
        .offset(offset)
        .limit(per_page)
        .all()
    ]

    # ── A+D: Fetch completo só para os IDs selecionados (com joinedload + undefer texto) ──
    redacoes = []
    if redacao_ids:
        redacoes = (
            db.query(RedacaoModel)
            .options(
                joinedload(RedacaoModel.arquivo), undefer(RedacaoModel.redacao_texto)
            )
            .filter(RedacaoModel.redacao_id.in_(redacao_ids))
            .order_by(RedacaoModel.redacao_id)
            .all()
        )

    data = [
        RedacaoOcrConfiancaSchema(
            redacao_id=r.redacao_id,
            teste_prova_id=r.teste_prova_id,
            redacao_status_id=r.redacao_status_id,
            ocr_confianca=r.ocr_confianca,
            tema=r.tema,
            redacao_texto=r.redacao_texto,
            arquivo_anonimo_nome_armazenamento=(
                r.arquivo.arquivo_anonimo_nome_armazenamento if r.arquivo else None
            ),
        )
        for r in redacoes
    ]

    # ── Lock: claim na redação retornada ──
    if data:
        session.claim(_usuario.id, data[0].redacao_id)

    return RedacaoOcrConfiancaListResponse(
        data=data,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


@router.get(
    "/imagem",
    summary="Proxy autenticado da imagem da redação",
)
async def get_imagem_redacao(
    arquivo: str = Query(
        ..., description="URL da imagem (arquivo_anonimo_nome_armazenamento)"
    ),
    _usuario=Depends(get_usuario_atual),
):
    """
    Faz proxy da imagem da redação, mascarando a URL real do armazenamento.
    Requer autenticação JWT — a URL original nunca é exposta ao cliente.
    """
    try:
        resp = http_requests.get(arquivo, timeout=15, stream=True)
        if resp.status_code != 200:
            raise HTTPException(status_code=404, detail="Imagem não encontrada")

        content_type = resp.headers.get("Content-Type", "image/jpeg")

        return StreamingResponse(
            resp.iter_content(chunk_size=8192),
            media_type=content_type,
            headers={"Cache-Control": "private, max-age=3600"},
        )
    except http_requests.RequestException:
        raise HTTPException(status_code=502, detail="Erro ao buscar a imagem")


# ── POST: Salvar validação OCR ──


@router.post(
    "/validacoes",
    response_model=ValidacaoOcrResponseSchema,
    summary="Salvar validação manual do OCR de uma redação",
    status_code=201,
)
async def salvar_validacao_ocr(
    body: ValidacaoOcrCreateSchema,
    db: Session = Depends(get_ocr_db),
    usuario=Depends(get_usuario_atual),
):
    validacao = ValidacaoOcrModel(
        revisor_id=usuario.id,
        redacao_id=body.redacao_id,
        ocr_pulou_trechos=body.ocr_pulou_trechos,
        ocr_trocou_palavras=body.ocr_trocou_palavras,
        ocr_trocou_caracteres=body.ocr_trocou_caracteres,
    )
    db.add(validacao)
    db.commit()
    db.refresh(validacao)

    # ── Lock: marcar como concluída ──
    # Busca todas as sessões ativas para marcar complete em todas
    from .ocr_lock import _sessions, _registry_lock

    with _registry_lock:
        for s in _sessions.values():
            s.complete(body.redacao_id)

    return validacao


# ── POST: Pular redação ──


class PularRedacaoSchema(BaseModel):
    redacao_id: int
    teste_prova_id: int
    redacao_status_id: Optional[int] = None
    ocr_confianca_min: Optional[float] = None
    ocr_confianca_max: Optional[float] = None


@router.post(
    "/pular",
    summary="Registrar que o revisor pulou uma redação",
    status_code=200,
)
async def pular_redacao(
    body: PularRedacaoSchema,
    usuario=Depends(get_usuario_atual),
):
    session = get_session(
        body.teste_prova_id,
        body.redacao_status_id,
        body.ocr_confianca_min,
        body.ocr_confianca_max,
    )
    session.skip(usuario.id, body.redacao_id)
    return {"ok": True}


# ── GET: Resumo admin de validações ──

STATUS_LABELS = {4: "Corrigida", 10: "OCR inválido", 11: "Correção inválida"}


@router.get(
    "/admin/resumo",
    response_model=OcrAdminResumoResponse,
    summary="Dashboard admin: resumo de validações OCR por revisor e status",
)
async def admin_resumo_ocr(
    teste_prova_id: int = Query(..., description="ID do teste de prova"),
    ocr_confianca_min: Optional[float] = Query(None, ge=0.0, le=1.0),
    ocr_confianca_max: Optional[float] = Query(None, ge=0.0, le=1.0),
    db: Session = Depends(get_ocr_db),
    usuario=Depends(get_usuario_atual),
):
    if not usuario.is_admin:
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores")

    # ── Filtro base para redações ──
    def filtro_redacoes(q):
        q = q.filter(
            RedacaoModel.teste_prova_id == teste_prova_id,
            RedacaoModel.deleted_at.is_(None),
            RedacaoModel.ocr_confianca.isnot(None),
        )
        if ocr_confianca_min is not None:
            q = q.filter(RedacaoModel.ocr_confianca >= ocr_confianca_min)
        if ocr_confianca_max is not None:
            q = q.filter(RedacaoModel.ocr_confianca <= ocr_confianca_max)
        return q

    # ── Query 1: Tabela de revisores ──
    revisores_query = (
        db.query(
            ValidacaoOcrModel.revisor_id,
            UsuarioModel.nome,
            func.count(ValidacaoOcrModel.id).label("revisado"),
            func.sum(func.cast(ValidacaoOcrModel.ocr_pulou_trechos, SaInteger)).label(
                "pulou_trechos_sim"
            ),
            func.sum(func.cast(ValidacaoOcrModel.ocr_trocou_palavras, SaInteger)).label(
                "trocou_palavras_sim"
            ),
            func.sum(
                func.cast(ValidacaoOcrModel.ocr_trocou_caracteres, SaInteger)
            ).label("trocou_caracteres_sim"),
        )
        .join(UsuarioModel, UsuarioModel.id == ValidacaoOcrModel.revisor_id)
        .join(RedacaoModel, RedacaoModel.redacao_id == ValidacaoOcrModel.redacao_id)
    )
    revisores_query = filtro_redacoes(revisores_query)
    revisores_query = revisores_query.group_by(
        ValidacaoOcrModel.revisor_id, UsuarioModel.nome
    )

    revisores = [
        RevisorResumoSchema(
            revisor_id=r.revisor_id,
            revisor_nome=r.nome,
            revisado=r.revisado,
            pulou_trechos_sim=int(r.pulou_trechos_sim or 0),
            trocou_palavras_sim=int(r.trocou_palavras_sim or 0),
            trocou_caracteres_sim=int(r.trocou_caracteres_sim or 0),
        )
        for r in revisores_query.all()
    ]

    # ── Query 2: Contagem por status ──
    status_query = (
        db.query(
            RedacaoModel.redacao_status_id,
            func.count(RedacaoModel.redacao_id).label("total"),
            func.count(ValidacaoOcrModel.id).label("validado"),
        )
        .outerjoin(
            ValidacaoOcrModel, ValidacaoOcrModel.redacao_id == RedacaoModel.redacao_id
        )
        .filter(RedacaoModel.redacao_status_id.in_([4, 10, 11]))
    )
    status_query = filtro_redacoes(status_query)
    status_query = status_query.group_by(RedacaoModel.redacao_status_id)

    status_contadores = [
        StatusContadorSchema(
            redacao_status_id=s.redacao_status_id,
            status_label=STATUS_LABELS.get(
                s.redacao_status_id, str(s.redacao_status_id)
            ),
            total=s.total,
            validado=s.validado,
            restante=s.total - s.validado,
        )
        for s in status_query.all()
    ]

    # Garante que os 3 statuses apareçam mesmo sem dados
    status_presentes = {s.redacao_status_id for s in status_contadores}
    for sid, label in STATUS_LABELS.items():
        if sid not in status_presentes:
            status_contadores.append(
                StatusContadorSchema(
                    redacao_status_id=sid,
                    status_label=label,
                    total=0,
                    validado=0,
                    restante=0,
                )
            )
    status_contadores.sort(key=lambda s: s.redacao_status_id)

    return OcrAdminResumoResponse(
        revisores=revisores,
        status_contadores=status_contadores,
    )
