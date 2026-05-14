"""
统一模板 JSON：以 pdfFieldId（f+数字）为键的公式识别与前端用计算计划。

- **推荐**：在 ``pdf.fields`` 各栏位上写 ``fieldExpression``（或 ``pdfFieldExpression``），
  必要时写 ``judgmentCriteriaByTestType`` / ``fieldVerdict``；导出前端 schema 时会写入
  对应表单字段的 ``source.pdfFieldExpression``、``formula``、``source.judgmentCriteriaByTestType``。
- **兼容**：仍支持顶层 ``fieldFormulas``（或 ``formSchema.fieldFormulas``）；保存库模板时会尽量
  下发到各 ``pdf.fields`` 行，无法匹配栏位的条目保留在根级 ``fieldFormulas``。
"""
from __future__ import annotations

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


def extract_pdf_field_refs(expression: str) -> List[str]:
    """从公式串中提取所有 f+数字 占位符（规范化）。"""
    out: Set[str] = set()
    for m in _PDF_FID_RE.finditer(expression or ""):
        out.add(normalize_pdf_field_id(m.group(0)))
    return sorted(out)


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


def collect_merged_field_formulas_dict(template_obj: Mapping[str, Any]) -> Dict[str, str]:
    """
    合并公式来源（优先级：pdf.fields 栏位上的 ``fieldExpression`` / ``pdfFieldExpression``
    高于模板根 ``fieldFormulas``，同目标 id 以栏位为准）。
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
    root = _coerce_field_formulas_dict(_root_only_field_formulas_raw(template_obj))
    for k, v in root.items():
        if k not in out:
            out[k] = v
    return out


def _collect_pdf_field_metadata_by_id(template_obj: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """从 ``pdf.fields`` 提取判定相关元数据（按 pdfFieldId 索引）。"""
    meta: Dict[str, Dict[str, Any]] = {}
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
        fv = row.get("fieldVerdict")
        if isinstance(fv, dict) and fv:
            chunk["fieldVerdict"] = fv
        if chunk:
            meta[pid] = chunk
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
            continue
        pid = normalize_pdf_field_id(str(src.get("pdfFieldId") or ""))
        if not pid:
            continue
        expr = fmap.get(pid)
        if expr:
            src["pdfFieldExpression"] = expr
            fld["formula"] = expr
        extra = meta_by_id.get(pid)
        if not extra:
            continue
        if "judgmentCriteriaByTestType" in extra:
            src["judgmentCriteriaByTestType"] = extra["judgmentCriteriaByTestType"]
        if "fieldVerdict" in extra:
            src["fieldVerdict"] = extra["fieldVerdict"]


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
