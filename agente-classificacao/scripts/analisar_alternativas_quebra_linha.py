import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import SessionLocal
from sqlalchemy import text

db = SessionLocal()

# Busca alternativas das questões migradas recentemente que terminam com <p><br></p> ou <p><br/></p>
result = db.execute(text("""
    SELECT 
        q.questao_id,
        qa.questao_alternativa_id,
        qa.questao_alternativa_prefixo,
        qa.questao_alternativa_texto
    FROM recursos_didaticos.rd_questoes q
    JOIN recursos_didaticos.rd_questoes_alternativas qa ON q.questao_id = qa.questao_id
    WHERE q.recurso_origem_id = 6
      AND (
          qa.questao_alternativa_texto LIKE '%<p><br></p>%'
          OR qa.questao_alternativa_texto LIKE '%<p><br/></p>%'
      )
    ORDER BY q.questao_id, qa.questao_alternativa_prefixo
"""))

alternativas_com_quebra = result.fetchall()

print("\n" + "=" * 100)
print("ANÁLISE DE ALTERNATIVAS COM QUEBRA DE LINHA NO FINAL")
print("=" * 100)
print(f"Total de alternativas encontradas: {len(alternativas_com_quebra)}")
print("=" * 100)

if alternativas_com_quebra:
    print(f"\n{'Questão ID':<15} {'Alt ID':<10} {'Prefixo':<10} {'Texto (primeiros 100 chars)':<60}")
    print("-" * 100)
    
    for alt in alternativas_com_quebra:
        texto_preview = alt.questao_alternativa_texto[:100].replace('\n', ' ')
        print(f"{alt.questao_id:<15} {alt.questao_alternativa_id:<10} {alt.questao_alternativa_prefixo:<10} {texto_preview:<60}")
    
    print("\n" + "-" * 100)
    print(f"\nIDs das alternativas afetadas (total: {len(alternativas_com_quebra)}):")
    ids = [str(alt.questao_alternativa_id) for alt in alternativas_com_quebra]
    print(", ".join(ids))
    
    print("\n" + "-" * 100)
    print(f"\nQuestões únicas afetadas:")
    questoes_unicas = set([alt.questao_id for alt in alternativas_com_quebra])
    print(f"Total: {len(questoes_unicas)} questões")
    print("IDs: " + ", ".join([str(qid) for qid in sorted(questoes_unicas)]))

print("\n" + "=" * 100)

db.close()
