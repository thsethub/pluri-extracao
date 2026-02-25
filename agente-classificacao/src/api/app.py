"""Aplicação FastAPI"""

from fastapi import FastAPI, HTTPException, status, Body
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from typing import Any

from ..config import settings
from ..utils import setup_logger
from ..models import Question
from ..services import QuestionClassifier
from .schemas import (
    ClassifyRequest,
    ClassifyResponse,
    ErrorResponse,
    HealthResponse,
    DisciplinesResponse,
    HabilidadeSchema,
    HabilidadesByDisciplineResponse,
    AllHabilidadesResponse,
)
from .db_router import router as db_router
from .extracao_router import router as extracao_router
from .classificacao_router import router as classificacao_router
from .ia_classificacao_router import router as ia_classificacao_router

# Setup do logger
setup_logger(settings.log_level)

# Descrição detalhada da API
description = """
## 🎓 Agente de Classificação de Questões com IA

API REST para classificação automática de questões educacionais em disciplinas utilizando modelos de IA da OpenAI.

### 📚 Funcionalidades

* **Classificação Inteligente**: Identifica automaticamente a(s) disciplina(s) de uma questão
* **Habilidades Específicas**: Identifica habilidades detalhadas dentro de cada disciplina
* **Scores de Confiança**: Retorna níveis de confiança para cada disciplina identificada
* **Raciocínio Explicável**: Fornece justificativa da classificação
* **15 Disciplinas**: Suporta classificação em áreas educacionais diversas
* **Alta Performance**: Processamento rápido com retry automático
* **Formato Flexível**: Aceita múltiplos formatos de entrada (string, JSON, HTML entities)

### 🔑 Disciplinas Suportadas

- Artes
- Biologia
- Ciências
- Educação Física
- Espanhol
- Filosofia
- Física
- Geografia
- História
- Língua Inglesa
- Língua Portuguesa
- Matemática
- Natureza e Sociedade
- Química
- Sociologia

### 🚀 Como Usar

1. Acesse a documentação interativa em `/docs`
2. Use o endpoint `POST /classify-discipline` para classificar questões
3. Consulte `GET /disciplines` para ver todas as disciplinas disponíveis
4. Verifique a saúde da API com `GET /health`

### 📊 Exemplos de Uso

Confira a seção **Try it out** em cada endpoint para testar diretamente no navegador!
"""

# Criação da aplicação FastAPI com metadados completos
app = FastAPI(
    title="🎓 Agente de Classificação de Questões",
    description=description,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    contact={
        "name": "Equipe de Desenvolvimento",
        "email": "contato@exemplo.com",
    },
    license_info={
        "name": "MIT License",
        "url": "https://opensource.org/licenses/MIT",
    },
    openapi_tags=[
        {
            "name": "Health",
            "description": "Endpoints para verificação de saúde e status da aplicação",
        },
        {
            "name": "Disciplines",
            "description": "Gerenciamento e listagem de disciplinas e habilidades disponíveis",
        },
        {
            "name": "Classification",
            "description": "Endpoints de classificação de questões em disciplinas",
        },
        {
            "name": "Database",
            "description": "Endpoints de consulta ao banco de dados (anos, disciplinas, habilidades, questões)",
        },
        {
            "name": "Classificação Manual",
            "description": "Sistema de classificação manual por professores com autenticação JWT",
        },
    ],
)

# Configuração de CORS
# Em produção, permitimos as origens do domínio thsethub.com.br
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://.*\.thsethub\.com\.br|https?://localhost:\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registra os routers
app.include_router(db_router)
app.include_router(extracao_router)
app.include_router(classificacao_router)
app.include_router(ia_classificacao_router)

# Inicializa o classificador (singleton)
classifier = QuestionClassifier()


