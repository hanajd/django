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


def _field_dict_to_compact_row(mf: Dict[str, Any]) -> Dict[str, Any]:
    page = int(mf.get("page") or 1)
    x = round(float(mf.get("x") or 0), 2)
    y = round(float(mf.get("y") or 0), 2)
    w = round(float(mf.get("w") or 0), 2)
    h = round(float(mf.get("h") or 0), 2)
    rect = [page, x, y, w, h]
    fid = mf.get("id")
    pdf_id = mf.get("pdfFieldId")
    ft = str(mf.get("fieldType") or "text").lower() or "text"
    out: Dict[str, Any] = {"id": fid, "rect": rect, "pdfFieldId": pdf_id}
    if ft != "text":
        out["fieldType"] = ft
    ph = str(mf.get("placeholder") or "").strip()
    if ph and ph != str(fid):
        out["placeholder"] = ph
    ti = str(mf.get("title") or "").strip()
    exp_ph = str(out.get("placeholder") or fid)
    if ti and ti != str(fid) and ti != exp_ph:
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
    return out
