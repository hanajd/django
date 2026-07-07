#!/usr/bin/env python3
"""修复口腔 CBCT 现场记录第五章双组防护表 fieldBindings 与公式。"""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import django

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
django.setup()

from radiation_detection_report.chapter5_field_sync import (  # noqa: E402
    _chapter_g1_calibration_factor_pid,
    _chapter_g2_calibration_factor_pid,
    _field_rules_are_chapter_sourced,
    apply_background_formulas_to_pdf_fields,
    apply_chapter_report_rules_to_field,
    apply_mean_formulas_to_pdf_fields,
    attach_seq_no_to_bindings,
    build_field_bindings_from_fields,
    export_field_pdf_id,
    iter_frontend_export_fields,
    mean_expression_for_binding,
    report_expression_for_binding,
    resolve_background_calibration_factor_pid,
    resolve_chapter5_layout_for_fields,
)
from utils.pdf_field_formulas import (  # noqa: E402
    _iter_form_schema_steps,
    _iter_frontend_field_dicts,
)

READING_KEYS = (
    "reading_1",
    "reading_2",
    "reading_3",
    "reading_1_2",
    "reading_2_2",
    "reading_3_2",
)

# 模板级章节报出值规则（保留 {mean}/{mean2} 占位，勿写 if 展开式）
TEMPLATE_REPORT_RULE_OVERRIDES: dict[str, tuple[str, str]] = {
    "f96fd70fc3734da4a6208fd0b4575b95.json": ("{mean}*f984", "{mean2}*f158"),
}
SLOT_KEYS = READING_KEYS + ("mean_m", "mean_m_2", "report_d", "report_d_2", "seq_no", "remark")

PARTIAL_ROW_PATCHES: dict[str, dict[str, str]] = {}

LANWEI_PATCHES: dict[str, dict[str, str]] = {}


def field_text(pdf: dict, pid: str) -> str:
    return str(pdf.get(pid, {}).get("id") or pdf.get(pid, {}).get("label") or "")


def row_prefix_from_text(t: str) -> str | None:
    if not t or t.startswith("栏位") or ("检测点位置_r" in t and "_测量读数M1_" in t):
        return None
    for pat in (
        r"(.+?)_测量读数M1$",
        r"(.+?)_测量读数M2$",
        r"(.+?)_测量读数M3$",
        r"(.+?)_测量均值Mbar$",
        r"(.+?)_测量读数M$",
        r"(.+?)_测量值$",
        r"(.+?)_报出值D$",
    ):
        m = re.match(pat, t)
        if m:
            return m.group(1)
    return None


def row_prefix_from_binding(b: dict, pdf: dict) -> str | None:
    for key in (
        "reading_1",
        "reading_2",
        "reading_3",
        "reading_1_2",
        "reading_2_2",
        "reading_3_2",
        "mean_m",
        "mean_m_2",
        "report_d",
        "report_d_2",
    ):
        pid = b.get(key)
        if not pid:
            continue
        prefix = row_prefix_from_text(field_text(pdf, pid))
        if prefix:
            return prefix
    return None


def parse_row_from_prefix(pdf: dict, prefix: str) -> dict:
    row_fields = sorted(
        [
            (int(p[1:]), p, field_text(pdf, p))
            for p in pdf
            if field_text(pdf, p).startswith(prefix + "_")
        ],
        key=lambda x: x[0],
    )
    r1 = r2 = r3 = r1_2 = ""
    vals: list[str] = []
    means: list[str] = []
    reports: list[str] = []
    for _, p, t in row_fields:
        if "备注" in t:
            break
        if t.endswith("_检测点位置"):
            continue
        if "序号" in t and "报出" not in t and "测量" not in t:
            continue
        if t.endswith("_测量读数M1"):
            r1 = p
        elif t.endswith("_测量读数M2"):
            r2 = p
        elif t.endswith("_测量读数M3"):
            r3 = p
        elif t.endswith("_测量均值Mbar"):
            means.append(p)
        elif t.endswith("_测量读数M"):
            r1_2 = p
        elif t.endswith("_测量值"):
            vals.append(p)
        elif t.endswith("_报出值D"):
            reports.append(p)
    return {
        "reading_1": r1,
        "reading_2": r2,
        "reading_3": r3,
        "mean_m": means[0] if means else "",
        "reading_1_2": r1_2,
        "reading_2_2": vals[0] if vals else "",
        "reading_3_2": vals[1] if len(vals) > 1 else "",
        "mean_m_2": means[1] if len(means) > 1 else "",
        "report_d": reports[0] if reports else "",
        "report_d_2": reports[1] if len(reports) > 1 else "",
    }


