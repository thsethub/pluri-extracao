from src.database import get_db, get_pg_db
from src.database.models import QuestaoModel
from src.database.pg_models import QuestaoAssuntoModel
from sqlalchemy import func

def cleanup_invalid_records():
    db = next(get_db())
    pg_db = next(get_pg_db())
    
    print("Buscando registros no PostgreSQL...")
    pg_items = pg_db.query(QuestaoAssuntoModel.questao_id).all()
    pg_ids = [r[0] for r in pg_items]
    total_pg = len(pg_ids)
    print(f"Total de registros encontrados no PG: {total_pg}")
    
    if total_pg == 0:
        print("Nenhum registro encontrado para limpar.")
        return

    batch_size = 1000
    invalid_ids = []
    
    print("Cruzando dados com MySQL para identificar questões sem habilidade...")
    for i in range(0, total_pg, batch_size):
        batch = pg_ids[i:i+batch_size]
        # Pegamos os que SÃO válidos no MySQL (têm habilidade)
        valid_in_mysql = [
            r[0] for r in db.query(QuestaoModel.id)
            .filter(QuestaoModel.id.in_(batch))
            .filter(QuestaoModel.habilidade_id.isnot(None))
            .all()
        ]
        valid_set = set(valid_in_mysql)
        # O que está no PG mas não é válido no MySQL deve ser removido
        batch_invalid = [qid for qid in batch if qid not in valid_set]
        invalid_ids.extend(batch_invalid)
        
    print(f"Total de registros inválidos identificados: {len(invalid_ids)}")
    
    if invalid_ids:
        print(f"Removendo {len(invalid_ids)} registros do PostgreSQL...")
        # Deletar em blocos para não estourar limite do IN
        for i in range(0, len(invalid_ids), batch_size):
            sub_batch = invalid_ids[i:i+batch_size]
            pg_db.query(QuestaoAssuntoModel).filter(QuestaoAssuntoModel.questao_id.in_(sub_batch)).delete(synchronize_session=False)
        
        pg_db.commit()
        print("Limpeza concluída com sucesso!")
    else:
        print("Nenhum registro inválido encontrado.")

if __name__ == "__main__":
    cleanup_invalid_records()
