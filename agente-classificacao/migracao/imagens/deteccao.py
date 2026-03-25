def detectar_tipo_imagem_por_bytes(data: bytes) -> str:
    """Detecta o tipo de imagem pelos magic numbers"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    elif data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    elif data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "gif"
    elif data.startswith(b"RIFF") and b"WEBP" in data[:12]:
        return "webp"
    elif data.startswith(b"<svg") or b"<svg" in data[:100]:
        return "svg"
    elif data.startswith(b"BM"):
        return "bmp"
    return "bin"


def detectar_tipo_src(src: str) -> str:
    """
    Detecta o tipo de src da imagem.

    Returns:
        'url' | 'base64' | 'imagem_id' | 'unknown'
    """
    if not src:
        return "unknown"

    # Base64
    if src.startswith("data:image/") or (";base64," in src):
        return "base64"

    # URL externa (http/https)
    if src.startswith("http://") or src.startswith("https://"):
        return "url"

    # imagem_id
    if "imagem_id=" in src or src.startswith("imagem_id="):
        return "imagem_id"

    # Pode ser caminho relativo ou outro formato
    return "unknown"
