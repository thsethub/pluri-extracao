import base64
import hashlib
import mimetypes
import os
import re
import sys
from io import BytesIO
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import boto3
import requests
from bs4 import BeautifulSoup
from PIL import Image

# Adiciona o diretório src ao sys.path para importar os módulos
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import PgSessionLocal, SessionLocal
from src.database.pg_usuario_models import ClassificacaoUsuarioModel
from src.database.pg_modulo_models import HabilidadeModuloModel
from src.database.models import QuestaoModel, DisciplinaModel
from src.config import settings

# Configuração S3 
_S3_BUCKET  = settings.aws_s3_bucket
_S3_PREFIX  = settings.aws_s3_prefix
_AWS_REGION = settings.aws_region

def _s3_client():
    return boto3.client(
        "s3",
        region_name=_AWS_REGION,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )

def _s3_url_publica(s3_key: str) -> str:
    return f"https://{_S3_BUCKET}.s3.{_AWS_REGION}.amazonaws.com/{s3_key}"

def _extrair_metadados_imagem(dados: bytes) -> dict:
    """Extrai metadados da imagem (largura, altura, formato, tamanho)."""
    try:
        img = Image.open(BytesIO(dados))
        return {
            "largura": img.width,
            "altura": img.height,
            "formato": img.format or "UNKNOWN",
            "tamanho_bytes": len(dados),
            "tamanho_kb": round(len(dados) / 1024, 2),
            "tamanho_mb": round(len(dados) / (1024 * 1024), 2),
        }
    except Exception as e:
        print(f"    [AVISO] Não foi possível extrair metadados da imagem: {e}")
        return {
            "largura": None,
            "altura": None,
            "formato": "UNKNOWN",
            "tamanho_bytes": len(dados),
            "tamanho_kb": round(len(dados) / 1024, 2),
            "tamanho_mb": round(len(dados) / (1024 * 1024), 2),
        }

def _upload_bytes(dados: bytes, nome_arquivo: str, content_type: str) -> tuple[str, dict]:
    """Faz upload de bytes para o S3 e retorna a URL pública + metadados da imagem."""
    s3_key = f"{_S3_PREFIX}/{nome_arquivo}"
    _s3_client().put_object(
        Bucket=_S3_BUCKET,
        Key=s3_key,
        Body=dados,
        ContentType=content_type,
    )
    url = _s3_url_publica(s3_key)
    metadados = _extrair_metadados_imagem(dados)
    return url, metadados

def _detectar_tipo_imagem_por_bytes(dados: bytes) -> str:
    """Detecta o tipo de imagem pelos magic numbers (primeiros bytes)."""
    if dados.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    elif dados.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    elif dados.startswith(b'GIF87a') or dados.startswith(b'GIF89a'):
        return '.gif'
    elif dados.startswith(b'RIFF') and dados[8:12] == b'WEBP':
        return '.webp'
    elif dados.startswith(b'<svg') or dados.startswith(b'<?xml'):
        return '.svg'
    elif dados.startswith(b'BM'):
        return '.bmp'
    # Padrão para imagens não identificadas
    return '.jpg'

def _ext_de_content_type(content_type: str, url: str = "", dados: bytes = None) -> str:
    """Determina a extensão do arquivo a partir do content-type, URL ou bytes."""
    ext = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ""
    # mimetypes retorna .jpe para image/jpeg em alguns sistemas
    if ext in (".jpe", ".jpeg"):
        return ".jpg"
    if ext and ext != ".bin":
        return ext
    
    # Fallback 1: tenta pegar extensão da URL
    url_ext = Path(urlparse(url).path).suffix.lower()
    if url_ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"):
        return url_ext
    
    # Fallback 2: detecta pelo conteúdo (magic numbers)
    if dados:
        detected = _detectar_tipo_imagem_por_bytes(dados)
        if detected:
            return detected
    
    # Fallback 3: assume JPG para imagens não identificadas (melhor que .bin)
    if content_type and 'image' in content_type.lower():
        return '.jpg'
    
    return ext or ".bin"

# ── Processamento de imagens base64 ───────────────────────────
_BASE64_URI = re.compile(r'data:(?P<mime>image/[^;]+);base64,(?P<dados>[A-Za-z0-9+/=]+)', re.IGNORECASE)

