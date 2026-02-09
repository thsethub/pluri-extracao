"""Serviço de tratamento e limpeza de enunciados HTML"""

import html
import re
import unicodedata
from typing import Tuple


def _normalizar_unicode(texto: str) -> str:
    """
    Normalização GENERALISTA de Unicode.

    Usa NFKC (Compatibility Decomposition + Canonical Composition) que
    resolve automaticamente centenas de caracteres problemáticos:
      - Ligaturas tipográficas:  ﬁ→fi  ﬂ→fl  ﬃ→ffi  ﬄ→ffl  ﬀ→ff
      - Fullwidth chars:  Ａ→A  １→1  （→(
      - Sobrescrito/subscrito:  ²→2  ₃→3  ⁿ→n
      - Frações:  ½→1⁄2  ¼→1⁄4
      - Romanos compat.:  ⅰ→i  Ⅳ→IV
      - Símbolos:  ™→TM  ℃→°C  №→No
      - E qualquer outro mapeamento de compatibilidade Unicode
    """
    if not texto:
        return texto

    # 1. NFKC: decomposição de compatibilidade + composição canônica
    texto = unicodedata.normalize("NFKC", texto)

    # 2. Espaços Unicode remanescentes -> espaço ASCII
    texto = re.sub(
        r"[\u00a0\u2000-\u200b\u2028\u2029\u202f\u205f\u2060\u3000\ufeff]",
        " ",
        texto,
    )

    # 3. Traços/hífens Unicode -> hífen ASCII
    texto = re.sub(r"[\u2010-\u2015\u2212\ufe58\ufe63\uff0d]", "-", texto)

    # 4. Aspas Unicode -> aspas ASCII
    texto = re.sub(r"[\u201c\u201d\u201e\u201f\u00ab\u00bb\u2039\u203a]", '"', texto)
    texto = re.sub(r"[\u2018\u2019\u201a\u201b\u2032\u2035]", "'", texto)

    # 5. Reticências Unicode -> ...
    texto = texto.replace("\u2026", "...")

    # 6. Bullet e símbolos de lista -> hífen
    texto = re.sub(
        r"[\u2022\u2023\u2043\u204c\u204d\u25aa\u25cf\u25e6\u2619]", "-", texto
    )

    # 7. Remover combining marks decorativos (sublinhado, sobrelinhas, etc.)
    texto = re.sub(r"[\u0300-\u036f]", "", texto)

    # 8. Remover caracteres de controle invisíveis (exceto \n \r \t)
    texto = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", texto)

    # 9. Remover variation selectors e outros zero-width
    texto = re.sub(r"[\ufe00-\ufe0f\u200c-\u200f\u202a-\u202e]", "", texto)

    # 10. CATCH-ALL: remover qualquer char exótico remanescente
    #     Mantém: ASCII, Latin-1 Supplement, Latin Extended-A/B, newlines
    #     Remove: Cirílico, Grego, símbolos matemáticos, etc.
    texto = re.sub(r"[^\x09\x0a\x0d\x20-\x7e\u00a0-\u024f]", "", texto)

    return texto


def tratar_enunciado(enunciado: str | None) -> Tuple[str, bool, str | None]:
    """
    Trata o enunciado de uma questão:
    1. Detecta se contém imagens (flag informativa)
    2. Remove tags <img> e URLs de imagem
    3. Decodifica HTML entities
    4. Remove demais tags HTML

    Returns:
        Tuple[str, bool, str | None]:
            - texto limpo (texto restante mesmo se tinha imagem)
            - contem_imagem (True/False) - apenas flag informativa
            - motivo_erro (se houver)
    """
    if not enunciado or not enunciado.strip():
        return "", False, "Enunciado vazio"

    # 1. Detecta se contém imagens (flag informativa, não bloqueia)
    tem_imagem = _contem_imagem(enunciado)

    # 1.5. Normaliza Unicode ANTES de qualquer processamento
    texto = _normalizar_unicode(enunciado)

    # 2. Remove tags <img> e URLs de imagem antes de processar
    texto = _remover_imagens(texto)

    # 3. Decodifica HTML entities (&#227; -> ã, &amp; -> &, etc.)
    texto = html.unescape(texto)

    # 4. Remove tags HTML preservando o texto
    texto = _remover_tags_html(texto)

    # 5. Limpa referências bibliográficas e créditos
    texto = _limpar_referencias(texto)

    # 6. Remove ruído de enunciado ("Texto I", "Analise a figura", etc.)
    texto = _limpar_ruido_enunciado(texto)

    # 7. Limpa espaços extras
    texto = _limpar_espacos(texto)

    if not texto.strip():
        if tem_imagem:
            return "", True, "Enunciado contém apenas imagem - sem texto"
        return "", False, "Enunciado ficou vazio após tratamento"

    return texto.strip(), tem_imagem, None


