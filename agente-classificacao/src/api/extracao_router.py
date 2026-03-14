"""Router com endpoints para o fluxo de extração de assuntos via webscraping"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from typing import Optional
from math import ceil
from datetime import datetime
from loguru import logger

from ..database import get_db
from ..database.models import QuestaoModel, QuestaoAlternativaModel, DisciplinaModel, AnoModel
from ..database.pg_models import QuestaoAssuntoModel
from ..services.enunciado_cleaner import tratar_enunciado
from .extracao_schemas import (
    AlternativaSchema,
    QuestaoAssuntoSchema,
    QuestaoAssuntoListResponse,
    ProximaQuestaoResponse,
    SalvarAssuntoRequest,
    SalvarAssuntoResponse,
    ExtracaoStatsResponse,
    LimparEnunciadoRequest,
    LimparEnunciadoResponse,
    TratarEnunciadoRequest,
    TratarEnunciadoResponse,
)

router = APIRouter(prefix="/extracao", tags=["Extração de Assuntos"])

# ========================
# LIMPAR ENUNCIADO COM IA
# ========================
_SYSTEM_PROMPT_LIMPAR = """Você é um extrator de enunciados de questões de provas.
O texto que você receberá é um enunciado de questão de prova que pode conter:
- Referências bibliográficas (autor, título, ano, editora, disponível em, acesso em)
- Nomes de obras de arte, livros, poemas
- Créditos de imagens
- Trechos de textos de apoio (fragmentos literários, históricos, jornais)
- O enunciado real da questão (o comando que o aluno deve responder)
- Caracteres especiais ou Unicode corrompidos (acentos duplicados, símbolos matemáticos, letras gregas, macron, overline, combining marks, etc.)

Sua tarefa:
1. Identifique o ENUNCIADO REAL da questão (o comando/pergunta que o aluno deve responder)
2. Se houver um texto de apoio importante que dá contexto à questão, inclua-o também
3. REMOVA: referências bibliográficas, créditos, "Disponível em", "Acesso em", nomes de autores isolados
4. REMOVA ou NORMALIZE caracteres especiais problemáticos:
   - Caracteres Unicode corrompidos ou malformados → remova-os
   - Letras com acentos estranhos em contexto matemático (ex: DÂB, DĈB) → normalize para letras simples (DAB, DCB)
   - Símbolos matemáticos Unicode (√, ∑, ∫, ≤, ≥, π, etc.) → converta para texto descritivo quando possível
   - Macron/overline (¯) sobre letras → remova
   - Qualquer caractere que não seja texto legível em português → remova

