"""Compact unified template `pdf.fields` normalization (no Django/fitz deps).

Supports optional ``rect``: ``[page, x, y, w, h]`` instead of five separate keys.
Fills defaults expected by HTMLPDF / report fill when keys are omitted.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

_PDF_ID = re.compile(r"^f\d+$")


def pdf_field_reading_order_key(field: Dict[str, Any]) -> tuple:
    """与 HTMLPDF 画板 ``getFieldsInReadingOrder`` 一致：page → y → x。"""
    try:
        page = int(field.get("page") or 1)
    except (TypeError, ValueError):
        page = 1
    try:
        y = float(field.get("y") or 0)
    except (TypeError, ValueError):
        y = 0.0
    try:
        x = float(field.get("x") or 0)
    except (TypeError, ValueError):
        x = 0.0
    tie = str(field.get("pdfFieldId") or field.get("id") or "")
    return (page, y, x, tie)


def reindex_pdf_field_ids_by_list_order(fields: List[Any]) -> List[Dict[str, Any]]:
    """按模板/编辑器 fields 数组顺序赋 f1..fN，与 HTMLPDF 侧栏、画板角标一致（不按坐标排序）。"""
    rows: List[Dict[str, Any]] = []
    for raw in fields or []:
        if isinstance(raw, dict):
            rows.append(dict(raw))
    for idx, f in enumerate(rows, start=1):
        f["pdfFieldId"] = f"f{idx}"
    return rows


def reindex_pdf_field_ids_by_reading_order(fields: List[Any]) -> List[Dict[str, Any]]:
    """已弃用：画板序号以列表顺序为准，请用 ``reindex_pdf_field_ids_by_list_order``。"""
    return reindex_pdf_field_ids_by_list_order(fields)


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
    return reindex_pdf_field_ids_by_list_order(out)


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


def _pdf_anchor_from_materialized_field(mf: Dict[str, Any]) -> Dict[str, Any] | None:
    """由模板 ``pdf.fields`` 条目生成 Flutter ``pdfAnchor``。"""
    rect = frontend_rect_from_materialized(mf)
    if not rect or len(rect) < 5:
        return None
    try:
        page = int(rect[0])
        x = float(rect[1])
        y = float(rect[2])
        w = float(rect[3])
        h = float(rect[4])
    except (TypeError, ValueError):
        return None
    if w <= 0 and h <= 0:
        return None
    out: Dict[str, Any] = {
        "page": page,
        "rect": [
            round(x, 2),
            round(y, 2),
            round(x + max(w, 0.0), 2),
            round(y + max(h, 0.0), 2),
        ],
    }
    ft = str(mf.get("fieldType") or "").strip().lower()
    if ft and ft != "text":
        out["anchorType"] = ft
    return out


def _inject_form_field_coords_from_pdf_fields(
    frontend_obj: Dict[str, Any], pdf_fields: List[Any]
) -> Dict[str, Any]:
    """将 ``filled_fields`` 中的 page/rect 写入表单 steps（规则引擎常已剥掉坐标）。"""
    if not isinstance(frontend_obj, dict) or not pdf_fields:
        return frontend_obj
    from utils.frontend_schema_rule_engine import _iter_form_field_nodes

    index: Dict[str, Dict[str, Any]] = {}
    for mf in pdf_fields:
        if not isinstance(mf, dict):
            continue
        pid = str(mf.get("pdfFieldId") or mf.get("id") or "").strip()
        if not pid:
            continue
        anchor = _pdf_anchor_from_materialized_field(mf)
        if anchor:
            index[pid] = anchor
    if not index:
        return frontend_obj

    for _section, field in _iter_form_field_nodes(frontend_obj):
        pid = str(field.get("pdfFieldId") or field.get("id") or "").strip()
        anchor = index.get(pid)
        if not anchor:
            continue
        field["pdfAnchor"] = dict(anchor)
        rect = anchor["rect"]
        field["page"] = anchor["page"]
        field["x"] = rect[0]
        field["y"] = rect[1]
        field["w"] = max(0.0, float(rect[2]) - float(rect[0]))
        field["h"] = max(0.0, float(rect[3]) - float(rect[1]))
    return frontend_obj


def enrich_frontend_steps_rect_from_pdf_fields(
    frontend_obj: Dict[str, Any], pdf_fields: List[Any]
) -> Dict[str, Any]:
    """任务导出收尾：PDF 覆盖模式保留 Flutter 可用的 ``pdfAnchor``。"""
    if not isinstance(frontend_obj, dict):
        return frontend_obj
    from utils.frontend_schema_rule_engine import _attach_pdf_anchors_to_form_schema, _compact_form_schema_payload

    frontend_obj["pdfOverlay"] = True
    frontend_obj = _inject_form_field_coords_from_pdf_fields(frontend_obj, pdf_fields)
    frontend_obj = _attach_pdf_anchors_to_form_schema(frontend_obj)
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
