"""
Orquestrador principal de migração de questões do trieduc para recursos_didaticos.

FLUXO:
1. Buscar dados da classificação (thsethub.classificacao_usuario)
2. Buscar questão completa (trieduc.questoes + alternativas)
3. Buscar enunciado tratado (thsethub.questao_assuntos)
4. Mapear disciplina e assuntos por NOME
5. Processar imagens (alta + baixa resolução)
6. Inserir em rd_questoes, rd_questoes_assuntos, rd_questoes_alternativas, rd_questoes_imagens
7. Marcar como migrada

MODO DRY-RUN: Não executa INSERTs, não marca como migrada, apenas mostra o que seria feito.
"""

import json
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text

from src.database import SessionLocal

from .exceptions import DuplicataException
from .imagens import processar_imagens_html
from .mapeamento import mapear_assuntos_por_nome, mapear_disciplina_por_nome
from .utils import integrar_texto_base

# ============================================================================
# SUBFUNÇÕES POR FASE
# ============================================================================


def _buscar_dados_classificacao(db, classificacao_id: int) -> Optional[Tuple]:
    """
    FASE 1.1: Busca dados da classificação.

    Returns:
        (questao_id, modulos_escolhidos, descricoes_assunto) ou None se não encontrada
    """
    print("\n FASE 1: Buscar dados da classificação")

    result = db.execute(
        text("""
        SELECT id, questao_id, modulos_escolhidos, descricoes_assunto_list
        FROM thsethub.classificacao_usuario
        WHERE id = :id
    """),
        {"id": classificacao_id},
    )

    classificacao = result.fetchone()
    if not classificacao:
        print(f" Classificação {classificacao_id} não encontrada!")
        return None

    questao_id_original = classificacao.questao_id
    modulos_escolhidos = (
        json.loads(classificacao.modulos_escolhidos)
        if classificacao.modulos_escolhidos
        else []
    )
    descricoes_assunto = (
        json.loads(classificacao.descricoes_assunto_list)
        if classificacao.descricoes_assunto_list
        else []
    )

    print(f"    Questão ID original: {questao_id_original}")
    print(f"    Módulos: {modulos_escolhidos}")
    print(f"    Assuntos: {descricoes_assunto}")

    return questao_id_original, modulos_escolhidos, descricoes_assunto


def _buscar_questao_e_alternativas(db, questao_id_original: int) -> Optional[Dict]:
    """
    FASE 1.2: Busca questão completa + alternativas + enunciado tratado.

    Returns:
        Dict com questao, alternativas, enunciado_tratado ou None se inválida
    """
    # Busca questão completa em trieduc
    print("\n Buscar questão em trieduc.questoes")
    result = db.execute(
        text("""
        SELECT id, disciplina_id, enunciado, resolucao, texto_base
        FROM trieduc.questoes
        WHERE id = :id
    """),
        {"id": questao_id_original},
    )

    questao = result.fetchone()
    if not questao:
        print(f" Questão {questao_id_original} não encontrada em trieduc!")
        return None

    print(f"    Enunciado: {len(questao.enunciado or '')} caracteres")
    print(f"    Resolução: {len(questao.resolucao or '')} caracteres")
    print(f"    Texto base: {len(questao.texto_base or '')} caracteres")

    # Busca alternativas
    print("\n Buscar alternativas em trieduc.questao_alternativas")
    result = db.execute(
        text("""
        SELECT id, ordem, conteudo, correta
        FROM trieduc.questao_alternativas
        WHERE questao_id = :questao_id
        ORDER BY ordem
    """),
        {"questao_id": questao_id_original},
    )

    alternativas = result.fetchall()
    print(f"   {len(alternativas)} alternativas encontradas")

    # Validação: apenas questões com 5 alternativas (A-E)
    if len(alternativas) != 5:
        print(
            f"    PULANDO: Questão não possui exatamente 5 alternativas (encontradas: {len(alternativas)})"
        )
        return None

    # Busca enunciado tratado
    print("\n Buscar enunciado tratado em thsethub.questao_assuntos")
    result = db.execute(
        text("""
        SELECT enunciado_tratado
        FROM thsethub.questao_assuntos
        WHERE questao_id = :questao_id
        LIMIT 1
    """),
        {"questao_id": questao_id_original},
    )

    enunciado_tratado_row = result.fetchone()
    enunciado_tratado = (
        enunciado_tratado_row.enunciado_tratado if enunciado_tratado_row else None
    )
    print(f"   Enunciado tratado: {len(enunciado_tratado or '')} caracteres")

    return {
        "questao": questao,
        "alternativas": alternativas,
        "enunciado_tratado": enunciado_tratado,
    }


