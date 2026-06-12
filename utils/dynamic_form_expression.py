"""
与动态表单前端表达式子集对齐的安全求值（报告/回填服务端）。

- 仅允许 ast 白名单节点，禁止函数定义、导入、属性任意写等。
- 支持将前端常用写法预处理为 Python：`&&` `||`、`true`/`false`/`null`、`~/` 整除等。
- 提供 verdict 规则求值及「计算字段」缺省补算入口。

与前端完整 parity 见 docs；未实现项（如 `??`、Dart 风格 `?:`）见 docs/动态表单后端对齐与前端配合说明.md。
"""

from __future__ import annotations

import ast
import math
import operator
import re
import unicodedata
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple, Union

_FLOAT_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_FW_DIGITS = str.maketrans("０１２３４５６７８９．，％", "0123456789.,%")


def _parse_first_number(s: Any) -> Optional[float]:
    """从字符串中抽取首个浮点数（与 verdict_from_criterion 行为一致，避免经 utils 包 __init__ 循环依赖）。"""
    if s is None:
        return None
    t = unicodedata.normalize("NFKC", str(s)).translate(_FW_DIGITS)
    m = _FLOAT_RE.search(t)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None

_ALLOWED_NODES: Tuple[type, ...] = (
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.IfExp,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.Subscript,
    ast.Slice,
    ast.Attribute,
    ast.keyword,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.FloorDiv,
    ast.Pow,
    ast.UAdd,
    ast.USub,
)


def _validate_ast(node: ast.AST, depth: int = 0) -> None:
    if depth > 80:
        raise ValueError("expression too deep")
    if not isinstance(node, _ALLOWED_NODES):
        raise ValueError(f"disallowed syntax: {type(node).__name__}")
    for ch in ast.iter_child_nodes(node):
        _validate_ast(ch, depth + 1)


