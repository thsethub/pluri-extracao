"""
Agente IA Classificador — Pipeline baseado em Prompts por Disciplina
====================================================================

Fluxo:
1. Recebe questao_id
2. Busca habilidade_id no MySQL
3. Consulta habilidade_modulos para obter módulos válidos + disciplina
4. Carrega prompt da disciplina (prompts/{disciplina}.json)
5. Monta prompt contextualizado com módulos possíveis + texto da questão
6. Chama GPT-4o-mini para classificação
7. Valida resposta contra módulos permitidos
8. Salva com justificativas
"""
import os
import json
import time
import asyncio
import traceback
import re
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import func, distinct
from sqlalchemy.orm import Session, joinedload
from loguru import logger
from typing import List, Optional, Dict, Any

from ..database import get_pg_db, get_db, PgSessionLocal, SessionLocal
from ..database.models import QuestaoModel, HabilidadeModel
from ..database.pg_usuario_models import ClassificacaoUsuarioModel
from ..database.pg_ia_models import ClassificacaoAgenteIaModel, QuestaoEmbeddingModel
from ..database.pg_modulo_models import HabilidadeModuloModel
from .ia_classificacao_schemas import IAClassificarRequest, IAClassificarResponse, IARetreinarResponse
from ..services.openai_client import OpenAIClient

router = APIRouter(prefix="/classificacao-ia", tags=["Classificação IA"])

# --------------------------------------------------------------------------- #
# Cache de prompts e flags de controle
# --------------------------------------------------------------------------- #
_PROMPTS_CACHE: Dict[str, dict] = {}
PROMPTS_DIR = os.path.join(os.getcwd(), "prompts")
CANCEL_VALIDATION = False

@router.post("/cancelar-validacao")
def cancelar_validacao():
    """Gatilho para interromper a execução massiva de classificação (SSE ou Background)"""
    global CANCEL_VALIDATION
    CANCEL_VALIDATION = True
    logger.warning("Sinal de CANCELAMENTO recebido. A validação será interrompida!")
    return {"message": "Validação parada com sucesso."}


def slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r'[áàãâä]', 'a', s)
    s = re.sub(r'[éèêë]', 'e', s)
    s = re.sub(r'[íìîï]', 'i', s)
    s = re.sub(r'[óòõôö]', 'o', s)
    s = re.sub(r'[úùûü]', 'u', s)
    s = re.sub(r'[ç]', 'c', s)
    s = re.sub(r'[^a-z0-9]+', '_', s)
    return s.strip('_')


def load_discipline_prompt(disciplina: str) -> Optional[dict]:
    """Carrega o prompt de uma disciplina do cache ou do disco."""
    slug = slugify(disciplina)
    if slug in _PROMPTS_CACHE:
        return _PROMPTS_CACHE[slug]
    
    filepath = os.path.join(PROMPTS_DIR, f"{slug}.json")
    if not os.path.exists(filepath):
        logger.warning(f"Prompt não encontrado para disciplina '{disciplina}' ({filepath})")
        return None
    
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    _PROMPTS_CACHE[slug] = data
    logger.info(f"Prompt carregado para '{disciplina}': {len(data.get('modulos', []))} módulos")
    return data


