"""Schemas da API"""

from pydantic import BaseModel, Field, field_validator
from typing import List, Dict, Optional, Any, Union
import html


class ClassifyRequest(BaseModel):
    """Request para classificação de questão - aceita diferentes formatos"""

    question: Union[str, Dict[str, Any], Any] = Field(
        ...,
        description="Questão a ser classificada (string ou objeto com campo 'question', 'text', 'content', 'enunciado', etc) ou objeto completo",
        examples=[
            "Qual é a fórmula química da água?",
            {"question": "Calcule a derivada da função f(x) = 3x² + 2x - 5"},
            {"text": "Quais foram as principais causas da Revolução Francesa?"},
            {
                "content": "Explique o processo de fotossíntese nas plantas",
                "metadata": {"source": "exam"},
            },
            {
                "enunciado": "Em 1947, o Partido Comunista foi colocado na ilegalidade no Brasil.",
                "alternativas": [{"conteudo": "Opção A", "correta": True}],
            },
        ],
    )

    @field_validator("question", mode="before")
    @classmethod
    def extract_question_text(cls, v: Any) -> str:
        """Extrai o texto da questão de diferentes formatos e decodifica HTML entities"""

        def clean_text(text: str) -> str:
            """Remove HTML entities e limpa o texto"""
            if text:
                # Decodifica HTML entities (&# 227; -> ã, etc)
                text = html.unescape(text)
                return text.strip()
            return text

        # Se já for string, retorna direto
        if isinstance(v, str):
            text = clean_text(v)
            if not text:
                raise ValueError("A questão não pode estar vazia")
            return text

        # Se for dict, tenta extrair de campos comuns
        if isinstance(v, dict):
            # Procura por campos conhecidos em ordem de prioridade
            for field in [
                "enunciado",  # Prioriza enunciado (comum em questões brasileiras)
                "question",
                "text",
                "content",
                "pergunta",
                "prompt",
                "titulo",
                "descricao",
            ]:
                if field in v and v[field] is not None:
                    # Aceita string ou converte para string
                    if isinstance(v[field], str):
                        text = clean_text(v[field])
                        if text:
                            return text
                    else:
                        # Tenta converter para string
                        try:
                            text = clean_text(str(v[field]))
                            if text and text != "None":
                                return text
                        except Exception:
                            continue

            # Se não encontrou, tenta pegar o primeiro valor string não vazio
            for value in v.values():
                if isinstance(value, str):
                    text = clean_text(value)
                    if (
                        text and len(text) > 10
                    ):  # Ignora strings muito curtas (IDs, etc)
                        return text

            raise ValueError("Nenhum campo de texto válido encontrado no objeto")

        # Tenta converter qualquer outro tipo para string
        try:
            text = clean_text(str(v))
            if not text or text == "None":
                raise ValueError("A questão não pode estar vazia")
            return text
        except Exception as e:
            raise ValueError(f"Formato de questão inválido: {str(e)}") from e

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"question": "Qual é a fórmula química da água?"},
                {"question": "Calcule a derivada da função f(x) = 3x² + 2x - 5"},
                {
                    "question": "Quais foram as principais causas da Segunda Guerra Mundial?"
                },
                {
                    "question": "Como a fotossíntese das plantas contribui para o ciclo do carbono?"
                },
                {"text": "Resolva a equação 2x + 5 = 15"},
                {
                    "content": "Explique o ciclo da água",
                    "metadata": {"source": "exam", "level": "high_school"},
                },
            ]
        }
    }


class HabilidadeSchema(BaseModel):
    """Schema de uma habilidade"""

    id: str = Field(..., description="ID único da habilidade (UUID)")
    sigla: str = Field(..., description="Sigla da habilidade (pode estar vazia)")
    habilidade: str = Field(..., description="Nome/descrição da habilidade")
    ano: str = Field(..., description="Ano escolar da habilidade")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "id": "3a2d956d-bc60-4a88-a346-81066dd17a38",
                    "sigla": "",
                    "habilidade": "História da Arte Brasileira - Arte no Período Colonial",
                    "ano": "Ensino Médio",
                }
            ]
        }
    }


