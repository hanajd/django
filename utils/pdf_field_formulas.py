"""
统一模板 JSON：以 pdfFieldId（f+数字）为键的公式识别与前端用计算计划。

- **推荐**：在 ``pdf.fields`` 各栏位上写 ``fieldExpression``（或 ``pdfFieldExpression``），
  必要时写 ``judgmentCriteriaByTestType`` / ``fieldVerdict``；导出前端 schema 时会写入
  对应表单字段的 ``source.pdfFieldExpression``、``formula``、``source.judgmentCriteriaByTestType``。
- **兼容**：仍支持顶层 ``fieldFormulas``（或 ``formSchema.fieldFormulas``）；保存库模板时会尽量
  下发到各 ``pdf.fields`` 行，无法匹配栏位的条目保留在根级 ``fieldFormulas``。
"""
from __future__ import annotations

import copy
import re
from collections import defaultdict, deque
from typing import Any, Dict, List, Mapping, Optional, Set

_PDF_FID_RE = re.compile(r"\bf(\d+)\b", re.IGNORECASE)
_FID_KEY_RE = re.compile(r"^f\d+$", re.IGNORECASE)


def normalize_pdf_field_id(token: str) -> str:
    s = str(token or "").strip()
    m = _FID_KEY_RE.match(s)
    if not m:
        return ""
    return f"f{int(s[1:], 10)}"


_FIT_R2_RE = re.compile(r"^fit_r2\(\s*(f\d+)\s*\)$", re.IGNORECASE)
# 待定系数模型：前端见 fit(...) 即用 fitBinding.x/y 做最小二乘，再代入 a、b 生成方程
_FIT_MODEL_RE = re.compile(
    r"^fit\(\s*y\s*=\s*(.+?)\s*\)$",
    re.IGNORECASE,
)
_FIT_MODEL_EXPR_BY_KIND = {
    "linear": "fit(y=a*x+b)",
    "log": "fit(y=a*ln(x)+b)",
    "exp": "fit(y=a*exp(b*x))",
}
_FIT_KIND_BY_CANON_MODEL = {
    "a*x+b": "linear",
    "a*ln(x)+b": "log",
    "a*exp(b*x)": "exp",
}


def parse_fit_r2_master_pid(expression: str) -> str:
    """解析 ``fit_r2(f123)`` 哨兵式，返回规范化主格 pdfFieldId。"""
    m = _FIT_R2_RE.match(str(expression or "").strip())
    if not m:
        return ""
    return normalize_pdf_field_id(m.group(1))


def build_fit_r2_expression(master_pdf_field_id: str) -> str:
    """兼容旧哨兵；新模板请用 ``build_pearson_r2_expression``。"""
    pid = normalize_pdf_field_id(str(master_pdf_field_id or ""))
    return f"fit_r2({pid})" if pid else ""


def _canon_fit_model_body(body: str) -> str:
    s = re.sub(r"\s+", "", str(body or "").strip().lower())
    s = s.replace("{a}", "a").replace("{b}", "b")
    s = s.replace("·", "*").replace("×", "*")
    # 兼容 a*x + b / a*ln(x)+b / a*e^(b*x) 等写法
    s = s.replace("{\\rm e}", "exp").replace("\\rm e", "exp").replace("e^", "exp")
    s = re.sub(r"exp\(([^)]+)\)", r"exp(\1)", s)
    s = s.replace("a*e^(b*x)", "a*exp(b*x)").replace("a*exp(bx)", "a*exp(b*x)")
    s = s.replace("a*lnx+b", "a*ln(x)+b")
    return s


def build_fit_model_expression(fit_kind: str = "linear") -> str:
    """按内部 kind 生成前端可直接识别的 ``fit(y=...)`` 模型式。"""
    kind = str(fit_kind or "linear").strip().lower()
    return _FIT_MODEL_EXPR_BY_KIND.get(kind, _FIT_MODEL_EXPR_BY_KIND["linear"])


def is_fit_model_expression(expression: str) -> bool:
    """是否为待定系数拟合模型式（含旧哨兵 ``fit_eq()``）。"""
    raw = str(expression or "").strip()
    if not raw:
        return False
    if raw.lower() == "fit_eq()":
        return True
    return bool(_FIT_MODEL_RE.match(raw))


