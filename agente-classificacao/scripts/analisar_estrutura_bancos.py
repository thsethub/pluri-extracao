"""
Script para analisar estruturas dos bancos e identificar mapeamentos necessários.
Não faz alterações, apenas leitura e análise.
"""

import sys
from pathlib import Path
from sqlalchemy import text, inspect

# Adiciona o diretório src ao sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import SessionLocal

def analisar_disciplinas():
    """Compara disciplinas entre trieduc e compartilhados"""
    db = SessionLocal()
    try:
        print("=" * 80)
        print("ANÁLISE: DISCIPLINAS")
        print("=" * 80)
        
        # Disciplinas do trieduc
        print("\n📚 DISCIPLINAS NO BANCO TRIEDUC:")
        result = db.execute(text("SELECT id, descricao FROM trieduc.disciplinas ORDER BY id"))
        disciplinas_trieduc = result.fetchall()
        
        for disc in disciplinas_trieduc:
            print(f"   ID {disc.id:3d}: {disc.descricao}")
        
        # Descobre estrutura da tabela disciplinas no compartilhados
        print("\n📚 ESTRUTURA DA TABELA compartilhados.disciplinas:")
        result = db.execute(text("DESCRIBE compartilhados.disciplinas"))
        colunas_comp = result.fetchall()
        
        for col in colunas_comp:
            print(f"   {col[0]:30s} {col[1]:20s} {col[2]:5s} {col[3]:5s}")
        
        # Busca disciplinas do compartilhados (ajustando para as colunas reais)
        print("\n📚 DISCIPLINAS NO BANCO COMPARTILHADOS:")
        # Tenta obter todas as colunas disponíveis
        colunas_disponiveis = [col[0] for col in colunas_comp]
        select_clause = ", ".join(colunas_disponiveis[:5])  # Primeiras 5 colunas
        
        result = db.execute(text(f"SELECT {select_clause} FROM compartilhados.disciplinas"))
        disciplinas_compartilhados = result.fetchall()
        
        for disc in disciplinas_compartilhados:
            print(f"   {disc}")
                    
    finally:
        db.close()

def analisar_modulos_compartilhados():
    """Analisa estrutura de módulos no banco compartilhados"""
    db = SessionLocal()
    try:
        print("\n" + "=" * 80)
        print("ANÁLISE: MÓDULOS E ASSUNTOS (COMPARTILHADOS)")
        print("=" * 80)
        
        # Verifica quais tabelas existem relacionadas a módulos
        print("\n📋 TABELAS DISPONÍVEIS NO BANCO COMPARTILHADOS:")
        result = db.execute(text("SHOW TABLES FROM compartilhados"))
        tabelas = result.fetchall()
        
        print("   Todas as tabelas:")
        for t in tabelas:
            print(f"      • {t[0]}")
        
        tabelas_modulos = [t[0] for t in tabelas if 'modulo' in t[0].lower() or 'assunto' in t[0].lower()]
        if tabelas_modulos:
            print(f"\n   Tabelas relacionadas a módulos/assuntos:")
            for t in tabelas_modulos:
                print(f"      • {t}")
        
        # Para cada tabela relacionada, mostra estrutura
        for tabela in tabelas_modulos[:3]:  # Primeiras 3 tabelas
            print(f"\n📦 ESTRUTURA DA TABELA compartilhados.{tabela}:")
            result = db.execute(text(f"DESCRIBE compartilhados.{tabela}"))
            colunas = result.fetchall()
            for col in colunas:
                print(f"   {col[0]:30s} {col[1]:20s} {col[2]:5s} {col[3]:5s}")
            
            # Mostra sample
            print(f"\n   AMOSTRA DE DADOS (3 registros):")
            result = db.execute(text(f"SELECT * FROM compartilhados.{tabela} LIMIT 3"))
            for i, row in enumerate(result.fetchall(), 1):
                print(f"      Registro {i}: {row}")
                
    finally:
        db.close()