def _format_range_part(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return ""
        if val == int(val):
            return str(int(val))
        text = f"{val:.10f}".rstrip("0").rstrip(".")
        return text or "0"
    if isinstance(val, int):
        return str(val)
    return str(val).strip()


def _split_top_level_range_token(expr: str) -> Optional[Tuple[str, str]]:
    """顶层 a~b：输出范围字符串，左右各自求值后用 ~ 连接（非 ~/ 整除）。"""
    s = str(expr or "").strip()
    if not s:
        return None
    depth = 0
    in_str: Optional[str] = None
    escape = False
    for i, ch in enumerate(s):
        if in_str:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == in_str:
                in_str = None
            continue
        if ch in ("'", '"'):
            in_str = ch
            continue
        if ch == "(":
            depth += 1
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            continue
        if depth == 0 and ch in ("~", "～") and (i + 1 >= len(s) or s[i + 1] != "/"):
            left = s[:i].strip()
            right = s[i + 1 :].strip()
            if left and right:
                return left, right
            return None
    return None


def _frontend_expr_to_python(expr: str) -> str:
    s = str(expr or "").strip()
    if not s:
        return ""
    s = s.replace("～", "~")
    s = re.sub(r"\btrue\b", "True", s, flags=re.IGNORECASE)
    s = re.sub(r"\bfalse\b", "False", s, flags=re.IGNORECASE)
    s = re.sub(r"\bnull\b", "None", s, flags=re.IGNORECASE)
    s = re.sub(r"&&", " and ", s)
    s = re.sub(r"\|\|", " or ", s)
    s = re.sub(r"~/", "//", s)
    # Python 中 if 为关键字，前端函数 if(a,b,c) 改为 __if__(a,b,c)
    s = re.sub(r"\bif\s*\(", "__if__(", s)
    return s


def _is_empty(x: Any) -> bool:
    if x is None:
        return True
    if isinstance(x, str):
        return x.strip() == ""
    if isinstance(x, (list, tuple, dict, set)):
        return len(x) == 0
    return False


def _to_bool(x: Any) -> bool:
    if x is None:
        return False
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
            return False
        return x != 0
    if isinstance(x, str):
        t = x.strip().lower()
        if t in ("", "false", "0", "no", "off"):
            return False
        return True
    if isinstance(x, (list, tuple, dict, set)):
        return len(x) > 0
    return bool(x)


def _flatten_numbers(*args: Any) -> List[float]:
    out: List[float] = []
    for a in args:
        if a is None:
            continue
        if isinstance(a, (list, tuple)):
            out.extend(_flatten_numbers(*a))
        else:
            try:
                out.append(float(a))
            except (TypeError, ValueError):
                pass
    return out


def _fn_avg(*args: Any) -> Optional[float]:
    xs = _flatten_numbers(*args)
    if not xs:
        return None
    return sum(xs) / len(xs)


def _fn_var(*args: Any) -> Optional[float]:
    """样本方差（分母 n-1）；少于 2 个有效数值时返回 None。"""
    xs = _flatten_numbers(*args)
    n = len(xs)
    if n < 2:
        return None
    mean = sum(xs) / n
    return sum((x - mean) ** 2 for x in xs) / (n - 1)


def _fn_std(*args: Any) -> Optional[float]:
    """样本标准差（分母 n-1）；std / stdev / stddev 同义。"""
    v = _fn_var(*args)
    if v is None:
        return None
    if v < 0:
        return 0.0
    return math.sqrt(v)


def _fn_median(*args: Any) -> Optional[float]:
    xs = sorted(_flatten_numbers(*args))
    if not xs:
        return None
    n = len(xs)
    mid = n // 2
    if n % 2 == 1:
        return float(xs[mid])
    return float(xs[mid - 1] + xs[mid]) / 2.0


def _fn_count(*args: Any) -> int:
    return len(_flatten_numbers(*args))


def _fn_sum(*args: Any) -> Optional[float]:
    xs = _flatten_numbers(*args)
    if not xs:
        return None
    return float(sum(xs))


def _fn_min(*args: Any) -> Optional[float]:
    xs = _flatten_numbers(*args)
    if not xs:
        return None
    return float(min(xs))


def _fn_max(*args: Any) -> Optional[float]:
    xs = _flatten_numbers(*args)
    if not xs:
        return None
    return float(max(xs))


def _fn_coalesce(*args: Any) -> Any:
    for a in args:
        if a is not None and not (isinstance(a, str) and a.strip() == ""):
            return a
    return None


def _fn_if(cond: Any, a: Any, b: Any) -> Any:
    return a if _to_bool(cond) else b


def _fn_parse_num(x: Any) -> Optional[float]:
    if x is None:
        return None
    return _parse_first_number(str(x))


def _fn_unit_factor(
    value: Any, enum_name: Optional[str] = None, *, enums: Optional[Dict[str, Any]] = None
) -> Optional[float]:
    """从 enums[enumName] 列表项中匹配 value，取 toBase（缺省 1.0）。"""
    if enums is None or not enum_name:
        return None
    raw_list = enums.get(str(enum_name).strip())
    if not isinstance(raw_list, list):
        return None
    want = str(value or "").strip()
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        if str(item.get("value") or "").strip() == want:
            try:
                return float(item.get("toBase", 1.0))
            except (TypeError, ValueError):
                return 1.0
    return None


def _fn_lookup(
    table_id: Any, match_map: Any, *, lookup_tables: Optional[Dict[str, Any]] = None
) -> Any:
    if lookup_tables is None or not isinstance(match_map, dict):
        return None
    tid = str(table_id or "").strip()
    rows = lookup_tables.get(tid)
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = row.get("key")
        if not isinstance(key, dict):
            continue
        ok = True
        for mk, mv in match_map.items():
            if str(key.get(mk)) != str(mv):
                ok = False
                break
        if ok:
            return row.get("value")
    return None


def _fn_ctdiw(center: Any, edge: Any) -> Optional[float]:
    try:
        c = float(center)
        e = float(edge)
    except (TypeError, ValueError):
        return None
    return c * (1.0 / 3.0) + e * (2.0 / 3.0)


_BINOPS: Dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
    ast.Pow: operator.pow,
}