def _mapear_disciplina_e_assuntos(
    db, questao, modulos_escolhidos: List[str], descricoes_assunto: List[str]
) -> Optional[Tuple]:
    """
    FASE 2: Mapeia disciplina e assuntos por nome.

    Returns:
        (disc_id, assuntos_mapeados, nome_disciplina) ou None se falhar
    """
    print("\n FASE 2: Mapeamento de disciplina e assuntos")

    # Busca nome da disciplina em trieduc
    result = db.execute(
        text("""
        SELECT descricao
        FROM trieduc.disciplinas
        WHERE id = :id
    """),
        {"id": questao.disciplina_id},
    )

    disciplina_row = result.fetchone()
    nome_disciplina = disciplina_row.descricao if disciplina_row else "Desconhecida"

    disc_id = mapear_disciplina_por_nome(nome_disciplina, db)
    if not disc_id:
        print("Não foi possível mapear a disciplina!")
        return None

    # Mapeia assuntos
    assuntos_mapeados = mapear_assuntos_por_nome(
        modulos_escolhidos, descricoes_assunto, db
    )

    if not assuntos_mapeados:
        print(" Nenhum assunto foi mapeado!")
    else:
        print(f"\n   ✓ {len(assuntos_mapeados)} assunto(s) mapeado(s):")
        for assu in assuntos_mapeados:
            print(f"      assu_id={assu['assu_id']} - {assu['assu_descricao']}")

    return disc_id, assuntos_mapeados, nome_disciplina


def _processar_todas_imagens(
    db, questao, alternativas, questao_id_original: int, dry_run: bool
) -> Tuple[str, List[Dict], List[Dict]]:
    """
    FASE 3: Processa imagens do enunciado, texto_base e alternativas.

    Returns:
        (enunciado_final, alternativas_processadas, todos_metadados_imagens)
    """
    print("\n FASE 3: Processar imagens")

    # Processar imagens do enunciado
    enunciado_com_s3, metadados_imagens_enunciado = processar_imagens_html(
        questao.enunciado, questao_id_original, db, dry_run=dry_run
    )

    # Processar imagens do texto_base (se houver)
    texto_base_com_s3 = None
    metadados_imagens_texto_base = []

    if questao.texto_base and questao.texto_base.strip():
        print("\n   Processando imagens do texto_base...")
        # Processa sempre — processar_imagens_html retorna o HTML original sem alterações
        # se não houver nenhuma <img>. Cobre URLs externas, base64 e imagem_id.
        texto_base_com_s3, metadados_imagens_texto_base = processar_imagens_html(
            questao.texto_base, questao_id_original, db, dry_run=dry_run
        )
        if metadados_imagens_texto_base:
            print(
                f"   {len(metadados_imagens_texto_base)} imagem(ns) do texto_base enviada(s) para S3"
            )
        else:
            print("   Texto_base sem imagens, mantendo original")

    # Integrar texto_base ao enunciado (se houver)
    if texto_base_com_s3:
        print("\n   Integrando texto_base ao enunciado...")
        enunciado_com_s3 = integrar_texto_base(texto_base_com_s3, enunciado_com_s3)
        print(f"   Texto_base integrado ({len(texto_base_com_s3)} caracteres)")

    # Processar imagens das alternativas
    metadados_imagens_alternativas = []
    alternativas_processadas = []

    for alt in alternativas:
        alt_html, alt_metadados = processar_imagens_html(
            alt.conteudo, questao_id_original, db, dry_run=dry_run
        )
        alternativas_processadas.append(
            {"ordem": alt.ordem, "texto": alt_html, "correta": alt.correta}
        )
        metadados_imagens_alternativas.extend(alt_metadados)

    # Combina todos os metadados de imagens (incluindo texto_base)
    todos_metadados_imagens = (
        metadados_imagens_texto_base
        + metadados_imagens_enunciado
        + metadados_imagens_alternativas
    )
    print(f"\n   Total de {len(todos_metadados_imagens)} imagens processadas")

    return enunciado_com_s3, alternativas_processadas, todos_metadados_imagens


def _verificar_duplicata(db, questao_id_original: int):
    """
    FASE 4 pré-check: Verifica se questão já foi migrada.

    Raises:
        DuplicataException se já migrada
    """
    print("\n   Verificando se questão já foi migrada...")
    result_check = db.execute(
        text("""
        SELECT questao_id
        FROM recursos_didaticos.rd_questoes
        WHERE recurso_origem_id = 6
          AND recurso_origem_chave = :chave
        LIMIT 1
    """),
        {"chave": str(questao_id_original)},
    )

    questao_existente = result_check.fetchone()
    if questao_existente:
        print(f"   Questão já foi migrada! ID: {questao_existente.questao_id}")
        print(f"   Abortando migração para evitar duplicata.")
        raise DuplicataException(
            f"Questão {questao_id_original} já foi migrada (ID {questao_existente.questao_id})"
        )

    print(f"   Questão ainda não foi migrada, prosseguindo...")


