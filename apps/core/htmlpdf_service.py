"""HTML-PDF 编辑器服务：模板导入/导出与 PDF 生成。"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import fitz
from django.conf import settings

DEFAULT_FONT_PT = 10.5
# 最小文本框：至少容纳一个五号汉字宽度，且高度可正常显示五号字单行
MIN_TEXT_FIELD_WIDTH = (DEFAULT_FONT_PT * 1.05) + 10
MIN_TEXT_FIELD_HEIGHT = (DEFAULT_FONT_PT * 1.2) + 6

_FONTS_DIR = Path(settings.BASE_DIR) / "htmlpdf" / "fonts"
SIMSUN_FONT = _FONTS_DIR / "SIMSUN.TTC"
TIMES_FONT_CANDIDATES = [
    _FONTS_DIR / "times.ttf",
    _FONTS_DIR / "Times New Roman.ttf",
    Path("/usr/share/fonts/truetype/msttcorefonts/times.ttf"),
]
CHECK_FONT_CANDIDATES = [
    _FONTS_DIR / "SEGUISYM.TTF",
    _FONTS_DIR / "SEGUISYM.ttf",
    _FONTS_DIR / "seguisym.ttf",
    _FONTS_DIR / "SegoeUISymbol.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    SIMSUN_FONT,
]


def ensure_htmlpdf_temp_dir(user_id: int) -> Path:
    root = Path(settings.FILE_LIBRARY_TEMP_ROOT) / "htmlpdf" / str(user_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def htmlpdf_source_pdf_path(user_id: int) -> Path:
    return ensure_htmlpdf_temp_dir(user_id) / "original.pdf"


def htmlpdf_source_meta_path(user_id: int) -> Path:
    return ensure_htmlpdf_temp_dir(user_id) / "source_meta.json"


def _coerce_rect(raw_rect) -> fitz.Rect | None:
    if raw_rect is None:
        return None
    if isinstance(raw_rect, fitz.Rect):
        return raw_rect
    if hasattr(raw_rect, "bbox"):
        box = getattr(raw_rect, "bbox")
        if box and len(box) >= 4:
            return fitz.Rect(float(box[0]), float(box[1]), float(box[2]), float(box[3]))
    if isinstance(raw_rect, (list, tuple)) and len(raw_rect) >= 4:
        return fitz.Rect(float(raw_rect[0]), float(raw_rect[1]), float(raw_rect[2]), float(raw_rect[3]))
    return None


def _extract_table_cell_rects(page: fitz.Page) -> List[fitz.Rect]:
    out: List[fitz.Rect] = []
    if hasattr(page, "find_tables"):
        try:
            table_finder = page.find_tables()
        except Exception:
            table_finder = None
        tables = getattr(table_finder, "tables", None) or []
        for table in tables:
            rows = getattr(table, "rows", None)
            if rows:
                for row in rows:
                    for cell in (getattr(row, "cells", None) or []):
                        rect = _coerce_rect(cell)
                        if rect is not None and not rect.is_empty:
                            out.append(rect)
                continue
            for cell in (getattr(table, "cells", None) or []):
                rect = _coerce_rect(cell)
                if rect is not None and not rect.is_empty:
                    out.append(rect)
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    for d in drawings:
        if d.get("type") not in ("s", "fs", "sf"):
            continue
        rect = _coerce_rect(d.get("rect"))
        if rect is None or rect.is_empty:
            continue
        w, h = float(rect.width), float(rect.height)
        if w < 18 or h < 10:
            continue
        if w > page.rect.width * 0.95 and h > page.rect.height * 0.95:
            continue
        out.append(rect)
    return out


def detect_table_cell_bbox_at_point(source_pdf: Path, page_no: int, x: float, y: float) -> Dict[str, float] | None:
    if not source_pdf.exists():
        raise FileNotFoundError("source pdf not found")
    if page_no <= 0:
        return None
    doc = fitz.open(source_pdf)
    try:
        if page_no > len(doc):
            return None
        page = doc[page_no - 1]
        point = fitz.Point(float(x), float(y))
        hits = [rect for rect in _extract_table_cell_rects(page) if rect.contains(point)]
        if not hits:
            return None
        hits.sort(key=lambda r: max(0.0, float(r.width) * float(r.height)))
        rect = hits[0]
        return {
            "x": float(rect.x0),
            "y": float(rect.y0),
            "w": float(rect.width),
            "h": float(rect.height),
        }
    finally:
        doc.close()


def detect_table_cells_for_field_boxes(source_pdf: Path, fields: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    批量为字段定位所属表格单元格（按字段中心点命中最小 cell）。
    返回项:
    {
      "index": 字段下标,
      "page": 页码,
      "cell": {"x","y","w","h"} | None
    }
    """
    out: List[Dict[str, Any]] = []
    if not source_pdf.exists():
        raise FileNotFoundError("source pdf not found")
    doc = fitz.open(source_pdf)
    try:
        page_cells: Dict[int, List[fitz.Rect]] = {}
        for i in range(len(doc)):
            page_no = i + 1
            page_cells[page_no] = _extract_table_cell_rects(doc[i])
        for idx, item in enumerate(fields or []):
            if not isinstance(item, dict):
                out.append({"index": idx, "page": 0, "cell": None})
                continue
            try:
                page_no = int(item.get("page") or 0)
                x = float(item.get("x") or 0.0)
                y = float(item.get("y") or 0.0)
                w = float(item.get("w") or 0.0)
                h = float(item.get("h") or 0.0)
            except Exception:
                out.append({"index": idx, "page": 0, "cell": None})
                continue
            if page_no <= 0 or page_no not in page_cells:
                out.append({"index": idx, "page": page_no, "cell": None})
                continue
            pt = fitz.Point(x + w / 2.0, y + h / 2.0)
            hits = [rect for rect in page_cells[page_no] if rect.contains(pt)]
            if not hits:
                out.append({"index": idx, "page": page_no, "cell": None})
                continue
            hits.sort(key=lambda r: max(0.0, float(r.width) * float(r.height)))
            hit = hits[0]
            out.append(
                {
                    "index": idx,
                    "page": page_no,
                    "cell": {
                        "x": float(hit.x0),
                        "y": float(hit.y0),
                        "w": float(hit.width),
                        "h": float(hit.height),
                    },
                }
            )
        return out
    finally:
        doc.close()


