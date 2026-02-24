import os
import sys
import argparse
from sqlalchemy import create_engine, text, MetaData, Table
from sqlalchemy.orm import sessionmaker
from loguru import logger
from rich.console import Console
from rich.table import Table
from rich.progress import Progress

# Configurações de Banco (Mantenha do arquivo original ou use env)
MYSQL_URL = 'mysql+pymysql://thiago:ThiagoLibro!@estudoplay-desenvolvimento-dbs.ctpetlcg3bwb.us-east-1.rds.amazonaws.com:3306/trieduc'
PG_URL = 'postgresql://pluri:pluri123@100.102.111.64:5435/pluri_assuntos'

# Mapeamento de Disciplinas (MySQL -> Postgres)
MAP_DISCIPLINAS = {
    "Língua Portuguesa": "Lingua Portuguesa",
    "Artes": "Arte",
    "Biologia": "Biologia",
    "Física": "Física",
    "Geografia": "Geografia",
    "História": "História",
    "Matemática": "Matemática",
    "Química": "Química",
    "Filosofia": "Filosofia",
    "Sociologia": "Sociologia",
    "Educação Física": "Educação Física",
    "Língua Inglesa": "Inglês",
    "Espanhol": "Espanhol",
    "Literatura": "Literatura",
    "Redação": "Redação"
}

console = Console()

def get_unique_mappings(pg_engine):
    """Retorna um dicionário habilidade_id -> detalhes do módulo único."""
    with pg_engine.connect() as conn:
        # 1. Identificar habilidade_id que aparecem exatamente 1 vez
        query = text("""
            SELECT habilidade_id, COUNT(*) as qtd
            FROM habilidade_modulos
            WHERE habilidade_id IS NOT NULL
            GROUP BY habilidade_id
            HAVING COUNT(*) = 1
        """)
        unique_ids = [r[0] for r in conn.execute(query).all()]
        
        if not unique_ids:
            return {}

        # 2. Buscar detalhes desses módulos
        ids_str = ",".join(map(str, unique_ids))
        query_details = text(f"SELECT * FROM habilidade_modulos WHERE habilidade_id IN ({ids_str})")
        details = conn.execute(query_details).all()
        
        mapping = {}
        for d in details:
            mapping[d.habilidade_id] = {
                "id": d.id,
                "modulo": d.modulo,
                "disciplina": d.disciplina,
                "classificacao_trieduc": d.habilidade_descricao,
                "descricao_assunto": d.descricao
            }
        return mapping