def _contem_imagem(texto: str) -> bool:
    """Verifica se o texto contém tags <img> ou referências a imagens"""
    # Padrão para tags <img>
    img_pattern = re.compile(r"<img\s", re.IGNORECASE)
    if img_pattern.search(texto):
        return True

    # Padrão para URLs de imagem comuns
    img_url_pattern = re.compile(
        r'https?://[^\s"\'<>]+\.(png|jpg|jpeg|gif|svg|webp|bmp)', re.IGNORECASE
    )
    if img_url_pattern.search(texto):
        return True

    return False


def _remover_imagens(texto: str) -> str:
    """Remove tags <img> e URLs de imagem do texto, preservando o restante."""
    # Remove tags <img ...> (self-closing ou não)
    texto = re.sub(r"<img[^>]*/?>", "", texto, flags=re.IGNORECASE)
    # Remove URLs de imagem soltas no texto
    texto = re.sub(
        r'https?://[^\s"\'<>]+\.(png|jpg|jpeg|gif|svg|webp|bmp)',
        "",
        texto,
        flags=re.IGNORECASE,
    )
    return texto


def _remover_tags_html(texto: str) -> str:
    """Remove todas as tags HTML preservando o conteúdo textual"""
    # Remove tags de estilo e script com conteúdo
    texto = re.sub(
        r"<style[^>]*>.*?</style>", "", texto, flags=re.DOTALL | re.IGNORECASE
    )
    texto = re.sub(
        r"<script[^>]*>.*?</script>", "", texto, flags=re.DOTALL | re.IGNORECASE
    )

    # Substitui <br>, <br/>, <p>, </p> por espaço/quebra
    texto = re.sub(r"<br\s*/?>", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"</p>", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"<p[^>]*>", "", texto, flags=re.IGNORECASE)

    # Remove todas as demais tags HTML
    texto = re.sub(r"<[^>]+>", "", texto)

    return texto


def _limpar_referencias(texto: str) -> str:
    """Remove referências bibliográficas e créditos comuns de questões de prova."""
    # "Disponível em: URL. Acesso em: date."
    texto = re.sub(r"Dispon[ií]vel\s+em:?\s*\S+\.?\s*", "", texto, flags=re.IGNORECASE)
    texto = re.sub(r"Acesso\s+em:?\s*[^.]*\.\s*", "", texto, flags=re.IGNORECASE)
    # "(Adaptado de ...)" ou "(Fonte: ...)" ou "(Extraído de ...)"
    texto = re.sub(
        r"\(\s*(?:Adaptado|Fonte|Extra[ií]do|Retirado)\s+de[^)]*\)\.?\s*",
        "",
        texto,
        flags=re.IGNORECASE,
    )
    # Crédito de imagem no início: "Charge anônima." / "Foto: ..."
    texto = re.sub(
        r"^\s*(?:Charge|Foto|Imagem|Ilustra[cç]ão|Gravura)\s*(?:an[oô]nima)?[.:,]?\s*",
        "",
        texto,
        flags=re.IGNORECASE,
    )
    return texto


def _limpar_espacos(texto: str) -> str:
    """Remove espaços duplicados e linhas em branco excessivas"""
    # Substitui múltiplos espaços por um único
    texto = re.sub(r"[ \t]+", " ", texto)
    # Substitui múltiplas quebras por uma
    texto = re.sub(r"\n\s*\n", "\n", texto)
    return texto.strip()


def _limpar_ruido_enunciado(texto: str) -> str:
    """Remove marcadores de texto e comandos genéricos que poluem a busca."""
    # "Texto I", "Texto II", "Texto 1", "Texto 2" etc. (qualquer posição)
    texto = re.sub(
        r"Texto\s+(?:[IVX]+|\d+)\s*[:\-]?\s*", "", texto, flags=re.IGNORECASE
    )
    # "Analise a/o figura/imagem/quadro/mapa/texto/gráfico a seguir/abaixo"
    texto = re.sub(
        r"Analise\s+(?:a|o|as|os)\s+(?:figura|imagem|quadro|mapa|texto|trecho|gr[aá]fico|tabela|charge|tirinha)s?\s*(?:a\s+seguir|abaixo|seguinte)?[.:\s]*",
        "",
        texto,
        flags=re.IGNORECASE,
    )
    return texto