_CMPOPS: Dict[type, Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


class _SafeEval(ast.NodeVisitor):
    def __init__(self, names: Dict[str, Any]):
        self.names = names

    def visit(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return self.visit(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in self.names:
                raise NameError(node.id)
            return self.names[node.id]
        if isinstance(node, ast.Attribute):
            base = self.visit(node.value)
            if not hasattr(base, node.attr):
                raise AttributeError(node.attr)
            return getattr(base, node.attr)
        if isinstance(node, ast.UnaryOp):
            v = self.visit(node.operand)
            if isinstance(node.op, ast.UAdd):
                return +v
            if isinstance(node.op, ast.USub):
                return -v
            if isinstance(node.op, ast.Not):
                return not _to_bool(v)
            raise ValueError("bad unary")
        if isinstance(node, ast.BinOp):
            left = self.visit(node.left)
            right = self.visit(node.right)
            fn = _BINOPS.get(type(node.op))
            if not fn:
                raise ValueError("bad binop")
            if left is None or right is None:
                if isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.FloorDiv, ast.Pow)):
                    return None
            return fn(left, right)
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                acc = True
                for v in node.values:
                    ev = self.visit(v)
                    if not _to_bool(ev):
                        return ev
                    acc = ev
                return acc
            if isinstance(node.op, ast.Or):
                for v in node.values:
                    ev = self.visit(v)
                    if _to_bool(ev):
                        return ev
                return ev
            raise ValueError("bad boolop")
        if isinstance(node, ast.Compare):
            left = self.visit(node.left)
            for op, comp in zip(node.ops, node.comparators):
                right = self.visit(comp)
                fn = _CMPOPS.get(type(op))
                if not fn:
                    raise ValueError("bad compare op")
                if not fn(left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return self.visit(node.body) if _to_bool(self.visit(node.test)) else self.visit(node.orelse)
        if isinstance(node, ast.List):
            return [self.visit(elt) for elt in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(self.visit(elt) for elt in node.elts)
        if isinstance(node, ast.Dict):
            out: Dict[Any, Any] = {}
            for k, v in zip(node.keys, node.values):
                if k is None:
                    continue
                out[self.visit(k)] = self.visit(v)
            return out
        if isinstance(node, ast.Subscript):
            base = self.visit(node.value)
            sl = node.slice
            if isinstance(sl, ast.Constant):
                return base[sl.value]
            if isinstance(sl, ast.Slice):
                return base[sl.lower and self.visit(sl.lower) : sl.upper and self.visit(sl.upper)]
            if hasattr(sl, "value"):
                return base[self.visit(sl)]
            return base[self.visit(sl)]
        if isinstance(node, ast.Call):
            return self._visit_call(node)
        raise ValueError(f"unsupported {type(node).__name__}")

    def _visit_call(self, node: ast.Call) -> Any:
        if node.keywords:
            kw = {k.arg: self.visit(k.value) for k in node.keywords if k.arg}
        else:
            kw = {}
        args = [self.visit(a) for a in node.args]
        if isinstance(node.func, ast.Name):
            fn = self.names.get(node.func.id)
            if callable(fn):
                return fn(*args, **kw)
        raise ValueError("only simple name calls allowed")


def build_eval_namespace(
    value_mapping: Mapping[str, Any],
    *,
    constants: Optional[Mapping[str, Any]] = None,
    enums: Optional[Mapping[str, Any]] = None,
    lookup_tables: Optional[Mapping[str, Any]] = None,
    row: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """构造求值环境：裸变量名来自 value_mapping（合法 Python 标识符键）。"""
    reserved: Set[str] = {
        "True",
        "False",
        "None",
        "abs",
        "round",
        "min",
        "max",
        "len",
        "sum",
        "avg",
        "var",
        "variance",
        "std",
        "stdev",
        "stddev",
        "median",
        "count",
        "coalesce",
        "__if__",
        "isEmpty",
        "parseNum",
        "toBool",
        "unitFactor",
        "lookup",
        "ctdiw",
        "constants",
        "enums",
        "lookupTables",
        "row",
        "values",
    }
    const_d = dict(constants or {})
    enums_d = dict(enums or {})
    lt_d = dict(lookup_tables or {})
    row_d = dict(row or {})

    ns: Dict[str, Any] = {
        "constants": _DictProxy(const_d),
        "enums": enums_d,
        "lookupTables": lt_d,
        "row": _DictProxy(row_d),
        "values": _DictProxy(dict(value_mapping)),
        "abs": abs,
        "round": round,
        "min": _fn_min,
        "max": _fn_max,
        "len": len,
        "sum": _fn_sum,
        "avg": _fn_avg,
        "var": _fn_var,
        "variance": _fn_var,
        "std": _fn_std,
        "stdev": _fn_std,
        "stddev": _fn_std,
        "median": _fn_median,
        "count": _fn_count,
        "coalesce": _fn_coalesce,
        "__if__": _fn_if,
        "isEmpty": _is_empty,
        "parseNum": _fn_parse_num,
        "toBool": _to_bool,
        "unitFactor": lambda v, n=None: _fn_unit_factor(v, n, enums=enums_d),
        "lookup": lambda tid, m: _fn_lookup(tid, m, lookup_tables=lt_d),
        "ctdiw": _fn_ctdiw,
    }
    for k, v in value_mapping.items():
        if isinstance(k, str) and k.isidentifier() and k not in reserved and k not in ns:
            ns[k] = v
    return ns


class _DictProxy:
    """支持 constants.xxx 与 values.xxx 属性访问。"""

    __slots__ = ("_d",)

    def __init__(self, d: Dict[str, Any]):
        object.__setattr__(self, "_d", d)

    def __getattr__(self, name: str) -> Any:
        d = object.__getattribute__(self, "_d")
        if name in d:
            return d[name]
        raise AttributeError(name)


def _evaluate_python_expr(
    py: str,
    value_mapping: Mapping[str, Any],
    *,
    constants: Optional[Mapping[str, Any]] = None,
    enums: Optional[Mapping[str, Any]] = None,
    lookup_tables: Optional[Mapping[str, Any]] = None,
    row: Optional[Mapping[str, Any]] = None,
) -> Any:
    if not py:
        return None
    tree = ast.parse(py, mode="eval")
    _validate_ast(tree)
    ns = build_eval_namespace(
        value_mapping,
        constants=constants,
        enums=enums,
        lookup_tables=lookup_tables,
        row=row,
    )
    return _SafeEval(ns).visit(tree)


def evaluate_expression(
    expr: str,
    value_mapping: Mapping[str, Any],
    *,
    constants: Optional[Mapping[str, Any]] = None,
    enums: Optional[Mapping[str, Any]] = None,
    lookup_tables: Optional[Mapping[str, Any]] = None,
    row: Optional[Mapping[str, Any]] = None,
) -> Any:
    range_parts = _split_top_level_range_token(expr)
    if range_parts:
        left_py = _frontend_expr_to_python(range_parts[0])
        right_py = _frontend_expr_to_python(range_parts[1])
        left_val = _evaluate_python_expr(
            left_py,
            value_mapping,
            constants=constants,
            enums=enums,
            lookup_tables=lookup_tables,
            row=row,
        )
        right_val = _evaluate_python_expr(
            right_py,
            value_mapping,
            constants=constants,
            enums=enums,
            lookup_tables=lookup_tables,
            row=row,
        )
        left_txt = _format_range_part(left_val)
        right_txt = _format_range_part(right_val)
        if not left_txt and not right_txt:
            return ""
        return f"{left_txt}~{right_txt}"

    py = _frontend_expr_to_python(expr)
    if not py:
        return None
    return _evaluate_python_expr(
        py,
        value_mapping,
        constants=constants,
        enums=enums,
        lookup_tables=lookup_tables,
        row=row,
    )


def evaluate_verdict_rule(
    rule: str,
    value_mapping: Mapping[str, Any],
    *,
    constants: Optional[Mapping[str, Any]] = None,
    enums: Optional[Mapping[str, Any]] = None,
    lookup_tables: Optional[Mapping[str, Any]] = None,
    row: Optional[Mapping[str, Any]] = None,
) -> Optional[bool]:
    """返回 True/False；求值失败或非布尔语义返回 None。"""
    try:
        v = evaluate_expression(
            rule,
            value_mapping,
            constants=constants,
            enums=enums,
            lookup_tables=lookup_tables,
            row=row,
        )
    except Exception:
        return None
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return bool(v)
    return None


def eval_computed_formula(
    formula: str,
    value_mapping: Mapping[str, Any],
    *,
    constants: Optional[Mapping[str, Any]] = None,
    enums: Optional[Mapping[str, Any]] = None,
    lookup_tables: Optional[Mapping[str, Any]] = None,
    row: Optional[Mapping[str, Any]] = None,
) -> Any:
    """计算字段公式求值；失败返回 None。"""
    try:
        return evaluate_expression(
            formula,
            value_mapping,
            constants=constants,
            enums=enums,
            lookup_tables=lookup_tables,
            row=row,
        )
    except Exception:
        return None