def analisar_recursos_didaticos():
    """Analisa estrutura do banco recursos_didaticos e tabela rd_questoes"""
    db = SessionLocal()
    try:
        print("\n" + "=" * 80)
        print("ANÁLISE: BANCO RECURSOS_DIDATICOS")
        print("=" * 80)
        
        # Verifica se o banco existe
        result = db.execute(text("SHOW DATABASES LIKE 'recursos_didaticos'"))
        if not result.fetchone():
            print("\n⚠️  BANCO 'recursos_didaticos' NÃO ENCONTRADO!")
            print("   Verifique o nome correto do banco de destino.")
            return
        
        # Lista tabelas do banco
        print("\n📋 TABELAS NO BANCO recursos_didaticos:")
        result = db.execute(text("SHOW TABLES FROM recursos_didaticos"))
        tabelas = result.fetchall()
        for t in tabelas:
            print(f"   • {t[0]}")
        
        # Analisa estrutura da tabela rd_questoes
        if ('rd_questoes',) in tabelas or any('questao' in t[0].lower() for t in tabelas):
            tabela_questoes = 'rd_questoes' if ('rd_questoes',) in tabelas else \
                              [t[0] for t in tabelas if 'questao' in t[0].lower()][0]
            
            print(f"\n📝 ESTRUTURA DA TABELA recursos_didaticos.{tabela_questoes}:")
            result = db.execute(text(f"DESCRIBE recursos_didaticos.{tabela_questoes}"))
            colunas = result.fetchall()
            
            print("   Coluna                        Tipo                 Null   Key")
            print("   " + "-" * 70)
            for col in colunas:
                print(f"   {col[0]:30s} {col[1]:20s} {col[2]:5s} {col[3]:5s}")
            
            # Verifica campos relacionados a disciplina, módulo e assunto
            print("\n🔍 CAMPOS IMPORTANTES IDENTIFICADOS:")
            campos_interesse = ['disciplina', 'modulo', 'assunto', 'habilidade']
            for col in colunas:
                for campo in campos_interesse:
                    if campo in col[0].lower():
                        print(f"   ✓ {col[0]:30s} → Tipo: {col[1]}")
            
            # Conta registros existentes
            result = db.execute(text(f"SELECT COUNT(*) FROM recursos_didaticos.{tabela_questoes}"))
            count = result.fetchone()[0]
            print(f"\n📊 Total de registros existentes: {count}")
            
            if count > 0:
                print("\n📝 AMOSTRA DE DADOS (primeiros 3 registros):")
                result = db.execute(text(f"SELECT * FROM recursos_didaticos.{tabela_questoes} LIMIT 3"))
                # Mostra apenas algumas colunas relevantes
                for i, row in enumerate(result.fetchall(), 1):
                    print(f"\n   Registro {i}:")
                    for j, col in enumerate(colunas[:10]):  # Primeiras 10 colunas
                        valor = row[j]
                        if valor and len(str(valor)) > 50:
                            valor = str(valor)[:50] + "..."
                        print(f"      {col[0]:25s}: {valor}")
        else:
            print("\n⚠️  TABELA 'rd_questoes' NÃO ENCONTRADA!")
            print("   Verifique o nome correto da tabela de destino.")
            
    finally:
        db.close()

def analisar_mapeamento_thsethub():
    """Analisa a tabela habilidade_modulos do thsethub que já tem o mapeamento"""
    db = SessionLocal()
    try:
        print("\n" + "=" * 80)
        print("ANÁLISE: MAPEAMENTO EXISTENTE (thsethub.habilidade_modulos)")
        print("=" * 80)
        
        print("\n📊 ESTRUTURA DA TABELA thsethub.habilidade_modulos:")
        result = db.execute(text("DESCRIBE thsethub.habilidade_modulos"))
        for col in result.fetchall():
            print(f"   {col[0]:30s} {col[1]:20s}")
        
        print("\n📝 AMOSTRA DE MAPEAMENTOS (5 registros):")
        result = db.execute(text("""
            SELECT id, habilidade_id, habilidade_descricao, 
                   disciplina, modulo, descricao 
            FROM thsethub.habilidade_modulos 
            LIMIT 5
        """))
        
        for row in result.fetchall():
            print(f"\n   ID: {row[0]}")
            print(f"   Habilidade TriEduc: {row[1]} - {row[2]}")
            print(f"   Disciplina: {row[3]}")
            print(f"   Módulo: {row[4]}")
            print(f"   Assunto: {row[5]}")
            
        # Agrupa por disciplina para ver quantos módulos temos mapeados
        print("\n📊 MÓDULOS MAPEADOS POR DISCIPLINA:")
        result = db.execute(text("""
            SELECT disciplina, COUNT(DISTINCT modulo) as total_modulos
            FROM thsethub.habilidade_modulos
            GROUP BY disciplina
            ORDER BY disciplina
        """))
        
        for row in result.fetchall():
            print(f"   {row[0]:25s}: {row[1]:3d} módulos")
            
    finally:
        db.close()

if __name__ == "__main__":
    try:
        print("\n🔍 INICIANDO ANÁLISE DAS ESTRUTURAS DOS BANCOS...")
        print("   (Somente leitura, sem alterações)")
        
        analisar_disciplinas()
        analisar_modulos_compartilhados()
        analisar_mapeamento_thsethub()
        analisar_recursos_didaticos()
        
        print("\n" + "=" * 80)
        print("✅ ANÁLISE CONCLUÍDA")
        print("=" * 80)
        
    except Exception as e:
        print(f"\n❌ ERRO na análise: {e}")
        import traceback
        traceback.print_exc()
