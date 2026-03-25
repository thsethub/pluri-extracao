import csv
import io
import json
import re
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import create_engine

from ..config import settings
from .importacao_sql_schemas import ImportacaoSqlResponse


router = APIRouter(prefix="/importacao", tags=["Importação SQL"])
_bearer_scheme = HTTPBearer(auto_error=False)
_IDENT_REGEX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INT_REGEX = re.compile(r"^-?\d+$")
_FLOAT_REGEX = re.compile(r"^-?\d+\.\d+$")


def _validar_token_n8n(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> str:
    configured_token = (getattr(settings, "n8n_bearer_token", "") or "").strip()
    if not configured_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Serviço sem token Bearer configurado (n8n_bearer_token).",
        )

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Header Authorization Bearer é obrigatório.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    received_token = credentials.credentials.strip()
    if not secrets.compare_digest(received_token, configured_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token Bearer inválido.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return received_token


def _sql_ident(value: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned or not _IDENT_REGEX.match(cleaned):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Identificador SQL inválido: {value}",
        )
    return f"`{cleaned}`"


def _sanitize_filename(value: str) -> str:
    stem = Path(value).stem
    sanitized = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")
    return sanitized or "arquivo"


def _to_sql_literal(raw_value: Optional[str]) -> str:
    if raw_value is None:
        return "NULL"

    value = str(raw_value).strip()
    if value == "":
        return "NULL"

    if _INT_REGEX.match(value) or _FLOAT_REGEX.match(value):
        return value

    escaped = value.replace("\\", "\\\\").replace("'", "''").replace("\x00", "")
    return f"'{escaped}'"


def _to_python_value(raw_value: Optional[str]):
    if raw_value is None:
        return None
    value = str(raw_value).strip()
    if value == "":
        return None
    if _INT_REGEX.match(value):
        try:
            return int(value)
        except ValueError:
            return value
    if _FLOAT_REGEX.match(value):
        try:
            return float(value)
        except ValueError:
            return value
    return value


def _flush_insert_chunk(sql_file, table_ident: str, columns_sql: str, rows_chunk: list[str]) -> None:
    if not rows_chunk:
        return
    sql_file.write(f"INSERT INTO {table_ident} ({columns_sql}) VALUES\n")
    sql_file.write(",\n".join(rows_chunk))
    sql_file.write(";\n")
    rows_chunk.clear()


@router.post(
    "/binario-para-sql",
    response_model=ImportacaoSqlResponse,
    summary="📥 Receber binário e gerar SQL MySQL",
)
async def binario_para_sql_mysql(
    arquivo: UploadFile = File(..., description="Arquivo CSV enviado em binário pelo n8n."),
    tabela_destino: str = Form(..., description="Tabela MySQL de destino."),
    banco_destino: Optional[str] = Form(None, description="Banco (schema) MySQL de destino."),
    delimitador: str = Form(";", description="Delimitador do CSV (ex: ; ou ,)."),
    encoding: str = Form("utf-8-sig", description="Encoding do arquivo CSV."),
    chunk_size: int = Form(2000, description="Linhas por INSERT (batch)."),
    incluir_truncate: bool = Form(False, description="Se true, adiciona TRUNCATE no início."),
    gerar_json_intermediario: bool = Form(
        False,
        description="Se true, também salva JSON intermediário em disco.",
    ),
    executar_no_mysql: bool = Form(
        True,
        description="Se true, executa inserção em MySQL durante o processamento.",
    ),
    _: str = Depends(_validar_token_n8n),
):
    inicio = time.perf_counter()

    if len(delimitador) != 1:
        raise HTTPException(status_code=400, detail="Delimitador precisa ter 1 caractere.")
    if chunk_size < 1 or chunk_size > 20000:
        raise HTTPException(status_code=400, detail="chunk_size deve estar entre 1 e 20000.")

    table_ident = _sql_ident(tabela_destino)
    db_ident = _sql_ident(banco_destino).strip("`") if banco_destino else None
    table_ref = f"`{db_ident}`.{table_ident}" if db_ident else table_ident

    base_output_dir = Path(__file__).resolve().parents[2] / "data" / "output"
    base_output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = _sanitize_filename(arquivo.filename or "input")
    output_sql = base_output_dir / f"{safe_name}_{timestamp}.sql"
    output_json = base_output_dir / f"{safe_name}_{timestamp}.json" if gerar_json_intermediario else None

    linhas_processadas = 0
    linhas_inseridas_mysql = 0
    colunas: list[str] = []
    rows_chunk: list[str] = []
    values_chunk_mysql: list[tuple] = []

    json_file = None
    first_json_item = True
    conn = None
    cursor = None

    try:
        arquivo.file.seek(0)
        text_stream = io.TextIOWrapper(arquivo.file, encoding=encoding, newline="")
        reader = csv.DictReader(text_stream, delimiter=delimitador)

        if not reader.fieldnames:
            raise HTTPException(status_code=400, detail="CSV sem cabeçalho.")

        colunas = [str(name).strip() for name in reader.fieldnames]
        if any(not col for col in colunas):
            raise HTTPException(status_code=400, detail="CSV com nome de coluna vazio.")

        seen = set()
        duplicates = {col for col in colunas if col in seen or seen.add(col)}
        if duplicates:
            raise HTTPException(
                status_code=400,
                detail=f"CSV com colunas duplicadas: {', '.join(sorted(duplicates))}",
            )

        columns_sql = ", ".join(_sql_ident(col) for col in colunas)

        if output_json:
            json_file = output_json.open("w", encoding="utf-8")
            json_file.write("[\n")

        if executar_no_mysql:
            engine = create_engine(settings.database_url, pool_pre_ping=True, pool_recycle=3600, echo=False)
            conn = engine.raw_connection()
            cursor = conn.cursor()

            if banco_destino:
                cursor.execute(f"USE {_sql_ident(banco_destino)}")

        with output_sql.open("w", encoding="utf-8", newline="\n") as sql_file:
            sql_file.write("-- SQL gerado automaticamente a partir de CSV binário (n8n)\n")
            sql_file.write(f"-- Tabela: {tabela_destino}\n")
            sql_file.write(f"-- Gerado em: {datetime.now().isoformat()}\n\n")

            if incluir_truncate:
                sql_file.write(f"TRUNCATE TABLE {table_ref};\n\n")
                if cursor is not None:
                    cursor.execute(f"TRUNCATE TABLE {table_ref}")

            mysql_insert_sql = None
            if cursor is not None:
                mysql_placeholders = ", ".join(["%s"] * len(colunas))
                mysql_insert_sql = f"INSERT INTO {table_ref} ({columns_sql}) VALUES ({mysql_placeholders})"

            for row in reader:
                linhas_processadas += 1

                if output_json and json_file is not None:
                    if not first_json_item:
                        json_file.write(",\n")
                    json.dump(row, json_file, ensure_ascii=False)
                    first_json_item = False

                values_sql = ", ".join(_to_sql_literal(row.get(col)) for col in colunas)
                rows_chunk.append(f"({values_sql})")

                if cursor is not None and mysql_insert_sql is not None:
                    values_chunk_mysql.append(tuple(_to_python_value(row.get(col)) for col in colunas))

                if len(rows_chunk) >= chunk_size:
                    _flush_insert_chunk(sql_file, table_ref, columns_sql, rows_chunk)
                    if cursor is not None and mysql_insert_sql is not None and values_chunk_mysql:
                        cursor.executemany(mysql_insert_sql, values_chunk_mysql)
                        conn.commit()
                        linhas_inseridas_mysql += len(values_chunk_mysql)
                        values_chunk_mysql.clear()

            _flush_insert_chunk(sql_file, table_ref, columns_sql, rows_chunk)
            if cursor is not None and mysql_insert_sql is not None and values_chunk_mysql:
                cursor.executemany(mysql_insert_sql, values_chunk_mysql)
                conn.commit()
                linhas_inseridas_mysql += len(values_chunk_mysql)
                values_chunk_mysql.clear()

        text_stream.detach()

        if output_json and json_file is not None:
            json_file.write("\n]\n")

    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Erro de decoding do arquivo. Verifique o encoding ({encoding}).",
        ) from exc
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao processar arquivo: {exc}",
        ) from exc
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()
        if json_file is not None:
            json_file.close()
        await arquivo.close()

    duracao = round(time.perf_counter() - inicio, 3)

    return ImportacaoSqlResponse(
        mensagem="Arquivo processado com sucesso.",
        tabela_destino=tabela_destino,
        banco_destino=banco_destino,
        arquivo_sql=str(output_sql),
        arquivo_json=str(output_json) if output_json else None,
        linhas_processadas=linhas_processadas,
        colunas=colunas,
        delimitador=delimitador,
        chunk_size=chunk_size,
        executado_no_mysql=executar_no_mysql,
        linhas_inseridas_mysql=linhas_inseridas_mysql,
        duracao_segundos=duracao,
    )