@app.get(
    "/",
    tags=["Health"],
    summary="🏠 Endpoint raiz",
    response_description="Informações básicas da API",
)
async def root():
    """
    ## Endpoint Raiz da API

    Retorna informações básicas sobre a API e links úteis para navegação.

    ### Retorna:
    - **message**: Mensagem de boas-vindas
    - **version**: Versão atual da API
    - **docs**: Link para documentação Swagger
    - **redoc**: Link para documentação ReDoc
    - **health**: Link para health check
    - **endpoints**: Lista de endpoints principais disponíveis
    """
    return {
        "message": "🎓 Agente de Classificação de Questões - API",
        "version": "1.0.0",
        "docs": "/docs",
        "redoc": "/redoc",
        "health": "/health",
        "endpoints": {
            "classify": "POST /classify-discipline",
            "disciplines": "GET /disciplines",
            "habilidades": "GET /habilidades",
            "habilidades_by_discipline": "GET /habilidades/{disciplina}",
        },
    }


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["Health"],
    summary="💚 Health check",
    response_description="Status de saúde da aplicação",
)
async def health_check():
    """
    ## Verificação de Saúde da Aplicação

    Endpoint para verificar se a aplicação está funcionando corretamente.

    ### Retorna:
    - **status**: Status da aplicação (healthy/unhealthy)
    - **version**: Versão da aplicação
    - **disciplines_count**: Número de disciplinas configuradas

    ### Status Codes:
    - **200**: Aplicação funcionando normalmente

    ### Exemplo de Resposta:
    ```json
    {
      "status": "healthy",
      "version": "1.0.0",
      "disciplines_count": 15
    }
    ```
    """
    disciplines = settings.get_disciplines_list()

    return HealthResponse(
        status="healthy", version="1.0.0", disciplines_count=len(disciplines)
    )


@app.get(
    "/disciplines",
    response_model=DisciplinesResponse,
    tags=["Disciplines"],
    summary="📚 Listar disciplinas",
    response_description="Lista completa de disciplinas disponíveis",
)
async def get_disciplines():
    """
    ## Listar Disciplinas Disponíveis

    Retorna a lista completa de disciplinas que o sistema pode identificar.

    ### Disciplinas Incluídas:
    - Artes
    - Biologia
    - Ciências
    - Educação Física
    - Espanhol
    - Filosofia
    - Física
    - Geografia
    - História
    - Língua Inglesa
    - Língua Portuguesa
    - Matemática
    - Natureza e Sociedade
    - Química
    - Sociologia

    ### Retorna:
    - **disciplines**: Array com os nomes das disciplinas
    - **count**: Número total de disciplinas

    ### Exemplo de Resposta:
    ```json
    {
      "disciplines": ["Artes", "Biologia", "Ciências", ...],
      "count": 15
    }
    ```
    """
    disciplines = settings.get_disciplines_list()

    return DisciplinesResponse(disciplines=disciplines, count=len(disciplines))


@app.get(
    "/habilidades",
    response_model=AllHabilidadesResponse,
    tags=["Disciplines"],
    summary="📋 Listar todas as habilidades",
    response_description="Lista completa de habilidades organizadas por disciplina",
)
async def get_all_habilidades():
    """
    ## Listar Todas as Habilidades

    Retorna todas as habilidades cadastradas, organizadas por disciplina.

    ### Retorna:
    - **habilidades**: Dicionário com habilidades por disciplina
    - **summary**: Contagem de habilidades por disciplina
    - **total**: Total de habilidades cadastradas

    ### Exemplo de Resposta:
    ```json
    {
      "habilidades": {
        "História": [
          {
            "id": "3a2d956d-bc60-4a88-a346-81066dd17a38",
            "sigla": "",
            "habilidade": "História da Arte Brasileira - Arte no Período Colonial",
            "ano": "Ensino Médio"
          }
        ],
        "Matemática": []
      },
      "summary": {
        "História": 13,
        "Matemática": 0
      },
      "total": 13
    }
    ```
    """
    habilidades_dict = settings.load_habilidades()
    summary = settings.get_all_habilidades_count()
    total = sum(summary.values())

    # Converte para schemas
    habilidades_response = {}
    for disciplina, habilidades in habilidades_dict.items():
        habilidades_response[disciplina] = [
            HabilidadeSchema(id=h.id, sigla=h.sigla, habilidade=h.habilidade, ano=h.ano)
            for h in habilidades
        ]

    return AllHabilidadesResponse(
        habilidades=habilidades_response, summary=summary, total=total
    )


