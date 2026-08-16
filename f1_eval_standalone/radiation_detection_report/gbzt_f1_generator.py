"""
根据 gbzt_f1_layout.json（v2）生成表 F.1 PDF。
固定列宽 + 逻辑行定义；宋体小四、行距 25 磅；页边距内流式排版、自动跨页。
"""

from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import fitz

from radiation_detection_report.f1_paragraph_layout import (
    draw_f1_paragraph_entries,
    f1_paragraph_line_entries,
    wrap_plain_lines,
)
from radiation_detection_report.generator import (
    CELL_PAD_X,
    CELL_PAD_Y,
    FontResources,
    _draw_cell_border,
    _draw_line,
    _inner_rect,
    _line_leading,
    _load_font_resources,
    _measure_text,
    _register_fonts,
    _text_ascent,
)
from radiation_detection_report.gbzt_f1_content_embed import embed_content_with_tables
from radiation_detection_report.gbzt_f1_field_record_embed import embed_field_record_table
from radiation_detection_report.gbzt_f1_generic_table_embed import embed_generic_table
from radiation_detection_report.gbzt_f1_workplace_layout_embed import embed_workplace_layout
from radiation_detection_report.gbzt_f1_rows import (
    F1_FONT_SIZE_PT,
    F1_LINE_SPACING_PT,
    F1_MARGINS_MM,
    F1_TITLE_GAP_PT,
    MM_TO_PT,
    scale_f1_table_to_margins,
)

# 正文距上表格线（与 content embed 的 CONTENT_TOP_PAD_PT 一致）
BODY_TEXT_TOP_PAD_PT = 10.0

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_F1_LAYOUT_PATH = os.path.join(PACKAGE_DIR, "layout", "gbzt_f1_layout.json")


def _f1_register_fonts(page: fitz.Page, base: FontResources) -> FontResources:
    """F.1 正文/表头均用普通宋体；保留 symbol 以便渲染 ☑ 等勾选符号。"""
    res = _register_fonts(page, base)
    res.times = res.song
    res.song_bold = res.song
    res.times_bold = res.song
    if res.song_metric:
        res.times_metric = res.song_metric
        res.song_bold_metric = res.song_metric
        res.times_bold_metric = res.song_metric
    # 勿将 symbol 回退为 song：SimSun 无 U+2611(☑) 等字形
    return res


def _f1_text_descent(font_size: float) -> float:
    return font_size * 0.22


def _f1_text_block_height(n_lines: int, font_size: float, line_spacing: float) -> float:
    if n_lines <= 0:
        return 0.0
    return (n_lines - 1) * line_spacing + _text_ascent(font_size) + _f1_text_descent(font_size)


def _f1_first_baseline_y(
    inner: fitz.Rect,
    n_lines: int,
    font_size: float,
    line_spacing: float,
) -> float:
    block_h = _f1_text_block_height(n_lines, font_size, line_spacing)
    return inner.y0 + max(0.0, (inner.height - block_h) / 2.0) + _text_ascent(font_size)


def _f1_autofit_row_height(
    text: str,
    inner_width: float,
    fonts: FontResources,
    font_size: float,
    line_spacing: float,
    min_height: float,
    *,
    plain: bool = False,
) -> float:
    if not str(text).strip():
        return max(min_height, line_spacing + 2 * CELL_PAD_Y)
    if plain:
        entries = wrap_plain_lines(str(text), max(8.0, inner_width), fonts, font_size)
    else:
        entries = f1_paragraph_line_entries(str(text), max(8.0, inner_width), fonts, font_size)
    block_h = _f1_text_block_height(len(entries), font_size, line_spacing) + 2 * CELL_PAD_Y
    return max(min_height, block_h)


