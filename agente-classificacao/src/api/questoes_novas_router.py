"""Router com endpoints para novas questões a classificar"""

from fastapi import APIRouter, Depends, HTTPException, Query, Path, status
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_
from typing import Optional, List
from datetime import datetime, timedelta
from math import ceil
from loguru import logger

from ..config import settings
from ..database import get_db
from ..database.models_questoes_novas import (
    QuestaoNovaModel,
    AlternativaNovaModel,
    ClassificacaoNovaModel,
    ClassificacaoNovaHistoricoModel,
)
from ..database.pg_usuario_models import UsuarioModel
from .questoes_novas_schemas import (
    QuestaoNovaListaSchema,
    QuestaoNovaDetalheSchema,
    AlternativaNovaSchema,
    ClassificacaoNovaRequest,
    ClassificacaoNovaResponse,
    ClassificacaoNovaDetalheSchema,
    QuestoesNovasListaResponse,
    FiltrosQuestoes,
    SincronizarRequest,
    SincronizarResponse,
    EstatisticasResponse,
    StatsPorClassificador,
    HistoricoQuestaoResponse,
    ClassificacaoHistoricoSchema,
    AcaoHistoricoEnum,
    StatusQuestaoEnum,
    ErroResponse,
)
from .classificacao_router import get_usuario_atual
from ..services.questoes_novas_service import QuestoesNovasService

router = APIRouter(prefix="/questoes-novas", tags=["Novas Questões"])


# ========================
# Endpoints Principais
# ========================


@router.get("/", response_model=QuestoesNovasListaResponse)
def listar_questoes_novas(
    pagina: int = Query(1, ge=1, description="Número da página"),
    tamanho: int = Query(20, ge=1, le=100, description="Itens por página"),
    status: Optional[str] = Query(None, description="Filtrar por status"),
    disciplina_sp: Optional[str] = Query(None, description="Filtrar por disciplina"),
    contem_imagem: Optional[bool] = Query(
        None, description="Filtrar por presença de imagem"
    ),
    ordenar_por: str = Query("created_at", description="Campo para ordenação"),
    ordem_desc: bool = Query(True, description="Ordenação decrescente"),
    db: Session = Depends(get_db),
    current_user=Depends(get_usuario_atual),
):
    """
    Lista questões não classificadas com paginação e filtros

    **Parâmetros:**
    - `pagina`: número da página (padrão: 1)
    - `tamanho`: itens por página (padrão: 20, máximo: 100)
    - `status`: filtrar por status (nao_classificada, em_progresso, classificada, rejeitada, duplicada)
    - `disciplina_sp`: filtrar por disciplina original
    - `contem_imagem`: filtrar questões com/sem imagem
    - `ordenar_por`: campo para ordenação (created_at, status, disciplina_sp)
    - `ordem_desc`: ordenação decrescente (padrão: true)

    **Returns:**
    Lista paginada de questões com metadados de paginação
    """
    try:
        logger.info(
            f"Listando questões - página={pagina}, tamanho={tamanho}, usuário={current_user.id}"
        )

        # Construir query base
        query = db.query(QuestaoNovaModel)

        # Aplicar filtros
        if status:
            if status not in [s.value for s in StatusQuestaoEnum]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Status inválido: {status}",
                )
            query = query.filter(QuestaoNovaModel.status == status)

        if disciplina_sp:
            query = query.filter(
                QuestaoNovaModel.disciplina_sp.ilike(f"%{disciplina_sp}%")
            )

        if contem_imagem is not None:
            query = query.filter(QuestaoNovaModel.contem_imagem == contem_imagem)

        # Contar total antes da paginação
        total = query.count()
        total_paginas = ceil(total / tamanho) if total > 0 else 1

        # Aplicar ordenação
        if ordem_desc:
            query = query.order_by(desc(getattr(QuestaoNovaModel, ordenar_por)))
        else:
            query = query.order_by(getattr(QuestaoNovaModel, ordenar_por))

        # Aplicar paginação
        offset = (pagina - 1) * tamanho
        questoes = query.offset(offset).limit(tamanho).all()

        # Converter para schema
        itens = [QuestaoNovaListaSchema.from_orm(q) for q in questoes]

        return QuestoesNovasListaResponse(
            total=total,
            pagina=pagina,
            tamanho=tamanho,
            total_paginas=total_paginas,
            itens=itens,
        )

    except Exception as e:
        logger.error(f"Erro ao listar questões: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao listar questões",
        )