def extract_table_cells_with_text(source_pdf: Path, page_no: int) -> List[Dict[str, Any]]:
    """
    读取某页表格单元格及单元格文字（按词坐标拼接）。
    返回 [{"x","y","w","h","text"}...]
    """
    if not source_pdf.exists():
        raise FileNotFoundError("source pdf not found")
    if page_no <= 0:
        return []
    doc = fitz.open(source_pdf)
    try:
        if page_no > len(doc):
            return []
        page = doc[page_no - 1]
        rects = _extract_table_cell_rects(page)
        words = page.get_text("words") or []
        word_rows = []
        for w in words:
            if not isinstance(w, (list, tuple)) or len(w) < 5:
                continue
            x0, y0, x1, y1, txt = w[:5]
            t = str(txt or "").strip()
            if not t:
                continue
            word_rows.append(
                {
                    "x0": float(x0),
                    "y0": float(y0),
                    "x1": float(x1),
                    "y1": float(y1),
                    "cx": (float(x0) + float(x1)) / 2.0,
                    "cy": (float(y0) + float(y1)) / 2.0,
                    "text": t,
                }
            )

        out: List[Dict[str, Any]] = []
        for r in rects:
            inside = []
            for wd in word_rows:
                pt = fitz.Point(float(wd["cx"]), float(wd["cy"]))
                if r.contains(pt):
                    inside.append(wd)
            inside.sort(key=lambda z: (float(z["y0"]), float(z["x0"])))
            text = "".join(str(z.get("text") or "") for z in inside).strip()
            out.append(
                {
                    "x": float(r.x0),
                    "y": float(r.y0),
                    "w": float(r.width),
                    "h": float(r.height),
                    "text": text,
                }
            )
        return out
    finally:
        doc.close()


