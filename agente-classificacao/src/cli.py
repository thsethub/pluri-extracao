"""Script principal de classificação via console"""

import json
from pathlib import Path
from typing import List
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.progress import Progress, SpinnerColumn, TextColumn
from loguru import logger

from .config import settings
from .utils import setup_logger
from .models import Question
from .services import QuestionClassifier


console = Console()


def print_banner():
    """Exibe o banner da aplicação"""
    banner = """
╔═══════════════════════════════════════════════════════════╗
║    Agente de Classificação de Questões com IA            ║
║    Powered by OpenAI                                      ║
╚═══════════════════════════════════════════════════════════╝
    """
    console.print(banner, style="bold cyan")


def get_categories() -> List[str]:
    """Solicita as categorias de classificação ao usuário

    Returns:
        Lista de categorias
    """
    console.print("\n[bold]Configuração de Categorias[/bold]", style="yellow")
    console.print("Digite as categorias disponíveis (separadas por vírgula):")
    console.print("Exemplo: Matemática, Física, Química, Biologia\n")

    categories_input = Prompt.ask("Categorias")
    categories = [cat.strip() for cat in categories_input.split(",") if cat.strip()]

    if not categories:
        console.print("[red]Erro: Nenhuma categoria fornecida![/red]")
        return get_categories()

    console.print(
        f"\n[green]✓[/green] {len(categories)} categorias configuradas:", style="green"
    )
    for cat in categories:
        console.print(f"  • {cat}")

    return categories


def get_question_input() -> str:
    """Solicita a questão ao usuário

    Returns:
        Conteúdo da questão
    """
    console.print("\n[bold]Digite a questão a ser classificada:[/bold]", style="yellow")
    console.print("(Digite 'sair' para encerrar)\n")

    lines = []
    while True:
        line = input()
        if line.lower() == "sair":
            return ""
        if not line and lines:  # Linha vazia após conteúdo = fim
            break
        if line:
            lines.append(line)

    return "\n".join(lines)


def display_classification_result(
    question: Question, classification, categories: List[str]
):
    """Exibe o resultado da classificação

    Args:
        question: Questão classificada
        classification: Resultado da classificação
        categories: Categorias disponíveis
    """
    console.print("\n[bold green]✓ Classificação Concluída![/bold green]\n")

    # Tabela de informações
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Campo", style="cyan", width=20)
    table.add_column("Valor", style="white")

    table.add_row("ID da Questão", str(question.id))
    table.add_row("Categorias", ", ".join(classification.categories))
    table.add_row("Modelo Usado", classification.model_used)
    table.add_row("Tokens Usados", str(classification.tokens_used))
    table.add_row("Tempo (ms)", str(classification.processing_time_ms))

    console.print(table)

    # Scores de confiança
    if classification.confidence_scores:
        console.print("\n[bold]Scores de Confiança:[/bold]", style="yellow")
        for cat, score in classification.confidence_scores.items():
            bar_length = int(score * 20)
            bar = "█" * bar_length + "░" * (20 - bar_length)
            console.print(f"  {cat:.<30} [{bar}] {score:.2%}")

    # Raciocínio
    if classification.reasoning:
        console.print("\n[bold]Raciocínio:[/bold]", style="yellow")
        console.print(f"  {classification.reasoning}\n")


def save_results(question: Question, classification, output_file: Path):
    """Salva os resultados em arquivo JSON

    Args:
        question: Questão classificada
        classification: Resultado da classificação
        output_file: Caminho do arquivo de saída
    """
    data = {
        "question": question.model_dump(mode="json"),
        "classification": classification.model_dump(mode="json"),
    }

    # Cria diretório se não existir
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Salva
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    console.print(f"\n[green]✓[/green] Resultado salvo em: {output_file}")


def main():
    """Função principal"""
    # Setup
    setup_logger(settings.log_level)
    print_banner()

    # Verifica se a API key está configurada
    if not settings.openai_api_key:
        console.print(
            "[red]ERRO: OPENAI_API_KEY não configurada no arquivo .env![/red]"
        )
        console.print(
            "Configure sua chave da OpenAI no arquivo .env antes de continuar."
        )
        return

    # Configuração inicial
    console.print(f"\n[dim]Modelo: {settings.openai_model}[/dim]")
    console.print(f"[dim]Max Tokens: {settings.openai_max_tokens}[/dim]\n")

    # Obtém categorias
    categories = get_categories()

    # Inicializa o classificador
    classifier = QuestionClassifier()

    # Loop de classificação
    question_count = 0
    while True:
        # Obtém questão
        question_text = get_question_input()

        if not question_text:
            console.print("\n[yellow]Encerrando...[/yellow]")
            break

        # Cria objeto Question
        question = Question(content=question_text)
        question_count += 1

        try:
            # Classifica com spinner
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                progress.add_task(description="Classificando questão...", total=None)
                classification = classifier.classify(question, categories)

            # Exibe resultado
            display_classification_result(question, classification, categories)

            # Pergunta se quer salvar
            if Confirm.ask("\nDeseja salvar o resultado?", default=True):
                output_dir = Path("data/output")
                output_file = output_dir / f"classification_{question.id}.json"
                save_results(question, classification, output_file)

            # Continuar?
            console.print()
            if not Confirm.ask("Classificar outra questão?", default=True):
                break

        except Exception as e:
            logger.error(f"Erro ao classificar questão: {str(e)}")
            console.print(f"\n[red]✗ Erro: {str(e)}[/red]\n")

            if Confirm.ask("Tentar novamente com outra questão?", default=True):
                continue
            else:
                break

    # Resumo final
    console.print(f"\n[bold green]Sessão finalizada![/bold green]")
    console.print(f"Total de questões classificadas: {question_count}")


if __name__ == "__main__":
    main()