@router.get("/{questao_id}", response_model=QuestaoNovaDetalheSchema)
def obter_detalhe_questao(
    questao_id: int = Path(..., ge=1, description="ID da questão"),
    db: Session = Depends(get_db),
    current_user=Depends(get_usuario_atual),
):
    """
    Obter detalhes completos de uma questão com alternativas e classificação

    **Returns:**
    Questão com dados completos, alternativas e classificação atual (se houver)
    """
    try:
        logger.info(f"Obtendo detalhes da questão {questao_id}")

        # Buscar questão
        questao = (
            db.query(QuestaoNovaModel).filter(QuestaoNovaModel.id == questao_id).first()
        )

        if not questao:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Questão {questao_id} não encontrada",
            )

        # Buscar alternativas
        alternativas = (
            db.query(AlternativaNovaModel)
            .filter(AlternativaNovaModel.questao_nova_id == questao_id)
            .order_by(AlternativaNovaModel.letra)
            .all()
        )

        # Buscar classificação
        classificacao = (
            db.query(ClassificacaoNovaModel)
            .filter(ClassificacaoNovaModel.questao_nova_id == questao_id)
            .first()
        )

        # Converter para schema
        resultado = QuestaoNovaDetalheSchema.from_orm(questao)
        resultado.alternativas = [
            AlternativaNovaSchema.from_orm(a) for a in alternativas
        ]

        if classificacao:
            resultado.classificacao = {
                "id": classificacao.id,
                "habilidades": classificacao.habilidades_identificadas,
                "disciplinas": classificacao.disciplinas_classificadas,
                "scores": classificacao.scores_confianca,
                "justificativa": classificacao.justificativa,
                "data": classificacao.data_criacao,
            }

        return resultado

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao obter detalhes: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao obter detalhes da questão",
        )


@router.post("/{questao_id}/classificar", response_model=ClassificacaoNovaResponse)
def classificar_questao(
    questao_id: int,
    dados: ClassificacaoNovaRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_usuario_atual),
):
    """
    Salvar classificação de uma questão

    **Parâmetros no body:**
    - `habilidades_identificadas`: lista de IDs das habilidades
    - `disciplinas_classificadas`: lista de IDs das disciplinas (mínimo 1)
    - `justificativa`: texto explicando a classificação (10-1000 caracteres)
    - `scores_confianca`: dict com scores (0-1) para cada disciplina

    **Returns:**
    Dados da classificação salva
    """
    try:
        logger.info(f"Classificando questão {questao_id} por usuário {current_user.id}")

        # Verificar existência da questão
        questao = (
            db.query(QuestaoNovaModel).filter(QuestaoNovaModel.id == questao_id).first()
        )

        if not questao:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Questão {questao_id} não encontrada",
            )

        # Verificar se já existe classificação
        classificacao_existente = (
            db.query(ClassificacaoNovaModel)
            .filter(ClassificacaoNovaModel.questao_nova_id == questao_id)
            .first()
        )

        if classificacao_existente:
            # Armazenar dados antigos no histórico
            historico = ClassificacaoNovaHistoricoModel(
                questao_nova_id=questao_id,
                classificacao_nova_id=classificacao_existente.id,
                acao=AcaoHistoricoEnum.ATUALIZADA,
                dados_anterior={
                    "habilidades": classificacao_existente.habilidades_identificadas,
                    "disciplinas": classificacao_existente.disciplinas_classificadas,
                    "justificativa": classificacao_existente.justificativa,
                },
                alterado_por_id=current_user.id,
            )
            db.add(historico)

            # Atualizar classificação existente
            classificacao_existente.habilidades_identificadas = (
                dados.habilidades_identificadas
            )
            classificacao_existente.disciplinas_classificadas = (
                dados.disciplinas_classificadas
            )
            classificacao_existente.justificativa = dados.justificativa
            classificacao_existente.scores_confianca = dados.scores_confianca
            classificacao = classificacao_existente
        else:
            # Criar nova classificação
            classificacao = ClassificacaoNovaModel(
                questao_nova_id=questao_id,
                habilidades_identificadas=dados.habilidades_identificadas,
                disciplinas_classificadas=dados.disciplinas_classificadas,
                justificativa=dados.justificativa,
                scores_confianca=dados.scores_confianca,
                classificado_por_id=current_user.id,
            )

            # Registrar no histórico
            historico = ClassificacaoNovaHistoricoModel(
                questao_nova_id=questao_id,
                classificacao_nova_id=None,  # Será preenchido após commit
                acao=AcaoHistoricoEnum.CRIADA,
                dados_novo={
                    "habilidades": dados.habilidades_identificadas,
                    "disciplinas": dados.disciplinas_classificadas,
                    "justificativa": dados.justificativa,
                },
                alterado_por_id=current_user.id,
            )
            db.add(historico)
            db.add(classificacao)

        # Atualizar status e data da questão
        questao.status = StatusQuestaoEnum.CLASSIFICADA
        questao.classificado_por_id = current_user.id
        questao.data_classificacao = datetime.utcnow()

        # Commit
        db.commit()
        db.refresh(classificacao)

        logger.info(f"Questão {questao_id} classificada com sucesso")

        return ClassificacaoNovaResponse(
            id=classificacao.id,
            questao_nova_id=classificacao.questao_nova_id,
            status="classificada",
            data_criacao=classificacao.data_criacao,
            mensagem="Classificação salva com sucesso",
        )

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao classificar questão: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao salvar classificação",
        )


