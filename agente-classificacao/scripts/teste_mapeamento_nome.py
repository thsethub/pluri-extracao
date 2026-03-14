"""
Script de teste para mapear assuntos usando BUSCA POR NOME.

FLUXO CORRETO:
1. classificacao_usuario → modulos_escolhidos (NOME), descricoes_assunto_list (NOME)
2. Buscar compartilhados.disciplinas_modulos por NOME do módulo
3. Com disc_modu_id correto, buscar compartilhados.assuntos
4. Filtrar assuntos por NOME (similaridade)
"""

import sys
from pathlib import Path
from sqlalchemy import text
from difflib import SequenceMatcher

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import SessionLocal

def similaridade_texto(a: str, b: str) -> float:
    """Calcula similaridade entre dois textos (0.0 a 1.0)"""
    a_clean = a.lower().replace('[rm]', '').replace('[', '').replace(']', '').strip()
    b_clean = b.lower().replace('[rm]', '').replace('[', '').replace(']', '').strip()
    return SequenceMatcher(None, a_clean, b_clean).ratio()

def mapear_assuntos_por_nome(classificacao_id: int = 13):
    """
    Mapeia assuntos usando BUSCA POR NOME (não por ID).
    """
    db = SessionLocal()
    try:
        print("=" * 80)
        print(f"MAPEAMENTO POR NOME (classificacao_id={classificacao_id})")
        print("=" * 80)
        
        # 1. Busca dados da classificação
        print("\n📋 PASSO 1: Buscar dados de classificacao_usuario")
        result = db.execute(text("""
            SELECT id, questao_id, modulos_escolhidos, descricoes_assunto_list
            FROM thsethub.classificacao_usuario
            WHERE id = :id
        """), {"id": classificacao_id})
        
        classificacao = result.fetchone()
        if not classificacao:
            print(f"❌ Classificação {classificacao_id} não encontrada")
            return
        
        import json
        modulos_escolhidos = json.loads(classificacao.modulos_escolhidos) if classificacao.modulos_escolhidos else []
        descricoes_assunto = json.loads(classificacao.descricoes_assunto_list) if classificacao.descricoes_assunto_list else []
        
        print(f"   ✓ Questão ID: {classificacao.questao_id}")
        print(f"   ✓ Módulos escolhidos (NOMES): {modulos_escolhidos}")
        print(f"   ✓ Descrições de assunto (NOMES): {descricoes_assunto}")
        
        if not modulos_escolhidos:
            print("\n⚠️  ATENÇÃO: Não há módulos escolhidos.")
            return
        
        todos_assuntos_mapeados = []
        
        # 2. Para cada módulo escolhido, BUSCAR POR NOME em compartilhados
        for idx, nome_modulo in enumerate(modulos_escolhidos):
            print(f"\n{'='*80}")
            print(f"📦 MÓDULO {idx+1}: '{nome_modulo}'")
            print(f"{'='*80}")
            
            # Busca módulo em compartilhados.disciplinas_modulos por NOME
            print(f"\n   🔍 PASSO 2: Buscar módulo em compartilhados.disciplinas_modulos")
            print(f"      Buscando: '%{nome_modulo}%' (SEM prefixo [RM])")
            
            result = db.execute(text("""
                SELECT disc_modu_id, disc_modu_descricao, disc_id
                FROM compartilhados.disciplinas_modulos
                WHERE disc_modu_descricao LIKE :nome
                  AND disc_modu_descricao NOT LIKE '[RM]%'
                  AND disc_modu_descricao NOT LIKE '% [RM]%'
            """), {"nome": f"%{nome_modulo}%"})
            
            modulos_encontrados = result.fetchall()
            
            if not modulos_encontrados:
                print(f"      ❌ Nenhum módulo encontrado com nome similar a '{nome_modulo}'")
                
                # Tenta busca mais flexível (removendo palavras comuns)
                palavras_chave = nome_modulo.replace('de', '').replace('da', '').replace('do', '').strip()
                print(f"      🔄 Tentando busca mais ampla: '%{palavras_chave}%' (SEM prefixo [RM])")
                
                result = db.execute(text("""
                    SELECT disc_modu_id, disc_modu_descricao, disc_id
                    FROM compartilhados.disciplinas_modulos
                    WHERE disc_modu_descricao LIKE :nome
                      AND disc_modu_descricao NOT LIKE '[RM]%'
                      AND disc_modu_descricao NOT LIKE '% [RM]%'
                """), {"nome": f"%{palavras_chave}%"})
                
                modulos_encontrados = result.fetchall()
                
                if not modulos_encontrados:
                    print(f"      ❌ Ainda assim, nenhum módulo encontrado")
                    continue
            
            print(f"      ✓ {len(modulos_encontrados)} módulo(s) encontrado(s):")
            for mod in modulos_encontrados:
                similaridade = similaridade_texto(nome_modulo, mod.disc_modu_descricao)
                print(f"        • disc_modu_id={mod.disc_modu_id:4d} disc_id={mod.disc_id:2d} ({similaridade:.0%} similar): {mod.disc_modu_descricao}")
            
            # 3. Buscar assuntos para TODOS os módulos encontrados
            print(f"\n   📋 PASSO 3: Buscar assuntos para CADA módulo encontrado")
            
            melhor_modulo = None
            assuntos_disponiveis = []
            
            # Ordena por similaridade
            modulos_ordenados = sorted(modulos_encontrados, 
                                      key=lambda m: similaridade_texto(nome_modulo, m.disc_modu_descricao),
                                      reverse=True)
            
            for mod in modulos_ordenados:
                print(f"\n      Verificando disc_modu_id={mod.disc_modu_id} ({mod.disc_modu_descricao})...")
                
                result = db.execute(text("""
                    SELECT assu_id, assu_descricao, disc_modu_id
                    FROM compartilhados.assuntos
                    WHERE disc_modu_id = :disc_modu_id
                """), {"disc_modu_id": mod.disc_modu_id})
                
                assuntos_mod = result.fetchall()
                
                if assuntos_mod:
                    print(f"      ✓ {len(assuntos_mod)} assunto(s) encontrado(s)!")
                    melhor_modulo = mod
                    assuntos_disponiveis = assuntos_mod
                    break  # Usa o primeiro que tem assuntos
                else:
                    print(f"      ✗ Sem assuntos")
            
            if not melhor_modulo or not assuntos_disponiveis:
                print(f"\n      ❌ Nenhum dos {len(modulos_encontrados)} módulos tem assuntos cadastrados!")
                continue
            
            similaridade_mod = similaridade_texto(nome_modulo, melhor_modulo.disc_modu_descricao)
            print(f"\n      ✅ MÓDULO SELECIONADO ({similaridade_mod:.0%} similar):")
            print(f"         disc_modu_id = {melhor_modulo.disc_modu_id}")
            print(f"         disc_modu_descricao = {melhor_modulo.disc_modu_descricao}")
            print(f"         disc_id = {melhor_modulo.disc_id}")
            print(f"         Total de assuntos: {len(assuntos_disponiveis)}")
            
            # 4. Match por nome com descricoes_assunto_list
            if idx < len(descricoes_assunto):
                nome_assunto_escolhido = descricoes_assunto[idx]
                print(f"\n   🔍 PASSO 4: Match por nome do assunto")
                print(f"      Assunto escolhido pelo usuário: '{nome_assunto_escolhido}'")
                
                # Calcula similaridade
                matches = []
                for assu in assuntos_disponiveis:
                    similaridade = similaridade_texto(nome_assunto_escolhido, assu.assu_descricao)
                    matches.append({
                        "assu_id": assu.assu_id,
                        "assu_descricao": assu.assu_descricao,
                        "similaridade": similaridade
                    })
                
                matches.sort(key=lambda x: x["similaridade"], reverse=True)
                
                print(f"\n      Similaridades:")
                for match in matches:
                    print(f"        {match['similaridade']:.0%} - assu_id={match['assu_id']:4d}: {match['assu_descricao']}")
                
                # Estratégia de seleção
                if matches[0]["similaridade"] >= 0.5:
                    print(f"\n      ✅ MATCH ENCONTRADO ({matches[0]['similaridade']:.0%}):")
                    print(f"         assu_id = {matches[0]['assu_id']}")
                    print(f"         assu_descricao = {matches[0]['assu_descricao']}")
                    
                    todos_assuntos_mapeados.append({
                        "assu_id": matches[0]["assu_id"],
                        "assu_descricao": matches[0]["assu_descricao"],
                        "disc_modu_id": melhor_modulo.disc_modu_id,
                        "disc_modu_descricao": melhor_modulo.disc_modu_descricao,
                        "disc_id": melhor_modulo.disc_id,
                        "similaridade": matches[0]["similaridade"]
                    })
                else:
                    print(f"\n      ⚠️  MATCH FRACO (< 50%): Usando TODOS os assuntos do módulo")
                    for match in matches:
                        todos_assuntos_mapeados.append({
                            "assu_id": match["assu_id"],
                            "assu_descricao": match["assu_descricao"],
                            "disc_modu_id": melhor_modulo.disc_modu_id,
                            "disc_modu_descricao": melhor_modulo.disc_modu_descricao,
                            "disc_id": melhor_modulo.disc_id,
                            "similaridade": match["similaridade"]
                        })
            else:
                print(f"\n      ℹ️  Sem descrição de assunto específica, usando TODOS")
                for assu in assuntos_disponiveis:
                    todos_assuntos_mapeados.append({
                        "assu_id": assu.assu_id,
                        "assu_descricao": assu.assu_descricao,
                        "disc_modu_id": melhor_modulo.disc_modu_id,
                        "disc_modu_descricao": melhor_modulo.disc_modu_descricao,
                        "disc_id": melhor_modulo.disc_id,
                        "similaridade": 1.0
                    })
        
        # 5. Resultado Final
        print("\n" + "=" * 80)
        print("✅ RESULTADO FINAL")
        print("=" * 80)
        
        if not todos_assuntos_mapeados:
            print("\n❌ Nenhum assunto foi mapeado!")
            return
        
        print(f"\n📊 Total de {len(todos_assuntos_mapeados)} assunto(s) mapeado(s):\n")
        for i, assunto in enumerate(todos_assuntos_mapeados, 1):
            print(f"{i}. assu_id={assunto['assu_id']:4d} - {assunto['assu_descricao']}")
            print(f"   Módulo: disc_modu_id={assunto['disc_modu_id']:4d} - {assunto['disc_modu_descricao']}")
            print(f"   Disciplina: disc_id={assunto['disc_id']}")
            print(f"   Similaridade: {assunto['similaridade']:.0%}")
            print()
        
        # SQL de exemplo
        disc_id = todos_assuntos_mapeados[0]["disc_id"]
        print(f"\n-- SQL para inserir em rd_questoes_assuntos:")
        for assunto in todos_assuntos_mapeados:
            print(f"-- INSERT INTO recursos_didaticos.rd_questoes_assuntos (questao_id, assu_id)")
            print(f"-- VALUES (@novo_questao_id, {assunto['assu_id']});")
        
    finally:
        db.close()

