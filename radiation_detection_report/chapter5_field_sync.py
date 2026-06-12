"""
工作场所放射防护（第五章）模板栏位同步与章节公式编译。

- 从模板编辑器 pdf.fields 提取 autoSemantic，生成 field_bindings（含 pdfFieldId）
- 章节公式：测量均值自动 avg(三次读数)；报出值按条件规则复用到各行
- 额外元数据写入 formSchema.radiationProtectionChapter，并同步到 tabletest 版式 JSON
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from utils.conditional_field_rules import (
    compile_conditional_expression,
    export_formula_rules_list,
    export_rule_for_frontend,
    normalize_rule_list,
)

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_LAYOUT_PATH = PACKAGE_DIR / "layout" / "tabletest_chapter5_full.json"

CHAPTER_KEY = "site_radiation_protection"
SCHEMA_KEY = "radiationProtectionChapter"

ROW_TOKEN_MEAN = "__ROW_MEAN__"
ROW_TOKEN_READING = (
    ("reading1", "__ROW_READING1__", "测量读数M", 1),
    ("reading2", "__ROW_READING2__", "测量读数M", 2),
    ("reading3", "__ROW_READING3__", "测量读数M", 3),
)
ROW_TOKEN_REPORT = "__ROW_REPORT__"

_COLUMN_READING = "测量读数M"
_COLUMN_MEAN = "测量均值Mbar"
_COLUMN_REPORT = "报出值D"


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip())


def field_table_semantic(field: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(field, dict):
        return {}
    sem = field.get("autoSemantic")
    if isinstance(sem, dict):
        return sem
    return {}


def field_pdf_field_id(field: Mapping[str, Any]) -> str:
    pid = _norm(field.get("pdfFieldId") or field.get("id") or "")
    m = re.match(r"^f\d+$", pid, re.I)
    return pid.lower() if m else ""


_RP_SECTION_KEYS = frozenset({CHAPTER_KEY, "radiation_protection"})


def _field_label_blob(field: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("placeholder", "title", "label", "hierarchyKey", "id"):
        v = _norm(field.get(key) or "")
        if v:
            parts.append(v)
    return " ".join(parts)


def field_looks_like_protection_table_cell(field: Mapping[str, Any]) -> bool:
    """steps 栏位无 sectionKey 时，用标签识别防护表数据格（读数/均值/报出值）。"""
    if not isinstance(field, dict):
        return False
    blob = _field_label_blob(field)
    if not blob or "本底" in blob or "序号本底水平" in blob:
        return False
    if re.search(r"_测量读数M\d*|_测量均值|_报出值", blob, re.I):
        return True
    return False


def is_protection_pdf_field(field: Mapping[str, Any]) -> bool:
    if not isinstance(field, dict):
        return False
    sk = _norm(field.get("templateSectionKey") or field.get("sectionKey") or "")
    if sk in _RP_SECTION_KEYS:
        return True
    if _norm(field.get("sectionType") or "") == "radiationProtection":
        return True
    sem = field_table_semantic(field)
    if sem.get("tableId") or sem.get("radiationPoint") or sem.get("radiationColumn"):
        return True
    if field_looks_like_protection_table_cell(field):
        return True
    ph = _field_label_blob(field)
    return "放射防护" in ph or "工作场所" in ph


def _field_display_label(field: Mapping[str, Any]) -> str:
    for key in ("placeholder", "title", "label", "id"):
        v = _norm(field.get(key) or "")
        if v:
            return v
    return ""


_PROTECTION_ROW_SUFFIX_PATTERNS = (
    re.compile(r"_测量读数M(\d+)$", re.I),
    re.compile(r"_测量读数M$", re.I),
    re.compile(r"_测量均值Mbar$", re.I),
    re.compile(r"_测量均值.*$", re.I),
    re.compile(r"_报出值D$", re.I),
    re.compile(r"_报出值$", re.I),
    re.compile(r"_备注$", re.I),
)


def _protection_row_point_key(field: Mapping[str, Any], sem: Mapping[str, Any]) -> str:
    """同一检测点位：工作人员操作位K_r1_测量读数M1 → 工作人员操作位K_r1。"""
    pt = _norm(sem.get("radiationPoint") or "")
    if pt:
        return pt
    label = _field_display_label(field)
    if not label:
        return ""
    for pat in _PROTECTION_ROW_SUFFIX_PATTERNS:
        m = pat.search(label)
        if m:
            return _norm(label[: m.start()])
    return label


def _reading_index_from_field(sem: Mapping[str, Any], field: Mapping[str, Any]) -> int:
    try:
        idx = int(sem.get("readingIndex") or 0)
    except (TypeError, ValueError):
        idx = 0
    if idx:
        return idx
    label = _field_display_label(field)
    m = re.search(r"读数M?(\d+)", label, re.I)
    if m:
        try:
            return int(m.group(1))
        except (TypeError, ValueError):
            pass
    return 0


def _column_role(sem: Mapping[str, Any], field: Mapping[str, Any]) -> str:
    col = _norm(sem.get("radiationColumn") or "")
    if col:
        return col
    if sem.get("meanOfReadings") or field.get("mean"):
        return _COLUMN_MEAN
    ph = _field_display_label(field)
    if "报出" in ph:
        return _COLUMN_REPORT
    if "均值" in ph or "平均" in ph:
        return _COLUMN_MEAN
    if "读数" in ph:
        return _COLUMN_READING
    return ""


def protection_field_semantic_unified(field: Mapping[str, Any]) -> Dict[str, Any]:
    """PDF 栏位或导出 steps 栏位上的防护表语义。"""
    sem = field_table_semantic(field)
    if sem:
        return sem
    if isinstance(field.get("table"), dict):
        return field["table"]
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    sem2 = src.get("autoSemantic")
    return sem2 if isinstance(sem2, dict) else {}


def export_field_pdf_id(field: Mapping[str, Any]) -> str:
    pid = field_pdf_field_id(field)
    if pid:
        return pid
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    alt = _norm(src.get("pdfFieldId") or field.get("id") or "")
    m = re.match(r"^f\d+$", alt, re.I)
    return alt.lower() if m else ""


def is_protection_unified_field(field: Mapping[str, Any]) -> bool:
    if is_protection_pdf_field(field):
        return True
    sem = protection_field_semantic_unified(field)
    if sem.get("tableId") or sem.get("radiationPoint") or sem.get("radiationColumn"):
        return True
    sk = _norm(field.get("templateSectionKey") or field.get("sectionKey") or "")
    return sk in _RP_SECTION_KEYS


def build_field_bindings_from_fields(fields: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """按 radiationPoint 聚合第五章栏位，记录各列 pdfFieldId（PDF 或 steps 均可）。"""
    rows: Dict[str, Dict[str, Any]] = {}
    for field in fields or []:
        if not is_protection_unified_field(field):
            continue
        sem = protection_field_semantic_unified(field)
        pid = export_field_pdf_id(field)
        if not pid:
            continue
        role = _column_role(sem, field)
        if not role and not sem.get("radiationPoint"):
            continue
        pt = _protection_row_point_key(field, sem)
        if not pt:
            continue
        row = rows.setdefault(
            pt,
            {
                "radiationPoint": pt,
                "row": sem.get("row"),
                "reading_1": "",
                "reading_2": "",
                "reading_3": "",
                "mean_m": "",
                "report_d": "",
                "remark": "",
            },
        )
        if role == _COLUMN_READING:
            idx = _reading_index_from_field(sem, field)
            if idx == 1:
                row["reading_1"] = pid
            elif idx == 2:
                row["reading_2"] = pid
            elif idx == 3:
                row["reading_3"] = pid
            else:
                for k in ("reading_1", "reading_2", "reading_3"):
                    if not row[k]:
                        row[k] = pid
                        break
        elif role == _COLUMN_MEAN or sem.get("meanOfReadings"):
            row["mean_m"] = pid
        elif role == _COLUMN_REPORT:
            row["report_d"] = pid
        elif role == "备注":
            row["remark"] = pid
    out = list(rows.values())
    out.sort(key=lambda r: (r.get("row") is None, r.get("row") or 0, r.get("radiationPoint") or ""))
    return out


def build_field_bindings_from_pdf_fields(fields: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return build_field_bindings_from_fields(fields)


def export_chapter_config_for_frontend(chapter: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """前端 JSON 仅暴露章节公式配置，不含 fieldBindings（后端自行维护）。"""
    if not isinstance(chapter, dict):
        return default_chapter_config()
    out: Dict[str, Any] = {
        "chapterKey": _norm(chapter.get("chapterKey")) or CHAPTER_KEY,
        "meanFormula": copy.deepcopy(
            chapter.get("meanFormula") if isinstance(chapter.get("meanFormula"), dict) else default_chapter_config()["meanFormula"]
        ),
    }
    rules = chapter.get("reportValueRules")
    if isinstance(rules, list):
        out["reportValueRules"] = export_formula_rules_list(rules)
    else:
        out["reportValueRules"] = copy.deepcopy(default_chapter_config()["reportValueRules"])
    return out


def _chapter_rule_ids(rules: List[Mapping[str, Any]]) -> set[str]:
    return {
        _norm(r.get("id"))
        for r in normalize_rule_list(rules)
        if _norm(r.get("id"))
    }


def _field_rules_are_chapter_sourced(
    field_rules: Any,
    chapter_rules: List[Mapping[str, Any]],
) -> bool:
    """栏位上的条件公式是否与章节 reportValueRules 同源（导出曾写入栏位，非用户单格编辑）。"""
    if not isinstance(field_rules, list) or not field_rules:
        return False
    fr = {
        _norm(r.get("id"))
        for r in field_rules
        if isinstance(r, dict) and _norm(r.get("id"))
    }
    cr = _chapter_rule_ids(chapter_rules)
    return bool(fr) and fr == cr


def field_has_per_cell_formula_override(
    field: Mapping[str, Any],
    *,
    chapter_rules: Optional[List[Mapping[str, Any]]] = None,
) -> bool:
    """栏位已单独配置公式时，不再套用章节级报出值公式。"""
    if not isinstance(field, dict):
        return False
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    chapter_rules = chapter_rules if isinstance(chapter_rules, list) else []
    for key in ("formulaRules", "fieldExpressionRules"):
        val = field.get(key)
        if isinstance(val, list) and val:
            if chapter_rules and _field_rules_are_chapter_sourced(val, chapter_rules):
                continue
            return True
        sval = src.get(key)
        if isinstance(sval, list) and sval:
            if chapter_rules and _field_rules_are_chapter_sourced(sval, chapter_rules):
                continue
            return True
    for key in ("fieldExpression", "formula"):
        if _norm(field.get(key)):
            return True
    for key in ("pdfFieldExpression", "fieldExpression"):
        if _norm(src.get(key)):
            return True
    return False


def default_chapter_config() -> Dict[str, Any]:
    return {
        "chapterKey": CHAPTER_KEY,
        "meanFormula": {
            "mode": "per_row_avg",
            "description": "同点位三次测量读数取平均",
        },
        "reportValueRules": [
            {
                "id": "rule_ge_response",
                "label": "出束时间≥仪器响应时间",
                "condition": "",
                "formula": "",
            },
            {
                "id": "rule_lt_response",
                "label": "出束时间＜仪器响应时间",
                "condition": "",
                "formula": "",
            },
        ],
        "fieldBindings": [],
    }


def build_chapter_state(
    fields: List[Mapping[str, Any]],
    existing: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    state = copy.deepcopy(existing) if isinstance(existing, dict) else default_chapter_config()
    state["chapterKey"] = CHAPTER_KEY
    state["fieldBindings"] = build_field_bindings_from_pdf_fields(fields)
    state.setdefault("meanFormula", default_chapter_config()["meanFormula"])
    rules = state.get("reportValueRules")
    if not isinstance(rules, list) or not rules:
        state["reportValueRules"] = default_chapter_config()["reportValueRules"]
    return state


def _substitute_row_tokens(expr: str, binding: Mapping[str, Any]) -> str:
    out = str(expr or "")
    repl = {
        ROW_TOKEN_MEAN: _norm(binding.get("mean_m") or ""),
        "__ROW_MEAN__": _norm(binding.get("mean_m") or ""),
        "{mean}": _norm(binding.get("mean_m") or ""),
        "__ROW_READING1__": _norm(binding.get("reading_1") or ""),
        "__ROW_READING2__": _norm(binding.get("reading_2") or ""),
        "__ROW_READING3__": _norm(binding.get("reading_3") or ""),
        "{reading1}": _norm(binding.get("reading_1") or ""),
        "{reading2}": _norm(binding.get("reading_2") or ""),
        "{reading3}": _norm(binding.get("reading_3") or ""),
    }
    for token, fid in repl.items():
        if token and fid:
            out = out.replace(token, fid)
    return out.strip()


def compile_report_expression(
    rules: List[Mapping[str, Any]],
    binding: Mapping[str, Any],
    *,
    default_formula: str = "",
) -> str:
    """将含 condition 的章节规则编译为 if 嵌套；condition 为空的规则不写入此表达式（前端人工选公式）。"""
    substituter: Callable[[str], str] = lambda s: _substitute_row_tokens(s, binding)
    normalized = normalize_rule_list(rules)
    return compile_conditional_expression(
        normalized,
        substituter=substituter,
        default_expression=_substitute_row_tokens(default_formula, binding),
    )


def manual_report_value_rules_for_binding(
    rules: List[Mapping[str, Any]],
    binding: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """condition 为空的章节报出值规则 → 栏位级列表（{mean} 已替换为该行均值 f 号）。"""
    manual_rows: List[Dict[str, Any]] = []
    for rule in normalize_rule_list(rules):
        cond = _norm(rule.get("condition"))
        expr = _norm(rule.get("expression") or rule.get("formula"))
        if cond or not expr:
            continue
        row = export_rule_for_frontend(rule)
        row["expression"] = _substitute_row_tokens(row.get("expression") or "", binding)
        if row["expression"]:
            manual_rows.append(row)
    return manual_rows


def _write_manual_report_rules_to_field(
    report_field: Dict[str, Any],
    manual: List[Dict[str, Any]],
    *,
    auto_expr: str = "",
    chapter_rules: Optional[List[Mapping[str, Any]]] = None,
) -> None:
    """单元格级条件公式：单条默认自动；多条且含自动规则时写入栏位备选。"""
    if not manual:
        return
    if len(manual) == 1 and not auto_expr:
        _set_chapter_field_expression(
            report_field,
            manual[0].get("expression") or "",
            chapter_rules=chapter_rules,
        )
        return
    report_field["formulaRules"] = copy.deepcopy(manual)
    report_field.pop("fieldExpressionRules", None)
    if str(report_field.get("type") or "").lower() in ("", "number"):
        report_field["type"] = "computed"


def _binding_with_mean_fallback(
    binding: Mapping[str, Any],
    report_field: Mapping[str, Any],
) -> Dict[str, Any]:
    """绑定行补全 mean_m：优先 fieldBindings，其次栏位已有 chapterMeanPdfFieldId。"""
    out = dict(binding) if isinstance(binding, dict) else {}
    mean_pid = _norm(out.get("mean_m") or "") or _norm(report_field.get("chapterMeanPdfFieldId") or "")
    if mean_pid:
        out["mean_m"] = mean_pid
    report_pid = _norm(out.get("report_d") or "") or export_field_pdf_id(report_field)
    if report_pid:
        out["report_d"] = report_pid
    return out


def apply_chapter_report_rules_to_field(
    report_field: Dict[str, Any],
    rules: List[Mapping[str, Any]],
    binding: Mapping[str, Any],
    *,
    force: bool = False,
) -> None:
    """
    报出值栏位（steps / pdf.fields 均可）：
    - condition 非空 → fieldExpression（if 嵌套，自动判定）；
    - condition 为空 → 写入 formulaRules（{mean} 已换行内 f 号），由前端按 §3.2 人工选公式；
    - 仅一条 condition 为空且无自动条 → 写 fieldExpression（默认公式）。
    """
    if not force and _field_has_user_cell_formula(report_field, chapter_rules=rules):
        return
    binding = _binding_with_mean_fallback(binding, report_field)
    mean_pid = _norm(binding.get("mean_m") or "")
    if mean_pid:
        report_field["chapterMeanPdfFieldId"] = mean_pid
    auto_expr = compile_report_expression(rules, binding)
    if auto_expr:
        _set_chapter_field_expression(report_field, auto_expr, chapter_rules=rules)
    manual = manual_report_value_rules_for_binding(rules, binding)
    _write_manual_report_rules_to_field(
        report_field,
        manual,
        auto_expr=auto_expr,
        chapter_rules=rules,
    )


def apply_report_formulas_to_pdf_fields(
    fields: List[Any],
    *,
    chapter: Optional[Mapping[str, Any]] = None,
) -> None:
    """保存坐标模板时：章节报出值公式写入各「报出值」pdf.fields 栏位。"""
    chapter = chapter if isinstance(chapter, dict) else {}
    rules = chapter.get("reportValueRules") if isinstance(chapter.get("reportValueRules"), list) else []
    if not rules:
        return
    bindings = chapter.get("fieldBindings") if isinstance(chapter.get("fieldBindings"), list) else None
    if not bindings:
        bindings = build_field_bindings_from_pdf_fields(fields)
    by_report = {
        _norm(b.get("report_d") or ""): b
        for b in bindings
        if isinstance(b, dict) and _norm(b.get("report_d") or "")
    }
    for field in fields or []:
        if not isinstance(field, dict):
            continue
        if not is_protection_pdf_field(field):
            continue
        sem = field_table_semantic(field)
        if _column_role(sem, field) != _COLUMN_REPORT:
            continue
        pid = field_pdf_field_id(field)
        binding = by_report.get(pid) or {}
        apply_chapter_report_rules_to_field(field, rules, binding)


def export_report_value_rules_for_frontend(
    rules: List[Mapping[str, Any]],
    binding: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    normalized = normalize_rule_list(rules)
    exported = export_formula_rules_list(normalized)
    for row, src in zip(exported, normalized):
        expr = _norm(src.get("expression"))
        if binding and expr:
            row["compiledExpression"] = _substitute_row_tokens(expr, binding)
    return {"reportValueRules": exported}


def mean_expression_for_binding(binding: Mapping[str, Any]) -> str:
    refs = [_norm(binding.get(k) or "") for k in ("reading_1", "reading_2", "reading_3")]
    refs = [r for r in refs if r]
    if len(refs) >= 2:
        return f"avg({','.join(refs[:3])})"
    mean_pid = _norm(binding.get("mean_m") or "")
    return mean_pid


def apply_mean_formulas_to_pdf_fields(
    fields: List[Any],
    *,
    chapter: Optional[Mapping[str, Any]] = None,
) -> None:
    """保存坐标模板时：按 field_bindings 将 per_row_avg 均值公式写入 pdf.fields 均值列。"""
    chapter = chapter if isinstance(chapter, dict) else {}
    mean_cfg = chapter.get("meanFormula") if isinstance(chapter.get("meanFormula"), dict) else {}
    if str(mean_cfg.get("mode") or "per_row_avg").strip() != "per_row_avg":
        return
    bindings = chapter.get("fieldBindings") if isinstance(chapter.get("fieldBindings"), list) else None
    if not bindings:
        bindings = build_field_bindings_from_pdf_fields(fields)
    bind_map = bindings_by_point(bindings)
    for field in fields or []:
        if not isinstance(field, dict):
            continue
        if not is_protection_pdf_field(field):
            continue
        sem = field_table_semantic(field)
        role = _column_role(sem, field)
        if role != _COLUMN_MEAN and not sem.get("meanOfReadings"):
            continue
        pt = _protection_row_point_key(field, sem)
        binding = bind_map.get(pt) or {}
        expr = mean_expression_for_binding(binding)
        if expr and expr.lower().startswith("avg("):
            field["fieldExpression"] = expr


def iter_frontend_export_fields(payload: Mapping[str, Any]):
    """遍历前端导出 JSON 中的栏位（含 matrixTable 单元格）。"""
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return
    for step in steps:
        if not isinstance(step, dict):
            continue
        for sec in step.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            for fld in sec.get("fields") or []:
                if isinstance(fld, dict):
                    yield fld
            matrix = sec.get("matrix")
            if not isinstance(matrix, dict):
                continue
            for hf in matrix.get("headerFields") or []:
                if isinstance(hf, dict):
                    yield hf
            for row in matrix.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                cells = row.get("cells")
                if not isinstance(cells, dict):
                    continue
                for cell in cells.values():
                    if isinstance(cell, dict):
                        yield cell


def index_export_fields_by_pdf_field_id(payload: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for field in iter_frontend_export_fields(payload):
        pid = export_field_pdf_id(field)
        if pid:
            out[pid] = field
    return out


def _bindings_look_stale(bindings: List[Mapping[str, Any]]) -> bool:
    """旧版把「读数/均值/报出值」整段 id 当作 radiationPoint，需从 pdf.fields 重算。"""
    stale_markers = ("_测量读数", "_测量均值", "_报出值")
    for row in bindings or []:
        if not isinstance(row, dict):
            continue
        pt = _norm(row.get("radiationPoint") or "")
        if any(marker in pt for marker in stale_markers):
            return True
    return False


def resolve_chapter_field_bindings(
    chapter: Optional[Mapping[str, Any]],
    *,
    pdf_fields: Optional[List[Any]] = None,
    export_payload: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """解析第五章 field_bindings：优先 pdf.fields 重算；章节内仅存且未过期时沿用。"""
    if pdf_fields:
        built = build_field_bindings_from_pdf_fields(pdf_fields)
        if built:
            return built
    if isinstance(chapter, dict):
        raw = chapter.get("fieldBindings")
        if isinstance(raw, list) and raw and not _bindings_look_stale(raw):
            return [dict(x) for x in raw if isinstance(x, dict)]
    if export_payload is not None:
        return build_field_bindings_from_fields(list(iter_frontend_export_fields(export_payload)))
    return []


def _field_has_user_cell_formula(
    field: Mapping[str, Any],
    *,
    chapter_rules: Optional[List[Mapping[str, Any]]] = None,
) -> bool:
    return field_has_per_cell_formula_override(field, chapter_rules=chapter_rules)


def _set_chapter_field_expression(
    field: Dict[str, Any],
    expr: str,
    *,
    chapter_rules: Optional[List[Mapping[str, Any]]] = None,
) -> None:
    """仅在栏位尚无单元格级公式时写入 fieldExpression，不改动 type/dependsOn 等其它键。"""
    expr = _norm(expr)
    if not expr or _field_has_user_cell_formula(field, chapter_rules=chapter_rules):
        return
    field["fieldExpression"] = expr


def resolve_chapter_config_for_export(
    chapter: Optional[Mapping[str, Any]] = None,
    *,
    pdf_fields: Optional[List[Any]] = None,
    export_payload: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    导出前端 JSON 兜底：合并模板已有章节配置与默认 meanFormula/reportValueRules，
    并从 pdf.fields 或 steps 反推 field_bindings（仅内部使用，不写入导出 JSON）。
    """
    base = copy.deepcopy(chapter) if isinstance(chapter, dict) else {}
    state: Dict[str, Any] = {**default_chapter_config(), **base}
    state["chapterKey"] = CHAPTER_KEY
    if not isinstance(state.get("meanFormula"), dict):
        state["meanFormula"] = default_chapter_config()["meanFormula"]
    rules = state.get("reportValueRules")
    if not isinstance(rules, list) or not rules:
        state["reportValueRules"] = default_chapter_config()["reportValueRules"]
    state["fieldBindings"] = resolve_chapter_field_bindings(
        state,
        pdf_fields=pdf_fields,
        export_payload=export_payload,
    )
    return state


