"""Aplica no banco de produção as atualizações de assuntos vindas do dry-run.

Fluxo:
1) lê o relatório JSON de dry-run e resolve os mapeamentos:
   - ATUALIZAR: muda descricao em habilidade_modulos
   - EXCLUIR: resolve duplicidade por módulo/assunto (mantém um hm_id e remapeia os outros)
2) atualiza habilidade_modulos (descricao e exclusão de duplicados)
3) atualiza classificacao_usuario nos campos:
   - habilidade_modulo_id
   - descricao_assunto
   - descricoes_assunto_list
   - classificacoes_trieduc_list
   - classificacao_trieduc

Observação: o script usa o CSV de impacto do dry-run para limitar atualização
de classificações às linhas já identificadas como impactadas.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _chunked(seq: Sequence[Any], size: int = 200) -> Iterable[Sequence[Any]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _safe_json_loads(value: Any) -> Optional[Any]:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None


def _as_int_list(value: Any) -> Optional[List[int]]:
    parsed = _safe_json_loads(value)
    if parsed is None:
        return None
    if not isinstance(parsed, list):
        return None
    result: List[int] = []
    for item in parsed:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            return None
    return result


def _as_string_list(value: Any) -> Optional[List[Any]]:
    parsed = _safe_json_loads(value)
    if parsed is None:
        return None
    if not isinstance(parsed, list):
        return None
    return parsed


def _build_hm_mappings(
    report: dict[str, Any]
) -> Tuple[dict[int, int], dict[int, str], dict[int, str], dict[int, str], List[dict[str, Any]]]:
    details = report.get("linhas_planilha_detalhe", [])

    context_updates: dict[tuple[str, str, str], dict[str, Any]] = {}
    context_excludes: dict[tuple[str, str, str], dict[str, Any]] = {}
    warn: List[dict[str, Any]] = []

    for row in details:
        action = str(row.get("action", "")).upper()
        old_desc = (row.get("old_desc") or "").strip()
        new_desc = (row.get("new_desc") or "").strip()
        ids = [int(x) for x in row.get("matched_hm_ids", []) if x is not None]
        key = (
            str(row.get("disciplina", "")).strip(),
            str(row.get("modulo", "")).strip(),
            old_desc,
        )

        if action == "ATUALIZAR" and old_desc != new_desc:
            entry = context_updates.setdefault(key, {"new_desc": new_desc, "ids": set()})
            if entry["new_desc"] != new_desc:
                warn.append(
                    {
                        "type": "divergente",
                        "context": key,
                        "first": entry["new_desc"],
                        "current": new_desc,
                    }
                )
                continue
            entry["ids"].update(ids)

        if action == "EXCLUIR":
            entry = context_excludes.setdefault(
                key, {"ids": set(), "new_desc": old_desc or row.get("new_desc", "")}
            )
            entry["ids"].update(ids)

    id_remap: dict[int, int] = {}
    hm_new_desc: dict[int, str] = {}
    hm_old_desc: dict[int, str] = {}
    hm_context: dict[int, str] = {}

    # atualizações diretas
    for (disc, mod, old_desc), payload in context_updates.items():
        new_desc = str(payload.get("new_desc", "")).strip()
        for hm_id in sorted(payload.get("ids", set())):
            hm_new_desc[hm_id] = new_desc
            hm_old_desc[hm_id] = old_desc
            hm_context[hm_id] = f"{disc} :: {mod}"

    # resolução de duplicidades (EXCLUIR): mantém o menor id e remapeia o restante
    for context, payload in context_excludes.items():
        update_payload = context_updates.get(context)
        all_ids = set(payload.get("ids", set()))
        if update_payload:
            all_ids |= set(update_payload.get("ids", set()))
        if len(all_ids) <= 1:
            continue
        keep_id = min(all_ids)
        for dup_id in sorted(all_ids):
            if dup_id == keep_id:
                continue
            id_remap[dup_id] = keep_id
            hm_new_desc.pop(dup_id, None)
            hm_old_desc.pop(dup_id, None)

    return id_remap, hm_new_desc, hm_old_desc, hm_context, warn


def _build_id_sets_from_csv(csv_path: Path) -> List[int]:
    if not csv_path.exists():
        return []
    ids: List[int] = []
    with csv_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            raw = (row.get("classificacao_usuario_id") or "").strip()
            if not raw:
                continue
            try:
                ids.append(int(raw))
            except ValueError:
                continue
    # mantém estabilidade
    return sorted(set(ids))


def _build_select_in_clause(column: str, ids: Sequence[int]) -> Tuple[str, Dict[str, int]]:
    placeholders = []
    params: Dict[str, int] = {}
    for i, row_id in enumerate(ids):
        key = f"id_{i}"
        placeholders.append(f":{key}")
        params[key] = int(row_id)
    return f"{column} IN ({', '.join(placeholders)})", params


def _apply_single_row_updates(
    row: dict[str, Any],
    id_remap: dict[int, int],
    hm_new_desc: dict[int, str],
    hm_old_desc: dict[int, str],
) -> tuple[dict[str, Any], bool]:
    changed: dict[str, Any] = {}
    changed_any = False

    old_hm_id = row["habilidade_modulo_id"]
    if old_hm_id is not None:
        old_hm_id = int(old_hm_id)
    new_hm_id = id_remap.get(old_hm_id, old_hm_id)
    if new_hm_id != old_hm_id:
        changed["habilidade_modulo_id"] = new_hm_id
        changed_any = True

    # texto legado
    old_desc_ref = hm_old_desc.get(old_hm_id, hm_old_desc.get(new_hm_id))
    new_desc_val = hm_new_desc.get(new_hm_id, hm_new_desc.get(old_hm_id))

    if row.get("descricao_assunto") and old_desc_ref and new_desc_val:
        desc_val = row["descricao_assunto"]
        if isinstance(desc_val, str) and desc_val.strip() == old_desc_ref:
            changed["descricao_assunto"] = new_desc_val
            changed_any = True

    if row.get("classificacao_trieduc") and old_desc_ref and new_desc_val:
        classif = row["classificacao_trieduc"]
        if isinstance(classif, str) and classif.strip() == old_desc_ref:
            changed["classificacao_trieduc"] = new_desc_val
            changed_any = True

    # campos JSON
    hm_ids = _as_int_list(row.get("habilidade_modulo_ids"))
    descricoes = _as_string_list(row.get("descricoes_assunto_list"))
    classificacoes = _as_string_list(row.get("classificacoes_trieduc_list"))

    if hm_ids is not None:
        new_hm_ids = list(hm_ids)
        changed_descricoes = False
        for idx, current_id in enumerate(new_hm_ids):
            mapped = id_remap.get(int(current_id), int(current_id))
            if mapped != current_id:
                new_hm_ids[idx] = mapped
                changed_any = True
            if descricoes is not None and idx < len(descricoes):
                old_desc_ref_in_array = hm_old_desc.get(int(current_id), hm_old_desc.get(mapped))
                new_desc_in_array = hm_new_desc.get(mapped, hm_new_desc.get(int(current_id)))
                if (
                    old_desc_ref_in_array
                    and new_desc_in_array
                    and isinstance(descricoes[idx], str)
                    and descricoes[idx] == old_desc_ref_in_array
                ):
                    descricoes[idx] = new_desc_in_array
                    changed_descricoes = True
                    changed_any = True

        if tuple(new_hm_ids) != tuple(hm_ids):
            changed["habilidade_modulo_ids"] = new_hm_ids
            changed_any = True
        if descricoes is not None and changed_descricoes:
            changed["descricoes_assunto_list"] = descricoes

    if classificacoes is not None and old_desc_ref and new_desc_val:
        new_classificacoes = []
        for item in classificacoes:
            if isinstance(item, str) and item == old_desc_ref:
                new_classificacoes.append(new_desc_val)
                changed_any = True
            else:
                new_classificacoes.append(item)
        if new_classificacoes != classificacoes:
            changed["classificacoes_trieduc_list"] = new_classificacoes

    return changed, changed_any


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aplica atualização de assuntos em thsethub (modo produção)."
    )
    parser.add_argument(
        "--report-json",
        default=str(
            Path("agente-classificacao") / "reports" / "dry_run_assuntos_modulos_thsethub_2026-03-05.json"
        ),
    )
    parser.add_argument(
        "--impact-csv",
        default=str(
            Path("agente-classificacao") / "reports" / "dry_run_classificacoes_impactadas_2026-03-05.csv"
        ),
    )
    args = parser.parse_args()

    report_path = Path(args.report_json)
    impact_csv = Path(args.impact_csv)

    if not report_path.exists():
        raise SystemExit(f"Relatório não encontrado: {report_path}")

    report = json.loads(report_path.read_text(encoding="utf-8"))

    id_remap, hm_new_desc, hm_old_desc, hm_context, warnings = _build_hm_mappings(report)
    impacted_class_ids = _build_id_sets_from_csv(impact_csv)

    env = _read_env(Path("agente-classificacao") / ".env")
    conn_str = (
        f"mysql+pymysql://{env['PG_USER']}:{quote_plus(env['PG_PASSWORD'])}"
        f"@{env['PG_HOST']}:{env['PG_PORT']}/{env['PG_NAME']}"
    )
    engine = create_engine(conn_str, pool_pre_ping=True, pool_recycle=3600, echo=False)

    print("[INFO] HM a alterar:", len(hm_new_desc))
    print("[INFO] HM a remover:", len(id_remap))
    print("[INFO] Classificações impactadas (CSV):", len(impacted_class_ids))
    if warnings:
        print("[WARN] Conflitos no relatório (ignorados):", len(warnings))
        for item in warnings[:5]:
            print(item)

    if not impacted_class_ids:
        print("[WARN] Sem IDs de classificacao_usuario no CSV. Vou pular atualização de classificações.")

    # snapshots para rastreabilidade
    ids_to_update = sorted(hm_new_desc.keys())
    ids_to_delete = sorted(id_remap.keys())

    print("[INFO] Iniciando transação...")

    with engine.begin() as conn:
        if ids_to_update:
            sel = text(
                "SELECT id, area, disciplina, modulo, descricao, disc_modu_id "
                "FROM habilidade_modulos WHERE id = :id"
            )
            before_updates = {}
            for hm_id in ids_to_update:
                row = conn.execute(sel, {"id": hm_id}).mappings().first()
                if row:
                    before_updates[hm_id] = dict(row)
            print("[INFO] Habilidades antes da alteração:", len(before_updates))

        if ids_to_update:
            for hm_id, new_desc in hm_new_desc.items():
                conn.execute(
                    text(
                        "UPDATE habilidade_modulos "
                        "SET descricao = :descricao "
                        "WHERE id = :id"
                    ),
                    {"id": hm_id, "descricao": new_desc},
                )

        if ids_to_delete:
            placeholders = ", ".join(str(x) for x in ids_to_delete)
            conn.execute(
                text(f"DELETE FROM habilidade_modulos WHERE id IN ({placeholders})"),
            )

        total_class_rows = 0
        changed_class_rows = 0
        changed_fields: Dict[str, int] = defaultdict(int)

        if impacted_class_ids:
            for chunk in _chunked(impacted_class_ids, 100):
                where, where_params = _build_select_in_clause("id", chunk)
                sql = (
                    "SELECT id, habilidade_modulo_id, habilidade_modulo_ids, "
                    "descricao_assunto, descricoes_assunto_list, "
                    "classificacao_trieduc, classificacoes_trieduc_list "
                    f"FROM classificacao_usuario WHERE {where}"
                )
                rows = conn.execute(text(sql), where_params).mappings().all()
                total_class_rows += len(rows)

                for row in rows:
                    update_payload, changed = _apply_single_row_updates(
                        dict(row),
                        id_remap=id_remap,
                        hm_new_desc=hm_new_desc,
                        hm_old_desc=hm_old_desc,
                    )
                    if not (changed_payload := (update_payload and bool(update_payload))):
                        continue

                    if not changed:
                        continue

                    if "habilidade_modulo_id" in update_payload:
                        changed_fields["habilidade_modulo_id"] += 1
                    if "descricao_assunto" in update_payload:
                        changed_fields["descricao_assunto"] += 1
                    if "descricoes_assunto_list" in update_payload:
                        changed_fields["descricoes_assunto_list"] += 1
                    if "classificacao_trieduc" in update_payload:
                        changed_fields["classificacao_trieduc"] += 1
                    if "classificacoes_trieduc_list" in update_payload:
                        changed_fields["classificacoes_trieduc_list"] += 1
                    if "habilidade_modulo_ids" in update_payload:
                        changed_fields["habilidade_modulo_ids"] += 1

                    set_parts = []
                    params = {"id": row["id"]}
                    for key, value in update_payload.items():
                        params[key] = value
                        if key in {
                            "habilidade_modulo_ids",
                            "descricoes_assunto_list",
                            "classificacoes_trieduc_list",
                        }:
                            params[key] = json.dumps(value, ensure_ascii=False)
                        set_parts.append(f"{key} = :{key}")
                    set_clause = ", ".join(set_parts)
                    conn.execute(
                        text(f"UPDATE classificacao_usuario SET {set_clause} WHERE id = :id"),
                        params,
                    )
                    changed_class_rows += 1

        report_out = {
            "id_remap": id_remap,
            "hm_new_desc": hm_new_desc,
            "hm_old_desc": hm_old_desc,
            "hm_context": hm_context,
            "impactados_classificacao_ids": impacted_class_ids,
            "total_hm_atualizados": len(ids_to_update),
            "total_hm_removidos": len(ids_to_delete),
            "total_classificacao_rows_lidas": total_class_rows,
            "total_classificacao_rows_atualizadas": changed_class_rows,
            "campos_classificacao_atualizados": changed_fields,
            "warnings": warnings,
        }
        out_path = Path("agente-classificacao") / "reports" / "execucao_atualizacao_assuntos_2026-03-05.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report_out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[OK] Aplicação concluída.")
    print(
        f"[OK] Habilidades atualizadas: {len(hm_new_desc)} | deletadas: {len(id_remap)} | "
        f"classificações atualizadas: {changed_class_rows}/{total_class_rows}"
    )
    for field_name, count in sorted(changed_fields.items()):
        print(f"[OK] {field_name}: {count}")


if __name__ == "__main__":
    main()