def _load_full_text_coordinate_boxing():
    path = Path(settings.BASE_DIR) / "htmlpdf" / "full_text_coordinate_boxing.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("full_text_coordinate_boxing", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def htmlpdf_auto_red_text_fields_for_editor(user_id: int) -> List[Dict[str, Any]]:
    """
    自动划框：复用 full_text_coordinate_boxing 的合并规则，仅保留 PDF 中红色字体对应的文本块。
    返回编辑器 fields 列表片段（含 redText 标记，前端用红色显示填写内容）。
    """
    pdf = htmlpdf_source_pdf_path(user_id)
    if not pdf.is_file():
        return []
    mod = _load_full_text_coordinate_boxing()
    if mod is None:
        return []
    extract_fn = getattr(mod, "extract_red_text_field_boxes", None)
    if not callable(extract_fn):
        return []
    boxes = extract_fn(str(pdf))
    if not isinstance(boxes, list):
        boxes = []
    check_boxes: List[Dict[str, Any]] = []
    extract_check_fn = getattr(mod, "extract_checkbox_symbol_boxes", None)
    if callable(extract_check_fn):
        raw_checks = extract_check_fn(str(pdf))
        if isinstance(raw_checks, list):
            check_boxes = raw_checks
    image_boxes: List[Dict[str, Any]] = []
    extract_image_fn = getattr(mod, "extract_signature_image_boxes", None)
    if callable(extract_image_fn):
        raw_images = extract_image_fn(str(pdf))
        if isinstance(raw_images, list):
            image_boxes = raw_images
    merged = list(boxes) + list(check_boxes) + list(image_boxes)
    fields: List[Dict[str, Any]] = []
    for i, b in enumerate(merged):
        if not isinstance(b, dict):
            continue
        field_type = str(b.get("fieldType") or "text").strip().lower()
        is_check = field_type == "check"
        is_image = field_type == "image"
        auto_name = str(b.get("name") or "").strip()
        snippet = str(b.get("text") or "").strip().replace("\n", " ")[:24]
        ph = auto_name or (
            (f"勾选{i + 1}")
            if is_check
            else ((f"图片{i + 1}") if is_image else (f"红字{i + 1}" + (f" · {snippet}" if snippet else "")))
        )
        row: Dict[str, Any] = {
            "page": int(b.get("page") or 1),
            "x": float(b.get("x") or 0),
            "y": float(b.get("y") or 0),
            "w": float(b.get("w") or 0),
            "h": float(b.get("h") or 0),
            "placeholder": ph,
            "originalPlaceholder": ph,
            "fieldType": "check" if is_check else ("image" if is_image else "text"),
            "redText": False if (is_check or is_image) else True,
            "value": "",
            "checked": False,
            "imageData": "",
        }
        cp = b.get("checkboxPair")
        if isinstance(cp, dict) and cp and is_check:
            row["checkboxPair"] = cp
        fields.append(row)
    return fields


def pick_times_font() -> Path | None:
    for p in TIMES_FONT_CANDIDATES:
        if p.exists():
            return p
    return None


def pick_check_font() -> Path | None:
    # 优先使用 SEGUISYM.TTF，确保对勾符号在导出 PDF 中可稳定显示。
    preferred = _FONTS_DIR / "SEGUISYM.TTF"
    if preferred.exists():
        return preferred
    for p in CHECK_FONT_CANDIDATES:
        if p.exists():
            return p
    return None


def contains_cjk(text: str) -> bool:
    return re.search(r"[\u3400-\u9FFF]", text or "") is not None


def wrap_text_to_width(text: str, max_width: float, font: fitz.Font, font_size: float) -> str:
    lines: List[str] = []
    for para in (text or "").splitlines() or [""]:
        current = ""
        for ch in para:
            trial = current + ch
            if font.text_length(trial, fontsize=font_size) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                    current = ch
                else:
                    lines.append(ch)
                    current = ""
        lines.append(current)
    return "\n".join(lines)


def fit_text_for_box(text: str, rect: fitz.Rect, font: fitz.Font) -> Tuple[str, float]:
    max_fs = min(10.5, rect.height * 0.85)
    min_fs = 5.0
    fs = max(max_fs, min_fs)
    while fs >= min_fs:
        wrapped = wrap_text_to_width(text, max(1.0, rect.width - 2), font, fs)
        line_count = max(1, wrapped.count("\n") + 1)
        line_height = fs * 1.2
        if line_count * line_height <= max(1.0, rect.height - 1):
            return wrapped, fs
        fs -= 0.5
    return wrap_text_to_width(text, max(1.0, rect.width - 2), font, min_fs), min_fs


