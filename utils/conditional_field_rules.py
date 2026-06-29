"""
条件公式 / 条件判定：编辑器存储与前端 JSON 导出。

- 规则项：label（说明）、condition（判断条件）、expression（公式或判定表达式）
- condition 为空：前端默认人工选择；有 condition：自动匹配
"""
from __future__ import annotations

import copy
import re
import uuid
from typing import Any, Callable, Dict, List, Mapping, Optional


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip())


# 导出前端 JSON 时：逻辑连接词统一为 and / or（编辑态可保留中文或 &&/||）
_RE_LOGIC_OR_PHRASE = re.compile(r"\s*或者\s*")
_RE_LOGIC_OR = re.compile(r"\s*或\s*")
_RE_LOGIC_AND = re.compile(r"\s*且\s*")
_RE_LOGIC_AND_YU = re.compile(r"\s*与\s*")
_RE_LOGIC_OR_ASCII = re.compile(r"\s*\|\|\s*")
_RE_LOGIC_AND_ASCII = re.compile(r"\s*&&\s*")


def normalize_logic_connectors_for_frontend_export(text: Any) -> str:
    """
    将公式/判定中的逻辑连接词规范为 ``and`` / ``or``。

    例：``±5% 且 ±5000`` → ``±5% and ±5000``；``<=25 或 >=10`` → ``<=25 or >=10``。
    仅用于导出前端运行态 JSON，不改变编辑器落盘内容。
    """
    s = str(text or "")
    if not s.strip():
        return s
    s = _RE_LOGIC_OR_PHRASE.sub(" or ", s)
    s = _RE_LOGIC_OR.sub(" or ", s)
    s = _RE_LOGIC_OR_ASCII.sub(" or ", s)
    s = _RE_LOGIC_AND_ASCII.sub(" and ", s)
    s = _RE_LOGIC_AND.sub(" and ", s)
    s = _RE_LOGIC_AND_YU.sub(" and ", s)
    return re.sub(r" {2,}", " ", s).strip()


def apply_field_logic_connectors_for_frontend_export(field: Dict[str, Any]) -> None:
    """就地规范化栏位及其 ``source`` 内公式/判定字符串的逻辑连接词。"""
    if not isinstance(field, dict):
        return

    def _patch_text_container(container: Dict[str, Any], keys: tuple[str, ...]) -> None:
        for key in keys:
            raw = container.get(key)
            if raw is None or raw == "":
                continue
            container[key] = normalize_logic_connectors_for_frontend_export(raw)

    _patch_text_container(field, ("fieldExpression", "formula", "judgmentCriterionText"))
    rules = field.get("formulaRules")
    if isinstance(rules, list):
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            for rk in ("condition", "expression", "formula"):
                if rule.get(rk):
                    rule[rk] = normalize_logic_connectors_for_frontend_export(rule[rk])
    jct = field.get("judgmentCriteriaByTestType")
    if isinstance(jct, dict):
        for k in ("acceptance", "status"):
            if jct.get(k):
                jct[k] = normalize_logic_connectors_for_frontend_export(jct[k])
    jsets = field.get("judgmentRuleSets")
    if isinstance(jsets, dict):
        for key in ("acceptance", "status"):
            block = jsets.get(key)
            if not isinstance(block, dict):
                continue
            if block.get("logicExpression"):
                block["logicExpression"] = normalize_logic_connectors_for_frontend_export(
                    block["logicExpression"]
                )
            for rule in block.get("rules") or []:
                if not isinstance(rule, dict):
                    continue
                for rk in ("condition", "expression", "formula"):
                    if rule.get(rk):
                        rule[rk] = normalize_logic_connectors_for_frontend_export(rule[rk])
    fv = field.get("fieldVerdict")
    if isinstance(fv, dict) and fv.get("rule"):
        fv["rule"] = normalize_logic_connectors_for_frontend_export(fv["rule"])

    src = field.get("source")
    if isinstance(src, dict):
        _patch_text_container(src, ("pdfFieldExpression", "fieldExpression", "formula"))
        src_jct = src.get("judgmentCriteriaByTestType")
        if isinstance(src_jct, dict):
            for k in ("acceptance", "status"):
                if src_jct.get(k):
                    src_jct[k] = normalize_logic_connectors_for_frontend_export(src_jct[k])
        src_rules = src.get("formulaRules")
        if isinstance(src_rules, list):
            for rule in src_rules:
                if not isinstance(rule, dict):
                    continue
                for rk in ("condition", "expression", "formula"):
                    if rule.get(rk):
                        rule[rk] = normalize_logic_connectors_for_frontend_export(rule[rk])