def _inserir_questao(
    db,
    enunciado: str,
    disc_id: int,
    resolucao,
    questao_id_original: int,
    enunciado_tratado,
    dry_run: bool,
) -> Optional[int]:
    """
    FASE 4.1: INSERT em rd_questoes.

    Returns:
        novo_questao_id ou None (em dry-run)
    """
    print("\n-- 1. INSERT INTO rd_questoes")

    if not dry_run:
        result = db.execute(
            text("""
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
        """),
            {
                "enunciado": enunciado,
                "disc_id": disc_id,
                "resolucao": resolucao,
                "recurso_origem_chave": str(questao_id_original),
                "enunciado_limpo": enunciado_tratado,
            },
        )

        novo_questao_id = result.lastrowid
        print(f"   Questão inserida! ID gerado: {novo_questao_id}")
        return novo_questao_id
    else:
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
            {repr(enunciado)},  -- questao_enunciado
            {disc_id},  -- disc_id
            2026,  -- questao_ano
            0,  -- questao_enem
            NULL,  -- video_id_comentario
            {repr(resolucao) if resolucao else 'NULL'},  -- questao_comentario_texto
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
        return None


def _inserir_assuntos(
    db, novo_questao_id: Optional[int], assuntos_mapeados: List[Dict], dry_run: bool
):
    """FASE 4.2: INSERT em rd_questoes_assuntos."""
    if not assuntos_mapeados:
        return

    print("\n-- 2. INSERT INTO rd_questoes_assuntos")

    if not dry_run:
        for assu in assuntos_mapeados:
            db.execute(
                text("""
            INSERT INTO recursos_didaticos.rd_questoes_assuntos (questao_id, assu_id, questao_assu_principal)
            VALUES (:questao_id, :assu_id, 1)
            """),
                {"questao_id": novo_questao_id, "assu_id": assu["assu_id"]},
            )
            print(f"   Assunto inserido: {assu['assu_descricao']}")
    else:
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


def _inserir_alternativas(
    db,
    novo_questao_id: Optional[int],
    alternativas_processadas: List[Dict],
    dry_run: bool,
):
    """FASE 4.3: INSERT em rd_questoes_alternativas."""
    print("\n-- 3. INSERT INTO rd_questoes_alternativas")
    prefixos = ["A", "B", "C", "D", "E"]

    if not dry_run:
        for alt in alternativas_processadas:
            prefixo = (
                prefixos[alt["ordem"] - 1] if alt["ordem"] <= 5 else str(alt["ordem"])
            )
            db.execute(
                text("""
            INSERT INTO recursos_didaticos.rd_questoes_alternativas
            (questao_id, questao_alternativa_prefixo, questao_alternativa_texto, questao_alternativa_correta)
            VALUES (:questao_id, :prefixo, :texto, :correta)
            """),
                {
                    "questao_id": novo_questao_id,
                    "prefixo": prefixo,
                    "texto": alt["texto"],
                    "correta": 1 if alt["correta"] else 0,
                },
            )
            print(f"   Alternativa {prefixo} inserida (correta={alt['correta']})")
    else:
        for alt in alternativas_processadas:
            prefixo = (
                prefixos[alt["ordem"] - 1] if alt["ordem"] <= 5 else str(alt["ordem"])
            )
            sql_alt = f"""
            INSERT INTO recursos_didaticos.rd_questoes_alternativas (
                questao_id, questao_alternativa_prefixo, questao_alternativa_texto, questao_alternativa_correta
            ) VALUES (
                @novo_questao_id, '{prefixo}', {repr(alt['texto'])}, {1 if alt['correta'] else 0}
            );
            """
            print(sql_alt)


def _inserir_imagens(
    db,
    novo_questao_id: Optional[int],
    todos_metadados_imagens: List[Dict],
    dry_run: bool,
):
    """FASE 4.4: INSERT em rd_questoes_imagens."""
    if not todos_metadados_imagens:
        return

    print("\n-- 4. INSERT INTO rd_questoes_imagens")

    if not dry_run:
        for idx, img_meta in enumerate(todos_metadados_imagens, 1):
            db.execute(
                text("""
            INSERT INTO recursos_didaticos.rd_questoes_imagens (
                questao_id, questao_imagem_baixa_resolucao, questao_imagem_alta_resolucao,
                questao_imagem_data_upload, questao_imagem_largura, questao_imagem_altura,
                questao_imagem_tamanho, created_at, updated_at
            ) VALUES (
                :questao_id, :url_baixa, :url_alta, NOW(), :largura, :altura, :tamanho, NOW(), NOW()
            )
            """),
                {
                    "questao_id": novo_questao_id,
                    "url_baixa": img_meta["url_baixa"],
                    "url_alta": img_meta["url_alta"],
                    "largura": img_meta["largura"],
                    "altura": img_meta["altura"],
                    "tamanho": img_meta["tamanho"],
                },
            )
            print(
                f"   Imagem {idx} inserida: {img_meta['largura']}x{img_meta['altura']}px"
            )
    else:
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