def apply_chapter_formulas_by_pdf_field_bindings(
    payload: Dict[str, Any],
    *,
    bindings: List[Mapping[str, Any]],
    report_value_rules: Optional[List[Mapping[str, Any]]] = None,
    mean_mode: str = "per_row_avg",
) -> Dict[str, Any]:
    """按 field_bindings 的 pdfFieldId 将章节均值/报出值公式写入导出 JSON 各栏位。"""
    if not isinstance(payload, dict) or not bindings:
        return payload
    by_pid = index_export_fields_by_pdf_field_id(payload)
    rules = report_value_rules if isinstance(report_value_rules, list) else []

    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        mean_pid = _norm(binding.get("mean_m") or "")
        if mean_mode == "per_row_avg" and mean_pid and mean_pid in by_pid:
            expr = mean_expression_for_binding(binding)
            if expr:
                _set_chapter_field_expression(by_pid[mean_pid], expr)

        report_pid = _norm(binding.get("report_d") or "")
        if not report_pid or report_pid not in by_pid:
            continue
        report_field = by_pid[report_pid]
        apply_chapter_report_rules_to_field(report_field, rules, binding, force=True)

    # 兜底：绑定表未覆盖但 steps 上已有 chapterMeanPdfFieldId 的报出值栏位
    for pid, report_field in by_pid.items():
        if not isinstance(report_field, dict):
            continue
        if pid in {_norm(b.get("report_d") or "") for b in bindings if isinstance(b, dict)}:
            continue
        mean_pid = _norm(report_field.get("chapterMeanPdfFieldId") or "")
        if not mean_pid:
            continue
        sem = protection_field_semantic_unified(report_field)
        role = _column_role(sem, report_field)
        label = _field_display_label(report_field)
        if role != _COLUMN_REPORT and "报出" not in label:
            continue
        apply_chapter_report_rules_to_field(
            report_field,
            rules,
            {"report_d": pid, "mean_m": mean_pid},
            force=True,
        )

    return payload


