import sys

p = r'c:\Users\Thiago\Documents\Projetos\Pluri\ocr\ocr-frontend\src\components\RevisorDetalhesHumanaModal.tsx'
with open(p, 'r', encoding='utf-8') as f:
    text = f.read()

replacements = {
    'Ã£': 'ã', 'Ã§': 'ç', 'Ãµ': 'õ', 'Ã\xad': 'í',
    'Ã¡': 'á', 'Ã©': 'é', 'â€”': '—', 'Â·': '·'
}

for k, v in replacements.items():
    text = text.replace(k, v)

with open(p, 'w', encoding='utf-8') as f:
    f.write(text)
