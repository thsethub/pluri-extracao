# Webscraping Agent - Super Professor

Agente de webscraping escalável para extrair classificações de questões do Super Professor.

## Setup

```bash
# Criar ambiente virtual
python -m venv .venv
.venv\Scripts\Activate.ps1  # Windows

# Instalar dependências
pip install -r requirements.txt

# Instalar browser
playwright install chromium
```

## Configuração

Edite o arquivo `.env` com suas credenciais e configurações.

## Uso

```bash
# Listar disciplinas e progresso
python main.py --list-disciplinas

# Ver estatísticas de extração
python main.py --stats

# Iniciar extração para uma disciplina (com browser visível)
python main.py --disciplina-id 1

# Iniciar extração no modo headless (sem interface)
python main.py --disciplina-id 1 --headless
```

## Arquitetura

```
main.py                 → Ponto de entrada + CLI
src/
  config.py             → Configurações (.env)
  logger.py             → Logging (console + arquivo)
  api_client.py         → Cliente HTTP para API de extração
  browser_manager.py    → Gerenciamento do Playwright (login, sessão, cookies)
  scraper.py            → Extração de classificações do site
  agent.py              → Orquestrador (loop principal resiliente)
storage/
  browser_state.json    → Sessão salva (cookies + localStorage)
logs/
  scraper_*.log         → Logs completos
  errors_*.log          → Apenas erros
```

## Fluxo

1. **API** fornece a próxima questão pendente (`GET /extracao/proxima?disciplina_id=X`)
2. **Browser** pesquisa o enunciado no Super Professor
3. **Scraper** extrai as classificações (breadcrumbs)
4. **API** salva o resultado (`POST /extracao/salvar`)
5. **Repeat** até acabar as questões pendentes

## Segurança

- Delays aleatórios entre requisições (3-7s configurável)
- Persistência de sessão (evita re-login constante)
- Pausa automática após erros consecutivos
- Screenshots automáticos em caso de erro
- Bloqueio de recursos pesados (imagens, fontes, analytics)
- User-Agent realista

## Pré-requisitos

Antes de rodar o webscraping, a **API de extração** precisa estar rodando:

```bash
cd "../Agende de Classificação"
python main.py
# API sobe em http://localhost:8000
```
