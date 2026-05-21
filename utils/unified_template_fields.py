"""Compact unified template `pdf.fields` normalization (no Django/fitz deps).

Supports optional ``rect``: ``[page, x, y, w, h]`` instead of five separate keys.
Fills defaults expected by HTMLPDF / report fill when keys are omitted.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

_PDF_ID = re.compile(r"^f\d+$")


def materialize_unified_pdf_fields(fields: List[Any]) -> List[Dict[str, Any]]:
    """Expand ``rect`` shorthands and apply safe defaults. Idempotent for full rows."""
    out: List[Dict[str, Any]] = []
    for idx, raw in enumerate(fields or []):
        if not isinstance(raw, dict):
            continue
        f: Dict[str, Any] = dict(raw)
        rect = f.pop("rect", None)
        has_box = all(k in f for k in ("page", "x", "y", "w", "h"))
        if not has_box and all(k in f for k in ("x0", "y0", "x1", "y1")):
            try:
                f["page"] = int(f.get("page") or 1)
                x0, y0, x1, y1 = float(f["x0"]), float(f["y0"]), float(f["x1"]), float(f["y1"])
                f["x"], f["y"] = x0, y0
                f["w"] = max(0.0, x1 - x0)
                f["h"] = max(0.0, y1 - y0)
                has_box = True
            except (TypeError, ValueError):
                pass
        if not has_box and isinstance(rect, (list, tuple)) and len(rect) >= 5:
            try:
                f["page"] = int(rect[0])
                f["x"] = float(rect[1])
                f["y"] = float(rect[2])
                f["w"] = float(rect[3])
                f["h"] = float(rect[4])
            except (TypeError, ValueError):
                pass

        ft = str(f.get("fieldType") or "text").strip().lower() or "text"
        f["fieldType"] = ft
        if ft in ("check", "image"):
            f.setdefault("redText", False)
        else:
            f.setdefault("redText", True)

        f.setdefault("content", "")
        f.setdefault("checked", False)
        f.setdefault("imageData", "")

        fid = str(f.get("id") or "").strip()
        if fid:
            f.setdefault("placeholder", fid)
            f.setdefault("title", str(f.get("placeholder") or fid).strip() or fid)

        pid = str(f.get("pdfFieldId") or "").strip()
        if not _PDF_ID.match(pid):
            iid = str(f.get("id") or "").strip()
            if _PDF_ID.match(iid):
                f["pdfFieldId"] = iid
            else:
                f["pdfFieldId"] = f"f{idx + 1}"
        out.append(f)
    used: set[str] = set()

    def _next_free_f(start: int) -> str:
        n = max(1, int(start))
        while f"f{n}" in used:
            n += 1
        return f"f{n}"

    for idx, f in enumerate(out):
        pid = str(f.get("pdfFieldId") or "").strip()
        if not _PDF_ID.match(pid):
            pid = _next_free_f(idx + 1)
        if pid in used:
            pid = _next_free_f(idx + 1)
        used.add(pid)
        f["pdfFieldId"] = pid
    return out


def compact_unified_pdf_fields_for_storage(fields: List[Any]) -> List[Dict[str, Any]]:
    """Write minimal ``pdf.fields`` rows (``rect`` + omitted defaults) for library template JSON."""
    materialized = materialize_unified_pdf_fields([f for f in fields or [] if isinstance(f, dict)])
    return [_field_dict_to_compact_row(m) for m in materialized]


def frontend_rect_from_field(field: Dict[str, Any]) -> List[float] | None:
    """从导出过程中的栏位对象提取 ``[page, x, y, w, h]``（与模板 pdf.fields.rect 一致）。"""
    if not isinstance(field, dict):
        return None
    existing = field.get("rect")
    if isinstance(existing, (list, tuple)) and len(existing) >= 5:
        try:
            return [
                int(existing[0]),
                round(float(existing[1]), 2),
                round(float(existing[2]), 2),
                round(float(existing[3]), 2),
                round(float(existing[4]), 2),
            ]
        except (TypeError, ValueError):
            pass
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    try:
        page = int(field.get("page", src.get("page", 1)) or 1)
    except (TypeError, ValueError):
        page = 1
    bbox = field.get("__bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        try:
            x, y, w, h = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
        except (TypeError, ValueError):
            x = y = w = h = 0.0
    else:
        try:
            x = float(field.get("x") if field.get("x") is not None else src.get("x", 0))
            y = float(field.get("y") if field.get("y") is not None else src.get("y", 0))
            w = float(field.get("w") if field.get("w") is not None else src.get("w", 0))
            h = float(field.get("h") if field.get("h") is not None else src.get("h", 0))
        except (TypeError, ValueError):
            return None
    if w <= 0 and h <= 0:
        return None
    return [page, round(x, 2), round(y, 2), round(w, 2), round(h, 2)]


def frontend_rect_from_materialized(mf: Dict[str, Any]) -> List[float] | None:
    """从 materialize 后的 pdf 栏位字典生成 rect。"""
    if not isinstance(mf, dict):
        return None
    rect = mf.get("rect")
    if isinstance(rect, (list, tuple)) and len(rect) >= 5:
        try:
            return [
                int(rect[0]),
                round(float(rect[1]), 2),
                round(float(rect[2]), 2),
                round(float(rect[3]), 2),
                round(float(rect[4]), 2),
            ]
        except (TypeError, ValueError):
            pass
    try:
        page = int(mf.get("page") or 1)
        x = float(mf.get("x") or 0)
        y = float(mf.get("y") or 0)
        w = float(mf.get("w") or 0)
        h = float(mf.get("h") or 0)
    except (TypeError, ValueError):
        return None
    if w <= 0 and h <= 0:
        return None
    return [page, round(x, 2), round(y, 2), round(w, 2), round(h, 2)]


def enrich_frontend_steps_rect_from_pdf_fields(
    frontend_obj: Dict[str, Any], pdf_fields: List[Any]
) -> Dict[str, Any]:
    """任务导出收尾：确保无 steps 内坐标、无 pdfBindings（回填仅用 pdfFieldId）。"""
    if not isinstance(frontend_obj, dict):
        return frontend_obj
    from utils.frontend_schema_rule_engine import _compact_form_schema_payload, _strip_pdf_coords_from_form_schema

    frontend_obj = _strip_pdf_coords_from_form_schema(frontend_obj)
    return _compact_form_schema_payload(frontend_obj)


def _field_dict_to_compact_row(mf: Dict[str, Any]) -> Dict[str, Any]:
    page = int(mf.get("page") or 1)
    x = round(float(mf.get("x") or 0), 2)
    y = round(float(mf.get("y") or 0), 2)
    w = round(float(mf.get("w") or 0), 2)
    h = round(float(mf.get("h") or 0), 2)
    rect = [page, x, y, w, h]
    fid = str(mf.get("id") or "").strip()
    pdf_id = str(mf.get("pdfFieldId") or "").strip()
    ft = str(mf.get("fieldType") or "text").lower() or "text"
    out: Dict[str, Any] = {"id": fid or pdf_id, "rect": rect, "pdfFieldId": pdf_id or fid}
    if ft != "text":
        out["fieldType"] = ft
    ph = str(mf.get("placeholder") or "").strip()
    if ph and ph not in {fid, pdf_id, str(out.get("id") or "")}:
        out["placeholder"] = ph
    ti = str(mf.get("title") or "").strip()
    if ti and ti not in {fid, pdf_id, ph, str(out.get("id") or "")}:
        out["title"] = ti
    if ft == "text":
        if mf.get("redText") is False:
            out["redText"] = False
    else:
        if mf.get("redText"):
            out["redText"] = True
    cp = mf.get("checkboxPair")
    if isinstance(cp, dict) and cp:
        out["checkboxPair"] = cp
    fe = str(mf.get("fieldExpression") or mf.get("pdfFieldExpression") or "").strip()
    if fe:
        out["fieldExpression"] = fe
    jct = mf.get("judgmentCriteriaByTestType")
    if isinstance(jct, dict) and jct:
        out["judgmentCriteriaByTestType"] = jct
    if mf.get("judgmentCriteriaManual") is True:
        out["judgmentCriteriaManual"] = True
    fv = mf.get("fieldVerdict")
    if isinstance(fv, dict) and fv:
        out["fieldVerdict"] = fv
    sk = str(mf.get("templateSectionKey") or mf.get("sectionKey") or "").strip()
    if sk:
        out["templateSectionKey"] = sk
    return out