def build_classification_prompt(
    prompt_data: dict,
    modulos_validos: List[str],
    texto_questao: str,
    habilidade_info: str
) -> str:
    """Monta o prompt completo para o LLM com os módulos filtrados."""
    
    # Filtrar apenas os módulos que são válidos para esta habilidade
    modulos_com_criterios = []
    modulos_dict = {m["nome"]: m for m in prompt_data.get("modulos", [])}
    
    for mod_nome in modulos_validos:
        if mod_nome in modulos_dict:
            m = modulos_dict[mod_nome]
            modulos_com_criterios.append(
                f'- **{m["nome"]}**\n'
                f'  Escopo: {m["escopo"]}\n'
                f'  Incluir quando: {m["incluir_quando"]}\n'
                f'  NÃO incluir quando: {m["nao_incluir_quando"]}\n'
                f'  Diferenciador: {m["diferenciador"]}'
            )
        else:
            modulos_com_criterios.append(f'- **{mod_nome}** (sem critérios detalhados disponíveis)')
    
    modulos_text = "\n\n".join(modulos_com_criterios)
    
    system_prompt = f"""Você é um especialista em classificação de questões educacionais da disciplina {prompt_data['disciplina']}.

INSTRUÇÃO GERAL DA DISCIPLINA:
{prompt_data.get('instrucao_geral', 'Classifique de acordo com o conteúdo principal da questão.')}

REGRAS PARA MÚLTIPLOS MÓDULOS:
{prompt_data.get('regras_multi_modulo', 'Atribua múltiplos módulos somente quando genuinamente necessário.')}

REGRAS OBRIGATÓRIAS:
1. Você SÓ pode escolher módulos da lista abaixo. NUNCA invente módulos.
2. Para CADA módulo escolhido, forneça uma justificativa de 1-2 frases.
3. As justificativas de módulos diferentes NÃO DEVEM se interseccionar em escopo.
4. Se dois módulos cobrem o mesmo aspecto, escolha o MAIS ESPECÍFICO.
5. Escolha no MÍNIMO 1 módulo e no MÁXIMO 3 módulos.
6. Responda APENAS com JSON válido no formato especificado."""

    user_prompt = f"""HABILIDADE TRIEDUC DA QUESTÃO:
{habilidade_info}

MÓDULOS POSSÍVEIS (com critérios de classificação):

{modulos_text}

TEXTO DA QUESTÃO:
{texto_questao[:4000]}

Responda APENAS com JSON no formato:
{{
  "modulos": [
    {{"nome": "Nome Exato do Módulo", "justificativa": "Por que este módulo se aplica..."}}
  ]
}}"""

    return system_prompt, user_prompt


