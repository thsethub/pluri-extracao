"""
Script completo de migração de questões do trieduc para recursos_didaticos.

FLUXO:
1. Buscar dados da classificação (thsethub.classificacao_usuario)
2. Buscar questão completa (trieduc.questoes + alternativas)
3. Buscar enunciado tratado (thsethub.questao_assuntos)
4. Mapear disciplina e assuntos por NOME
5. Processar imagens (alta + baixa resolução)
6. Inserir em rd_questoes, rd_questoes_assuntos, rd_questoes_alternativas, rd_questoes_imagens
7. Marcar como migrada

MODO DRY-RUN: Não executa INSERTs, apenas mostra o que seria feito.
"""

import sys
import json
import uuid
import base64
import requests
from pathlib import Path
from io import BytesIO
from typing import List, Dict, Tuple, Optional
from datetime import datetime
from difflib import SequenceMatcher
from urllib.parse import urlparse

import boto3
from PIL import Image
from bs4 import BeautifulSoup
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import SessionLocal
from src.config import settings

# ============================================================================
# CONFIGURAÇÕES S3
# ============================================================================

s3_client = boto3.client(
    's3',
    aws_access_key_id=settings.aws_access_key_id,
    aws_secret_access_key=settings.aws_secret_access_key,
    region_name=settings.aws_region
)

S3_BUCKET = settings.aws_s3_bucket
S3_FOLDER_HIGH = "questoes-sync-teste"  # Alta resolução
S3_FOLDER_LOW = "questoes-sync-teste/low"  # Baixa resolução

# ============================================================================
# FUNÇÕES AUXILIARES
# ============================================================================

def similaridade_texto(a: str, b: str) -> float:
    """Calcula similaridade entre dois textos (0.0 a 1.0)"""
    a_clean = a.lower().replace('[rm]', '').replace('[', '').replace(']', '').strip()
    b_clean = b.lower().replace('[rm]', '').replace('[', '').replace(']', '').strip()
    return SequenceMatcher(None, a_clean, b_clean).ratio()


def detectar_tipo_imagem_por_bytes(data: bytes) -> str:
    """Detecta o tipo de imagem pelos magic numbers"""
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    elif data.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    elif data.startswith(b'GIF87a') or data.startswith(b'GIF89a'):
        return 'gif'
    elif data.startswith(b'RIFF') and b'WEBP' in data[:12]:
        return 'webp'
    elif data.startswith(b'<svg') or b'<svg' in data[:100]:
        return 'svg'
    elif data.startswith(b'BM'):
        return 'bmp'
    return 'bin'


def redimensionar_imagem(imagem_bytes: bytes, percentual: float = 0.5) -> Tuple[bytes, int, int]:
    """
    Redimensiona imagem para percentual do tamanho original.
    
    Args:
        imagem_bytes: Bytes da imagem original
        percentual: Percentual do tamanho (0.5 = 50%)
    
    Returns:
        (bytes_redimensionados, nova_largura, nova_altura)
    """
    try:
        img = Image.open(BytesIO(imagem_bytes))
        
        # Calcula novas dimensões
        nova_largura = int(img.width * percentual)
        nova_altura = int(img.height * percentual)
        
        # Redimensiona
        img_resized = img.resize((nova_largura, nova_altura), Image.Resampling.LANCZOS)
        
        # Salva em bytes
        buffer = BytesIO()
        formato = img.format or 'PNG'
        img_resized.save(buffer, format=formato)
        buffer.seek(0)
        
        return buffer.getvalue(), nova_largura, nova_altura
    except Exception as e:
        print(f"⚠️  Erro ao redimensionar imagem: {e}")
        # Em caso de erro, retorna a imagem original reduzida manualmente
        return imagem_bytes, 0, 0


def extrair_metadados_imagem(imagem_bytes: bytes) -> Dict:
    """Extrai metadados da imagem usando PIL"""
    try:
        img = Image.open(BytesIO(imagem_bytes))
        
        return {
            "largura": img.width,
            "altura": img.height,
            "tamanho_bytes": len(imagem_bytes),
            "tamanho_kb": round(len(imagem_bytes) / 1024, 2),
            "formato": img.format or "UNKNOWN"
        }
    except Exception as e:
        return {
            "largura": 0,
            "altura": 0,
            "tamanho_bytes": len(imagem_bytes),
            "tamanho_kb": round(len(imagem_bytes) / 1024, 2),
            "formato": "UNKNOWN"
        }


def baixar_imagem_url(url: str) -> Optional[bytes]:
    """
    Baixa imagem de uma URL externa.
    
    Returns:
        bytes da imagem ou None se falhar
    """
    try:
        print(f"      🌐 Baixando de URL: {url[:80]}...")
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        imagem_bytes = response.content
        print(f"      ✓ Baixado: {len(imagem_bytes)} bytes")
        return imagem_bytes
    except Exception as e:
        print(f"      ⚠️  Erro ao baixar URL: {e}")
        return None