@app.get(
    "/habilidades/{disciplina}",
    response_model=HabilidadesByDisciplineResponse,
    tags=["Disciplines"],
    summary="📖 Listar habilidades por disciplina",
    response_description="Lista de habilidades de uma disciplina específica",
)
async def get_habilidades_by_discipline(disciplina: str):
    """
    ## Listar Habilidades por Disciplina

    Retorna todas as habilidades de uma disciplina específica.

    ### Parâmetros:
    - **disciplina**: Nome da disciplina (ex: "História", "Matemática", etc.)

    ### Retorna:
    - **disciplina**: Nome da disciplina consultada
    - **habilidades**: Lista de habilidades da disciplina
    - **count**: Número total de habilidades

    ### Exemplo de Resposta:
    ```json
    {
      "disciplina": "História",
      "habilidades": [
        {
          "id": "3a2d956d-bc60-4a88-a346-81066dd17a38",
          "sigla": "",
          "habilidade": "História da Arte Brasileira - Arte no Período Colonial",
          "ano": "Ensino Médio"
        }
      ],
      "count": 13
    }
    ```

    ### Notas:
    - O nome da disciplina deve ser exato (case-sensitive)
    - Disciplinas sem habilidades cadastradas retornam array vazio
    """
    habilidades = settings.get_habilidades_by_discipline(disciplina)

    # Converte para schemas
    habilidades_schemas = [
        HabilidadeSchema(id=h.id, sigla=h.sigla, habilidade=h.habilidade, ano=h.ano)
        for h in habilidades
    ]

    return HabilidadesByDisciplineResponse(
        disciplina=disciplina, habilidades=habilidades_schemas, count=len(habilidades)
    )


