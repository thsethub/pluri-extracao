"""
Logger configurado com Loguru.
Escreve no console (colorido) e em arquivo rotativo.
"""

import sys
from loguru import logger
from src.config import settings


def setup_logger():
    """Configura o logger do agente de webscraping."""
    # Remove handler padrão
    logger.remove()

    # Console — colorful, conciso
    logger.add(
        sys.stderr,
        level=settings.LOG_LEVEL,
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # Arquivo — detalhado, com rotação
    logger.add(
        str(settings.LOG_DIR / "scraper_{time:YYYY-MM-DD}.log"),
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        rotation="10 MB",
        retention="30 days",
        compression="zip",
        encoding="utf-8",
    )

    # Arquivo separado só para erros
    logger.add(
        str(settings.LOG_DIR / "errors_{time:YYYY-MM-DD}.log"),
        level="ERROR",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}\n{exception}",
        rotation="5 MB",
        retention="30 days",
        compression="zip",
        encoding="utf-8",
    )

    return logger


# Logger global
log = setup_logger()