def load_f1_layout(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("schema", "").endswith("/v1"):
        raise ValueError(
            "检测到 v1 版式，请重新提取或改用 layout/gbzt_f1_layout.json（v2）。"
            "运行: python -m radiation_detection_report.extract_gbzt_f1 --layout-only"
        )
    return data


def load_f1_data(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _f1_field_values(raw: Dict[str, Any]) -> Dict[str, Any]:
    if "fields" in raw and isinstance(raw["fields"], dict):
        return raw["fields"]
    return {k: v for k, v in raw.items() if not k.startswith("_") and k != "schema"}


def _stringify_f1_values(raw: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in raw.items():
        if v is None:
            out[k] = ""
        elif isinstance(v, (dict, list)):
            out[k] = ""
        else:
            out[k] = str(v)
    return out


class GbztF1TableBuilder:
    def __init__(self, layout: Dict[str, Any], data: Dict[str, Any]) -> None:
        self.layout = layout
        self.raw_data: Dict[str, Any] = dict(data)
        field_values = _f1_field_values(data)
        self.values = _stringify_f1_values(field_values)
        self.doc = fitz.open()
        self.font_base = _load_font_resources()
        self.fonts: FontResources = self.font_base
        self.page: Optional[fitz.Page] = None
        self.y: float = 0.0
        self.page_band_top: Optional[float] = None
        self.page_band_bottom: Optional[float] = None
        self.page_index = 0
        self._figure_page_pending = False

        ps = layout.get("page_size", {})
        page_w = float(ps.get("width", 595.3))
        page_h = float(ps.get("height", 841.9))

        text_cfg = layout.get("text", {})
        self.font_size = float(text_cfg.get("font_size_pt", F1_FONT_SIZE_PT))
        self.line_spacing = float(text_cfg.get("line_spacing_pt", F1_LINE_SPACING_PT))

        margins_mm = layout.get("margins_mm", F1_MARGINS_MM)
        margins_pt = layout.get("margins_pt")
        if margins_pt:
            self.margin_top = float(margins_pt.get("top", margins_mm.get("top", 26) * MM_TO_PT))
            self.margin_bottom = float(margins_pt.get("bottom", margins_mm.get("bottom", 26) * MM_TO_PT))
            self.margin_left = float(margins_pt.get("left", margins_mm.get("left", 28) * MM_TO_PT))
            self.margin_right = float(margins_pt.get("right", margins_mm.get("right", 28) * MM_TO_PT))
        else:
            self.margin_top = float(margins_mm.get("top", 26)) * MM_TO_PT
            self.margin_bottom = float(margins_mm.get("bottom", 26)) * MM_TO_PT
            self.margin_left = float(margins_mm.get("left", 28)) * MM_TO_PT
            self.margin_right = float(margins_mm.get("right", 28)) * MM_TO_PT

        table = layout.get("table", {})
        if table.get("x_left") and table.get("x_right"):
            self.table_x_left = float(table["x_left"])
            self.table_x_right = float(table["x_right"])
            self.columns = dict(table.get("columns", {}))
        else:
            tl, tr, cols, _ = scale_f1_table_to_margins(page_w, page_h)
            self.table_x_left = tl
            self.table_x_right = tr
            self.columns = cols

        self.page_width = page_w
        self.page_height = page_h
        self.content_top = self.margin_top
        self.content_bottom = page_h - self.margin_bottom
        self.row_min_h = float(table.get("row_min_height", self.line_spacing))

        title_cfg = layout.get("title", {})
        self.title_gap = float(title_cfg.get("gap_below_pt", F1_TITLE_GAP_PT))
        self.title_align = str(title_cfg.get("align", "center"))
        self.repeat_title = bool(layout.get("pagination", {}).get("repeat_title_on_each_page", True))
        # 三号表题（最多约 3 行换行）+ 五号编号 + 间距
        self._title_block_h = 16.0 * 1.28 * 3 + 10.5 + 6.0 + self.title_gap
        self.table_start_y = self.content_top + self._title_block_h
        self._title_bottom_y = self.table_start_y

        self.rows: List[Dict[str, Any]] = list(layout.get("rows", []))

    def _col_rect(self, col: str, y0: float, y1: float) -> fitz.Rect:
        c = self.columns[col]
        return fitz.Rect(c["x0"], y0, c["x1"], y1)

    def _col_inner_width(self, col: str) -> float:
        c = self.columns[col]
        return max(8.0, c["x1"] - c["x0"] - 2 * CELL_PAD_X)

    def _slot_char_width(self, *, bold: bool = True) -> float:
        return _measure_text("字", self.fonts, self.font_size, bold=bold)

    def _slots_block_rect(
        self, inner: fitz.Rect, slots: int, anchor: str = "center", *, bold: bool = True
    ) -> fitz.Rect:
        """在单元格内放置「slots 个字宽」的文本块（可左对齐或居中）。"""
        target_w = min(self._slot_char_width(bold=bold) * slots, inner.width)
        if anchor == "left":
            x0 = inner.x0
        else:
            x0 = inner.x0 + max(0.0, (inner.width - target_w) / 2.0)
        return fitz.Rect(x0, inner.y0, x0 + target_w, inner.y1)

    def _slots_line_gap(
        self, line: str, block_width: float, *, bold: bool = True
    ) -> Optional[float]:
        """在固定字宽块内自适应分配字距（非铺满整格）。"""
        if len(line) <= 1:
            return None
        line_w = _measure_text(line, self.fonts, self.font_size, bold=bold)
        gaps = len(line) - 1
        need = block_width - line_w
        if gaps <= 0 or need <= 0:
            return None
        return need / gaps

    def _label_line_spacing(self) -> float:
        """表头行距：按字号紧排，不用正文 25 磅行距。"""
        return _line_leading(self.font_size)

    def _label_text_height(
        self,
        text: str,
        col: str,
        label_format: Optional[Dict[str, Any]] = None,
    ) -> float:
        fmt = label_format or {"mode": "center"}
        ls = self._label_line_spacing()
        lines = self._label_format_lines(text, fmt)
        if lines:
            n = len(lines)
            return max(
                self.row_min_h,
                _f1_text_block_height(n, self.font_size, ls) + 2 * CELL_PAD_Y,
            )
        clean = re.sub(r"[\s\u3000]+", "", str(text))
        if fmt.get("mode") == "slots":
            return max(
                self.row_min_h,
                _f1_text_block_height(1, self.font_size, ls) + 2 * CELL_PAD_Y,
            )
        return _f1_autofit_row_height(
            clean,
            self._col_inner_width(col),
            self.fonts,
            self.font_size,
            ls,
            min_height=self.row_min_h,
        )

    @staticmethod
    def _chunk_chars(text: str, n: int) -> List[str]:
        clean = re.sub(r"[\s\u3000]+", "", str(text))
        if n <= 0 or not clean:
            return [clean] if clean else []
        return [clean[i : i + n] for i in range(0, len(clean), n)]

    def _label_format_lines(
        self, text: str, fmt: Optional[Dict[str, Any]]
    ) -> List[str]:
        fmt = fmt or {}
        if fmt.get("lines"):
            return [str(line) for line in fmt["lines"] if str(line)]
        wrap_n = int(fmt.get("wrap_chars") or 0)
        if wrap_n > 0:
            return self._chunk_chars(text, wrap_n)
        return []

    def _value_text_height(
        self,
        text: str,
        col: str,
        value_format: Optional[Dict[str, Any]] = None,
    ) -> float:
        fmt = value_format or {}
        if fmt.get("mode") == "slots":
            return max(
                self.row_min_h,
                _f1_text_block_height(1, self.font_size, self.line_spacing) + 2 * CELL_PAD_Y,
            )
        plain = fmt.get("mode") == "center"
        return self._text_height(text, col, plain=plain)

    def _text_height(self, text: str, col: str, *, plain: bool = False) -> float:
        return _f1_autofit_row_height(
            text,
            self._col_inner_width(col),
            self.fonts,
            self.font_size,
            self.line_spacing,
            min_height=self.row_min_h,
            plain=plain,
        )

    def _field_style_for(self, field_key: str) -> Dict[str, Any]:
        styles = self.raw_data.get("field_styles")
        if not isinstance(styles, dict):
            return {}
        st = styles.get(str(field_key or ""))
        return dict(st) if isinstance(st, dict) else {}

    def _line_entries_for_text(self, text: str, col: str) -> List[Tuple[str, float]]:
        max_w = max(8.0, self._col_inner_width(col))
        return f1_paragraph_line_entries(str(text), max_w, self.fonts, self.font_size)

    def _line_entries_for_value(
        self,
        text: str,
        col: str,
        value_format: Optional[Dict[str, Any]] = None,
        *,
        field_key: str = "",
        first_indent: Optional[bool] = None,
        para_indents: Optional[Sequence[bool]] = None,
    ) -> List[Tuple[str, float]]:
        max_w = max(8.0, self._col_inner_width(col))
        if value_format and value_format.get("mode") == "center":
            return wrap_plain_lines(str(text), max_w, self.fonts, self.font_size)
        st = self._field_style_for(field_key) if field_key else {}
        if first_indent is None:
            first_indent = bool(st.get("first_indent", True)) if st else True
        if para_indents is None and isinstance(st.get("para_indents"), list):
            para_indents = [bool(x) for x in st["para_indents"]]
        return f1_paragraph_line_entries(
            str(text),
            max_w,
            self.fonts,
            self.font_size,
            first_indent=bool(first_indent),
            para_indents=para_indents,
        )

    def _height_for_line_count(self, n: int, *, body_top_pad: bool = False) -> float:
        if n <= 0:
            return self.row_min_h
        pad = (BODY_TEXT_TOP_PAD_PT + CELL_PAD_Y) if body_top_pad else (2 * CELL_PAD_Y)
        return max(
            self.row_min_h,
            _f1_text_block_height(n, self.font_size, self.line_spacing) + pad,
        )

    def _max_lines_that_fit(self, available: float, *, body_top_pad: bool = False) -> int:
        if available < self.row_min_h:
            return 0
        n = 1
        while self._height_for_line_count(n, body_top_pad=body_top_pad) <= available:
            n += 1
        return max(1, n - 1)

    def _f1_draw_label_formatted(
        self,
        rect: fitz.Rect,
        text: str,
        label_format: Optional[Dict[str, Any]] = None,
    ) -> None:
        """按 label_format 绘制表头（居中 / 四字宽拉伸 / 指定换行 / wrap_chars 等）；表头一律加粗。"""
        assert self.page is not None
        fmt = label_format or {"mode": "center"}
        mode = str(fmt.get("mode", "center"))
        inner = _inner_rect(rect)
        bold = True

        lines = self._label_format_lines(text, fmt)
        if not lines:
            clean = re.sub(r"[\s\u3000]+", "", str(text))
            lines = [clean] if clean else []

        if not lines:
            return

        ls = self._label_line_spacing()
        base_y = _f1_first_baseline_y(inner, len(lines), self.font_size, ls)
        for li, line in enumerate(lines):
            if not line:
                continue
            baseline = base_y + li * ls
            if mode == "slots":
                self._f1_draw_slots_line(inner, line, baseline, fmt)
            elif mode == "left":
                _draw_line(
                    self.page,
                    line,
                    baseline,
                    inner,
                    self.fonts,
                    self.font_size,
                    "left",
                    bold=bold,
                )
            else:
                _draw_line(
                    self.page,
                    line,
                    baseline,
                    inner,
                    self.fonts,
                    self.font_size,
                    "center",
                    bold=bold,
                )

    def _f1_draw_slots_line(
        self,
        inner: fitz.Rect,
        line: str,
        baseline: float,
        fmt: Dict[str, Any],
    ) -> None:
        assert self.page is not None
        slots = int(fmt.get("slots", 4))
        anchor = str(fmt.get("anchor", "center"))
        bold = True
        block = self._slots_block_rect(inner, slots, anchor, bold=bold)
        gap = self._slots_line_gap(line, block.width, bold=bold)
        if gap is not None and len(line) > 1:
            _draw_line(
                self.page,
                line,
                baseline,
                block,
                self.fonts,
                self.font_size,
                "left",
                letter_gap=gap,
                bold=bold,
            )
        else:
            _draw_line(
                self.page,
                line,
                baseline,
                block,
                self.fonts,
                self.font_size,
                "center",
                bold=bold,
            )

    def _f1_draw_slots_formatted(
        self,
        rect: fitz.Rect,
        text: str,
        fmt: Dict[str, Any],
        *,
        strip_spaces: bool = False,
    ) -> None:
        assert self.page is not None
        inner = _inner_rect(rect)
        raw = str(text)
        line = re.sub(r"[\s\u3000]+", "", raw) if strip_spaces else raw
        if not line:
            return
        base_y = _f1_first_baseline_y(inner, 1, self.font_size, self.line_spacing)
        self._f1_draw_slots_line(inner, line, base_y, fmt)

    def _f1_draw_content_in_rect(
        self,
        rect: fitz.Rect,
        text: str,
        *,
        align: str = "left",
        vertical_center: bool = False,
        line_spacing: Optional[float] = None,
    ) -> None:
        assert self.page is not None
        if not text:
            return
        inner = _inner_rect(rect)
        max_w = max(8.0, inner.width)
        ls = float(line_spacing) if line_spacing is not None else float(self.line_spacing)
        # 居中内容（如建设性质选项、图题）按明文换行，不做正文首行缩进
        if align == "center":
            entries = wrap_plain_lines(str(text), max_w, self.fonts, self.font_size)
            if vertical_center:
                base_y = _f1_first_baseline_y(
                    inner, len(entries), self.font_size, ls
                )
            else:
                base_y = inner.y0 + _text_ascent(self.font_size)
            for li, (line, _) in enumerate(entries):
                baseline = base_y + li * ls
                if baseline > inner.y1 + self.font_size * 0.15:
                    break
                _draw_line(self.page, line, baseline, inner, self.fonts, self.font_size, align)
        else:
            entries = f1_paragraph_line_entries(str(text), max_w, self.fonts, self.font_size)
            draw_f1_paragraph_entries(
                self.page,
                rect,
                entries,
                self.fonts,
                self.font_size,
                ls,
                align=align,
                vertical_center=vertical_center,
            )

    def _draw_cell_content_entries(
        self,
        rect: fitz.Rect,
        entries: List[Tuple[str, float]],
        *,
        align: str = "left",
        vertical_center: bool = False,
    ) -> None:
        assert self.page is not None
        draw_f1_paragraph_entries(
            self.page,
            rect,
            entries,
            self.fonts,
            self.font_size,
            self.line_spacing,
            align=align,
            vertical_center=vertical_center,
        )

    def _draw_side_borders(self, top_y: float, bottom_y: float) -> None:
        if self.page is None or bottom_y <= top_y:
            return
        self.page.draw_line(
            (self.table_x_left, top_y), (self.table_x_left, bottom_y), color=(0, 0, 0), width=0.6
        )
        self.page.draw_line(
            (self.table_x_right, top_y), (self.table_x_right, bottom_y), color=(0, 0, 0), width=0.6
        )

    def _finish_page_band(self) -> None:
        # 异形页（A3 附图等）外框已自绘，禁止再按 A4 表宽补竖线（否则图区中间多一条线）
        if (
            self.page is not None
            and self.page_band_top is not None
            and self.page_band_bottom is not None
        ):
            ps = self.layout.get("page_size") or {}
            want_w = float(ps.get("width") or self.page_width)
            want_h = float(ps.get("height") or self.page_height)
            cur_w = float(self.page.rect.width)
            cur_h = float(self.page.rect.height)
            if abs(cur_w - want_w) <= 1.0 and abs(cur_h - want_h) <= 1.0:
                self._draw_side_borders(self.page_band_top, self.page_band_bottom)
        self.page_band_top = None
        self.page_band_bottom = None

    def _draw_page_number(self, page_no: int) -> None:
        """旧式奇右偶左页码；若 layout 启用 report_chrome 则跳过（由后处理统一页脚）。"""
        pag = self.layout.get("pagination") or {}
        if pag.get("report_chrome"):
            return
        assert self.page is not None
        text = str(page_no)
        w = _measure_text(text, self.fonts, self.font_size)
        # 使用当前页实际尺寸，避免 A3 等异形页仍按 A4 page_width 定位
        page_w = float(self.page.rect.width)
        page_h = float(self.page.rect.height)
        baseline = page_h - self.margin_bottom / 2.0 + self.font_size * 0.35
        if page_no % 2 == 1:
            x = page_w - self.margin_right - w
        else:
            x = self.margin_left
        fn = self.fonts.song
        self.page.insert_text((x, baseline), text, fontname=fn, fontsize=self.font_size, color=(0, 0, 0))

    def _new_page(self, *, continuation: bool) -> None:
        if self.page is not None:
            self._finish_page_band()
            self._draw_page_number(self.page_index)
        self._figure_page_pending = False
        self.page_index += 1
        ps = self.layout["page_size"]
        self.page = self.doc.new_page(width=ps["width"], height=ps["height"])
        self.fonts = _f1_register_fonts(self.page, self.font_base)
        if not continuation or self.repeat_title:
            self._draw_title()
            self.y = float(getattr(self, "_title_bottom_y", self.table_start_y))
        else:
            self.y = self.content_top

    def _ensure_space(self, height: float) -> None:
        if self.page is None:
            self._new_page(continuation=False)
            return
        # 附图页（A3 等）之后回到 A4 流式排版，避免在异形页上继续画表
        ps = self.layout.get("page_size") or {}
        want_w = float(ps.get("width") or self.page_width)
        want_h = float(ps.get("height") or self.page_height)
        cur_w = float(self.page.rect.width)
        cur_h = float(self.page.rect.height)
        if abs(cur_w - want_w) > 1.0 or abs(cur_h - want_h) > 1.0:
            self._figure_page_pending = False
            self._new_page(continuation=True)
            return
        if getattr(self, "_figure_page_pending", False):
            self._figure_page_pending = False
            self._new_page(continuation=True)
            return
        if self.y + height > self.content_bottom:
            self._new_page(continuation=True)

    def _mark_band(self, top: float, bottom: float) -> None:
        if self.page_band_top is None:
            self.page_band_top = top
        else:
            self.page_band_top = min(self.page_band_top, top)
        if self.page_band_bottom is None:
            self.page_band_bottom = bottom
        else:
            self.page_band_bottom = max(self.page_band_bottom, bottom)

    def _draw_title(self) -> None:
        """表题：黑体三号完整报告表名称（按表宽换行）；其下靠右「编号：…」五号宋体。"""
        assert self.page is not None
        title = self.layout.get("title") or {}
        line1 = str(title.get("title_line1") or "").strip()
        line2 = str(title.get("title_line2") or "").strip()
        name_text = str(title.get("table_name") or self.layout.get("table_title", "")).strip()
        report_no = str(
            title.get("report_no")
            or self.layout.get("report_no")
            or ""
        ).strip()

        # 优先完整全称；否则拼封面两行（避免只显示到「院区」）
        if not name_text:
            name_text = f"{line1}{line2}".strip()
        if not name_text and (line1 or line2):
            name_text = f"{line1}{line2}".strip()

        from f1_eval_report.fonts_util import (
            PT_SAN_HAO,
            PT_WU_HAO,
            ensure_hei,
            ensure_song,
            simhei_path,
            text_width,
        )

        hei = ensure_hei(self.page)
        song = ensure_song(self.page)
        hei_path = simhei_path()
        fs_title = PT_SAN_HAO
        fs_no = PT_WU_HAO
        y0 = self.content_top
        max_w = max(40.0, float(self.table_x_right - self.table_x_left))

        # 按表宽逐字换行（中文标题）
        lines: list[str] = []
        if name_text:
            buf = ""
            for ch in name_text:
                trial = buf + ch
                if buf and text_width(trial, hei_path, fs_title) > max_w:
                    lines.append(buf)
                    buf = ch
                else:
                    buf = trial
            if buf:
                lines.append(buf)

        h_line = fs_title * 1.28
        for i, ln in enumerate(lines):
            self.page.insert_textbox(
                fitz.Rect(self.table_x_left, y0, self.table_x_right, y0 + h_line + 2),
                ln,
                fontname=hei,
                fontsize=fs_title,
                color=(0, 0, 0),
                align=1,
            )
            y0 += h_line
        # 编号紧挨表题；编号与表格顶线再收紧
        if report_no:
            y0 += 1.0
            label = report_no if report_no.startswith("编号") else f"编号：{report_no}"
            self.page.insert_textbox(
                fitz.Rect(self.table_x_left, y0, self.table_x_right, y0 + fs_no + 4),
                label,
                fontname=song,
                fontsize=fs_no,
                color=(0, 0, 0),
                align=2,
            )
            y0 += fs_no + 3.0
        else:
            y0 += 2.0
        self._title_bottom_y = y0 + min(float(self.title_gap), 3.0)

    def _draw_cell(
        self,
        col: str,
        y0: float,
        y1: float,
        text: str,
        *,
        is_label: bool,
        label_format: Optional[Dict[str, Any]] = None,
        value_format: Optional[Dict[str, Any]] = None,
    ) -> None:
        assert self.page is not None
        rect = self._col_rect(col, y0, y1)
        _draw_cell_border(self.page, rect)
        if text or (is_label and label_format and label_format.get("lines")):
            if is_label:
                self._f1_draw_label_formatted(rect, text, label_format)
            elif value_format and value_format.get("mode") == "slots":
                self._f1_draw_slots_formatted(rect, text, value_format)
            else:
                align = (
                    "center"
                    if value_format and value_format.get("mode") == "center"
                    else "left"
                )
                self._f1_draw_content_in_rect(
                    rect, text, align=align, vertical_center=True
                )

    def _line_height(self, cells: List[Dict[str, Any]]) -> float:
        h = self.row_min_h
        for cell in cells:
            role = cell.get("role")
            col = cell.get("col", "")
            if role == "value":
                text = self.values.get(cell.get("field_key", ""), "")
                h = max(
                    h,
                    self._value_text_height(text, col, cell.get("value_format")),
                )
                continue
            elif role == "static":
                text = str(cell.get("text", ""))
                h = max(
                    h,
                    self._value_text_height(text, col, cell.get("value_format")),
                )
                continue
            elif role == "label":
                text = str(cell.get("text", ""))
                h = max(
                    h,
                    self._label_text_height(text, col, cell.get("label_format")),
                )
                continue
            else:
                text = ""
            if text:
                h = max(h, self._text_height(text, col))
        return h

    def _draw_line_cells(self, cells: List[Dict[str, Any]], y0: float, height: float) -> None:
        y1 = y0 + height
        for cell in cells:
            role = cell.get("role")
            col = cell.get("col", "")
            if role == "label":
                self._draw_cell(
                    col,
                    y0,
                    y1,
                    str(cell.get("text", "")),
                    is_label=True,
                    label_format=cell.get("label_format"),
                )
            elif role == "static":
                self._draw_cell(
                    col,
                    y0,
                    y1,
                    str(cell.get("text", "")),
                    is_label=False,
                    value_format=cell.get("value_format"),
                )
            elif role == "value":
                text = self.values.get(cell.get("field_key", ""), "")
                self._draw_cell(
                    col,
                    y0,
                    y1,
                    text,
                    is_label=False,
                    value_format=cell.get("value_format"),
                )

    def _advance(self, height: float) -> None:
        top = self.y
        self.y += height
        self._mark_band(top, self.y)

    def _draw_pairs(self, row_def: Dict[str, Any]) -> None:
        pairs = row_def.get("pairs", [])
        heights = []
        for p in pairs:
            lh = self._label_text_height(
                p.get("label", ""), p["label_col"], p.get("label_format")
            )
            vh = self._value_text_height(
                self.values.get(p.get("field_key", ""), ""),
                p["value_col"],
                p.get("value_format"),
            )
            heights.append(max(lh, vh))
        h = max(heights) if heights else self.row_min_h
        self._ensure_space(h)
        y0 = self.y
        for p in pairs:
            self._draw_cell(
                p["label_col"],
                y0,
                y0 + h,
                p.get("label", ""),
                is_label=True,
                label_format=p.get("label_format"),
            )
            self._draw_cell(
                p["value_col"],
                y0,
                y0 + h,
                self.values.get(p.get("field_key", ""), ""),
                is_label=False,
                value_format=p.get("value_format"),
            )
        self._advance(h)

    def _draw_label_value(self, row_def: Dict[str, Any]) -> None:
        label = str(row_def.get("label", ""))
        value_col = row_def.get("value_col", "value_full")
        label_col = row_def.get("label_col", "label_main")
        label_format = row_def.get("label_format")
        value_format = row_def.get("value_format")
        value_align = (
            "center"
            if value_format and value_format.get("mode") == "center"
            else "left"
        )
        value = self.values.get(row_def.get("field_key", ""), "")
        field_key = str(row_def.get("field_key") or "")
        entries = self._line_entries_for_value(
            value, value_col, value_format, field_key=field_key
        )
        has_value = any(line.strip() for line, _ in entries)

        if not has_value:
            h = max(self._label_text_height(label, label_col, label_format), self.row_min_h)
            self._ensure_space(h)
            y0 = self.y
            self._draw_cell(
                label_col, y0, y0 + h, label, is_label=True, label_format=label_format
            )
            self._draw_cell(
                value_col, y0, y0 + h, "", is_label=False, value_format=value_format
            )
            self._advance(h)
            return

        label_h = self._label_text_height(label, label_col, label_format)
        body_pad = value_align != "center"
        idx = 0
        while idx < len(entries):
            if self.page is None:
                self._new_page(continuation=False)
            available = self.content_bottom - self.y
            if available < self.row_min_h:
                self._new_page(continuation=True)
                available = self.content_bottom - self.y

            remaining = len(entries) - idx
            n = min(remaining, self._max_lines_that_fit(available, body_top_pad=body_pad))
            while n > 1 and self._height_for_line_count(n, body_top_pad=body_pad) > available:
                n -= 1
            if n <= 0:
                self._new_page(continuation=True)
                continue

            chunk_entries = entries[idx:idx + n]
            idx += n
            chunk_h = max(
                self._height_for_line_count(len(chunk_entries), body_top_pad=body_pad),
                label_h,
            )

            y0 = self.y
            y1 = y0 + chunk_h
            self._draw_cell(
                label_col, y0, y1, label, is_label=True, label_format=label_format
            )
            value_rect = self._col_rect(value_col, y0, y1)
            _draw_cell_border(self.page, value_rect)
            # 正文距上表格线 BODY_TEXT_TOP_PAD_PT；居中短栏不加
            draw_rect = value_rect
            if body_pad:
                draw_rect = fitz.Rect(
                    value_rect.x0,
                    value_rect.y0 + BODY_TEXT_TOP_PAD_PT,
                    value_rect.x1,
                    value_rect.y1,
                )
            self._draw_cell_content_entries(
                draw_rect,
                chunk_entries,
                align=value_align,
                vertical_center=value_align == "center",
            )
            self._advance(chunk_h)

    def _draw_grid(self, row_def: Dict[str, Any]) -> None:
        cells = row_def.get("cells", [])
        h = self._line_height(cells)
        self._ensure_space(h)
        y0 = self.y
        self._draw_line_cells(cells, y0, h)
        self._advance(h)

    def _flatten_section_lines(self, row_def: Dict[str, Any]) -> List[Dict[str, Any]]:
        blocks = row_def.get("blocks")
        if blocks:
            flat: List[Dict[str, Any]] = []
            for bi, block in enumerate(blocks):
                side_label = block.get("side_label")
                side_col = block.get("side_col")
                for li, line in enumerate(block.get("lines", [])):
                    flat.append(
                        {
                            "cells": line.get("cells", []),
                            "side_label": side_label,
                            "side_col": side_col,
                            "block_index": bi,
                            "line_in_block": li,
                        }
                    )
            return flat
        return [
            {
                "cells": line.get("cells", []),
                "side_label": None,
                "side_col": None,
                "block_index": -1,
                "line_in_block": 0,
            }
            for line in row_def.get("lines", [])
        ]

    def _draw_section(self, row_def: Dict[str, Any]) -> None:
        section_col = row_def.get("section_col", "label_main")
        section_label = str(row_def.get("section_label", ""))
        flat = self._flatten_section_lines(row_def)
        line_heights = [self._line_height(item["cells"]) for item in flat]

        i = 0
        while i < len(flat):
            self._ensure_space(line_heights[i])

            chunk_indices: List[int] = []
            chunk_h = 0.0
            while i < len(flat):
                lh = line_heights[i]
                if chunk_h > 0 and self.y + chunk_h + lh > self.content_bottom:
                    break
                chunk_indices.append(i)
                chunk_h += lh
                i += 1

            y0 = self.y
            section_fmt = row_def.get("section_label_format")
            self._draw_cell(
                section_col,
                y0,
                y0 + chunk_h,
                section_label,
                is_label=True,
                label_format=section_fmt,
            )

            cy = y0
            pos = 0
            while pos < len(chunk_indices):
                idx = chunk_indices[pos]
                item = flat[idx]
                bi = item["block_index"]
                block_chunk: List[int] = []
                while pos < len(chunk_indices) and flat[chunk_indices[pos]]["block_index"] == bi:
                    block_chunk.append(chunk_indices[pos])
                    pos += 1

                block_chunk_h = sum(line_heights[j] for j in block_chunk)
                side_col = item.get("side_col")
                side_label = item.get("side_label")
                if side_col and side_label:
                    self._draw_cell(
                        side_col,
                        cy,
                        cy + block_chunk_h,
                        str(side_label),
                        is_label=True,
                        label_format=row_def.get("side_label_format"),
                    )

                for j in block_chunk:
                    lh = line_heights[j]
                    self._draw_line_cells(flat[j]["cells"], cy, lh)
                    cy += lh

            self._advance(chunk_h)

    def _apply_row_spacing(self, row_def: Dict[str, Any]) -> Tuple[float, float]:
        """按行覆盖行距；返回 (旧 line_spacing, 旧 row_min_h) 供还原。"""
        prev_ls = self.line_spacing
        prev_min = self.row_min_h
        if row_def.get("line_spacing_pt") is not None:
            self.line_spacing = float(row_def["line_spacing_pt"])
            self.row_min_h = max(
                self.line_spacing,
                float(row_def.get("row_min_height_pt") or self.line_spacing),
            )
        elif str(row_def.get("spacing") or "") == "normal":
            self.line_spacing = _line_leading(self.font_size)
            self.row_min_h = max(
                self.line_spacing,
                float(row_def.get("row_min_height_pt") or self.line_spacing),
            )
        return prev_ls, prev_min

    def _draw_row_def(self, row_def: Dict[str, Any]) -> None:
        prev_ls, prev_min = self._apply_row_spacing(row_def)
        try:
            t = row_def.get("type")
            if t == "pairs":
                self._draw_pairs(row_def)
            elif t == "label_value":
                self._draw_label_value(row_def)
            elif t == "label_embedded_table":
                embed = row_def.get("embed") or {}
                kind = embed.get("kind")
                if kind == "field_record":
                    embed_field_record_table(self, row_def, embed)
                elif kind == "generic_table":
                    embed_generic_table(self, row_def, embed)
                elif kind == "content_with_tables":
                    embed_content_with_tables(self, row_def, embed)
                elif kind == "workplace_layout":
                    embed_workplace_layout(self, row_def, embed)
                else:
                    raise ValueError(f"未知嵌套表格类型: {kind}")
            elif t == "grid":
                self._draw_grid(row_def)
            elif t == "section":
                self._draw_section(row_def)
        finally:
            self.line_spacing = prev_ls
            self.row_min_h = prev_min

    def build(self) -> fitz.Document:
        if not self.rows:
            raise ValueError("版式中无 rows 定义")
        for row_def in self.rows:
            self._draw_row_def(row_def)
        self._finish_page_band()
        if self.page is not None:
            self._draw_page_number(self.page_index)
        return self.doc

    def save(self, output_path: str) -> None:
        from f1_eval_report.fonts_util import save_pdf_compressed

        doc = self.build()
        try:
            save_pdf_compressed(doc, output_path)
        finally:
            doc.close()


def generate_gbzt_f1_pdf(
    layout_path: str,
    output_pdf: str,
    data_path: Optional[str] = None,
) -> None:
    layout = load_f1_layout(layout_path)
    values = load_f1_data(data_path)
    GbztF1TableBuilder(layout, values).save(output_pdf)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate GBZ/T table F.1 PDF (v2 layout).")
    parser.add_argument("--layout", default=DEFAULT_F1_LAYOUT_PATH)
    parser.add_argument("--data", default=None)
    parser.add_argument("--output", default="gbzt_f1_output.pdf")
    args = parser.parse_args()
    generate_gbzt_f1_pdf(args.layout, args.output, args.data)
    print(f"PDF written: {args.output}")


if __name__ == "__main__":
    main()