@app.post(
    "/classify-discipline",
    response_model=ClassifyResponse,
    tags=["Classification"],
    summary="🎯 Classificar questão",
    response_description="Resultado da classificação com disciplinas identificadas",
    responses={
        200: {
            "description": "Classificação realizada com sucesso",
            "content": {
                "application/json": {
                    "example": {
                        "question_id": "550e8400-e29b-41d4-a716-446655440000",
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
                        "reasoning": "Questão sobre história do Brasil no contexto da Guerra Fria.",
                        "model_used": "gpt-3.5-turbo",
                        "tokens_used": 198,
                        "processing_time_ms": 1856,
                    }
                }
            },
        },
        400: {
            "model": ErrorResponse,
            "description": "Erro de validação - Questão inválida ou vazia",
            "content": {
                "application/json": {
                    "example": {
                        "error": "Validation Error",
                        "detail": "A questão não pode estar vazia",
                    }
                }
            },
        },
        500: {
            "model": ErrorResponse,
            "description": "Erro interno do servidor",
            "content": {
                "application/json": {
                    "example": {
                        "error": "Internal Server Error",
                        "detail": "Erro ao processar classificação: timeout da API OpenAI",
                    }
                }
            },
        },
    },
)
async def classify_discipline(body: Any = Body(...)):
    """
    ## Classificar Questão em Disciplinas

    Utiliza IA da OpenAI para identificar automaticamente a(s) disciplina(s) de uma questão educacional.

    ### Como Funciona:
    1. Recebe o texto da questão
    2. Analisa o conteúdo usando GPT-3.5-turbo ou GPT-4
    3. Identifica as disciplinas mais relevantes (1 a 3)
    4. Calcula scores de confiança para cada disciplina
    5. Gera uma justificativa explicando a classificação

    ### Parâmetros:
    - **question** (obrigatório): Questão a ser classificada
        - Aceita **string direta**: `"Qual é a fórmula química da água?"`
        - Aceita **objeto JSON completo**: Envia o objeto inteiro da questão com enunciado, alternativas, etc.
        - Aceita **objeto JSON** com campos flexíveis:
            - `{"question": "texto da questão"}`
            - `{"text": "texto da questão"}`
            - `{"content": "texto da questão"}`
            - `{"enunciado": "texto da questão"}`
            - `{"pergunta": "texto da questão"}`
            - Qualquer outro objeto que contenha um campo de texto

    ### Formatos Aceitos:

    **String simples:**
    ```json
    {
      "question": "Qual é a fórmula química da água?"
    }
    ```

    **Objeto completo da questão:**
    ```json
    {
      "question": {
        "id": "3f8b5046-691d-46ff-8915-0037571f1a3b",
        "enunciado": "Em 1947, o Partido Comunista foi colocado na ilegalidade no Brasil.",
        "alternativas": [...],
        "resolucao": "...",
        "classificacao": {...}
      }
    }
    ```

    **Ou envie diretamente o objeto da questão completo (sem wrapper "question"):**
    ```json
    {
      "id": "3f8b5046-691d-46ff-8915-0037571f1a3b",
      "enunciado": "Em 1947, o Partido Comunista foi colocado na ilegalidade no Brasil.",
      "alternativas": [...],
      "resolucao": "..."
    }
    ```

    **Objeto com metadados:**
    ```json
    {
      "question": {
        "text": "Calcule a derivada de f(x) = 3x²",
        "metadata": {
          "source": "exam",
          "difficulty": "medium"
        }
      }
    }
    ```

    ### Retorna:
    - **question_id**: UUID único da questão
    - **question**: Texto original da questão
    - **disciplines**: Lista de disciplinas identificadas (1-3)
    - **confidence_scores**: Score de 0 a 1 para cada disciplina
    - **habilidades**: Lista de habilidades específicas identificadas (quando disponíveis)
    - **reasoning**: Explicação da classificação
    - **model_used**: Modelo OpenAI utilizado
    - **tokens_used**: Total de tokens consumidos
    - **processing_time_ms**: Tempo de processamento em milissegundos

    ### Exemplos de Questões:

    **Química:**
    ```
    Qual é a fórmula química da água e como ela se forma?
    ```

    **Matemática:**
    ```
    Calcule a derivada da função f(x) = 3x² + 2x - 5
    ```

    **História:**
    ```
    Quais foram as principais causas da Segunda Guerra Mundial?
    ```

    **Multidisciplinar (Biologia + Química):**
    ```
    Como a fotossíntese das plantas contribui para o ciclo do carbono?
    ```

    ### Notas Importantes:
    - O sistema pode identificar até 3 disciplinas por questão
    - Questões multidisciplinares retornam múltiplas categorias
    - O score de confiança indica a relevância de cada disciplina
    - **Habilidades específicas** são identificadas automaticamente quando cadastradas para a disciplina
    - Disciplinas sem habilidades cadastradas retornam array vazio em `habilidades`
    - O processamento geralmente leva entre 1-3 segundos
    - Aceita HTML entities (&#227;, &#231;, etc.) que são automaticamente decodificados
    """
    try:
        # Log do body recebido para debug
        logger.debug(f"Body recebido (tipo: {type(body)}): {str(body)[:100]}...")

        # Tenta criar o request a partir do body recebido
        # Se o body já contém o campo "question", usa direto
        if isinstance(body, dict) and "question" in body:
            request_data = body
        else:
            # Caso contrário, considera o body inteiro como a questão
            request_data = {"question": body}

        # Valida usando o schema Pydantic
        request = ClassifyRequest(**request_data)

        logger.info(f"Recebida requisição de classificação: {request.question[:50]}...")

        # Cria objeto Question
        question = Question(content=request.question)

        # Obtém disciplinas disponíveis
        disciplines = settings.get_disciplines_list()

        # Classifica
        classification = classifier.classify(question, disciplines)

        # Converte habilidades para schema
        habilidades_schemas = [
            HabilidadeSchema(
                id=h["id"], sigla=h["sigla"], habilidade=h["habilidade"], ano=h["ano"]
            )
            for h in classification.habilidades
        ]

        # Monta resposta
        response = ClassifyResponse(
            question_id=str(question.id),
            question=question.content,
            disciplines=classification.categories,
            confidence_scores=classification.confidence_scores,
            habilidades=habilidades_schemas,
            reasoning=classification.reasoning,
            model_used=classification.model_used,
            tokens_used=classification.tokens_used,
            processing_time_ms=classification.processing_time_ms,
        )

        logger.success(f"Questão {question.id} classificada com sucesso")

        return response

    except ValueError as e:
        logger.error(f"Erro de validação: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except Exception as e:
        logger.error(f"Erro ao classificar questão: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao processar classificação: {str(e)}",
        ) from e


@app.on_event("startup")
async def startup_event():
    """Evento executado no startup da aplicação"""
    logger.info("🚀 Iniciando Agente de Classificação de Questões")
    logger.info(f"📚 Disciplinas configuradas: {len(settings.get_disciplines_list())}")
    logger.info(f"🤖 Modelo OpenAI: {settings.openai_model}")

    # Inicializa tabelas no PostgreSQL local
    from ..database import init_pg_tables

    try:
        init_pg_tables()
        logger.info("🗃️ Tabelas PostgreSQL inicializadas com sucesso")
    except Exception as e:
        logger.warning(f"⚠️ Falha ao inicializar PostgreSQL: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Evento executado no shutdown da aplicação"""
    logger.info("🛑 Encerrando Agente de Classificação de Questões")