def _processar_base64(html: str, questao_id: int, metadados_lista: list = None) -> str:
    """Substitui todos os data URIs base64 do HTML por URLs do S3."""
    if metadados_lista is None:
        metadados_lista = []
    
    def substituir(match):
        mime   = match.group("mime")
        dados  = base64.b64decode(match.group("dados"))
        digest = hashlib.md5(dados).hexdigest()[:8]
        ext    = _ext_de_content_type(mime, dados=dados)
        nome   = f"questao_{questao_id}_base64_{digest}{ext}"
        url, metadados = _upload_bytes(dados, nome, mime)
        
        # Adiciona informações à lista de metadados
        metadados_lista.append({
            "origem": "base64",
            "nome_arquivo": nome,
            "url_s3": url,
            **metadados
        })
        
        print(f"    [base64] {nome} → {url}")
        print(f"             {metadados['largura']}x{metadados['altura']}px, {metadados['tamanho_kb']}KB, formato: {metadados['formato']}")
        return url
    
    return _BASE64_URI.sub(substituir, html)

# ── Processamento de imagens por URL (<img src="...">) ─────────
def _processar_img_tags(html: str, questao_id: int, metadados_lista: list = None) -> str:
    """Baixa as imagens referenciadas por <img src> e substitui pelos URLs do S3."""
    if metadados_lista is None:
        metadados_lista = []
    
    soup   = BeautifulSoup(html, "html.parser")
    mapeamento: dict[str, str] = {}

    for idx, tag in enumerate(soup.find_all("img")):
        src = tag.get("src", "")
        if not src or src.startswith("data:") or "amazonaws.com" in src:
            continue                    # base64 já tratado, ou já subiu ao S3
        if src in mapeamento:
            tag["src"] = mapeamento[src]
            continue

        try:
            resp = requests.get(src, timeout=15)
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "image/jpeg")
            
            # Detecta extensão usando content-type, URL e bytes da imagem
            ext = _ext_de_content_type(content_type, src, resp.content)
            
            caminho = urlparse(src).path
            nome_orig = Path(caminho).stem or f"img_{idx}"
            digest = hashlib.md5(resp.content).hexdigest()[:8]
            nome = f"questao_{questao_id}_{nome_orig}_{digest}{ext}"
            
            # Ajusta content-type se necessário baseado na extensão detectada
            if ext == '.jpg' or ext == '.jpeg':
                content_type = 'image/jpeg'
            elif ext == '.png':
                content_type = 'image/png'
            elif ext == '.gif':
                content_type = 'image/gif'
            elif ext == '.webp':
                content_type = 'image/webp'
            elif ext == '.svg':
                content_type = 'image/svg+xml'
            
            url, metadados = _upload_bytes(resp.content, nome, content_type)
            
            # Adiciona informações à lista de metadados
            metadados_lista.append({
                "origem": "img_tag",
                "url_original": src,
                "nome_arquivo": nome,
                "url_s3": url,
                **metadados
            })
            
            print(f"    [img_tag] {src}")
            print(f"              → {url}")
            print(f"              {metadados['largura']}x{metadados['altura']}px, {metadados['tamanho_kb']}KB, formato: {metadados['formato']}")
            
            mapeamento[src] = url
            tag["src"] = url
        except Exception as exc:
            print(f"    [img_tag] ERRO ao baixar {src}: {exc}")

    return str(soup)

# Função principal de processamento de imagens do enunciado
def processar_imagens_questao(questao_id: int, enunciado: str, tipo_imagem: str) -> tuple[str, list]:
    """
    Processa as imagens do enunciado conforme o tipo detectado.
    Retorna:
        - HTML com os src/data-URIs substituídos pelas URLs do S3
        - Lista de metadados de todas as imagens processadas
    """
    metadados_imagens = []
    html = enunciado
    
    if "base64" in tipo_imagem:
        html = _processar_base64(html, questao_id, metadados_imagens)
    if "img_tag" in tipo_imagem:
        html = _processar_img_tags(html, questao_id, metadados_imagens)
    
    return html, metadados_imagens

