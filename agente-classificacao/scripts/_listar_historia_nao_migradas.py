"""
Script temporário para listar questões de História/classificacao_nova ainda não migradas.
"""
import sys
import csv
sys.path.insert(0, '.')

from src.config.settings import settings
from sqlalchemy import create_engine, text

engine = create_engine(settings.database_url)

with engine.connect() as db:
    result = db.execute(text("""
        SELECT 
            cu.id                 AS classificacao_id,
            cu.questao_id,
            d.descricao           AS disciplina,
            cu.tipo_acao,
            cu.migrada_desenvolvimento,
            cu.migrada_producao,
            (
                SELECT COUNT(*)
                FROM trieduc.questao_alternativas qa
                WHERE qa.questao_id = cu.questao_id
            )                     AS num_alternativas
        FROM thsethub.classificacao_usuario cu
        JOIN trieduc.questoes q   ON cu.questao_id = q.id
        JOIN trieduc.disciplinas d ON q.disciplina_id = d.id
        WHERE cu.tipo_acao = 'classificacao_nova'
          AND d.descricao LIKE '%Historia%'
          AND cu.modulos_escolhidos IS NOT NULL
          AND cu.modulos_escolhidos != '[]'
          AND (cu.migrada_desenvolvimento IS NULL OR cu.migrada_desenvolvimento = FALSE)
        ORDER BY cu.id
    """))
    rows = result.fetchall()

print(f"Total questoes nao migradas (Historia / classificacao_nova): {len(rows)}")
print()

header = f"{'classificacao_id':>16} | {'questao_id':>10} | {'migrada_dev':>11} | {'migrada_prod':>12} | {'num_alt':>7} | disciplina"
print(header)
print("-" * len(header))

for r in rows:
    migrada_dev_val = str(r.migrada_desenvolvimento) if r.migrada_desenvolvimento is not None else "NULL"
    migrada_prod_val = str(r.migrada_producao) if r.migrada_producao is not None else "NULL"
    print(f"{r.classificacao_id:>16} | {r.questao_id:>10} | {migrada_dev_val:>11} | {migrada_prod_val:>12} | {int(r.num_alternativas):>7} | {r.disciplina}")

# Exporta CSV
csv_path = "data/output/_historia_classificacao_nova_nao_migradas.csv"
with open(csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["classificacao_id", "questao_id", "disciplina", "tipo_acao", "migrada_desenvolvimento", "migrada_producao", "num_alternativas"])
    for r in rows:
        writer.writerow([r.classificacao_id, r.questao_id, r.disciplina, r.tipo_acao,
                         r.migrada_desenvolvimento, r.migrada_producao, int(r.num_alternativas)])

print(f"\nCSV exportado: {csv_path}")

# Agrupa por num_alternativas
from collections import Counter
counter = Counter(int(r.num_alternativas) for r in rows)
print("\nDistribuicao por numero de alternativas:")
for num_alt, count in sorted(counter.items()):
    print(f"  {num_alt} alternativas: {count} questoes")
