# Comandos de Migração

Todos os comandos são executados a partir da **raiz do projeto** (`agente-classificacao/`).

Por padrão, todos os comandos rodam em **DRY-RUN**: nada é inserido no banco, apenas o SQL e o relatório são exibidos.
Para executar de verdade, adicione `--producao` ao final (será solicitada confirmação interativa).

---

## Argumentos disponíveis

| Argumento | Tipo | Padrão | Descrição |
|---|---|---|---|
| `--id` | int | — | ID específico de `classificacao_usuario` |
| `--tipo-acao` | escolha | — | `classificacao_nova` \| `correcao` \| `verificacao` |
| `--disciplina` | string | — | Nome (ou parte do nome) da disciplina |
| `--limite` | int \| `None` | `100` | Máximo de questões a processar |
| `--excluir-lista-quimica` | flag | off | Exclui IDs da `QUESTOES_QUIMICA_ID` |
| `--producao` | flag | off | Executa INSERTs reais (exige confirmação) |

> **Obrigatório**: ao menos um dos três deve ser informado: `--id`, `--tipo-acao` ou `--disciplina`.

---

## 1. Migrar uma questão individual

```bash
# DRY-RUN (padrão) — mostra o SQL sem inserir
python -m migracao --id 13

# PRODUÇÃO — insere no banco (pede confirmação)
python -m migracao --id 13 --producao
```

---

## 2. Migrar por tipo de ação

```bash
# Somente classificações novas (DRY-RUN, limite padrão de 100)
python -m migracao --tipo-acao classificacao_nova

# Somente correções (DRY-RUN)
python -m migracao --tipo-acao correcao

# Somente verificações (DRY-RUN)
python -m migracao --tipo-acao verificacao

# Classificações novas em PRODUÇÃO
python -m migracao --tipo-acao classificacao_nova --producao
```

---

## 3. Migrar por disciplina

```bash
# DRY-RUN — busca por LIKE '%Matemática%'
python -m migracao --disciplina Matemática

# Português
python -m migracao --disciplina Português

# Biologia
python -m migracao --disciplina Biologia

# Física
python -m migracao --disciplina Física

# Química
python -m migracao --disciplina Química

# História
python -m migracao --disciplina História

# Geografia
python -m migracao --disciplina Geografia

# Filosofia
python -m migracao --disciplina Filosofia

# Sociologia
python -m migracao --disciplina Sociologia

# Arte
python -m migracao --disciplina Arte

# Inglês
python -m migracao --disciplina Inglês

# Espanhol
python -m migracao --disciplina Espanhol

# Educação Física
python -m migracao --disciplina "Educação Física"

# Redação
python -m migracao --disciplina Redação

# Literatura
python -m migracao --disciplina Literatura
```

---

## 4. Combinando filtros

```bash
# Correções de Português (DRY-RUN)
python -m migracao --tipo-acao correcao --disciplina Português

# Correções de Português em PRODUÇÃO
python -m migracao --tipo-acao correcao --disciplina Português --producao

# Classificações novas de Matemática (DRY-RUN)
python -m migracao --tipo-acao classificacao_nova --disciplina Matemática

# Verificações de História em PRODUÇÃO
python -m migracao --tipo-acao verificacao --disciplina História --producao

# Subir questões globais
python -m migracao --global --limit None
```
python -m migracao --global --limit None
---

## 5. Controlando o limite de questões

```bash
# Processar apenas 10 questões (DRY-RUN)
python -m migracao --tipo-acao classificacao_nova --limite 10

# Processar 50 questões de Biologia
python -m migracao --disciplina Biologia --limite 50

# Sem limite (todas as questões disponíveis)
python -m migracao --tipo-acao classificacao_nova --limite None

# Sem limite em PRODUÇÃO (atenção!)
python -m migracao --tipo-acao classificacao_nova --limite None --producao
```

---

## 6. Excluir lista de Química

A flag `--excluir-lista-quimica` exclui da busca os IDs presentes em `constants.QUESTOES_QUIMICA_ID`
(questões de Química já migradas separadamente).

```bash
# Classificações novas excluindo a lista de Química (DRY-RUN)
python -m migracao --tipo-acao classificacao_nova --excluir-lista-quimica

# Com limite
python -m migracao --tipo-acao classificacao_nova --excluir-lista-quimica --limite 200

# Em PRODUÇÃO
python -m migracao --tipo-acao classificacao_nova --excluir-lista-quimica --limite None --producao
```

---

## 7. Fluxo recomendado para produção

```bash
# 1. Validar com DRY-RUN e limite pequeno
python -m migracao --tipo-acao classificacao_nova --limite 5

# 2. Validar com DRY-RUN no volume real
python -m migracao --tipo-acao classificacao_nova --limite None

# 3. Executar em PRODUÇÃO
python -m migracao --tipo-acao classificacao_nova --limite None --producao
```

---

## 8. Ajuda da CLI

```bash
python -m migracao --help
```
