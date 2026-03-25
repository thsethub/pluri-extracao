"""
Script para identificar questões migradas que possuem texto_base não incluído.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import SessionLocal
from sqlalchemy import text

def analisar_texto_base_faltante():
    """Identifica questões migradas com texto_base que não foi incluído."""
    
    db = SessionLocal()
    
    print("=" * 80)
    print("ANÁLISE: Questões migradas com texto_base faltante")
    print("=" * 80)
    
    # Query para encontrar questões migradas que têm texto_base no origem
    query = text("""
        SELECT 
            rq.questao_id,
            rq.recurso_origem_chave as questao_id_trieduc,
            CHAR_LENGTH(tq.texto_base) as tamanho_texto_base,
            CASE 
                WHEN tq.texto_base LIKE '%<img%' THEN 'Com imagens'
                ELSE 'Sem imagens'
            END as possui_imagens,
            cu.id as classificacao_id,
            tq.enunciado as enunciado_original
        FROM recursos_didaticos.rd_questoes rq
        INNER JOIN trieduc.questoes tq 
            ON tq.id = rq.recurso_origem_chave
        LEFT JOIN thsethub.classificacao_usuario cu
            ON cu.questao_id = tq.id
        WHERE rq.recurso_origem_id = 6
          AND tq.texto_base IS NOT NULL
          AND CHAR_LENGTH(tq.texto_base) > 0
        ORDER BY CHAR_LENGTH(tq.texto_base) DESC
        LIMIT 20
    """)
    
    result = db.execute(query)
    questoes = result.fetchall()
    
    if not questoes:
        print("\n❌ Nenhuma questão migrada encontrada com texto_base")
        return
    
    print(f"\n✅ Encontradas {len(questoes)} questões migradas com texto_base\n")
    
    # Exibe resumo
    print(f"{'ID RD':<10} {'ID Trieduc':<12} {'Classificação':<15} {'Tamanho':<10} {'Imagens':<15}")
    print("-" * 80)
    
    ids_classificacao = []
    
    for q in questoes:
        questao_id = q.questao_id
        questao_id_trieduc = q.questao_id_trieduc
        classificacao_id = q.classificacao_id or "N/A"
        tamanho = q.tamanho_texto_base
        possui_img = q.possui_imagens
        
        print(f"{questao_id:<10} {questao_id_trieduc:<12} {str(classificacao_id):<15} {tamanho:<10} {possui_img:<15}")
        
        if q.classificacao_id:
            ids_classificacao.append(q.classificacao_id)
    
    # Mostra detalhes de algumas questões
    print("\n" + "=" * 80)
    print("DETALHES DAS PRIMEIRAS 3 QUESTÕES")
    print("=" * 80)
    
    for i, q in enumerate(questoes[:3], 1):
        print(f"\n{'='*80}")
        print(f"Questão {i}")
        print(f"{'='*80}")
        
        # Busca detalhes completos
        detail_query = text("""
            SELECT 
                tq.id,
                tq.texto_base,
                tq.enunciado,
                d.descricao as disciplina
            FROM trieduc.questoes tq
            LEFT JOIN trieduc.disciplinas d ON d.id = tq.disciplina_id
            WHERE tq.id = :id
        """)
        
        detail_result = db.execute(detail_query, {"id": q.questao_id_trieduc})
        detail = detail_result.fetchone()
        
        if detail:
            print(f"ID Trieduc: {detail.id}")
            print(f"Disciplina: {detail.disciplina}")
            print(f"\nTexto Base ({len(detail.texto_base)} chars):")
            print("-" * 80)
            print(detail.texto_base[:500] + ("..." if len(detail.texto_base) > 500 else ""))
            print(f"\nEnunciado ({len(detail.enunciado)} chars):")
            print("-" * 80)
            print(detail.enunciado[:300] + ("..." if len(detail.enunciado) > 300 else ""))
    
    # Comando para testar
    if ids_classificacao:
        print("\n" + "=" * 80)
        print("COMANDOS PARA TESTAR")
        print("=" * 80)
        print("\nPara testar a migração com texto_base integrado, execute:\n")
        for cid in ids_classificacao[:5]:
            print(f"python scripts/migrar_questao_completa.py --id {cid}")
    
    db.close()

if __name__ == "__main__":
    try:
        analisar_texto_base_faltante()
    except Exception as e:
        print(f"\n❌ Erro: {e}")
        import traceback
        traceback.print_exc()
