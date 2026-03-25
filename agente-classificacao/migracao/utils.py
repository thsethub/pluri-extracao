import hashlib
import threading
import time


def gerar_hash_timestamp_thread() -> str:
    """
    Gera hash SHA1 baseado no timestamp com microsegundos + thread ID.
    Equivalente ao PHP: sha1(microtime(TRUE) + thread_id)

    Returns:
        Hash SHA1 como string hexadecimal
    """
    timestamp_micro = time.time()  # timestamp com microsegundos
    thread_id = threading.get_ident()  # ID da thread atual

    # Concatena timestamp + thread_id
    unique_string = f"{timestamp_micro:.6f}-{thread_id}"

    # Gera SHA1
    return hashlib.sha1(unique_string.encode()).hexdigest()


def integrar_texto_base(texto_base: str, enunciado: str) -> str:
    """
    Integra o texto_base ao início do enunciado, envolvendo-o em tag <p> e adicionando separador.

    Args:
        texto_base: Contexto prévio da questão (pode ser HTML ou texto puro)
        enunciado: Enunciado principal da questão

    Returns:
        Enunciado completo com texto_base integrado (se houver), ou enunciado original
    """
    if not texto_base or not texto_base.strip():
        return enunciado

    # Limpa espaços e garante que não está vazio
    texto_base_limpo = texto_base.strip()
    if not texto_base_limpo:
        return enunciado

    # Envolve texto_base em <p> e adiciona separador
    texto_base_formatado = f"<p>{texto_base_limpo}</p>"
    separador = "<hr/>"

    # Concatena: texto_base + separador + enunciado
    return f"{texto_base_formatado}{separador}{enunciado}"
