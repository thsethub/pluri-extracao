"""
Script temporário para listar todas as disciplinas e tipos de ação disponíveis
em thsethub.classificacao_usuario com entradas pendentes de migração.
"""
import sys
sys.path.insert(0, '.')

from src.config.settings import settings
from sqlalchemy import create_engine, text

engine = create_engine(settings.database_url)

with engine.connect() as db:
    result = db.execute(text("""
        SELECT 
            d.descricao           AS disciplina,
            cu.tipo_acao,
            COUNT(*)              AS total,
            SUM(CASE WHEN cu.migrada = TRUE THEN 1 ELSE 0 END)  AS migradas,
            SUM(CASE WHEN cu.migrada IS NULL OR cu.migrada = FALSE THEN 1 ELSE 0 END) AS pendentes
        FROM thsethub.classificacao_usuario cu
        JOIN trieduc.questoes q    ON cu.questao_id = q.id
        JOIN trieduc.disciplinas d ON q.disciplina_id = d.id
        WHERE cu.modulos_escolhidos IS NOT NULL
          AND cu.modulos_escolhidos != '[]'
        GROUP BY d.descricao, cu.tipo_acao
        ORDER BY d.descricao, cu.tipo_acao
    """))
    rows = result.fetchall()

print(f"{'Disciplina':<30} | {'tipo_acao':<20} | {'total':>6} | {'migradas':>8} | {'pendentes':>9}")
print("-" * 85)
for r in rows:
    print(f"{r.disciplina:<30} | {r.tipo_acao:<20} | {r.total:>6} | {int(r.migradas):>8} | {int(r.pendentes):>9}")
