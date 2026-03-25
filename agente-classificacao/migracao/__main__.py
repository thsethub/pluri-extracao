import sys
import argparse

from .constants import QUESTOES_QUIMICA_ID
from .lote import buscar_classificacoes_para_migrar, migrar_questoes_em_lote
from .migrador import migrar_questao_completa


def main():
    parser = argparse.ArgumentParser(
        description="Migra questões do trieduc para recursos_didaticos",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos de uso:

  # Migrar UMA questão específica (DRY-RUN)
  python -m migracao --id 13

  # Migrar UMA questão em PRODUÇÃO
  python -m migracao --id 13 --producao

  # Migrar TODAS as classificações novas (DRY-RUN)
  python -m migracao --tipo-acao classificacao_nova

  # Migrar apenas Matemática (DRY-RUN)
  python -m migracao --disciplina Matemática

  # Migrar correções de Português em PRODUÇÃO
  python -m migracao --tipo-acao correcao --disciplina Português --producao

  # Limitar a 50 questões
  python -m migracao --tipo-acao classificacao_nova --limite 50
        """,
    )

    parser.add_argument("--id", type=int, help="ID específico de classificacao_usuario")
    parser.add_argument(
        "--tipo-acao",
        choices=["classificacao_nova", "correcao", "verificacao"],
        help="Filtrar por tipo de ação",
    )
    parser.add_argument("--disciplina", type=str, help="Filtrar por nome da disciplina")
    parser.add_argument(
        "--limite",
        type=str,
        default="100",
        help="Máximo de questões a migrar (padrão: 100, use None para sem limite)",
    )
    parser.add_argument(
        "--producao",
        action="store_true",
        help="Executar em modo PRODUÇÃO (insere no banco)",
    )
    parser.add_argument(
        "--excluir-lista-quimica",
        action="store_true",
        help="Excluir questões da lista QUESTOES_QUIMICA_ID (questoes já migradas/separadas)",
    )

    args = parser.parse_args()

    dry_run = not args.producao

    # Processa limite
    if args.limite.lower() == "none":
        limite = None
    else:
        try:
            limite = int(args.limite)
        except ValueError:
            parser.error(
                f"--limite deve ser um número ou 'None', recebido: {args.limite}"
            )

    # Validação
    if not args.id and not args.tipo_acao and not args.disciplina:
        parser.error("Você deve especificar --id OU --tipo-acao OU --disciplina")

    # Modo PRODUÇÃO - confirmação
    if not dry_run:
        print("\n ATENÇÃO: Modo PRODUÇÃO ativado! Os dados SERÃO inseridos no banco!")
        resposta = input("Deseja continuar? (sim/não): ")
        if resposta.lower() != "sim":
            print("Cancelado.")
            sys.exit(0)

    # Migração de UMA questão específica
    if args.id:
        print(f"\n Modo: Migração de UMA questão (ID {args.id})")
        migrar_questao_completa(args.id, dry_run=dry_run)

    # Migração EM LOTE
    else:
        print(f"\n Modo: Migração EM LOTE")
        excluir_ids = QUESTOES_QUIMICA_ID if args.excluir_lista_quimica else None
        classificacao_ids = buscar_classificacoes_para_migrar(
            tipo_acao=args.tipo_acao,
            disciplina=args.disciplina,
            limite=limite,
            excluir_questao_ids=excluir_ids,
        )

        if not classificacao_ids:
            print("\n Nenhuma classificação encontrada com os filtros especificados.")
            sys.exit(0)

        migrar_questoes_em_lote(classificacao_ids, dry_run=dry_run)


if __name__ == "__main__":
    main()
