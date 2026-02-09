"""
Configurações do agente de webscraping.
Carrega variáveis do .env e expõe para todo o projeto.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Raiz do projeto (onde fica o .env)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

load_dotenv(ENV_PATH)


class Settings:
    """Configurações centralizadas do scraper."""

    # --- Super Professor ---
    SUPERPRO_EMAIL: str = os.getenv("SUPERPRO_EMAIL", "")
    SUPERPRO_PASSWORD: str = os.getenv("SUPERPRO_PASSWORD", "")
    SUPERPRO_BASE_URL: str = os.getenv(
        "SUPERPRO_BASE_URL", "https://superprofessor.com.br"
    )
    SUPERPRO_INTERNO_URL: str = "https://interno.superprofessor.com.br"
    SUPERPRO_API_URL: str = "https://api-questoes.superprofessor.com.br/api"
    LOGIN_URL: str = f"{SUPERPRO_BASE_URL}/acesso-professor"

    # --- API Local ---
    API_BASE_URL: str = os.getenv("API_BASE_URL", "http://localhost:8000")

    # --- Delays (anti-detecção) ---
    DELAY_MIN: float = float(os.getenv("DELAY_MIN", "3"))
    DELAY_MAX: float = float(os.getenv("DELAY_MAX", "7"))
    DELAY_AFTER_LOGIN: float = float(os.getenv("DELAY_AFTER_LOGIN", "5"))

    # --- Timeouts ---
    NAVIGATION_TIMEOUT: int = (
        int(os.getenv("NAVIGATION_TIMEOUT", "30")) * 1000
    )  # Playwright usa ms

    # --- Limites de segurança ---
    MAX_CONSECUTIVE_ERRORS: int = int(os.getenv("MAX_CONSECUTIVE_ERRORS", "3"))
    LONG_PAUSE_SECONDS: int = int(os.getenv("LONG_PAUSE_SECONDS", "120"))
    MAX_QUESTIONS_PER_SESSION: int = int(os.getenv("MAX_QUESTIONS_PER_SESSION", "0"))
    MAX_SERVER_DOWN_ROUNDS: int = int(os.getenv("MAX_SERVER_DOWN_ROUNDS", "10"))

    # --- Browser ---
    HEADLESS: bool = os.getenv("HEADLESS", "false").lower() == "true"

    # --- Logs ---
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE: str = os.getenv("LOG_FILE", "logs/scraper.log")
    LOG_DIR: Path = PROJECT_ROOT / "logs"

    # --- Cookies/Session persistence ---
    STORAGE_DIR: Path = PROJECT_ROOT / "storage"
    COOKIES_FILE: Path = STORAGE_DIR / "cookies.json"
    STATE_FILE: Path = STORAGE_DIR / "browser_state.json"

    @classmethod
    def ensure_dirs(cls):
        """Cria diretórios necessários."""
        cls.LOG_DIR.mkdir(parents=True, exist_ok=True)
        cls.STORAGE_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