def buscar_questoes(tipo_acao: str = None, disciplina: str = None, limite: int = 10):

    # Se disciplina foi informada, busca os IDs das questões dessa disciplina no trieduc
    questao_ids_filtro = None
    if disciplina:
        db_trieduc = SessionLocal()
        try:
            disc = db_trieduc.query(DisciplinaModel).filter(
                DisciplinaModel.descricao.ilike(f"%{disciplina}%")
            ).first()
            if not disc:
                print(f"Disciplina '{disciplina}' não encontrada.")
                return []
            questao_ids_filtro = [
                q.id for q in db_trieduc.query(QuestaoModel.id).filter(
                    QuestaoModel.disciplina_id == disc.id
                ).all()
            ]
            if not questao_ids_filtro:
                print(f"Nenhuma questão encontrada para disciplina '{disc.descricao}'.")
                return []
            print(f"Filtrando por disciplina: {disc.descricao} (id={disc.id}, {len(questao_ids_filtro)} questões)")
        finally:
            db_trieduc.close()

    # Abre sessão com o banco thsethub
    db = PgSessionLocal()
    try:
        query = db.query(ClassificacaoUsuarioModel).filter(
            ClassificacaoUsuarioModel.migrada == False  # Somente registros não migrados
        )
        
        # Filtra por tipo de ação, se fornecido
        if tipo_acao:
            query = query.filter(ClassificacaoUsuarioModel.tipo_acao == tipo_acao)

        # Filtra por disciplina (via IDs de questões)
        if questao_ids_filtro is not None:
            query = query.filter(ClassificacaoUsuarioModel.questao_id.in_(questao_ids_filtro))

        # Busca em ordem de criação de indexação
        registros = query.order_by(ClassificacaoUsuarioModel.id).limit(limite).all()

        return registros
    finally:
        # Fecha a sessão ao finalizar a consulta
        db.close()

# Padrões para detectar imagem em texto HTML
_IMG_TAG = re.compile(r'<img\b', re.IGNORECASE)
_BASE64 = re.compile(r'data:image/[^;]+;base64,', re.IGNORECASE)

def _detectar_tipo_imagem(texto: str) -> Optional[str]:
    #Retorna o tipo de imagem encontrado no texto: 'img_tag', 'base64', 'img_tag+base64' ou None.
    if not texto:
        return None
    tem_img = bool(_IMG_TAG.search(texto))
    tem_base64 = bool(_BASE64.search(texto))
    if tem_img and tem_base64:
        return "img_tag+base64"
    if tem_img:
        return "img_tag"
    if tem_base64:
        return "base64"
    return None

def registro_possui_imagem(registro: ClassificacaoUsuarioModel) -> Optional[str]:
    #Busca a questão na tabela questoes (trieduc) e retorna o tipo de imagem encontrado no enunciado.
    db = SessionLocal()
    try:
        questao = db.query(QuestaoModel).filter(
            QuestaoModel.id == registro.questao_id
        ).first()

        if not questao:
            return None

        return _detectar_tipo_imagem(questao.enunciado)
    finally:
        db.close()

def buscar_questao_completa(questao_id: int) -> Optional[dict]:
    """
    Busca todos os dados de uma questão, incluindo alternativas.
    Retorna um dicionário com a estrutura completa da questão.
    """
    db_trieduc = SessionLocal()
    try:
        from src.database.models import QuestaoAlternativaModel
        
        # Busca a questão com todos os relacionamentos
        questao = db_trieduc.query(QuestaoModel).filter(
            QuestaoModel.id == questao_id
        ).first()
        
        if not questao:
            print(f"Questão {questao_id} não encontrada no banco trieduc")
            return None
        
        # Busca as alternativas da questão
        alternativas = db_trieduc.query(QuestaoAlternativaModel).filter(
            QuestaoAlternativaModel.questao_id == questao_id
        ).order_by(QuestaoAlternativaModel.ordem).all()
        
        # Monta o dicionário com todos os dados
        dados_questao = {
            "id": questao.id,
            "questao_id": questao.questao_id,
            "enunciado": questao.enunciado,
            "texto_base": questao.texto_base,
            "resolucao": questao.resolucao,
            "origem": questao.origem,
            "tipo": questao.tipo,
            "tipo_imagem": _detectar_tipo_imagem(questao.enunciado),
            # Dados de relacionamentos
            "ano_id": questao.ano_id,
            "ano_descricao": questao.ano.descricao if questao.ano else None,
            "disciplina_id": questao.disciplina_id,
            "disciplina_descricao": questao.disciplina.descricao if questao.disciplina else None,
            "habilidade_id": questao.habilidade_id,
            "habilidade_sigla": questao.habilidade.sigla if questao.habilidade else None,
            "habilidade_descricao": questao.habilidade.descricao if questao.habilidade else None,
            # Alternativas
            "alternativas": [
                {
                    "id": alt.id,
                    "qa_id": alt.qa_id,
                    "ordem": alt.ordem,
                    "conteudo": alt.conteudo,
                    "correta": bool(alt.correta),
                }
                for alt in alternativas
            ]
        }
        
        return dados_questao
        
    finally:
        db_trieduc.close()