def parse_fit_model_kind(expression: str) -> str:
    """
    从 ``fit(y=...)`` / 旧 ``fit_eq()`` / 旧 fitKind 兼容串解析内部 kind。
    仅供后台迁移与 R² Pearson 展开；前端应直接按模型式做最小二乘，不必依赖 kind 枚举。
    """
    raw = str(expression or "").strip()
    if not raw:
        return ""
    low = raw.lower()
    if low == "fit_eq()":
        return "linear"
    if low in _FIT_MODEL_EXPR_BY_KIND:
        return low
    m = _FIT_MODEL_RE.match(raw)
    if not m:
        return ""
    canon = _canon_fit_model_body(m.group(1))
    return _FIT_KIND_BY_CANON_MODEL.get(canon, "")


def migrate_fit_rule_expression(
    expression: str = "",
    *,
    fit_kind: str = "",
) -> str:
    """
    将旧 ``fitKind`` + ``fit_eq()`` 迁成 ``fit(y=...)``；
    已是模型式则规范化空白后返回。
    """
    kind = str(fit_kind or "").strip().lower()
    expr = str(expression or "").strip()
    # 优先用显式 fitKind（旧数据 expression 常为 fit_eq()）
    if kind in _FIT_MODEL_EXPR_BY_KIND and (not expr or expr.lower() == "fit_eq()"):
        return build_fit_model_expression(kind)
    parsed = parse_fit_model_kind(expr)
    if parsed:
        return build_fit_model_expression(parsed)
    if expr.lower() == "fit_eq()":
        return build_fit_model_expression("linear")
    return expr


def build_pearson_r2_expression(
    x_ids: List[str],
    y_ids: List[str],
    *,
    fit_kind: str = "linear",
    fit_model: str = "",
) -> str:
    """
    可直接求值的 Pearson R²（与线性/对数/指数拟合的变换空间一致）：

    - linear / fit(y=a*x+b): (x, y)
    - log / fit(y=a*ln(x)+b): (ln(x), y)
    - exp / fit(y=a*exp(b*x)): (x, ln(y))

    R² = (n·Σxy − Σx·Σy)² / ((n·Σx² − (Σx)²)·(n·Σy² − (Σy)²))
    """
    kind = parse_fit_model_kind(fit_model) or str(fit_kind or "linear").strip().lower()
    if kind not in ("linear", "log", "exp"):
        kind = "linear"
    xs: List[str] = []
    ys: List[str] = []
    n_in = min(len(x_ids or []), len(y_ids or []))
    for i in range(n_in):
        xid = normalize_pdf_field_id(str(x_ids[i] or ""))
        yid = normalize_pdf_field_id(str(y_ids[i] or ""))
        if not xid or not yid:
            continue
        if kind == "log":
            xs.append(f"ln({xid})")
            ys.append(yid)
        elif kind == "exp":
            xs.append(xid)
            ys.append(f"ln({yid})")
        else:
            xs.append(xid)
            ys.append(yid)
    n = len(xs)
    if n < 2:
        return ""
    sx = "+".join(xs)
    sy = "+".join(ys)
    sxy = "+".join(f"({xs[i]})*({ys[i]})" for i in range(n))
    sx2 = "+".join(f"({xs[i]})*({xs[i]})" for i in range(n))
    sy2 = "+".join(f"({ys[i]})*({ys[i]})" for i in range(n))
    num = f"(({n})*({sxy})-({sx})*({sy}))"
    den = f"((({n})*({sx2})-({sx})*({sx}))*(({n})*({sy2})-({sy})*({sy})))"
    return f"(({num})*({num})/{den})"


