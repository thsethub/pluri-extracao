# ==============================================================================
# COMANDOS DE MIGRAÇÃO EM PRODUÇÃO
# Gerado em: 19/03/2026
# Script: agente-classificacao/scripts/migrar_questao_completa.py
# Flag --producao obrigatória para persistir no banco
#
# Tipos de ação suportados pelo script: classificacao_nova | correcao
# (confirmacao e auto_classificacao não são suportados pelo parâmetro --tipo-acao)
#
# Status por disciplina (pendentes no banco):
#   Biologia          classificacao_nova: 1466  |  correcao:  614
#   Educação Física   classificacao_nova:   34  |  correcao:    1
#   Espanhol          classificacao_nova:  192  |  correcao:   --
#   Filosofia         classificacao_nova:  228  |  correcao:   19
#   Física            classificacao_nova: 1010  |  correcao: 1379
#   Geografia         classificacao_nova: 1900  |  correcao:  241
#   História          classificacao_nova:   95  |  correcao:  119  (*95 com 4 alternativas, bloqueadas)
#   Língua Inglesa    classificacao_nova:   14  |  correcao:   19
#   Língua Portuguesa classificacao_nova: 3326  |  correcao: 2022
#   Matemática        classificacao_nova: 1808  |  correcao: 1839
#   Química           classificacao_nova: 2922  |  correcao: 1152
#   Sociologia        classificacao_nova:  287  |  correcao:   20
# ==============================================================================

# Activate o ambiente virtual antes de rodar:
# & .venv\Scripts\Activate.ps1   (PowerShell)
# source .venv/Scripts/activate  (bash/Git Bash)

# ==============================================================================
# BIOLOGIA
# ==============================================================================

python scripts/migrar_questao_completa.py --tipo-acao classificacao_nova --disciplina "Biologia" --limite None --producao

python scripts/migrar_questao_completa.py --tipo-acao correcao --disciplina "Biologia" --limite None --producao

# ==============================================================================
# EDUCAÇÃO FÍSICA
# ==============================================================================

python scripts/migrar_questao_completa.py --tipo-acao classificacao_nova --disciplina "Educação Física" --limite None --producao

python scripts/migrar_questao_completa.py --tipo-acao correcao --disciplina "Educação Física" --limite None --producao

# ==============================================================================
# ESPANHOL
# ==============================================================================

python scripts/migrar_questao_completa.py --tipo-acao classificacao_nova --disciplina "Espanhol" --limite None --producao

# (sem entradas de correcao para Espanhol)

# ==============================================================================
# FILOSOFIA
# ==============================================================================

python scripts/migrar_questao_completa.py --tipo-acao classificacao_nova --disciplina "Filosofia" --limite None --producao

python scripts/migrar_questao_completa.py --tipo-acao correcao --disciplina "Filosofia" --limite None --producao

# ==============================================================================
# FÍSICA
# ==============================================================================

python scripts/migrar_questao_completa.py --tipo-acao classificacao_nova --disciplina "Física" --limite None --producao

python scripts/migrar_questao_completa.py --tipo-acao correcao --disciplina "Física" --limite None --producao

# ==============================================================================
# GEOGRAFIA
# ==============================================================================

python scripts/migrar_questao_completa.py --tipo-acao classificacao_nova --disciplina "Geografia" --limite None --producao

python scripts/migrar_questao_completa.py --tipo-acao correcao --disciplina "Geografia" --limite None --producao

# ==============================================================================
# HISTÓRIA
# ATENÇÃO: As 95 questões de classificacao_nova têm 4 alternativas (bloqueadas
# pela regra de negócio). Verificar manualmente antes de rodar.
# As de correcao (119 pendentes) podem ter o mesmo problema — confirmar.
# ==============================================================================

python scripts/migrar_questao_completa.py --tipo-acao classificacao_nova --disciplina "História" --limite None --producao

python scripts/migrar_questao_completa.py --tipo-acao correcao --disciplina "História" --limite None --producao

# ==============================================================================
# LÍNGUA INGLESA
# ==============================================================================

python scripts/migrar_questao_completa.py --tipo-acao classificacao_nova --disciplina "Língua Inglesa" --limite None --producao

python scripts/migrar_questao_completa.py --tipo-acao correcao --disciplina "Língua Inglesa" --limite None --producao

# ==============================================================================
# LÍNGUA PORTUGUESA
# ==============================================================================

python scripts/migrar_questao_completa.py --tipo-acao classificacao_nova --disciplina "Língua Portuguesa" --limite None --producao

python scripts/migrar_questao_completa.py --tipo-acao correcao --disciplina "Língua Portuguesa" --limite None --producao

# ==============================================================================
# MATEMÁTICA
# ==============================================================================

python scripts/migrar_questao_completa.py --tipo-acao classificacao_nova --disciplina "Matemática" --limite None --producao

python scripts/migrar_questao_completa.py --tipo-acao correcao --disciplina "Matemática" --limite None --producao

# ==============================================================================
# QUÍMICA
# ==============================================================================

python scripts/migrar_questao_completa.py --tipo-acao classificacao_nova --disciplina "Química" --limite None --producao

python scripts/migrar_questao_completa.py --tipo-acao correcao --disciplina "Química" --limite None --producao

# ==============================================================================
# SOCIOLOGIA
# ==============================================================================

python scripts/migrar_questao_completa.py --tipo-acao classificacao_nova --disciplina "Sociologia" --limite None --producao

python scripts/migrar_questao_completa.py --tipo-acao correcao --disciplina "Sociologia" --limite None --producao

# ==============================================================================
# DISCIPLINAS SEM SUPORTE (tipo_acao não aceito pelo script)
# Os seguintes tipo_acao existem no banco mas não são suportados por --tipo-acao:
#   auto_classificacao  → Artes(1), Física(8), Geografia(5), História(18),
#                         Língua Portuguesa(186), Química(1)
#   confirmacao         → Biologia(133), Filosofia(1), Física(301), Geografia(187),
#                         História(9), Língua Inglesa(5), Matemática(41), Química(515),
#                         Sociologia(2)
# ==============================================================================