def new_rule_id() -> str:
    return "rule_" + uuid.uuid4().hex[:12]


def normalize_rule(
    rule: Any,
    *,
    default_label: str = "",
    rule_count: int = 1,
) -> Dict[str, Any]:
    if not isinstance(rule, dict):
        rule = {}
    cond = _norm(rule.get("condition"))
    expr = _norm(rule.get("expression") or rule.get("formula"))
    label = _norm(rule.get("label") or default_label)
    rid = _norm(rule.get("id")) or new_rule_id()
    _ = rule_count  # 保留参数供调用方区分单条默认公式与多条备选
    return {
        "id": rid,
        "label": label,
        "condition": cond,
        "expression": expr,
    }


def export_rule_for_frontend(rule: Mapping[str, Any]) -> Dict[str, Any]:
    """前端运行态：仅四字段（id/label/condition/expression），与 Flutter 约定一致。"""
    row = normalize_rule(rule)
    return {
        "id": row["id"],
        "label": row["label"],
        "condition": normalize_logic_connectors_for_frontend_export(row["condition"]),
        "expression": normalize_logic_connectors_for_frontend_export(row["expression"]),
    }


def export_formula_rules_list(rules: Any) -> List[Dict[str, Any]]:
    return [export_rule_for_frontend(r) for r in normalize_rule_list(rules)]


def has_auto_conditional_rules(rules: Any) -> bool:
    for row in normalize_rule_list(rules):
        if row.get("condition") and row.get("expression"):
            return True
    return False


def normalize_rule_list(rules: Any) -> List[Dict[str, Any]]:
    if not isinstance(rules, list):
        return []
    raw = [r for r in rules if isinstance(r, dict)]
    count = len(raw)
    out: List[Dict[str, Any]] = []
    for i, row in enumerate(raw):
        out.append(normalize_rule(row, default_label=f"条件{i + 1}", rule_count=count))
    return out


def default_judgment_rule_sets() -> Dict[str, Any]:
    return {
        "acceptance": {"combine": "and", "logicExpression": "", "rules": []},
        "status": {"combine": "and", "logicExpression": "", "rules": []},
    }


def normalize_judgment_rule_sets(raw: Any) -> Dict[str, Any]:
    base = default_judgment_rule_sets()
    if not isinstance(raw, dict):
        return base
    out = copy.deepcopy(base)
    for key in ("acceptance", "status"):
        block = raw.get(key)
        if not isinstance(block, dict):
            continue
        combine = _norm(block.get("combine") or "and").lower()
        if combine not in ("and", "or"):
            combine = "and"
        out[key] = {
            "combine": combine,
            "logicExpression": _norm(block.get("logicExpression")),
            "rules": normalize_rule_list(block.get("rules")),
        }
    return out


def compile_conditional_expression(
    rules: List[Mapping[str, Any]],
    *,
    substituter: Optional[Callable[[str], str]] = None,
    default_expression: str = "",
) -> str:
    """将带 condition 的规则编译为 if 嵌套；condition 为空的规则不参与（由前端人工点选，见 §3.2）。"""
    substituter = substituter or (lambda s: s)
    parts: List[tuple[str, str]] = []
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        cond = substituter(_norm(rule.get("condition")))
        expr = substituter(_norm(rule.get("expression") or rule.get("formula")))
        if cond and expr:
            parts.append((cond, expr))
    if not parts:
        return substituter(_norm(default_expression))
    tail = substituter(_norm(default_expression)) or '""'
    for cond, expr in reversed(parts):
        tail = f"if({cond},{expr},{tail})"
    return tail