def resolve_fit_r2_master_pid(field: Mapping[str, Any]) -> str:
    """优先从 ``fieldExpression`` / ``formula`` 的 fit_r2(...) 解析；旧数据回退 fitConfigRef。"""
    if not isinstance(field, Mapping):
        return ""
    for key in ("fieldExpression", "formula", "pdfFieldExpression"):
        pid = parse_fit_r2_master_pid(str(field.get(key) or ""))
        if pid:
            return pid
    src = field.get("source") if isinstance(field.get("source"), Mapping) else {}
    if isinstance(src, Mapping):
        for key in ("pdfFieldExpression", "fieldExpression", "formula"):
            pid = parse_fit_r2_master_pid(str(src.get(key) or ""))
            if pid:
                return pid
    return normalize_pdf_field_id(str(field.get("fitConfigRef") or src.get("fitConfigRef") or ""))


def extract_pdf_field_refs(expression: str) -> List[str]:
    """从公式串中提取所有 f+数字 占位符（规范化）。"""
    out: Set[str] = set()
    for m in _PDF_FID_RE.finditer(expression or ""):
        out.add(normalize_pdf_field_id(m.group(0)))
    return sorted(out)


def remap_pdf_field_refs_in_text(expr: str, id_map: Mapping[str, str]) -> str:
    """将公式/判定串中的 f 号按映射表替换（与 HTMLPDF ``remapFormulaPdfFieldRefs`` 一致）。"""
    if not expr or not id_map:
        return expr

    def repl(m: re.Match) -> str:
        old = normalize_pdf_field_id(m.group(0))
        return str(id_map.get(old) or old)

    return _PDF_FID_RE.sub(repl, expr)


def remap_pdf_field_row_formula_metadata(field: Dict[str, Any], id_map: Mapping[str, str]) -> None:
    """栏位重编号后，同步改写与该栏位绑定的公式/判定中的 f 引用。"""
    if not isinstance(field, dict) or not id_map:
        return
    for key in ("fieldExpression", "pdfFieldExpression"):
        raw = str(field.get(key) or "").strip()
        if raw:
            field[key] = remap_pdf_field_refs_in_text(raw, id_map)
    for rules_key in ("formulaRules", "fieldExpressionRules"):
        rules = field.get(rules_key)
        if not isinstance(rules, list):
            continue
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            cond = str(rule.get("condition") or "").strip()
            if cond:
                rule["condition"] = remap_pdf_field_refs_in_text(cond, id_map)
            for expr_key in ("expression", "formula"):
                raw = str(rule.get(expr_key) or "").strip()
                if raw:
                    rule[expr_key] = remap_pdf_field_refs_in_text(raw, id_map)
    fb = field.get("fitBinding")
    if isinstance(fb, dict):
        for list_key in ("x", "y"):
            vals = fb.get(list_key)
            if not isinstance(vals, list):
                continue
            remapped = []
            for v in vals:
                pid = normalize_pdf_field_id(str(v or ""))
                if not pid:
                    continue
                remapped.append(id_map.get(pid, pid))
            fb[list_key] = remapped
        r2 = normalize_pdf_field_id(str(fb.get("r2FieldId") or ""))
        if r2:
            fb["r2FieldId"] = id_map.get(r2, r2)
    fit_ref = normalize_pdf_field_id(str(field.get("fitConfigRef") or ""))
    if fit_ref:
        # 旧键迁移：改写为 fit_r2，不再保留 fitConfigRef
        field["fieldExpression"] = build_fit_r2_expression(id_map.get(fit_ref, fit_ref))
        field.pop("fitConfigRef", None)
    # 已有 fit_r2(...) 由上面的 fieldExpression remap 处理
    jct = field.get("judgmentCriteriaByTestType")
    if isinstance(jct, dict):
        for sub in ("acceptance", "status"):
            raw = str(jct.get(sub) or "").strip()
            if raw:
                jct[sub] = remap_pdf_field_refs_in_text(raw, id_map)


def _coerce_field_formulas_dict(raw: Any) -> Dict[str, str]:
    """将 list[{pdfFieldId, expression}] 或 dict 规范为 { f19: expr, ... }。"""
    out: Dict[str, str] = {}
    if isinstance(raw, Mapping):
        for k, v in raw.items():
            tk = normalize_pdf_field_id(str(k))
            if not tk or v is None:
                continue
            expr = str(v).strip()
            if expr:
                out[tk] = expr
        return out
    if isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict):
                continue
            tk = normalize_pdf_field_id(str(row.get("pdfFieldId") or row.get("targetPdfFieldId") or ""))
            expr = str(row.get("expression") or row.get("formula") or "").strip()
            if tk and expr:
                out[tk] = expr
        return out
    return {}


