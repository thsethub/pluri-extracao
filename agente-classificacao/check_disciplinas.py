from sqlalchemy import create_engine, text
import sys

pg_url = "postgresql://pluri:pluri123@100.102.111.64:5435/pluri_assuntos"

try:
    engine = create_engine(pg_url)
    with engine.connect() as conn:
        print("Connected.")
        # Re-check actual table name might be habilidade_modulos
        result = conn.execute(text("SELECT DISTINCT disciplina FROM habilidade_modulos ORDER BY disciplina"))
        disciplines = [row[0] for row in result]
        print("Distinct Disciplines in habilidade_modulos:")
        for d in disciplines:
            print(f"- {d}")
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