class ClassifyResponse(BaseModel):
    """Response com resultado da classificação"""

    question_id: str = Field(
        ...,
        description="ID único da questão (UUID)",
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )
    question: str = Field(
        ...,
        description="Texto da questão classificada",
        examples=["Qual é a fórmula química da água?"],
    )
    disciplines: List[str] = Field(
        ...,
        description="Lista de disciplinas identificadas (1 a 3)",
        examples=[["Química"], ["Matemática"], ["Biologia", "Química"]],
    )
    confidence_scores: Dict[str, float] = Field(
        ...,
        description="Scores de confiança por disciplina (0.0 a 1.0)",
        examples=[
            {"Química": 0.98},
            {"Matemática": 0.95},
            {"Biologia": 0.92, "Química": 0.87},
        ],
    )
    habilidades: List[HabilidadeSchema] = Field(
        default_factory=list,
        description="Habilidades específicas identificadas dentro das disciplinas",
        examples=[
            [
                {
                    "id": "3a2d956d-bc60-4a88-a346-81066dd17a38",
                    "sigla": "",
                    "habilidade": "História da Arte Brasileira - Arte no Período Colonial",
                    "ano": "Ensino Médio",
                }
            ]
        ],
    )
    reasoning: Optional[str] = Field(
        None,
        description="Raciocínio e justificativa da classificação",
        examples=[
            "A questão aborda conceitos fundamentais de química molecular, especificamente a composição de substâncias."
        ],
    )
    model_used: str = Field(
        ...,
        description="Modelo OpenAI utilizado para classificação",
        examples=["gpt-3.5-turbo", "gpt-4"],
    )
    tokens_used: int = Field(
        ...,
        description="Total de tokens consumidos na requisição",
        ge=0,
        examples=[150, 245, 320],
    )
    processing_time_ms: int = Field(
        ...,
        description="Tempo de processamento em milissegundos",
        ge=0,
        examples=[1200, 1534, 2103],
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "question_id": "550e8400-e29b-41d4-a716-446655440000",
                    "question": "Qual é a fórmula química da água?",
                    "disciplines": ["Química"],
                    "confidence_scores": {"Química": 0.98},
                    "habilidades": [],
                    "reasoning": "A questão aborda conceitos fundamentais de química molecular, especificamente a composição de substâncias.",
                    "model_used": "gpt-3.5-turbo",
                    "tokens_used": 145,
                    "processing_time_ms": 1234,
                },
                {
                    "question_id": "660e8400-e29b-41d4-a716-446655440001",
                    "question": "Em 1947, o Partido Comunista foi colocado na ilegalidade no Brasil.",
                    "disciplines": ["História"],
                    "confidence_scores": {"História": 0.95},
                    "habilidades": [
                        {
                            "id": "3a2d956d-bc60-4a88-a346-81066dd17a38",
                            "sigla": "",
                            "habilidade": "História da Arte Brasileira - Arte no Período Colonial",
                            "ano": "Ensino Médio",
                        }
                    ],
                    "reasoning": "Questão sobre história do Brasil no período republicano, contexto político da Guerra Fria.",
                    "model_used": "gpt-3.5-turbo",
                    "tokens_used": 198,
                    "processing_time_ms": 1856,
                },
                {
                    "question_id": "770e8400-e29b-41d4-a716-446655440002",
                    "question": "Como a fotossíntese contribui para o ciclo do carbono?",
                    "disciplines": ["Biologia", "Química"],
                    "confidence_scores": {"Biologia": 0.95, "Química": 0.88},
                    "habilidades": [],
                    "reasoning": "Questão multidisciplinar envolvendo processos biológicos e reações químicas no contexto ambiental.",
                    "model_used": "gpt-3.5-turbo",
                    "tokens_used": 187,
                    "processing_time_ms": 1678,
                },
            ]
        }
    }


class ErrorResponse(BaseModel):
    """Response de erro"""

    error: str = Field(
        ...,
        description="Tipo ou categoria do erro",
        examples=["Validation Error", "Internal Server Error", "Bad Request"],
    )
    detail: Optional[str] = Field(
        None,
        description="Detalhes adicionais sobre o erro",
        examples=[
            "A questão não pode estar vazia",
            "Erro ao processar classificação: timeout da API OpenAI",
            "Formato JSON inválido",
        ],
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "error": "Validation Error",
                    "detail": "A questão não pode estar vazia",
                },
                {
                    "error": "Internal Server Error",
                    "detail": "Erro ao processar classificação: timeout da API OpenAI",
                },
            ]
        }
    }


class HealthResponse(BaseModel):
    """Response do health check"""

    status: str = Field(
        ..., description="Status da aplicação", examples=["healthy", "unhealthy"]
    )
    version: str = Field(..., description="Versão da aplicação", examples=["1.0.0"])
    disciplines_count: int = Field(
        ..., description="Número de disciplinas configuradas", ge=0, examples=[15]
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"status": "healthy", "version": "1.0.0", "disciplines_count": 15}
            ]
        }
    }


class DisciplinesResponse(BaseModel):
    """Response com lista de disciplinas"""

    disciplines: List[str] = Field(
        ...,
        description="Lista de disciplinas disponíveis para classificação",
        examples=[
            [
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
                "Matemática",
                "Natureza e Sociedade",
                "Química",
                "Sociologia",
            ]
        ],
    )
    count: int = Field(
        ..., description="Número total de disciplinas", ge=0, examples=[15]
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "disciplines": [
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
                        "Matemática",
                        "Natureza e Sociedade",
                        "Química",
                        "Sociologia",
                    ],
                    "count": 15,
                }
            ]
        }
    }


class HabilidadesByDisciplineResponse(BaseModel):
    """Response com habilidades de uma disciplina"""

    disciplina: str = Field(..., description="Nome da disciplina")
    habilidades: List[HabilidadeSchema] = Field(
        ..., description="Lista de habilidades da disciplina"
    )
    count: int = Field(
        ..., description="Número total de habilidades", ge=0, examples=[13]
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "disciplina": "História",
                    "habilidades": [
                        {
                            "id": "3a2d956d-bc60-4a88-a346-81066dd17a38",
                            "sigla": "",
                            "habilidade": "História da Arte Brasileira - Arte no Período Colonial",
                            "ano": "Ensino Médio",
                        }
                    ],
                    "count": 13,
                }
            ]
        }
    }


class AllHabilidadesResponse(BaseModel):
    """Response com todas as habilidades organizadas por disciplina"""

    habilidades: Dict[str, List[HabilidadeSchema]] = Field(
        ..., description="Habilidades organizadas por disciplina"
    )
    summary: Dict[str, int] = Field(
        ..., description="Contagem de habilidades por disciplina"
    )
    total: int = Field(..., description="Total de habilidades cadastradas", ge=0)

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "habilidades": {
                        "História": [
                            {
                                "id": "3a2d956d-bc60-4a88-a346-81066dd17a38",
                                "sigla": "",
                                "habilidade": "História da Arte Brasileira - Arte no Período Colonial",
                                "ano": "Ensino Médio",
                            }
                        ],
                        "Matemática": [],
                    },
                    "summary": {"História": 13, "Matemática": 0},
                    "total": 13,
                }
            ]
        }
    }
