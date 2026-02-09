"""Cliente para OpenAI API"""

import time
from typing import Dict, Any, Optional
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential
from loguru import logger

from ..config import settings


class OpenAIClient:
    """Cliente wrapper para OpenAI API com retry e rate limiting"""

    def __init__(self):
        """Inicializa o cliente OpenAI"""
        self.client = OpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_model
        self.max_tokens = settings.openai_max_tokens
        self.temperature = settings.openai_temperature

    @retry(
        stop=stop_after_attempt(settings.max_retries),
        wait=wait_exponential(multiplier=settings.retry_delay, min=1, max=10),
        reraise=True,
    )
    def create_completion(
        self,
        messages: list[Dict[str, str]],
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Cria uma completion com retry automático

        Args:
            messages: Lista de mensagens do chat
            model: Modelo a ser usado (padrão: configuração)
            max_tokens: Máximo de tokens (padrão: configuração)
            temperature: Temperature (padrão: configuração)
            **kwargs: Parâmetros adicionais

        Returns:
            Dicionário com a resposta da API
        """
        start_time = time.time()

        try:
            response = self.client.chat.completions.create(
                model=model or self.model,
                messages=messages,
                max_tokens=max_tokens or self.max_tokens,
                temperature=(
                    temperature if temperature is not None else self.temperature
                ),
                **kwargs,
            )

            processing_time = int((time.time() - start_time) * 1000)

            result = {
                "content": response.choices[0].message.content,
                "model": response.model,
                "tokens_used": response.usage.total_tokens,
                "processing_time_ms": processing_time,
                "finish_reason": response.choices[0].finish_reason,
            }

            logger.debug(
                f"Completion criada | Modelo: {result['model']} | "
                f"Tokens: {result['tokens_used']} | Tempo: {processing_time}ms"
            )

            return result

        except Exception as e:
            logger.error(f"Erro ao criar completion: {str(e)}")
            raise