def _marcar_migrada_e_commit(db, classificacao_id: int, dry_run: bool):
    """FASE 5: Marca classificação como migrada e faz COMMIT."""
    if not dry_run:
        print("\n-- 5. Marcar classificação como migrada")
        db.execute(
            text("""
        UPDATE thsethub.classificacao_usuario
        SET migrada = TRUE
        WHERE id = :classificacao_id
        """),
            {"classificacao_id": classificacao_id},
        )
        print(f"   Classificação {classificacao_id} marcada como migrada")

        # COMMIT
        db.commit()
        print("\n COMMIT realizado! Migração concluída com sucesso!")


def _imprimir_resumo(
    questao_id_original: int,
    novo_questao_id: Optional[int],
    nome_disciplina: str,
    disc_id: int,
    assuntos_mapeados: List[Dict],
    alternativas_processadas: List[Dict],
    todos_metadados_imagens: List[Dict],
    dry_run: bool,
):
    """Imprime resumo final da migração."""
    print("\n" + "=" * 80)
    print(" RESUMO DA MIGRAÇÃO")
    print("=" * 80)
    print(f"Questão ID original (trieduc): {questao_id_original}")
    if novo_questao_id:
        print(f"Questão ID novo (recursos_didaticos): {novo_questao_id}")
    print(f"Disciplina: {nome_disciplina} (disc_id={disc_id})")
    print(f"Assuntos mapeados: {len(assuntos_mapeados)}")
    print(f"Alternativas: {len(alternativas_processadas)}")
    print(f"Imagens: {len(todos_metadados_imagens)}")
    print(
        f"Modo: {'PRODUÇÃO (executado e commitado!)' if not dry_run else 'DRY-RUN (não executado)'}"
    )
    print("=" * 80)


# ============================================================================
# FUNÇÃO PRINCIPAL (ORQUESTRADOR)
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
        print(
            f"{'[DRY-RUN] ' if dry_run else ''}MIGRAÇÃO COMPLETA - Classificação ID {classificacao_id}"
        )
        print("=" * 80)

        # FASE 1.1: Buscar dados da classificação
        dados_classificacao = _buscar_dados_classificacao(db, classificacao_id)
        if not dados_classificacao:
            return
        questao_id_original, modulos_escolhidos, descricoes_assunto = (
            dados_classificacao
        )

        # FASE 1.2: Buscar questão + alternativas
        dados_questao = _buscar_questao_e_alternativas(db, questao_id_original)
        if not dados_questao:
            return
        questao = dados_questao["questao"]
        alternativas = dados_questao["alternativas"]
        enunciado_tratado = dados_questao["enunciado_tratado"]

        # FASE 2: Mapeamento de disciplina e assuntos
        resultado_mapeamento = _mapear_disciplina_e_assuntos(
            db, questao, modulos_escolhidos, descricoes_assunto
        )
        if not resultado_mapeamento:
            return
        disc_id, assuntos_mapeados, nome_disciplina = resultado_mapeamento

        # FASE 3: Processar imagens
        enunciado_com_s3, alternativas_processadas, todos_metadados_imagens = (
            _processar_todas_imagens(
                db, questao, alternativas, questao_id_original, dry_run
            )
        )

        # FASE 4: Inserir no banco
        print(
            f"\n FASE 4: {'Inserir no banco' if not dry_run else 'Gerar SQLs de inserção'}"
        )

        if not dry_run:
            _verificar_duplicata(db, questao_id_original)

        novo_questao_id = _inserir_questao(
            db,
            enunciado_com_s3,
            disc_id,
            questao.resolucao,
            questao_id_original,
            enunciado_tratado,
            dry_run,
        )
        _inserir_assuntos(db, novo_questao_id, assuntos_mapeados, dry_run)
        _inserir_alternativas(db, novo_questao_id, alternativas_processadas, dry_run)
        _inserir_imagens(db, novo_questao_id, todos_metadados_imagens, dry_run)

        # FASE 5: Marcar como migrada + COMMIT
        _marcar_migrada_e_commit(db, classificacao_id, dry_run)

        # RESUMO
        _imprimir_resumo(
            questao_id_original,
            novo_questao_id,
            nome_disciplina,
            disc_id,
            assuntos_mapeados,
            alternativas_processadas,
            todos_metadados_imagens,
            dry_run,
        )

        return True  # Retorna True indicando sucesso

    finally:
        db.close()
