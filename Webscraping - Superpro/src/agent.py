"""
Agente de extração de classificações do SuperProfessor.

Orquestra o fluxo completo:
1. Busca questão pendente na API local
2. Pesquisa no SuperProfessor via API direta (sem browser)
3. Compara textos para validar match
4. Salva classificação encontrada

Projetado para rodar por longas horas sem problemas.
"""

import asyncio
import random
from datetime import datetime

from loguru import logger

from .config import settings
from .token_manager import TokenManager
from .superpro_client import SuperProClient
from .local_api_client import LocalApiClient


class ExtractionAgent:
    """Agente autônomo de extração de classificações."""

    def __init__(self, disciplina_ids: list[int] | None = None):
        self.disciplina_ids = disciplina_ids or [
            12,
            2,
            11,
            7,
            14,
            6,
            8,
            9,
            15,
            10,
            5,
            1,
        ]
        self.token_manager = TokenManager(settings.STORAGE_DIR)
        self.superpro = SuperProClient(self.token_manager)
        self.local_api = LocalApiClient()

        # Estatísticas
        self.stats = {
            "started_at": None,
            "total_processed": 0,
            "found": 0,
            "not_found": 0,
            "errors": 0,
            "saved": 0,
            "consecutive_errors": 0,
            "server_down_rounds": 0,
            "current_discipline": None,
        }

    async def start(self):
        """Inicia os clientes."""
        await self.local_api.start()
        await self.superpro.start()
        self.stats["started_at"] = datetime.now()
        logger.info("=== Agente de extração iniciado ===")

    async def stop(self):
        """Encerra os clientes."""
        await self.superpro.close()
        await self.local_api.close()
        self._print_stats()
        logger.info("=== Agente de extração encerrado ===")

    def _print_stats(self):
        """Imprime estatísticas do agente."""
        s = self.stats
        elapsed = ""
        if s["started_at"]:
            delta = datetime.now() - s["started_at"]
            hours, rem = divmod(delta.total_seconds(), 3600)
            minutes, secs = divmod(rem, 60)
            elapsed = f"{int(hours)}h{int(minutes)}m{int(secs)}s"

        rate = (s["found"] / max(1, s["total_processed"])) * 100

        logger.info(
            f"\n{'='*50}\n"
            f"  ESTATÍSTICAS DA SESSÃO\n"
            f"{'='*50}\n"
            f"  Tempo: {elapsed}\n"
            f"  Processadas: {s['total_processed']}\n"
            f"  Encontradas: {s['found']} ({rate:.1f}%)\n"
            f"  Não encontradas: {s['not_found']}\n"
            f"  Erros: {s['errors']}\n"
            f"  Salvas: {s['saved']}\n"
            f"{'='*50}"
        )

    async def _process_question(self, questao: dict) -> str:
        """
        Processa uma questão: busca no SuperProfessor e salva classificação.

        Returns:
            'found', 'not_found', ou 'api_error'
        """
        qid = questao["id"]
        disc_id = questao.get("disciplina_id")
        enunciado = questao.get("enunciado_tratado", "")
        contem_imagem = questao.get("contem_imagem", False)

        if not enunciado or len(enunciado.strip()) < 20:
            logger.warning(
                f"Q#{qid}: Enunciado muito curto ({len(enunciado)} chars), salvando vazio"
            )
            await self.local_api.salvar_extracao(qid, [])
            return "not_found"

        # Se contém imagem, usar IA para extrair o enunciado real
        # Nota: enunciado aqui NÃO contém alternativas, apenas o texto do enunciado
        enunciado_ia = None
        if contem_imagem:
            logger.debug(f"Q#{qid}: Contém imagem, limpando com IA...")
            enunciado_limpo = await self.local_api.limpar_enunciado(enunciado)
            if enunciado_limpo and len(enunciado_limpo.strip()) >= 20:
                logger.debug(
                    f"Q#{qid}: IA limpou {len(enunciado)} -> {len(enunciado_limpo)} chars"
                )
                enunciado_ia = enunciado_limpo
                enunciado = enunciado_limpo
            else:
                logger.debug(f"Q#{qid}: IA não conseguiu limpar, usando original")

        if not enunciado or len(enunciado.strip()) < 20:
            logger.warning(
                f"Q#{qid}: Enunciado muito curto ({len(enunciado)} chars), salvando vazio"
            )
            await self.local_api.salvar_extracao(qid, [])
            return "not_found"

        # Construir texto de busca: enunciado + alternativas (formato SuperPro)
        # As alternativas são separadas do enunciado_tratado pela API
        texto_busca = enunciado
        alternativas = questao.get("alternativas", [])
        if alternativas:
            letras = "abcdefghij"
            partes = [
                f"{letras[i]}) {alt.get('conteudo', '')}"
                for i, alt in enumerate(alternativas)
            ]
            texto_busca = enunciado + " " + " ".join(partes)

        # Buscar no SuperProfessor
        result = await self.superpro.find_and_classify(
            enunciado=texto_busca,
            nosso_disc_id=disc_id,
            min_similarity=0.80,
        )

        if result and result.get("api_error"):
            logger.warning(f"Q#{qid}: API do SuperProfessor fora do ar")
            return "api_error"

        if result and result.get("sp_id"):
            sp_id = result["sp_id"]
            sim = result["similarity"]
            raw_classifs = result["classificacoes"]

            # Se match >= 80%, salvar como oficial. Caso contrário, salvar em "não enquadrada"
            classificacoes_oficiais = []
            classificacoes_nao_enquadradas = []

            if sim >= 0.80:
                classificacoes_oficiais = raw_classifs
                status_msg = "FOUND"
            else:
                classificacoes_nao_enquadradas = raw_classifs
                status_msg = "LOW_MATCH"

            logger.info(
                f"Q#{qid} -> SP#{sp_id} ({sim:.0%}) [{status_msg}] | "
                f"{' | '.join(raw_classifs[:3])}"
            )

            ok = await self.local_api.salvar_extracao(
                qid,
                classificacoes_oficiais,
                superpro_id=sp_id,
                enunciado_tratado=enunciado_ia,
                similaridade=sim,
                enunciado_superpro=result.get("enunciado_superpro"),
                classificacao_nao_enquadrada=classificacoes_nao_enquadradas
            )
            if ok:
                self.stats["saved"] += 1
            return "found" if sim >= 0.80 else "low_match"
        else:
            logger.info(f"Q#{qid}: Não encontrada no SuperProfessor")
            # Salvar como não encontrada (array vazio)
            await self.local_api.salvar_extracao(qid, [])
            return "not_found"

    async def run(
        self,
        max_questions: int = 0,
        delay_range: tuple[float, float] = (0.5, 1.5),
        max_workers: int = 2,
    ):
        """
        Loop principal do agente com processamento paralelo.

        Args:
            max_questions: Máximo de questões a processar (0 = infinito)
            delay_range: Intervalo de delay entre questões (min, max) em segundos
            max_workers: Número de questões processadas em paralelo (default: 2)
        """
        await self.start()
        semaphore = asyncio.Semaphore(max_workers)
        logger.info(f"Paralelismo: {max_workers} workers")

        async def _worker(questao: dict):
            """Worker que processa uma questão com semáforo de concorrência."""
            async with semaphore:
                qid = questao["id"]
                try:
                    status = await self._process_question(questao)

                    if status == "found":
                        self.stats["found"] += 1
                        self.stats["consecutive_errors"] = 0
                        self.stats["server_down_rounds"] = 0
                    elif status == "api_error":
                        self.stats["errors"] += 1
                        self.stats["consecutive_errors"] += 1
                        self.stats["total_processed"] -= 1
                        logger.warning(
                            f"API instável — "
                            f"(erros: {self.stats['consecutive_errors']}/{settings.MAX_CONSECUTIVE_ERRORS})"
                        )
                    else:
                        self.stats["not_found"] += 1
                        self.stats["consecutive_errors"] = 0
                        self.stats["server_down_rounds"] = 0

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Erro ao processar Q#{qid}: {e}")
                    self.stats["errors"] += 1
                    self.stats["consecutive_errors"] += 1

                # Delay aleatório por worker (ser gentil com a API)
                delay = random.uniform(*delay_range)
                await asyncio.sleep(delay)

        try:
            disc_index = 0
            empty_rounds = 0

            while True:
                # Verificar limite
                if max_questions > 0 and self.stats["total_processed"] >= max_questions:
                    logger.info(f"Limite de {max_questions} questões atingido")
                    break

                # Verificar erros consecutivos
                if self.stats["consecutive_errors"] >= settings.MAX_CONSECUTIVE_ERRORS:
                    self.stats["server_down_rounds"] += 1
                    rounds = self.stats["server_down_rounds"]
                    max_rounds = settings.MAX_SERVER_DOWN_ROUNDS

                    if rounds > max_rounds:
                        logger.error(
                            f"❌ API instável por {rounds} rodadas consecutivas. "
                            f"Encerrando agente automaticamente."
                        )
                        break

                    pause = min(settings.LONG_PAUSE_SECONDS * rounds, 600)
                    logger.warning(
                        f"⚠️ {self.stats['consecutive_errors']} erros consecutivos "
                        f"(rodada {rounds}/{max_rounds}). "
                        f"Pausando {pause}s ({pause//60}min)..."
                    )
                    await asyncio.sleep(pause)
                    self.stats["consecutive_errors"] = 0

                # ── Fase 1: Coletar um lote de questões (serial) ──
                batch = []
                disciplines_tried = 0

                while len(batch) < max_workers and disciplines_tried < len(self.disciplina_ids):
                    if max_questions > 0 and (self.stats["total_processed"] + len(batch)) >= max_questions:
                        break

                    disc_id = self.disciplina_ids[disc_index % len(self.disciplina_ids)]
                    self.stats["current_discipline"] = disc_id

                    try:
                        questao = await self.local_api.proxima_questao(disc_id)
                    except Exception as e:
                        logger.error(f"Erro ao buscar próxima questão (disc {disc_id}): {e}")
                        self.stats["consecutive_errors"] += 1
                        self.stats["errors"] += 1
                        disc_index += 1
                        disciplines_tried += 1
                        continue

                    if not questao:
                        logger.debug(f"Disciplina {disc_id}: sem questões pendentes")
                        disc_index += 1
                        disciplines_tried += 1
                        empty_rounds += 1
                        continue

                    empty_rounds = 0
                    batch.append(questao)
                    disc_index += 1
                    disciplines_tried += 1

                # Nenhuma questão encontrada em nenhuma disciplina
                if not batch:
                    if empty_rounds >= len(self.disciplina_ids):
                        logger.info("Todas as disciplinas sem questões pendentes!")
                        break
                    await asyncio.sleep(5)
                    continue

                # ── Fase 2: Processar o lote em paralelo ──
                self.stats["total_processed"] += len(batch)
                logger.debug(f"Processando lote de {len(batch)} questão(ões) em paralelo")

                tasks = [asyncio.create_task(_worker(q)) for q in batch]
                await asyncio.gather(*tasks, return_exceptions=True)

                # Log periódico (a cada 10 questões)
                if self.stats["total_processed"] % 10 == 0:
                    self._print_stats()

        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Interrompido pelo usuário")
        except Exception as e:
            logger.error(f"Erro inesperado no loop principal: {e}")
        finally:
            await self.stop()