def find_value_fields_between(pdf: dict, start_pid: str, end_pid: str) -> list[str]:
    if not start_pid or not end_pid:
        return []
    lo, hi = int(start_pid[1:]), int(end_pid[1:])
    out: list[str] = []
    for i in range(lo + 1, hi):
        pid = f"f{i}"
        if pid not in pdf:
            continue
        t = field_text(pdf, pid)
        if "测量值" in t or t.startswith("栏位"):
            out.append(pid)
    return out


def shift_dual_binding(b: dict, pdf: dict) -> dict:
    if b.get("reading_3") or not b.get("reading_1_2"):
        return b
    out = dict(b)
    o12, o22, o32 = out.get("reading_1_2"), out.get("reading_2_2"), out.get("reading_3_2")
    mean2 = out.get("mean_m_2")
    out["reading_3"] = o12 or ""
    out["reading_1_2"] = o22 or ""
    out["reading_2_2"] = o32 or ""
    if mean2:
        anchor = o32 or o22
        extras = find_value_fields_between(pdf, anchor, mean2) if anchor else []
        if o32:
            out["reading_3_2"] = extras[0] if extras else ""
        else:
            if extras:
                out["reading_2_2"] = extras[0]
            if len(extras) > 1:
                out["reading_3_2"] = extras[1]
            elif not extras:
                out["reading_3_2"] = ""
    else:
        out["reading_3_2"] = ""
    return out