def decodificar_base64_imagem(base64_str: str) -> Optional[bytes]:
    """
    Decodifica imagem em Base64.
    
    Args:
        base64_str: String Base64 (pode incluir data:image/png;base64,...)
    
    Returns:
        bytes da imagem ou None se falhar
    """
    try:
        # Remove prefixo data:image/...;base64, se existir
        if 'base64,' in base64_str:
            base64_str = base64_str.split('base64,')[1]
        
        imagem_bytes = base64.b64decode(base64_str)
        print(f"      ✓ Base64 decodificado: {len(imagem_bytes)} bytes")
        return imagem_bytes
    except Exception as e:
        print(f"      ⚠️  Erro ao decodificar Base64: {e}")
        return None


def detectar_tipo_src(src: str) -> str:
    """
    Detecta o tipo de src da imagem.
    
    Returns:
        'url' | 'base64' | 'imagem_id' | 'unknown'
    """
    if not src:
        return 'unknown'
    
    # Base64
    if src.startswith('data:image/') or (';base64,' in src):
        return 'base64'
    
    # URL externa (http/https)
    if src.startswith('http://') or src.startswith('https://'):
        return 'url'
    
    # imagem_id
    if 'imagem_id=' in src or src.startswith('imagem_id='):
        return 'imagem_id'
    
    # Pode ser caminho relativo ou outro formato
    return 'unknown'


def obter_bytes_imagem(src: str, db) -> Optional[bytes]:
    """
    Obtém os bytes da imagem independente do formato do src.
    
    Args:
        src: Atributo src da tag <img>
        db: Sessão do banco de dados
    
    Returns:
        bytes da imagem ou None se falhar
    """
    tipo = detectar_tipo_src(src)
    
    if tipo == 'url':
        return baixar_imagem_url(src)
    
    elif tipo == 'base64':
        return decodificar_base64_imagem(src)
    
    elif tipo == 'imagem_id':
        # Extrai ID da imagem
        if 'imagem_id=' in src:
            imagem_id = src.split('imagem_id=')[1].split('&')[0]
        else:
            imagem_id = src.replace('imagem_id=', '')
        
        print(f"      📦 Buscando imagem_id={imagem_id} no banco...")
        
        # Busca no banco
        result = db.execute(text("""
            SELECT imagem
            FROM trieduc.imagens
            WHERE id = :imagem_id
        """), {"imagem_id": imagem_id})
        
        row = result.fetchone()
        if row and row.imagem:
            print(f"      ✓ Imagem encontrada: {len(row.imagem)} bytes")
            return row.imagem
        else:
            print(f"      ⚠️  Imagem {imagem_id} não encontrada no banco")
            return None
    
    else:
        print(f"      ⚠️  Tipo de src desconhecido: {src[:100]}")
        return None


def extrair_metadados_imagem(imagem_bytes: bytes) -> Dict:
    """Extrai metadados da imagem usando PIL"""
    try:
        img = Image.open(BytesIO(imagem_bytes))
        
        return {
            "largura": img.width,
            "altura": img.height,
            "tamanho_bytes": len(imagem_bytes),
            "tamanho_kb": round(len(imagem_bytes) / 1024, 2),
            "formato": img.format or "UNKNOWN"
        }
    except Exception as e:
        return {
            "largura": 0,
            "altura": 0,
            "tamanho_bytes": len(imagem_bytes),
            "tamanho_kb": round(len(imagem_bytes) / 1024, 2),
            "formato": "UNKNOWN"
        }


def upload_imagem_s3_duplo(
    imagem_bytes: bytes,
    questao_id: int,
    indice: int,
    dry_run: bool = True
) -> Tuple[str, str, Dict]:
    """
    Faz upload de imagem em ALTA e BAIXA resolução para S3.
    
    Returns:
        (url_alta, url_baixa, metadados_alta)
    """
    # Detecta formato
    formato = detectar_tipo_imagem_por_bytes(imagem_bytes)
    
    # Gera nome único
    uuid_str = str(uuid.uuid4())[:8]
    nome_arquivo = f"questao_{questao_id}_{uuid_str}_{indice}.{formato}"
    
    # ALTA RESOLUÇÃO
    s3_key_high = f"{S3_FOLDER_HIGH}/{nome_arquivo}"
    url_alta = f"https://{S3_BUCKET}.s3.amazonaws.com/{s3_key_high}"
    metadados_alta = extrair_metadados_imagem(imagem_bytes)
    
    if not dry_run:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key_high,
            Body=imagem_bytes,
            ContentType=f'image/{formato}'
        )
        print(f"      ✓ Upload ALTA: {nome_arquivo}")
    else:
        print(f"      [DRY-RUN] Upload ALTA: {nome_arquivo}")
    
    # BAIXA RESOLUÇÃO (50%)
    imagem_baixa, _, _ = redimensionar_imagem(imagem_bytes, percentual=0.5)
    s3_key_low = f"{S3_FOLDER_LOW}/{nome_arquivo}"
    url_baixa = f"https://{S3_BUCKET}.s3.amazonaws.com/{s3_key_low}"
    
    if not dry_run:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key_low,
            Body=imagem_baixa,
            ContentType=f'image/{formato}'
        )
        print(f"      ✓ Upload BAIXA: {nome_arquivo}")
    else:
        print(f"      [DRY-RUN] Upload BAIXA: {nome_arquivo}")
    
    return url_alta, url_baixa, metadados_alta


