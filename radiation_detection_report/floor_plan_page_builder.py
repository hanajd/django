"""
工作场所平面布局及检测点方位图页：插入现场记录 floorPlan 平面图与图题。
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any, Mapping, Optional

import fitz

from radiation_detection_report.generator import (
    FONT_SIZE_WU_HAO,
    FONT_SIZE_XIAO_SI,
    FontResources,
    _draw_line,
    _load_font_resources,
    _register_fonts,
    _text_ascent,
    default_layout_path,
    load_layout,
)

logger = logging.getLogger(__name__)

_PAGE_W = 595.2999877929688
_PAGE_H = 841.9000244140625


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip())


def _decode_image_bytes(raw_b64: str) -> Optional[bytes]:
    text = (raw_b64 or "").strip()
    if not text:
        return None
    if text.startswith("data:image"):
        text = text.split(",", 1)[1]
    try:
        return base64.b64decode(text, validate=False)
    except (ValueError, TypeError):
        return None


def _floor_plan_diagram_image_bytes(raw: str) -> Optional[bytes]:
    s = (raw or "").strip()
    if not s.startswith("{"):
        return None
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    b64 = obj.get("imageBase64")
    if isinstance(b64, str) and b64.strip():
        return _decode_image_bytes(b64)
    return None


def _normalize_library_rel_path(raw: str) -> str:
    """将 /media/file_library/... 规范为相对 FILE_LIBRARY_ROOT 的路径。"""
    s = (raw or "").strip().replace("\\", "/")
    if s.startswith("/media/"):
        s = s[len("/media/") :]
    elif s.lower().startswith("media/"):
        s = s[len("media/") :]
    while s.startswith("/"):
        s = s[1:]
    if s.startswith("file_library/"):
        s = s[len("file_library/") :]
    return s


def _read_library_image_bytes(rel_path: str) -> Optional[bytes]:
    rel = _normalize_library_rel_path(rel_path)
    if not rel:
        return None
    try:
        from apps.core import pipeline_service

        path = pipeline_service.library_absolute_path(rel)
        if path and path.is_file():
            return path.read_bytes()
    except Exception as exc:
        logger.debug("read floor plan image: %s", exc)
    return None


_F_SLOT_KEY = re.compile(r"^f\d+$", re.I)


def _iter_schema_fields_from_steps(steps: list | None):
    """遍历 unified_form_template steps 下全部控件（含 matrix 单元格）。"""
    if not isinstance(steps, list):
        return
    for step in steps:
        if not isinstance(step, dict):
            continue
        fl = step.get("fields")
        if isinstance(fl, list):
            for f in fl:
                if isinstance(f, dict):
                    yield f
        sections = step.get("sections")
        if not isinstance(sections, list):
            continue
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            fl2 = sec.get("fields")
            if isinstance(fl2, list):
                for f in fl2:
                    if isinstance(f, dict):
                        yield f
            m = sec.get("matrix") if isinstance(sec.get("matrix"), dict) else {}
            for hf in m.get("headerFields") or []:
                if isinstance(hf, dict):
                    yield hf
            for row in m.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                cells = row.get("cells") if isinstance(row.get("cells"), dict) else {}
                for cell in cells.values():
                    if isinstance(cell, dict):
                        yield cell


def _floor_plan_pdf_field_ids(site_template_parsed: Optional[Mapping[str, Any]]) -> list[str]:
    """从现场记录模板解析 type=floorPlan 栏位的 pdfFieldId（如 DSA f548）。"""
    if not isinstance(site_template_parsed, dict):
        return []
    steps = site_template_parsed.get("steps")
    if not isinstance(steps, list):
        form = site_template_parsed.get("formSchema") or site_template_parsed.get("form_schema")
        if isinstance(form, dict):
            steps = form.get("steps")
    ids: list[str] = []
    seen: set[str] = set()
    for fld in _iter_schema_fields_from_steps(steps if isinstance(steps, list) else None):
        if str(fld.get("type") or "").strip() != "floorPlan":
            continue
        pid = str(fld.get("pdfFieldId") or fld.get("id") or "").strip().lower()
        if not _F_SLOT_KEY.match(pid) or pid in seen:
            continue
        seen.add(pid)
        ids.append(pid)
    return ids


def _bucket_value(bucket: Mapping[str, Any], pid: str) -> object:
    if pid in bucket:
        return bucket.get(pid)
    alt = pid.upper() if pid.startswith("f") else pid
    if alt in bucket:
        return bucket.get(alt)
    return None


def _coerce_floor_plan_image_bytes(val: object) -> Optional[bytes]:
    if val in (None, ""):
        return None
    if not isinstance(val, str):
        return None
    s = val.strip()
    if not s:
        return None
    if "/signatures/" in s.replace("\\", "/").lower():
        return None
    if s.startswith("data:image"):
        return _decode_image_bytes(s)
    if s.startswith(("{", "[")) and "floorPlanDiagram" in s:
        return _floor_plan_diagram_image_bytes(s)
    if s.startswith(("/media", "media/", "file_library", "inspection_submits")):
        norm = s.replace("\\", "/").lower()
        if "/signatures/" in norm:
            return None
        return _read_library_image_bytes(_normalize_library_rel_path(s))
    if len(s) > 64 and re.fullmatch(r"[A-Za-z0-9+/=\s]+", s):
        return _decode_image_bytes(s)
    return None


def template_has_floor_plan_field(site_template_parsed: Optional[Mapping[str, Any]]) -> bool:
    """现场记录模板是否配置了 type=floorPlan 栏位。"""
    return bool(_floor_plan_pdf_field_ids(site_template_parsed))


def extract_floor_plan_image_bytes(
    payload: Optional[Mapping[str, Any]],
    *,
    site_template_parsed: Optional[Mapping[str, Any]] = None,
) -> Optional[bytes]:
    """从现场记录提交 JSON 提取平面图二进制。"""
    if not isinstance(payload, dict):
        return None
    dd = payload.get("dynamicData")
    if not isinstance(dd, dict):
        dd = {}
    tr = payload.get("testResult")
    if not isinstance(tr, dict):
        tr = {}

    field_ids = _floor_plan_pdf_field_ids(site_template_parsed)
    if not field_ids:
        return None

    for pid in field_ids:
        for raw in (_bucket_value(dd, pid), _bucket_value(tr, pid)):
            data = _coerce_floor_plan_image_bytes(raw)
            if data:
                return data
    return None


def resolve_equipment_venue(payload: Optional[Mapping[str, Any]]) -> str:
    if not isinstance(payload, dict):
        return ""
    ei = payload.get("equipmentInfo") if isinstance(payload.get("equipmentInfo"), dict) else {}
    dd = payload.get("dynamicData") if isinstance(payload.get("dynamicData"), dict) else {}
    for raw in (
        ei.get("location"),
        ei.get("installLocation"),
        ei.get("useLocation"),
        dd.get("设备所在场所"),
        dd.get("所在场所"),
    ):
        text = _norm(raw)
        if text:
            return text
    return ""


def count_existing_figure_numbers(doc: fitz.Document) -> int:
    count = 0
    for pi in range(doc.page_count):
        compact = re.sub(r"\s+", "", doc[pi].get_text("text"))
        count += len(re.findall(r"图\d+", compact))
    return count


def build_floor_plan_page_pdf_bytes(
    image_bytes: Optional[bytes] = None,
    *,
    section_major: int,
    section_minor: int,
    venue: str = "",
    figure_no: int = 1,
) -> bytes:
    from radiation_detection_report.generator import _format_section_title_text

    layout = load_layout(default_layout_path())
    font_base = _load_font_resources()
    doc = fitz.open()
    page = doc.new_page(width=_PAGE_W, height=_PAGE_H)
    try:
        from utils.pdf_merge import insert_report_page_watermark_bottom_layer

        insert_report_page_watermark_bottom_layer(page, _PAGE_W, _PAGE_H)
    except Exception:
        pass
    fonts = _register_fonts(page, font_base)
    x_left = float(layout.table_x_left)
    x_right = float(layout.table_x_right)
    title_y = float(layout.title_y_top)
    title_h = float(layout.title_height)

    title = _format_section_title_text(
        f"{section_major}.{section_minor} 工作场所平面布局及检测点方位图"
    )
    baseline = title_y + _text_ascent(FONT_SIZE_XIAO_SI)
    inner = fitz.Rect(x_left, title_y, x_right, title_y + title_h)
    _draw_line(page, title, baseline, inner, fonts, FONT_SIZE_XIAO_SI, "left")

    if image_bytes:
        margin_top = title_y + title_h + 6.0
        max_w = x_right - x_left
        caption_h = 22.0
        footer = 64.0
        max_h = min(460.0, _PAGE_H - margin_top - footer - caption_h)

        img_w, img_h = max_w, max_h * 0.72
        try:
            pix = fitz.Pixmap(image_bytes)
            if pix.width > 0 and pix.height > 0:
                scale = min(max_w / float(pix.width), max_h / float(pix.height), 1.0)
                img_w = float(pix.width) * scale
                img_h = float(pix.height) * scale
        except Exception:
            pass

        img_rect = fitz.Rect(x_left, margin_top, x_left + img_w, margin_top + img_h)
        try:
            from utils.pdf_compress import compress_image_bytes_for_pdf

            image_bytes = compress_image_bytes_for_pdf(
                image_bytes,
                box_width_pt=float(img_w),
                box_height_pt=float(img_h),
                target_dpi=150.0,
                max_edge_px=1600,
            )
            page.insert_image(img_rect, stream=image_bytes, keep_proportion=True)
        except Exception as exc:
            logger.warning("insert floor plan image failed: %s", exc)
            doc.close()
            return b""

        venue_text = _norm(venue) or "工作场所"
        caption = f"图{figure_no}  {venue_text}平面布局及检测点方位图"
        cap_y = margin_top + img_h + 8.0
        cap_baseline = cap_y + _text_ascent(FONT_SIZE_WU_HAO)
        cap_inner = fitz.Rect(x_left, cap_y, x_right, cap_y + caption_h)
        _draw_line(page, caption, cap_baseline, cap_inner, fonts, FONT_SIZE_WU_HAO, "center")

    try:
        from utils.pdf_compress import pdf_document_to_compressed_bytes

        return pdf_document_to_compressed_bytes(doc, subset_fonts=True)
    finally:
        doc.close()
