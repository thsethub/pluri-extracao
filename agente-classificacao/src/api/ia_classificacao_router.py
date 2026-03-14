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
import csv
import time
import asyncio
import traceback
import re
import ast
import uuid
import threading
from datetime import datetime
from collections import Counter, deque
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import func, distinct
from sqlalchemy.orm import Session, joinedload
from loguru import logger
from typing import List, Optional, Dict, Any

from ..config import settings
from ..database import get_db, SessionLocal
from ..database.models import QuestaoModel, HabilidadeModel
from ..database.pg_usuario_models import ClassificacaoUsuarioModel
from ..database.pg_ia_models import (
    ClassificacaoAgenteIaModel,
    QuestaoEmbeddingModel,
    ClassificacaoAgenteIaErroModel,
)
from ..database.pg_modulo_models import HabilidadeModuloModel
from .ia_classificacao_schemas import IAClassificarRequest, IAClassificarResponse, IARetreinarResponse
from ..services.openai_client import OpenAIClient

router = APIRouter(prefix="/classificacao-ia", tags=["Classificação IA"])

# --------------------------------------------------------------------------- #
# Cache de prompts e flags de controle
# --------------------------------------------------------------------------- #
_PROMPTS_CACHE: Dict[str, dict] = {}
_HUMAN_PRIOR_CACHE: Dict[int, dict] = {}
PROMPTS_DIR = os.path.join(os.getcwd(), "prompts")
OUTPUT_DIR = os.path.join(os.getcwd(), "data", "output")
CANCEL_VALIDATION = False

# Worker paralelo para validacao em background (continua mesmo sem tela aberta)
MAX_VALIDATION_LOGS = 12000
VALIDATION_STATE_LOCK = threading.Lock()
VALIDATION_QUEUE_LOCK = threading.Lock()
VALIDATION_STOP_EVENT = threading.Event()
VALIDATION_QUEUE: deque[int] = deque()
VALIDATION_WORKER_THREADS: List[threading.Thread] = []
VALIDATION_JOB_STATE: Dict[str, Any] = {
    "job_id": None,
    "status": "idle",  # idle|running|stopping|stopped|completed|error
    "limit": 0,
    "workers_requested": 0,
    "workers_active": 0,
    "total": 0,
    "processed": 0,
    "sucesso": 0,
    "erros": 0,
    "queue_remaining": 0,
    "total_tokens": 0,
    "total_cost": 0.0,
    "last_questao_id": None,
    "last_error": None,
    "started_at": None,
    "finished_at": None,
    "backup_csv": None,
    "ia_lote_csv": None,
    "classificacoes_ia_removidas": 0,
    "logs": [],
}

@router.post("/cancelar-validacao")
def cancelar_validacao():
    """Gatilho para interromper validacao massiva (SSE e workers paralelos)."""
    global CANCEL_VALIDATION
    CANCEL_VALIDATION = True
    VALIDATION_STOP_EVENT.set()
    with VALIDATION_STATE_LOCK:
        if VALIDATION_JOB_STATE.get("status") == "running":
            VALIDATION_JOB_STATE["status"] = "stopping"
    logger.warning("Sinal de CANCELAMENTO recebido. A validacao sera interrompida!")
    _append_validation_log("warning", "Sinal de cancelamento recebido")
    return {"message": "Validacao parada com sucesso."}


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


def extract_description_set(assuntos_sugeridos: Any) -> set[str]:
    """Normaliza descrições em um set para comparação IA vs manual."""
    if not assuntos_sugeridos:
        return set()
    if isinstance(assuntos_sugeridos, dict):
        return {str(v) for v in assuntos_sugeridos.values() if v}
    if isinstance(assuntos_sugeridos, list):
        return {str(v) for v in assuntos_sugeridos if v}
    return set()


def calculate_estimated_cost_usd(input_tokens: int, output_tokens: int) -> float:
    """Calcula custo estimado com tarifas por 1k tokens configuradas por ambiente."""
    in_cost = (input_tokens / 1000.0) * settings.ia_cost_per_1k_input_tokens
    out_cost = (output_tokens / 1000.0) * settings.ia_cost_per_1k_output_tokens
    return in_cost + out_cost


