"""
Ponto de entrada do Agente de Extração - Super Professor.

Usa chamadas HTTP diretas à API interna do SuperProfessor
(sem browser), com login via Playwright apenas para renovar token.

Uso:
    python main.py --run                         Inicia extração (todas as disciplinas)
    python main.py --run --disc 12               Apenas Matemática
    python main.py --run --disc 12 2 11          Matemática, Biologia e Português
    python main.py --run --max 100               Processar no máximo 100 questões
    python main.py --list-disciplinas            Lista disciplinas e progresso
    python main.py --stats                       Mostra estatísticas gerais
    python main.py --login                       Renova token JWT via browser
"""

import argparse
import asyncio
import sys

from rich.console import Console
from rich.table import Table

from src.config import settings
from src.local_api_client import LocalApiClient
from src.agent import ExtractionAgent
from src.token_manager import TokenManager

console = Console()


async def list_disciplinas():
    """Lista disciplinas disponíveis com stats de extração."""
    api = LocalApiClient()
    await api.start()

    disciplinas = await api.disciplinas()
    stats_list = await api.stats()

    await api.close()

    if not disciplinas:
        console.print(
            "[red]Erro: não foi possível buscar disciplinas. A API local está rodando?[/red]"
        )
        return

    stats_map = {}
    if stats_list:
        for s in stats_list:
            did = s.get("disciplina_id")
            if did:
                stats_map[did] = s

    table = Table(title="📚 Disciplinas Disponíveis")
    table.add_column("ID", style="cyan", justify="right")
    table.add_column("Nome", style="bold")
    table.add_column("Total", justify="right")
    table.add_column("Extraídas", justify="right", style="green")
    table.add_column("Imagem", justify="right", style="yellow")
    table.add_column("Pendentes", justify="right", style="red")

    for d in disciplinas:
        s = stats_map.get(d["id"], {})
        total = s.get("total_questoes", 0)
        extraidas = s.get("extraidas", 0)
        imagem = s.get("com_imagem", 0)
        pendentes = total - extraidas - imagem

        table.add_row(
            str(d["id"]),
            d["descricao"],
            str(total),
            str(extraidas),
            str(imagem),
            str(pendentes) if pendentes > 0 else "✅ 0",
        )

    console.print(table)


async def show_stats():
    """Mostra estatísticas detalhadas de extração."""
    api = LocalApiClient()
    await api.start()
    stats_data = await api.stats()
    await api.close()

    if not stats_data:
        console.print("[red]Erro: não foi possível buscar stats.[/red]")
        return

    valid = [s for s in stats_data if s.get("disciplina_id")]

    total_geral = sum(s.get("total_questoes", 0) for s in valid)
    total_extraidas = sum(s.get("extraidas", 0) for s in valid)
    total_imagem = sum(s.get("com_imagem", 0) for s in valid)

    console.print(f"\n[bold]📊 Progresso Geral[/bold]")
    console.print(f"  Total de questões: {total_geral:,}")
    console.print(f"  Extraídas: [green]{total_extraidas:,}[/green]")
    console.print(f"  Com imagem (puladas): [yellow]{total_imagem:,}[/yellow]")
    console.print(
        f"  Pendentes: [red]{total_geral - total_extraidas - total_imagem:,}[/red]"
    )

    if total_geral > 0:
        pct = ((total_extraidas + total_imagem) / total_geral) * 100
        console.print(f"  Progresso: {pct:.1f}%\n")


async def renew_token():
    """Renova o token JWT via login no browser."""
    tm = TokenManager(settings.STORAGE_DIR)

    if tm.is_valid:
        console.print(
            f"[green]Token atual ainda é válido (expira em {tm._expires_at})[/green]"
        )
        return

    console.print("[yellow]Renovando token via browser...[/yellow]")
    await tm.ensure_valid_token()

    if tm.is_valid:
        console.print(
            f"[green]Token renovado com sucesso! Expira em {tm._expires_at}[/green]"
        )
    else:
        console.print("[red]Falha ao renovar token[/red]")


async def run_agent(disc_ids: list[int] | None, max_questions: int, headless: bool):
    """Executa o agente de extração."""
    if headless:
        settings.HEADLESS = True

    agent = ExtractionAgent(disciplina_ids=disc_ids)
    await agent.run(max_questions=max_questions)


def main():
    parser = argparse.ArgumentParser(
        description="🤖 Agente de Extração de Classificações - Super Professor (API Direta)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  python main.py --run                       Extrai todas as disciplinas
  python main.py --run --disc 12             Apenas Matemática
  python main.py --run --disc 12 2 11        Matemática, Bio e Port
  python main.py --run --max 50              Processa no máximo 50 questões
  python main.py --list-disciplinas          Lista disciplinas e progresso
  python main.py --stats                     Estatísticas gerais
  python main.py --login                     Renova token JWT
        """,
    )

    parser.add_argument("--run", action="store_true", help="Inicia extração")
    parser.add_argument(
        "--disc", type=int, nargs="+", help="IDs das disciplinas (ex: 12 2 11)"
    )
    parser.add_argument(
        "--max", type=int, default=0, help="Máximo de questões (0=infinito)"
    )
    parser.add_argument(
        "--list-disciplinas", action="store_true", help="Lista disciplinas"
    )
    parser.add_argument("--stats", action="store_true", help="Mostra estatísticas")
    parser.add_argument(
        "--login", action="store_true", help="Renova token JWT via browser"
    )
    parser.add_argument("--headless", action="store_true", help="Browser sem interface")

    args = parser.parse_args()

    if args.list_disciplinas:
        asyncio.run(list_disciplinas())
    elif args.stats:
        asyncio.run(show_stats())
    elif args.login:
        asyncio.run(renew_token())
    elif args.run:
        asyncio.run(run_agent(args.disc, args.max, args.headless))
    else:
        parser.print_help()
        console.print("\n[yellow]💡 Use --run para iniciar a extração[/yellow]")


if __name__ == "__main__":
    main()