def compile_judgment_criteria_text(rule_set: Mapping[str, Any]) -> str:
    """将判定规则集编译为展示/兼容用字符串。"""
    if not isinstance(rule_set, dict):
        return ""
    logic_expr = _norm(rule_set.get("logicExpression"))
    if logic_expr:
        return logic_expr
    rules = normalize_rule_list(rule_set.get("rules"))
    exprs = [
        _norm(r.get("expression"))
        for r in rules
        if _norm(r.get("expression")) and not _norm(r.get("condition"))
    ]
    if not exprs:
        exprs = [_norm(r.get("expression")) for r in rules if _norm(r.get("expression"))]
    if not exprs:
        return ""
    combine = _norm(rule_set.get("combine") or "and").lower()
    sep = " 且 " if combine == "and" else " 或 "
    return sep.join(exprs)


def judgment_rule_set_has_auto_conditions(rule_set: Mapping[str, Any] | None) -> bool:
    if not isinstance(rule_set, dict):
        return False
    for row in normalize_rule_list(rule_set.get("rules")):
        if row.get("condition") and row.get("expression"):
            return True
    return False


def judgment_rule_sets_have_auto_conditions(rule_sets: Mapping[str, Any] | None) -> bool:
    if not isinstance(rule_sets, dict):
        return False
    for key in ("acceptance", "status"):
        if judgment_rule_set_has_auto_conditions(rule_sets.get(key)):
            return True
    return False


def judgment_criteria_bucket_from_inspection_type(inspection_type: str) -> str:
    """
    检测类型展示文案 / testType 枚举 → judgmentCriteriaByTestType 键。
    验收现场记录模板仅使用 acceptance；状态现场记录仅使用 status。
    """
    t = str(inspection_type or "").strip()
    tl = t.lower()
    if tl in {"acceptance", "accept", "验收", "验收检测"}:
        return "acceptance"
    if tl in {"status", "状态", "状态检测"}:
        return "status"
    if "验收" in t and "状态" not in t.replace("验收", "", 1):
        return "acceptance"
    if "状态" in t:
        return "status"
    return ""


def _judgment_bucket_from_inspection_type(inspection_type: str) -> str:
    bucket = judgment_criteria_bucket_from_inspection_type(inspection_type)
    return bucket if bucket else "acceptance"


def resolve_judgment_block_for_eval(
    rule_set: Mapping[str, Any] | None,
    *,
    value_mapping: Mapping[str, Any] | None = None,
    constants: Mapping[str, Any] | None = None,
    enums: Mapping[str, Any] | None = None,
    lookup_tables: Mapping[str, Any] | None = None,
) -> str:
    """按条件选取单条判定标准串；无自动条件时编译为兼容字符串。"""
    if not isinstance(rule_set, dict):
        return ""
    logic_expr = _norm(rule_set.get("logicExpression"))
    if logic_expr:
        return logic_expr
    rules = normalize_rule_list(rule_set.get("rules"))
    if not rules:
        return ""
    vm = value_mapping if isinstance(value_mapping, dict) else {}
    const = constants if isinstance(constants, dict) else {}
    enums_d = enums if isinstance(enums, dict) else {}
    lts = lookup_tables if isinstance(lookup_tables, dict) else {}
    fallback = ""
    try:
        from utils.dynamic_form_expression import eval_computed_formula
    except ImportError:
        eval_computed_formula = None  # type: ignore[assignment]
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        expr = _norm(rule.get("expression") or rule.get("formula"))
        if not expr:
            continue
        cond = _norm(rule.get("condition"))
        if not cond:
            if not fallback:
                fallback = expr
            continue
        if eval_computed_formula is None:
            continue
        try:
            if eval_computed_formula(
                cond,
                vm,
                constants=const,
                enums=enums_d,
                lookup_tables=lts,
            ) is True:
                return expr
        except Exception:
            continue
    if fallback:
        return fallback
    return compile_judgment_criteria_text(rule_set)


def resolve_judgment_criteria_for_eval(
    field: Mapping[str, Any],
    *,
    inspection_type: str = "",
    value_mapping: Mapping[str, Any] | None = None,
    constants: Mapping[str, Any] | None = None,
    enums: Mapping[str, Any] | None = None,
    lookup_tables: Mapping[str, Any] | None = None,
) -> str:
    """解析栏位在指定检测类型下应使用的判定标准串。"""
    if not isinstance(field, dict):
        return ""
    bucket = judgment_criteria_bucket_from_inspection_type(inspection_type)
    if not bucket:
        return ""
    sets = field.get("judgmentRuleSets")
    if isinstance(sets, dict):
        block = sets.get(bucket)
        if isinstance(block, dict) and normalize_rule_list(block.get("rules")):
            resolved = resolve_judgment_block_for_eval(
                block,
                value_mapping=value_mapping,
                constants=constants,
                enums=enums,
                lookup_tables=lookup_tables,
            )
            if resolved:
                return resolved
    jct = field.get("judgmentCriteriaByTestType")
    if isinstance(jct, dict) and jct:
        preferred = str(jct.get(bucket) or "").strip()
        if preferred:
            return preferred
    for k in ("judgmentCriterionText", "judgmentCriterion", "criterionText", "判定标准"):
        t = str(field.get(k) or "").strip()
        if t:
            return t
    return ""