def buscar_modulos_assuntos_classificacao(habilidade_modulo_ids: list) -> list:
    """
    Busca os detalhes completos dos módulos e assuntos escolhidos pelo usuário.
    Retorna uma lista com {modulo, descricao_assunto, habilidade_descricao}
    """
    if not habilidade_modulo_ids:
        return []
    
    db_thsethub = PgSessionLocal()
    try:
        modulos_detalhes = db_thsethub.query(HabilidadeModuloModel).filter(
            HabilidadeModuloModel.id.in_(habilidade_modulo_ids)
        ).all()
        
        return [
            {
                "id": modulo.id,
                "modulo": modulo.modulo,
                "descricao_assunto": modulo.descricao,
                "habilidade_id": modulo.habilidade_id,
                "habilidade_descricao": modulo.habilidade_descricao,
                "area": modulo.area,
                "disciplina": modulo.disciplina,
            }
            for modulo in modulos_detalhes
        ]
    finally:
        db_thsethub.close()

def buscar_registro_classificacao_com_questao(registro_id: int = None, questao_id: int = None) -> Optional[dict]:
    """
    Busca um registro de classificacao_usuario junto com todos os dados da questão.
    Pode buscar por registro_id ou questao_id.
    Retorna um dicionário unificado com classificação + questão completa.
    """
    db_thsethub = PgSessionLocal()
    try:
        # Busca o registro de classificação
        if registro_id:
            registro = db_thsethub.query(ClassificacaoUsuarioModel).filter(
                ClassificacaoUsuarioModel.id == registro_id
            ).first()
        elif questao_id:
            registro = db_thsethub.query(ClassificacaoUsuarioModel).filter(
                ClassificacaoUsuarioModel.questao_id == questao_id,
                ClassificacaoUsuarioModel.migrada == False
            ).first()
        else:
            print("Informe registro_id ou questao_id")
            return None
        
        if not registro:
            print(f"Registro de classificação não encontrado")
            return None
        
        # Busca os dados completos da questão
        dados_questao = buscar_questao_completa(registro.questao_id)
        
        if not dados_questao:
            return None
        
        # Busca os detalhes dos módulos e assuntos escolhidos
        modulos_assuntos = buscar_modulos_assuntos_classificacao(registro.habilidade_modulo_ids or [])
        
        # Monta o dicionário unificado
        dados_completos = {
            "classificacao": {
                "id": registro.id,
                "usuario_id": registro.usuario_id,
                "questao_id": registro.questao_id,
                "habilidade_id": registro.habilidade_id,
                "tipo_acao": registro.tipo_acao,
                # Dados brutos (legado)
                "modulos_escolhidos": registro.modulos_escolhidos,
                "classificacoes_trieduc_list": registro.classificacoes_trieduc_list,
                "descricoes_assunto_list": registro.descricoes_assunto_list,
                "habilidade_modulo_ids": registro.habilidade_modulo_ids,
                # Dados estruturados (novos)
                "modulos_assuntos_detalhados": modulos_assuntos,
                "classificacao_extracao": registro.classificacao_extracao,
                "observacao": registro.observacao,
                "created_at": registro.created_at,
                "migrada": registro.migrada,
            },
            "questao": dados_questao
        }
        
        return dados_completos
        
    finally:
        db_thsethub.close()

