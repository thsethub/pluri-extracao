from typing import Dict, List, Tuple

from bs4 import BeautifulSoup

from .processamento import obter_bytes_imagem
from .s3 import upload_imagem_s3_duplo


def processar_imagens_html(
    html: str, questao_id: int, db, dry_run: bool = True
) -> Tuple[str, List[Dict]]:
    """
    Processa imagens no HTML, faz upload duplo (alta/baixa) e retorna:
    - HTML atualizado com URLs S3 (alta resolução)
    - Lista de metadados das imagens para rd_questoes_imagens

    Suporta:
    - URLs externas (http/https) - baixa a imagem
    - Base64 (data:image/...) - decodifica
    - imagem_id (do banco trieduc) - busca no banco
    """
    if not html:
        return html, []

    soup = BeautifulSoup(html, "html.parser")
    imagens_encontradas = soup.find_all("img")

    if not imagens_encontradas:
        return html, []

    print(f"\n   {len(imagens_encontradas)} imagem(ns) encontrada(s)")

    metadados_lista = []

    for idx, img_tag in enumerate(imagens_encontradas):
        src = img_tag.get("src", "")

        if not src:
            print(f"\n      Imagem {idx+1}: SEM src, pulando...")
            continue

        from .deteccao import detectar_tipo_src

        tipo_src = detectar_tipo_src(src)
        print(f"\n      Imagem {idx+1}: Tipo={tipo_src}")
        print(f"         src: {src[:100]}{'...' if len(src) > 100 else ''}")

        # Obtém bytes da imagem (independente do formato)
        imagem_bytes = obter_bytes_imagem(src, db)

        if not imagem_bytes:
            print(f"       Não foi possível obter bytes da imagem, pulando...")
            continue

        # Upload duplo (alta + baixa)
        url_alta, url_baixa, metadados = upload_imagem_s3_duplo(
            imagem_bytes, questao_id, idx, dry_run=dry_run
        )

        # Atualiza tag com URL ALTA no HTML
        img_tag["src"] = url_alta

        # Guarda metadados para rd_questoes_imagens
        metadados_lista.append(
            {
                "url_alta": url_alta,
                "url_baixa": url_baixa,
                "largura": metadados["largura"],
                "altura": metadados["altura"],
                "tamanho": metadados["tamanho_bytes"],
            }
        )

    return str(soup), metadados_lista