def main():
    parser = argparse.ArgumentParser(description="Auto-classificação rigorosa de questões de módulo único.")
    parser.add_argument("--dry-run", action="store_true", help="Não executa alterações no banco.")
    parser.add_argument("--disciplina", type=str, help="Filtra por uma disciplina específica (nome no MySQL).")
    parser.add_argument("--limit", type=int, default=0, help="Limite de questões a processar.")
    args = parser.parse_args()

    logger.info("Iniciando processo de auto-classificação rigorosa...")
    
    pg_engine = create_engine(PG_URL)
    mysql_engine = create_engine(MYSQL_URL)

    # 1. Obter mapeamentos unívocos do Postgres
    mappings = get_unique_mappings(pg_engine)
    logger.info(f"Encontradas {len(mappings)} habilidades com mapeamento único (1:1) no Postgres.")

    # 2. Buscar questões EM no MySQL
    with mysql_engine.connect() as my_conn:
        query_q = text("""
            SELECT q.id, q.questao_id as uuid, q.habilidade_id, q.enunciado, d.descricao as disciplina_nome, q.disciplina_id
            FROM questoes q
            JOIN disciplinas d ON q.disciplina_id = d.id
            WHERE q.ano_id = 3 AND q.habilidade_id IS NOT NULL
        """)
        all_questions = my_conn.execute(query_q).all()
    
    logger.info(f"Total de questões EM com habilidade no MySQL: {len(all_questions)}")

    # 3. Filtragem e Validação
    to_classify = []
    stats = {"already_classified": 0, "no_unique_mapping": 0, "disciplina_mismatch": 0, "skipped_by_user": 0, "eligible": 0}
    
    # Cache do que já foi classificado/pulado no PG para performance
    with pg_engine.connect() as pg_conn:
        # 1. Da tabela de assuntos consolidada (inclui scraper e flag de manual)
        classified_assuntos = {r[0] for r in pg_conn.execute(text("SELECT questao_id FROM questao_assuntos WHERE classificado_manualmente = true OR (classificacoes IS NOT NULL AND classificacoes != '[]')")).all()}
        
        # 2. Da tabela de histórico de ações do usuário (Rigor extra: se existe registro aqui, não mexe)
        classified_manual = {r[0] for r in pg_conn.execute(text("SELECT DISTINCT questao_id FROM classificacao_usuario")).all()}
        
        # União de todas que já possuem algum tipo de "veredito"
        classified_ids = classified_assuntos.union(classified_manual)
        
        # 3. Questões que o usuário preferiu não tocar agora
        skipped_ids = {r[0] for r in pg_conn.execute(text("SELECT questao_id FROM questoes_puladas")).all()}
    
    logger.debug(f"Cache: {len(classified_ids)} classificadas, {len(skipped_ids)} puladas.")

    for q in all_questions:
        # Filtro de disciplina via argumento
        if args.disciplina and q.disciplina_nome != args.disciplina:
            continue
            
        # Limite
        if args.limit > 0 and len(to_classify) >= args.limit:
            break

        # Regra 1: Já classificada?
        if q.id in classified_ids:
            stats["already_classified"] += 1
            continue
            
        # Regra 2: Pulada pelo usuário?
        if q.id in skipped_ids:
            stats["skipped_by_user"] += 1
            continue

        # Regra 3: Possui mapeamento unívoco?
        mapping = mappings.get(q.habilidade_id)
        if not mapping:
            stats["no_unique_mapping"] += 1
            continue
            
        # Regra 4: Paridade de Disciplina (Rigor Extremo)
        # O nome no MySQL deve bater com o nome no Postgres (via MAP_DISCIPLINAS)
        expected_pg_disc = MAP_DISCIPLINAS.get(q.disciplina_nome)
        if expected_pg_disc != mapping["disciplina"]:
            stats["disciplina_mismatch"] += 1
            logger.warning(f"Q#{q.id}: Mismatch de disciplina. MySQL: '{q.disciplina_nome}' ({expected_pg_disc}) vs PG: '{mapping['disciplina']}'")
            continue

        # Se passou em tudo, é elegível
        to_classify.append({
            "mysql_item": q,
            "mapping": mapping
        })
        stats["eligible"] += 1

    # 4. Relatório e Amostragem
    console.print(f"\n[bold green]RESULTADOS DA ANÁLISE RIGOROSA:[/bold green]")
    console.print(f"  - [cyan]Elegíveis para Auto-Classificação:[/cyan] [bold]{stats['eligible']}[/bold]")
    console.print(f"  - [yellow]Já Classificadas (Manual/Scraper):[/yellow] {stats['already_classified']}")
    console.print(f"  - [yellow]Puladas pelo Usuário (Pendentes):[/yellow] {stats['skipped_by_user']}")
    console.print(f"  - [red]Sem Mapeamento Único (1:N):[/red] {stats['no_unique_mapping']}")
    console.print(f"  - [red]Mismatch de Disciplina:[/red] {stats['disciplina_mismatch']}")

    if not to_classify:
        logger.warning("Nenhuma questão elegível encontrada.")
        return

    # Amostragem (5 questões)
    sample_table = Table(title="Amostra de Classificações (Top 5)")
    sample_table.add_column("QID", justify="right")
    sample_table.add_column("Disciplina (MySQL)")
    sample_table.add_column("Módulo (PG)")
    sample_table.add_column("TRIEDUC Classif")

    for item in to_classify[:5]:
        sample_table.add_row(
            str(item["mysql_item"].id),
            item["mysql_item"].disciplina_nome,
            item["mapping"]["modulo"],
            item["mapping"]["classificacao_trieduc"]
        )
    console.print(sample_table)

    if args.dry_run:
        console.print("\n[bold yellow]MODO DRY-RUN: Nenhuma alteração foi feita.[/bold yellow]")
        return

    # 5. Execução (Transacional)
    confirm = input(f"\nConfirmar classificação de {len(to_classify)} questões? (s/N): ")
    if confirm.lower() != 's':
        logger.info("Operação cancelada.")
        return

    with Progress() as progress:
        task = progress.add_task("[cyan]Classificando...", total=len(to_classify))
        
        # Refletir tabelas para usar Core (mais robusto que raw SQL)
        metadata = MetaData()
        metadata.reflect(pg_engine)
        t_assuntos = metadata.tables['questao_assuntos']
        t_classif = metadata.tables['classificacao_usuario']

        with pg_engine.connect() as pg_conn:
            with pg_conn.begin(): # Transaction
                from sqlalchemy import select, and_, update, insert, func
                for item in to_classify:
                    q = item["mysql_item"]
                    m = item["mapping"]
                    
                    try:
                        # 5a. Upsert em questao_assuntos
                        stmt_check = select(t_assuntos.c.id).where(t_assuntos.c.questao_id == q.id)
                        exists_id = pg_conn.execute(stmt_check).scalar()
                        
                        if exists_id:
                            # Update
                            stmt_upd = update(t_assuntos).where(t_assuntos.c.questao_id == q.id).values(
                                classificado_manualmente=True,
                                extracao_feita=True,
                                disciplina_id=q.disciplina_id,
                                disciplina_nome=q.disciplina_nome,
                                precisa_verificar=False
                            )
                            pg_conn.execute(stmt_upd)
                        else:
                            # Insert
                            stmt_ins = insert(t_assuntos).values(
                                questao_id=q.id,
                                questao_id_str=str(q.uuid),
                                disciplina_id=q.disciplina_id,
                                disciplina_nome=q.disciplina_nome,
                                extracao_feita=True,
                                classificado_manualmente=True,
                                contem_imagem=False,
                                precisa_verificar=False,
                                created_at=func.now()
                            )
                            pg_conn.execute(stmt_ins)

                        # 5b. Inserir em classificacao_usuario (Histórico)
                        stmt_hist = insert(t_classif).values(
                            usuario_id=0,
                            questao_id=q.id,
                            habilidade_id=q.habilidade_id,
                            modulo_escolhido=m["modulo"],
                            classificacao_trieduc=m["classificacao_trieduc"],
                            descricao_assunto=m["descricao_assunto"],
                            habilidade_modulo_id=m["id"],
                            tipo_acao='auto_classificacao',
                            observacao=f"Auto-classificação (módulo único) para habilidade Trieduc {q.habilidade_id}",
                            created_at=func.now()
                        )
                        pg_conn.execute(stmt_hist)

                    except Exception as e:
                        logger.error(f"Erro ao processar QID {q.id}: {e}")
                        raise e
                    
                    progress.update(task, advance=1)

    logger.success(f"Successfully classified {len(to_classify)} questions.")

if __name__ == "__main__":
    main()
