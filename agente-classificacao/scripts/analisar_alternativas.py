import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import SessionLocal
from sqlalchemy import text

db = SessionLocal()

result = db.execute(text("""
    SELECT 
        cu.id as classificacao_id,
        cu.questao_id,
        COUNT(qa.id) as num_alternativas
    FROM thsethub.classificacao_usuario cu
    JOIN trieduc.questoes q ON cu.questao_id = q.id
    JOIN trieduc.disciplinas d ON q.disciplina_id = d.id
    LEFT JOIN trieduc.questao_alternativas qa ON q.id = qa.questao_id
    WHERE d.descricao LIKE '%História%'
      AND cu.tipo_acao = 'classificacao_nova'
      AND (cu.migrada IS NULL OR cu.migrada = FALSE)
      AND cu.modulos_escolhidos IS NOT NULL
      AND cu.modulos_escolhidos != '[]'
    GROUP BY cu.id, cu.questao_id
    LIMIT 10
"""))

questoes = result.fetchall()

print("\n" + "=" * 80)
print("ANÁLISE DE ALTERNATIVAS DAS QUESTÕES DE HISTÓRIA")
print("=" * 80)
print(f"{'Classificação ID':<20} {'Questão ID':<15} {'Nº Alternativas':<20}")
print("-" * 80)

for q in questoes:
    print(f"{q.classificacao_id:<20} {q.questao_id:<15} {q.num_alternativas:<20}")

print("=" * 80)

db.close()
