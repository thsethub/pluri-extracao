# ── Webscraping Agent ─────────────────────────────────────────
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependências do sistema (Playwright precisa de libs do Chromium)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc libpq-dev curl && \
    rm -rf /var/lib/apt/lists/*

# Instalar deps Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir uvicorn fastapi psycopg2-binary && \
    playwright install --with-deps chromium

# Copiar código
COPY . .

# Criar diretórios necessários
RUN mkdir -p logs storage

EXPOSE 8501

CMD ["python", "web.py"]
