"""
Script para atualizar enunciado de questão já migrada, integrando o texto_base.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import SessionLocal
from sqlalchemy import text

from migracao.imagens.html_parser import processar_imagens_html


def integrar_texto_base(texto_base: str, enunciado: str) -> str:
    """Integra o texto_base ao início do enunciado."""
    if not texto_base or not texto_base.strip():
        return enunciado

    texto_base_limpo = texto_base.strip()
    if not texto_base_limpo:
        return enunciado

    # Envolve texto_base em <p> e adiciona separador
    texto_base_formatado = f"<p>{texto_base_limpo}</p>"
    separador = "<hr/>"

    # Concatena: texto_base + separador + enunciado
    return f"{texto_base_formatado}{separador}{enunciado}"


def atualizar_questao_com_texto_base(questao_rd_id: int):
    """Atualiza enunciado de uma questão específica integrando o texto_base."""

    db = SessionLocal()

    print("=" * 80)
    print(f"ATUALIZAR QUESTÃO ID {questao_rd_id} COM TEXTO_BASE")
    print("=" * 80)

    try:
        # Busca dados da questão migrada
        query = text("""
            SELECT 
                rq.questao_id,
                rq.recurso_origem_chave,
                rq.questao_enunciado as enunciado_atual,
                tq.texto_base,
                tq.enunciado as enunciado_original
            FROM recursos_didaticos.rd_questoes rq
            INNER JOIN trieduc.questoes tq 
                ON tq.id = rq.recurso_origem_chave
            WHERE rq.questao_id = :questao_id
        """)

        result = db.execute(query, {"questao_id": questao_rd_id})
        questao = result.fetchone()

        if not questao:
            print(f"❌ Questão ID {questao_rd_id} não encontrada!")
            return

        print(f"\n✅ Questão encontrada:")
        print(f"   ID RD: {questao.questao_id}")
        print(f"   ID Trieduc: {questao.recurso_origem_chave}")
        print(f"   Enunciado atual: {len(questao.enunciado_atual)} caracteres")
        print(f"   Texto base: {len(questao.texto_base or '')} caracteres")

        if not questao.texto_base or not questao.texto_base.strip():
            print("\n⚠️ Esta questão não possui texto_base, nada a fazer.")
            return

        # Processa imagens do texto_base (URL externa, base64, imagem_id) → upload S3
        print("\n🖼️  Processando imagens do texto_base...")
        texto_base_processado, imagens_s3 = processar_imagens_html(
            questao.texto_base, questao.questao_id, db, dry_run=False
        )
        if imagens_s3:
            print(f"   ✅ {len(imagens_s3)} imagem(ns) enviada(s) para S3")
        else:
            print("   Sem imagens para processar no texto_base")

        # Integra texto_base (com URLs S3) ao enunciado original
        enunciado_novo = integrar_texto_base(
            texto_base_processado, questao.enunciado_original
        )

        print(f"\n📝 Novo enunciado gerado: {len(enunciado_novo)} caracteres")
        print(f"\nPrévia dos primeiros 500 caracteres:")
        print("-" * 80)
        print(enunciado_novo[:500])
        print("-" * 80)

        # Confirma antes de atualizar
        confirmacao = input("\n⚠️  Deseja ATUALIZAR a questão no banco? (sim/não): ")

        if confirmacao.lower() != "sim":
            print("\n❌ Operação cancelada pelo usuário.")
            return

        # Atualiza no banco
        update_query = text("""
            UPDATE recursos_didaticos.rd_questoes
            SET questao_enunciado = :novo_enunciado
            WHERE questao_id = :questao_id
        """)

        db.execute(
            update_query,
            {"novo_enunciado": enunciado_novo, "questao_id": questao_rd_id},
        )

        db.commit()

        print(f"\n✅ Questão ID {questao_rd_id} atualizada com sucesso!")
        print(f"   Enunciado anterior: {len(questao.enunciado_atual)} caracteres")
        print(f"   Enunciado novo: {len(enunciado_novo)} caracteres")
        print(
            f"   Diferença: +{len(enunciado_novo) - len(questao.enunciado_atual)} caracteres"
        )

    except Exception as e:
        db.rollback()
        print(f"\n❌ Erro ao atualizar questão: {e}")
        import traceback

        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python atualizar_enunciado_com_texto_base.py <questao_rd_id>")
        print("Exemplo: python atualizar_enunciado_com_texto_base.py 22902")
        sys.exit(1)

    questao_id = int(sys.argv[1])
    atualizar_questao_com_texto_base(questao_id)