# ========================
# Endpoints de Sincronização
# ========================


@router.post("/sync/superpro", response_model=SincronizarResponse)
def sincronizar_superpro(
    dados: SincronizarRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_usuario_atual),
):
    """
    Sincronizar questões da base local superpro_db

    **Restrição:** Apenas usuários admin podem executar

    **Parâmetros:**
    - `apenas_nao_classificadas`: sincronizar apenas questões não classificadas (padrão: true)
    - `limite`: limite de questões para sincronizar (padrão: sem limite)

    **Returns:**
    Resumo da sincronização realizada
    """
    # Verificar se usuário é admin
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas administradores podem sincronizar",
        )

    try:
        logger.info(f"Iniciando sincronização - usuário admin {current_user.id}")

        service = QuestoesNovasService(db)
        resultado = service.sincronizar_superpro(
            apenas_nao_classificadas=dados.apenas_nao_classificadas, limite=dados.limite
        )

        logger.info(f"Sincronização concluída: {resultado}")

        return SincronizarResponse(
            sucesso=True,
            questoes_sincronizadas=resultado.get("total", 0),
            questoes_adicionadas=resultado.get("adicionadas", 0),
            questoes_atualizadas=resultado.get("atualizadas", 0),
            questoes_com_erro=resultado.get("erros", 0),
            timestamp=datetime.utcnow(),
            mensagem=f"Sincronização realizada com sucesso",
        )

    except Exception as e:
        logger.error(f"Erro na sincronização: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao sincronizar: {str(e)}",
        )


# ========================
# Endpoints de Estatísticas
# ========================


@router.get("/stats/resumo", response_model=EstatisticasResponse)
def obter_estatisticas(
    db: Session = Depends(get_db), current_user=Depends(get_usuario_atual)
):
    """
    Retorna estatísticas gerais de classificação

    **Returns:**
    - Total de questões
    - Contagem por status
    - Percentual de conclusão
    - Tempo médio de classificação
    - Usuários ativos
    """
    try:
        # Contar questões por status
        total = db.query(func.count(QuestaoNovaModel.id)).scalar() or 0
        nao_classificadas = (
            db.query(func.count(QuestaoNovaModel.id))
            .filter(QuestaoNovaModel.status == StatusQuestaoEnum.NAO_CLASSIFICADA)
            .scalar()
            or 0
        )
        em_progresso = (
            db.query(func.count(QuestaoNovaModel.id))
            .filter(QuestaoNovaModel.status == StatusQuestaoEnum.EM_PROGRESSO)
            .scalar()
            or 0
        )
        classificadas = (
            db.query(func.count(QuestaoNovaModel.id))
            .filter(QuestaoNovaModel.status == StatusQuestaoEnum.CLASSIFICADA)
            .scalar()
            or 0
        )
        rejeitadas = (
            db.query(func.count(QuestaoNovaModel.id))
            .filter(QuestaoNovaModel.status == StatusQuestaoEnum.REJEITADA)
            .scalar()
            or 0
        )
        duplicadas = (
            db.query(func.count(QuestaoNovaModel.id))
            .filter(QuestaoNovaModel.status == StatusQuestaoEnum.DUPLICADA)
            .scalar()
            or 0
        )

        # Calcular percentual
        percentual = (classificadas / total * 100) if total > 0 else 0

        # Usuários ativos
        usuarios_ativos = (
            db.query(
                func.count(func.distinct(ClassificacaoNovaModel.classificado_por_id))
            ).scalar()
            or 0
        )

        return EstatisticasResponse(
            total_questoes=total,
            nao_classificadas=nao_classificadas,
            em_progresso=em_progresso,
            classificadas=classificadas,
            rejeitadas=rejeitadas,
            duplicadas=duplicadas,
            percentual_concluido=round(percentual, 2),
            usuarios_ativos=usuarios_ativos,
        )

    except Exception as e:
        logger.error(f"Erro ao obter estatísticas: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao calcular estatísticas",
        )