Responda APENAS com o texto limpo, sem explicações."""


@router.post(
    "/limpar-enunciado",
    response_model=LimparEnunciadoResponse,
    summary="🧹 Limpar enunciado com IA",
)
async def limpar_enunciado(request: LimparEnunciadoRequest):
    """
    Usa OpenAI para extrair apenas o enunciado real de questões
    que contêm referências de imagens, créditos e lixo textual.
    """
    from ..services.openai_client import OpenAIClient

    if not request.enunciado or len(request.enunciado.strip()) < 10:
        return LimparEnunciadoResponse(
            enunciado_limpo=request.enunciado or "",
            sucesso=False,
            mensagem="Enunciado muito curto",
        )

    try:
        client = OpenAIClient()
        result = client.create_completion(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT_LIMPAR},
                {"role": "user", "content": request.enunciado},
            ],
            max_tokens=300,
            temperature=0.0,
        )
        enunciado_limpo = result.get("content", "").strip()

        if not enunciado_limpo:
            return LimparEnunciadoResponse(
                enunciado_limpo=request.enunciado,
                sucesso=False,
                mensagem="IA retornou vazio",
            )

        logger.debug(
            f"Enunciado limpo pela IA: {len(request.enunciado)} -> {len(enunciado_limpo)} chars"
        )
        return LimparEnunciadoResponse(
            enunciado_limpo=enunciado_limpo,
            sucesso=True,
        )

    except Exception as e:
        logger.error(f"Erro ao limpar enunciado com IA: {e}")
        return LimparEnunciadoResponse(
            enunciado_limpo=request.enunciado,
            sucesso=False,
            mensagem=str(e),
        )


# ========================
# TRATAR ENUNCIADO (limpeza programática, sem IA)
# ========================
@router.post(
    "/tratar-enunciado",
    response_model=TratarEnunciadoResponse,
    summary="🔤 Tratar enunciado - remover HTML, Unicode e caracteres especiais",
)
async def tratar_enunciado_endpoint(request: TratarEnunciadoRequest):
    """
    Limpa o enunciado de forma **programática** (sem chamar IA).

    Remove:
    - Tags HTML (`<p>`, `<img>`, `<br>`, etc.)
    - URLs de imagens
    - Caracteres Unicode problemáticos (combining marks, macron, overline)
    - Notação matemática com diacríticos (DÂB → DAB, DĈB → DCB)
    - Símbolos matemáticos Unicode (√, ≤, ≥, π, ∞, etc.)
    - Letras gregas (α → alfa, β → beta, etc.)
    - Referências bibliográficas e créditos
    - Espaços duplicados e linhas em branco

    Preserva:
    - Acentos normais do português (á, â, ã, é, ê, í, ó, ô, õ, ú, ç)
    - Texto legível em português
    """
    if not request.enunciado or len(request.enunciado.strip()) < 5:
        return TratarEnunciadoResponse(
            enunciado_original=request.enunciado or "",
            enunciado_tratado="",
            sucesso=False,
            motivo_erro="Enunciado muito curto ou vazio",
        )

    try:
        enunciado_tratado, contem_imagem, motivo_erro = tratar_enunciado(
            request.enunciado
        )

        chars_removidos = len(request.enunciado) - len(enunciado_tratado)

        return TratarEnunciadoResponse(
            enunciado_original=request.enunciado,
            enunciado_tratado=enunciado_tratado,
            contem_imagem=contem_imagem,
            caracteres_removidos=max(0, chars_removidos),
            sucesso=motivo_erro is None,
            motivo_erro=motivo_erro,
        )

    except Exception as e:
        logger.error(f"Erro ao tratar enunciado: {e}")
        return TratarEnunciadoResponse(
            enunciado_original=request.enunciado,
            enunciado_tratado=request.enunciado,
            sucesso=False,
            motivo_erro=str(e),
        )


# ========================
# PRÓXIMA QUESTÃO PARA EXTRAIR
# ========================
@router.get(
    "/proxima",
    response_model=ProximaQuestaoResponse,
    summary="🔍 Próxima questão para extração",
    response_description="Retorna a próxima questão não processada para webscraping",
)
async def proxima_questao(
    disciplina_id: int = Query(..., description="ID da disciplina para filtrar"),
    ano_id: Optional[int] = Query(
        3, description="ID do ano/nível (3=Ensino Médio). None=todos"
    ),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
):
    """
    Retorna a próxima questão que ainda não teve extração de assunto tentada.

    **Fluxo:**
    1. Busca questões da disciplina no MySQL
    2. Filtra as que já possuem registro no PostgreSQL (já tentadas)
    3. Pega a primeira pendente em ordem de ID
    4. Trata o enunciado (remove HTML, decodifica entities)
    5. Se contém imagem, registra automaticamente como "pulada" e busca a próxima
    6. Retorna a questão com enunciado limpo pronto para webscraping

    - **disciplina_id**: ID da disciplina (obrigatório)
    """
    # Busca IDs já processados no PostgreSQL (banco separado)
    ids_processados_rows = pg_db.query(QuestaoAssuntoModel.questao_id).all()
    ids_processados = {row[0] for row in ids_processados_rows}

    MAX_SKIP = 100  # Limite de pulos por enunciado vazio

    for _ in range(MAX_SKIP):
        # Busca próxima questão não processada no MySQL
        query = (
            db.query(QuestaoModel)
            .options(
                joinedload(QuestaoModel.disciplina),
                joinedload(QuestaoModel.ano),
                joinedload(QuestaoModel.alternativas),
            )
            .filter(QuestaoModel.disciplina_id == disciplina_id)
            .filter(QuestaoModel.habilidade_id.isnot(None))
        )

        # Filtra por ano/nível (default: Ensino Médio)
        if ano_id is not None:
            query = query.filter(QuestaoModel.ano_id == ano_id)

        # Filtra os já processados (se houver)
        if ids_processados:
            query = query.filter(~QuestaoModel.id.in_(ids_processados))

        questao = query.order_by(QuestaoModel.id).first()

        if not questao:
            raise HTTPException(
                status_code=404,
                detail=f"Nenhuma questão pendente para disciplina_id={disciplina_id}",
            )

        # Trata o enunciado (remove <img>, limpa HTML)
        enunciado_tratado, contem_imagem, motivo_erro = tratar_enunciado(
            questao.enunciado
        )

        disc_nome = questao.disciplina.descricao if questao.disciplina else None
        ano_nome = questao.ano.descricao if questao.ano else None

        if motivo_erro:
            # Enunciado vazio após tratamento (só imagem sem texto, ou vazio)
            # Registra como pulada e continua para a próxima
            registro = QuestaoAssuntoModel(
                questao_id=questao.id,
                questao_id_str=questao.questao_id,
                disciplina_id=questao.disciplina_id,
                disciplina_nome=disc_nome,
                classificacoes=[],
                enunciado_original=questao.enunciado,
                enunciado_tratado=enunciado_tratado or None,
                extracao_feita=False,
                contem_imagem=contem_imagem,
                motivo_erro=motivo_erro,
            )
            pg_db.add(registro)
            pg_db.commit()
            ids_processados.add(questao.id)
            logger.info(f"Questão {questao.id} pulada: {motivo_erro}")
            continue

        # Preparar alternativas se for múltipla escolha
        alternativas_resp = []
        if questao.tipo == "Múltipla Escolha" and questao.alternativas:
            letras = "abcdefghij"
            for idx, alt in enumerate(sorted(questao.alternativas, key=lambda a: a.ordem or 0)):
                conteudo_limpo, _, _ = tratar_enunciado(alt.conteudo or "")
                alternativas_resp.append(
                    AlternativaSchema(
                        ordem=alt.ordem or 0,
                        conteudo=conteudo_limpo,
                        correta=bool(alt.correta),
                    )
                )

        # Questão válida (pode ter imagem mas tem texto suficiente)
        return ProximaQuestaoResponse(
            id=questao.id,
            questao_id=questao.questao_id,
            enunciado_original=questao.enunciado,
            enunciado_tratado=enunciado_tratado,
            disciplina_id=questao.disciplina_id,
            disciplina_nome=disc_nome,
            habilidade_id=questao.habilidade_id,
            ano_id=questao.ano_id,
            ano_nome=ano_nome,
            tipo=questao.tipo,
            alternativas=alternativas_resp,
            contem_imagem=contem_imagem,
            motivo_erro=None,
        )

    raise HTTPException(
        status_code=404,
        detail=f"Puladas {MAX_SKIP} questões consecutivas sem texto. Verifique a disciplina {disciplina_id}.",
    )


# ========================
# PRÓXIMA QUESTÃO PARA RE-CLASSIFICAR (precisa_verificar)
# ========================
@router.get(
    "/proxima-verificar",
    response_model=ProximaQuestaoResponse,
    summary="🔄 Próxima questão para re-classificação",
    response_description="Retorna a próxima questão com precisa_verificar=True",
)
async def proxima_questao_verificar(
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
):
    """
    Retorna a próxima questão que está marcada como `precisa_verificar=True`
    no PostgreSQL, com dados completos do MySQL (incluindo alternativas).

    Usada pelo agente de reclassificação para re-processar questões duvidosas.
    """
    # Buscar próxima questão com precisa_verificar=True no PostgreSQL
    registro_pg = (
        pg_db.query(QuestaoAssuntoModel)
        .filter(QuestaoAssuntoModel.precisa_verificar == True)
        .order_by(QuestaoAssuntoModel.id)
        .first()
    )

    if not registro_pg:
        raise HTTPException(
            status_code=404,
            detail="Nenhuma questão com precisa_verificar pendente",
        )

    # Buscar dados completos no MySQL (com alternativas)
    questao = (
        db.query(QuestaoModel)
        .options(
            joinedload(QuestaoModel.disciplina),
            joinedload(QuestaoModel.ano),
            joinedload(QuestaoModel.alternativas),
        )
        .filter(QuestaoModel.id == registro_pg.questao_id)
        .first()
    )

    if not questao:
        raise HTTPException(
            status_code=404,
            detail=f"Questão {registro_pg.questao_id} não encontrada no MySQL",
        )

    # Tratar enunciado
    enunciado_tratado, contem_imagem, motivo_erro = tratar_enunciado(
        questao.enunciado
    )

    disc_nome = questao.disciplina.descricao if questao.disciplina else None
    ano_nome = questao.ano.descricao if questao.ano else None

    # Preparar alternativas se for múltipla escolha
    alternativas_resp = []
    if questao.tipo == "Múltipla Escolha" and questao.alternativas:
        letras = "abcdefghij"
        for idx, alt in enumerate(sorted(questao.alternativas, key=lambda a: a.ordem or 0)):
            conteudo_limpo, _, _ = tratar_enunciado(alt.conteudo or "")
            alternativas_resp.append(
                AlternativaSchema(
                    ordem=alt.ordem or 0,
                    conteudo=conteudo_limpo,
                    correta=bool(alt.correta),
                )
            )

    return ProximaQuestaoResponse(
        id=questao.id,
        questao_id=questao.questao_id,
        enunciado_original=questao.enunciado,
        enunciado_tratado=enunciado_tratado or "",
        disciplina_id=questao.disciplina_id,
        disciplina_nome=disc_nome,
        habilidade_id=questao.habilidade_id,
        ano_id=questao.ano_id,
        ano_nome=ano_nome,
        tipo=questao.tipo,
        alternativas=alternativas_resp,
        contem_imagem=contem_imagem,
        motivo_erro=motivo_erro,
    )


# ========================
# SALVAR RESULTADO DA EXTRAÇÃO
# ========================
@router.post(
    "/salvar",
    response_model=SalvarAssuntoResponse,
    summary="💾 Salvar resultado da extração",
    response_description="Salva as classificações de assunto extraídas via webscraping",
)
async def salvar_extracao(
    request: SalvarAssuntoRequest,
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
):
    """
    Salva o resultado da extração de assunto de uma questão.

    **Exemplo de request:**
    ```json
    {
        "questao_id": 1,
        "classificacoes": [
            "História > Brasil > Sistema Colonial > Relações Socioeconômicas e Culturais",
            "História > Brasil > Escravidão"
        ]
    }
    ```
    """
    # Verifica se a questão existe no MySQL
    questao = (
        db.query(QuestaoModel)
        .options(joinedload(QuestaoModel.disciplina))
        .filter(QuestaoModel.id == request.questao_id)
        .first()
    )
    if not questao:
        raise HTTPException(status_code=404, detail="Questão não encontrada no banco")

    # Verifica se já existe registro no PostgreSQL
    existente = (
        pg_db.query(QuestaoAssuntoModel)
        .filter(QuestaoAssuntoModel.questao_id == request.questao_id)
        .first()
    )

    disc_nome = questao.disciplina.descricao if questao.disciplina else None
    enunciado_tratado, _, _ = tratar_enunciado(questao.enunciado)

    # Se o agent enviou enunciado limpo pela IA, usar esse
    if request.enunciado_tratado and len(request.enunciado_tratado.strip()) >= 20:
        enunciado_tratado = request.enunciado_tratado

    if existente:
        # Atualiza o registro existente
        existente.classificacoes = request.classificacoes
        existente.classificacao_nao_enquadrada = request.classificacao_nao_enquadrada
        existente.extracao_feita = True
        existente.motivo_erro = None
        # Se veio no request, usa. Se não, False (padrão de fim de extração)
        if request.precisa_verificar is not None:
            existente.precisa_verificar = request.precisa_verificar
        else:
            existente.precisa_verificar = False
        
        existente.enunciado_tratado = enunciado_tratado or existente.enunciado_tratado
        if request.superpro_id:
            existente.superpro_id = request.superpro_id
        if request.similaridade is not None:
            existente.similaridade = request.similaridade
        if request.enunciado_superpro:
            existente.enunciado_superpro = request.enunciado_superpro
        pg_db.commit()
        logger.success(
            f"Questão {request.questao_id} atualizada. Classif: {len(request.classificacoes)}, Não Enquadrada: {len(request.classificacao_nao_enquadrada)}"
        )
    else:
        # Cria novo registro
        registro = QuestaoAssuntoModel(
            questao_id=questao.id,
            questao_id_str=questao.questao_id,
            superpro_id=request.superpro_id,
            disciplina_id=questao.disciplina_id,
            disciplina_nome=disc_nome,
            classificacoes=request.classificacoes,
            classificacao_nao_enquadrada=request.classificacao_nao_enquadrada,
            enunciado_original=questao.enunciado,
            enunciado_tratado=enunciado_tratado,
            similaridade=request.similaridade,
            extracao_feita=True,
            contem_imagem=False,
            precisa_verificar=request.precisa_verificar if request.precisa_verificar is not None else False,
            enunciado_superpro=request.enunciado_superpro,
            motivo_erro=None,
        )
        pg_db.add(registro)
        pg_db.commit()
        logger.success(
            f"Questão {request.questao_id} salva. Classif: {len(request.classificacoes)}, Não Enquadrada: {len(request.classificacao_nao_enquadrada)}"
        )

    return SalvarAssuntoResponse(
        success=True,
        questao_id=request.questao_id,
        classificacoes=request.classificacoes,
        message=f"Extração salva com {len(request.classificacoes)} classificação(ões)",
    )


# ========================
# LISTAR ASSUNTOS EXTRAÍDOS
# ========================
@router.get(
    "/assuntos",
    response_model=QuestaoAssuntoListResponse,
    summary="📋 Listar assuntos extraídos",
)
async def listar_assuntos(
    page: int = Query(1, ge=1, description="Página"),
    per_page: int = Query(20, ge=1, le=100, description="Itens por página"),
    disciplina_id: Optional[int] = Query(None, description="Filtrar por disciplina"),
    apenas_extraidas: bool = Query(
        False, description="Apenas com extração bem sucedida"
    ),
    apenas_com_imagem: bool = Query(False, description="Apenas questões com imagem"),
    precisa_verificar: Optional[bool] = Query(
        None, description="Filtrar por precisa_verificar (True/False)"
    ),
    tem_classificacao: Optional[bool] = Query(
        None, description="Filtrar por presença de classificação"
    ),
    questao_id: Optional[int] = Query(None, description="Filtrar por ID da questão"),
    superpro_id: Optional[int] = Query(None, description="Filtrar por ID SuperProfessor"),
    data_inicio: Optional[str] = Query(None, description="Data início (YYYY-MM-DD)"),
    data_fim: Optional[str] = Query(None, description="Data fim (YYYY-MM-DD)"),
    pg_db: Session = Depends(get_db),
):
    """Lista os registros de assuntos com paginação e filtros."""
    query = pg_db.query(QuestaoAssuntoModel)

    if disciplina_id:
        query = query.filter(QuestaoAssuntoModel.disciplina_id == disciplina_id)
    if apenas_extraidas:
        query = query.filter(QuestaoAssuntoModel.extracao_feita == True)
    if apenas_com_imagem:
        query = query.filter(QuestaoAssuntoModel.contem_imagem == True)
    if precisa_verificar is not None:
        query = query.filter(QuestaoAssuntoModel.precisa_verificar == precisa_verificar)
    
    if questao_id:
        query = query.filter(QuestaoAssuntoModel.questao_id == questao_id)
    if superpro_id:
        query = query.filter(QuestaoAssuntoModel.superpro_id == superpro_id)

    if data_inicio:
        try:
            dt_inicio = datetime.strptime(data_inicio, "%Y-%m-%d")
            query = query.filter(QuestaoAssuntoModel.criado_em >= dt_inicio)
        except ValueError:
            pass
    if data_fim:
        try:
            dt_fim = datetime.strptime(data_fim, "%Y-%m-%d")
            # Adiciona 1 dia para incluir o dia final completo
            query = query.filter(QuestaoAssuntoModel.criado_em <= dt_fim)
        except ValueError:
            pass
    
    if tem_classificacao is not None:
        if tem_classificacao:
            query = query.filter(
                QuestaoAssuntoModel.classificacoes.isnot(None),
                func.json_length(QuestaoAssuntoModel.classificacoes) > 0
            )
        else:
            query = query.filter(
                (QuestaoAssuntoModel.classificacoes.is_(None)) | 
                (func.json_length(QuestaoAssuntoModel.classificacoes) == 0)
            )

    total = query.count()
    pages = ceil(total / per_page) if total > 0 else 1
    offset = (page - 1) * per_page

    registros = (
        query.order_by(QuestaoAssuntoModel.id).offset(offset).limit(per_page).all()
    )

    return QuestaoAssuntoListResponse(
        data=[QuestaoAssuntoSchema.model_validate(r) for r in registros],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


# ========================
# BUSCAR ASSUNTO POR QUESTÃO
# ========================
@router.get(
    "/assuntos/{questao_id}",
    response_model=QuestaoAssuntoSchema,
    summary="🔎 Buscar assunto de uma questão",
)
async def buscar_assunto(questao_id: int, pg_db: Session = Depends(get_db)):
    """Retorna o registro de assunto de uma questão específica."""
    registro = (
        pg_db.query(QuestaoAssuntoModel)
        .filter(QuestaoAssuntoModel.questao_id == questao_id)
        .first()
    )
    if not registro:
        raise HTTPException(
            status_code=404, detail="Assunto não encontrado para esta questão"
        )
    return QuestaoAssuntoSchema.model_validate(registro)


# ========================
# ESTATÍSTICAS DE EXTRAÇÃO
# ========================
@router.get(
    "/stats",
    response_model=list[ExtracaoStatsResponse],
    summary="📊 Estatísticas de extração",
    response_description="Progresso da extração por disciplina",
)
async def estatisticas_extracao(
    ano_id: Optional[int] = Query(
        3, description="ID do ano/nível (3=Ensino Médio). None=todos"
    ),
    db: Session = Depends(get_db),
    pg_db: Session = Depends(get_db),
):
    """
    Retorna estatísticas detalhadas do progresso de extração
    agrupadas por disciplina.
    """
    # Total de questões por disciplina no MySQL
    disciplinas = db.query(DisciplinaModel).order_by(DisciplinaModel.id).all()

    stats = []
    for disc in disciplinas:
        total_query = db.query(QuestaoModel).filter(
            QuestaoModel.disciplina_id == disc.id,
            QuestaoModel.habilidade_id.isnot(None),
        )
        if ano_id is not None:
            total_query = total_query.filter(QuestaoModel.ano_id == ano_id)
        total = total_query.count()

        extraidas = (
            pg_db.query(QuestaoAssuntoModel)
            .filter(
                QuestaoAssuntoModel.disciplina_id == disc.id,
                QuestaoAssuntoModel.extracao_feita == True,
            )
            .count()
        )

        com_imagem = (
            pg_db.query(QuestaoAssuntoModel)
            .filter(
                QuestaoAssuntoModel.disciplina_id == disc.id,
                QuestaoAssuntoModel.contem_imagem == True,
            )
            .count()
        )

        com_erro = (
            pg_db.query(QuestaoAssuntoModel)
            .filter(
                QuestaoAssuntoModel.disciplina_id == disc.id,
                QuestaoAssuntoModel.extracao_feita == False,
                QuestaoAssuntoModel.contem_imagem == False,
            )
            .count()
        )

        processadas = extraidas + com_imagem + com_erro
        pendentes = total - processadas
        percentual = (processadas / total * 100) if total > 0 else 0

        stats.append(
            ExtracaoStatsResponse(
                disciplina_id=disc.id,
                disciplina_nome=disc.descricao,
                total_questoes=total,
                extraidas=extraidas,
                com_imagem=com_imagem,
                com_erro=com_erro,
                pendentes=pendentes,
                percentual_concluido=round(percentual, 2),
            )
        )

    return stats


# ========================
# RESET - LIMPAR BANCO DE EXTRAÇÃO
# ========================
@router.delete(
    "/reset",
    summary="🗑️ Resetar banco de extração",
    response_description="Remove todos os registros de extração do PostgreSQL",
)
async def reset_extracao(
    pg_db: Session = Depends(get_db),
):
    """
    Remove TODOS os registros da tabela questao_assuntos no PostgreSQL.
    Use com cuidado — essa operação é irreversível.
    """
    count = pg_db.query(QuestaoAssuntoModel).count()
    pg_db.query(QuestaoAssuntoModel).delete()
    pg_db.commit()
    logger.warning(f"🗑️ RESET: {count} registros removidos de questao_assuntos")
    return {
        "success": True,
        "deleted": count,
        "message": f"{count} registros removidos",
    }

