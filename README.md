# Pluri Extração

Sistema de extração e classificação de questões educacionais com integração SuperProfessor.

## Arquitetura

```
┌─────────────────┐     ┌──────────────────┐     ┌────────────────┐
│  Webscraping     │────▶│  API Classificação│────▶│  PostgreSQL    │
│  (porta 8501)    │     │  (porta 8000)     │     │  (porta 5432)  │
└─────────────────┘     └──────────────────┘     └────────────────┘
                              │
                              ▼
                        ┌──────────┐     ┌──────────────┐
                        │  MySQL   │     │  OpenAI API   │
                        │  (RDS)   │     │  (GPT-3.5)    │
                        └──────────┘     └──────────────┘
```

- **API de Classificação**: FastAPI que lê questões do MySQL, classifica via OpenAI e salva no PostgreSQL
- **Webscraping Agent**: Painel web que busca questões no SuperProfessor via API HTTP
- **Watchtower**: Monitora o GHCR e atualiza containers automaticamente a cada 5 min

## Deploy no Servidor

### 1. Pré-requisitos

- Docker e Docker Compose instalados
- Acesso ao GitHub Container Registry

### 2. Clonar e configurar

```bash
git clone https://github.com/thsethub/pluri-extracao.git
cd pluri-extracao

# Criar .env a partir do template
cp .env.example .env
nano .env  # preencher credenciais reais
```

### 3. Login no GHCR (uma vez)

```bash
# Criar um Personal Access Token (PAT) no GitHub com permissão "read:packages"
echo "SEU_GITHUB_PAT" | docker login ghcr.io -u thsethub --password-stdin
```

### 4. Subir tudo

```bash
docker compose up -d
```

Isso inicia:
- **PostgreSQL** na porta 5433
- **API** na porta 8000
- **Webscraping** na porta 8501
- **Watchtower** monitorando atualizações

### 5. Verificar status

```bash
docker compose ps
docker compose logs -f api
docker compose logs -f webscraping
```

## CI/CD

O workflow GitHub Actions (`.github/workflows/ci.yml`) roda automaticamente em cada push na branch `main`:

1. Build da imagem da **API** → `ghcr.io/thsethub/pluri-extracao/api:latest`
2. Build da imagem do **Webscraping** → `ghcr.io/thsethub/pluri-extracao/webscraping:latest`

O **Watchtower** no servidor detecta as novas imagens a cada 5 minutos e atualiza os containers automaticamente. **Não precisa fazer nada no servidor** — basta dar push no GitHub.

## Fluxo de Atualização

```
git push origin main
      │
      ▼
GitHub Actions builda e pusha imagens para GHCR
      │
      ▼  (até 5 min)
Watchtower detecta nova imagem
      │
      ▼
Container antigo é removido, novo é criado com a imagem atualizada
```

## Desenvolvimento Local

```bash
# Sem Docker (como antes)
.\iniciar.ps1    # Windows
.\parar.ps1      # Windows

# Com Docker
docker compose up --build    # rebuild local
```

## Portas

| Serviço      | Porta |
|-------------|-------|
| API          | 8000  |
| Webscraping  | 8501  |
| PostgreSQL   | 5433  |
