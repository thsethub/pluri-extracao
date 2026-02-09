"""Configuração de logging"""

import sys
from loguru import logger
from pathlib import Path


def setup_logger(log_level: str = "INFO") -> None:
    """Configura o logger da aplicação

    Args:
        log_level: Nível de log (DEBUG, INFO, WARNING, ERROR)
    """
    # Remove handlers padrão
    logger.remove()

    # Console handler com formatação colorida
    logger.add(
        sys.stdout,
        colorize=True,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
        level=log_level,
    )

    # File handler para logs persistentes
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    logger.add(
        logs_dir / "classifier_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="30 days",
        level=log_level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function} - {message}",
    )
