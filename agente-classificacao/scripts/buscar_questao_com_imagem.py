import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import SessionLocal
from sqlalchemy import text

db = SessionLocal()

result = db.execute(text("""
    SELECT cu.id, cu.questao_id 
    FROM thsethub.classificacao_usuario cu
    JOIN trieduc.questoes q ON cu.questao_id = q.id
    JOIN trieduc.disciplinas d ON q.disciplina_id = d.id
    WHERE d.descricao LIKE '%História%'
      AND cu.tipo_acao = 'classificacao_nova'
      AND (cu.migrada IS NULL OR cu.migrada = FALSE)
      AND cu.modulos_escolhidos IS NOT NULL
      AND cu.modulos_escolhidos != '[]'
      AND q.enunciado LIKE '%<img%'
    LIMIT 1
"""))

row = result.fetchone()
if row:
    print(f"Classificação ID: {row.id}, Questão ID: {row.questao_id}")
else:
    print("Nenhuma questão com imagem encontrada")

db.close()