if __name__ == "__main__":
    import json
    
    print("=" * 80)
    print("TESTE: Buscar questão completa com classificação")
    print("=" * 80)
    
    # Primeiro, busca registros não migrados que tenham imagem
    print("\n1. Buscando registros não migrados com imagem...")
    registros = buscar_questoes(limite=10)
    
    registro_com_imagem = None
    for reg in registros:
        tipo_img = registro_possui_imagem(reg)
        if tipo_img:
            registro_com_imagem = reg
            print(f"   ✓ Encontrado: registro_id={reg.id}, questao_id={reg.questao_id}, tipo_imagem={tipo_img}")
            break
        else:
            print(f"   - Registro {reg.id} (questão {reg.questao_id}): sem imagem")
    
    if not registro_com_imagem:
        print("\n   ⚠ Nenhum registro com imagem encontrado nos primeiros 10 registros")
        print("   Usando questão conhecida com imagem: questao_id=15684")
        # Tenta buscar direto pela questão conhecida
        dados = buscar_registro_classificacao_com_questao(questao_id=15684)
    else:
        print(f"\n2. Buscando dados completos do registro {registro_com_imagem.id}...")
        dados = buscar_registro_classificacao_com_questao(registro_id=registro_com_imagem.id)
    
    if dados:
        print("\n" + "=" * 80)
        print("RESULTADO: Dados completos da questão com classificação")
        print("=" * 80)
        
        # Exibe dados da classificação
        print("\n📋 CLASSIFICAÇÃO:")
        print(f"   ID: {dados['classificacao']['id']}")
        print(f"   Usuário: {dados['classificacao']['usuario_id']}")
        print(f"   Tipo de ação: {dados['classificacao']['tipo_acao']}")
        print(f"   Migrada: {dados['classificacao']['migrada']}")
        
        # Exibe módulos e assuntos detalhados
        modulos_assuntos = dados['classificacao']['modulos_assuntos_detalhados']
        if modulos_assuntos:
            print(f"\n   📚 MÓDULOS E ASSUNTOS ESCOLHIDOS ({len(modulos_assuntos)}):")
            for ma in modulos_assuntos:
                print(f"      • Módulo: {ma['modulo']}")
                print(f"        Assunto: {ma['descricao_assunto']}")
                print(f"        Habilidade TriEduc: {ma['habilidade_descricao']} (id={ma['habilidade_id']})")
                print()
        
        # Exibe dados da questão
        print("📝 QUESTÃO:")
        print(f"   ID: {dados['questao']['id']}")
        print(f"   Questão ID: {dados['questao']['questao_id']}")
        print(f"   Disciplina: {dados['questao']['disciplina_descricao']} (id={dados['questao']['disciplina_id']})")
        print(f"   Ano: {dados['questao']['ano_descricao']} (id={dados['questao']['ano_id']})")
        
        # Mostra habilidade corretamente
        hab_display = dados['questao']['habilidade_descricao'] or dados['questao']['habilidade_sigla'] or "N/A"
        print(f"   Habilidade: {hab_display} (id={dados['questao']['habilidade_id']})")
        
        print(f"   Origem: {dados['questao']['origem']}")
        print(f"   Tipo: {dados['questao']['tipo']}")
        print(f"   Tipo de imagem: {dados['questao']['tipo_imagem']}")
        print(f"   Enunciado (primeiros 200 chars): {dados['questao']['enunciado'][:200] if dados['questao']['enunciado'] else 'N/A'}...")
        
        # Exibe alternativas
        print(f"\n✓ ALTERNATIVAS ({len(dados['questao']['alternativas'])}):")
        for alt in dados['questao']['alternativas']:
            correta_marca = "✓" if alt['correta'] else " "
            conteudo_preview = alt['conteudo'][:80] if alt['conteudo'] else "N/A"
            print(f"   [{correta_marca}] {alt['ordem']}: {conteudo_preview}...")
        
        # Salva JSON completo para análise
        output_path = Path(__file__).parent.parent / "reports" / "teste_questao_completa.json"
        output_path.parent.mkdir(exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(dados, f, indent=2, ensure_ascii=False, default=str)
        print(f"\n💾 Dados completos salvos em: {output_path}")
        
        # Se tem imagem, processa e mostra as URLs
        if dados['questao']['tipo_imagem']:
            print("\n" + "=" * 80)
            print("PROCESSANDO IMAGENS...")
            print("=" * 80)
            enunciado_novo, metadados_imagens = processar_imagens_questao(
                dados['questao']['id'],
                dados['questao']['enunciado'],
                dados['questao']['tipo_imagem']
            )
            
            print(f"\n✓ {len(metadados_imagens)} imagem(ns) processada(s) e enviada(s) para S3")
            print("\n📊 RESUMO DOS METADADOS:")
            for i, meta in enumerate(metadados_imagens, 1):
                print(f"\n   Imagem {i}:")
                print(f"   • URL S3: {meta['url_s3']}")
                print(f"   • Dimensões: {meta['largura']}x{meta['altura']}px")
                print(f"   • Tamanho: {meta['tamanho_kb']}KB ({meta['tamanho_bytes']} bytes)")
                print(f"   • Formato: {meta['formato']}")
                if meta.get('url_original'):
                    print(f"   • URL original: {meta['url_original'][:80]}...")
    else:
        print("\n❌ Não foi possível buscar dados da questão")