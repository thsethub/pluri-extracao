# Agente de Classificação de Questões com IA

API REST para classificação automática de questões em disciplinas utilizando OpenAI.

## 🚀 Quick Start

### 1. Criar e ativar ambiente virtual

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1  # Windows PowerShell
```

### 2. Instalar dependências

```bash
pip install -r requirements.txt
```

### 3. Configurar variáveis de ambiente

Edite o arquivo `.env` e adicione sua chave da OpenAI:
```env
OPENAI_API_KEY=sk-sua-chave-aqui
```

### 4. Executar a API

```bash
python main.py
```

A API estará disponível em: **http://localhost:8000**

## 📚 Documentação da API

Após iniciar o servidor, acesse:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## 🎯 Endpoints

### POST /classify-discipline

Classifica uma questão nas disciplinas mais apropriadas.

**Request:**
```json
{
  "question": "Qual é a fórmula química da água?"
}
```

**Response:**
```json
{
  "question_id": "550e8400-e29b-41d4-a716-446655440000",
  "question": "Qual é a fórmula química da água?",
  "disciplines": ["Química"],
  "confidence_scores": {
    "Química": 0.98
  },
  "reasoning": "A questão aborda conceitos básicos de química molecular",
  "model_used": "gpt-3.5-turbo",
  "tokens_used": 150,
  "processing_time_ms": 1200
}
```

### GET /disciplines

Lista todas as disciplinas disponíveis para classificação.

**Response:**
```json
{
  "disciplines": [
    "Artes",
    "Biologia",
    "Ciências",
    "Educação Física",
    "Espanhol",
    "Filosofia",
    "Física",
    "Geografia",
    "História",
    "Língua Inglesa",
    "Língua Portuguesa",
    "Matemática",
    "Natureza e Sociedade",
    "Química",
    "Sociologia"
  ],
  "count": 15
}
```

### GET /health

Health check da aplicação.

**Response:**
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "disciplines_count": 15
}
```

## 🧪 Testando a API

### Com cURL:

```bash
curl -X POST "http://localhost:8000/classify-discipline" \
  -H "Content-Type: application/json" \
  -d "{\"question\":\"Qual é a fórmula química da água?\"}"
```

### Com Python:

```python
import requests

response = requests.post(
    "http://localhost:8000/classify-discipline",
    json={"question": "Qual é a fórmula química da água?"}
)

print(response.json())
```

### Com JavaScript/Fetch:

```javascript
fetch('http://localhost:8000/classify-discipline', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    question: 'Qual é a fórmula química da água?'
  })
})
.then(response => response.json())
.then(data => console.log(data));
```

## 📁 Estrutura do Projeto

```
agente-classificacao/
├── src/
│   ├── api/             # API FastAPI
│   │   ├── app.py       # Aplicação principal
│   │   └── schemas.py   # Schemas Pydantic
│   ├── config/          # Configurações
│   ├── models/          # Modelos de dados
│   ├── services/        # Lógica de negócio
│   ├── utils/           # Utilitários
│   └── cli.py           # Versão console (opcional)
├── main.py              # Entry point da API
├── .env                 # Variáveis de ambiente
└── requirements.txt     # Dependências Python
```

## ⚙️ Configurações

Edite o arquivo `.env`:

```env
# OpenAI
OPENAI_API_KEY=sk-your-key
OPENAI_MODEL=gpt-3.5-turbo
OPENAI_MAX_TOKENS=500
OPENAI_TEMPERATURE=0

# Disciplinas (separadas por vírgula)
DISCIPLINES=Artes,Biologia,Ciências,...
```

## 🐳 Docker (Opcional)

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
```

## 📊 Disciplinas Disponíveis

Por padrão, o sistema classifica questões nas seguintes disciplinas:

- Artes
- Biologia
- Ciências
- Educação Física
- Espanhol
- Filosofia
- Física
- Geografia
- História
- Língua Inglesa
- Língua Portuguesa
- Matemática
- Natureza e Sociedade
- Química
- Sociologia

## 📝 Arquitetura

Veja o documento [ARQUITETURA.md](ARQUITETURA.md) para detalhes completos da arquitetura do sistema.

## 🔒 Segurança

⚠️ **IMPORTANTE**: 
- Nunca versione o arquivo `.env` contendo sua chave da API
- Use HTTPS em produção
- Configure CORS adequadamente para seu domínio