if __name__ == "__main__":
    print("\n🔬 TESTE DE MAPEAMENTO POR NOME - 5 QUESTÕES\n")
    print("=" * 80)
    print("IMPORTANTE: Busca módulos e assuntos por NOME, não por ID!")
    print("=" * 80)
    
    # Busca 5 classificações para testar
    db = SessionLocal()
    try:
        print("\n📋 Buscando 5 classificações de exemplo...\n")
        result = db.execute(text("""
            SELECT id, questao_id, modulos_escolhidos, descricoes_assunto_list
            FROM thsethub.classificacao_usuario
            WHERE modulos_escolhidos IS NOT NULL 
              AND modulos_escolhidos != '[]'
            ORDER BY id
            LIMIT 5
        """))
        
        classificacoes = result.fetchall()
        
        if not classificacoes:
            print("❌ Nenhuma classificação encontrada!")
        else:
            import json
            print(f"✓ {len(classificacoes)} classificações encontradas:\n")
            for c in classificacoes:
                modulos = json.loads(c.modulos_escolhidos) if c.modulos_escolhidos else []
                print(f"  • ID {c.id:4d} - Questão {c.questao_id:5d} - Módulos: {modulos}")
            
            print("\n" + "=" * 80)
            
            # Testa cada uma
            for i, c in enumerate(classificacoes, 1):
                print(f"\n\n{'#' * 80}")
                print(f"# TESTE {i}/5 - Classificação ID {c.id}")
                print(f"{'#' * 80}\n")
                
                mapear_assuntos_por_nome(classificacao_id=c.id)
                
                if i < len(classificacoes):
                    print("\n" + "─" * 80)
    finally:
        db.close()
    
    print("\n" + "=" * 80)
    print("✅ TODOS OS TESTES CONCLUÍDOS")
    print("=" * 80)
