import concurrent.futures
import time
from typing import List, Optional

from sqlalchemy import text

from src.database import SessionLocal

from .exceptions import DuplicataException
from .migrador import migrar_questao_completa


def buscar_classificacoes_para_migrar(
    tipo_acao: Optional[str] = None,
    disciplina: Optional[str] = None,
    limite: Optional[int] = 100,
    excluir_questao_ids: Optional[List[int]] = None,
) -> List[int]:
    """
    Busca classificações que precisam ser migradas com filtros opcionais.

    Args:
        tipo_acao: Filtro por tipo_acao (classificacao_nova, correcao, verificacao)
        disciplina: Filtro por nome da disciplina
        limite: Máximo de classificações a retornar (None para sem limite)
        excluir_questao_ids: Lista de questao_id (trieduc) a excluir da busca

    Returns:
        Lista de IDs de classificacao_usuario
    """
    db = SessionLocal()
    try:
        print("\n BUSCAR CLASSIFICAÇÕES PARA MIGRAR")
        print("=" * 80)

        # Monta query com JOIN se precisar filtrar por disciplina
        if disciplina:
            from_clause = """
                FROM thsethub.classificacao_usuario cu
                JOIN trieduc.questoes q ON cu.questao_id = q.id
                JOIN trieduc.disciplinas d ON q.disciplina_id = d.id
            """
        else:
            from_clause = "FROM thsethub.classificacao_usuario cu"

        # Monta WHERE clauses
        where_clauses = [
            "(cu.migrada IS NULL OR cu.migrada = FALSE)",
            "cu.modulos_escolhidos IS NOT NULL",
            "cu.modulos_escolhidos != '[]'",
        ]

        params = {}

        if tipo_acao:
            where_clauses.append("cu.tipo_acao = :tipo_acao")
            params["tipo_acao"] = tipo_acao
            print(f"   Filtro: tipo_acao = '{tipo_acao}'")

        if disciplina:
            where_clauses.append("d.descricao LIKE :disciplina")
            params["disciplina"] = f"%{disciplina}%"
            print(f"   Filtro: disciplina LIKE '%{disciplina}%'")

        if excluir_questao_ids:
            ids_str = ",".join(str(int(i)) for i in excluir_questao_ids)
            where_clauses.append(f"cu.questao_id NOT IN ({ids_str})")
            print(
                f"   Excluindo {len(excluir_questao_ids)} questões da lista QUESTOES_QUIMICA_ID"
            )

        where_sql = " AND ".join(where_clauses)

        # Monta query completa
        if disciplina:
            query = f"""
                SELECT cu.id, cu.questao_id, d.descricao as disciplina, cu.tipo_acao
                {from_clause}
                WHERE {where_sql}
                ORDER BY cu.id
                {'' if limite is None else 'LIMIT :limite'}
            """
        else:
            query = f"""
                SELECT cu.id, cu.questao_id, cu.tipo_acao
                {from_clause}
                WHERE {where_sql}
                ORDER BY cu.id
                {'' if limite is None else 'LIMIT :limite'}
            """

        if limite is not None:
            params["limite"] = limite

        result = db.execute(text(query), params)
        classificacoes = result.fetchall()

        print(f"\n   ✓ {len(classificacoes)} classificação(ões) encontrada(s)")

        if classificacoes:
            print("\n   Classificações encontradas:")
            for c in classificacoes[:10]:  # Mostra até 10
                if disciplina:
                    print(
                        f"       ID {c.id:5d} - Questão {c.questao_id:5d} - {c.disciplina:20s} - {c.tipo_acao}"
                    )
                else:
                    print(
                        f"       ID {c.id:5d} - Questão {c.questao_id:5d} - {c.tipo_acao}"
                    )

            if len(classificacoes) > 10:
                print(f"      ... e mais {len(classificacoes) - 10}")

        print("=" * 80)

        return [c.id for c in classificacoes]

    finally:
        db.close()


def migrar_questoes_em_lote(classificacao_ids: List[int], dry_run: bool = True):
    """
    Migra múltiplas questões em lote.

    Args:
        classificacao_ids: Lista de IDs de classificacao_usuario
        dry_run: Se True, não executa INSERTs
    """
    total = len(classificacao_ids)
    sucesso = 0
    falhas = 0
    duplicadas = 0
    puladas = 0
    resultados = []
    print("\n" + "=" * 80)
    print(
        f"{'DRY-RUN: ' if dry_run else ''}MIGRAÇÃO EM LOTE (PARALELO) - {total} questões"
    )
    print("=" * 80)

    def migrar_wrapper(classificacao_id):
        try:
            result = migrar_questao_completa(classificacao_id, dry_run=dry_run)
            # Se retornou None, foi pulada por não ter 5 alternativas
            if result is None:
                return (classificacao_id, "pulada", "Questão não possui 5 alternativas")
            return (classificacao_id, "sucesso", None)
        except DuplicataException:
            return (classificacao_id, "duplicata", None)
        except Exception as e:
            return (classificacao_id, "falha", str(e))

    max_workers = min(8, total)  # Ajuste conforme capacidade do servidor
    start_time = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_id = {
            executor.submit(migrar_wrapper, cid): cid for cid in classificacao_ids
        }
        for idx, future in enumerate(concurrent.futures.as_completed(future_to_id), 1):
            cid = future_to_id[future]
            print(f"\n{'#' * 80}")
            print(f"# {idx}/{total} - Classificação ID {cid}")
            print(f"{'#' * 80}")
            result = future.result()
            resultados.append(result)
            if result[1] == "sucesso":
                sucesso += 1
            elif result[1] == "duplicata":
                duplicadas += 1
                print(f"  Questão já migrada anteriormente (duplicata)")
            elif result[1] == "pulada":
                puladas += 1
                print(f"  PULADA: {result[2]}")
            elif result[1] == "falha":
                falhas += 1
                print(f" ERRO ao migrar: {result[2]}")
    end_time = time.time()
    tempo_total = end_time - start_time
    tempo_medio = tempo_total / total if total > 0 else 0

    # Relatório final
    print("\n" + "=" * 80)
    print(" RELATÓRIO FINAL DA MIGRAÇÃO (PARALELO)")
    print("=" * 80)
    print(f"Total processado: {total}")
    print(f" Sucesso: {sucesso}")
    print(f" Duplicadas (já migradas): {duplicadas}")
    print(f" Puladas (não possuem 5 alternativas): {puladas}")
    print(f" Falhas: {falhas}")
    print(
        f"Modo: {'DRY-RUN (não executado)' if dry_run else 'PRODUÇÃO (executado e commitado)'}"
    )
    print(f"Tempo total: {tempo_total:.2f} segundos")
    print(f"Tempo médio por questão: {tempo_medio:.2f} segundos")
    print("=" * 80)
