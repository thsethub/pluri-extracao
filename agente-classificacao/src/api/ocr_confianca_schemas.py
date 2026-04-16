from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel


class RedacaoOcrConfiancaSchema(BaseModel):
    redacao_id: int
    teste_prova_id: Optional[int]
    redacao_status_id: int
    ocr_confianca: Optional[float]
    tema: Optional[str]
    redacao_texto: Optional[str]
    arquivo_anonimo_nome_armazenamento: Optional[str]

    model_config = {"from_attributes": True}


class RedacaoOcrConfiancaListResponse(BaseModel):
    data: List[RedacaoOcrConfiancaSchema]
    total: int
    page: int
    per_page: int
    pages: int


# ── Validação OCR (POST) ──


class ValidacaoOcrCreateSchema(BaseModel):
    redacao_id: int
    ocr_pulou_trechos: bool
    ocr_trocou_palavras: bool
    ocr_trocou_caracteres: bool


class ValidacaoOcrResponseSchema(BaseModel):
    id: int
    revisor_id: int
    redacao_id: int
    ocr_pulou_trechos: bool
    ocr_trocou_palavras: bool
    ocr_trocou_caracteres: bool
    created_at: Optional[datetime]

    model_config = {"from_attributes": True}


# ── Admin: resumo de validações ──


class RevisorResumoSchema(BaseModel):
    revisor_id: int
    revisor_nome: str
    revisado: int
    pulou_trechos_sim: int
    trocou_palavras_sim: int
    trocou_caracteres_sim: int


class StatusContadorSchema(BaseModel):
    redacao_status_id: int
    status_label: str
    total: int
    validado: int
    restante: int


class OcrAdminResumoResponse(BaseModel):
    revisores: List[RevisorResumoSchema]
    status_contadores: List[StatusContadorSchema]
