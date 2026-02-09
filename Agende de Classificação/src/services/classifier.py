"""Serviço de classificação de questões"""

import json
from typing import List, Dict
from loguru import logger

from ..models import Question, Classification
from ..config import settings, Habilidade
from .openai_client import OpenAIClient


class QuestionClassifier:
    """Classificador de questões usando OpenAI"""

    def __init__(self):
        """Inicializa o classificador"""
        self.client = OpenAIClient()

    def _build_prompt(
        self, question: Question, categories: List[str]
    ) -> List[Dict[str, str]]:
        """Constrói o prompt para classificação

        Args:
            question: Questão a ser classificada
            categories: Lista de categorias possíveis

        Returns:
            Lista de mensagens formatadas para a API
        """
        system_message = """Você é um especialista em classificação de questões educacionais.
Sua tarefa é analisar a questão fornecida e classificá-la nas categorias mais apropriadas.

Responda APENAS com um JSON no seguinte formato:
{
  "categories": ["categoria1", "categoria2"],
  "confidence_scores": {
    "categoria1": 0.95,
    "categoria2": 0.87
  },
  "reasoning": "Breve explicação da classificação"
}

Use apenas as categorias fornecidas. Escolha as mais relevantes (mínimo 1, máximo 3).
Os scores de confiança devem estar entre 0 e 1."""

        categories_str = "\n".join([f"- {cat}" for cat in categories])

        user_message = f"""Categorias disponíveis:
{categories_str}

Questão a classificar:
{question.content}

Classifique esta questão."""

        return [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ]

    def _build_habilidade_prompt(
        self, question: Question, habilidades: List[Habilidade]
    ) -> List[Dict[str, str]]:
        """Constrói o prompt para classificação de habilidade

        Args:
            question: Questão a ser classificada
            habilidades: Lista de habilidades possíveis

        Returns:
            Lista de mensagens formatadas para a API
        """
        system_message = """Você é um especialista em classificação de questões educacionais.
Sua tarefa é analisar a questão fornecida e identificar a habilidade mais apropriada.

Responda APENAS com um JSON no seguinte formato:
{
  "habilidade_id": "id-da-habilidade",
  "confidence": 0.95,
  "reasoning": "Breve explicação da escolha"
}

Escolha APENAS UMA habilidade, a mais relevante para a questão.
O score de confiança deve estar entre 0 e 1."""

        habilidades_str = "\n".join(
            [f"- ID: {h.id} | {h.habilidade} ({h.ano})" for h in habilidades]
        )

        user_message = f"""Habilidades disponíveis:
{habilidades_str}

Questão a classificar:
{question.content}

Identifique a habilidade mais apropriada."""

        return [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ]

    def _classify_habilidade(
        self, question: Question, disciplina: str
    ) -> List[Dict[str, str]]:
        """Classifica a habilidade específica dentro de uma disciplina

        Args:
            question: Questão a ser classificada
            disciplina: Disciplina identificada

        Returns:
            Lista de habilidades identificadas
        """
        # Busca as habilidades da disciplina
        habilidades = settings.get_habilidades_by_discipline(disciplina)

        if not habilidades:
            logger.warning(
                f"Nenhuma habilidade cadastrada para a disciplina '{disciplina}'"
            )
            return []

        logger.info(
            f"Classificando habilidade em '{disciplina}' ({len(habilidades)} disponíveis)"
        )

        # Constrói o prompt
        messages = self._build_habilidade_prompt(question, habilidades)

        # Chama a API
        response = self.client.create_completion(messages)

        # Parse da resposta
        try:
            result = json.loads(response["content"])
            habilidade_id = result.get("habilidade_id")

            # Busca a habilidade pelo ID
            habilidade_encontrada = next(
                (h for h in habilidades if h.id == habilidade_id), None
            )

            if habilidade_encontrada:
                logger.success(
                    f"Habilidade identificada: {habilidade_encontrada.habilidade}"
                )
                return [
                    {
                        "id": habilidade_encontrada.id,
                        "sigla": habilidade_encontrada.sigla,
                        "habilidade": habilidade_encontrada.habilidade,
                        "ano": habilidade_encontrada.ano,
                    }
                ]
            else:
                logger.warning(f"Habilidade com ID {habilidade_id} não encontrada")
                return []

        except json.JSONDecodeError as e:
            logger.error(f"Erro ao fazer parse da resposta de habilidade: {str(e)}")
            return []
        except Exception as e:
            logger.error(f"Erro ao classificar habilidade: {str(e)}")
            return []

    def classify(self, question: Question, categories: List[str]) -> Classification:
        """Classifica uma questão

        Args:
            question: Questão a ser classificada
            categories: Lista de categorias possíveis

        Returns:
            Objeto Classification com o resultado
        """
        logger.info(f"Classificando questão {question.id}")

        # Constrói o prompt
        messages = self._build_prompt(question, categories)

        # Chama a API
        response = self.client.create_completion(messages)

        # Parse da resposta
        try:
            result = json.loads(response["content"])

            # Identifica habilidades para cada disciplina
            habilidades_identificadas = []
            for disciplina in result["categories"]:
                habs = self._classify_habilidade(question, disciplina)
                habilidades_identificadas.extend(habs)

            classification = Classification(
                question_id=question.id,
                categories=result["categories"],
                confidence_scores=result.get("confidence_scores", {}),
                habilidades=habilidades_identificadas,
                reasoning=result.get("reasoning"),
                model_used=response["model"],
                tokens_used=response["tokens_used"],
                processing_time_ms=response["processing_time_ms"],
            )

            logger.success(
                f"Questão {question.id} classificada | "
                f"Categorias: {', '.join(classification.categories)}"
            )

            return classification

        except json.JSONDecodeError as e:
            logger.error(f"Erro ao fazer parse da resposta: {str(e)}")
            logger.debug(f"Resposta recebida: {response['content']}")
            raise ValueError(
                f"Resposta da API não está em formato JSON válido: {str(e)}"
            ) from e