def collect_known_pdf_field_ids(template_obj: Mapping[str, Any]) -> Set[str]:
    ids: Set[str] = set()
    pdf = template_obj.get("pdf")
    if isinstance(pdf, dict):
        fields = pdf.get("fields")
        if isinstance(fields, list):
            for f in fields:
                if isinstance(f, dict):
                    pid = normalize_pdf_field_id(str(f.get("pdfFieldId") or ""))
                    if pid:
                        ids.add(pid)
    fields2 = template_obj.get("fields")
    if isinstance(fields2, list):
        for f in fields2:
            if isinstance(f, dict):
                pid = normalize_pdf_field_id(str(f.get("pdfFieldId") or ""))
                if pid:
                    ids.add(pid)
    return ids


def _root_only_field_formulas_raw(template_obj: Mapping[str, Any]) -> Any:
    """仅读取模板根 / formSchema 上的 fieldFormulas（不含 pdf.fields 逐栏位）。"""
    v = template_obj.get("fieldFormulas")
    if v is None:
        fs = template_obj.get("formSchema")
        if isinstance(fs, dict):
            v = fs.get("fieldFormulas")
    if v is None:
        v = template_obj.get("pdfFieldFormulas")
        if v is None:
            fs = template_obj.get("formSchema")
            if isinstance(fs, dict):
                v = fs.get("pdfFieldFormulas")
    return v


def raw_field_formulas_from_template(template_obj: Mapping[str, Any]) -> Any:
    """兼容旧调用：等价于 ``_root_only_field_formulas_raw``。"""
    return _root_only_field_formulas_raw(template_obj)


def _iter_form_schema_steps(template_obj: Mapping[str, Any]) -> List[Any]:
    steps: List[Any] = []
    for key in ("formSchema",):
        fs = template_obj.get(key)
        if isinstance(fs, dict) and isinstance(fs.get("steps"), list):
            steps = fs.get("steps") or []
            break
    if not steps and isinstance(template_obj.get("steps"), list):
        steps = template_obj.get("steps") or []
    return steps


def _collect_form_schema_field_formulas(template_obj: Mapping[str, Any]) -> Dict[str, str]:
    """从已保存的 ``formSchema.steps``（或根级 ``steps``）提取 ``pdfFieldId -> 公式``。"""
    out: Dict[str, str] = {}
    steps = _iter_form_schema_steps(template_obj)
    if not steps:
        return out
    for fld in _iter_frontend_field_dicts({"steps": steps}):
        src = fld.get("source") if isinstance(fld.get("source"), dict) else {}
        pid = normalize_pdf_field_id(
            str(fld.get("pdfFieldId") or src.get("pdfFieldId") or "")
        )
        expr = str(
            fld.get("formula")
            or fld.get("fieldExpression")
            or src.get("pdfFieldExpression")
            or src.get("fieldExpression")
            or ""
        ).strip()
        if pid and expr:
            out[pid] = expr
    return out


