"""
A3 横置附图页：表题 + 表格外框 + 侧栏表头（防护设施和措施 / 放射防护分区）+ 图。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import fitz

from radiation_detection_report.generator import (
    CELL_PAD_X,
    _paragraph_line_entries,
    _register_fonts,
    _text_ascent,
)
from radiation_detection_report.gbzt_f1_content_embed import F1ContentEmbedBuilder

PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_A3_LANDSCAPE_W = 1190.55
DEFAULT_A3_LANDSCAPE_H = 841.9
EMBED_CELL_PAD = CELL_PAD_X
# 图题/表题换行：用字号相关紧凑行距，不用正文 25 磅
CAPTION_LINE_FACTOR = 1.25


def _f1_text_descent(font_size: float) -> float:
    return font_size * 0.22


def _f1_text_block_height(n_lines: int, font_size: float, line_spacing: float) -> float:
    if n_lines <= 0:
        return 0.0
    return (n_lines - 1) * line_spacing + _text_ascent(font_size) + _f1_text_descent(font_size)


def _caption_line_spacing(font_size: float) -> float:
    return max(10.0, float(font_size) * CAPTION_LINE_FACTOR)


def _measure_caption_height(host: Any, caption: str, max_width: float) -> float:
    """按紧凑行距测算图题高度（可多行），保证不超出表格框。"""
    from radiation_detection_report.f1_paragraph_layout import wrap_plain_lines

    text = str(caption or "").strip()
    if not text:
        return 0.0
    fs = float(host.font_size)
    ls = _caption_line_spacing(fs)
    entries = wrap_plain_lines(text, max(8.0, max_width), host.fonts, fs)
    n = max(1, len(entries))
    return _f1_text_block_height(n, fs, ls) + 2.0


def _draw_caption_in_rect(host: Any, rect: fitz.Rect, caption: str) -> None:
    """图题水平居中、贴顶绘制；行距紧凑，禁止用正文 25pt。"""
    host._f1_draw_content_in_rect(
        rect,
        caption,
        align="center",
        vertical_center=False,
        line_spacing=_caption_line_spacing(float(host.font_size)),
    )


def _trim_image_content_box(path: str, *, margin: int = 2) -> Optional[fitz.Rect]:
    """
    探测近白边空白，返回内容区相对原图像素的 clip（IRect 坐标系）。
    失败则返回 None（使用整图）。
    """
    try:
        pix = fitz.Pixmap(path)
        if pix.alpha:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        w, h = pix.width, pix.height
        if w < 8 or h < 8 or not pix.samples:
            return None
        n = pix.n  # 通常 3
        thr = 248
        samples = pix.samples
        # 抽样扫描找非白边界（步进加速）
        step = max(1, min(w, h) // 400)

        def _row_has_ink(y: int) -> bool:
            row = y * w * n
            for x in range(0, w, step):
                i = row + x * n
                if samples[i] < thr or samples[i + 1] < thr or samples[i + 2] < thr:
                    return True
            return False

        def _col_has_ink(x: int) -> bool:
            for y in range(0, h, step):
                i = (y * w + x) * n
                if samples[i] < thr or samples[i + 1] < thr or samples[i + 2] < thr:
                    return True
            return False

        top = 0
        while top < h and not _row_has_ink(top):
            top += step
        bottom = h - 1
        while bottom > top and not _row_has_ink(bottom):
            bottom -= step
        left = 0
        while left < w and not _col_has_ink(left):
            left += step
        right = w - 1
        while right > left and not _col_has_ink(right):
            right -= step
        # 空白过大才裁（内容至少占 55% 面积），避免误裁
        cw, ch = right - left + 1, bottom - top + 1
        if cw < w * 0.55 or ch < h * 0.55:
            return None
        left = max(0, left - margin)
        top = max(0, top - margin)
        right = min(w - 1, right + margin)
        bottom = min(h - 1, bottom + margin)
        return fitz.Rect(left, top, right + 1, bottom + 1)
    except Exception:
        return None


def _fitted_image_size(
    path: str,
    max_w: float,
    max_h: float,
    *,
    trim_whitespace: bool = False,
) -> tuple[float, float]:
    """按比例落入 max_w×max_h，返回显示宽高（不绘制）。"""
    if not os.path.isfile(path) or max_w <= 1 or max_h <= 1:
        return max(1.0, max_w), max(1.0, max_h)
    clip = _trim_image_content_box(path) if trim_whitespace else None
    try:
        pix = fitz.Pixmap(path)
        src = pix
        if clip is not None:
            ir = fitz.IRect(int(clip.x0), int(clip.y0), int(clip.x1), int(clip.y1))
            try:
                src = fitz.Pixmap(pix, ir)
            except Exception:
                src = pix
        iw, ih = float(src.width), float(src.height)
        if iw <= 1 or ih <= 1:
            return max_w, max_h
        scale = min(max_w / iw, max_h / ih)
        return iw * scale, ih * scale
    except Exception:
        return max_w, max_h


def _fit_image_in_box(
    page: fitz.Page,
    path: str,
    box: fitz.Rect,
    *,
    trim_whitespace: bool = False,
    valign: str = "center",
) -> fitz.Rect:
    """将图片按宽或高最大化落入 box；valign=top|center|bottom。"""
    if not os.path.isfile(path) or box.width <= 1 or box.height <= 1:
        return fitz.Rect(box)
    clip = _trim_image_content_box(path) if trim_whitespace else None
    try:
        pix = fitz.Pixmap(path)
        src = pix
        if clip is not None:
            ir = fitz.IRect(int(clip.x0), int(clip.y0), int(clip.x1), int(clip.y1))
            try:
                src = fitz.Pixmap(pix, ir)
            except Exception:
                src = pix
        iw, ih = float(src.width), float(src.height)
        if iw <= 1 or ih <= 1:
            page.insert_image(box, filename=path, keep_proportion=True)
            return fitz.Rect(box)
        scale = min(box.width / iw, box.height / ih)
        nw, nh = iw * scale, ih * scale
        x0 = box.x0 + (box.width - nw) / 2.0
        if valign == "top":
            y0 = box.y0
        elif valign == "bottom":
            y0 = box.y1 - nh
        else:
            y0 = box.y0 + (box.height - nh) / 2.0
        rect = fitz.Rect(x0, y0, x0 + nw, y0 + nh)
        page.insert_image(rect, pixmap=src, keep_proportion=True)
        return rect
    except Exception:
        page.insert_image(box, filename=path, keep_proportion=True)
        return fitz.Rect(box)



def _f1_register_fonts(page: fitz.Page, base: Any) -> Any:
    res = _register_fonts(page, base)
    res.times = res.song
    res.symbol = res.song
    if res.song_metric:
        res.times_metric = res.song_metric
        res.symbol_metric = res.song_metric
    return res


def _resolve_image_path(image_rel: str) -> str:
    if os.path.isabs(image_rel) and os.path.isfile(image_rel):
        return image_rel
    candidates = [
        os.path.join(PACKAGE_DIR, "examples", image_rel),
        os.path.join(
            os.path.dirname(PACKAGE_DIR),
            "radiation_detection",
            "examples",
            image_rel,
        ),
        image_rel,
        os.path.join(os.getcwd(), image_rel),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return os.path.join(PACKAGE_DIR, "examples", image_rel)


def _landscape_cfg(host: Any) -> Dict[str, Any]:
    return dict(host.layout.get("landscape_figure_page") or {})


def _half_sub_label_format(
    row_def: Optional[Dict[str, Any]] = None,
    embed: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """半宽子表头格式：与栏目内「屏蔽设施」等一致，默认两字折行居中。"""
    for src in (embed, row_def):
        if isinstance(src, dict) and isinstance(src.get("sub_label_format"), dict):
            return dict(src.get("sub_label_format") or {})
    return {"mode": "center", "wrap_chars": 2}


def insert_landscape_figure_page(
    host: Any,
    figure: Dict[str, Any],
    *,
    section_label: str = "",
    section_label_lines: Optional[list] = None,
    row_label: str = "",
    label_col: str = "label_main_half",
    sub_label_col: str = "sub_label_row_half",
    sub_value_col: str = "value_wide_half",
    sub_label_format: Optional[Dict[str, Any]] = None,
) -> None:
    """
    插入 A3 横置附图页。

    - 页边距与 A4 一致
    - 绘制表 F.1 表题（可关）
    - 绘制表格外框
    - 可选左侧表头：section_label（防护设施和措施）+ row_label（放射防护分区）
    - 页脚页码按当前页实际宽高定位
    """
    cfg = _landscape_cfg(host)
    page_w = float(figure.get("page_width_pt", cfg.get("default_width_pt", DEFAULT_A3_LANDSCAPE_W)))
    page_h = float(figure.get("page_height_pt", cfg.get("default_height_pt", DEFAULT_A3_LANDSCAPE_H)))
    ml = float(host.margin_left)
    mr = float(host.margin_right)
    mt = float(host.margin_top)
    mb = float(host.margin_bottom)
    inner_pad = float(figure.get("inner_pad_pt", cfg.get("inner_pad_pt", 8.0)))
    caption_gap = float(figure.get("caption_gap_pt", 10.0))
    draw_title = bool(cfg.get("draw_title", True)) and bool(
        (host.layout.get("title") or {}).get("draw_on_landscape", True)
    )
    if "draw_title" in figure:
        draw_title = bool(figure.get("draw_title"))
    draw_frame = bool(cfg.get("draw_table_frame", True))
    if "draw_table_frame" in figure:
        draw_frame = bool(figure.get("draw_table_frame"))
    draw_side = bool(cfg.get("draw_side_labels", True))
    if "draw_side_labels" in figure:
        draw_side = bool(figure.get("draw_side_labels"))
    # 无侧栏文字时不画侧栏
    if not (section_label or row_label):
        draw_side = False

    if host.page is not None:
        host._finish_page_band()
        host._draw_page_number(host.page_index)

    host.page_band_top = None
    host.page_band_bottom = None
    host.page_index += 1
    page = host.doc.new_page(width=page_w, height=page_h)
    host.page = page
    host.fonts = _f1_register_fonts(page, host.font_base)

    # A4 表宽（表题与侧栏表头保持与 A4 相同绝对列宽，不随 A3 拉宽）
    a4_left = float(host.table_x_left)
    a4_right = float(host.table_x_right)

    # 表题仅首页出现一次；A3 续页不画表题（页脚页码仍由 _new_page 绘制）
    title_bottom = mt
    if draw_title:
        title = host.layout.get("title") or {}
        label_text = str(title.get("table_label") or host.layout.get("table_label", "表F.1"))
        name_text = str(title.get("table_name") or host.layout.get("table_title", ""))
        title_text = f"{label_text}  {name_text}".strip()
        title_h = host.line_spacing
        title_rect = fitz.Rect(a4_left, mt, a4_right, mt + title_h)
        host._f1_draw_content_in_rect(
            title_rect, title_text, align="center", vertical_center=True
        )
        gap = float(title.get("gap_below_pt", 6.0))
        title_bottom = mt + title_h + gap

    # 表格外框：高度按 A3，宽度铺满横页可用区（图区可加宽）
    frame = fitz.Rect(ml, title_bottom, page_w - mr, page_h - mb)
    if draw_frame:
        page.draw_rect(frame, color=(0, 0, 0), width=0.6)

    # 侧栏表头：直接使用 A4 列绝对坐标（pt 宽度不变），仅值区向右扩展到 A3 右缘
    content_left = frame.x0
    if draw_side and (section_label or row_label):
        cols = host.columns
        sec_col = cols.get(label_col) or cols.get("label_main_half") or cols["label_main"]
        sec_x0 = float(sec_col["x0"])
        sec_x1 = float(sec_col["x1"])
        row_col = (
            cols.get(sub_label_col)
            or cols.get("sub_label_row_half")
            or cols.get("sub_label_row")
            or sec_col
        )
        row_x0 = float(row_col["x0"])
        row_x1 = float(row_col["x1"])
        val_col = (
            cols.get(sub_value_col)
            or cols.get("value_wide_half")
            or cols.get("value_wide")
            or cols.get("value_full_half")
            or cols["value_full"]
        )
        val_x0 = float(val_col["x0"])

        # 左：防护设施和措施 / 工作场所布局 — 统一两字折行（与栏目 label_format 一致）
        if section_label:
            sec_rect = fitz.Rect(sec_x0, frame.y0, sec_x1, frame.y1)
            page.draw_rect(sec_rect, color=(0, 0, 0), width=0.6)
            host._f1_draw_label_formatted(
                sec_rect,
                section_label,
                {"mode": "center", "wrap_chars": 2},
            )
        # 中：放射防护分区（半宽子表头：两字折行居中）
        if row_label:
            row_rect = fitz.Rect(row_x0, frame.y0, row_x1, frame.y1)
            page.draw_rect(row_rect, color=(0, 0, 0), width=0.6)
            host._f1_draw_label_formatted(
                row_rect,
                row_label,
                sub_label_format or {"mode": "center", "wrap_chars": 2},
            )
            content_left = val_x0
        else:
            content_left = sec_x1 if section_label else frame.x0

        # 值区左竖线；右缘由 A3 外框承担
        page.draw_line(
            (content_left, frame.y0),
            (content_left, frame.y1),
            color=(0, 0, 0),
            width=0.6,
        )

    caption = str(figure.get("caption", "")).strip()
    # 尽量铺满值区：内边距压到最小；图题钉在单元格底，图片在剩余区域按比例最大化
    inner_pad = min(float(inner_pad), 0.8)
    caption_gap = 1.0
    inner_w = max(8.0, frame.x1 - content_left - 2 * inner_pad)
    caption_h = _measure_caption_height(host, caption, inner_w) if caption else 0.0

    cap_bottom = frame.y1 - inner_pad
    cap_top = cap_bottom - caption_h if caption else cap_bottom
    img_top = frame.y0 + inner_pad
    img_bottom = max(img_top + 40.0, cap_top - (caption_gap if caption else 0.0))
    box = fitz.Rect(
        content_left + inner_pad,
        img_top,
        frame.x1 - inner_pad,
        img_bottom,
    )

    image_path = _resolve_image_path(str(figure["image"]))
    if os.path.isfile(image_path):
        # 在预留框内按比例最大化（居中放置，尺寸已是最大）
        _fit_image_in_box(
            page, image_path, box, trim_whitespace=True, valign="center"
        )

    if caption:
        cap_rect = fitz.Rect(
            content_left + inner_pad,
            cap_top,
            frame.x1 - inner_pad,
            cap_bottom,
        )
        _draw_caption_in_rect(host, cap_rect, caption)

    # 不在此处再 _new_page：否则会留下一张空白 A4。
    # 不设置 page_band：外框已自绘，避免 _finish_page_band 按 A4 宽度补竖线。
    host.page_band_top = None
    host.page_band_bottom = None
    host.y = float(page_h)  # 促使后续流式排版换页
    host._figure_page_pending = True


def insert_stacked_a4_figures_page(
    host: Any,
    figures: list,
    *,
    section_label: str = "",
    section_label_lines: Optional[list] = None,
    row_label: str = "",
    label_col: str = "label_main_half",
    sub_label_col: str = "sub_label_row_half",
    sub_value_col: str = "value_wide_half",
    sub_label_format: Optional[Dict[str, Any]] = None,
) -> None:
    """
    样式图合页（警告标志 + 灯箱 / 注意事项告知栏）：
    - 左列上下排前两张，右列一张，图题底对齐；
    - 仅用于 style_figure_id 样式图，平面布局等独立附图勿走此路径。
    """
    figs = [f for f in (figures or []) if isinstance(f, dict) and f.get("image")]
    if not figs:
        return
    if len(figs) == 1:
        insert_landscape_figure_page(
            host,
            figs[0],
            section_label=section_label,
            section_label_lines=section_label_lines,
            row_label=row_label,
            label_col=label_col,
            sub_label_col=sub_label_col,
            sub_value_col=sub_value_col,
            sub_label_format=sub_label_format,
        )
        return

    ps = host.layout.get("page_size") or {}
    page_w = float(ps.get("width") or 595.3)
    page_h = float(ps.get("height") or 841.9)
    ml = float(host.margin_left)
    mr = float(host.margin_right)
    mt = float(host.margin_top)
    mb = float(host.margin_bottom)
    inner_pad = 3.0
    col_gap = 8.0
    # stack_gap 在下方紧凑布局中另行设定

    # 估算所需高度：约半页以上；不足则换页
    need_h = min(420.0, page_h - mt - mb - 40.0)
    on_a4 = (
        host.page is not None
        and abs(float(host.page.rect.width) - page_w) <= 1.0
        and abs(float(host.page.rect.height) - page_h) <= 1.0
    )
    remain = float(host.content_bottom - host.y) if on_a4 else -1.0
    use_current = on_a4 and remain >= need_h * 0.55 and remain >= 220.0

    if use_current:
        host._finish_page_band()
        page = host.page
        assert page is not None
        y0 = float(host.y)
        y1 = float(host.content_bottom)
        host.fonts = _f1_register_fonts(page, host.font_base)
    else:
        if host.page is not None:
            host._finish_page_band()
            host._draw_page_number(host.page_index)
        host.page_band_top = None
        host.page_band_bottom = None
        host.page_index += 1
        page = host.doc.new_page(width=page_w, height=page_h)
        host.page = page
        host.fonts = _f1_register_fonts(page, host.font_base)
        y0 = mt
        y1 = page_h - mb
        host._figure_page_pending = True

    frame = fitz.Rect(ml, y0, page_w - mr, y1)
    page.draw_rect(frame, color=(0, 0, 0), width=0.6)

    content_left = frame.x0
    draw_side = bool(section_label or row_label)
    if draw_side:
        cols = host.columns
        sec_col = cols.get(label_col) or cols.get("label_main_half") or cols["label_main"]
        sec_x0 = float(sec_col["x0"])
        sec_x1 = float(sec_col["x1"])
        if section_label:
            sec_rect = fitz.Rect(sec_x0, frame.y0, sec_x1, frame.y1)
            page.draw_rect(sec_rect, color=(0, 0, 0), width=0.6)
            host._f1_draw_label_formatted(
                sec_rect,
                section_label,
                {"mode": "center", "wrap_chars": 2},
            )
            content_left = sec_x1
        if row_label:
            row_col = (
                cols.get(sub_label_col)
                or cols.get("sub_label_row_half")
                or cols.get("sub_label_row")
                or sec_col
            )
            row_x0 = float(row_col["x0"])
            row_x1 = float(row_col["x1"])
            row_rect = fitz.Rect(row_x0, frame.y0, row_x1, frame.y1)
            page.draw_rect(row_rect, color=(0, 0, 0), width=0.6)
            host._f1_draw_label_formatted(
                row_rect,
                row_label,
                sub_label_format or {"mode": "center", "wrap_chars": 2},
            )
            val_col = (
                cols.get(sub_value_col)
                or cols.get("value_wide_half")
                or cols.get("value_wide")
                or cols["value_full"]
            )
            content_left = float(val_col["x0"])
        page.draw_line(
            (content_left, frame.y0),
            (content_left, frame.y1),
            color=(0, 0, 0),
            width=0.6,
        )

    # 左列：前 n-1 张自顶向下紧凑叠放；右列图题与左列最下图题齐平（右图上方可留白，单元格底部留空）
    left_figs = figs[:-1]
    right_fig = figs[-1]
    avail_w = frame.x1 - content_left - 2 * inner_pad
    left_w = (avail_w - col_gap) * 0.48
    right_w = avail_w - col_gap - left_w
    left_x0 = content_left + inner_pad
    right_x0 = left_x0 + left_w + col_gap
    area_top = frame.y0 + inner_pad
    area_bot = frame.y1 - inner_pad
    area_h = max(40.0, area_bot - area_top)
    caption_gap = 1.0
    stack_gap = 4.0

    def _cap_h(fig: dict, width: float, max_frac: float) -> float:
        caption = str(fig.get("caption") or "").strip()
        if not caption:
            return 0.0
        h = _measure_caption_height(host, caption, width)
        return min(h, max(12.0, area_h * max_frac))

    # 测算左列各图自然高度，过高则整体压缩
    left_meta: list = []
    for fig in left_figs:
        cap_h = _cap_h(fig, left_w, 0.22)
        path = _resolve_image_path(str(fig["image"]))
        _nw, nh = _fitted_image_size(path, left_w, area_h, trim_whitespace=True)
        left_meta.append({"fig": fig, "path": path, "cap_h": cap_h, "img_h": nh})

    n_left = max(1, len(left_meta))
    gaps_h = stack_gap * (n_left - 1)
    caps_h = sum(m["cap_h"] + (caption_gap if m["cap_h"] else 0.0) for m in left_meta)
    imgs_h = sum(m["img_h"] for m in left_meta)
    need = imgs_h + caps_h + gaps_h
    if need > area_h + 0.5 and imgs_h > 1:
        scale = max(0.35, (area_h - caps_h - gaps_h) / imgs_h)
        for m in left_meta:
            m["img_h"] *= scale

    # 自上而下放置：顶对齐，底部空白即可；记录最下图题底边供右列对齐
    y_cursor = area_top
    left_cap_bottom = area_top
    for m in left_meta:
        cap_h = float(m["cap_h"])
        img_h = float(m["img_h"])
        img_top = y_cursor
        img_bot = img_top + img_h
        if img_bot > area_bot:
            img_h = max(8.0, area_bot - img_top)
            img_bot = img_top + img_h
        img_box = fitz.Rect(left_x0, img_top, left_x0 + left_w, img_bot)
        _fit_image_in_box(
            page, m["path"], img_box, trim_whitespace=True, valign="top"
        )
        cap_top = img_bot + (caption_gap if cap_h else 0.0)
        cap_bot = cap_top + cap_h if cap_h else cap_top
        if cap_bot > area_bot and cap_h > 0:
            cap_bot = area_bot
            cap_top = max(img_bot, cap_bot - cap_h)
        caption = str(m["fig"].get("caption") or "").strip()
        if caption and cap_h > 0:
            _draw_caption_in_rect(
                host,
                fitz.Rect(left_x0, cap_top, left_x0 + left_w, cap_bot),
                caption,
            )
        left_cap_bottom = cap_bot if cap_h else img_bot
        y_cursor = left_cap_bottom + stack_gap

    # 右列：图题底与左列最下图题齐平；图贴图题，上方留白
    caption_r = str(right_fig.get("caption") or "").strip()
    cap_h_r = _cap_h(right_fig, right_w, 0.18)
    cap_bot_r = min(left_cap_bottom, area_bot)
    cap_top_r = cap_bot_r - cap_h_r if cap_h_r else cap_bot_r
    if cap_top_r < area_top:
        cap_top_r = area_top
        cap_bot_r = min(area_bot, cap_top_r + cap_h_r)
    right_img_bot = max(area_top + 20.0, cap_top_r - (caption_gap if cap_h_r else 0.0))
    right_box = fitz.Rect(right_x0, area_top, right_x0 + right_w, right_img_bot)
    _fit_image_in_box(
        page,
        _resolve_image_path(str(right_fig["image"])),
        right_box,
        trim_whitespace=True,
        valign="bottom",
    )
    if caption_r and cap_h_r > 0:
        _draw_caption_in_rect(
            host,
            fitz.Rect(right_x0, cap_top_r, right_x0 + right_w, cap_bot_r),
            caption_r,
        )

    host.page_band_top = None
    host.page_band_bottom = None
    if use_current:
        host.y = float(frame.y1)
        host._mark_band(float(frame.y0), float(frame.y1))
        host._figure_page_pending = False
    else:
        host.y = float(page_h)
        host._figure_page_pending = True


def embed_section_with_landscape_figure(
    host: Any, row_def: Dict[str, Any], embed: Dict[str, Any]
) -> None:
    """正文（+可选嵌套表）后插入带表头的 A3 附图页。"""
    merged = dict(embed)
    # 先按 content_with_tables 流式排版正文/表
    text_embed = {
        "kind": "content_with_tables",
        "text_key": embed.get("text_key", ""),
        "tables_key": embed.get("tables_key"),
    }
    # 临时关闭 figure，避免 content builder 提前插图
    builder = F1ContentEmbedBuilder(host, row_def, text_embed)
    # 用 section 子列宽：若配置了 sub_value_col，把 value 画进宽值列
    sub_value = row_def.get("sub_value_col")
    sub_label = row_def.get("sub_label")
    sub_label_col = row_def.get("sub_label_col", "sub_label_row")
    if sub_value:
        # 包装：左侧画 section 大标签，中侧子标签，右侧流式内容
        _embed_section_row_with_content(host, row_def, builder, sub_label, sub_label_col, sub_value)
    else:
        builder.run()

    figure_key = embed.get("figure_key")
    figure = host.raw_data.get(figure_key) if figure_key else None
    sub_fmt = _half_sub_label_format(row_def, embed)
    if isinstance(figure, dict) and figure.get("image"):
        insert_landscape_figure_page(
            host,
            figure,
            section_label=str(embed.get("section_label") or row_def.get("label") or ""),
            section_label_lines=embed.get("section_label_lines"),
            row_label=str(embed.get("row_label") or sub_label or ""),
            label_col=str(
                embed.get("label_col") or row_def.get("label_col") or "label_main_half"
            ),
            sub_label_col=str(
                embed.get("sub_label_col") or sub_label_col or "sub_label_row_half"
            ),
            sub_value_col=str(
                embed.get("sub_value_col")
                or row_def.get("sub_value_col")
                or "value_wide_half"
            ),
            sub_label_format=sub_fmt,
        )


def _embed_section_row_with_content(
    host: Any,
    row_def: Dict[str, Any],
    content_builder: F1ContentEmbedBuilder,
    sub_label: str,
    sub_label_col: str,
    sub_value_col: str,
) -> None:
    """在「防护设施和措施 | 放射防护分区 | 正文」三列结构中排版正文。"""
    # 改写 row_def 使 content 画在 value_wide
    local_row = dict(row_def)
    local_row["value_col"] = sub_value_col
    local_row["label_col"] = row_def.get("label_col", "label_main")
    content_builder.row_def = local_row
    content_builder.band_top = host.y
    content_builder.y = host.y
    content_builder.page = host.page
    sub_fmt = _half_sub_label_format(row_def)

    tables = []
    key = content_builder.embed.get("tables_key")
    if key:
        raw = host.raw_data.get(key)
        if isinstance(raw, list):
            tables = [t for t in raw if isinstance(t, dict) and t.get("rows")]
    has_text = bool(content_builder.text.strip())
    if not has_text and not tables:
        # 空行占位，仍保留三列表头
        h = max(
            host._label_text_height(
                str(row_def.get("label", "")),
                local_row["label_col"],
                row_def.get("label_format"),
            ),
            host._label_text_height(sub_label, sub_label_col, sub_fmt),
            host.row_min_h,
        )
        host._ensure_space(h)
        y0 = host.y
        host._draw_cell(
            local_row["label_col"],
            y0,
            y0 + h,
            str(row_def.get("label", "")),
            is_label=True,
            label_format=row_def.get("label_format"),
        )
        host._draw_cell(
            sub_label_col, y0, y0 + h, sub_label, is_label=True, label_format=sub_fmt
        )
        host._draw_cell(sub_value_col, y0, y0 + h, "", is_label=False)
        host._advance(h)
        return

    # 有内容：先回到 A4 流式页，避免沿用附图页的 y=page_h 先画空框再二次换页
    host._ensure_space(host.row_min_h)
    content_builder.page = host.page
    content_builder.band_top = host.y
    from radiation_detection_report.gbzt_f1_content_embed import CONTENT_TOP_PAD_PT

    content_builder.y = host.y + CONTENT_TOP_PAD_PT
    host.y = content_builder.y
    content_builder._sync_f1_table_frame(draw_top=True)

    orig_finish = content_builder._finish_band

    def finish_with_sub_label() -> None:
        bottom = content_builder._segment_bottom()
        if content_builder.band_top >= bottom:
            return
        content_builder._sync_f1_table_frame(draw_bottom=True)
        # 左侧大标签
        host._draw_cell(
            local_row["label_col"],
            content_builder.band_top,
            bottom,
            str(row_def.get("label", "")),
            is_label=True,
            label_format=row_def.get("label_format"),
        )
        # 中侧子标签（与「屏蔽设施」等半宽子表头一致：两字折行）
        host._draw_cell(
            sub_label_col,
            content_builder.band_top,
            bottom,
            sub_label,
            is_label=True,
            label_format=sub_fmt,
        )
        host._mark_band(content_builder.band_top, bottom)

    content_builder._finish_band = finish_with_sub_label  # type: ignore[method-assign]
    # 必须走流式计划，否则正文里的 <<<F1_TABLE>>> 会原样印出
    tables = _resolve_tables_for_section(content_builder)
    plan = getattr(content_builder, "_flow_plan", None) or []
    if any(s.get("kind") in ("table", "tables") for s in plan):
        content_builder._run_flow_plan(tables)
    else:
        content_builder._flow_text()
        content_builder._draw_tables(tables)
        after = getattr(content_builder, "text_after", "") or ""
        if str(after).strip():
            content_builder._indent_part = "after"
            content_builder.text = str(after)
            content_builder._flow_text()
    content_builder._finish_band()
    host.y = content_builder._segment_bottom()
    content_builder._finish_band = orig_finish  # type: ignore[method-assign]


def _resolve_tables_for_section(content_builder: Any) -> list:
    from radiation_detection_report.gbzt_f1_content_embed import _resolve_tables

    return _resolve_tables(content_builder.host, content_builder.embed)
