"""
评价报告附件页生成。

版式约定：
- 主标题（附件一 / 附件二 …）在表格外框上方，左对齐
- 次级标题（附件 2-1 …）在表格框内、图片上方，左对齐
- 图片置于表格线内；通常一页一张图
- 不画评价表「附件」栏目表头（那只出现在正文 F.1 单元格清单里）
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import fitz

from radiation_detection_report.generator import (
    FontResources,
    _load_font_resources,
    _measure_text,
    _register_fonts,
    _text_ascent,
)

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FORMAT_PATH = os.path.join(PACKAGE_DIR, "base", "attachment_format.json")
MM_TO_PT = 72.0 / 25.4

# 常用页面模式别名（便于数据 JSON 简写）
PAGE_MODE_ALIASES = {
    "default": "a4_portrait",
    "a4": "a4_portrait",
    "a4_rotate": "a4_landscape",
    "page_rotate_90": "a4_landscape",
    "a3": "a3_portrait",
    "a3_enlarge": "a3_portrait",
    "a3_rotate": "a3_landscape",
    "page_a3_rotate_90": "a3_landscape",
}


def load_attachment_format(path: Optional[str] = None) -> Dict[str, Any]:
    with open(path or DEFAULT_FORMAT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def format_attachment_list_text(
    items: Sequence[Dict[str, Any]],
    *,
    fmt: Optional[Dict[str, Any]] = None,
    project_full_name: str = "",
) -> str:
    """生成写入 F.1「附件」单元格的多行文本。"""
    fmt = fmt or load_attachment_format()
    cfg = fmt.get("list_in_f1_table") or {}
    join_with = str(cfg.get("join_with", "\n"))
    template = str(cfg.get("item_template", "{code} {title}"))
    lines: List[str] = []
    for it in items:
        code = str(it.get("code") or it.get("primary_title") or "").strip()
        title = str(it.get("title") or "").strip()
        title = title.replace("{项目全称}", project_full_name)
        if not code and not title:
            continue
        lines.append(
            template.format(code=code, title=title).strip()
            if title
            else code
        )
    return join_with.join(lines)


def default_attachment_titles(
    *,
    project_full_name: str = "",
    fmt: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    fmt = fmt or load_attachment_format()
    out: List[Dict[str, str]] = []
    for row in fmt.get("fixed_attachment_titles") or []:
        title = str(row.get("title", "")).replace("{项目全称}", project_full_name)
        out.append({"code": str(row.get("code", "")), "title": title, "kind": str(row.get("kind", ""))})
    return out


def _margins_pt(fmt: Dict[str, Any]) -> Dict[str, float]:
    page = fmt.get("page") or {}
    mm = page.get("margins_mm") or {"top": 26, "bottom": 26, "left": 28, "right": 28}
    return {
        "top": float(mm["top"]) * MM_TO_PT,
        "bottom": float(mm["bottom"]) * MM_TO_PT,
        "left": float(mm["left"]) * MM_TO_PT,
        "right": float(mm["right"]) * MM_TO_PT,
    }


def _resolve_page_mode(fmt: Dict[str, Any], mode: str) -> Tuple[str, Dict[str, Any]]:
    modes = fmt.get("page_modes") or {}
    key = PAGE_MODE_ALIASES.get(mode, mode)
    if key not in modes:
        key = str(fmt.get("default_page_mode") or "a4_portrait")
    return key, dict(modes[key])


def _resolve_image_path(path: str, search_dirs: Optional[Sequence[str]] = None) -> str:
    if os.path.isabs(path) and os.path.isfile(path):
        return path
    dirs = list(search_dirs or [])
    root = os.path.dirname(PACKAGE_DIR)
    dirs.extend(
        [
            os.path.join(PACKAGE_DIR, "examples"),
            os.path.join(root, "radiation_detection", "examples"),
            os.path.join(root, "radiation_detection_report", "examples"),
            os.getcwd(),
        ]
    )
    for d in dirs:
        cand = path if os.path.isabs(path) else os.path.join(d, path)
        if os.path.isfile(cand):
            return cand
    return path


def _register_song(page: fitz.Page, base: FontResources) -> FontResources:
    res = _register_fonts(page, base)
    res.times = res.song
    res.symbol = res.song
    return res


def _draw_left_text(
    page: fitz.Page,
    fonts: FontResources,
    text: str,
    x: float,
    baseline: float,
    font_size: float,
) -> None:
    if not text:
        return
    page.insert_text(
        (x, baseline),
        text,
        fontname=fonts.song,
        fontsize=font_size,
        color=(0, 0, 0),
    )


def _draw_page_number(
    page: fitz.Page,
    fonts: FontResources,
    page_no: int,
    *,
    page_w: float,
    page_h: float,
    margin_left: float,
    margin_right: float,
    margin_bottom: float,
    font_size: float,
) -> None:
    text = str(page_no)
    w = _measure_text(text, fonts, font_size)
    baseline = page_h - margin_bottom / 2.0 + font_size * 0.35
    if page_no % 2 == 1:
        x = page_w - margin_right - w
    else:
        x = margin_left
    page.insert_text((x, baseline), text, fontname=fonts.song, fontsize=font_size, color=(0, 0, 0))


def _fit_image_rect(
    box: fitz.Rect,
    img_w: float,
    img_h: float,
) -> fitz.Rect:
    if img_w <= 0 or img_h <= 0:
        return box
    bw, bh = box.width, box.height
    scale = min(bw / img_w, bh / img_h)
    tw, th = img_w * scale, img_h * scale
    x0 = box.x0 + (bw - tw) / 2.0
    y0 = box.y0 + (bh - th) / 2.0
    return fitz.Rect(x0, y0, x0 + tw, y0 + th)


def render_attachment_page(
    doc: fitz.Document,
    item: Dict[str, Any],
    *,
    page_index: int,
    fmt: Optional[Dict[str, Any]] = None,
    font_base: Optional[FontResources] = None,
    search_dirs: Optional[Sequence[str]] = None,
) -> fitz.Page:
    """
    向 doc 追加一页附件图。

    item 字段：
      - primary_title: 表外标题，如「附件二」（可空，同组续页可省略）
      - secondary_title: 表内图片上方标题，如「附件 2-1」
      - image: 图片路径
      - page_mode: a4_portrait / a4_landscape / a3_portrait / a3_landscape（及别名）
      - image_rotate_deg: 0/90/180/270（图片旋转）
    """
    fmt = fmt or load_attachment_format()
    page_cfg = fmt.get("page") or {}
    margins = _margins_pt(fmt)
    font_size = float(page_cfg.get("font_size_pt", 12.0))
    outer_gap = float(page_cfg.get("outer_title_gap_pt", 6.0))
    inner_gap = float(page_cfg.get("inner_subtitle_gap_pt", 6.0))
    inner_pad = float(page_cfg.get("inner_pad_pt", 8.0))
    border_w = float(page_cfg.get("border_width_pt", 0.6))

    mode_key, mode = _resolve_page_mode(
        fmt, str(item.get("page_mode") or fmt.get("default_page_mode") or "a4_portrait")
    )
    page_w = float(mode["width_pt"])
    page_h = float(mode["height_pt"])
    rotate = int(item.get("image_rotate_deg", fmt.get("default_image_rotate_deg", 0)) or 0)
    if rotate % 90 != 0:
        rotate = 0
    rotate = rotate % 360

    font_base = font_base or _load_font_resources()
    page = doc.new_page(width=page_w, height=page_h)
    fonts = _register_song(page, font_base)

    ml, mr, mt, mb = margins["left"], margins["right"], margins["top"], margins["bottom"]
    primary = str(item.get("primary_title") or item.get("code") or "").strip()
    secondary = str(item.get("secondary_title") or "").strip()

    # 表外主标题占用高度
    title_block_h = 0.0
    if primary:
        title_block_h = font_size * 1.2 + outer_gap
        baseline = mt + _text_ascent(font_size)
        _draw_left_text(page, fonts, primary, ml, baseline, font_size)

    frame = fitz.Rect(ml, mt + title_block_h, page_w - mr, page_h - mb)
    page.draw_rect(frame, color=(0, 0, 0), width=border_w)

    content_top = frame.y0 + inner_pad
    if secondary:
        baseline = content_top + _text_ascent(font_size)
        _draw_left_text(page, fonts, secondary, frame.x0 + inner_pad, baseline, font_size)
        content_top = baseline + font_size * 0.35 + inner_gap

    img_box = fitz.Rect(
        frame.x0 + inner_pad,
        content_top,
        frame.x1 - inner_pad,
        frame.y1 - inner_pad,
    )

    image_path = _resolve_image_path(str(item.get("image") or ""), search_dirs)
    if image_path and os.path.isfile(image_path):
        # 先读尺寸，再按旋转交换宽高后 fit
        try:
            pix = fitz.Pixmap(image_path)
            iw, ih = float(pix.width), float(pix.height)
            pix = None
        except Exception:
            # 备用：用临时插入再删测量（部分格式）
            iw, ih = img_box.width, img_box.height
        if rotate in (90, 270):
            iw, ih = ih, iw
        dest = _fit_image_rect(img_box, iw, ih)
        page.insert_image(dest, filename=image_path, keep_proportion=True, rotate=rotate)

    if page_cfg.get("draw_page_number", True):
        _draw_page_number(
            page,
            fonts,
            page_index,
            page_w=page_w,
            page_h=page_h,
            margin_left=ml,
            margin_right=mr,
            margin_bottom=mb,
            font_size=font_size,
        )
    return page


def append_attachment_pages(
    doc: fitz.Document,
    pages: Sequence[Dict[str, Any]],
    *,
    start_page_no: int = 1,
    fmt: Optional[Dict[str, Any]] = None,
    search_dirs: Optional[Sequence[str]] = None,
) -> int:
    """按顺序追加附件图页，返回下一页页码。"""
    fmt = fmt or load_attachment_format()
    font_base = _load_font_resources()
    page_no = start_page_no
    for item in pages:
        if not isinstance(item, dict):
            continue
        if not item.get("image"):
            continue
        render_attachment_page(
            doc,
            item,
            page_index=page_no,
            fmt=fmt,
            font_base=font_base,
            search_dirs=search_dirs,
        )
        page_no += 1
    return page_no


def generate_attachments_pdf(
    data: Dict[str, Any],
    output_pdf: str,
    *,
    format_path: Optional[str] = None,
    start_page_no: int = 1,
) -> None:
    """
    仅生成附件图册 PDF。

    data 示例见 examples/attachments_example.json
    """
    fmt = load_attachment_format(format_path)
    pages = data.get("attachment_pages") or data.get("pages") or []
    doc = fitz.open()
    try:
        if not pages:
            raise ValueError("attachment_pages 为空")
        append_attachment_pages(doc, pages, start_page_no=start_page_no, fmt=fmt)
        from f1_eval_report.fonts_util import save_pdf_compressed

        save_pdf_compressed(doc, output_pdf)
    finally:
        doc.close()
