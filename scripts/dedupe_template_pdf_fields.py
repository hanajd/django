#!/usr/bin/env python3
"""Remove duplicate PDF field boxes (same rect) from unified template JSON; remap formulas."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]


def _import_module(name: str, rel_path: str):
    import importlib.util

    path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


_ut = _import_module("unified_template_fields", "utils/unified_template_fields.py")
_pf = _import_module("pdf_field_formulas", "utils/pdf_field_formulas.py")

_coerce_field_formulas_dict = _pf._coerce_field_formulas_dict
attach_root_field_formulas_to_pdf_field_rows = _pf.attach_root_field_formulas_to_pdf_field_rows
normalize_pdf_field_id = _pf.normalize_pdf_field_id
materialize_unified_pdf_fields = _ut.materialize_unified_pdf_fields
_field_dict_to_compact_row = _ut._field_dict_to_compact_row

_FID_RE = re.compile(r"\bf(\d+)\b", re.IGNORECASE)

RICH_KEYS = (
    "fieldExpression",
    "pdfFieldExpression",
    "judgmentCriteriaByTestType",
    "judgmentCriteriaManual",
    "fieldVerdict",
    "fieldVerdictPlan",
)


def rect_key(field: Dict[str, Any], tol: float = 0.5) -> Tuple:
    rect = field.get("rect")
    if isinstance(rect, (list, tuple)) and len(rect) >= 5:
        page, x, y, w, h = (
            int(rect[0]),
            float(rect[1]),
            float(rect[2]),
            float(rect[3]),
            float(rect[4]),
        )
    else:
        page = int(field.get("page") or 1)
        x, y, w, h = (
            float(field.get("x") or 0),
            float(field.get("y") or 0),
            float(field.get("w") or 0),
            float(field.get("h") or 0),
        )
    return (
        page,
        round(x / tol) * tol,
        round(y / tol) * tol,
        round(w / tol) * tol,
        round(h / tol) * tol,
    )


def field_score(field: Dict[str, Any]) -> int:
    score = 0
    if str(field.get("fieldExpression") or field.get("pdfFieldExpression") or "").strip():
        score += 1000
    jct = field.get("judgmentCriteriaByTestType")
    if isinstance(jct, dict) and jct:
        score += 500
    if field.get("judgmentCriteriaManual") is True:
        score += 100
    fv = field.get("fieldVerdict")
    if isinstance(fv, dict) and fv:
        score += 300
    if field.get("fieldVerdictPlan") not in (None, "", {}, []):
        score += 200
    ft = str(field.get("fieldType") or "text").lower()
    if ft in ("check", "image"):
        score += 10
    name = str(field.get("id") or field.get("title") or "").strip()
    score += min(len(name), 80)
    return score


def pick_keeper(indices: List[int], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    best_i = max(indices, key=lambda i: (field_score(rows[i]), -i))
    return dict(rows[best_i])


def remap_expression(expr: str, id_map: Dict[str, str]) -> str:
    if not expr or not id_map:
        return expr

    def repl(m: re.Match) -> str:
        old = normalize_pdf_field_id(m.group(0))
        return id_map.get(old, old) or m.group(0)

    return _FID_RE.sub(repl, expr)


def remap_field_metadata(field: Dict[str, Any], id_map: Dict[str, str]) -> None:
    for key in ("fieldExpression", "pdfFieldExpression"):
        raw = str(field.get(key) or "").strip()
        if raw:
            field[key] = remap_expression(raw, id_map)
    fv = field.get("fieldVerdict")
    if isinstance(fv, dict):
        for sub in ("expression", "formula", "rule"):
            if isinstance(fv.get(sub), str):
                fv[sub] = remap_expression(fv[sub], id_map)


def remap_json_tree(node: Any, id_map: Dict[str, str]) -> Any:
    if isinstance(node, dict):
        for k, v in list(node.items()):
            nk = normalize_pdf_field_id(str(k)) if _FID_RE.fullmatch(str(k).strip()) else ""
            if nk and nk in id_map:
                node[id_map[nk]] = remap_json_tree(node.pop(k), id_map)
            else:
                node[k] = remap_json_tree(v, id_map)
        for key in ("pdfFieldId", "targetPdfFieldId", "id"):
            if key in node and isinstance(node[key], str):
                pid = normalize_pdf_field_id(node[key])
                if pid and pid in id_map:
                    node[key] = id_map[pid]
        for key in ("fieldExpression", "pdfFieldExpression", "formula", "expression"):
            if isinstance(node.get(key), str):
                node[key] = remap_expression(node[key], id_map)
        return node
    if isinstance(node, list):
        return [remap_json_tree(x, id_map) for x in node]
    if isinstance(node, str) and _FID_RE.search(node):
        return remap_expression(node, id_map)
    return node


def dedupe_template(data: Dict[str, Any], *, tol: float = 0.5) -> Tuple[Dict[str, Any], Dict[str, int]]:
    pdf = data.setdefault("pdf", {})
    raw_fields = pdf.get("fields") if isinstance(pdf.get("fields"), list) else []
    materialized = materialize_unified_pdf_fields(raw_fields)

    groups: Dict[Tuple, List[int]] = defaultdict(list)
    for i, row in enumerate(materialized):
        groups[rect_key(row, tol=tol)].append(i)

    rep_map: Dict[str, str] = {}
    keeper_by_key: Dict[Tuple, Dict[str, Any]] = {}
    for key, indices in groups.items():
        keeper = pick_keeper(indices, materialized)
        keeper_by_key[key] = keeper
        kid = normalize_pdf_field_id(str(keeper.get("pdfFieldId") or ""))
        for i in indices:
            oid = normalize_pdf_field_id(str(materialized[i].get("pdfFieldId") or ""))
            if oid and kid:
                rep_map[oid] = kid

    kept_ids = {
        normalize_pdf_field_id(str(keeper_by_key[k].get("pdfFieldId") or ""))
        for k in keeper_by_key
    }
    kept_ids.discard("")

    ordered_keepers: List[Dict[str, Any]] = []
    seen: set = set()
    for row in materialized:
        key = rect_key(row, tol=tol)
        if key in seen:
            continue
        seen.add(key)
        ordered_keepers.append(dict(keeper_by_key[key]))

    # 仅把「已删除的重复栏位 id」映射到保留栏位 id；不整体重排 f1..fN，避免公式大面积失效。
    id_map: Dict[str, str] = {}
    for dup_old, rep_old in rep_map.items():
        if dup_old != rep_old and dup_old not in kept_ids and rep_old in kept_ids:
            id_map[dup_old] = rep_old

    for row in ordered_keepers:
        remap_field_metadata(row, id_map)

    root_ff = data.get("fieldFormulas")
    fs = data.get("formSchema")
    if root_ff is None and isinstance(fs, dict):
        root_ff = fs.get("fieldFormulas")
    formulas = _coerce_field_formulas_dict(root_ff)
    new_formulas: Dict[str, str] = {}
    for key, expr in formulas.items():
        if key not in kept_ids:
            key = id_map.get(key, key)
        if key not in kept_ids:
            continue
        new_formulas[key] = remap_expression(expr, id_map)
    attach_root_field_formulas_to_pdf_field_rows(ordered_keepers, new_formulas)
    orphans = {
        k: v
        for k, v in new_formulas.items()
        if k not in {normalize_pdf_field_id(str(r.get("pdfFieldId") or "")) for r in ordered_keepers}
    }

    # 勿用 compact_unified_pdf_fields_for_storage：其内部会再次 materialize 并重排 f1..fN。
    compact = [_field_dict_to_compact_row(f) for f in ordered_keepers]
    pdf["fields"] = compact
    if orphans:
        data["fieldFormulas"] = orphans
    elif "fieldFormulas" in data and not orphans:
        data.pop("fieldFormulas", None)
    if isinstance(fs, dict) and "fieldFormulas" in fs:
        if orphans:
            fs["fieldFormulas"] = orphans
        else:
            fs.pop("fieldFormulas", None)

    remap_json_tree(data.get("formSchema"), id_map)
    remap_json_tree(data.get("steps"), id_map)

    stats = {
        "before": len(materialized),
        "after": len(compact),
        "removed": len(materialized) - len(compact),
        "duplicate_groups": sum(1 for v in groups.values() if len(v) > 1),
        "with_formula_or_judgment_kept": sum(1 for r in compact if field_score(materialize_unified_pdf_fields([r])[0]) >= 500),
    }
    return data, stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("json_path", type=Path)
    parser.add_argument("--tol", type=float, default=0.5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    path = args.json_path.resolve()
    if not path.exists():
        print(f"Not found: {path}", file=sys.stderr)
        return 1
    data = json.loads(path.read_text(encoding="utf-8"))
    out, stats = dedupe_template(data, tol=args.tol)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    if args.dry_run:
        return 0
    backup = path.with_suffix(path.suffix + f".bak-{datetime.now().strftime('%Y%m%d%H%M%S')}")
    shutil.copy2(path, backup)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}")
    print(f"Backup {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
