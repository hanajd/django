"""
统一模板 JSON：以 pdfFieldId（f+数字）为键的公式识别与前端用计算计划。

模板中可写顶层 ``fieldFormulas``（或 ``formSchema.fieldFormulas`` / ``pdfFieldFormulas``）：
  {"f19": "f140 * f141", "f51": "(f148 + f149 + f150) / 3"}

导出前端 schema 时合并 ``formulaPlan``（依赖与求值顺序），供前端表达式引擎消费。
"""
from __future__ import annotations

import re
from collections import defaultdict, deque
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

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


def raw_field_formulas_from_template(template_obj: Mapping[str, Any]) -> Any:
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
    解析 fieldFormulas，生成前端用 formulaPlan（含拓扑求值顺序）。

    返回 None 表示无任何公式；否则为可并入前端 JSON 的字典。
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
    """将模板中的 fieldFormulas 合并进规则引擎导出的前端 JSON。"""
    raw = raw_field_formulas_from_template(template_obj)
    bundle = compile_pdf_field_formula_bundle(
        raw,
        known_pdf_field_ids=collect_known_pdf_field_ids(template_obj),
    )
    if not bundle:
        return frontend
    out = dict(frontend)
    out.update(bundle)
    return out
