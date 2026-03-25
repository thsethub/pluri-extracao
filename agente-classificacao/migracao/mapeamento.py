from difflib import SequenceMatcher
from typing import Dict, List, Optional

from sqlalchemy import text


def similaridade_texto(a: str, b: str) -> float:
    """Calcula similaridade entre dois textos (0.0 a 1.0)"""
    a_clean = a.lower().replace("[rm]", "").replace("[", "").replace("]", "").strip()
    b_clean = b.lower().replace("[rm]", "").replace("[", "").replace("]", "").strip()
    return SequenceMatcher(None, a_clean, b_clean).ratio()


def mapear_disciplina_por_nome(nome_disciplina: str, db) -> Optional[int]:
    """Mapeia nome da disciplina para disc_id em compartilhados"""
    result = db.execute(
        text("""
        SELECT disc_id, disc_descricao
        FROM compartilhados.disciplinas
        WHERE disc_descricao LIKE :nome
        LIMIT 1
    """),
        {"nome": f"%{nome_disciplina}%"},
    )

    row = result.fetchone()
    if row:
        print(
            f"    Disciplina mapeada: '{nome_disciplina}' → disc_id={row.disc_id} ({row.disc_descricao})"
        )
        return row.disc_id
    else:
        print(f"     Disciplina '{nome_disciplina}' não encontrada em compartilhados")
        return None


def mapear_assuntos_por_nome(
    modulos_escolhidos: List[str], descricoes_assunto: List[str], db
) -> List[Dict]:
    """
    Mapeia módulos e assuntos por NOME (SEM prefixo [RM]).

    Returns:
        Lista de dicts com assu_id, assu_descricao, disc_modu_id, etc.
    """
    todos_assuntos = []

    for idx, nome_modulo in enumerate(modulos_escolhidos):
        print(f"\n    Módulo {idx+1}: '{nome_modulo}'")

        # Busca módulo SEM [RM]
        result = db.execute(
            text("""
            SELECT disc_modu_id, disc_modu_descricao, disc_id
            FROM compartilhados.disciplinas_modulos
            WHERE disc_modu_descricao LIKE :nome
              AND disc_modu_descricao NOT LIKE '[RM]%'
              AND disc_modu_descricao NOT LIKE '%% [RM]%%'
        """),
            {"nome": f"%{nome_modulo}%"},
        )

        modulos_encontrados = result.fetchall()

        if not modulos_encontrados:
            print(f"       Módulo não encontrado")
            continue

        # Busca assuntos para cada módulo encontrado
        melhor_modulo = None
        assuntos_disponiveis = []

        for mod in sorted(
            modulos_encontrados,
            key=lambda m: similaridade_texto(nome_modulo, m.disc_modu_descricao),
            reverse=True,
        ):

            result = db.execute(
                text("""
                SELECT assu_id, assu_descricao, disc_modu_id
                FROM compartilhados.assuntos
                WHERE disc_modu_id = :disc_modu_id
            """),
                {"disc_modu_id": mod.disc_modu_id},
            )

            assuntos_mod = result.fetchall()

            if assuntos_mod:
                melhor_modulo = mod
                assuntos_disponiveis = assuntos_mod
                break

        if not melhor_modulo:
            print(f"       Nenhum módulo com assuntos encontrado")
            continue

        print(
            f"       Módulo: disc_modu_id={melhor_modulo.disc_modu_id} - {melhor_modulo.disc_modu_descricao}"
        )
        print(f"       {len(assuntos_disponiveis)} assuntos disponíveis")

        # Match por nome com descricao_assunto
        if idx < len(descricoes_assunto):
            nome_assunto = descricoes_assunto[idx]

            # Calcula similaridade
            matches = []
            for assu in assuntos_disponiveis:
                similaridade = similaridade_texto(nome_assunto, assu.assu_descricao)
                matches.append(
                    {
                        "assu_id": assu.assu_id,
                        "assu_descricao": assu.assu_descricao,
                        "disc_modu_id": melhor_modulo.disc_modu_id,
                        "disc_modu_descricao": melhor_modulo.disc_modu_descricao,
                        "disc_id": melhor_modulo.disc_id,
                        "similaridade": similaridade,
                    }
                )

            matches.sort(key=lambda x: x["similaridade"], reverse=True)

            # Usa melhor match se >= 50%
            if matches and matches[0]["similaridade"] >= 0.5:
                print(
                    f"       Match: {matches[0]['similaridade']:.0%} - {matches[0]['assu_descricao']}"
                )
                todos_assuntos.append(matches[0])
            else:
                # Usa todos do módulo
                print(f"        Match fraco, usando TODOS os assuntos do módulo")
                todos_assuntos.extend(matches)
        else:
            # Usa todos do módulo
            for assu in assuntos_disponiveis:
                todos_assuntos.append(
                    {
                        "assu_id": assu.assu_id,
                        "assu_descricao": assu.assu_descricao,
                        "disc_modu_id": melhor_modulo.disc_modu_id,
                        "disc_modu_descricao": melhor_modulo.disc_modu_descricao,
                        "disc_id": melhor_modulo.disc_id,
                        "similaridade": 1.0,
                    }
                )

    return todos_assuntos