def apply_chapter_formulas_to_export_payload(
    payload: Dict[str, Any],
    chapter: Optional[Mapping[str, Any]] = None,
    *,
    pdf_fields: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """
    导出前端 JSON 收尾（兜底）：按 pdfFieldId 写入章节均值 avg(...) 与报出值公式。
    模板无 ``radiationProtectionChapter`` 时仍从 pdf.fields / steps 反推绑定；
    栏位已有单元格公式（fieldExpression / formula / formulaRules 等）时不覆盖。
    导出 JSON 仅保留 ``radiationProtectionChapter`` 前端可见配置（不含 fieldBindings）。
    """
    if not isinstance(payload, dict):
        return payload
    chapter_in = chapter if isinstance(chapter, dict) else payload.get(SCHEMA_KEY)
    chapter = resolve_chapter_config_for_export(
        chapter_in if isinstance(chapter_in, dict) else None,
        pdf_fields=pdf_fields,
        export_payload=payload,
    )
    bindings = chapter.get("fieldBindings") if isinstance(chapter.get("fieldBindings"), list) else []
    if not bindings:
        return payload
    mean_cfg = chapter.get("meanFormula") if isinstance(chapter.get("meanFormula"), dict) else {}
    mean_mode = str(mean_cfg.get("mode") or "per_row_avg").strip()
    rules = chapter.get("reportValueRules") if isinstance(chapter.get("reportValueRules"), list) else []
    apply_chapter_formulas_by_pdf_field_bindings(
        payload,
        bindings=bindings,
        report_value_rules=rules,
        mean_mode=mean_mode,
    )
    # 根级章节配置：优先保留模板已保存的 reportValueRules（含 {mean} 占位），勿用默认空规则覆盖
    src_rules = None
    if isinstance(chapter_in, dict) and isinstance(chapter_in.get("reportValueRules"), list):
        src_rules = chapter_in.get("reportValueRules")
    exported = export_chapter_config_for_frontend(chapter)
    if isinstance(src_rules, list) and src_rules:
        exported["reportValueRules"] = export_formula_rules_list(src_rules)
    payload[SCHEMA_KEY] = exported
    return payload


def bindings_by_point(bindings: List[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in bindings or []:
        if not isinstance(row, dict):
            continue
        pt = _norm(row.get("radiationPoint") or "")
        if pt:
            out[pt] = dict(row)
    return out


def apply_chapter_formulas_to_frontend_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """规则引擎 finalize 阶段：按 pdfFieldId 写入章节公式（无章节配置时亦兜底）。"""
    if not isinstance(payload, dict):
        return payload
    chapter = payload.get(SCHEMA_KEY) if isinstance(payload.get(SCHEMA_KEY), dict) else None
    pdf_fields = None
    pdf = payload.get("pdf")
    if isinstance(pdf, dict) and isinstance(pdf.get("fields"), list):
        pdf_fields = pdf.get("fields")
    return apply_chapter_formulas_to_export_payload(payload, chapter, pdf_fields=pdf_fields)


def sync_table_template_bindings(
    layout: Dict[str, Any],
    bindings: List[Mapping[str, Any]],
    *,
    chapter_config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """把编辑器 pdfFieldId 写入 radiation_detection_report 表格模板。"""
    out = copy.deepcopy(layout) if isinstance(layout, dict) else {}
    table_tpl = out.setdefault("table_template", {})
    table_tpl["field_bindings"] = copy.deepcopy(list(bindings or []))
    if isinstance(chapter_config, dict):
        table_tpl["chapter_formulas"] = {
            "meanFormula": copy.deepcopy(chapter_config.get("meanFormula") or {}),
            "reportValueRules": copy.deepcopy(chapter_config.get("reportValueRules") or []),
        }
    return out


def update_reference_layout_file(
    chapter_state: Mapping[str, Any],
    layout_path: Optional[Path] = None,
) -> Path:
    path = Path(layout_path or DEFAULT_LAYOUT_PATH)
    if path.is_file():
        with path.open("r", encoding="utf-8") as f:
            layout = json.load(f)
    else:
        layout = {"schema": "workplace_radiation_js009_chapter5/v1", "table_template": {}}
    merged = sync_table_template_bindings(
        layout,
        chapter_state.get("fieldBindings") or [],
        chapter_config=chapter_state,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    return path
