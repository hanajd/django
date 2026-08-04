"""
拟合引用函数：在现有 fitBinding + fit(y=...) 配置上，用表达式引用系数 / 正算 / 反算。

不新增 JSON 顶层结构；仅识别公式串中的：
  fit_a(src) / fit_b(src) / fit_y(src, xExpr) / fit_x(src, yExpr) / fit_eq(src)

src 为拟合源栏 pdfFieldId（含 fitBinding 与 fit(y=...) 规则）。
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from utils.pdf_field_formulas import (
    is_fit_model_expression,
    migrate_fit_rule_expression,
    normalize_pdf_field_id,
    parse_fit_model_kind,
)

_FIT_REF_HEAD_RE = re.compile(
    r"\b(fit_a|fit_b|fit_y|fit_x|fit_eq)\s*\(",
    re.IGNORECASE,
)
_EPS = 1e-12


def expression_has_fit_ref(expr: str) -> bool:
    return bool(_FIT_REF_HEAD_RE.search(str(expr or "")))


def _to_float(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    s = str(v).strip().replace(",", "")
    if not s or s == "/":
        return None
    try:
        x = float(s)
    except (TypeError, ValueError):
        m = re.search(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", s)
        if not m:
            return None
        try:
            x = float(m.group(0))
        except (TypeError, ValueError):
            return None
    if math.isnan(x) or math.isinf(x):
        return None
    return x


def _vm_get(value_mapping: Mapping[str, Any], pid: str) -> Any:
    pid = normalize_pdf_field_id(pid)
    if not pid:
        return None
    if pid in value_mapping:
        return value_mapping[pid]
    up = pid.upper()
    if up in value_mapping:
        return value_mapping[up]
    return value_mapping.get(pid.lower())


def _field_lookup(
    field_by_pid: Optional[Mapping[str, Mapping[str, Any]]], pid: str
) -> Optional[Mapping[str, Any]]:
    if not isinstance(field_by_pid, Mapping):
        return None
    pid = normalize_pdf_field_id(pid)
    if not pid:
        return None
    f = field_by_pid.get(pid) or field_by_pid.get(pid.lower()) or field_by_pid.get(pid.upper())
    return f if isinstance(f, Mapping) else None


def _truthy_condition(cond: str, value_mapping: Mapping[str, Any]) -> bool:
    c = str(cond or "").strip()
    if not c:
        return True
    # 栏位 id：勾选/非空为真
    pid = normalize_pdf_field_id(c)
    if pid and re.fullmatch(r"f\d+", pid, re.I):
        v = _vm_get(value_mapping, pid)
        if v is None or v == "" or v == "/":
            return False
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return float(v) != 0.0
        s = str(v).strip().lower()
        if s in ("0", "false", "no", "off", "否"):
            return False
        return True
    # 简单比较交给动态表达式（延迟导入避免环）
    try:
        from utils.dynamic_form_expression import evaluate_expression

        return bool(evaluate_expression(c, value_mapping))
    except Exception:
        return False


def resolve_fit_source_model_expression(
    field: Mapping[str, Any],
    value_mapping: Mapping[str, Any],
) -> str:
    """按 formulaRules 条件取首条命中的 fit(y=...)；否则看栏位 formula。"""
    rules = field.get("formulaRules")
    if isinstance(rules, list) and rules:
        for r in rules:
            if not isinstance(r, Mapping):
                continue
            expr = str(r.get("expression") if r.get("expression") is not None else r.get("formula") or "").strip()
            kind_hint = str(r.get("fitKind") or "").strip()
            expr = migrate_fit_rule_expression(expr, fit_kind=kind_hint)
            if not is_fit_model_expression(expr):
                continue
            if _truthy_condition(str(r.get("condition") or ""), value_mapping):
                return expr
        return ""
    for key in ("fieldExpression", "formula", "pdfFieldExpression"):
        expr = migrate_fit_rule_expression(str(field.get(key) or "").strip())
        if is_fit_model_expression(expr):
            return expr
    return ""


def collect_fit_xy_points(
    field: Mapping[str, Any],
    value_mapping: Mapping[str, Any],
    *,
    kind: str,
) -> List[Tuple[float, float]]:
    fb = field.get("fitBinding") if isinstance(field.get("fitBinding"), Mapping) else {}
    xs_ids = fb.get("x") if isinstance(fb.get("x"), list) else []
    ys_ids = fb.get("y") if isinstance(fb.get("y"), list) else []
    n = min(len(xs_ids), len(ys_ids))
    pts: List[Tuple[float, float]] = []
    for i in range(n):
        xid = normalize_pdf_field_id(str(xs_ids[i] or ""))
        yid = normalize_pdf_field_id(str(ys_ids[i] or ""))
        if not xid or not yid:
            continue
        x = _to_float(_vm_get(value_mapping, xid))
        y = _to_float(_vm_get(value_mapping, yid))
        if x is None or y is None:
            continue
        if kind == "log" and x <= 0:
            continue
        if kind == "exp" and y <= 0:
            continue
        pts.append((x, y))
    return pts


def ols_fit_coefficients(
    kind: str, points: Sequence[Tuple[float, float]]
) -> Optional[Tuple[float, float]]:
    """返回 (a, b)；点数不足或不满足约束时 None。"""
    k = str(kind or "linear").strip().lower()
    if k not in ("linear", "log", "exp"):
        k = "linear"
    if len(points) < 2:
        return None
    xs: List[float] = []
    ys: List[float] = []
    for x, y in points:
        if k == "log":
            xs.append(math.log(x))
            ys.append(y)
        elif k == "exp":
            xs.append(x)
            ys.append(math.log(y))
        else:
            xs.append(x)
            ys.append(y)
    n = len(xs)
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(v * v for v in xs)
    sxy = sum(xs[i] * ys[i] for i in range(n))
    den = n * sxx - sx * sx
    if abs(den) < _EPS:
        return None
    slope = (n * sxy - sx * sy) / den
    intercept = (sy - slope * sx) / n
    if k == "exp":
        # (x, ln y) → ln y = b*x + ln a  ⇒ a=exp(intercept), b=slope
        a = math.exp(intercept)
        b = slope
        if a <= 0 or math.isnan(a) or math.isinf(a):
            return None
        return (a, b)
    return (slope, intercept)


def compute_fit_ab_for_field(
    field: Mapping[str, Any],
    value_mapping: Mapping[str, Any],
) -> Optional[Tuple[str, float, float]]:
    """(kind, a, b) 或 None。"""
    model_expr = resolve_fit_source_model_expression(field, value_mapping)
    kind = parse_fit_model_kind(model_expr) or "linear"
    pts = collect_fit_xy_points(field, value_mapping, kind=kind)
    ab = ols_fit_coefficients(kind, pts)
    if ab is None:
        return None
    return (kind, ab[0], ab[1])


def format_fit_equation_latex(kind: str, a: float, b: float, *, precision: int = 2) -> str:
    prec = max(0, int(precision))

    def fmt(v: float) -> str:
        if prec == 0:
            return str(int(round(v)))
        return f"{round(v, prec):.{prec}f}"

    sa, sb = fmt(a), fmt(abs(b))
    sign = "-" if b < 0 else "+"
    k = str(kind or "linear").strip().lower()
    if k == "log":
        return f"y = {sa}\\ln x {sign} {sb}"
    if k == "exp":
        return f"y = {sa}{{\\rm e}}^{{{fmt(b)}x}}"
    return f"y = {sa}x {sign} {sb}"


def fit_predict_y(kind: str, a: float, b: float, x: float) -> Optional[float]:
    k = str(kind or "linear").strip().lower()
    try:
        if k == "log":
            if x <= 0:
                return None
            return a * math.log(x) + b
        if k == "exp":
            return a * math.exp(b * x)
        return a * x + b
    except (ValueError, OverflowError):
        return None


def fit_solve_x(kind: str, a: float, b: float, y: float) -> Optional[float]:
    k = str(kind or "linear").strip().lower()
    try:
        if k == "log":
            if abs(a) < _EPS:
                return None
            return math.exp((y - b) / a)
        if k == "exp":
            if a <= 0 or abs(b) < _EPS:
                return None
            ratio = y / a
            if ratio <= 0:
                return None
            return math.log(ratio) / b
        if abs(a) < _EPS:
            return None
        return (y - b) / a
    except (ValueError, OverflowError):
        return None


def _find_matching_paren(s: str, open_idx: int) -> int:
    depth = 0
    for i in range(open_idx, len(s)):
        ch = s[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _split_top_level_args(inner: str) -> List[str]:
    args: List[str] = []
    buf: List[str] = []
    depth = 0
    for ch in inner:
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf or args:
        args.append("".join(buf).strip())
    return [a for a in args if a != ""]


def _eval_numeric_subexpr(
    expr: str,
    value_mapping: Mapping[str, Any],
    field_by_pid: Optional[Mapping[str, Mapping[str, Any]]],
) -> Optional[float]:
    expanded = expand_fit_ref_functions(
        expr, value_mapping, field_by_pid=field_by_pid, _depth=1
    )
    if expanded is None:
        return None
    # 纯数字
    num = _to_float(expanded)
    if num is not None and re.fullmatch(
        r"\s*[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?\s*", str(expanded)
    ):
        return num
    try:
        from utils.dynamic_form_expression import evaluate_expression

        v = evaluate_expression(str(expanded), value_mapping)
        return _to_float(v)
    except Exception:
        return None


def _resolve_source_ab(
    src_pid: str,
    value_mapping: Mapping[str, Any],
    field_by_pid: Optional[Mapping[str, Mapping[str, Any]]],
) -> Optional[Tuple[str, float, float]]:
    field = _field_lookup(field_by_pid, src_pid)
    if field is None:
        return None
    return compute_fit_ab_for_field(field, value_mapping)


def expand_fit_ref_functions(
    expr: str,
    value_mapping: Mapping[str, Any],
    *,
    field_by_pid: Optional[Mapping[str, Mapping[str, Any]]] = None,
    precision: int = 2,
    _depth: int = 0,
) -> Optional[str]:
    """
    将表达式中的 fit_a/b/y/x 替换为数值字面量；fit_eq 仅当整式为该调用时返回方程串。
    无法求值时对应片段置空并可能导致整体失败（返回 None）。
    """
    if _depth > 12:
        return None
    text = str(expr or "").strip()
    if not text:
        return text
    if not expression_has_fit_ref(text):
        return text

    # 整式 fit_eq(src)
    m_whole = re.fullmatch(
        r"fit_eq\s*\(\s*(f\d+)\s*\)", text, flags=re.IGNORECASE
    )
    if m_whole:
        ab = _resolve_source_ab(m_whole.group(1), value_mapping, field_by_pid)
        if ab is None:
            return ""
        kind, a, b = ab
        return format_fit_equation_latex(kind, a, b, precision=precision)

    out = text
    # 反复替换最内层调用
    for _ in range(32):
        m = _FIT_REF_HEAD_RE.search(out)
        if not m:
            break
        name = m.group(1).lower()
        open_i = out.find("(", m.start())
        close_i = _find_matching_paren(out, open_i)
        if close_i < 0:
            return None
        inner = out[open_i + 1 : close_i]
        args = _split_top_level_args(inner)
        replacement: Optional[str] = None

        if name == "fit_eq":
            # 嵌套在其它表达式中的方程字符串无意义
            replacement = '""'
        elif name in ("fit_a", "fit_b"):
            if len(args) != 1:
                return None
            ab = _resolve_source_ab(args[0], value_mapping, field_by_pid)
            if ab is None:
                replacement = "null"
            else:
                _kind, a, b = ab
                val = a if name == "fit_a" else b
                replacement = repr(float(val))
        elif name in ("fit_y", "fit_x"):
            if len(args) != 2:
                return None
            ab = _resolve_source_ab(args[0], value_mapping, field_by_pid)
            arg_val = _eval_numeric_subexpr(args[1], value_mapping, field_by_pid)
            if ab is None or arg_val is None:
                replacement = "null"
            else:
                kind, a, b = ab
                res = (
                    fit_predict_y(kind, a, b, arg_val)
                    if name == "fit_y"
                    else fit_solve_x(kind, a, b, arg_val)
                )
                replacement = "null" if res is None else repr(float(res))
        else:
            return None

        out = out[: m.start()] + str(replacement) + out[close_i + 1 :]

    return out


def evaluate_with_fit_refs(
    expr: str,
    value_mapping: Mapping[str, Any],
    *,
    field_by_pid: Optional[Mapping[str, Mapping[str, Any]]] = None,
    precision: int = 2,
    constants: Optional[Mapping[str, Any]] = None,
    enums: Optional[Mapping[str, Any]] = None,
    lookup_tables: Optional[Mapping[str, Any]] = None,
    row: Optional[Mapping[str, Any]] = None,
) -> Any:
    """先展开拟合引用，再走动态表达式引擎。"""
    from utils.dynamic_form_expression import evaluate_expression

    raw = str(expr or "").strip()
    if not raw:
        return None
    if expression_has_fit_ref(raw):
        expanded = expand_fit_ref_functions(
            raw,
            value_mapping,
            field_by_pid=field_by_pid,
            precision=precision,
        )
        if expanded is None:
            return None
        ex = str(expanded).strip()
        if re.match(r"^y\s*=", ex, flags=re.IGNORECASE):
            return ex
        if ex in ("", "null", "None"):
            return None if ex != "" else ""
        raw = ex
    return evaluate_expression(
        raw,
        value_mapping,
        constants=constants,
        enums=enums,
        lookup_tables=lookup_tables,
        row=row,
    )


def index_pdf_fields_by_pid(
    fields: Optional[Sequence[Any]],
) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for f in fields or []:
        if not isinstance(f, dict):
            continue
        pid = normalize_pdf_field_id(
            str(f.get("pdfFieldId") or f.get("id") or "")
        )
        if pid:
            out[pid] = f
    return out
