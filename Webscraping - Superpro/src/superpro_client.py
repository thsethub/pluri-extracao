"""
Cliente direto da API do SuperProfessor.
Faz chamadas HTTP sem browser para busca e detalhes de questões.
"""

import asyncio
import re
import unicodedata
from typing import Optional
from difflib import SequenceMatcher

import httpx
from loguru import logger
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    retry_if_exception,
    RetryError,
)

from .token_manager import TokenManager


def _is_server_error(exc: BaseException) -> bool:
    """Retorna True se for um HTTPStatusError com status 5xx."""
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500


# ── Normalização Unicode (evita 500 na API SuperProfessor) ───────────


def _sanitize_text_for_api(texto: str) -> str:
    """
    Normalização GENERALISTA de Unicode antes de enviar à API.

    Usa NFKC (Compatibility Decomposition + Canonical Composition) que
    resolve automaticamente centenas de caracteres problemáticos:
      - Ligaturas tipográficas:  ﬁ→fi  ﬂ→fl  ﬃ→ffi  ﬄ→ffl  ﬀ→ff
      - Fullwidth chars:  Ａ→A  １→1  （→(
      - Sobrescrito/subscrito:  ²→2  ₃→3  ⁿ→n
      - Frações:  ½→1⁄2  ¼→1⁄4
      - Romanos compat.:  ⅰ→i  Ⅳ→IV
      - Símbolos:  ™→TM  ℃→°C  №→No
      - E qualquer outro mapeamento de compatibilidade Unicode

    Depois normaliza espaços, traços, aspas e remove controle invisível.
    """
    if not texto:
        return texto

    # 1. NFKC: decomposição de compatibilidade + composição canônica
    #    Resolve ligaturas, fullwidth, sobrescrito, etc. automaticamente
    texto = unicodedata.normalize("NFKC", texto)

    # 2. Espaços Unicode remanescentes -> espaço ASCII
    texto = re.sub(
        r"[\u00a0\u2000-\u200b\u2028\u2029\u202f\u205f\u2060\u3000\ufeff]",
        " ",
        texto,
    )

    # 3. Traços/hífens Unicode -> hífen ASCII
    texto = re.sub(r"[\u2010-\u2015\u2212\ufe58\ufe63\uff0d]", "-", texto)

    # 4. Aspas Unicode -> aspas ASCII
    texto = re.sub(r"[\u201c\u201d\u201e\u201f\u00ab\u00bb\u2039\u203a]", '"', texto)
    texto = re.sub(r"[\u2018\u2019\u201a\u201b\u2032\u2035]", "'", texto)

    # 5. Reticências Unicode -> ...
    texto = texto.replace("\u2026", "...")

    # 6. Bullet e símbolos de lista -> hífen
    texto = re.sub(
        r"[\u2022\u2023\u2043\u204c\u204d\u25aa\u25cf\u25e6\u2619]", "-", texto
    )

    # 7. Remover combining marks decorativos (sublinhado, sobrelinhas, etc.)
    #    Range U+0300-U+036F = combining diacritical marks
    texto = re.sub(r"[\u0300-\u036f]", "", texto)

    # 8. Remover caracteres de controle invisíveis (exceto \n \r \t)
    texto = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", texto)

    # 9. Remover variation selectors e outros zero-width
    texto = re.sub(r"[\ufe00-\ufe0f\u200c-\u200f\u202a-\u202e]", "", texto)

    # 10. CATCH-ALL: remover qualquer char exótico remanescente
    #     Mantém: ASCII (U+0020-U+007E), Latin-1 Supplement (U+00A0-U+00FF),
    #     Latin Extended-A/B (U+0100-U+024F), e espaços/newlines (\n\r\t)
    #     Remove: Cirílico, Grego, símbolos matemáticos, etc.
    texto = re.sub(r"[^\x09\x0a\x0d\x20-\x7e\u00a0-\u024f]", "", texto)

    return texto


