"""HTML-PDF 编辑器服务：模板导入/导出与 PDF 生成。"""
from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

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
        return {"fields": data, "content": {}}
    if isinstance(data, dict):
        if "fields" not in data and "content" not in data:
            return {"fields": [], "content": data}
        fields = data.get("fields", [])
        content_map = data.get("content", {})
        if not content_map and isinstance(fields, list):
            for item in fields:
                if isinstance(item, dict) and "id" in item and "content" in item:
                    content_map[item["id"]] = item.get("content", "")
        return {"fields": fields, "content": content_map}
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
