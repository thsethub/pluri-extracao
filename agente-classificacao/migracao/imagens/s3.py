from typing import Dict, Tuple

import boto3

from src.config import settings

from ..utils import gerar_hash_timestamp_thread
from .deteccao import detectar_tipo_imagem_por_bytes
from .processamento import extrair_metadados_imagem, redimensionar_imagem

s3_client = boto3.client(
    "s3",
    aws_access_key_id=settings.aws_access_key_id,
    aws_secret_access_key=settings.aws_secret_access_key,
    region_name=settings.aws_region,
)

S3_BUCKET = settings.aws_s3_bucket
S3_FOLDER_HIGH = "sem_chamada/imagens_questoes"  # Alta resolução
S3_FOLDER_LOW = "sem_chamada/imagens_questoes/low"  # Baixa resolução


def upload_imagem_s3_duplo(
    imagem_bytes: bytes, questao_id: int, indice: int, dry_run: bool = True
) -> Tuple[str, str, Dict]:
    """
    Faz upload de imagem em ALTA e BAIXA resolução para S3.

    Returns:
        (url_alta, url_baixa, metadados_alta)
    """
    # Detecta formato
    formato = detectar_tipo_imagem_por_bytes(imagem_bytes)

    # Gera hash único baseado em timestamp + thread ID
    hash_unico = gerar_hash_timestamp_thread()
    nome_arquivo = f"trieduc-{hash_unico}.{formato}"

    # ALTA RESOLUÇÃO
    s3_key_high = f"{S3_FOLDER_HIGH}/{nome_arquivo}"
    url_alta = f"https://{S3_BUCKET}.s3.amazonaws.com/{s3_key_high}"
    metadados_alta = extrair_metadados_imagem(imagem_bytes)

    if not dry_run:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key_high,
            Body=imagem_bytes,
            ContentType=f"image/{formato}",
        )
        print(f"      Upload ALTA: {nome_arquivo}")
    else:
        print(f"      [DRY-RUN] Upload ALTA: {nome_arquivo}")

    # BAIXA RESOLUÇÃO (50%)
    imagem_baixa, _, _ = redimensionar_imagem(imagem_bytes, percentual=0.5)
    s3_key_low = f"{S3_FOLDER_LOW}/{nome_arquivo}"
    url_baixa = f"https://{S3_BUCKET}.s3.amazonaws.com/{s3_key_low}"

    if not dry_run:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key_low,
            Body=imagem_baixa,
            ContentType=f"image/{formato}",
        )
        print(f"      Upload BAIXA: {nome_arquivo}")
    else:
        print(f"      [DRY-RUN] Upload BAIXA: {nome_arquivo}")

    return url_alta, url_baixa, metadados_alta