def ensure_min_text_rect(rect: fitz.Rect) -> fitz.Rect:
    width = rect.width
    height = rect.height
    if width >= MIN_TEXT_FIELD_WIDTH and height >= MIN_TEXT_FIELD_HEIGHT:
        return rect
    cx = (rect.x0 + rect.x1) / 2.0
    cy = (rect.y0 + rect.y1) / 2.0
    half_w = max(width, MIN_TEXT_FIELD_WIDTH) / 2.0
    half_h = max(height, MIN_TEXT_FIELD_HEIGHT) / 2.0
    return fitz.Rect(cx - half_w, cy - half_h, cx + half_w, cy + half_h)


def safe_inset_rect(rect: fitz.Rect, pad: float) -> fitz.Rect:
    if rect.width <= pad * 2 + 0.5 or rect.height <= pad * 2 + 0.5:
        return rect
    return fitz.Rect(rect.x0 + pad, rect.y0 + pad, rect.x1 - pad, rect.y1 - pad)


def parse_template_json(raw_text: str) -> Dict[str, Any]:
    data = json.loads(raw_text)
    if isinstance(data, list):
        return {"fields": data, "content": {}, "bindings": {}, "source_pdf": {}}
    if isinstance(data, dict):
        # Unified template format (v1/v2): keep HTMLPDF fields under pdf.fields.
        pdf_block = data.get("pdf")
        if isinstance(pdf_block, dict) and isinstance(pdf_block.get("fields"), list):
            template_meta = data.get("meta", {}) if isinstance(data.get("meta"), dict) else {}
            if not template_meta:
                template_meta = {
                    "templateId": data.get("templateId", ""),
                    "templateName": data.get("templateName", ""),
                    "version": data.get("version", ""),
                    "reportType": data.get("reportType", ""),
                    "standard": data.get("standard", ""),
                    "pdfUrl": data.get("pdfUrl", ""),
                    "locale": data.get("locale", ""),
                }
            form_schema = data.get("formSchema") if isinstance(data.get("formSchema"), dict) else {}
            if not form_schema:
                form_schema = {
                    "constants": data.get("constants", {}) if isinstance(data.get("constants"), dict) else {},
                    "enums": data.get("enums", {}) if isinstance(data.get("enums"), dict) else {},
                    "steps": data.get("steps", []) if isinstance(data.get("steps"), list) else [],
                }
            return {
                "fields": pdf_block.get("fields") or [],
                "content": data.get("content", {}) if isinstance(data.get("content"), dict) else {},
                "bindings": data.get("bindings", {}) if isinstance(data.get("bindings"), dict) else {},
                "source_pdf": pdf_block.get("source_pdf", {}) if isinstance(pdf_block.get("source_pdf"), dict) else {},
                "template_meta": template_meta,
                "form_schema": form_schema,
                "templateId": template_meta.get("templateId", ""),
                "templateName": template_meta.get("templateName", ""),
                "version": template_meta.get("version", ""),
                "reportType": template_meta.get("reportType", ""),
                "standard": template_meta.get("standard", ""),
                "pdfUrl": template_meta.get("pdfUrl", ""),
                "locale": template_meta.get("locale", ""),
                "constants": form_schema.get("constants", {}) if isinstance(form_schema.get("constants"), dict) else {},
                "enums": form_schema.get("enums", {}) if isinstance(form_schema.get("enums"), dict) else {},
                "steps": form_schema.get("steps", []) if isinstance(form_schema.get("steps"), list) else [],
                "schema": data.get("schema") or "",
            }
        if "fields" not in data and "content" not in data:
            return {
                "fields": [],
                "content": data,
                "bindings": data.get("bindings", {}) if isinstance(data.get("bindings"), dict) else {},
                "source_pdf": data.get("source_pdf", {}) if isinstance(data.get("source_pdf"), dict) else {},
                "template_meta": data.get("meta", {}) if isinstance(data.get("meta"), dict) else {},
                "schema": data.get("schema") or "",
            }
        fields = data.get("fields", [])
        content_map = data.get("content", {})
        if not content_map and isinstance(fields, list):
            for item in fields:
                if isinstance(item, dict) and "id" in item and "content" in item:
                    content_map[item["id"]] = item.get("content", "")
        return {
            "fields": fields,
            "content": content_map,
            "bindings": data.get("bindings", {}) if isinstance(data.get("bindings"), dict) else {},
            "source_pdf": data.get("source_pdf", {}) if isinstance(data.get("source_pdf"), dict) else {},
            "template_meta": data.get("meta", {}) if isinstance(data.get("meta"), dict) else {},
            "schema": data.get("schema") or "",
        }
    raise ValueError("JSON structure unsupported")


