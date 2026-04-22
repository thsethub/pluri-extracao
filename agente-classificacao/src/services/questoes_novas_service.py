"""Serviço para gerenciar questões novas"""

from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Dict, Optional, List
from datetime import datetime
from loguru import logger
import json

from ..database.models_questoes_novas import (
    QuestaoNovaModel,
    AlternativaNovaModel,
    ClassificacaoNovaModel,
    ClassificacaoNovaHistoricoModel,
)
from ..config import settings


class QuestoesNovasService:
    """Serviço para gerenciar questões novas a classificar"""

    def __init__(self, db: Session):
        self.db = db

    def sincronizar_superpro(
        self, apenas_nao_classificadas: bool = True, limite: Optional[int] = None
    ) -> Dict[str, int]:
        """
        Sincronizar questões da base local superpro_db

        Estratégia:
        - Busca questões do superpro_db.questoes_extraidas
        - Insere questões que ainda não existem em questoes_novas
        - Copia alternativas correspondentes

        Args:
            apenas_nao_classificadas: Se True, sincroniza apenas questões não classificadas
            limite: Limite de questões a sincronizar (None = sem limite)

        Returns:
            Dict com estatísticas da sincronização
        """
        try:
            logger.info("Iniciando sincronização com superpro_db")

            resultado = {
                "total": 0,
                "adicionadas": 0,
                "atualizadas": 0,
                "alternativas_adicionadas": 0,
                "erros": 0,
            }

            # Query para buscar questões do superpro_db
            # Assumindo que já existe conexão configurada
            query_questoes = """
            SELECT 
                qe.sp_id,
                qe.disciplina_sp,
                qe.tipo_questao,
                qe.enunciado,
                qe.gabarito,
                qe.resolucao,
                qe.classif_sp_breadcrumb,
                qe.fonte_vestibular,
                qe.ano,
                qe.contem_imagem,
                qe.disciplinas_libro,
                qe.assuntos_libro,
                qe.assunto_sp
            FROM superpro_db.questoes_extraidas qe
            WHERE qe.sp_id NOT IN (SELECT sp_id FROM questoes_novas)
            """

            if limite:
                query_questoes += f" LIMIT {limite}"

            # Executar query
            conexao_superpro = self._get_superpro_connection()
            resultados = conexao_superpro.execute(text(query_questoes))
            questoes_para_sync = resultados.fetchall()

            logger.info(
                f"Encontradas {len(questoes_para_sync)} questões para sincronizar"
            )

            # Inserir questões novas
            for row in questoes_para_sync:
                try:
                    questao_nova = QuestaoNovaModel(
                        sp_id=row[0],
                        disciplina_sp=row[1],
                        tipo_questao=row[2],
                        enunciado=row[3],
                        gabarito=row[4],
                        resolucao=row[5],
                        classif_sp_breadcrumb=row[6],
                        fonte_vestibular=row[7],
                        ano=row[8],
                        contem_imagem=bool(row[9]),
                        disciplinas_libro=row[10],  # JSON
                        assuntos_libro=row[11],  # JSON
                        assunto_sp=row[12],
                        status="nao_classificada",
                    )

                    self.db.add(questao_nova)
                    self.db.flush()  # Flush para obter o ID gerado

                    resultado["adicionadas"] += 1
                    resultado["total"] += 1

                    logger.debug(f"Questão {row[0]} adicionada com sucesso")

                except Exception as e:
                    logger.error(f"Erro ao adicionar questão {row[0]}: {str(e)}")
                    resultado["erros"] += 1
                    self.db.rollback()
                    continue

            # Commit das questões
            try:
                self.db.commit()
            except Exception as e:
                logger.error(f"Erro ao fazer commit das questões: {str(e)}")
                self.db.rollback()
                resultado["erros"] += resultado["adicionadas"]
                resultado["adicionadas"] = 0

            # Sincronizar alternativas para todas as questões sincronizadas
            resultado["alternativas_adicionadas"] = self._sincronizar_alternativas()

            logger.info(f"Sincronização concluída: {resultado}")
            return resultado

        except Exception as e:
            logger.error(f"Erro crítico na sincronização: {str(e)}")
            raise

    def _sincronizar_alternativas(self) -> int:
        """
        Sincronizar alternativas das questões novas

        Returns:
            Número de alternativas sincronizadas
        """
        try:
            query_alternativas = """
            SELECT 
                alt.id,
                alt.sp_id,
                alt.letra,
                alt.texto
            FROM superpro_db.alternativas alt
            WHERE alt.sp_id IN (SELECT sp_id FROM questoes_novas)
            AND (alt.sp_id, alt.letra) NOT IN (
                SELECT qn.sp_id, an.letra 
                FROM questoes_novas qn
                JOIN alternativas_novas an ON an.questao_nova_id = qn.id
            )
            """

            conexao_superpro = self._get_superpro_connection()
            resultados = conexao_superpro.execute(text(query_alternativas))
            alternativas_para_sync = resultados.fetchall()

            logger.info(f"Sincronizando {len(alternativas_para_sync)} alternativas")

            adicionadas = 0
            for row in alternativas_para_sync:
                try:
                    sp_id, letra, texto = row[1], row[2], row[3]

                    # Buscar a questão nova correspondente
                    questao_nova = (
                        self.db.query(QuestaoNovaModel)
                        .filter(QuestaoNovaModel.sp_id == sp_id)
                        .first()
                    )

                    if not questao_nova:
                        logger.warning(
                            f"Questão {sp_id} não encontrada para alternativa"
                        )
                        continue

                    # Verificar se alternativa já existe
                    existe = (
                        self.db.query(AlternativaNovaModel)
                        .filter(
                            AlternativaNovaModel.questao_nova_id == questao_nova.id,
                            AlternativaNovaModel.letra == letra,
                        )
                        .first()
                    )

                    if existe:
                        continue

                    # Inserir alternativa
                    alternativa = AlternativaNovaModel(
                        questao_nova_id=questao_nova.id, letra=letra, texto=texto
                    )

                    self.db.add(alternativa)
                    adicionadas += 1

                except Exception as e:
                    logger.error(f"Erro ao sincronizar alternativa: {str(e)}")
                    continue

            # Commit
            self.db.commit()
            logger.info(f"{adicionadas} alternativas sincronizadas")
            return adicionadas

        except Exception as e:
            logger.error(f"Erro ao sincronizar alternativas: {str(e)}")
            self.db.rollback()
            return 0

    def _get_superpro_connection(self):
        """
        Obter conexão com superpro_db

        A configuração deve estar em settings.DATABASE_SUPERPRO_URL
        """
        # Aqui você precisará configurar a conexão com o banco superpro_db
        # Por enquanto, retorna uma conexão de exemplo

        # Opção 1: Usar a conexão atual (se estiver usando MySQL/MariaDB híbrido)
        # Opção 2: Criar uma engine separada

        # Para agora, vamos usar uma query que acessa diretamente
        # Você precisará configurar isto baseado na sua setup
        return self.db

    def obter_questoes_pendentes(
        self, pagina: int = 1, tamanho: int = 20, filtros: Optional[Dict] = None
    ) -> tuple:
        """
        Obter questões pendentes de classificação

        Args:
            pagina: Número da página
            tamanho: Tamanho da página
            filtros: Dicionário com filtros adicionais

        Returns:
            Tupla (questões, total)
        """
        try:
            query = self.db.query(QuestaoNovaModel).filter(
                QuestaoNovaModel.status == "nao_classificada"
            )

            # Aplicar filtros se fornecidos
            if filtros:
                if "disciplina" in filtros:
                    query = query.filter(
                        QuestaoNovaModel.disciplina_sp.ilike(
                            f"%{filtros['disciplina']}%"
                        )
                    )
                if "contem_imagem" in filtros:
                    query = query.filter(
                        QuestaoNovaModel.contem_imagem == filtros["contem_imagem"]
                    )

            total = query.count()

            # Paginação
            offset = (pagina - 1) * tamanho
            questoes = (
                query.order_by(QuestaoNovaModel.created_at.desc())
                .offset(offset)
                .limit(tamanho)
                .all()
            )

            return questoes, total

        except Exception as e:
            logger.error(f"Erro ao buscar questões pendentes: {str(e)}")
            raise

    def salvar_classificacao(
        self,
        questao_id: int,
        habilidades: List[int],
        disciplinas: List[int],
        justificativa: str,
        scores: Dict[int, float],
        usuario_id: int,
    ) -> ClassificacaoNovaModel:
        """
        Salvar classificação de uma questão

        Args:
            questao_id: ID da questão
            habilidades: Lista de IDs de habilidades
            disciplinas: Lista de IDs de disciplinas
            justificativa: Justificativa da classificação
            scores: Scores de confiança por disciplina
            usuario_id: ID do usuário que está classificando

        Returns:
            Classificação salva
        """
        try:
            # Buscar questão
            questao = (
                self.db.query(QuestaoNovaModel)
                .filter(QuestaoNovaModel.id == questao_id)
                .first()
            )

            if not questao:
                raise ValueError(f"Questão {questao_id} não encontrada")

            # Verificar se já existe classificação
            classificacao = (
                self.db.query(ClassificacaoNovaModel)
                .filter(ClassificacaoNovaModel.questao_nova_id == questao_id)
                .first()
            )

            if classificacao:
                # Guardar dados antigos para histórico
                dados_anterior = {
                    "habilidades": classificacao.habilidades_identificadas,
                    "disciplinas": classificacao.disciplinas_classificadas,
                    "justificativa": classificacao.justificativa,
                }

                # Atualizar
                classificacao.habilidades_identificadas = habilidades
                classificacao.disciplinas_classificadas = disciplinas
                classificacao.justificativa = justificativa
                classificacao.scores_confianca = scores

                # Registrar no histórico
                historico = ClassificacaoNovaHistoricoModel(
                    questao_nova_id=questao_id,
                    classificacao_nova_id=classificacao.id,
                    acao="atualizada",
                    dados_anterior=dados_anterior,
                    dados_novo={
                        "habilidades": habilidades,
                        "disciplinas": disciplinas,
                        "justificativa": justificativa,
                    },
                    alterado_por_id=usuario_id,
                )
                self.db.add(historico)
            else:
                # Criar nova
                classificacao = ClassificacaoNovaModel(
                    questao_nova_id=questao_id,
                    habilidades_identificadas=habilidades,
                    disciplinas_classificadas=disciplinas,
                    justificativa=justificativa,
                    scores_confianca=scores,
                    classificado_por_id=usuario_id,
                )
                self.db.add(classificacao)

                # Registrar no histórico
                historico = ClassificacaoNovaHistoricoModel(
                    questao_nova_id=questao_id,
                    acao="criada",
                    dados_novo={
                        "habilidades": habilidades,
                        "disciplinas": disciplinas,
                        "justificativa": justificativa,
                    },
                    alterado_por_id=usuario_id,
                )
                self.db.add(historico)

            # Atualizar questão
            questao.status = "classificada"
            questao.classificado_por_id = usuario_id
            questao.data_classificacao = datetime.utcnow()

            # Commit
            self.db.commit()
            self.db.refresh(classificacao)

            logger.info(
                f"Classificação salva: questão {questao_id}, usuário {usuario_id}"
            )
            return classificacao

        except Exception as e:
            self.db.rollback()
            logger.error(f"Erro ao salvar classificação: {str(e)}")
            raise

    def obter_estatisticas(self) -> Dict:
        """
        Obter estatísticas gerais de classificação

        Returns:
            Dict com estatísticas
        """
        try:
            total = self.db.query(QuestaoNovaModel).count()
            nao_classificadas = (
                self.db.query(QuestaoNovaModel)
                .filter(QuestaoNovaModel.status == "nao_classificada")
                .count()
            )
            classificadas = (
                self.db.query(QuestaoNovaModel)
                .filter(QuestaoNovaModel.status == "classificada")
                .count()
            )
            em_progresso = (
                self.db.query(QuestaoNovaModel)
                .filter(QuestaoNovaModel.status == "em_progresso")
                .count()
            )

            return {
                "total": total,
                "nao_classificadas": nao_classificadas,
                "classificadas": classificadas,
                "em_progresso": em_progresso,
                "percentual_concluido": (
                    (classificadas / total * 100) if total > 0 else 0
                ),
            }

        except Exception as e:
            logger.error(f"Erro ao calcular estatísticas: {str(e)}")
            raise
