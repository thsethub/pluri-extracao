"""
Script para atualizar em LOTE todas as questões migradas com texto_base faltante.
"""

import sys
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import SessionLocal
from sqlalchemy import text
import time

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


def buscar_questoes_com_texto_base() -> List[Tuple]:
    """Busca todas as questões migradas que têm texto_base não incluído."""

    db = SessionLocal()

    query = text("""
        SELECT 
            rq.questao_id,
            rq.recurso_origem_chave as questao_id_trieduc,
            rq.questao_enunciado as enunciado_atual,
            tq.texto_base,
            tq.enunciado as enunciado_original,
            CHAR_LENGTH(tq.texto_base) as tamanho_texto_base
        FROM recursos_didaticos.rd_questoes rq
        INNER JOIN trieduc.questoes tq 
            ON tq.id = rq.recurso_origem_chave
        WHERE rq.recurso_origem_id = 6
          AND tq.texto_base IS NOT NULL
          AND CHAR_LENGTH(tq.texto_base) > 0
        ORDER BY CHAR_LENGTH(tq.texto_base) DESC
    """)

    result = db.execute(query)
    questoes = result.fetchall()
    db.close()

    return questoes


def atualizar_lote():
    """Atualiza todas as questões com texto_base em lote."""

    print("=" * 80)
    print("ATUALIZAÇÃO EM LOTE - INTEGRAR TEXTO_BASE")
    print("=" * 80)

    # Busca questões
    print("\n🔍 Buscando questões com texto_base não incluído...")
    questoes = buscar_questoes_com_texto_base()

    if not questoes:
        print("\n✅ Nenhuma questão encontrada para atualizar!")
        return

    print(f"\n📋 Encontradas {len(questoes)} questões para atualizar")

    # Mostra resumo
    print(
        f"\n{'ID RD':<10} {'ID Trieduc':<12} {'Enunciado Atual':<18} {'Texto Base':<15} {'Novo Total':<15}"
    )
    print("-" * 80)

    for q in questoes[:10]:  # Mostra primeiras 10
        enunciado_novo = integrar_texto_base(q.texto_base, q.enunciado_original)
        print(
            f"{q.questao_id:<10} {q.questao_id_trieduc:<12} {len(q.enunciado_atual):<18} {q.tamanho_texto_base:<15} {len(enunciado_novo):<15}"
        )

    if len(questoes) > 10:
        print(f"... e mais {len(questoes) - 10} questões")

    # Confirma
    print("\n" + "=" * 80)
    confirmacao = input(f"\n⚠️  Deseja atualizar {len(questoes)} questões? (sim/não): ")

    if confirmacao.lower() != "sim":
        print("\n❌ Operação cancelada pelo usuário.")
        return

    # Atualiza em lote
    print(f"\n🚀 Iniciando atualização de {len(questoes)} questões...")
    print("=" * 80)

    db = SessionLocal()

    sucesso = 0
    erros = 0
    chars_adicionados = 0

    update_query = text("""
        UPDATE recursos_didaticos.rd_questoes
        SET questao_enunciado = :novo_enunciado
        WHERE questao_id = :questao_id
    """)

    for i, q in enumerate(questoes, 1):
        try:
            # Processa imagens do texto_base (URL externa, base64, imagem_id) → upload S3
            texto_base_processado, imagens_s3 = processar_imagens_html(
                q.texto_base, q.questao_id, db, dry_run=False
            )
            if imagens_s3:
                print(
                    f"   📸 {len(imagens_s3)} imagem(ns) enviada(s) para S3 na questão {q.questao_id}"
                )

            # Gera novo enunciado com texto_base já com URLs S3
            enunciado_novo = integrar_texto_base(
                texto_base_processado, q.enunciado_original
            )

            # Atualiza no banco
            db.execute(
                update_query,
                {"novo_enunciado": enunciado_novo, "questao_id": q.questao_id},
            )

            db.commit()

            sucesso += 1
            chars_adicionados += len(enunciado_novo) - len(q.enunciado_atual)

            # Progresso
            if i % 10 == 0 or i == len(questoes):
                print(
                    f"   Processadas: {i}/{len(questoes)} | Sucesso: {sucesso} | Erros: {erros}"
                )

        except Exception as e:
            db.rollback()
            erros += 1
            print(f"\n   ❌ Erro ao atualizar questão ID {q.questao_id}: {e}")

        # Pequeno delay para não sobrecarregar o banco
        if i % 50 == 0:
            time.sleep(0.5)

    db.close()

    # Relatório final
    print("\n" + "=" * 80)
    print("RELATÓRIO FINAL")
    print("=" * 80)
    print(f"✅ Questões atualizadas com sucesso: {sucesso}")
    print(f"❌ Erros: {erros}")
    print(f"📊 Total de caracteres adicionados: {chars_adicionados:,}")
    print(
        f"📈 Média de caracteres por questão: {chars_adicionados // sucesso if sucesso > 0 else 0:,}"
    )

    if sucesso > 0:
        print(
            f"\n🎉 Atualização concluída! {sucesso} questões agora têm o texto_base integrado!"
        )


if __name__ == "__main__":
    try:
        atualizar_lote()
    except KeyboardInterrupt:
        print("\n\n⚠️  Operação interrompida pelo usuário.")
    except Exception as e:
        print(f"\n❌ Erro fatal: {e}")
        import traceback

        traceback.print_exc()