def parse_json_like_list(value: Any) -> List[str]:
    """Normaliza campo JSON/lista legado em lista de strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if v and str(v).strip()]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(raw)
                if isinstance(parsed, list):
                    return [str(v).strip() for v in parsed if v and str(v).strip()]
            except Exception:
                continue
        return [raw]
    return []


def ensure_output_dir() -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return OUTPUT_DIR


def _json_cell(value: Any) -> str:
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False)


def persist_classificacao_ia_error(
    questao_id: Optional[int],
    etapa: str,
    erro: Any,
    payload: Optional[Dict[str, Any]] = None,
    modelo_utilizado: Optional[str] = None,
    stacktrace: Optional[str] = None,
) -> None:
    """Persist errors to PostgreSQL using an isolated session."""
    safe_payload = payload
    if safe_payload is not None:
        try:
            json.dumps(safe_payload, ensure_ascii=False)
        except Exception:
            safe_payload = {"payload_repr": repr(safe_payload)}

    session = SessionLocal()
    try:
        record = ClassificacaoAgenteIaErroModel(
            questao_id=questao_id,
            etapa=(etapa or "unknown")[:80],
            erro=str(erro)[:4000],
            stacktrace=(stacktrace or "")[:30000] if stacktrace else None,
            payload=safe_payload,
            modelo_utilizado=modelo_utilizado,
            prompt_version=settings.ia_prompt_version,
        )
        session.add(record)
        session.commit()
    except Exception as persist_ex:
        session.rollback()
        logger.error(f"Falha ao persistir erro IA: {persist_ex}")
    finally:
        session.close()


def export_classificacoes_ia_csv(file_path: str, rows: List[ClassificacaoAgenteIaModel]) -> int:
    fieldnames = [
        "id",
        "questao_id",
        "disciplina",
        "modelo_utilizado",
        "prompt_version",
        "confianca_media",
        "usou_llm",
        "modulos_sugeridos",
        "assuntos_sugeridos",
        "modulos_possiveis",
        "justificativas",
        "habilidade_trieduc",
        "categorias_preditas",
        "enunciado",
        "created_at",
    ]
    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "id": row.id,
                    "questao_id": row.questao_id,
                    "disciplina": row.disciplina or "",
                    "modelo_utilizado": row.modelo_utilizado or "",
                    "prompt_version": row.prompt_version or "",
                    "confianca_media": row.confianca_media,
                    "usou_llm": row.usou_llm,
                    "modulos_sugeridos": _json_cell(row.modulos_sugeridos),
                    "assuntos_sugeridos": _json_cell(row.assuntos_sugeridos),
                    "modulos_possiveis": _json_cell(row.modulos_possiveis),
                    "justificativas": _json_cell(row.justificativas),
                    "habilidade_trieduc": _json_cell(row.habilidade_trieduc),
                    "categorias_preditas": _json_cell(row.categorias_preditas),
                    "enunciado": row.enunciado or "",
                    "created_at": row.created_at.isoformat() if row.created_at else "",
                }
            )
    return len(rows)


def export_ia_lote_csv(file_path: str, questao_ids: List[int]) -> int:
    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["ordem", "questao_id"])
        writer.writeheader()
        for idx, qid in enumerate(questao_ids, start=1):
            writer.writerow({"ordem": idx, "questao_id": qid})
    return len(questao_ids)


def prepare_lote_files(
    pg_db: Session,
    limit: int,
    reset_classificacoes_ia: bool = True,
) -> Dict[str, Any]:
    """Exporta CSVs (backup + lote) e opcionalmente limpa tabela IA."""
    ensure_output_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = os.path.join(
        OUTPUT_DIR,
        f"classificacoes_agente_ia_backup_{timestamp}.csv",
    )
    lote_file = os.path.join(
        OUTPUT_DIR,
        f"ia_lote_{limit}_{timestamp}.csv",
    )

    ia_rows = (
        pg_db.query(ClassificacaoAgenteIaModel)
        .order_by(ClassificacaoAgenteIaModel.questao_id)
        .all()
    )
    backup_count = export_classificacoes_ia_csv(backup_file, ia_rows)

    manual_ids = [
        r[0]
        for r in pg_db.query(ClassificacaoUsuarioModel.questao_id)
        .filter(ClassificacaoUsuarioModel.usuario_id != 0)
        .distinct()
        .order_by(ClassificacaoUsuarioModel.questao_id)
        .limit(limit)
        .all()
    ]
    lote_count = export_ia_lote_csv(lote_file, manual_ids)

    deleted_count = 0
    if reset_classificacoes_ia:
        deleted_count = (
            pg_db.query(ClassificacaoAgenteIaModel)
            .delete(synchronize_session=False)
        )
        pg_db.commit()

    return {
        "backup_csv": backup_file,
        "backup_rows": backup_count,
        "ia_lote_csv": lote_file,
        "ia_lote_rows": lote_count,
        "reset_aplicado": reset_classificacoes_ia,
        "classificacoes_ia_removidas": deleted_count,
        "manual_ids": manual_ids,
    }


def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _append_validation_log(level: str, message: str) -> None:
    entry = {"time": _utc_now_iso(), "level": level, "message": message}
    with VALIDATION_STATE_LOCK:
        logs = VALIDATION_JOB_STATE.get("logs", [])
        logs.append(entry)
        if len(logs) > MAX_VALIDATION_LOGS:
            logs = logs[-MAX_VALIDATION_LOGS:]
        VALIDATION_JOB_STATE["logs"] = logs


def _get_validation_state_snapshot() -> Dict[str, Any]:
    with VALIDATION_STATE_LOCK:
        snapshot = dict(VALIDATION_JOB_STATE)
        snapshot["logs"] = list(VALIDATION_JOB_STATE.get("logs", []))
        return snapshot


def _validation_worker_loop(job_id: str, worker_idx: int) -> None:
    """Loop de um worker: consome fila unica, sem repetir questoes."""
    pg_session = SessionLocal()
    mysql_session = SessionLocal()
    _append_validation_log("info", f"Worker-{worker_idx} iniciado")

    try:
        while True:
            if VALIDATION_STOP_EVENT.is_set():
                break

            qid = None
            remaining = 0
            with VALIDATION_QUEUE_LOCK:
                if VALIDATION_QUEUE:
                    qid = VALIDATION_QUEUE.popleft()
                    remaining = len(VALIDATION_QUEUE)

            if qid is None:
                break

            with VALIDATION_STATE_LOCK:
                if VALIDATION_JOB_STATE.get("job_id") != job_id:
                    return
                VALIDATION_JOB_STATE["queue_remaining"] = remaining
                VALIDATION_JOB_STATE["last_questao_id"] = qid

            try:
                req = IAClassificarRequest(questao_id=qid, force_fallback_on_empty=True)
                result = classificar_questao(req, pg_session, mysql_session)
                processed_now = 0
                total_now = 0
                with VALIDATION_STATE_LOCK:
                    if VALIDATION_JOB_STATE.get("job_id") != job_id:
                        return
                    VALIDATION_JOB_STATE["processed"] += 1
                    VALIDATION_JOB_STATE["sucesso"] += 1
                    VALIDATION_JOB_STATE["total_tokens"] += int(result.tokens_usados or 0)
                    VALIDATION_JOB_STATE["total_cost"] = round(
                        float(VALIDATION_JOB_STATE.get("total_cost", 0.0)) + float(result.custo_estimado_usd or 0.0),
                        6,
                    )
                    processed_now = int(VALIDATION_JOB_STATE.get("processed", 0))
                    total_now = int(VALIDATION_JOB_STATE.get("total", 0))
                _append_validation_log(
                    "info",
                    (
                        f"[{processed_now}/{total_now}] QID {qid} OK | "
                        f"modulos={result.modulos_sugeridos} | "
                        f"tokens={result.tokens_usados} | "
                        f"custo=${(result.custo_estimado_usd or 0.0):.6f}"
                    ),
                )
            except Exception as ex:
                pg_session.rollback()
                processed_now = 0
                total_now = 0
                with VALIDATION_STATE_LOCK:
                    if VALIDATION_JOB_STATE.get("job_id") != job_id:
                        return
                    VALIDATION_JOB_STATE["processed"] += 1
                    VALIDATION_JOB_STATE["erros"] += 1
                    VALIDATION_JOB_STATE["last_error"] = str(ex)[:500]
                    processed_now = int(VALIDATION_JOB_STATE.get("processed", 0))
                    total_now = int(VALIDATION_JOB_STATE.get("total", 0))
                persist_classificacao_ia_error(
                    questao_id=qid,
                    etapa="validar_workers_loop",
                    erro=ex,
                    payload={"worker": worker_idx},
                    stacktrace=traceback.format_exc(),
                )
                _append_validation_log(
                    "error",
                    f"[{processed_now}/{total_now}] Worker-{worker_idx} erro na QID {qid}: {str(ex)[:180]}",
                )

            time.sleep(0.02)
    finally:
        pg_session.close()
        mysql_session.close()
        with VALIDATION_STATE_LOCK:
            if VALIDATION_JOB_STATE.get("job_id") == job_id:
                current = int(VALIDATION_JOB_STATE.get("workers_active", 0))
                VALIDATION_JOB_STATE["workers_active"] = max(0, current - 1)
        _append_validation_log("info", f"Worker-{worker_idx} finalizado")


def _validation_monitor_loop(job_id: str, workers: List[threading.Thread]) -> None:
    for t in workers:
        t.join()

    with VALIDATION_STATE_LOCK:
        if VALIDATION_JOB_STATE.get("job_id") != job_id:
            return

        if VALIDATION_JOB_STATE.get("status") in {"running", "stopping"}:
            VALIDATION_JOB_STATE["status"] = "stopped" if VALIDATION_STOP_EVENT.is_set() else "completed"

        VALIDATION_JOB_STATE["workers_active"] = 0
        VALIDATION_JOB_STATE["queue_remaining"] = 0
        VALIDATION_JOB_STATE["finished_at"] = _utc_now_iso()

    snapshot = _get_validation_state_snapshot()
    _append_validation_log(
        "success",
        (
            f"Job {job_id} finalizado com status={snapshot.get('status')} | "
            f"sucesso={snapshot.get('sucesso')} erros={snapshot.get('erros')} "
            f"processado={snapshot.get('processed')}/{snapshot.get('total')}"
        ),
    )


def canonicalize_module_name(module_name: str, modulos_validos: List[str]) -> Optional[str]:
    """Resolve nome de módulo com normalização leve (case/acentos/pontuação)."""
    if not module_name:
        return None

    normalized = module_name.strip().lower()
    for mv in modulos_validos:
        if mv.strip().lower() == normalized:
            return mv

    target_slug = slugify(module_name)
    for mv in modulos_validos:
        if slugify(mv) == target_slug:
            return mv

    # Fuzzy leve para variações de singular/plural e pequenos sufixos.
    for mv in modulos_validos:
        mv_slug = slugify(mv)
        if target_slug and (target_slug in mv_slug or mv_slug in target_slug):
            return mv

    return None


def dedupe_preserve_order(items: List[str]) -> List[str]:
    out = []
    seen = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def build_module_subject_candidates(
    modulos_habilidade: List[HabilidadeModuloModel],
) -> Dict[str, List[str]]:
    """
    Gera mapa modulo -> lista de assuntos (descricoes) validos para a habilidade.
    Preserva ordem e remove duplicatas.
    """
    subjects_by_module: Dict[str, List[str]] = {}
    seen_pairs = set()

    for row in modulos_habilidade:
        modulo = (row.modulo or "").strip()
        descricao = (row.descricao or "").strip()
        if not modulo:
            continue

        if modulo not in subjects_by_module:
            subjects_by_module[modulo] = []

        if descricao:
            key = (modulo, descricao)
            if key not in seen_pairs:
                seen_pairs.add(key)
                subjects_by_module[modulo].append(descricao)

    return subjects_by_module


def canonicalize_subject_description(
    descricao: str,
    valid_descriptions: List[str],
) -> Optional[str]:
    """Resolve descricao de assunto para um valor valido da lista do modulo."""
    if not descricao:
        return None

    raw = descricao.strip()
    if not raw:
        return None

    normalized = raw.lower()
    for d in valid_descriptions:
        if d.strip().lower() == normalized:
            return d

    target_slug = slugify(raw)
    for d in valid_descriptions:
        if slugify(d) == target_slug:
            return d

    for d in valid_descriptions:
        dslug = slugify(d)
        if target_slug and (target_slug in dslug or dslug in target_slug):
            return d

    return None


def build_subject_options_text(
    modulos_validos: List[str],
    subjects_by_module: Dict[str, List[str]],
) -> str:
    """Monta texto de opcoes de assunto por modulo para o prompt."""
    chunks: List[str] = []
    for modulo in modulos_validos:
        descricoes = subjects_by_module.get(modulo, [])
        if descricoes:
            linhas = "\n".join(f"    - {d}" for d in descricoes)
            chunks.append(f"- **{modulo}**:\n{linhas}")
        else:
            chunks.append(f"- **{modulo}**:\n    - (sem descricao cadastrada)")
    return "\n\n".join(chunks)


def build_question_context_text(q: QuestaoModel, texto_override: Optional[str]) -> str:
    """Monta contexto completo para classificação: texto_base + enunciado + alternativas."""
    parts: List[str] = []

    if q.texto_base:
        parts.append(f"TEXTO BASE:\n{q.texto_base}")

    if texto_override:
        parts.append(f"ENUNCIADO (REQUEST):\n{texto_override}")
        if q.enunciado and q.enunciado.strip() != texto_override.strip():
            parts.append(f"ENUNCIADO (DB):\n{q.enunciado}")
    elif q.enunciado:
        parts.append(f"ENUNCIADO:\n{q.enunciado}")

    if q.tipo:
        parts.append(f"TIPO DA QUESTAO: {q.tipo}")

    if q.alternativas:
        alternativas_ordenadas = sorted(
            q.alternativas,
            key=lambda a: (a.ordem if a.ordem is not None else 9999, a.id or 0),
        )
        alt_lines = []
        for idx, alt in enumerate(alternativas_ordenadas, start=1):
            conteudo = (alt.conteudo or "").strip()
            if not conteudo:
                continue
            label = alt.ordem if alt.ordem is not None else idx
            alt_lines.append(f"{label}) {conteudo}")
        if alt_lines:
            parts.append("ALTERNATIVAS:\n" + "\n".join(alt_lines))

    return "\n\n".join(parts).strip()


def extract_image_urls_from_text(raw_text: Optional[str]) -> List[str]:
    """Extrai imagens base64 e URLs http(s) de tags <img src=...>."""
    if not raw_text:
        return []

    urls = []
    pattern = r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>'
    for match in re.finditer(pattern, raw_text, flags=re.IGNORECASE):
        src = match.group(1).strip()
        if src.startswith("data:image/") or src.startswith("http://") or src.startswith("https://"):
            urls.append(src)
    return dedupe_preserve_order(urls)


def collect_question_image_urls(q: QuestaoModel, texto_contexto: str) -> List[str]:
    """Coleta imagens do enunciado, texto base, alternativas e contexto montado."""
    fragments = [texto_contexto, q.enunciado or "", q.texto_base or ""]
    if q.alternativas:
        fragments.extend((alt.conteudo or "") for alt in q.alternativas)

    all_urls: List[str] = []
    for fragment in fragments:
        all_urls.extend(extract_image_urls_from_text(fragment))
    return dedupe_preserve_order(all_urls)


def get_human_module_priors(
    pg_db: Session, habilidade_id: Optional[int]
) -> Dict[str, Any]:
    """
    Retorna distribuição dos módulos escolhidos manualmente para a habilidade.
    Estrutura:
      {
        "total_samples": int,
        "items": [{"modulo": str, "count": int, "share": float}]
      }
    """
    if not settings.ia_use_human_priors or not habilidade_id:
        return {"total_samples": 0, "items": []}

    if habilidade_id in _HUMAN_PRIOR_CACHE:
        return _HUMAN_PRIOR_CACHE[habilidade_id]

    rows = (
        pg_db.query(
            ClassificacaoUsuarioModel.modulo_escolhido,
            ClassificacaoUsuarioModel.modulos_escolhidos,
        )
        .filter(ClassificacaoUsuarioModel.usuario_id != 0)
        .filter(ClassificacaoUsuarioModel.habilidade_id == habilidade_id)
        .all()
    )

    counter: Counter[str] = Counter()
    total_samples = 0

    for modulo_single, modulos_multi in rows:
        modules = parse_json_like_list(modulos_multi)
        if not modules and modulo_single:
            modules = [str(modulo_single).strip()]

        modules = dedupe_preserve_order([m for m in modules if m])
        if not modules:
            continue

        total_samples += 1
        for module_name in modules:
            counter[module_name] += 1

    top_k = max(1, settings.ia_human_prior_top_k)
    items = []
    for module_name, count in counter.most_common(top_k):
        share = (count / total_samples) if total_samples else 0.0
        items.append(
            {
                "modulo": module_name,
                "count": count,
                "share": round(share, 4),
            }
        )

    result = {"total_samples": total_samples, "items": items}
    _HUMAN_PRIOR_CACHE[habilidade_id] = result
    return result


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
{texto_questao[:settings.ia_max_question_chars]}

Responda APENAS com JSON no formato:
{{
  "modulos": [
    {{"nome": "Nome Exato do Módulo", "justificativa": "Por que este módulo se aplica..."}}
  ]
}}"""

    return system_prompt, user_prompt


