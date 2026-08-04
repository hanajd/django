"""
将 fit_a / fit_b / fit_y / fit_x 展开为与 R² 同类的可求值算术式。

平板侧通常只认四则 / ln / exp / if，不实现 OLS 引用函数；导出与编辑器插入时都应写展开式。
fit_eq(源) 仍保留函数形态（输出方程字符串）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from utils.pdf_field_formulas import (
    is_fit_model_expression,
    migrate_fit_rule_expression,
    normalize_pdf_field_id,
    parse_fit_model_kind,
)

_FIT_REF_CALL_RE = re.compile(
    r"^\s*(fit_a|fit_b|fit_y|fit_x)\s*\(\s*(f\d+)\s*(?:,\s*(.+?))?\s*\)\s*$",
    re.IGNORECASE | re.DOTALL,
)


def _transformed_xy(
    x_list: Sequence[Any], y_list: Sequence[Any], kind: str
) -> Tuple[str, List[str], List[str]]:
    k = str(kind or "linear").strip().lower()
    if k not in ("linear", "log", "exp"):
        k = "linear"
    xs: List[str] = []
    ys: List[str] = []
    n_in = min(len(x_list or []), len(y_list or []))
    for i in range(n_in):
        xid = normalize_pdf_field_id(str(x_list[i] or ""))
        yid = normalize_pdf_field_id(str(y_list[i] or ""))
        if not xid or not yid:
            continue
        if k == "log":
            xs.append(f"ln({xid})")
            ys.append(yid)
        elif k == "exp":
            xs.append(xid)
            ys.append(f"ln({yid})")
        else:
            xs.append(xid)
            ys.append(yid)
    return k, xs, ys


def build_ols_ab_exprs(
    x_list: Sequence[Any], y_list: Sequence[Any], kind: str
) -> Optional[Dict[str, str]]:
    k, xs, ys = _transformed_xy(x_list, y_list, kind)
    n = len(xs)
    if n < 2:
        return None
    sx = "+".join(xs)
    sy = "+".join(ys)
    sxy = "+".join(f"({xs[i]})*({ys[i]})" for i in range(n))
    sx2 = "+".join(f"({x})*({x})" for x in xs)
    den = f"(({n})*({sx2})-({sx})*({sx}))"
    slope = f"((({n})*({sxy})-({sx})*({sy}))/{den})"
    intercept = f"(({sy}-({slope})*({sx}))/{n})"
    if k == "exp":
        return {
            "kind": "exp",
            "slope": slope,
            "intercept": intercept,
            "a": f"exp({intercept})",
            "b": slope,
        }
    return {
        "kind": k,
        "slope": slope,
        "intercept": intercept,
        "a": slope,
        "b": intercept,
    }


def build_fit_ref_expanded_expression(
    kind: str,
    usage: str,
    x_list: Sequence[Any],
    y_list: Sequence[Any],
    arg_expr: str = "",
) -> str:
    u = str(usage or "").strip().lower()
    if u in ("eq", "fit_eq"):
        return ""
    ab = build_ols_ab_exprs(x_list, y_list, kind)
    if not ab:
        return ""
    if u in ("a", "fit_a"):
        return ab["a"]
    if u in ("b", "fit_b"):
        return ab["b"]
    arg = str(arg_expr or "").strip()
    if not arg:
        return ""
    if u in ("y", "fit_y"):
        if ab["kind"] == "log":
            return f"(({ab['a']})*ln({arg})+({ab['b']}))"
        if ab["kind"] == "exp":
            return f"exp(({ab['slope']})*({arg})+({ab['intercept']}))"
        return f"(({ab['a']})*({arg})+({ab['b']}))"
    if u in ("x", "fit_x"):
        if ab["kind"] == "log":
            return f"exp((({arg})-({ab['b']}))/({ab['a']}))"
        if ab["kind"] == "exp":
            return f"((ln({arg})-({ab['intercept']}))/({ab['slope']}))"
        return f"((({arg})-({ab['b']}))/({ab['a']}))"
    return ""


def expand_fit_ref_call_to_rules(
    src_field: Mapping[str, Any],
    usage: str,
    arg_expr: str = "",
) -> List[Dict[str, str]]:
    fb = src_field.get("fitBinding") if isinstance(src_field.get("fitBinding"), Mapping) else {}
    xb = fb.get("x") if isinstance(fb.get("x"), list) else []
    yb = fb.get("y") if isinstance(fb.get("y"), list) else []
    src_rules = src_field.get("formulaRules") if isinstance(src_field.get("formulaRules"), list) else []
    out: List[Dict[str, str]] = []

    def push(label: str, condition: str, kind: str) -> None:
        expr = build_fit_ref_expanded_expression(kind, usage, xb, yb, arg_expr)
        if not expr:
            return
        out.append(
            {
                "label": str(label or ""),
                "condition": str(condition or "").strip(),
                "expression": expr,
                "formula": expr,
            }
        )

    if src_rules:
        for r in src_rules:
            if not isinstance(r, Mapping):
                continue
            model = migrate_fit_rule_expression(
                str(r.get("expression") if r.get("expression") is not None else r.get("formula") or ""),
                fit_kind=str(r.get("fitKind") or ""),
            )
            kind = parse_fit_model_kind(model) or "linear"
            if not is_fit_model_expression(model) and not parse_fit_model_kind(model):
                kind = "linear"
            push(str(r.get("label") or ""), str(r.get("condition") or ""), kind)
    if not out:
        for kind, label in (("linear", "线性"), ("log", "对数"), ("exp", "指数")):
            push(label, "", kind)
    return out


def parse_fit_ref_call(expr: str) -> Optional[Tuple[str, str, str]]:
    m = _FIT_REF_CALL_RE.match(str(expr or ""))
    if not m:
        return None
    return (m.group(1).lower(), normalize_pdf_field_id(m.group(2)), str(m.group(3) or "").strip())


def expand_field_fit_ref_inplace(
    field: Dict[str, Any],
    field_by_pid: Mapping[str, Mapping[str, Any]],
) -> bool:
    """若栏位公式为整式 fit_a/b/y/x(...)，就地改写为展开式 + formulaRules。"""
    changed = False
    candidates = []
    for key in ("fieldExpression", "formula", "pdfFieldExpression"):
        raw = str(field.get(key) or "").strip()
        if raw:
            candidates.append(raw)
            break
    if not candidates:
        rules = field.get("formulaRules")
        if isinstance(rules, list) and rules and isinstance(rules[0], Mapping):
            raw = str(rules[0].get("expression") or rules[0].get("formula") or "").strip()
            if raw:
                candidates.append(raw)
    if not candidates:
        return False
    parsed = parse_fit_ref_call(candidates[0])
    if not parsed:
        return False
    usage, src_pid, arg = parsed
    src = field_by_pid.get(src_pid) or field_by_pid.get(src_pid.lower())
    if not isinstance(src, Mapping):
        return False
    rules = expand_fit_ref_call_to_rules(src, usage, arg)
    if not rules:
        return False
    primary = rules[0]["expression"]
    field["fieldExpression"] = primary
    field["formula"] = primary
    if "pdfFieldExpression" in field:
        field["pdfFieldExpression"] = primary
    # 保留原规则 id（若有）
    out_rules = []
    for i, r in enumerate(rules):
        item = dict(r)
        if isinstance(field.get("formulaRules"), list) and i < len(field["formulaRules"]):
            old = field["formulaRules"][i]
            if isinstance(old, Mapping) and old.get("id"):
                item["id"] = old["id"]
        out_rules.append(item)
    field["formulaRules"] = out_rules
    return True


def expand_fit_refs_in_schema_fields(fields: Sequence[Any]) -> int:
    by_pid: Dict[str, Mapping[str, Any]] = {}
    for f in fields:
        if not isinstance(f, Mapping):
            continue
        pid = normalize_pdf_field_id(str(f.get("pdfFieldId") or f.get("id") or ""))
        if pid:
            by_pid[pid] = f
            by_pid[pid.lower()] = f
    n = 0
    for f in fields:
        if isinstance(f, dict) and expand_field_fit_ref_inplace(f, by_pid):
            n += 1
    return n