def build_filled_pdf(fields: List[Dict[str, Any]], source_pdf: Path) -> bytes:
    if not source_pdf.exists():
        raise FileNotFoundError("source pdf not found")
    doc = fitz.open(source_pdf)
    times_font_path = pick_times_font()
    check_font_path = pick_check_font()
    if not SIMSUN_FONT.exists():
        doc.close()
        raise RuntimeError(f"missing font: {SIMSUN_FONT}")
    if not check_font_path:
        doc.close()
        raise RuntimeError("missing checkmark font")

    try:
        for f in fields:
            page = doc[int(f["page"]) - 1]
            field_type = (f.get("fieldType") or "text").lower()
            raw_rect = fitz.Rect(f["x0"], f["y0"], f["x1"], f["y1"])
            if field_type == "text":
                raw_rect = ensure_min_text_rect(raw_rect)
            r = safe_inset_rect(raw_rect, 0.6)

            if field_type == "check":
                checked = bool(f.get("checked")) or str(f.get("value", "")).strip() in (
                    "1",
                    "true",
                    "True",
                    "yes",
                    "on",
                    "✓",
                )
                if checked:
                    page.insert_font(fontname="F_CHECK", fontfile=str(check_font_path))
                    remain = page.insert_textbox(
                        r,
                        "✓",
                        fontname="F_CHECK",
                        fontsize=10.5,
                        color=(1, 0, 0),
                        align=fitz.TEXT_ALIGN_CENTER,
                        overlay=True,
                    )
                    # 某些 PDF 在 textbox 布局下不落字，回退为中心点直写。
                    if remain < 0:
                        cx = (r.x0 + r.x1) / 2.0
                        cy = (r.y0 + r.y1) / 2.0
                        pt = fitz.Point(cx - 3.2, cy + 3.2)
                        page.insert_text(
                            pt,
                            "✓",
                            fontname="F_CHECK",
                            fontsize=10.5,
                            color=(1, 0, 0),
                            overlay=True,
                        )
                continue

            if field_type == "image":
                image_data = (f.get("imageData") or "").strip()
                if not image_data:
                    continue
                if image_data.startswith("data:"):
                    image_data = image_data.split(",", 1)[1]
                image_bytes = base64.b64decode(image_data)
                page.insert_image(r, stream=image_bytes, keep_proportion=True, overlay=True)
                continue

            text = str(f.get("value", "")).strip()
            if not text:
                continue
            use_simsun = contains_cjk(text) or not times_font_path
            font_file = SIMSUN_FONT if use_simsun else times_font_path
            font_name = "F_SIMSUN" if use_simsun else "F_TIMES"
            page.insert_font(fontname=font_name, fontfile=str(font_file))
            font_obj = fitz.Font(fontfile=str(font_file))
            wrapped_text, fs = fit_text_for_box(text, r, font_obj)
            align = fitz.TEXT_ALIGN_LEFT if "\n" in wrapped_text else fitz.TEXT_ALIGN_CENTER
            line_count = max(1, wrapped_text.count("\n") + 1)
            text_h = line_count * fs * 1.2
            if text_h < r.height:
                offset_y = (r.height - text_h) / 2.0
                text_rect = fitz.Rect(r.x0, r.y0 + offset_y, r.x1, r.y1)
            else:
                text_rect = r
            page.insert_textbox(
                text_rect,
                wrapped_text,
                fontname=font_name,
                fontsize=fs,
                color=(1, 0, 0),
                align=align,
                overlay=True,
            )
    finally:
        out = io.BytesIO()
        doc.save(out, clean=True, garbage=3)
        doc.close()
    return out.getvalue()