def _collect_form_schema_field_metadata(template_obj: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """从 ``formSchema.steps`` 提取判定元数据（按 pdfFieldId）。"""
    meta: Dict[str, Dict[str, Any]] = {}
    steps = _iter_form_schema_steps(template_obj)
    if not steps:
        return meta
    for fld in _iter_frontend_field_dicts({"steps": steps}):
        src = fld.get("source") if isinstance(fld.get("source"), dict) else {}
        pid = normalize_pdf_field_id(
            str(fld.get("pdfFieldId") or src.get("pdfFieldId") or "")
        )
        if not pid:
            continue
        chunk: Dict[str, Any] = {}
        jct = fld.get("judgmentCriteriaByTestType")
        if not isinstance(jct, dict) or not jct:
            jct = src.get("judgmentCriteriaByTestType")
        if isinstance(jct, dict) and jct:
            chunk["judgmentCriteriaByTestType"] = jct
        fv = fld.get("fieldVerdict")
        if not isinstance(fv, dict) or not fv:
            fv = src.get("fieldVerdict")
        if isinstance(fv, dict) and fv:
            chunk["fieldVerdict"] = fv
        if fld.get("judgmentCriteriaManual") or src.get("judgmentCriteriaManual"):
            chunk["judgmentCriteriaManual"] = True
        if chunk:
            meta[pid] = {**meta.get(pid, {}), **chunk}
    return meta


def collect_merged_field_formulas_dict(template_obj: Mapping[str, Any]) -> Dict[str, str]:
    """
    合并公式来源（优先级：``pdf.fields`` 栏位 > 已保存 ``formSchema.steps`` >
    模板根 ``fieldFormulas``）。
    """
    out: Dict[str, str] = {}
    pdf = template_obj.get("pdf") if isinstance(template_obj.get("pdf"), dict) else {}
    fields = pdf.get("fields")
    if isinstance(fields, list):
        for row in fields:
            if not isinstance(row, dict):
                continue
            pid = normalize_pdf_field_id(str(row.get("pdfFieldId") or ""))
            expr = str(row.get("fieldExpression") or row.get("pdfFieldExpression") or "").strip()
            if pid and expr:
                out[pid] = expr
            elif pid:
                # 旧数据仅有 fitConfigRef：补成 fit_r2(主格)
                r2_master = resolve_fit_r2_master_pid(row)
                if r2_master:
                    out[pid] = build_fit_r2_expression(r2_master)
    for k, v in _collect_form_schema_field_formulas(template_obj).items():
        if k not in out:
            out[k] = v
    root = _coerce_field_formulas_dict(_root_only_field_formulas_raw(template_obj))
    for k, v in root.items():
        if k not in out:
            out[k] = v
    return out


def _collect_pdf_field_metadata_by_id(template_obj: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """从 ``pdf.fields`` 与 ``formSchema.steps`` 提取判定相关元数据（按 pdfFieldId 索引）。"""
    meta: Dict[str, Dict[str, Any]] = dict(_collect_form_schema_field_metadata(template_obj))
    pdf = template_obj.get("pdf") if isinstance(template_obj.get("pdf"), dict) else {}
    fields = pdf.get("fields")
    if not isinstance(fields, list):
        return meta
    for row in fields:
        if not isinstance(row, dict):
            continue
        pid = normalize_pdf_field_id(str(row.get("pdfFieldId") or ""))
        if not pid:
            continue
        chunk: Dict[str, Any] = {}
        jct = row.get("judgmentCriteriaByTestType")
        if isinstance(jct, dict) and jct:
            chunk["judgmentCriteriaByTestType"] = jct
        jsets = row.get("judgmentRuleSets")
        if isinstance(jsets, dict) and jsets:
            chunk["judgmentRuleSets"] = copy.deepcopy(jsets)
        fv = row.get("fieldVerdict")
        if isinstance(fv, dict) and fv:
            chunk["fieldVerdict"] = fv
        if row.get("judgmentCriteriaManual"):
            chunk["judgmentCriteriaManual"] = True
        rules = row.get("formulaRules")
        if not isinstance(rules, list) or not rules:
            rules = row.get("fieldExpressionRules")
        if isinstance(rules, list) and rules:
            chunk["formulaRules"] = copy.deepcopy(rules)
        mean_ref = str(row.get("chapterMeanPdfFieldId") or "").strip()
        if mean_ref:
            chunk["chapterMeanPdfFieldId"] = normalize_pdf_field_id(mean_ref) or mean_ref
        fb = row.get("fitBinding")
        if isinstance(fb, dict) and (fb.get("x") or fb.get("y") or fb.get("r2FieldId")):
            chunk["fitBinding"] = copy.deepcopy(fb)
        r2_master = resolve_fit_r2_master_pid(row)
        if r2_master:
            chunk["fieldExpression"] = build_fit_r2_expression(r2_master)
        display_format = str(row.get("displayFormat") or "").strip()
        if display_format:
            chunk["displayFormat"] = display_format
        if chunk:
            meta[pid] = {**meta.get(pid, {}), **chunk}
    return meta


def _iter_frontend_field_dicts(frontend: Mapping[str, Any]):
    """深度遍历导出 schema 中所有字段对象（含 matrix 单元格）。"""
    steps = frontend.get("steps")
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


def _root_field_formulas_from_template_obj(template_obj: Mapping[str, Any]) -> Any:
    raw = _root_only_field_formulas_raw(template_obj)
    if raw not in (None, "", [], {}):
        return raw
    fs = template_obj.get("formSchema")
    if isinstance(fs, dict):
        for key in ("fieldFormulas", "pdfFieldFormulas"):
            v = fs.get(key)
            if v not in (None, "", [], {}):
                return v
    return None


def build_pdf_field_formula_merge_source(
    input_fields: List[Any],
    template_blob: Optional[Mapping[str, Any]] = None,
    *,
    root_field_formulas: Any = None,
) -> Dict[str, Any]:
    """
    构造 ``merge_field_formulas_into_frontend`` 用的模板片段。

    前端 JSON 导出时 ``input_fields`` 应为主坐标模板 ``pdf.fields``；库内模板用于补全公式与判定元数据。
    """
    rows: List[Dict[str, Any]] = [
        dict(r) for r in (input_fields or []) if isinstance(r, dict)
    ]
    if root_field_formulas is not None:
        attach_root_field_formulas_to_pdf_field_rows(rows, root_field_formulas)
    if isinstance(template_blob, dict):
        attach_root_field_formulas_to_pdf_field_rows(
            rows, _root_field_formulas_from_template_obj(template_blob)
        )
        blob_exprs = collect_merged_field_formulas_dict(template_blob)
        blob_meta = _collect_pdf_field_metadata_by_id(template_blob)
        for row in rows:
            pid = normalize_pdf_field_id(str(row.get("pdfFieldId") or ""))
            if not pid:
                continue
            if not str(row.get("fieldExpression") or row.get("pdfFieldExpression") or "").strip():
                ex = blob_exprs.get(pid)
                if ex:
                    row["fieldExpression"] = ex
            extra = blob_meta.get(pid) or {}
            jct = extra.get("judgmentCriteriaByTestType")
            if isinstance(jct, dict) and jct and not row.get("judgmentCriteriaByTestType"):
                row["judgmentCriteriaByTestType"] = jct
            fv = extra.get("fieldVerdict")
            if isinstance(fv, dict) and fv and not row.get("fieldVerdict"):
                row["fieldVerdict"] = fv
            if extra.get("judgmentCriteriaManual") and not row.get("judgmentCriteriaManual"):
                row["judgmentCriteriaManual"] = True
            rules = extra.get("formulaRules")
            if not isinstance(rules, list) or not rules:
                rules = extra.get("fieldExpressionRules")
            if isinstance(rules, list) and rules:
                row["formulaRules"] = copy.deepcopy(rules)
            mean_ref = str(extra.get("chapterMeanPdfFieldId") or "").strip()
            if mean_ref:
                row["chapterMeanPdfFieldId"] = mean_ref
    out: Dict[str, Any] = {"pdf": {"fields": rows}}
    if root_field_formulas is not None:
        out["fieldFormulas"] = root_field_formulas
    return out


def embed_pdf_field_formulas_into_frontend_fields(
    frontend: Dict[str, Any],
    template_obj: Mapping[str, Any],
    *,
    formulas: Optional[Dict[str, str]] = None,
) -> None:
    """
    将 PDF 栏位公式与判定元数据写入 ``steps`` 内各字段的 ``source``（及 ``formula``），
    满足前端「公式在字段定义上」的约定；原地修改 ``frontend``。
    """
    fmap = formulas if formulas is not None else collect_merged_field_formulas_dict(template_obj)
    meta_by_id = _collect_pdf_field_metadata_by_id(template_obj)
    for fld in _iter_frontend_field_dicts(frontend):
        src = fld.get("source")
        if not isinstance(src, dict):
            src = {}
        pid = normalize_pdf_field_id(
            str(src.get("pdfFieldId") or fld.get("pdfFieldId") or "")
        )
        if not pid:
            continue
        if not src.get("pdfFieldId"):
            src["pdfFieldId"] = pid
            fld["source"] = src
        expr = fmap.get(pid)
        if expr:
            src["pdfFieldExpression"] = expr
            fld["formula"] = expr
            fld["fieldExpression"] = expr
        extra = meta_by_id.get(pid)
        if not extra:
            continue
        if "judgmentCriteriaByTestType" in extra:
            src["judgmentCriteriaByTestType"] = extra["judgmentCriteriaByTestType"]
            fld["judgmentCriteriaByTestType"] = extra["judgmentCriteriaByTestType"]
        if "judgmentRuleSets" in extra:
            src["judgmentRuleSets"] = copy.deepcopy(extra["judgmentRuleSets"])
            fld["judgmentRuleSets"] = copy.deepcopy(extra["judgmentRuleSets"])
        if "fieldVerdict" in extra:
            src["fieldVerdict"] = extra["fieldVerdict"]
            fld["fieldVerdict"] = extra["fieldVerdict"]
        if extra.get("judgmentCriteriaManual"):
            src["judgmentCriteriaManual"] = True
            fld["judgmentCriteriaManual"] = True
        rules = extra.get("formulaRules")
        if not isinstance(rules, list) or not rules:
            rules = extra.get("fieldExpressionRules")
        if isinstance(rules, list) and rules:
            fld["formulaRules"] = copy.deepcopy(rules)
            src["formulaRules"] = copy.deepcopy(rules)
            fld.pop("fieldExpressionRules", None)
            src.pop("fieldExpressionRules", None)
        mean_ref = str(extra.get("chapterMeanPdfFieldId") or "").strip()
        if mean_ref:
            fld["chapterMeanPdfFieldId"] = mean_ref
            src["chapterMeanPdfFieldId"] = mean_ref
        fb = extra.get("fitBinding")
        if isinstance(fb, dict) and (fb.get("x") or fb.get("y") or fb.get("r2FieldId")):
            fld["fitBinding"] = copy.deepcopy(fb)
            src["fitBinding"] = copy.deepcopy(fb)
            fld["type"] = "computed"
            if not str(fld.get("displayFormat") or "").strip():
                fld["displayFormat"] = str(extra.get("displayFormat") or "latex").strip() or "latex"
        r2_expr = str(extra.get("fieldExpression") or "").strip()
        r2_master = parse_fit_r2_master_pid(r2_expr) or resolve_fit_r2_master_pid(extra)
        if r2_master and not isinstance(fld.get("fitBinding"), dict):
            expr = build_fit_r2_expression(r2_master)
            fld["fieldExpression"] = expr
            fld["formula"] = expr
            src["pdfFieldExpression"] = expr
            fld["type"] = "computed"
            fld.pop("fitConfigRef", None)
            src.pop("fitConfigRef", None)
        display_format = str(extra.get("displayFormat") or "").strip()
        if display_format:
            fld["displayFormat"] = display_format
            src["displayFormat"] = display_format
        try:
            from utils.conditional_field_rules import apply_field_logic_connectors_for_frontend_export

            apply_field_logic_connectors_for_frontend_export(fld)
        except Exception:
            pass


def _sanitize_expression_preview(expr: str, max_len: int = 512) -> str:
    s = str(expr or "").strip().replace("\n", " ").replace("\r", " ")
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s


def compile_pdf_field_formula_bundle(
    raw: Any,
    *,
    known_pdf_field_ids: Optional[Set[str]] = None,
) -> Optional[Dict[str, Any]]:
    """
    解析公式映射，生成校验结果及（内部使用的）拓扑求值顺序。

    ``raw`` 可为根级 ``fieldFormulas`` 原始结构，或已合并的 ``Dict[targetPdfFieldId, expression]``。
    """
    formulas = _coerce_field_formulas_dict(raw)
    if not formulas:
        return None

    known = known_pdf_field_ids or set()
    errors: List[str] = []
    warnings: List[str] = []

    target_deps: Dict[str, List[str]] = {}
    nodes: Set[str] = set(formulas.keys())

    for target, expr in formulas.items():
        refs = extract_pdf_field_refs(expr)
        deps = sorted({r for r in refs if r != target})
        target_deps[target] = deps
        nodes.update(deps)
        if known:
            for r in refs:
                if r != target and r not in formulas and r not in known:
                    warnings.append(f"公式 {target} 引用 {r}：该 pdfFieldId 未出现在模板 pdf.fields 中（可能为笔误或外部常量）")

    # 拓扑：边 dep -> target（dep 必须先于 target 求值）
    adj: Dict[str, List[str]] = defaultdict(list)
    indeg: Dict[str, int] = defaultdict(int)
    for n in nodes:
        indeg.setdefault(n, 0)
    for t, deps in target_deps.items():
        for d in deps:
            if d not in nodes:
                nodes.add(d)
                indeg.setdefault(d, 0)
            adj[d].append(t)
            indeg[t] += 1

    q = deque([n for n in sorted(nodes) if indeg.get(n, 0) == 0])
    order: List[str] = []
    while q:
        n = q.popleft()
        order.append(n)
        for t in sorted(adj.get(n, [])):
            indeg[t] -= 1
            if indeg[t] == 0:
                q.append(t)

    if len(order) != len(nodes):
        errors.append("公式依赖存在环路，无法生成可靠的求值顺序；请检查相互引用的 pdfFieldId。")

    entries: List[Dict[str, Any]] = []
    for target in sorted(formulas.keys()):
        expr = formulas[target]
        deps = target_deps.get(target, [])
        entries.append(
            {
                "targetPdfFieldId": target,
                "expression": _sanitize_expression_preview(expr),
                "dependsOn": list(deps),
                "refs": extract_pdf_field_refs(expr),
            }
        )

    return {
        "fieldFormulas": dict(sorted(formulas.items(), key=lambda kv: int(kv[0][1:], 10))),
        "formulaPlan": {
            "version": 1,
            "notation": "pdfFieldId",
            "entries": entries,
            "evaluationOrder": order,
        },
        "formulaValidation": {
            "ok": not errors,
            "errors": errors,
            "warnings": sorted(set(warnings)),
        },
    }


def merge_field_formulas_into_frontend(
    frontend: Dict[str, Any],
    template_obj: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    将模板中的 PDF 栏位公式与判定元数据写入 ``steps`` 各字段定义（``source`` / ``formula``）。

    不再向根级写入 ``fieldFormulas`` / ``formulaPlan``；校验结果置于 ``pdfFieldFormulaValidation``。
    """
    formulas = collect_merged_field_formulas_dict(template_obj)
    out = dict(frontend)
    for k in ("fieldFormulas", "formulaPlan", "formulaValidation"):
        out.pop(k, None)
    embed_pdf_field_formulas_into_frontend_fields(out, template_obj, formulas=formulas)
    if not formulas:
        return out
    bundle = compile_pdf_field_formula_bundle(
        formulas,
        known_pdf_field_ids=collect_known_pdf_field_ids(template_obj),
    )
    if bundle and isinstance(bundle.get("formulaValidation"), dict):
        out["pdfFieldFormulaValidation"] = bundle["formulaValidation"]
    return out


def attach_root_field_formulas_to_pdf_field_rows(rows: List[Any], field_formulas_raw: Any) -> Dict[str, str]:
    """
    将根级 ``fieldFormulas`` 写入 ``rows`` 中匹配 ``pdfFieldId`` 的栏位 ``fieldExpression``。
    栏位已有 ``fieldExpression`` / ``pdfFieldExpression`` 时不覆盖。返回仍未匹配的 ``{f*: expr}``。
    """
    targets = _coerce_field_formulas_dict(field_formulas_raw)
    if not targets:
        return {}
    pending = dict(targets)
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        pid = normalize_pdf_field_id(str(row.get("pdfFieldId") or ""))
        if pid not in pending:
            continue
        expr = pending.pop(pid)
        if not expr:
            continue
        existing = str(row.get("fieldExpression") or row.get("pdfFieldExpression") or "").strip()
        if not existing:
            row["fieldExpression"] = expr
    return pending