def export_judgment_rule_sets_for_frontend(rule_sets: Mapping[str, Any]) -> Dict[str, Any]:
    sets = normalize_judgment_rule_sets(rule_sets)
    out: Dict[str, Any] = {}
    for key in ("acceptance", "status"):
        block = sets.get(key) or {}
        rules = export_formula_rules_list(block.get("rules"))
        if not rules:
            continue
        combine = _norm(block.get("combine") or "and").lower()
        if combine not in ("and", "or"):
            combine = "and"
        out[key] = {
            "combine": combine,
            "logicExpression": normalize_logic_connectors_for_frontend_export(
                block.get("logicExpression") or ""
            ),
            "rules": rules,
        }
    return out


def export_formula_rules_meta(rules: List[Mapping[str, Any]]) -> Dict[str, Any]:
    return {"formulaRules": export_formula_rules_list(rules)}


def resolve_field_expression_rules(field: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """读取条件公式列表；真源键 ``formulaRules``，旧键 ``fieldExpressionRules`` 只读兜底。"""
    if not isinstance(field, dict):
        return []
    rules = field.get("formulaRules")
    if not isinstance(rules, list) or not rules:
        legacy = field.get("fieldExpressionRules")
        rules = legacy if isinstance(legacy, list) else []
    return [r for r in rules if isinstance(r, dict)]


def resolve_field_formula_for_eval(
    field: Mapping[str, Any],
    value_mapping: Mapping[str, Any] | None = None,
    *,
    constants: Mapping[str, Any] | None = None,
    enums: Mapping[str, Any] | None = None,
    lookup_tables: Mapping[str, Any] | None = None,
) -> str:
    """
    解析栏位用于后端求值的公式字符串。

    顺序：``fieldExpression`` / ``formula`` → 带 condition 的规则（条件为真）→
    无法判定条件时采用第一条带 expression 的规则（与前端 §3.2 人工选公式兜底一致）。
    """
    if not isinstance(field, dict):
        return ""
    formula = _norm(field.get("fieldExpression") or field.get("formula"))
    if formula:
        return formula
    rules = resolve_field_expression_rules(field)
    if not rules:
        return ""
    vm = value_mapping if isinstance(value_mapping, dict) else {}
    const = constants if isinstance(constants, dict) else {}
    enums_d = enums if isinstance(enums, dict) else {}
    lts = lookup_tables if isinstance(lookup_tables, dict) else {}
    fallback = ""
    try:
        from utils.dynamic_form_expression import eval_computed_formula
    except ImportError:
        eval_computed_formula = None  # type: ignore[assignment]
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        expr = _norm(rule.get("expression") or rule.get("formula"))
        if not expr:
            continue
        if not fallback:
            fallback = expr
        cond = _norm(rule.get("condition"))
        if not cond or eval_computed_formula is None:
            continue
        try:
            if eval_computed_formula(
                cond,
                vm,
                constants=const,
                enums=enums_d,
                lookup_tables=lts,
            ) is True:
                return expr
        except Exception:
            continue
    return fallback


def strip_legacy_field_expression_rules_list_key(field: Dict[str, Any]) -> None:
    """移除冗余的 ``fieldExpressionRules`` 列表键（规则项内 ``expression``/``formula`` 仍保留）。"""
    if not isinstance(field, dict):
        return
    field.pop("fieldExpressionRules", None)
    src = field.get("source")
    if isinstance(src, dict):
        src.pop("fieldExpressionRules", None)


def export_judgment_rules_meta(rule_sets: Mapping[str, Any]) -> Dict[str, Any]:
    sets = normalize_judgment_rule_sets(rule_sets)
    jct: Dict[str, str] = {}
    for key in ("acceptance", "status"):
        compiled = compile_judgment_criteria_text(sets.get(key) or {})
        if compiled:
            jct[key] = compiled
    return {
        "judgmentRuleSets": sets,
        "judgmentCriteriaByTestType": jct,
    }


def apply_formula_rules_to_field_dict(
    field: Dict[str, Any],
    *,
    substituter: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    if not isinstance(field, dict):
        return field
    rules = resolve_field_expression_rules(field)
    if rules:
        normalized = normalize_rule_list(rules)
        exported = export_formula_rules_list(rules)
        field["formulaRules"] = exported
        strip_legacy_field_expression_rules_list_key(field)
        default_expr = _norm(field.get("fieldExpression") or field.get("formula"))
        auto_compiled = compile_conditional_expression(
            normalized,
            substituter=substituter,
            default_expression=default_expr,
        )
        if auto_compiled and auto_compiled != '""' and has_auto_conditional_rules(normalized):
            field["formula"] = auto_compiled
            field["fieldExpression"] = auto_compiled
            field["type"] = field.get("type") or "computed"
    for legacy in (
        "formulaSelectionMode",
        "requiresManualFormulaSelection",
        "selectionMode",
        "requiresManualSelection",
    ):
        field.pop(legacy, None)
    return field


def apply_judgment_rules_to_field_dict(field: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(field, dict):
        return field
    sets = field.get("judgmentRuleSets")
    if not isinstance(sets, dict) or not any(
        (sets.get(k) or {}).get("rules") for k in ("acceptance", "status") if isinstance(sets.get(k), dict)
    ):
        return field
    meta = export_judgment_rules_meta(sets)
    field["judgmentRuleSets"] = meta["judgmentRuleSets"]
    if meta["judgmentCriteriaByTestType"]:
        existing = field.get("judgmentCriteriaByTestType")
        if not isinstance(existing, dict):
            existing = {}
        merged = dict(existing)
        merged.update(meta["judgmentCriteriaByTestType"])
        field["judgmentCriteriaByTestType"] = merged
    return field


def _migrate_judgment_rule_sets_to_criteria(field: Dict[str, Any]) -> None:
    """无自动判断条件时，将 judgmentRuleSets 压平为 judgmentCriteriaByTestType。"""
    sets = field.get("judgmentRuleSets")
    if not isinstance(sets, dict):
        return
    if judgment_rule_sets_have_auto_conditions(sets):
        return
    existing = field.get("judgmentCriteriaByTestType")
    if not isinstance(existing, dict):
        existing = {}
    for key in ("acceptance", "status"):
        block = sets.get(key)
        if not isinstance(block, dict):
            continue
        compiled = compile_judgment_criteria_text(block)
        existing[key] = compiled if compiled else ""
    field["judgmentCriteriaByTestType"] = existing
    field.pop("judgmentRuleSets", None)
    field.pop("judgmentSelectionMode", None)
    field.pop("requiresManualJudgmentSelection", None)


def enrich_frontend_field_with_conditional_rules(
    field: Dict[str, Any],
    *,
    substituter: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    if not resolve_field_expression_rules(field):
        if isinstance(src.get("formulaRules"), list):
            field["formulaRules"] = copy.deepcopy(src["formulaRules"])
        elif isinstance(src.get("fieldExpressionRules"), list):
            field["formulaRules"] = copy.deepcopy(src["fieldExpressionRules"])
    if isinstance(src.get("judgmentRuleSets"), dict) and not isinstance(field.get("judgmentRuleSets"), dict):
        field["judgmentRuleSets"] = copy.deepcopy(src["judgmentRuleSets"])
    _migrate_judgment_rule_sets_to_criteria(field)
    apply_formula_rules_to_field_dict(field, substituter=substituter)
    apply_judgment_rules_to_field_dict(field)
    strip_legacy_field_expression_rules_list_key(field)
    apply_field_logic_connectors_for_frontend_export(field)
    return field


def enrich_pdf_field_row_with_conditional_rules(row: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(row, dict):
        return row
    if resolve_field_expression_rules(row):
        apply_formula_rules_to_field_dict(row)
    _migrate_judgment_rule_sets_to_criteria(row)
    return row
