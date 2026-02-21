from src.database import get_db, get_pg_db
from src.database.models import QuestaoModel
from src.database.pg_models import QuestaoAssuntoModel
from sqlalchemy import text, func

def archive_and_cleanup():
    db = next(get_db())
    pg_db = next(get_pg_db())
    
    archive_table = "questao_assuntos_sem_habilidade"
    
    print(f"Verificando se a tabela de arquivo '{archive_table}' existe...")
    # Cria a tabela de arquivo com o mesmo schema que a questao_assuntos se ela não existir
    pg_db.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {archive_table} (
            LIKE questao_assuntos INCLUDING ALL
        );
    """))
    pg_db.commit()
    
    print("Buscando registros no PostgreSQL...")
    pg_items = pg_db.query(QuestaoAssuntoModel.questao_id).all()
    pg_ids = [r[0] for r in pg_items]
    total_pg = len(pg_ids)
    print(f"Total de registros encontrados no PG: {total_pg}")
    
    if total_pg == 0:
        print("Nenhum registro encontrado.")
        return

    batch_size = 1000
    invalid_ids = []
    
    print("Cruzando dados com MySQL para identificar questões sem habilidade...")
    for i in range(0, total_pg, batch_size):
        batch = pg_ids[i:i+batch_size]
        # Pegamos os IDs que SÃO válidos no MySQL (têm habilidade)
        valid_in_mysql = [
            r[0] for r in db.query(QuestaoModel.id)
            .filter(QuestaoModel.id.in_(batch))
            .filter(QuestaoModel.habilidade_id.isnot(None))
            .all()
        ]
        valid_set = set(valid_in_mysql)
        # O que está no PG mas não é válido no MySQL deve ser arquivado
        batch_invalid = [qid for qid in batch if qid not in valid_set]
        invalid_ids.extend(batch_invalid)
        
    print(f"Total de registros para arquivar: {len(invalid_ids)}")
    
    if invalid_ids:
        print(f"Copiando {len(invalid_ids)} registros para a tabela '{archive_table}'...")
        # Copiar em blocos
        for i in range(0, len(invalid_ids), batch_size):
            sub_batch = invalid_ids[i:i+batch_size]
            
            # Usando raw SQL para mover os dados de uma tabela para a outra
            placeholders = ", ".join([str(id) for id in sub_batch])
            pg_db.execute(text(f"""
                INSERT INTO {archive_table} 
                SELECT * FROM questao_assuntos 
                WHERE questao_id IN ({placeholders})
                ON CONFLICT (questao_id) DO NOTHING;
            """))
            
            # Deletar da tabela principal
            pg_db.query(QuestaoAssuntoModel).filter(QuestaoAssuntoModel.questao_id.in_(sub_batch)).delete(synchronize_session=False)
        
        pg_db.commit()
        print(f"Sucesso! {len(invalid_ids)} registros movidos para '{archive_table}'.")
    else:
        print("Nenhum registro inválido encontrado.")

if __name__ == "__main__":
    archive_and_cleanup()
