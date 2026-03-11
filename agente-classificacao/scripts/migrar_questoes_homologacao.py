from re import A
import sys
from pathlib import Path

# Adiciona o diretório src ao sys.path para importar os módulos
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import PgSessionLocal
from src.database.pg_usuario_models import ClassificacaoUsuarioModel

def buscar_questoes(tipo_acao: str = None, limite: int = 100):

    #Abre um sessão com um banco MySQL
    db = PgSessionLocal()
    try:
        query = db.query(ClassificacaoUsuarioModel).filter(
            ClassificacaoUsuarioModel.migrada == False  # Somente registros não migrados
        )
        
        #Atualiza a query para filtrar por tipo de ação, se fornecido
        if tipo_acao:
            query = query.filter(ClassificacaoUsuarioModel.tipo_acao == tipo_acao)

        #Busca em ordem de criação de indexação
        registros = query.order_by(ClassificacaoUsuarioModel.id).limit(limite).all()

        return registros
    finally:
        #Fecha a sessão ao finalizar a consulta
        db.close()

if __name__ == "__main__":

    #Busca todos os registros não migrados e para qualquer tipo de acao
    todos = buscar_questoes()
    print (f"Total de registros encontrados para migração: {len(todos)}")

    #Busca apenas classifica

    