# --------------------------------------------------------------------------- #
# Endpoint principal de classificação
# --------------------------------------------------------------------------- #
@router.post("/classificar", response_model=IAClassificarResponse)
def classificar_questao(
    request: IAClassificarRequest,
    pg_db: Session = Depends(get_pg_db),
    db: Session = Depends(get_db)
):
    try:
        start_time = time.time()
        
        # 1. Obter Questão no MySQL
        q = db.query(QuestaoModel).options(
            joinedload(QuestaoModel.habilidade)
        ).filter(QuestaoModel.id == request.questao_id).first()
        
        if not q:
            raise HTTPException(status_code=404, detail="Questão não encontrada no MySQL")
        
        # 2. Preparar Texto
        texto_final = request.texto
        if not texto_final:
            partes = []
            if q.texto_base:
                partes.append(f"Texto Base: {q.texto_base}")
            if q.enunciado:
                partes.append(f"Enunciado: {q.enunciado}")
            texto_final = "\n".join(partes)
        
        if not texto_final or len(texto_final.strip()) < 10:
            raise HTTPException(status_code=400, detail="Texto da questão muito curto ou ausente")

        # 3. Preparar Metadados da Habilidade Trieduc
        habilidade_trieduc_data = None
        habilidade_info = "Não disponível"
        habilidade_id = None
        
        if q.habilidade:
            habilidade_id = q.habilidade.id
            habilidade_trieduc_data = {
                "id": q.habilidade.id,
                "hab_id": q.habilidade.hab_id,
                "sigla": q.habilidade.sigla,
                "descricao": q.habilidade.descricao
            }
            habilidade_info = f"{q.habilidade.sigla}: {q.habilidade.descricao}"
        
        # 4. Buscar módulos válidos para esta habilidade
        modulos_habilidade = pg_db.query(HabilidadeModuloModel).filter(
            HabilidadeModuloModel.habilidade_id == habilidade_id
        ).all() if habilidade_id else []
        
        if not modulos_habilidade:
            # Fallback: buscar pela descrição da habilidade
            if q.habilidade:
                modulos_habilidade = pg_db.query(HabilidadeModuloModel).filter(
                    HabilidadeModuloModel.habilidade_descricao == q.habilidade.descricao
                ).all()
        
        if not modulos_habilidade:
            logger.warning(f"QID {request.questao_id}: Nenhum módulo mapeado para habilidade {habilidade_id}")
            raise HTTPException(
                status_code=422,
                detail=f"Nenhum módulo mapeado para habilidade_id={habilidade_id}"
            )
        
        # 5. Identificar disciplina e módulos válidos
        disciplina = modulos_habilidade[0].disciplina
        modulos_validos = list(set(m.modulo for m in modulos_habilidade))
        descricoes_modulos = {m.modulo: m.descricao for m in modulos_habilidade}
        modulos_possiveis = modulos_validos.copy()
        
        logger.info(
            f"QID {request.questao_id}: Disciplina={disciplina}, "
            f"Módulos válidos={len(modulos_validos)}, Hab={habilidade_info[:50]}"
        )
        
        # 6. Carregar prompt da disciplina
        prompt_data = load_discipline_prompt(disciplina)
        if not prompt_data:
            raise HTTPException(
                status_code=500,
                detail=f"Prompt não encontrado para disciplina '{disciplina}'"
            )
        
        # 7. Classificar via LLM
        system_prompt, user_prompt = build_classification_prompt(
            prompt_data, modulos_validos, texto_final, habilidade_info
        )
        
        # Buscar imagens em base64 embutidas na questão
        base64_images = []
        if isinstance(texto_final, str):
            pattern = r'<img[^>]+src=["\'](data:image/[a-zA-Z0-9]+;base64,[^"\']+)["\'][^>]*>'
            import re
            for m in re.finditer(pattern, texto_final):
                base64_images.append(m.group(1))

        # Montar payload multimodal
        user_content = [{"type": "text", "text": user_prompt}]
        for img_data in base64_images:
            user_content.append({"type": "image_url", "image_url": {"url": img_data}})

        client = OpenAIClient()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content if base64_images else user_prompt}
        ]
        
        response = client.create_completion(messages, model="gpt-5.2")
        tokens_used = response.get("tokens_used", 0)
        llm_time_ms = response.get("processing_time_ms", 0)
        # GPT-5.2: custo médio estimado
        custo_estimado = tokens_used * 0.00002  # custo em USD
        
        # 8. Parsear resposta
        try:
            content = response["content"]
            # Limpar possível markdown
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            data = json.loads(content)
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.error(f"QID {request.questao_id}: Erro ao parsear resposta LLM: {response.get('content', '')[:200]}")
            raise HTTPException(status_code=500, detail="Resposta inválida do LLM")
        
        # 9. Validar módulos contra lista permitida
        modulos_preditos = []
        justificativas = {}
        
        for mod in data.get("modulos", []):
            nome = mod.get("nome", "")
            justificativa = mod.get("justificativa", "")
            
            if nome in modulos_validos:
                modulos_preditos.append(nome)
                justificativas[nome] = justificativa
            else:
                # Tentar match aproximado (case-insensitive)
                match_found = False
                for mv in modulos_validos:
                    if mv.lower().strip() == nome.lower().strip():
                        modulos_preditos.append(mv)
                        justificativas[mv] = justificativa
                        match_found = True
                        break
                if not match_found:
                    logger.warning(f"QID {request.questao_id}: Módulo inválido ignorado: '{nome}'")
        
        if not modulos_preditos:
            logger.error(f"QID {request.questao_id}: Nenhum módulo válido retornado pelo LLM")
            # Fallback: usar o primeiro módulo válido
            modulos_preditos = [modulos_validos[0]]
            justificativas = {modulos_validos[0]: "Classificação padrão (fallback automático)"}
        
        # 10. Persistir resultado
        prompt_slug = slugify(disciplina)
        modelo_nome = f"gpt-5.2_prompt_{prompt_slug}"
        
        try:
            existing = pg_db.query(ClassificacaoAgenteIaModel).filter(
                ClassificacaoAgenteIaModel.questao_id == request.questao_id
            ).first()
            
            record_data = {
                "enunciado": (q.enunciado or "")[:1000],
                "modulos_preditos": modulos_preditos,
                "justificativas": justificativas,
                "modulos_possiveis": modulos_possiveis,
                "descricoes_modulos": descricoes_modulos,
                "habilidade_trieduc": habilidade_trieduc_data,
                "disciplina": disciplina,
                "categorias_preditas": [],
                "confianca_media": 1.0,
                "modelo_utilizado": modelo_nome,
                "prompt_version": "v1",
                "usou_llm": True,
            }
            
            # Métricas de custo/tempo para logging
            logger.info(
                f"QID {request.questao_id} METRICS: "
                f"tokens={tokens_used} custo=${custo_estimado:.6f} "
                f"llm_time={llm_time_ms}ms"
            )
            
            if existing:
                for k, v in record_data.items():
                    setattr(existing, k, v)
            else:
                res_record = ClassificacaoAgenteIaModel(
                    questao_id=request.questao_id,
                    **record_data
                )
                pg_db.add(res_record)
            
            pg_db.commit()
        except Exception as e:
            pg_db.rollback()
            logger.error(f"Erro ao salvar resultado IA no DB: {e}")

        elapsed = time.time() - start_time
        logger.info(
            f"QID {request.questao_id}: {modulos_preditos} "
            f"({len(justificativas)} justificativas) em {elapsed:.2f}s"
        )
        
        return IAClassificarResponse(
            questao_id=request.questao_id,
            modulos_preditos=modulos_preditos,
            justificativas=justificativas,
            disciplina=disciplina,
            modulos_possiveis=modulos_possiveis,
            categorias_preditas=[],
            confianca_media=1.0,
            modelo_utilizado=modelo_nome,
            usou_llm=True,
            tempo_processamento=elapsed,
            tokens_usados=tokens_used,
            custo_estimado_usd=custo_estimado
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"ERRO 500 no endpoint: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))


# --------------------------------------------------------------------------- #
# Endpoints auxiliares
# --------------------------------------------------------------------------- #
@router.get("/logs")
async def get_ia_logs(lines: int = 50):
    """Retorna as últimas linhas do log do classificador"""
    from datetime import date
    log_file = f"logs/classifier_{date.today().isoformat()}.log"
    if not os.path.exists(log_file):
        return {"logs": ["Arquivo de log não encontrado."]}
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
            return {"logs": all_lines[-lines:]}
    except Exception as e:
        return {"logs": [f"Erro ao ler log: {e}"]}


@router.post("/treinar", response_model=IARetreinarResponse)
async def treinar_modelo(background_tasks: BackgroundTasks):
    """Dispara a pipeline de treinamento em background (gera embeddings)"""
    import subprocess, sys
    def run_train():
        logger.info("Iniciando processo de re-treino via subprocess...")
        try:
            res = subprocess.run(
                [sys.executable, "train_pipeline.py"],
                capture_output=True, text=True, encoding="utf-8"
            )
            if res.returncode == 0:
                logger.success("Re-treino concluído com sucesso!")
            else:
                logger.error(f"Erro no re-treino: {res.stderr}")
        except Exception as e:
            logger.error(f"Falha ao executar pipeline de treino: {e}")

    background_tasks.add_task(run_train)
    return IARetreinarResponse(
        message="Processo de treinamento iniciado em segundo plano.",
        status="processing"
    )


@router.get("/status")
async def get_ia_status(pg_db: Session = Depends(get_pg_db)):
    """Retorna estatísticas rápidas do Agente IA"""
    try:
        total_ia = pg_db.query(func.count(ClassificacaoAgenteIaModel.id)).scalar()
        enriquecidos = pg_db.query(func.count(ClassificacaoAgenteIaModel.id)).filter(
            ClassificacaoAgenteIaModel.enunciado != None
        ).scalar()
        com_justificativa = pg_db.query(func.count(ClassificacaoAgenteIaModel.id)).filter(
            ClassificacaoAgenteIaModel.justificativas != None
        ).scalar()
        
        total_manual = pg_db.query(func.count(distinct(ClassificacaoUsuarioModel.questao_id)))\
            .filter(ClassificacaoUsuarioModel.usuario_id != 0)\
            .scalar()
        
        return {
            "total_ia": total_ia,
            "enriquecidos": enriquecidos,
            "com_justificativa": com_justificativa,
            "total_manual": total_manual,
            "progresso_validacao": (total_ia / total_manual * 100) if total_manual and total_manual > 0 else 0
        }
    except Exception as e:
        logger.error(f"Erro ao obter status IA: {e}")
        return {"error": str(e)}


@router.get("/validar-manual")
async def validar_manual(background_tasks: BackgroundTasks):
    """Gatilho para classificar massivamente as questões classificadas por humanos"""
    
    def run_validation():
        global CANCEL_VALIDATION
        CANCEL_VALIDATION = False
        logger.info("🎯 Iniciando Validação Massiva da IA contra base manual (LLM + Prompts)...")
        pg_session = PgSessionLocal()
        mysql_session = SessionLocal()
        
        try:
            manual_ids = [r[0] for r in pg_session.query(ClassificacaoUsuarioModel.questao_id)\
                .filter(ClassificacaoUsuarioModel.usuario_id != 0)\
                .distinct().all()]
            logger.info(f"Encontradas {len(manual_ids)} questões de professores para validar.")
            
            sucesso = 0
            erros = 0
            for i, qid in enumerate(manual_ids):
                if CANCEL_VALIDATION:
                    logger.warning("Validação em background interrompida pelo usuário.")
                    break
                try:
                    from .ia_classificacao_schemas import IAClassificarRequest
                    req = IAClassificarRequest(questao_id=qid)
                    classificar_questao(req, pg_session, mysql_session)
                    sucesso += 1
                    if (i + 1) % 50 == 0:
                        logger.info(f"Progresso: {i+1}/{len(manual_ids)} ({sucesso} OK, {erros} erros)")
                    time.sleep(0.05)  # Rate limit
                except Exception as ex:
                    pg_session.rollback()
                    erros += 1
                    logger.error(f"Erro QID {qid}: {ex}")
            
            logger.success(f"✅ Validação concluída: {sucesso} OK, {erros} erros de {len(manual_ids)} total")
        except Exception as e:
            logger.error(f"Erro na validação: {e}")
        finally:
            pg_session.close()
            mysql_session.close()

    background_tasks.add_task(run_validation)
    return {"message": "Validação massiva (LLM + Prompts) iniciada em background."}


@router.post("/reload-prompts")
async def reload_prompts():
    """Recarrega os prompts do disco (após edição manual)"""
    global _PROMPTS_CACHE
    _PROMPTS_CACHE = {}
    
    loaded = []
    if os.path.exists(PROMPTS_DIR):
        for f in os.listdir(PROMPTS_DIR):
            if f.endswith(".json") and not f.startswith("_"):
                disc = f.replace(".json", "")
                load_discipline_prompt(disc)
                loaded.append(disc)
    
    return {"message": f"Prompts recarregados: {len(loaded)} disciplinas", "disciplinas": loaded}


# --------------------------------------------------------------------------- #
# Listagem e Detalhes de Classificações
# --------------------------------------------------------------------------- #
@router.get("/classificacoes")
async def list_classificacoes(
    page: int = 1,
    per_page: int = 50,
    modelo_filter: Optional[str] = None,
    disciplina_filter: Optional[str] = None,
    match_filter: Optional[str] = None,
    pg_db: Session = Depends(get_pg_db)
):
    """Lista classificações IA com paginação e filtros (calcula match em Python)"""
    try:
        query = pg_db.query(ClassificacaoAgenteIaModel)
        
        if modelo_filter and modelo_filter != "all":
            if modelo_filter == "gpt-4o":
                query = query.filter(ClassificacaoAgenteIaModel.modelo_utilizado.ilike(f"%gpt-4o\_%"))
            else:
                query = query.filter(ClassificacaoAgenteIaModel.modelo_utilizado.ilike(f"%{modelo_filter}%"))
        if disciplina_filter and disciplina_filter != "all":
            query = query.filter(ClassificacaoAgenteIaModel.disciplina == disciplina_filter)
        
        items = query.order_by(ClassificacaoAgenteIaModel.created_at.desc()).all()
        
        # Buscar todas as classificacoes manuais para fazer o match
        manuals = pg_db.query(ClassificacaoUsuarioModel).filter(ClassificacaoUsuarioModel.usuario_id != 0).all()
        manual_dict = {m.questao_id: m for m in manuals}
        
        filtered_items = []
        for item in items:
            manual = manual_dict.get(item.questao_id)
            manual_modulos = None
            manual_descricoes = None
            if manual:
                manual_modulos = manual.modulos_escolhidos or ([manual.modulo_escolhido] if manual.modulo_escolhido else [])
                manual_descricoes = manual.descricoes_assunto_list or ([manual.descricao_assunto] if manual.descricao_assunto else [])
            
            ia_set = set(item.modulos_preditos or [])
            ia_desc_set = set(item.descricoes_modulos or [])
            manual_set = set(manual_modulos) if manual_modulos else None
            manual_desc_set = set([d for d in manual_descricoes if d]) if manual_descricoes else set()
            
            match_status = "pending"
            if manual_set is not None:
                if ia_set == manual_set and ia_desc_set == manual_desc_set:
                    match_status = "exact"
                elif ia_set & manual_set:
                    match_status = "partial"
                else:
                    match_status = "none"
            
            if match_filter and match_filter != "all" and match_status != match_filter:
                continue
                
            filtered_items.append((item, match_status))
        
        total = len(filtered_items)
        page_items = filtered_items[(page - 1) * per_page : page * per_page]
        
        results = []
        for item, match in page_items:
            results.append({
                "questao_id": item.questao_id,
                "modulos_preditos": item.modulos_preditos,
                "disciplina": item.disciplina,
                "confianca_media": item.confianca_media,
                "modelo_utilizado": item.modelo_utilizado,
                "usou_llm": item.usou_llm,
                "prompt_version": item.prompt_version,
                "tem_justificativa": item.justificativas is not None,
                "match_status": match,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            })
        
        return {
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
            "items": results
        }
    except Exception as e:
        logger.error(f"Erro ao listar classificações: {e}")
        return {"total": 0, "items": [], "error": str(e)}


@router.get("/classificacao/{questao_id}")
async def get_classificacao_detail(
    questao_id: int,
    pg_db: Session = Depends(get_pg_db),
    db: Session = Depends(get_db)
):
    """Retorna detalhes completos de uma classificação IA vs Manual"""
    try:
        ia = pg_db.query(ClassificacaoAgenteIaModel).filter(
            ClassificacaoAgenteIaModel.questao_id == questao_id
        ).first()
        
        if not ia:
            raise HTTPException(status_code=404, detail="Classificação IA não encontrada")
        
        # Buscar classificação manual
        manual = pg_db.query(ClassificacaoUsuarioModel).filter(
            ClassificacaoUsuarioModel.questao_id == questao_id,
            ClassificacaoUsuarioModel.usuario_id != 0
        ).first()
        
        manual_modulos = None
        manual_descricoes = None
        if manual:
            manual_modulos = manual.modulos_escolhidos or ([manual.modulo_escolhido] if manual.modulo_escolhido else [])
            manual_descricoes = manual.descricoes_assunto_list or ([manual.descricao_assunto] if manual.descricao_assunto else [])
        
        ia_set = set(ia.modulos_preditos or [])
        ia_desc_set = set(ia.descricoes_modulos or [])
        manual_set = set(manual_modulos) if manual_modulos else None
        manual_desc_set = set([d for d in manual_descricoes if d]) if manual_descricoes else set()
        
        match_status = None
        if manual_set is not None:
            if ia_set == manual_set and ia_desc_set == manual_desc_set:
                match_status = "exact"
            elif ia_set & manual_set:
                match_status = "partial"
            else:
                match_status = "none"
        
        return {
            "questao_id": questao_id,
            "enunciado": ia.enunciado,
            "disciplina": ia.disciplina,
            "habilidade_trieduc": ia.habilidade_trieduc,
            "ia": {
                "modulos_preditos": ia.modulos_preditos,
                "justificativas": ia.justificativas,
                "modulos_possiveis": ia.modulos_possiveis,
                "descricoes_modulos": ia.descricoes_modulos,
                "confianca_media": ia.confianca_media,
                "modelo_utilizado": ia.modelo_utilizado,
                "prompt_version": ia.prompt_version,
                "usou_llm": ia.usou_llm,
                "created_at": ia.created_at.isoformat() if ia.created_at else None,
            },
            "manual": {
                "modulos": manual_modulos,
                "descricoes": manual_descricoes,
            } if manual else None,
            "comparacao": {
                "match_status": match_status,
                "modulos_extra": list(ia_set - manual_set) if manual_set else None,
                "modulos_faltando": list(manual_set - ia_set) if manual_set else None,
            } if manual_set else None
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao obter detalhe: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --------------------------------------------------------------------------- #
# Validação com Streaming (SSE) — primeiras N questões
# --------------------------------------------------------------------------- #
from starlette.responses import StreamingResponse

@router.get("/validar-stream")
async def validar_stream(limit: int = 1000):
    """Classifica as primeiras N questões manuais com streaming SSE"""
    
    def event_generator():
        global CANCEL_VALIDATION
        CANCEL_VALIDATION = False
        pg_session = PgSessionLocal()
        mysql_session = SessionLocal()
        
        try:
            manual_ids = [r[0] for r in pg_session.query(ClassificacaoUsuarioModel.questao_id)\
                .filter(ClassificacaoUsuarioModel.usuario_id != 0)\
                .distinct().order_by(ClassificacaoUsuarioModel.questao_id)\
                .limit(limit).all()]
            
            total = len(manual_ids)
            yield f"data: {json.dumps({'type': 'start', 'total': total})}\n\n"
            
            sucesso = 0
            erros = 0
            total_tokens = 0
            total_cost = 0.0
            
            for i, qid in enumerate(manual_ids):
                if CANCEL_VALIDATION:
                    yield f"data: {json.dumps({'type': 'fatal_error', 'error': 'Validação interrompida forçadamente pelo usuário.'})}\n\n"
                    break
                try:
                    req = IAClassificarRequest(questao_id=qid)
                    result = classificar_questao(req, pg_session, mysql_session)
                    sucesso += 1
                    total_tokens += result.tokens_usados
                    total_cost += result.custo_estimado_usd
                    
                    # Buscar manual para comparar
                    manual_q = pg_session.query(ClassificacaoUsuarioModel).filter(
                        ClassificacaoUsuarioModel.questao_id == qid,
                        ClassificacaoUsuarioModel.usuario_id != 0
                    ).first()
                    
                    manual_mods = None
                    manual_desc = None
                    if manual_q:
                        manual_mods = manual_q.modulos_escolhidos or ([manual_q.modulo_escolhido] if manual_q.modulo_escolhido else [])
                        manual_desc = manual_q.descricoes_assunto_list or ([manual_q.descricao_assunto] if manual_q.descricao_assunto else [])
                    
                    match = "none"
                    if manual_mods:
                        ia_mods_set = set(result.modulos_preditos or [])
                        m_mods_set = set(manual_mods)
                        
                        if ia_mods_set == m_mods_set:
                            match = "exact"
                        elif ia_mods_set & m_mods_set:
                            match = "partial"
                    
                    event_data = {
                        "type": "progress",
                        "index": i + 1,
                        "total": total,
                        "questao_id": qid,
                        "modulos_preditos": result.modulos_preditos,
                        "manual": manual_mods,
                        "match": match,
                        "disciplina": result.disciplina,
                        "tempo": round(result.tempo_processamento, 2),
                        "tokens": result.tokens_usados,
                        "custo": round(result.custo_estimado_usd, 6),
                        "sucesso_total": sucesso,
                        "erros_total": erros,
                    }
                    yield f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"
                    
                    time.sleep(0.05)
                    
                except Exception as ex:
                    pg_session.rollback()
                    erros += 1
                    yield f"data: {json.dumps({'type': 'error', 'index': i+1, 'questao_id': qid, 'error': str(ex)[:200]})}\n\n"
            
            yield f"data: {json.dumps({'type': 'done', 'sucesso': sucesso, 'erros': erros, 'total_tokens': total_tokens, 'total_cost': round(total_cost, 4)})}\n\n"
            
        except Exception as e:
            yield f"data: {json.dumps({'type': 'fatal_error', 'error': str(e)})}\n\n"
        finally:
            pg_session.close()
            mysql_session.close()
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