@router.get("/stats/por-classificador", response_model=List[StatsPorClassificador])
def obter_stats_por_classificador(
    db: Session = Depends(get_db), current_user=Depends(get_usuario_atual)
):
    """
    Retorna estatísticas de classificação por usuário

    **Returns:**
    Lista de usuários com total de questões classificadas e datas
    """
    try:
        stats = (
            db.query(
                UsuarioModel.id,
                UsuarioModel.nome,
                func.count(ClassificacaoNovaModel.id).label("total"),
                func.min(ClassificacaoNovaModel.data_criacao).label("primeira"),
                func.max(ClassificacaoNovaModel.data_criacao).label("ultima"),
            )
            .join(
                ClassificacaoNovaModel,
                ClassificacaoNovaModel.classificado_por_id == UsuarioModel.id,
                isouter=True,
            )
            .group_by(UsuarioModel.id, UsuarioModel.nome)
            .all()
        )

        resultado = []
        for stat in stats:
            resultado.append(
                StatsPorClassificador(
                    classificador_id=stat.id,
                    classificador_nome=stat.nome,
                    total_classificadas=stat.total or 0,
                    primeira_classificacao=stat.primeira,
                    ultima_classificacao=stat.ultima,
                )
            )

        return resultado

    except Exception as e:
        logger.error(f"Erro ao obter stats: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao calcular estatísticas",
        )


# ========================
# Endpoints de Histórico
# ========================


@router.get("/{questao_id}/historico", response_model=HistoricoQuestaoResponse)
def obter_historico_questao(
    questao_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_usuario_atual),
):
    """
    Obter histórico de mudanças de uma questão

    **Returns:**
    Lista com todas as alterações realizadas na classificação
    """
    try:
        # Verificar existência
        questao = (
            db.query(QuestaoNovaModel).filter(QuestaoNovaModel.id == questao_id).first()
        )

        if not questao:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Questão {questao_id} não encontrada",
            )

        # Buscar histórico
        historico = (
            db.query(ClassificacaoNovaHistoricoModel)
            .filter(ClassificacaoNovaHistoricoModel.questao_nova_id == questao_id)
            .order_by(desc(ClassificacaoNovaHistoricoModel.data_alteracao))
            .all()
        )

        itens = []
        for h in historico:
            usuario_nome = None
            if h.alterado_por_id:
                usuario = (
                    db.query(UsuarioModel)
                    .filter(UsuarioModel.id == h.alterado_por_id)
                    .first()
                )
                usuario_nome = usuario.nome if usuario else None

            itens.append(
                ClassificacaoHistoricoSchema(
                    id=h.id,
                    questao_nova_id=h.questao_nova_id,
                    acao=h.acao,
                    dados_anterior=h.dados_anterior,
                    dados_novo=h.dados_novo,
                    alterado_por_id=h.alterado_por_id,
                    alterado_por_nome=usuario_nome,
                    data_alteracao=h.data_alteracao,
                )
            )

        return HistoricoQuestaoResponse(questao_id=questao_id, historico=itens)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao obter histórico: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao obter histórico",
        )
