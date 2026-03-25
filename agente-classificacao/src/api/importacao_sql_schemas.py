from pydantic import BaseModel, Field
from typing import List, Optional


class ImportacaoSqlResponse(BaseModel):
    status: str = "ok"
    mensagem: str
    tabela_destino: str
    banco_destino: Optional[str] = None
    arquivo_sql: str
    arquivo_json: Optional[str] = None
    linhas_processadas: int = Field(..., ge=0)
    colunas: List[str]
    delimitador: str
    chunk_size: int = Field(..., ge=1)
    executado_no_mysql: bool = False
    linhas_inseridas_mysql: int = Field(0, ge=0)
    duracao_segundos: float = Field(..., ge=0)