def infer_seq_no_map(pdf: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for pid, f in pdf.items():
        t = str(f.get("id") or "")
        m = re.match(r"序号(_r\d+)$", t)
        if m and m.group(1) not in out:
            out[m.group(1)] = pid
    return out


def row_rn_from_binding(b: dict, pdf: dict) -> str | None:
    for key in ("reading_1", "reading_2", "reading_3", "reading_1_2", "mean_m"):
        pid = b.get(key)
        if not pid:
            continue
        m = re.search(r"(_r\d+)_", field_text(pdf, pid))
        if m:
            return m.group(1)
    return None


def sync_field_from_pdf(fld: dict, pdf: dict) -> None:
    pid = export_field_pdf_id(fld)
    if not pid or pid not in pdf:
        return
    src = pdf[pid]
    expr = str(src.get("fieldExpression") or src.get("pdfFieldExpression") or "").strip()
    if expr:
        fld["fieldExpression"] = expr
        fld["formula"] = expr
    else:
        for k in ("fieldExpression", "pdfFieldExpression", "formula"):
            fld.pop(k, None)
    if not src.get("fieldFormulaUserOverride"):
        fld.pop("fieldFormulaUserOverride", None)
    fr = src.get("formulaRules")
    if isinstance(fr, list) and fr:
        fld["formulaRules"] = copy.deepcopy(fr)
    else:
        fld.pop("formulaRules", None)
        fld.pop("fieldExpressionRules", None)
    src_obj = fld.get("source")
    if isinstance(src_obj, dict):
        if expr:
            src_obj["fieldExpression"] = expr
            src_obj["pdfFieldExpression"] = expr
        else:
            src_obj.pop("fieldExpression", None)
            src_obj.pop("pdfFieldExpression", None)


def binding_primary_prefix(b: dict, pdf: dict) -> str | None:
    for key in ("reading_1", "reading_3", "reading_1_2", "reading_2", "mean_m"):
        pid = b.get(key)
        if not pid:
            continue
        t = field_text(pdf, pid)
        if t.startswith("栏位"):
            continue
        prefix = row_prefix_from_text(t)
        if prefix:
            return prefix
    return None


def slot_usage(bindings: list[dict]) -> dict[str, list[tuple[str, str]]]:
    from collections import defaultdict

    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for b in bindings:
        rp = str(b.get("radiationPoint") or "")
        for key in SLOT_KEYS:
            if key in ("radiationPoint", "row", "remark", "seq_no"):
                continue
            pid = b.get(key)
            if pid:
                out[str(pid)].append((rp, key))
    return {pid: items for pid, items in out.items() if len(items) > 1}


def rebind_row_from_coord(
    b: dict,
    coord: dict,
    pdf: dict,
) -> dict:
    row = {k: b.get(k, "") for k in ("radiationPoint", "row", "seq_no", "remark")}
    prefix = binding_primary_prefix(coord, pdf) or binding_primary_prefix(b, pdf)
    if not prefix:
        return copy.deepcopy(b)
    parsed = parse_row_from_prefix(pdf, prefix)
    for key in SLOT_KEYS:
        if key in ("radiationPoint", "row", "remark", "seq_no"):
            continue
        row[key] = parsed.get(key) or ""
    if not row.get("reading_3") and row.get("reading_1_2"):
        row = shift_dual_binding(row, pdf)
    return row


def reconcile_bindings_with_coordinates(
    bindings: list[dict],
    fields: list[dict],
    chapter: dict,
    template_data: dict,
    pdf: dict,
) -> int:
    coord_by_rp = {
        str(b.get("radiationPoint") or ""): b
        for b in build_field_bindings_from_pdf_fields(
            fields,
            chapter=chapter,
            template_payload=template_data,
        )
    }
    fixed = 0
    for idx, b in enumerate(bindings):
        if "本底" in str(b.get("radiationPoint") or ""):
            continue
        rp = str(b.get("radiationPoint") or "")
        coord = coord_by_rp.get(rp)
        if not coord:
            continue
        if rp in LANWEI_PATCHES and any(
            LANWEI_PATCHES[rp].get(k) in pdf for k in LANWEI_PATCHES[rp] if LANWEI_PATCHES[rp].get(k)
        ):
            continue
        cur_prefix = binding_primary_prefix(b, pdf)
        coord_prefix = binding_primary_prefix(coord, pdf)
        usage = slot_usage(bindings)
        has_dup = any(
            b.get(key) in usage
            for key in SLOT_KEYS
            if key not in ("radiationPoint", "row", "remark", "seq_no") and b.get(key)
        )
        if has_dup or (coord_prefix and cur_prefix != coord_prefix):
            bindings[idx] = rebind_row_from_coord(b, coord, pdf)
            fixed += 1
    return fixed


def restore_missing_coord_bindings(
    bindings: list[dict],
    fields: list[dict],
    chapter: dict,
    template_data: dict,
    pdf: dict,
) -> int:
    coord_by_rp = {
        str(b.get("radiationPoint") or ""): b
        for b in build_field_bindings_from_pdf_fields(
            fields,
            chapter=chapter,
            template_payload=template_data,
        )
    }
    existing = {str(b.get("radiationPoint") or "") for b in bindings}
    added = 0
    for rp, coord in sorted(coord_by_rp.items(), key=lambda x: x[0]):
        if not rp or rp in existing or "本底" in rp:
            continue
        bindings.append(rebind_row_from_coord(coord, coord, pdf))
        added += 1
    return added


def clear_mismatched_chapter_formulas(
    bindings: list[dict],
    pdf: dict[str, dict],
    chapter: dict,
) -> int:
    """绑定与栏位公式不一致时，清除 fieldFormulaUserOverride 以便章节公式重算。"""
    rules = chapter.get("reportValueRules") if isinstance(chapter.get("reportValueRules"), list) else []
    cleared = 0
    for binding in bindings:
        if not isinstance(binding, dict) or "本底" in str(binding.get("radiationPoint") or ""):
            continue
        for group in (1, 2):
            mean_key = "mean_m" if group == 1 else "mean_m_2"
            mean_pid = str(binding.get(mean_key) or "").strip()
            if not mean_pid or mean_pid not in pdf:
                continue
            expected = mean_expression_for_binding(binding, group=group)
            field = pdf[mean_pid]
            current = str(field.get("fieldExpression") or field.get("pdfFieldExpression") or "").strip()
            if expected and current != expected:
                field.pop("fieldFormulaUserOverride", None)
                field.pop("fieldExpression", None)
                field.pop("pdfFieldExpression", None)
                cleared += 1
        for group in (1, 2):
            report_key = "report_d" if group == 1 else "report_d_2"
            report_pid = str(binding.get(report_key) or "").strip()
            if not report_pid or report_pid not in pdf:
                continue
            field = pdf[report_pid]
            expected = report_expression_for_binding(rules, binding, report_group=group)
            current = str(field.get("fieldExpression") or field.get("pdfFieldExpression") or "").strip()
            field_rules = field.get("formulaRules")
            rules_stale = (
                isinstance(field_rules, list)
                and field_rules
                and not _field_rules_are_chapter_sourced(field_rules, rules)
            )
            has_chapter_tokens = "{mean" in current or (
                isinstance(field_rules, list)
                and any("{mean" in str(r.get("expression") or r.get("formula") or "") for r in field_rules)
            )
            if expected and (
                current != expected
                or rules_stale
                or has_chapter_tokens
                or current.startswith("if(")
            ):
                field.pop("fieldFormulaUserOverride", None)
                field.pop("fieldExpression", None)
                field.pop("pdfFieldExpression", None)
                field.pop("formula", None)
                if rules_stale or has_chapter_tokens:
                    field.pop("formulaRules", None)
                    field.pop("fieldExpressionRules", None)
                cleared += 1
    return cleared


def clear_stale_protection_formula_overrides(fields: list[dict]) -> int:
    """清除无实际公式的 fieldFormulaUserOverride，避免章节公式无法写入。"""
    cleared = 0
    for field in fields:
        if not isinstance(field, dict):
            continue
        if not field.get("fieldFormulaUserOverride"):
            continue
        if field.get("fieldExpression") or field.get("pdfFieldExpression") or field.get("formulaRules"):
            continue
        label = str(field.get("id") or field.get("label") or "")
        if "测量均值" in label or ("报出值" in label and "本底" not in label):
            field.pop("fieldFormulaUserOverride", None)
            cleared += 1
    return cleared


def ensure_dual_report_value_rules(chapter: dict, fields: list[dict], *, template_name: str = "") -> None:
    override = TEMPLATE_REPORT_RULE_OVERRIDES.get(template_name)
    if override:
        g1_expr, g2_expr = override
    else:
        g1 = _chapter_g1_calibration_factor_pid(chapter) or resolve_background_calibration_factor_pid(chapter, fields) or "f20"
        g2 = _chapter_g2_calibration_factor_pid(chapter) or g1
        g1_expr = f"{{mean}}*{g1}"
        g2_expr = f"{{mean2}}*{g2}"
    chapter["reportValueRules"] = [
        {
            "id": "rule_ge_response",
            "label": "出束时间≥仪器响应时间",
            "condition": "",
            "expression": g1_expr,
        },
        {
            "id": "rule_lt_response",
            "label": "出束时间＜仪器响应时间",
            "condition": "",
            "expression": g2_expr,
        },
    ]


def apply_report_formulas_by_bindings(
    fields: list[dict],
    bindings: list[dict],
    chapter: dict,
) -> int:
    rules = chapter.get("reportValueRules") if isinstance(chapter.get("reportValueRules"), list) else []
    if not rules:
        return 0
    by_pid = {
        export_field_pdf_id(field): field
        for field in fields
        if isinstance(field, dict) and export_field_pdf_id(field)
    }
    applied = 0
    for binding in bindings:
        if not isinstance(binding, dict) or "本底" in str(binding.get("radiationPoint") or ""):
            continue
        for group in (1, 2):
            report_key = "report_d" if group == 1 else "report_d_2"
            report_pid = str(binding.get(report_key) or "").strip()
            if not report_pid or report_pid not in by_pid:
                continue
            apply_chapter_report_rules_to_field(
                by_pid[report_pid],
                rules,
                binding,
                report_group=group,
            )
            applied += 1
    return applied


def is_mean_bar_pdf_field_label(label: str) -> bool:
    return "_测量均值Mbar" in label or label.endswith("测量均值Mbar")


def fix_template(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    chapter = data.setdefault("radiationProtectionChapter", {})
    fields = data["pdf"]["fields"]
    pdf = {export_field_pdf_id(f): f for f in fields if export_field_pdf_id(f)}

    old_by_rp = {
        str(b.get("radiationPoint") or ""): b
        for b in (chapter.get("fieldBindings") or [])
        if isinstance(b, dict)
    }

    new_bindings = build_field_bindings_from_fields(
        fields,
        chapter=chapter,
        template_payload=data,
    )
    for b in new_bindings:
        rp = str(b.get("radiationPoint") or "")
        old = old_by_rp.get(rp)
        if not old:
            continue
        if old.get("seq_no") and not b.get("seq_no"):
            b["seq_no"] = old["seq_no"]
        if old.get("remark") and not b.get("remark"):
            b["remark"] = old["remark"]

    for rp, patch in {**PARTIAL_ROW_PATCHES, **LANWEI_PATCHES}.items():
        if not patch:
            continue
        target = next((b for b in new_bindings if b.get("radiationPoint") == rp), None)
        if not target:
            continue
        for key, pid in patch.items():
            if pid and pid not in pdf:
                continue
            target[key] = pid or ""

    chapter["fieldBindings"] = new_bindings
    seq_map = infer_seq_no_map(pdf)
    for b in new_bindings:
        if b.get("seq_no"):
            continue
        rn = row_rn_from_binding(b, pdf)
        if rn and rn in seq_map:
            b["seq_no"] = seq_map[rn]

    col_layout = resolve_chapter5_layout_for_fields(fields)
    attach_seq_no_to_bindings(new_bindings, fields, column_layout=col_layout)

    cleared = clear_stale_protection_formula_overrides(fields)
    mismatched = clear_mismatched_chapter_formulas(new_bindings, pdf, chapter)
    ensure_dual_report_value_rules(chapter, fields, template_name=path.name)

    for pid, f in pdf.items():
        if is_mean_bar_pdf_field_label(field_text(pdf, pid)):
            f.pop("fieldExpression", None)
            f.pop("pdfFieldExpression", None)
    apply_mean_formulas_to_pdf_fields(fields, chapter=chapter)
    report_applied = apply_report_formulas_by_bindings(fields, new_bindings, chapter)
    apply_background_formulas_to_pdf_fields(fields, chapter=chapter)

    for binding in new_bindings:
        if not isinstance(binding, dict) or "本底" in str(binding.get("radiationPoint") or ""):
            continue
        rules = chapter.get("reportValueRules") if isinstance(chapter.get("reportValueRules"), list) else []
        for group in (1, 2):
            report_key = "report_d" if group == 1 else "report_d_2"
            report_pid = str(binding.get(report_key) or "").strip()
            if report_pid in pdf:
                report_field = pdf[report_pid]
                report_field.pop("formulaRules", None)
                report_field.pop("fieldExpressionRules", None)
                apply_chapter_report_rules_to_field(
                    report_field,
                    rules,
                    binding,
                    report_group=group,
                    force=True,
                )

    mean_pids = set()
    for b in new_bindings:
        for k in ("mean_m", "mean_m_2"):
            if b.get(k):
                mean_pids.add(b[k])

    for pid, f in pdf.items():
        if is_mean_bar_pdf_field_label(field_text(pdf, pid)) and pid not in mean_pids:
            f.pop("fieldExpression", None)
            f.pop("pdfFieldExpression", None)

    reading_pids: set[str] = set()
    for b in new_bindings:
        for k in READING_KEYS:
            if b.get(k):
                reading_pids.add(b[k])

    for pid, f in pdf.items():
        if pid in reading_pids:
            continue
        expr = str(f.get("fieldExpression") or "")
        t = field_text(pdf, pid)
        if "测量值" in t and expr.startswith("avg("):
            f.pop("fieldExpression", None)
            f.pop("pdfFieldExpression", None)

    for fld in iter_frontend_export_fields(data):
        sync_field_from_pdf(fld, pdf)
    steps = _iter_form_schema_steps(data)
    if steps:
        for fld in _iter_frontend_field_dicts({"steps": steps}):
            sync_field_from_pdf(fld, pdf)
    if isinstance(data.get("formSchema"), dict):
        rpc = data["formSchema"].get("radiationProtectionChapter")
        if isinstance(rpc, dict):
            rpc["fieldBindings"] = copy.deepcopy(new_bindings)
            rpc["reportValueRules"] = copy.deepcopy(chapter.get("reportValueRules") or [])

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        path.name,
        "cleared",
        cleared,
        "mismatched",
        mismatched,
        "report",
        report_applied,
        "bindings",
        len(new_bindings),
    )


def main() -> None:
    targets = [
        ROOT
        / "media/file_library/templates/001-验收检测/08-口腔CBCT/site/jxfs-js116-v20-xcbct-202641/current/1e71d6cb901d41d489d0269e4e9e303f.json",
        ROOT
        / "media/file_library/templates/001-验收检测/08-口腔CBCT/site/xcbct-1/current/9f0eedfbd0b54cd78c007f23795cb64b.json",
        ROOT
        / "media/file_library/templates/002-状态检测/08-口腔CBCT/site/oral-cbct-status-site/current/6e74a0e710ca4993b46c3f7f1b62feb2.json",
        ROOT
        / "media/file_library/templates/002-状态检测/08-口腔CBCT/site/jxfs-js008-v30-x202641-1/current/f96fd70fc3734da4a6208fd0b4575b95.json",
    ]
    for path in targets:
        if path.is_file():
            fix_template(path)


if __name__ == "__main__":
    main()
