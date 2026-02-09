"""Configurações da aplicação"""

from typing import List, Dict, Optional
from pathlib import Path
import json
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import BaseModel


class Habilidade(BaseModel):
    """Modelo de uma habilidade"""

    id: str
    sigla: str
    habilidade: str
    ano: str


class Settings(BaseSettings):
    """Configurações globais da aplicação"""

    # OpenAI
    openai_api_key: str
    openai_model: str = "gpt-3.5-turbo"
    openai_max_tokens: int = 500
    openai_temperature: float = 0.0

    # Application
    log_level: str = "INFO"
    max_retries: int = 3
    retry_delay: int = 1

    # Database MySQL (leitura - questões)
    db_host: str = "localhost"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "trieduc"

    # Database PostgreSQL local (escrita - assuntos)
    pg_host: str = "localhost"
    pg_port: int = 5433
    pg_user: str = "pluri"
    pg_password: str = "pluri123"
    pg_name: str = "pluri_assuntos"

    @property
    def database_url(self) -> str:
        """Retorna a URL de conexão do banco MySQL (questões)"""
        return f"mysql+pymysql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"

    @property
    def pg_database_url(self) -> str:
        """Retorna a URL de conexão do banco PostgreSQL (assuntos)"""
        return f"postgresql+psycopg2://{self.pg_user}:{self.pg_password}@{self.pg_host}:{self.pg_port}/{self.pg_name}"

    # Disciplinas disponíveis
    disciplines: str = (
        "Artes,Biologia,Ciências,Educação Física,Espanhol,Filosofia,Física,Geografia,História,Língua Inglesa,Língua Portuguesa,Matemática,Natureza e Sociedade,Química,Sociologia"
    )

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False
    )

    _habilidades_cache: Optional[Dict[str, List[Habilidade]]] = None

    def get_disciplines_list(self) -> List[str]:
        """Retorna a lista de disciplinas como array"""
        return [d.strip() for d in self.disciplines.split(",")]

    def get_habilidades_path(self) -> Path:
        """Retorna o caminho do arquivo de habilidades"""
        return Path(__file__).parent / "habilidades.json"

    def load_habilidades(self) -> Dict[str, List[Habilidade]]:
        """Carrega as habilidades do arquivo JSON"""
        if self._habilidades_cache is not None:
            return self._habilidades_cache

        habilidades_path = self.get_habilidades_path()

        if not habilidades_path.exists():
            return {}

        with open(habilidades_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Converte para modelos Pydantic
        habilidades_dict = {}
        for disciplina, habs in data.items():
            habilidades_dict[disciplina] = [Habilidade(**h) for h in habs]

        self._habilidades_cache = habilidades_dict
        return habilidades_dict

    def get_habilidades_by_discipline(self, disciplina: str) -> List[Habilidade]:
        """Retorna as habilidades de uma disciplina específica"""
        habilidades = self.load_habilidades()
        return habilidades.get(disciplina, [])

    def get_all_habilidades_count(self) -> Dict[str, int]:
        """Retorna a contagem de habilidades por disciplina"""
        habilidades = self.load_habilidades()
        return {disc: len(habs) for disc, habs in habilidades.items()}


# Instância global de configurações
settings = Settings()
