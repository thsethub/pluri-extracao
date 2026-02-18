"""
Agente de reclassificação de questões com precisa_verificar=True.

Orquestra o fluxo:
1. Busca questão marcada como precisa_verificar na API local
2. Pesquisa no SuperProfessor via API direta
3. Compara textos para validar match
4. Salva nova classificação (sobrescreve anterior)
5. Para quando não houver mais questões pendentes

Uso:
    python main.py --reclassificar
    python main.py --reclassificar --max 50
"""

import asyncio
import random
from datetime import datetime

from loguru import logger

from .config import settings
from .token_manager import TokenManager
from .superpro_client import SuperProClient
from .local_api_client import LocalApiClient


class ReclassificationAgent:
    """Agente dedicado para re-classificar questões com precisa_verificar."""

    def __init__(self):
        self.token_manager = TokenManager(settings.STORAGE_DIR)
        self.superpro = SuperProClient(self.token_manager)
        self.local_api = LocalApiClient()

        self.stats = {
            "started_at": None,
            "total_processed": 0,
            "found": 0,
            "not_found": 0,
            "errors": 0,
            "saved": 0,
            "consecutive_errors": 0,
        }

    async def start(self):
        """Inicia os clientes."""
        await self.local_api.start()
        await self.superpro.start()
        self.stats["started_at"] = datetime.now()
        logger.info("=== Agente de reclassificação iniciado ===")

    async def stop(self):
        """Encerra os clientes."""
        await self.superpro.close()
        await self.local_api.close()
        self._print_stats()
        logger.info("=== Agente de reclassificação encerrado ===")

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
            f"  RECLASSIFICAÇÃO - ESTATÍSTICAS\n"
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
        Re-processa uma questão: busca no SuperProfessor e salva classificação.

        Returns:
            'found', 'not_found', 'low_match', ou 'api_error'
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

        # Se for múltipla escolha, concatenar alternativas ao enunciado
        if questao.get("tipo") == "Múltipla Escolha":
            letras = "abcdefghij"
            alts = questao.get("alternativas", [])
            if alts:
                partes = [f"{letras[i]}) {a['conteudo']}" for i, a in enumerate(alts)]
                enunciado = enunciado + " " + " ".join(partes)
                logger.debug(
                    f"Q#{qid}: Múltipla Escolha - {len(alts)} alternativas concatenadas"
                )

        # Buscar no SuperProfessor
        result = await self.superpro.find_and_classify(
            enunciado=enunciado,
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

            classificacoes_oficiais = []
            classificacoes_nao_enquadradas = []

            if sim >= 0.80:
                classificacoes_oficiais = raw_classifs
                status_msg = "FOUND"
            else:
                classificacoes_nao_enquadradas = raw_classifs
                status_msg = "LOW_MATCH"

            logger.info(
                f"[RECLASS] Q#{qid} -> SP#{sp_id} ({sim:.0%}) [{status_msg}] | "
                f"{' | '.join(raw_classifs[:3])}"
            )

            ok = await self.local_api.salvar_extracao(
                qid,
                classificacoes_oficiais,
                superpro_id=sp_id,
                enunciado_tratado=enunciado_ia,
                similaridade=sim,
                enunciado_superpro=result.get("enunciado_superpro"),
                classificacao_nao_enquadrada=classificacoes_nao_enquadradas,
            )
            if ok:
                self.stats["saved"] += 1
            return "found" if sim >= 0.80 else "low_match"
        else:
            logger.info(f"[RECLASS] Q#{qid}: Não encontrada no SuperProfessor")
            await self.local_api.salvar_extracao(qid, [])
            return "not_found"

    async def run(
        self,
        max_questions: int = 0,
        delay_range: tuple[float, float] = (0.5, 1.5),
    ):
        """
        Loop principal do agente de reclassificação.

        Args:
            max_questions: Máximo de questões a processar (0 = todas)
            delay_range: Intervalo de delay entre questões (min, max) em segundos
        """
        await self.start()

        try:
            while True:
                # Verificar limite
                if max_questions > 0 and self.stats["total_processed"] >= max_questions:
                    logger.info(f"Limite de {max_questions} questões atingido")
                    break

                # Verificar erros consecutivos
                if self.stats["consecutive_errors"] >= settings.MAX_CONSECUTIVE_ERRORS:
                    pause = min(settings.LONG_PAUSE_SECONDS * 2, 600)
                    logger.warning(
                        f"⚠️ {self.stats['consecutive_errors']} erros consecutivos. "
                        f"Pausando {pause}s..."
                    )
                    await asyncio.sleep(pause)
                    self.stats["consecutive_errors"] = 0

                # Buscar próxima questão para verificar
                try:
                    questao = await self.local_api.proxima_questao_verificar()
                except Exception as e:
                    logger.error(f"Erro ao buscar questão para verificar: {e}")
                    self.stats["consecutive_errors"] += 1
                    self.stats["errors"] += 1
                    await asyncio.sleep(5)
                    continue

                if not questao:
                    logger.info(
                        "✅ Todas as questões precisa_verificar foram re-classificadas!"
                    )
                    break

                self.stats["total_processed"] += 1

                # Processar
                try:
                    status = await self._process_question(questao)

                    if status == "found":
                        self.stats["found"] += 1
                        self.stats["consecutive_errors"] = 0
                    elif status == "api_error":
                        self.stats["errors"] += 1
                        self.stats["consecutive_errors"] += 1
                        self.stats["total_processed"] -= 1
                        logger.warning(
                            f"API instável — aguardando 30s... "
                            f"(erros: {self.stats['consecutive_errors']})"
                        )
                        await asyncio.sleep(30)
                        continue
                    else:
                        self.stats["not_found"] += 1
                        self.stats["consecutive_errors"] = 0

                except asyncio.CancelledError:
                    logger.info("Tarefa cancelada")
                    break
                except Exception as e:
                    logger.error(f"Erro ao processar Q#{questao['id']}: {e}")
                    self.stats["errors"] += 1
                    self.stats["consecutive_errors"] += 1

                # Delay aleatório
                delay = random.uniform(*delay_range)
                await asyncio.sleep(delay)

                # Log periódico (a cada 10 questões)
                if self.stats["total_processed"] % 10 == 0:
                    self._print_stats()

        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Interrompido pelo usuário")
        except Exception as e:
            logger.error(f"Erro inesperado no loop principal: {e}")
        finally:
            await self.stop()
