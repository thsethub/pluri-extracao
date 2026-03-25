import base64
from io import BytesIO
from typing import Dict, Optional, Tuple

import requests
from PIL import Image
from sqlalchemy import text

from .deteccao import detectar_tipo_src


def redimensionar_imagem(
    imagem_bytes: bytes, percentual: float = 0.5
) -> Tuple[bytes, int, int]:
    """
    Redimensiona imagem para percentual do tamanho original.

    Args:
        imagem_bytes: Bytes da imagem original
        percentual: Percentual do tamanho (0.5 = 50%)

    Returns:
        (bytes_redimensionados, nova_largura, nova_altura)
    """
    try:
        img = Image.open(BytesIO(imagem_bytes))

        # Calcula novas dimensões
        nova_largura = int(img.width * percentual)
        nova_altura = int(img.height * percentual)

        # Redimensiona
        img_resized = img.resize((nova_largura, nova_altura), Image.Resampling.LANCZOS)

        # Salva em bytes
        buffer = BytesIO()
        formato = img.format or "PNG"
        img_resized.save(buffer, format=formato)
        buffer.seek(0)

        return buffer.getvalue(), nova_largura, nova_altura
    except Exception as e:
        print(f"  Erro ao redimensionar imagem: {e}")
        # Em caso de erro, retorna a imagem original reduzida manualmente
        return imagem_bytes, 0, 0


def extrair_metadados_imagem(imagem_bytes: bytes) -> Dict:
    """Extrai metadados da imagem usando PIL"""
    try:
        img = Image.open(BytesIO(imagem_bytes))

        return {
            "largura": img.width,
            "altura": img.height,
            "tamanho_bytes": len(imagem_bytes),
            "tamanho_kb": round(len(imagem_bytes) / 1024, 2),
            "formato": img.format or "UNKNOWN",
        }
    except Exception as e:
        return {
            "largura": 0,
            "altura": 0,
            "tamanho_bytes": len(imagem_bytes),
            "tamanho_kb": round(len(imagem_bytes) / 1024, 2),
            "formato": "UNKNOWN",
        }


def baixar_imagem_url(url: str) -> Optional[bytes]:
    """
    Baixa imagem de uma URL externa.

    Returns:
        bytes da imagem ou None se falhar
    """
    try:
        print(f"       Baixando de URL: {url[:80]}...")
        response = requests.get(url, timeout=30)
        response.raise_for_status()

        imagem_bytes = response.content
        print(f"       Baixado: {len(imagem_bytes)} bytes")
        return imagem_bytes
    except Exception as e:
        print(f"        Erro ao baixar URL: {e}")
        return None


def decodificar_base64_imagem(base64_str: str) -> Optional[bytes]:
    """
    Decodifica imagem em Base64.

    Args:
        base64_str: String Base64 (pode incluir data:image/png;base64,...)

    Returns:
        bytes da imagem ou None se falhar
    """
    try:
        # Remove prefixo data:image/...;base64, se existir
        if "base64," in base64_str:
            base64_str = base64_str.split("base64,")[1]

        imagem_bytes = base64.b64decode(base64_str)
        print(f"       Base64 decodificado: {len(imagem_bytes)} bytes")
        return imagem_bytes
    except Exception as e:
        print(f"        Erro ao decodificar Base64: {e}")
        return None


def obter_bytes_imagem(src: str, db) -> Optional[bytes]:
    """
    Obtém os bytes da imagem independente do formato do src.

    Args:
        src: Atributo src da tag <img>
        db: Sessão do banco de dados

    Returns:
        bytes da imagem ou None se falhar
    """
    tipo = detectar_tipo_src(src)

    if tipo == "url":
        return baixar_imagem_url(src)

    elif tipo == "base64":
        return decodificar_base64_imagem(src)

    elif tipo == "imagem_id":
        # Extrai ID da imagem
        if "imagem_id=" in src:
            imagem_id = src.split("imagem_id=")[1].split("&")[0]
        else:
            imagem_id = src.replace("imagem_id=", "")

        print(f"       Buscando imagem_id={imagem_id} no banco...")

        # Busca no banco
        result = db.execute(
            text("""
            SELECT imagem
            FROM trieduc.imagens
            WHERE id = :imagem_id
        """),
            {"imagem_id": imagem_id},
        )

        row = result.fetchone()
        if row and row.imagem:
            print(f"       Imagem encontrada: {len(row.imagem)} bytes")
            return row.imagem
        else:
            print(f"       Imagem {imagem_id} não encontrada no banco")
            return None

    else:
        print(f"       Tipo de src desconhecido: {src[:100]}")
        return None
