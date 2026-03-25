from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def _resolver_saida(input_csv: Path, output_json: str | None) -> Path:
    base_dir = Path(__file__).resolve().parent.parent

    if output_json:
        output_path = Path(output_json)
        if not output_path.is_absolute():
            output_path = (Path.cwd() / output_path).resolve()
        return output_path

    return base_dir / "data" / f"{input_csv.stem}.json"


def _converter_csv_para_json(
    input_csv: Path,
    output_json: Path,
    encoding: str,
    delimiter: str,
    ensure_ascii: bool,
) -> dict[str, Any]:
    if not input_csv.exists():
        raise FileNotFoundError(f"Arquivo CSV não encontrado: {input_csv}")

    if len(delimiter) != 1:
        raise ValueError("O delimitador deve ter exatamente 1 caractere.")

    with input_csv.open("r", encoding=encoding, newline="") as fp:
        reader = csv.DictReader(fp, delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError("CSV sem cabeçalho. Informe um arquivo com colunas na primeira linha.")
        registros = list(reader)

    output_json.parent.mkdir(parents=True, exist_ok=True)

    with output_json.open("w", encoding="utf-8") as fp:
        json.dump(registros, fp, ensure_ascii=ensure_ascii, indent=2)

    return {
        "input_csv": str(input_csv),
        "output_json": str(output_json),
        "total_registros": len(registros),
        "colunas": reader.fieldnames,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Converte uma base CSV para JSON e salva em data/ por padrão."
    )
    parser.add_argument(
        "input_csv",
        help="Caminho do arquivo CSV de entrada.",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Caminho do JSON de saída. Se omitido, salva em data/<nome_do_csv>.json.",
    )
    parser.add_argument(
        "--encoding",
        default="utf-8-sig",
        help="Encoding do CSV de entrada (padrão: utf-8-sig).",
    )
    parser.add_argument(
        "--delimiter",
        default=",",
        help="Delimitador do CSV (padrão: ,).",
    )
    parser.add_argument(
        "--ensure-ascii",
        action="store_true",
        help="Se informado, escapa caracteres não-ASCII no JSON.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    input_csv = Path(args.input_csv)
    if not input_csv.is_absolute():
        input_csv = (Path.cwd() / input_csv).resolve()

    output_json = _resolver_saida(input_csv, args.output_json)

    resultado = _converter_csv_para_json(
        input_csv=input_csv,
        output_json=output_json,
        encoding=args.encoding,
        delimiter=args.delimiter,
        ensure_ascii=args.ensure_ascii,
    )

    print("[OK] Conversão concluída")
    print(f"- CSV: {resultado['input_csv']}")
    print(f"- JSON: {resultado['output_json']}")
    print(f"- Registros: {resultado['total_registros']}")
    print(f"- Colunas: {', '.join(resultado['colunas'] or [])}")



if __name__ == "__main__":
    main()