def build_classification_prompt_v2(
    prompt_data: dict,
    modulos_validos: List[str],
    subjects_by_module: Dict[str, List[str]],
    texto_questao: str,
    habilidade_info: str,
    human_priors: Optional[Dict[str, Any]] = None,
    has_images: bool = False,
) -> tuple[str, str]:
    """
    Versao otimizada do prompt:
    - privilegia single-label
    - usa prior humano por habilidade (quando houver amostra suficiente)
    """
    modulos_com_criterios = []
    modulos_dict = {m["nome"]: m for m in prompt_data.get("modulos", [])}

    for mod_nome in modulos_validos:
        if mod_nome in modulos_dict:
            m = modulos_dict[mod_nome]
            modulos_com_criterios.append(
                f'- **{m["nome"]}**\n'
                f'  Escopo: {m["escopo"]}\n'
                f'  Incluir quando: {m["incluir_quando"]}\n'
                f'  Nao incluir quando: {m["nao_incluir_quando"]}\n'
                f'  Diferenciador: {m["diferenciador"]}'
            )
        else:
            modulos_com_criterios.append(
                f'- **{mod_nome}** (sem criterios detalhados disponiveis)'
            )

    modulos_text = "\n\n".join(modulos_com_criterios)
    assuntos_text = build_subject_options_text(modulos_validos, subjects_by_module)
    max_modules = max(1, settings.ia_max_output_modules)

    prior_text = "Sem historico manual suficiente para esta habilidade."
    if human_priors and human_priors.get("total_samples", 0) >= settings.ia_human_prior_min_samples:
        prior_lines = [
            f"- {item['modulo']} (freq={item['count']}, share={item['share']})"
            for item in human_priors.get("items", [])
        ]
        if prior_lines:
            prior_text = (
                f"Base manual disponivel ({human_priors['total_samples']} amostras):\n"
                + "\n".join(prior_lines)
                + "\nUse como prior fraco: so desvie quando o enunciado indicar claramente outro modulo."
            )

    image_rule = (
        "9. Se houver imagens, descreva de forma objetiva o que foi usado da imagem no campo `analise_imagem`."
        if has_images
        else "9. Se nao houver imagem relevante, retorne `analise_imagem` vazio."
    )

    system_prompt = f"""Voce e um especialista em classificacao de questoes educacionais da disciplina {prompt_data['disciplina']}.

INSTRUCAO GERAL DA DISCIPLINA:
{prompt_data.get('instrucao_geral', 'Classifique de acordo com o conteudo principal da questao.')}

REGRAS PARA MULTIPLOS MODULOS:
{prompt_data.get('regras_multi_modulo', 'Atribua multiplos modulos somente quando genuinamente necessario.')}

REGRAS OBRIGATORIAS:
1. Voce SO pode escolher modulos da lista abaixo. NUNCA invente modulos.
2. Para CADA modulo escolhido, selecione TAMBEM a descricao de assunto EXATA da lista permitida para esse modulo.
3. Para CADA modulo escolhido, forneca uma justificativa de 1-2 frases.
4. As justificativas de modulos diferentes NAO DEVEM se interseccionar em escopo.
5. Se dois modulos cobrem o mesmo aspecto, escolha o MAIS ESPECIFICO.
6. Priorize classificacao SINGLE-LABEL (1 modulo) sempre que possivel.
7. Use multiplos modulos somente quando a questao exigir, de forma inequivoca, mais de um foco independente.
8. Escolha no MINIMO 1 modulo e no MAXIMO {max_modules} modulos.
9. Responda APENAS com JSON valido no formato especificado.
{image_rule}"""

    user_prompt = f"""HABILIDADE TRIEDUC DA QUESTAO:
{habilidade_info}

HISTORICO HUMANO (apoio):
{prior_text}

MODULOS POSSIVEIS (com criterios de classificacao):

{modulos_text}

ASSUNTOS POSSIVEIS POR MODULO (escolha a descricao EXATA):
{assuntos_text}

CONTEXTO COMPLETO DA QUESTAO (texto base, enunciado e alternativas):
{texto_questao[:settings.ia_max_question_chars]}

Responda APENAS com JSON no formato:
{{
  "modulos": [
    {{
      "nome": "Nome Exato do Modulo",
      "descricao": "Descricao Exata do Assunto para esse Modulo",
      "justificativa": "Por que este modulo+assunto se aplica..."
    }}
  ],
  "analise_imagem": "Texto curto descrevendo o que foi lido da imagem (se houver)"
}}"""

    return system_prompt, user_prompt