# Mapeamento: nosso disc_id -> SP ID_MATERIA
DISC_MAP = {
    1: 22,  # Artes -> 22
    2: 1,  # Biologia -> 1
    5: 9,  # Espanhol -> 9
    6: 10,  # Filosofia -> 10
    7: 2,  # Física -> 2
    8: 3,  # Geografia -> 3
    9: 4,  # História -> 4
    10: 5,  # Inglês -> 5
    11: 7,  # Língua Portuguesa -> 7
    12: 6,  # Matemática -> 6
    14: 8,  # Química -> 8
    15: 11,  # Sociologia -> 11
}

SUPERPRO_API = "https://api-questoes.superprofessor.com.br/api"

# Tipos de ensino
TEACHING_TYPES = ["MEDIO", "FUNDAMENTAL", "SUPERIOR"]


class SuperProClient:
    """Cliente para a API REST interna do SuperProfessor."""

    def __init__(self, token_manager: TokenManager):
        self.token_manager = token_manager
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def start(self):
        """Inicia o cliente HTTP."""
        await self.token_manager.ensure_valid_token()
        self._client = httpx.AsyncClient(
            headers=self.token_manager.headers,
            timeout=httpx.Timeout(60.0, connect=15.0),
        )
        logger.info("SuperPro API client iniciado")

    async def close(self):
        """Fecha o cliente HTTP."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _ensure_client(self):
        """Garante que o client está ativo e com token válido."""
        if not self.token_manager.is_valid:
            logger.warning("Token expirado, renovando...")
            await self.token_manager.ensure_valid_token()
            if self._client:
                self._client.headers.update(self.token_manager.headers)

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=2, min=2, max=15),
        retry=(
            retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError))
            | retry_if_exception(_is_server_error)
        ),
        before_sleep=lambda rs: logger.warning(
            f"Retry {rs.attempt_number}/2 após {type(rs.outcome.exception()).__name__}: {rs.outcome.exception()}"
        ),
    )
    async def search_questions(
        self,
        text: str,
        sp_materia_id: int | None = None,
        teaching_type: str = "MEDIO",
        mode: str = "EVERY",
    ) -> list[int]:
        """
        Busca questões na API do SuperProfessor.

        Args:
            text: Texto para buscar no enunciado
            sp_materia_id: ID da matéria no SuperProfessor (opcional)
            teaching_type: MEDIO, FUNDAMENTAL ou SUPERIOR
            mode: EVERY (todas as palavras) ou SOME (qualquer palavra)

        Returns:
            Lista de IDs de questões encontradas
        """
        await self._ensure_client()

        disciplines = []
        if sp_materia_id:
            disciplines = [
                {
                    "ID_MATERIA": str(sp_materia_id),
                    "ID_DIVISAO": 0,
                    "ID_TOPICO": 0,
                    "ID_ITEM": 0,
                    "ID_SUBITEM": 0,
                }
            ]

        body = {
            "latter_questions": True,
            "disciplines": disciplines,
            "teaching_type": teaching_type,
            "text_to_search": _sanitize_text_for_api(
                text
            ),  # REDUNDÂNCIA: sanitizar antes de enviar
            "text_question_enunciated": True,
            "text_search_type": mode,
        }

        resp = await self._client.post(
            f"{SUPERPRO_API}/v2/spro-bco-questao-memory", json=body
        )
        resp.raise_for_status()
        return resp.json().get("QUESTION_IDS", [])

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=2, min=2, max=15),
        retry=(
            retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError))
            | retry_if_exception(_is_server_error)
        ),
    )
    async def get_specifics(self, question_ids: list[int]) -> list[dict]:
        """
        Obtém detalhes e classificações de questões.

        Args:
            question_ids: Lista de IDs (max ~50 por chamada)

        Returns:
            Lista de dicts com dados de cada questão
        """
        await self._ensure_client()

        if not question_ids:
            return []

        params = [("question_ids[]", str(qid)) for qid in question_ids]
        resp = await self._client.get(
            f"{SUPERPRO_API}/v2/spro-bco-questao/specifics", params=params
        )
        resp.raise_for_status()
        return resp.json().get("QUESTIONS", [])

    async def get_taxonomy(self, teaching_type: str = "MEDIO") -> list[dict]:
        """Obtém a árvore completa de matérias/assuntos."""
        await self._ensure_client()

        resp = await self._client.get(
            f"{SUPERPRO_API}/spro-materia-questao",
            params={"teaching_type": teaching_type, "sort_direction": "ASC"},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Limpeza e extração de termos
    # ------------------------------------------------------------------

    # Prefixos comuns que poluem a busca (case-insensitive)
    _NOISE_PREFIXES = re.compile(
        r"^\s*"
        r"(?:"
        r"TEXTO\s+[IVX]+\s*[:\-]?\s*"
        r"|Leia\s+(?:o\s+)?(?:texto|trecho|fragmento|excerto)s?\s*(?:abaixo|a\s+seguir)?[.:\s]*"
        r"|Observe\s+(?:o|a|os|as)?\s*(?:texto|imagem|mapa|quadro|tabela|gráfico|figura)s?\s*(?:abaixo|a\s+seguir)?[.:\s]*"
        r"|Considere\s+o\s+texto\s*(?:abaixo|a\s+seguir)?[.:\s]*"
        r"|Analise\s+(?:o|a|os|as)\s+(?:texto|trecho|figura|imagem|quadro|mapa|gr[aá]fico|tabela|charge|tirinha)s?\s*(?:a\s+seguir|abaixo|seguinte)?[.:\s]*"
        r")",
        re.IGNORECASE,
    )

    _REFERENCE_PATTERNS = re.compile(
        r"(?:"
        r"Dispon[ií]vel\s+em:?\s*\S+\.?\s*"
        r"|Acesso\s+em:?\s*[^.]*\.\s*"
        r"|\(\s*(?:Adaptado|Fonte|Extra[ií]do|Retirado)\s+de[^)]*\)\.?\s*"
        r")",
        re.IGNORECASE,
    )

    @classmethod
    def clean_enunciado(cls, text: str) -> str:
        """Remove prefixos genéricos, referências bibliográficas e Unicode problemático."""
        # PRIMEIRO: normalizar Unicode (ligaturas, espaços especiais, etc.)
        cleaned = _sanitize_text_for_api(text)
        cleaned = cls._NOISE_PREFIXES.sub("", cleaned).strip()
        cleaned = cls._REFERENCE_PATTERNS.sub("", cleaned).strip()
        # "Texto I", "Texto II", "Texto 1" etc. em qualquer posição
        cleaned = re.sub(
            r"Texto\s+(?:[IVX]+|\d+)\s*[:\-]?\s*", "", cleaned, flags=re.IGNORECASE
        ).strip()
        # Créditos de imagem no início
        cleaned = re.sub(
            r"^\s*(?:Charge|Foto|Imagem|Ilustra[cç]ão|Gravura)\s*(?:an[oô]nima)?[.:,]?\s*",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()
        # Se removeu quase tudo, voltar ao original
        return cleaned if len(cleaned) > 20 else text.strip()

    @staticmethod
    def extract_search_terms(text: str, max_words: int = 7) -> str:
        """Extrai as primeiras palavras significativas do enunciado para busca."""
        # Remover caracteres especiais mas manter acentos
        clean = re.sub(r"[^\w\sáàâãéèêíìîóòôõúùûçÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ]", " ", text)
        clean = re.sub(r"\s+", " ", clean).strip()

        words = clean.split()
        selected = words[:max_words]
        return " ".join(selected)

    @staticmethod
    def extract_first_sentence(text: str, max_sentences: int = 1) -> str:
        """Extrai a(s) primeira(s) frase(s) do enunciado (até o ponto final)."""
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        result = " ".join(sentences[:max_sentences]).strip()
        words = result.split()
        if len(words) > 20:
            result = " ".join(words[:20])
        return result

    @staticmethod
    def extract_last_sentence(text: str) -> str:
        """Extrai a última frase significativa (onde geralmente está a pergunta)."""
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        # Pegar a última frase não vazia com pelo menos 5 palavras
        for sent in reversed(sentences):
            sent = sent.strip()
            if len(sent.split()) >= 5:
                words = sent.split()
                if len(words) > 20:
                    sent = " ".join(words[:20])
                return sent
        return ""

    @staticmethod
    def compare_texts(text_a: str, text_b: str) -> float:
        """Compara dois textos e retorna similaridade (0.0 a 1.0)."""
        a = re.sub(r"\s+", " ", text_a.lower().strip())
        b = re.sub(r"\s+", " ", text_b.lower().strip())
        return SequenceMatcher(None, a[:800], b[:800]).ratio()

    @staticmethod
    def format_classification(classif: dict) -> str:
        """Formata uma classificação como string 'Matéria > Divisão > Tópico > Item'."""
        parts = []
        try:
            for key in ["MATERIA", "DIVISAO", "TOPICO", "ITEM", "SUBITEM"]:
                val = classif.get(key)
                if val and isinstance(val, list) and len(val) > 0:
                    first = val[0]
                    if isinstance(first, dict):
                        # Tenta key direto (ex: "MATERIA"), depois "NOME_ITEM", "NOME", "DESCRICAO"
                        name = (
                            first.get(key, "")
                            or first.get(f"NOME_{key}", "")
                            or first.get("NOME", "")
                            or first.get("DESCRICAO", "")
                        )
                        if name:
                            parts.append(str(name))
                    elif isinstance(first, str):
                        parts.append(first)
                elif val and isinstance(val, str):
                    parts.append(val)
        except Exception as e:
            logger.debug(f"Erro ao formatar classificação: {e}")
        return " > ".join(p for p in parts if p)

    async def find_and_classify(
        self,
        enunciado: str,
        nosso_disc_id: int | None = None,
        min_similarity: float = 0.35,
    ) -> dict | None:
        """
        Busca uma questão no SuperProfessor e retorna sua classificação.

        Estratégias (em ordem):
        1. disc + frase1 (primeira frase limpa com disciplina)
        2. disc + 7 palavras (fallback mais curto com disciplina)
        3. sem_disc + frase1
        4. sem_disc + 7 palavras
        5. sem_disc + última frase (pergunta real em textos longos)
        6. sem_disc + 5 palavras (busca mais ampla possível)

        Returns:
            Dict com 'sp_id', 'similarity', 'classificacoes',
            ou {'api_error': True} se a API está fora do ar,
            ou None se não encontrar
        """
        if not enunciado or len(enunciado.strip()) < 20:
            return None

        # Limpar prefixos inúteis (TEXTO I, Leia o texto, etc)
        cleaned = self.clean_enunciado(enunciado)

        # Preparar termos de busca a partir do texto limpo
        frase1 = self.extract_first_sentence(cleaned, max_sentences=1)
        terms_7w = self.extract_search_terms(cleaned, max_words=7)
        terms_5w = self.extract_search_terms(cleaned, max_words=5)

        # Última frase (onde geralmente fica a pergunta em textos longos)
        last_sent = self.extract_last_sentence(cleaned)

        # Precisa de um mínimo de texto para buscar
        if len(frase1) < 8 and len(terms_7w) < 8:
            return None

        sp_materia_id = DISC_MAP.get(nosso_disc_id) if nosso_disc_id else None

        # Montar set de termos já usados para evitar duplicatas
        used_terms = set()
        strategies = []

        def add_strategy(name, terms, materia, mode):
            if terms and len(terms) >= 8 and terms not in used_terms:
                strategies.append((name, terms, materia, mode))
                used_terms.add(terms)

        # Com disciplina
        if sp_materia_id:
            add_strategy("disc+frase1", frase1, sp_materia_id, "EVERY")
            add_strategy("disc+7words", terms_7w, sp_materia_id, "EVERY")

        # Sem disciplina
        add_strategy("sem_disc+frase1", frase1, None, "EVERY")
        add_strategy("sem_disc+7words", terms_7w, None, "EVERY")

        # Última frase (captura perguntas no final de textos longos)
        if last_sent and last_sent != frase1:
            add_strategy("sem_disc+ultima", last_sent, None, "EVERY")

        # Fallback mais amplo
        add_strategy("sem_disc+5words", terms_5w, None, "EVERY")

        server_errors = 0

        for name, terms, materia, mode in strategies:
            # Short-circuit: se servidor já falhou 2x, não tentar mais
            if server_errors >= 2:
                logger.warning(
                    f"Abortando estratégias restantes (servidor instável, {server_errors} erros)"
                )
                break

            try:
                ids = await self.search_questions(
                    text=terms,
                    sp_materia_id=materia,
                    teaching_type="MEDIO",
                    mode=mode,
                )

                if not ids:
                    continue

                # Verificar os top 15 resultados
                check_ids = ids[:15]
                specs = await self.get_specifics(check_ids)

                if not specs or not isinstance(specs, list):
                    logger.debug(
                        f"[{name}] specs vazio ou formato inesperado: {type(specs)}"
                    )
                    continue

                best_match = None
                best_ratio = 0.0

                for sq in specs:
                    if not isinstance(sq, dict):
                        continue
                    sp_text = sq.get("TEXTO_QUESTAO", "")
                    if not sp_text:
                        continue
                    ratio = self.compare_texts(enunciado, sp_text)

                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_match = sq

                if best_match and best_ratio >= min_similarity:
                    sp_id = best_match.get("ID_BCO_QUESTAO", best_match.get("id", 0))
                    classifs = best_match.get("CLASSIFICACAO_QUESTAO", [])

                    if isinstance(classifs, list):
                        formatted = [
                            self.format_classification(c)
                            for c in classifs
                            if isinstance(c, dict)
                        ]
                    else:
                        formatted = []

                    logger.info(
                        f"MATCH [{name}] SP_ID={sp_id} "
                        f"sim={best_ratio:.0%} classifs={len(classifs)}"
                    )

                    return {
                        "sp_id": sp_id,
                        "similarity": best_ratio,
                        "strategy": name,
                        "classificacoes": formatted,
                        "raw_classificacoes": classifs,
                    }
                else:
                    logger.debug(
                        f"[{name}] Sem match suficiente. "
                        f"Melhor ratio={best_ratio:.2f}, specs={len(specs)}"
                    )

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    logger.error("Token expirado durante busca")
                    await self.token_manager.ensure_valid_token()
                    self._client.headers.update(self.token_manager.headers)
                    continue
                if e.response.status_code >= 500:
                    server_errors += 1
                logger.warning(f"Erro HTTP [{name}]: {e.response.status_code}")
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                server_errors += 1
                logger.warning(f"Erro conexão [{name}]: {type(e).__name__}")
            except RetryError as e:
                # Retry esgotado (5xx/timeout persistente) = erro de servidor
                server_errors += 1
                last_exc = e.last_attempt.exception() if e.last_attempt else None
                exc_detail = (
                    f"{type(last_exc).__name__}: {last_exc}"
                    if last_exc
                    else "desconhecido"
                )
                logger.warning(f"Retry esgotado [{name}]: {exc_detail}")
            except Exception as e:
                # KeyError, IndexError, TypeError etc = erro de parsing, NÃO de servidor
                logger.warning(f"Erro parsing [{name}]: {type(e).__name__}: {e}")

        # Se houve erros de servidor (inclusive short-circuit), sinalizar
        if server_errors >= 2:
            return {"api_error": True, "error_count": server_errors}

        return None