def processar_imagens_html(
    html: str,
    questao_id: int,
    db,
    dry_run: bool = True
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
    
    soup = BeautifulSoup(html, 'html.parser')
    imagens_encontradas = soup.find_all('img')
    
    if not imagens_encontradas:
        return html, []
    
    print(f"\n   📷 {len(imagens_encontradas)} imagem(ns) encontrada(s)")
    
    metadados_lista = []
    
    for idx, img_tag in enumerate(imagens_encontradas):
        src = img_tag.get('src', '')
        
        if not src:
            print(f"\n      Imagem {idx+1}: SEM src, pulando...")
            continue
        
        tipo_src = detectar_tipo_src(src)
        print(f"\n      Imagem {idx+1}: Tipo={tipo_src}")
        print(f"         src: {src[:100]}{'...' if len(src) > 100 else ''}")
        
        # Obtém bytes da imagem (independente do formato)
        imagem_bytes = obter_bytes_imagem(src, db)
        
        if not imagem_bytes:
            print(f"      ⚠️  Não foi possível obter bytes da imagem, pulando...")
            continue
        
        # Upload duplo (alta + baixa)
        url_alta, url_baixa, metadados = upload_imagem_s3_duplo(
            imagem_bytes, questao_id, idx, dry_run=dry_run
        )
        
        # Atualiza tag com URL ALTA no HTML
        img_tag['src'] = url_alta
        
        # Guarda metadados para rd_questoes_imagens
        metadados_lista.append({
            "url_alta": url_alta,
            "url_baixa": url_baixa,
            "largura": metadados["largura"],
            "altura": metadados["altura"],
            "tamanho": metadados["tamanho_bytes"]
        })
    
    return str(soup), metadados_lista


# ============================================================================
# FUNÇÕES DE MAPEAMENTO
# ============================================================================

def mapear_disciplina_por_nome(nome_disciplina: str, db) -> Optional[int]:
    """Mapeia nome da disciplina para disc_id em compartilhados"""
    result = db.execute(text("""
        SELECT disc_id, disc_descricao
        FROM compartilhados.disciplinas
        WHERE disc_descricao LIKE :nome
        LIMIT 1
    """), {"nome": f"%{nome_disciplina}%"})
    
    row = result.fetchone()
    if row:
        print(f"   ✓ Disciplina mapeada: '{nome_disciplina}' → disc_id={row.disc_id} ({row.disc_descricao})")
        return row.disc_id
    else:
        print(f"   ⚠️  Disciplina '{nome_disciplina}' não encontrada em compartilhados")
        return None


def mapear_assuntos_por_nome(
    modulos_escolhidos: List[str],
    descricoes_assunto: List[str],
    db
) -> List[Dict]:
    """
    Mapeia módulos e assuntos por NOME (SEM prefixo [RM]).
    
    Returns:
        Lista de dicts com assu_id, assu_descricao, disc_modu_id, etc.
    """
    todos_assuntos = []
    
    for idx, nome_modulo in enumerate(modulos_escolhidos):
        print(f"\n   📦 Módulo {idx+1}: '{nome_modulo}'")
        
        # Busca módulo SEM [RM]
        result = db.execute(text("""
            SELECT disc_modu_id, disc_modu_descricao, disc_id
            FROM compartilhados.disciplinas_modulos
            WHERE disc_modu_descricao LIKE :nome
              AND disc_modu_descricao NOT LIKE '[RM]%'
              AND disc_modu_descricao NOT LIKE '% [RM]%'
        """), {"nome": f"%{nome_modulo}%"})
        
        modulos_encontrados = result.fetchall()
        
        if not modulos_encontrados:
            print(f"      ⚠️  Módulo não encontrado")
            continue
        
        # Busca assuntos para cada módulo encontrado
        melhor_modulo = None
        assuntos_disponiveis = []
        
        for mod in sorted(modulos_encontrados, 
                         key=lambda m: similaridade_texto(nome_modulo, m.disc_modu_descricao),
                         reverse=True):
            
            result = db.execute(text("""
                SELECT assu_id, assu_descricao, disc_modu_id
                FROM compartilhados.assuntos
                WHERE disc_modu_id = :disc_modu_id
            """), {"disc_modu_id": mod.disc_modu_id})
            
            assuntos_mod = result.fetchall()
            
            if assuntos_mod:
                melhor_modulo = mod
                assuntos_disponiveis = assuntos_mod
                break
        
        if not melhor_modulo:
            print(f"      ⚠️  Nenhum módulo com assuntos encontrado")
            continue
        
        print(f"      ✓ Módulo: disc_modu_id={melhor_modulo.disc_modu_id} - {melhor_modulo.disc_modu_descricao}")
        print(f"      ✓ {len(assuntos_disponiveis)} assuntos disponíveis")
        
        # Match por nome com descricao_assunto
        if idx < len(descricoes_assunto):
            nome_assunto = descricoes_assunto[idx]
            
            # Calcula similaridade
            matches = []
            for assu in assuntos_disponiveis:
                similaridade = similaridade_texto(nome_assunto, assu.assu_descricao)
                matches.append({
                    "assu_id": assu.assu_id,
                    "assu_descricao": assu.assu_descricao,
                    "disc_modu_id": melhor_modulo.disc_modu_id,
                    "disc_modu_descricao": melhor_modulo.disc_modu_descricao,
                    "disc_id": melhor_modulo.disc_id,
                    "similaridade": similaridade
                })
            
            matches.sort(key=lambda x: x["similaridade"], reverse=True)
            
            # Usa melhor match se >= 50%
            if matches and matches[0]["similaridade"] >= 0.5:
                print(f"      ✓ Match: {matches[0]['similaridade']:.0%} - {matches[0]['assu_descricao']}")
                todos_assuntos.append(matches[0])
            else:
                # Usa todos do módulo
                print(f"      ⚠️  Match fraco, usando TODOS os assuntos do módulo")
                todos_assuntos.extend(matches)
        else:
            # Usa todos do módulo
            for assu in assuntos_disponiveis:
                todos_assuntos.append({
                    "assu_id": assu.assu_id,
                    "assu_descricao": assu.assu_descricao,
                    "disc_modu_id": melhor_modulo.disc_modu_id,
                    "disc_modu_descricao": melhor_modulo.disc_modu_descricao,
                    "disc_id": melhor_modulo.disc_id,
                    "similaridade": 1.0
                })
    
    return todos_assuntos


# ============================================================================
# FUNÇÃO PRINCIPAL DE MIGRAÇÃO
# ============================================================================

def migrar_questao_completa(classificacao_id: int, dry_run: bool = True):
    """
    Migra UMA questão completa do trieduc para recursos_didaticos.
    
    Args:
        classificacao_id: ID da classificação em thsethub.classificacao_usuario
        dry_run: Se True, não executa INSERTs, apenas mostra o que seria feito
    """
    db = SessionLocal()
    
    try:
        print("=" * 80)
        print(f"{'[DRY-RUN] ' if dry_run else ''}MIGRAÇÃO COMPLETA - Classificação ID {classificacao_id}")
        print("=" * 80)
        
        # ====================================================================
        # FASE 1: BUSCAR DADOS
        # ====================================================================
        print("\n📋 FASE 1: Buscar dados da classificação")
        
        result = db.execute(text("""
            SELECT id, questao_id, modulos_escolhidos, descricoes_assunto_list
            FROM thsethub.classificacao_usuario
            WHERE id = :id
        """), {"id": classificacao_id})
        
        classificacao = result.fetchone()
        if not classificacao:
            print(f"❌ Classificação {classificacao_id} não encontrada!")
            return
        
        questao_id_original = classificacao.questao_id
        modulos_escolhidos = json.loads(classificacao.modulos_escolhidos) if classificacao.modulos_escolhidos else []
        descricoes_assunto = json.loads(classificacao.descricoes_assunto_list) if classificacao.descricoes_assunto_list else []
        
        print(f"   ✓ Questão ID original: {questao_id_original}")
        print(f"   ✓ Módulos: {modulos_escolhidos}")
        print(f"   ✓ Assuntos: {descricoes_assunto}")
        
        # Busca questão completa em trieduc
        print("\n📄 Buscar questão em trieduc.questoes")
        result = db.execute(text("""
            SELECT id, disciplina_id, enunciado, resolucao
            FROM trieduc.questoes
            WHERE id = :id
        """), {"id": questao_id_original})
        
        questao = result.fetchone()
        if not questao:
            print(f"❌ Questão {questao_id_original} não encontrada em trieduc!")
            return
        
        print(f"   ✓ Enunciado: {len(questao.enunciado or '')} caracteres")
        print(f"   ✓ Resolução: {len(questao.resolucao or '')} caracteres")
        
        # Busca alternativas
        print("\n📝 Buscar alternativas em trieduc.questao_alternativas")
        result = db.execute(text("""
            SELECT id, ordem, conteudo, correta
            FROM trieduc.questao_alternativas
            WHERE questao_id = :questao_id
            ORDER BY ordem
        """), {"questao_id": questao_id_original})
        
        alternativas = result.fetchall()
        print(f"   ✓ {len(alternativas)} alternativas encontradas")
        
        # Busca enunciado tratado
        print("\n📝 Buscar enunciado tratado em thsethub.questao_assuntos")
        result = db.execute(text("""
            SELECT enunciado_tratado
            FROM thsethub.questao_assuntos
            WHERE questao_id = :questao_id
            LIMIT 1
        """), {"questao_id": questao_id_original})
        
        enunciado_tratado_row = result.fetchone()
        enunciado_tratado = enunciado_tratado_row.enunciado_tratado if enunciado_tratado_row else None
        print(f"   ✓ Enunciado tratado: {len(enunciado_tratado or '')} caracteres")
        
        # ====================================================================
        # FASE 2: MAPEAMENTO
        # ====================================================================
        print("\n🗺️  FASE 2: Mapeamento de disciplina e assuntos")
        
        # Busca nome da disciplina em trieduc
        result = db.execute(text("""
            SELECT descricao
            FROM trieduc.disciplinas
            WHERE id = :id
        """), {"id": questao.disciplina_id})
        
        disciplina_row = result.fetchone()
        nome_disciplina = disciplina_row.descricao if disciplina_row else "Desconhecida"
        
        disc_id = mapear_disciplina_por_nome(nome_disciplina, db)
        if not disc_id:
            print("❌ Não foi possível mapear a disciplina!")
            return
        
        # Mapeia assuntos
        assuntos_mapeados = mapear_assuntos_por_nome(modulos_escolhidos, descricoes_assunto, db)
        
        if not assuntos_mapeados:
            print("⚠️  Nenhum assunto foi mapeado!")
        else:
            print(f"\n   ✓ {len(assuntos_mapeados)} assunto(s) mapeado(s):")
            for assu in assuntos_mapeados:
                print(f"      • assu_id={assu['assu_id']} - {assu['assu_descricao']}")
        
        # ====================================================================
        # FASE 3: PROCESSAR IMAGENS
        # ====================================================================
        print("\n🖼️  FASE 3: Processar imagens")
        
        enunciado_com_s3, metadados_imagens_enunciado = processar_imagens_html(
            questao.enunciado, questao_id_original, db, dry_run=dry_run
        )
        
        # Processar imagens das alternativas
        metadados_imagens_alternativas = []
        alternativas_processadas = []
        
        for alt in alternativas:
            alt_html, alt_metadados = processar_imagens_html(
                alt.conteudo, questao_id_original, db, dry_run=dry_run
            )
            alternativas_processadas.append({
                "ordem": alt.ordem,
                "texto": alt_html,
                "correta": alt.correta
            })
            metadados_imagens_alternativas.extend(alt_metadados)
        
        # Combina todos os metadados de imagens
        todos_metadados_imagens = metadados_imagens_enunciado + metadados_imagens_alternativas
        print(f"\n   ✓ Total de {len(todos_metadados_imagens)} imagens processadas")
        
        # ====================================================================
        # FASE 4: INSERIR NO BANCO (ou gerar SQLs se DRY-RUN)
        # ====================================================================
        print(f"\n💾 FASE 4: {'Inserir no banco' if not dry_run else 'Gerar SQLs de inserção'}")
        
        # Verifica se já foi migrada
        if not dry_run:
            print("\n   🔍 Verificando se questão já foi migrada...")
            result_check = db.execute(text("""
                SELECT questao_id 
                FROM recursos_didaticos.rd_questoes 
                WHERE recurso_origem_id = 6 
                  AND recurso_origem_chave = :chave
                LIMIT 1
            """), {"chave": str(questao_id_original)})
            
            questao_existente = result_check.fetchone()
            if questao_existente:
                print(f"   ⚠️  Questão já foi migrada! ID: {questao_existente.questao_id}")
                print(f"   ℹ️  Abortando migração para evitar duplicata.")
                raise DuplicataException(f"Questão {questao_id_original} já foi migrada (ID {questao_existente.questao_id})")
            
            print(f"   ✓ Questão ainda não foi migrada, prosseguindo...")
        
        novo_questao_id = None
        
        # ====================================================================
        # 1. INSERT em rd_questoes
        # ====================================================================
        print("\n-- 1. INSERT INTO rd_questoes")
        
        if not dry_run:
            # PRODUÇÃO: Executa INSERT de verdade
            result = db.execute(text("""
            INSERT INTO recursos_didaticos.rd_questoes (
                detentor_direito_autoral_id,
                questao_fonte_id,
                questao_enunciado,
                disc_id,
                questao_ano,
                questao_enem,
                video_id_comentario,
                questao_comentario_texto,
                questao_tipo,
                seg_codigo,
                recurso_origem_id,
                recurso_origem_chave,
                questao_ativa,
                questao_data_criacao,
                quantidade_palavras,
                quantidade_letras,
                quantidade_letra_a,
                questao_tri_a,
                questao_tri_b,
                questao_tri_g,
                questao_saeb,
                questao_id_origem,
                questao_psas,
                questao_usua_id_autor,
                questao_enunciado_texto_limpo
            ) VALUES (
                NULL,
                167,
                :enunciado,
                :disc_id,
                2026,
                0,
                NULL,
                :resolucao,
                'M',
                '04',
                6,
                :recurso_origem_chave,
                1,
                NOW(),
                NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                :enunciado_limpo
            )
            """), {
                "enunciado": enunciado_com_s3,
                "disc_id": disc_id,
                "resolucao": questao.resolucao,
                "recurso_origem_chave": str(questao_id_original),
                "enunciado_limpo": enunciado_tratado
            })
            
            novo_questao_id = result.lastrowid
            print(f"   ✅ Questão inserida! ID gerado: {novo_questao_id}")
        else:
            # DRY-RUN: Apenas mostra SQL
            sql_rd_questoes = f"""
            INSERT INTO recursos_didaticos.rd_questoes (
                detentor_direito_autoral_id,
                questao_fonte_id,
                questao_enunciado,
                disc_id,
                questao_ano,
                questao_enem,
                video_id_comentario,
                questao_comentario_texto,
                questao_tipo,
                seg_codigo,
                recurso_origem_id,
                recurso_origem_chave,
                questao_ativa,
                questao_data_criacao,
                quantidade_palavras,
                quantidade_letras,
                quantidade_letra_a,
                questao_tri_a,
                questao_tri_b,
                questao_tri_g,
                questao_saeb,
                questao_id_origem,
                questao_psas,
                questao_usua_id_autor,
                questao_enunciado_texto_limpo
            ) VALUES (
                NULL,  -- detentor_direito_autoral_id
                167,  -- questao_fonte_id (Trieduc)
                {repr(enunciado_com_s3)},  -- questao_enunciado
                {disc_id},  -- disc_id
                2026,  -- questao_ano
                0,  -- questao_enem
                NULL,  -- video_id_comentario
                {repr(questao.resolucao) if questao.resolucao else 'NULL'},  -- questao_comentario_texto
                'M',  -- questao_tipo
                '04',  -- seg_codigo (Ensino Médio)
                6,  -- recurso_origem_id (thsethub)
                '{questao_id_original}',  -- recurso_origem_chave
                1,  -- questao_ativa
                NOW(),  -- questao_data_criacao
                NULL,  -- quantidade_palavras
                NULL,  -- quantidade_letras
                NULL,  -- quantidade_letra_a
                NULL,  -- questao_tri_a
                NULL,  -- questao_tri_b
                NULL,  -- questao_tri_g
                NULL,  -- questao_saeb
                NULL,  -- questao_id_origem
                NULL,  -- questao_psas
                NULL,  -- questao_usua_id_autor
                {repr(enunciado_tratado) if enunciado_tratado else 'NULL'}  -- questao_enunciado_texto_limpo
            );

            SET @novo_questao_id = LAST_INSERT_ID();
            """
            print(sql_rd_questoes)
        
        # ====================================================================
        # 2. INSERT em rd_questoes_assuntos
        # ====================================================================
        if assuntos_mapeados:
            print("\n-- 2. INSERT INTO rd_questoes_assuntos")
            
            if not dry_run:
                # PRODUÇÃO: Executa INSERTs de verdade
                for assu in assuntos_mapeados:
                    db.execute(text("""
                    INSERT INTO recursos_didaticos.rd_questoes_assuntos (questao_id, assu_id, questao_assu_principal)
                    VALUES (:questao_id, :assu_id, 1)
                    """), {"questao_id": novo_questao_id, "assu_id": assu['assu_id']})
                    print(f"   ✅ Assunto inserido: {assu['assu_descricao']}")
            else:
                # DRY-RUN: Mostra SQLs
                for assu in assuntos_mapeados:
                    sql_assu = f"""
                    INSERT INTO recursos_didaticos.rd_questoes_assuntos (
                        questao_id, assu_id, questao_assu_principal
                    ) VALUES (
                        @novo_questao_id, {assu['assu_id']}, 1
                    );
                    -- Assunto: {assu['assu_descricao']}
                    """
                    print(sql_assu)
        
        # ====================================================================
        # 3. INSERT em rd_questoes_alternativas
        # ====================================================================
        print("\n-- 3. INSERT INTO rd_questoes_alternativas")
        prefixos = ['A', 'B', 'C', 'D', 'E']
        
        if not dry_run:
            # PRODUÇÃO: Executa INSERTs de verdade
            for alt in alternativas_processadas:
                prefixo = prefixos[alt['ordem'] - 1] if alt['ordem'] <= 5 else str(alt['ordem'])
                db.execute(text("""
                INSERT INTO recursos_didaticos.rd_questoes_alternativas 
                (questao_id, questao_alternativa_prefixo, questao_alternativa_texto, questao_alternativa_correta)
                VALUES (:questao_id, :prefixo, :texto, :correta)
                """), {
                    "questao_id": novo_questao_id,
                    "prefixo": prefixo,
                    "texto": alt['texto'],
                    "correta": 1 if alt['correta'] else 0
                })
                print(f"   ✅ Alternativa {prefixo} inserida (correta={alt['correta']})")
        else:
            # DRY-RUN: Mostra SQLs
            for alt in alternativas_processadas:
                prefixo = prefixos[alt['ordem'] - 1] if alt['ordem'] <= 5 else str(alt['ordem'])
                sql_alt = f"""
                INSERT INTO recursos_didaticos.rd_questoes_alternativas (
                    questao_id, questao_alternativa_prefixo, questao_alternativa_texto, questao_alternativa_correta
                ) VALUES (
                    @novo_questao_id, '{prefixo}', {repr(alt['texto'])}, {1 if alt['correta'] else 0}
                );
                """
                print(sql_alt)
        
        # ====================================================================
        # 4. INSERT em rd_questoes_imagens
        # ====================================================================
        if todos_metadados_imagens:
            print("\n-- 4. INSERT INTO rd_questoes_imagens")
            
            if not dry_run:
                # PRODUÇÃO: Executa INSERTs de verdade
                for idx, img_meta in enumerate(todos_metadados_imagens, 1):
                    db.execute(text("""
                    INSERT INTO recursos_didaticos.rd_questoes_imagens (
                        questao_id, questao_imagem_baixa_resolucao, questao_imagem_alta_resolucao,
                        questao_imagem_data_upload, questao_imagem_largura, questao_imagem_altura,
                        questao_imagem_tamanho, created_at, updated_at
                    ) VALUES (
                        :questao_id, :url_baixa, :url_alta, NOW(), :largura, :altura, :tamanho, NOW(), NOW()
                    )
                    """), {
                        "questao_id": novo_questao_id,
                        "url_baixa": img_meta['url_baixa'],
                        "url_alta": img_meta['url_alta'],
                        "largura": img_meta['largura'],
                        "altura": img_meta['altura'],
                        "tamanho": img_meta['tamanho']
                    })
                    print(f"   ✅ Imagem {idx} inserida: {img_meta['largura']}x{img_meta['altura']}px")
            else:
                # DRY-RUN: Mostra SQLs
                for idx, img_meta in enumerate(todos_metadados_imagens, 1):
                    sql_img = f"""
                    INSERT INTO recursos_didaticos.rd_questoes_imagens (
                        questao_id, 
                        questao_imagem_baixa_resolucao, 
                        questao_imagem_alta_resolucao,
                        questao_imagem_data_upload,
                        questao_imagem_largura,
                        questao_imagem_altura,
                        questao_imagem_tamanho,
                        created_at,
                        updated_at
                    ) VALUES (
                        @novo_questao_id,
                        '{img_meta['url_baixa']}',  -- baixa resolução
                        '{img_meta['url_alta']}',  -- alta resolução
                        NOW(),  -- data_upload
                        {img_meta['largura']},  -- largura (alta)
                        {img_meta['altura']},  -- altura (alta)
                        {img_meta['tamanho']},  -- tamanho em bytes (alta)
                        NOW(),  -- created_at
                        NOW()  -- updated_at
                    );
                    -- Imagem {idx}: {img_meta['largura']}x{img_meta['altura']}px, {img_meta['tamanho']} bytes
                    """
                    print(sql_img)
        
        # ====================================================================
        # 5. Marcar classificação como migrada e COMMIT
        # ====================================================================
        if not dry_run:
            print("\n-- 5. Marcar classificação como migrada")
            db.execute(text("""
            UPDATE thsethub.classificacao_usuario
            SET migrada = TRUE
            WHERE id = :classificacao_id
            """), {"classificacao_id": classificacao_id})
            print(f"   ✅ Classificação {classificacao_id} marcada como migrada")
            
            # COMMIT
            db.commit()
            print("\n🎉 COMMIT realizado! Migração concluída com sucesso!")
        
        
        # ====================================================================
        # RESUMO FINAL
        # ====================================================================
        print("\n" + "=" * 80)
        print("✅ RESUMO DA MIGRAÇÃO")
        print("=" * 80)
        print(f"Questão ID original (trieduc): {questao_id_original}")
        if novo_questao_id:
            print(f"Questão ID novo (recursos_didaticos): {novo_questao_id}")
        print(f"Disciplina: {nome_disciplina} (disc_id={disc_id})")
        print(f"Assuntos mapeados: {len(assuntos_mapeados)}")
        print(f"Alternativas: {len(alternativas_processadas)}")
        print(f"Imagens: {len(todos_metadados_imagens)}")
        print(f"Modo: {'PRODUÇÃO (executado e commitado!)' if not dry_run else 'DRY-RUN (não executado)'}")
        print("=" * 80)
        
    finally:
        db.close()


# ============================================================================
# FUNÇÕES DE BUSCA E MIGRAÇÃO EM LOTE
# ============================================================================

def buscar_classificacoes_para_migrar(
    tipo_acao: Optional[str] = None,
    disciplina: Optional[str] = None,
    limite: int = 100
) -> List[int]:
    """
    Busca classificações que precisam ser migradas com filtros opcionais.
    
    Args:
        tipo_acao: Filtro por tipo_acao (classificacao_nova, correcao, verificacao)
        disciplina: Filtro por nome da disciplina
        limite: Máximo de classificações a retornar
    
    Returns:
        Lista de IDs de classificacao_usuario
    """
    db = SessionLocal()
    try:
        print("\n🔍 BUSCAR CLASSIFICAÇÕES PARA MIGRAR")
        print("=" * 80)
        
        # Monta query com JOIN se precisar filtrar por disciplina
        if disciplina:
            # JOIN com questões e disciplinas para filtrar por nome da disciplina
            from_clause = """
                FROM thsethub.classificacao_usuario cu
                JOIN trieduc.questoes q ON cu.questao_id = q.id
                JOIN trieduc.disciplinas d ON q.disciplina_id = d.id
            """
        else:
            from_clause = "FROM thsethub.classificacao_usuario cu"
        
        # Monta WHERE clauses
        where_clauses = [
            "(cu.migrada IS NULL OR cu.migrada = FALSE)",
            "cu.modulos_escolhidos IS NOT NULL",
            "cu.modulos_escolhidos != '[]'"
        ]
        
        params = {}
        
        if tipo_acao:
            where_clauses.append("cu.tipo_acao = :tipo_acao")
            params["tipo_acao"] = tipo_acao
            print(f"   Filtro: tipo_acao = '{tipo_acao}'")
        
        if disciplina:
            where_clauses.append("d.descricao LIKE :disciplina")
            params["disciplina"] = f"%{disciplina}%"
            print(f"   Filtro: disciplina LIKE '%{disciplina}%'")
        
        where_sql = " AND ".join(where_clauses)
        
        # Monta query completa
        if disciplina:
            query = f"""
                SELECT cu.id, cu.questao_id, d.descricao as disciplina, cu.tipo_acao
                {from_clause}
                WHERE {where_sql}
                ORDER BY cu.id
                LIMIT :limite
            """
        else:
            query = f"""
                SELECT cu.id, cu.questao_id, cu.tipo_acao
                {from_clause}
                WHERE {where_sql}
                ORDER BY cu.id
                LIMIT :limite
            """
        
        params["limite"] = limite
        
        result = db.execute(text(query), params)
        classificacoes = result.fetchall()
        
        print(f"\n   ✓ {len(classificacoes)} classificação(ões) encontrada(s)")
        
        if classificacoes:
            print("\n   Classificações encontradas:")
            for c in classificacoes[:10]:  # Mostra até 10
                if disciplina:
                    print(f"      • ID {c.id:5d} - Questão {c.questao_id:5d} - {c.disciplina:20s} - {c.tipo_acao}")
                else:
                    print(f"      • ID {c.id:5d} - Questão {c.questao_id:5d} - {c.tipo_acao}")
            
            if len(classificacoes) > 10:
                print(f"      ... e mais {len(classificacoes) - 10}")
        
        print("=" * 80)
        
        return [c.id for c in classificacoes]
    
    finally:
        db.close()


def migrar_questoes_em_lote(
    classificacao_ids: List[int],
    dry_run: bool = True
):
    """
    Migra múltiplas questões em lote.
    
    Args:
        classificacao_ids: Lista de IDs de classificacao_usuario
        dry_run: Se True, não executa INSERTs
    """
    total = len(classificacao_ids)
    sucesso = 0
    falhas = 0
    duplicadas = 0
    
    print("\n" + "=" * 80)
    print(f"{'DRY-RUN: ' if dry_run else ''}MIGRAÇÃO EM LOTE - {total} questões")
    print("=" * 80)
    
    for idx, classificacao_id in enumerate(classificacao_ids, 1):
        print(f"\n{'#' * 80}")
        print(f"# {idx}/{total} - Classificação ID {classificacao_id}")
        print(f"{'#' * 80}")
        
        try:
            migrar_questao_completa(classificacao_id, dry_run=dry_run)
            sucesso += 1
        except DuplicataException:
            duplicadas += 1
            print(f"⚠️  Questão já migrada anteriormente (duplicata)")
        except Exception as e:
            falhas += 1
            print(f"❌ ERRO ao migrar: {e}")
            import traceback
            traceback.print_exc()
        
        # Pausa entre migrações em modo produção
        if not dry_run and idx < total:
            import time
            time.sleep(0.5)
    
    # Relatório final
    print("\n" + "=" * 80)
    print("📊 RELATÓRIO FINAL DA MIGRAÇÃO")
    print("=" * 80)
    print(f"Total processado: {total}")
    print(f"✅ Sucesso: {sucesso}")
    print(f"⚠️  Duplicadas (já migradas): {duplicadas}")
    print(f"❌ Falhas: {falhas}")
    print(f"Modo: {'DRY-RUN (não executado)' if dry_run else 'PRODUÇÃO (executado e commitado)'}")
    print("=" * 80)


class DuplicataException(Exception):
    """Exceção para indicar que a questão já foi migrada"""
    pass


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Migra questões do trieduc para recursos_didaticos',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos de uso:
  
  # Migrar UMA questão específica (DRY-RUN)
  python migrar_questao_completa.py --id 13
  
  # Migrar UMA questão em PRODUÇÃO
  python migrar_questao_completa.py --id 13 --producao
  
  # Migrar TODAS as classificações novas (DRY-RUN)
  python migrar_questao_completa.py --tipo-acao classificacao_nova
  
  # Migrar apenas Matemática (DRY-RUN)
  python migrar_questao_completa.py --disciplina Matemática
  
  # Migrar correções de Português em PRODUÇÃO
  python migrar_questao_completa.py --tipo-acao correcao --disciplina Português --producao
  
  # Limitar a 50 questões
  python migrar_questao_completa.py --tipo-acao classificacao_nova --limite 50
        """
    )
    
    parser.add_argument('--id', type=int, help='ID específico de classificacao_usuario')
    parser.add_argument('--tipo-acao', choices=['classificacao_nova', 'correcao', 'verificacao'],
                        help='Filtrar por tipo de ação')
    parser.add_argument('--disciplina', type=str, help='Filtrar por nome da disciplina')
    parser.add_argument('--limite', type=int, default=100, help='Máximo de questões a migrar (padrão: 100)')
    parser.add_argument('--producao', action='store_true', help='Executar em modo PRODUÇÃO (insere no banco)')
    
    args = parser.parse_args()
    
    dry_run = not args.producao
    
    # Validação
    if not args.id and not args.tipo_acao and not args.disciplina:
        parser.error("Você deve especificar --id OU --tipo-acao OU --disciplina")
    
    # Modo PRODUÇÃO - confirmação
    if not dry_run:
        print("\n⚠️  ATENÇÃO: Modo PRODUÇÃO ativado! Os dados SERÃO inseridos no banco!")
        resposta = input("Deseja continuar? (sim/não): ")
        if resposta.lower() != 'sim':
            print("Cancelado.")
            sys.exit(0)
    
    # Migração de UMA questão específica
    if args.id:
        print(f"\n🎯 Modo: Migração de UMA questão (ID {args.id})")
        migrar_questao_completa(args.id, dry_run=dry_run)
    
    # Migração EM LOTE
    else:
        print(f"\n📦 Modo: Migração EM LOTE")
        classificacao_ids = buscar_classificacoes_para_migrar(
            tipo_acao=args.tipo_acao,
            disciplina=args.disciplina,
            limite=args.limite
        )
        
        if not classificacao_ids:
            print("\n⚠️  Nenhuma classificação encontrada com os filtros especificados.")
            sys.exit(0)
        
        migrar_questoes_em_lote(classificacao_ids, dry_run=dry_run)