# --------------------------------------------------------------------------- #
# Endpoint principal de classificação
# --------------------------------------------------------------------------- #
@router.post("/classificar", response_model=IAClassificarResponse)
def classificar_questao(
    request: IAClassificarRequest,
    pg_db: Session = Depends(get_db),
    db: Session = Depends(get_db)
):
    try:
        start_time = time.time()
        
        # 1. Obter Questão no MySQL
        q = db.query(QuestaoModel).options(
            joinedload(QuestaoModel.habilidade),
            joinedload(QuestaoModel.alternativas),
        ).filter(QuestaoModel.id == request.questao_id).first()
        
        if not q:
            raise HTTPException(status_code=404, detail="Questão não encontrada no MySQL")
        
        # 2. Preparar contexto completo da questão
        texto_final = build_question_context_text(q, request.texto)
        
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
        subjects_by_module = build_module_subject_candidates(modulos_habilidade)
        modulos_validos = list(subjects_by_module.keys())
        modulos_possiveis = modulos_validos.copy()
        human_priors = get_human_module_priors(pg_db, habilidade_id)
        if human_priors.get("items"):
            logger.info(
                f"QID {request.questao_id}: Priors humanos "
                f"(n={human_priors.get('total_samples', 0)}) "
                f"{human_priors['items']}"
            )
        
        logger.info(
            f"QID {request.questao_id}: Disciplina={disciplina}, "
            f"Módulos válidos={len(modulos_validos)}, "
            f"Assuntos={sum(len(v) for v in subjects_by_module.values())}, "
            f"Hab={habilidade_info[:50]}"
        )
        
        # 6. Carregar prompt da disciplina
        prompt_data = load_discipline_prompt(disciplina)
        if not prompt_data:
            raise HTTPException(
                status_code=500,
                detail=f"Prompt não encontrado para disciplina '{disciplina}'"
            )
        
        # Buscar imagens (base64 e URLs) em enunciado, texto base e alternativas
        image_urls = collect_question_image_urls(q, texto_final)
        max_images = 8
        if len(image_urls) > max_images:
            logger.info(
                f"QID {request.questao_id}: {len(image_urls)} imagens encontradas; "
                f"enviando apenas {max_images}"
            )
            image_urls = image_urls[:max_images]
        elif image_urls:
            logger.info(f"QID {request.questao_id}: imagens enviadas ao modelo = {len(image_urls)}")

        # 7. Classificar via LLM
        system_prompt, user_prompt = build_classification_prompt_v2(
            prompt_data,
            modulos_validos,
            subjects_by_module,
            texto_final,
            habilidade_info,
            human_priors,
            bool(image_urls),
        )

        # Montar payload multimodal
        user_content = [{"type": "text", "text": user_prompt}]
        for img_data in image_urls:
            user_content.append({"type": "image_url", "image_url": {"url": img_data}})

        client = OpenAIClient()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content if image_urls else user_prompt}
        ]
        
        response = client.create_completion(messages, model=settings.ia_classification_model)
        modelo_resposta = response.get("model", settings.ia_classification_model)
        tokens_used = response.get("tokens_used", 0)
        input_tokens = response.get("input_tokens", 0)
        output_tokens = response.get("output_tokens", 0)
        llm_time_ms = response.get("processing_time_ms", 0)
        custo_estimado = calculate_estimated_cost_usd(input_tokens, output_tokens)
        
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
        
        # 9. Validar módulos e assuntos contra lista permitida
        modulos_sugeridos = []
        descricoes_preditas: Dict[str, str] = {}
        justificativas = {}
        analise_imagem = data.get("analise_imagem", "")
        if not isinstance(analise_imagem, str):
            analise_imagem = str(analise_imagem) if analise_imagem is not None else ""
        analise_imagem = analise_imagem.strip()
        
        for mod in data.get("modulos", []):
            nome = mod.get("nome", "")
            descricao = mod.get("descricao", "")
            justificativa = mod.get("justificativa", "")
            
            canonical_name = canonicalize_module_name(nome, modulos_validos)
            if canonical_name:
                modulos_sugeridos.append(canonical_name)
                justificativas[canonical_name] = justificativa

                valid_subjects = subjects_by_module.get(canonical_name, [])
                canonical_subject = canonicalize_subject_description(
                    str(descricao) if descricao is not None else "",
                    valid_subjects,
                )
                if canonical_subject:
                    descricoes_preditas[canonical_name] = canonical_subject
                elif len(valid_subjects) == 1:
                    # Se houver assunto único para o módulo, faz autofill.
                    descricoes_preditas[canonical_name] = valid_subjects[0]
                elif valid_subjects:
                    logger.warning(
                        f"QID {request.questao_id}: assunto inválido/ausente para módulo "
                        f"'{canonical_name}' (retornado='{descricao}')"
                    )
            else:
                logger.warning(f"QID {request.questao_id}: Módulo inválido ignorado: '{nome}'")

        modulos_sugeridos = dedupe_preserve_order(modulos_sugeridos)
        max_output_modules = max(1, settings.ia_max_output_modules)
        if len(modulos_sugeridos) > max_output_modules:
            dropped = modulos_sugeridos[max_output_modules:]
            modulos_sugeridos = modulos_sugeridos[:max_output_modules]
            justificativas = {m: justificativas.get(m, "") for m in modulos_sugeridos}
            descricoes_preditas = {m: descricoes_preditas[m] for m in modulos_sugeridos if m in descricoes_preditas}
            logger.info(
                f"QID {request.questao_id}: truncado para {max_output_modules} módulo(s); "
                f"descartados={dropped}"
            )
        
        if not modulos_sugeridos:
            logger.error(f"QID {request.questao_id}: Nenhum módulo válido retornado pelo LLM")
            if settings.ia_enable_fallback_first_module or request.force_fallback_on_empty:
                fallback_module = None
                for item in human_priors.get("items", []):
                    candidate = canonicalize_module_name(item.get("modulo", ""), modulos_validos)
                    if candidate:
                        fallback_module = candidate
                        break

                if fallback_module is None:
                    fallback_module = modulos_validos[0]

                modulos_sugeridos = [fallback_module]
                justificativas = {fallback_module: "Classificação padrão (fallback automático)"}
                fallback_subjects = subjects_by_module.get(fallback_module, [])
                if len(fallback_subjects) == 1:
                    descricoes_preditas = {fallback_module: fallback_subjects[0]}
                else:
                    descricoes_preditas = {}
                logger.warning(
                    f"QID {request.questao_id}: fallback automático ativado "
                    f"({fallback_module})"
                )
            else:
                raise HTTPException(
                    status_code=422,
                    detail="LLM não retornou nenhum módulo válido para esta questão"
                )

        if analise_imagem and image_urls:
            justificativas["__analise_imagem__"] = analise_imagem
        
        # 10. Persistir resultado
        prompt_slug = slugify(disciplina)
        modelo_nome = f"{modelo_resposta}_prompt_{prompt_slug}"
        
        try:
            existing = pg_db.query(ClassificacaoAgenteIaModel).filter(
                ClassificacaoAgenteIaModel.questao_id == request.questao_id
            ).first()
            
            record_data = {
                "enunciado": (q.enunciado or "")[:1000],
                "modulos_sugeridos": modulos_sugeridos,
                "justificativas": justificativas,
                "modulos_possiveis": modulos_possiveis,
                "assuntos_sugeridos": descricoes_preditas,
                "habilidade_trieduc": habilidade_trieduc_data,
                "disciplina": disciplina,
                "categorias_preditas": [],
                "confianca_media": 1.0,
                "modelo_utilizado": modelo_nome,
                "prompt_version": settings.ia_prompt_version,
                "usou_llm": True,
            }
            
            # Métricas de custo/tempo para logging
            logger.info(
                f"QID {request.questao_id} METRICS: "
                f"model={modelo_resposta} input_tokens={input_tokens} "
                f"output_tokens={output_tokens} total_tokens={tokens_used} "
                f"custo=${custo_estimado:.6f} "
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
            stack = traceback.format_exc()
            logger.error(f"Erro ao salvar resultado IA no DB: {e}\n{stack}")
            persist_classificacao_ia_error(
                questao_id=request.questao_id,
                etapa="classificar_persistencia",
                erro=e,
                payload={
                    "disciplina": disciplina,
                    "modelo": modelo_nome,
                    "modulos_sugeridos": modulos_sugeridos,
                },
                modelo_utilizado=modelo_nome,
                stacktrace=stack,
            )
            raise HTTPException(status_code=500, detail="Erro ao salvar resultado IA")

        elapsed = time.time() - start_time
        logger.info(
            f"QID {request.questao_id}: {modulos_sugeridos} "
            f"assuntos={list(descricoes_preditas.values())} "
            f"({len(justificativas)} justificativas) em {elapsed:.2f}s"
        )
        
        return IAClassificarResponse(
            questao_id=request.questao_id,
            modulos_sugeridos=modulos_sugeridos,
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
    
    except HTTPException as e:
        persist_classificacao_ia_error(
            questao_id=request.questao_id,
            etapa="classificar_http_exception",
            erro=getattr(e, "detail", str(e)),
            payload={"status_code": e.status_code},
            stacktrace=traceback.format_exc(),
        )
        raise
    except Exception as e:
        stack = traceback.format_exc()
        logger.error(f"ERRO 500 no endpoint: {e}\n{stack}")
        persist_classificacao_ia_error(
            questao_id=request.questao_id,
            etapa="classificar_exception",
            erro=e,
            stacktrace=stack,
        )
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
async def get_ia_status(pg_db: Session = Depends(get_db)):
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


@router.post("/preparar-lote")
async def preparar_lote(
    limit: int = 5000,
    reset_classificacoes_ia: bool = True,
    pg_db: Session = Depends(get_db),
):
    """
    Prepara nova rodada de classificação:
    1) Exporta CSV de backup da tabela classificacoes_agente_ia
    2) Exporta CSV do ia_lote (questoes manuais alvo)
    3) Opcionalmente limpa classificacoes_agente_ia
    """
    if limit <= 0:
        raise HTTPException(status_code=400, detail="Parametro 'limit' deve ser maior que zero.")

    try:
        result = prepare_lote_files(
            pg_db=pg_db,
            limit=limit,
            reset_classificacoes_ia=reset_classificacoes_ia,
        )

        return {
            "message": "Lote preparado com sucesso.",
            "backup_csv": result["backup_csv"],
            "backup_rows": result["backup_rows"],
            "ia_lote_csv": result["ia_lote_csv"],
            "ia_lote_rows": result["ia_lote_rows"],
            "reset_aplicado": result["reset_aplicado"],
            "classificacoes_ia_removidas": result["classificacoes_ia_removidas"],
        }
    except Exception as e:
        pg_db.rollback()
        stack = traceback.format_exc()
        logger.error(f"Erro ao preparar lote IA: {e}\n{stack}")
        persist_classificacao_ia_error(
            questao_id=None,
            etapa="preparar_lote",
            erro=e,
            payload={"limit": limit, "reset_classificacoes_ia": reset_classificacoes_ia},
            stacktrace=stack,
        )
        raise HTTPException(status_code=500, detail=f"Erro ao preparar lote IA: {str(e)}")


@router.post("/validar-workers/start")
async def validar_workers_start(
    limit: int = 5000,
    workers: int = 2,
    prepare_lote_before_run: bool = True,
    reset_before_run: bool = True,
    pg_db: Session = Depends(get_db),
):
    """
    Inicia classificação em paralelo com N workers.
    Cada questao entra numa fila unica: nenhum worker pega a mesma questao.
    """
    global CANCEL_VALIDATION, VALIDATION_WORKER_THREADS

    if limit <= 0:
        raise HTTPException(status_code=400, detail="Parametro 'limit' deve ser maior que zero.")
    if workers <= 0 or workers > 16:
        raise HTTPException(status_code=400, detail="Parametro 'workers' deve estar entre 1 e 16.")

    with VALIDATION_STATE_LOCK:
        status = VALIDATION_JOB_STATE.get("status")
        if status in {"running", "stopping"}:
            raise HTTPException(
                status_code=409,
                detail="Ja existe um job de validacao em execucao.",
            )

    prepared = None
    manual_ids: List[int] = []
    if prepare_lote_before_run:
        prepared = prepare_lote_files(
            pg_db=pg_db,
            limit=limit,
            reset_classificacoes_ia=reset_before_run,
        )
        manual_ids = list(prepared.get("manual_ids", []))
    else:
        manual_ids = [
            r[0]
            for r in pg_db.query(ClassificacaoUsuarioModel.questao_id)
            .filter(ClassificacaoUsuarioModel.usuario_id != 0)
            .distinct()
            .order_by(ClassificacaoUsuarioModel.questao_id)
            .limit(limit)
            .all()
        ]

    if not manual_ids:
        raise HTTPException(status_code=404, detail="Nenhuma questao manual encontrada para validar.")

    CANCEL_VALIDATION = False
    VALIDATION_STOP_EVENT.clear()
    job_id = str(uuid.uuid4())

    with VALIDATION_QUEUE_LOCK:
        VALIDATION_QUEUE.clear()
        VALIDATION_QUEUE.extend(manual_ids)

    with VALIDATION_STATE_LOCK:
        VALIDATION_JOB_STATE.update(
            {
                "job_id": job_id,
                "status": "running",
                "limit": limit,
                "workers_requested": workers,
                "workers_active": workers,
                "total": len(manual_ids),
                "processed": 0,
                "sucesso": 0,
                "erros": 0,
                "queue_remaining": len(manual_ids),
                "total_tokens": 0,
                "total_cost": 0.0,
                "last_questao_id": None,
                "last_error": None,
                "started_at": _utc_now_iso(),
                "finished_at": None,
                "logs": [],
                "backup_csv": prepared.get("backup_csv") if prepared else None,
                "ia_lote_csv": prepared.get("ia_lote_csv") if prepared else None,
                "classificacoes_ia_removidas": (
                    prepared.get("classificacoes_ia_removidas") if prepared else 0
                ),
            }
        )

    _append_validation_log(
        "info",
        f"Job {job_id} iniciado: total={len(manual_ids)} workers={workers}",
    )
    if prepared:
        _append_validation_log(
            "info",
            (
                f"Lote preparado: backup={prepared.get('backup_csv')} | "
                f"lote={prepared.get('ia_lote_csv')} | "
                f"removidas={prepared.get('classificacoes_ia_removidas')}"
            ),
        )

    worker_threads: List[threading.Thread] = []
    for idx in range(workers):
        t = threading.Thread(
            target=_validation_worker_loop,
            args=(job_id, idx + 1),
            daemon=True,
            name=f"ia-validation-worker-{idx+1}",
        )
        worker_threads.append(t)
        t.start()

    VALIDATION_WORKER_THREADS = worker_threads

    monitor = threading.Thread(
        target=_validation_monitor_loop,
        args=(job_id, worker_threads),
        daemon=True,
        name="ia-validation-monitor",
    )
    monitor.start()

    return {
        "message": "Validacao paralela iniciada.",
        "prepare_lote_before_run": prepare_lote_before_run,
        "reset_before_run": reset_before_run,
        "backup_csv": prepared.get("backup_csv") if prepared else None,
        "ia_lote_csv": prepared.get("ia_lote_csv") if prepared else None,
        "job": _get_validation_state_snapshot(),
    }


@router.post("/validar-workers/stop")
async def validar_workers_stop():
    """Solicita parada graciosa do job paralelo atual."""
    global CANCEL_VALIDATION
    CANCEL_VALIDATION = True
    VALIDATION_STOP_EVENT.set()
    with VALIDATION_STATE_LOCK:
        if VALIDATION_JOB_STATE.get("status") == "running":
            VALIDATION_JOB_STATE["status"] = "stopping"
    _append_validation_log("warning", "Parada solicitada pelo usuario")
    return {"message": "Sinal de parada enviado.", "job": _get_validation_state_snapshot()}


@router.get("/validar-workers/status")
async def validar_workers_status():
    """Retorna estado atual do job paralelo de validação."""
    return _get_validation_state_snapshot()


@router.get("/validar-manual")
async def validar_manual(background_tasks: BackgroundTasks):
    """Gatilho para classificar massivamente as questões classificadas por humanos"""
    
    def run_validation():
        global CANCEL_VALIDATION
        CANCEL_VALIDATION = False
        logger.info("🎯 Iniciando Validação Massiva da IA contra base manual (LLM + Prompts)...")
        pg_session = SessionLocal()
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
                    persist_classificacao_ia_error(
                        questao_id=qid,
                        etapa="validar_manual_loop",
                        erro=ex,
                        payload={"index": i + 1, "total": len(manual_ids)},
                        stacktrace=traceback.format_exc(),
                    )
            
            logger.success(f"✅ Validação concluída: {sucesso} OK, {erros} erros de {len(manual_ids)} total")
        except Exception as e:
            logger.error(f"Erro na validação: {e}")
            persist_classificacao_ia_error(
                questao_id=None,
                etapa="validar_manual_fatal",
                erro=e,
                stacktrace=traceback.format_exc(),
            )
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
# Helpers de listagem
# --------------------------------------------------------------------------- #
def _compute_match_status(item: ClassificacaoAgenteIaModel, manual: Optional[ClassificacaoUsuarioModel]) -> str:
    if manual is None:
        return "pending"
    manual_modulos = manual.modulos_escolhidos or ([manual.modulo_escolhido] if manual.modulo_escolhido else [])
    manual_descricoes = manual.descricoes_assunto_list or ([manual.descricao_assunto] if manual.descricao_assunto else [])
    ia_set = set(item.modulos_sugeridos or [])
    ia_desc_set = extract_description_set(item.assuntos_sugeridos)
    manual_set = set(manual_modulos) if manual_modulos else None
    manual_desc_set = set([d for d in manual_descricoes if d]) if manual_descricoes else set()
    if manual_set is None:
        return "pending"
    if ia_set == manual_set and ia_desc_set == manual_desc_set:
        return "exact"
    if ia_set & manual_set:
        return "partial"
    return "none"


def _build_list_item(item: ClassificacaoAgenteIaModel, match_status: str) -> dict:
    return {
        "questao_id": item.questao_id,
        "modulos_sugeridos": item.modulos_sugeridos,
        "disciplina": item.disciplina,
        "confianca_media": item.confianca_media,
        "modelo_utilizado": item.modelo_utilizado,
        "usou_llm": item.usou_llm,
        "prompt_version": item.prompt_version,
        "tem_justificativa": item.justificativas is not None,
        "match_status": match_status,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _build_base_query(pg_db: Session, modelo_filter: Optional[str], disciplina_filter: Optional[str]):
    query = pg_db.query(ClassificacaoAgenteIaModel)
    if modelo_filter and modelo_filter not in ("", "all"):
        if modelo_filter == "gpt-4o":
            query = query.filter(ClassificacaoAgenteIaModel.modelo_utilizado.ilike("%gpt-4o_%"))
        else:
            query = query.filter(ClassificacaoAgenteIaModel.modelo_utilizado.ilike(f"%{modelo_filter}%"))
    if disciplina_filter and disciplina_filter not in ("", "all"):
        query = query.filter(ClassificacaoAgenteIaModel.disciplina == disciplina_filter)
    return query


def _fetch_manuals_for_ids(pg_db: Session, questao_ids: list) -> dict:
    if not questao_ids:
        return {}
    rows = (
        pg_db.query(ClassificacaoUsuarioModel)
        .filter(
            ClassificacaoUsuarioModel.questao_id.in_(questao_ids),
            ClassificacaoUsuarioModel.usuario_id != 0,
        )
        .all()
    )
    return {m.questao_id: m for m in rows}


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
    pg_db: Session = Depends(get_db)
):
    """Lista classificações IA com paginação e filtros.

    Quando não há match_filter: pagina direto no SQL (rápido).
    Quando há match_filter: carrega apenas os IDs IA filtrados e busca
    manuals somente para esses IDs (evita full-table scan desnecessário).
    """
    try:
        needs_match_filter = bool(match_filter and match_filter not in ("", "all"))
        base_query = _build_base_query(pg_db, modelo_filter, disciplina_filter)

        if not needs_match_filter:
            # Caminho rápido: SQL-level pagination — O(page_size) em memória
            total = base_query.count()
            page_items = (
                base_query
                .order_by(ClassificacaoAgenteIaModel.created_at.desc())
                .offset((page - 1) * per_page)
                .limit(per_page)
                .all()
            )
            manual_dict = _fetch_manuals_for_ids(pg_db, [i.questao_id for i in page_items])
            results = [
                _build_list_item(item, _compute_match_status(item, manual_dict.get(item.questao_id)))
                for item in page_items
            ]
            return {
                "total": total,
                "page": page,
                "per_page": per_page,
                "pages": (total + per_page - 1) // per_page if total else 0,
                "items": results,
            }

        # Caminho com filtro de match: carrega todos os IDs IA filtrados,
        # mas busca manuais apenas para esses IDs (não para toda a tabela).
        all_items = base_query.order_by(ClassificacaoAgenteIaModel.created_at.desc()).all()
        manual_dict = _fetch_manuals_for_ids(pg_db, [i.questao_id for i in all_items])

        filtered: list[tuple] = []
        for item in all_items:
            status = _compute_match_status(item, manual_dict.get(item.questao_id))
            if status == match_filter:
                filtered.append((item, status))

        total = len(filtered)
        page_slice = filtered[(page - 1) * per_page: page * per_page]
        results = [_build_list_item(item, match) for item, match in page_slice]

        return {
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page if total else 0,
            "items": results,
        }

    except Exception as e:
        logger.error(f"Erro ao listar classificações: {e}")
        return {"total": 0, "items": [], "error": str(e)}


@router.get("/classificacao/{questao_id}")
async def get_classificacao_detail(
    questao_id: int,
    pg_db: Session = Depends(get_db),
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
        
        ia_set = set(ia.modulos_sugeridos or [])
        ia_desc_set = extract_description_set(ia.assuntos_sugeridos)
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

        questao = (
            db.query(QuestaoModel)
            .options(joinedload(QuestaoModel.alternativas))
            .filter(QuestaoModel.id == questao_id)
            .first()
        )

        alternativas = []
        if questao and questao.alternativas:
            ordenadas = sorted(
                questao.alternativas,
                key=lambda a: (a.ordem if a.ordem is not None else 9999, a.id or 0),
            )
            for idx, alt in enumerate(ordenadas, start=1):
                if not (alt.conteudo or "").strip():
                    continue
                alternativas.append(
                    {
                        "ordem": alt.ordem if alt.ordem is not None else idx,
                        "conteudo_html": alt.conteudo or "",
                        "conteudo": re.sub(r"<[^>]+>", "", alt.conteudo or "").strip(),
                    }
                )

        enunciado_html = questao.enunciado if questao and questao.enunciado else ia.enunciado
        texto_base_html = questao.texto_base if questao else None
        has_images = bool(collect_question_image_urls(questao, enunciado_html or "")) if questao else False
        analise_imagem_ia = None
        if isinstance(ia.justificativas, dict):
            analise_imagem_ia = ia.justificativas.get("__analise_imagem__")
        
        return {
            "questao_id": questao_id,
            "enunciado": enunciado_html,
            "enunciado_html": enunciado_html,
            "texto_base_html": texto_base_html,
            "alternativas": alternativas,
            "has_images": has_images,
            "disciplina": ia.disciplina,
            "habilidade_trieduc": ia.habilidade_trieduc,
            "ia": {
                "modulos_sugeridos": ia.modulos_sugeridos,
                "justificativas": ia.justificativas,
                "modulos_possiveis": ia.modulos_possiveis,
                "assuntos_sugeridos": ia.assuntos_sugeridos,
                "analise_imagem": analise_imagem_ia,
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
async def validar_stream(limit: int = 5000):
    """Classifica as primeiras N questões manuais com streaming SSE"""
    
    def event_generator():
        global CANCEL_VALIDATION
        CANCEL_VALIDATION = False
        pg_session = SessionLocal()
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
                    
                    ia_saved = pg_session.query(ClassificacaoAgenteIaModel).filter(
                        ClassificacaoAgenteIaModel.questao_id == qid
                    ).first()
                    ia_desc_set = extract_description_set(ia_saved.assuntos_sugeridos if ia_saved else None)

                    match = "none"
                    if manual_mods:
                        ia_mods_set = set(result.modulos_sugeridos or [])
                        m_mods_set = set(manual_mods)
                        manual_desc_set = set([d for d in (manual_desc or []) if d])
                        
                        if ia_mods_set == m_mods_set and ia_desc_set == manual_desc_set:
                            match = "exact"
                        elif ia_mods_set & m_mods_set:
                            match = "partial"
                    
                    event_data = {
                        "type": "progress",
                        "index": i + 1,
                        "total": total,
                        "questao_id": qid,
                        "modulos_sugeridos": result.modulos_sugeridos,
                        "manual": manual_mods,
                        "ia_assuntos": sorted(list(ia_desc_set)),
                        "manual_assuntos": manual_desc or [],
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
                    persist_classificacao_ia_error(
                        questao_id=qid,
                        etapa="validar_stream_loop",
                        erro=ex,
                        payload={"index": i + 1, "total": total},
                        stacktrace=traceback.format_exc(),
                    )
                    yield f"data: {json.dumps({'type': 'error', 'index': i+1, 'questao_id': qid, 'error': str(ex)[:200]})}\n\n"
            
            yield f"data: {json.dumps({'type': 'done', 'sucesso': sucesso, 'erros': erros, 'total_tokens': total_tokens, 'total_cost': round(total_cost, 4)})}\n\n"
            
        except Exception as e:
            persist_classificacao_ia_error(
                questao_id=None,
                etapa="validar_stream_fatal",
                erro=e,
                stacktrace=traceback.format_exc(),
            )
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


