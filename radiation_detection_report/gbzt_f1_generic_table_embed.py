"""
在 GBZ/T 表 F.1 的 value 区域内嵌套渲染常规表格（如 assess 4.1 屏蔽设计表）。
表格正文默认五号；表题默认小四；自动折行与跨页；默认续页不重复表头。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

import fitz

from radiation_detection_report.generator import (
    CELL_PAD_X,
    CELL_PAD_Y,
    FONT_SIZE_WU_HAO,
    FONT_SIZE_XIAO_SI,
    _autofit_row_height,
    _cell_lines,
    _draw_cell_border,
    _draw_cell_content,
    _draw_line,
    _line_leading,
    _text_ascent,
    _text_block_height,
)

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))

EMBED_FONT_SIZE = FONT_SIZE_WU_HAO  # 五号 10.5pt：嵌套表单元格默认
EMBED_TITLE_FONT_SIZE = FONT_SIZE_XIAO_SI  # 小四 12pt：表题默认
EMBED_CELL_PAD = CELL_PAD_X
DEFAULT_ROW_MIN_H = 17.5


def _resolve_embed_table(host: Any, embed: Dict[str, Any]) -> Dict[str, Any]:
    key = embed.get("data_key", "shielding_table")
    raw = host.raw_data.get(key)
    if isinstance(raw, dict) and raw.get("rows"):
        return raw
    default_rel = embed.get("default_data")
    if default_rel:
        path = default_rel if os.path.isabs(default_rel) else os.path.join(PACKAGE_DIR, default_rel)
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("rows"):
                return data
    layout_rel = embed.get("layout")
    if layout_rel:
        path = layout_rel if os.path.isabs(layout_rel) else os.path.join(PACKAGE_DIR, layout_rel)
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("rows"):
                return data
    raise ValueError(
        f"嵌套表格缺少数据：请在 JSON 中提供 '{key}' 或配置 embed.default_data / embed.layout"
    )


def _column_bounds(table_left: float, table_right: float, fractions: List[float]) -> List[float]:
    w = table_right - table_left
    return [table_left + float(f) * w for f in fractions]


class F1GenericTableEmbedBuilder:
    def __init__(
        self,
        host: Any,
        row_def: Dict[str, Any],
        table_data: Dict[str, Any],
        embed: Dict[str, Any],
    ) -> None:
        self.host = host
        self.row_def = row_def
        self.data = table_data
        self.embed = embed
        self.defer_finish = bool(embed.get("defer_finish"))
        self.page = host.page
        self.fonts = host.fonts
        self.y = host.y
        self.band_top = host.y
        self.row_min_h = float(
            table_data.get("row_min_height_pt")
            or embed.get("row_min_height_pt")
            or DEFAULT_ROW_MIN_H
        )
        value_col = row_def.get("value_col", "value_full")
        x0 = host.columns[value_col]["x0"] + EMBED_CELL_PAD
        x1 = host.columns[value_col]["x1"] - EMBED_CELL_PAD
        self.table_left = x0
        self.table_right = x1
        fracs = table_data.get("column_fractions") or []
        if len(fracs) < 2:
            n = int(table_data.get("column_count", 4))
            fracs = [i / n for i in range(n + 1)]
        self.col_bounds = _column_bounds(self.table_left, self.table_right, fracs)

    def _usable_bottom(self) -> float:
        return float(self.host.content_bottom) - EMBED_CELL_PAD

    def _segment_bottom(self) -> float:
        return self.y + EMBED_CELL_PAD

    def _sync_f1_table_frame(
        self,
        *,
        draw_top: bool = False,
        draw_bottom: bool = False,
    ) -> None:
        bottom = self._segment_bottom()
        if bottom <= self.band_top:
            return
        self.host._mark_band(self.band_top, bottom)
        self.page = self.host.page
        page = self.page
        if page is None:
            return
        x_left = self.host.table_x_left
        x_right = self.host.table_x_right
        label_col = self.row_def.get("label_col", "label_main")
        lx1 = self.host.columns[label_col]["x1"]
        line_w = 0.6
        color = (0, 0, 0)
        if draw_top:
            page.draw_line(
                (x_left, self.band_top), (x_right, self.band_top), color=color, width=line_w
            )
        page.draw_line((lx1, self.band_top), (lx1, bottom), color=color, width=line_w)
        if draw_bottom:
            page.draw_line((x_left, bottom), (x_right, bottom), color=color, width=line_w)

    def _finish_band(self) -> None:
        bottom = self._segment_bottom()
        if self.band_top >= bottom:
            return
        self._sync_f1_table_frame(draw_bottom=True)
        if self.defer_finish:
            self.host._mark_band(self.band_top, bottom)
            return
        self.host._draw_cell(
            self.row_def.get("label_col", "label_main"),
            self.band_top,
            bottom,
            str(self.row_def.get("label", "")),
            is_label=True,
            label_format=self.row_def.get("label_format"),
        )
        self.host._mark_band(self.band_top, bottom)

    def _page_break(self) -> None:
        if self.defer_finish:
            parent = getattr(self.host, "_content_embed_parent", None)
            # 先把父级游标同步到当前嵌套表位置，避免用正文末尾的旧 y 在表中误画分隔线
            if parent is not None:
                parent.page = self.host.page
                parent.y = self.y
            self._sync_f1_table_frame(draw_bottom=True)
            if parent is not None:
                parent._finish_band()
            self.host._new_page(continuation=True)
            self.page = self.host.page
            self.band_top = self.host.y
            self.y = self.host.y + EMBED_CELL_PAD
            if parent is not None:
                parent.page = self.host.page
                parent.band_top = self.host.y
                parent.y = self.y
            self._sync_f1_table_frame(draw_top=True)
        else:
            self._finish_band()
            self.host._new_page(continuation=True)
            self.page = self.host.page
            self.band_top = self.host.y
            self.y = self.host.y + EMBED_CELL_PAD
            self._sync_f1_table_frame(draw_top=True)

    def _ensure_space(self, height: float) -> None:
        if self.y + height <= self._usable_bottom():
            return
        self._page_break()

    def _cell_x(self, c0: int, c1: int) -> Tuple[float, float]:
        c0 = max(0, min(c0, len(self.col_bounds) - 2))
        c1 = max(c0 + 1, min(c1, len(self.col_bounds) - 1))
        return self.col_bounds[c0], self.col_bounds[c1]

    def _inner_width(self, c0: int, c1: int) -> float:
        x0, x1 = self._cell_x(c0, c1)
        return max(8.0, x1 - x0 - 2 * CELL_PAD_X)

    def _cell_font_size(self, cell: Dict[str, Any]) -> float:
        try:
            fs = float(cell.get("font_size_pt")) if cell.get("font_size_pt") is not None else EMBED_FONT_SIZE
        except (TypeError, ValueError):
            fs = EMBED_FONT_SIZE
        return max(8.0, min(24.0, fs))

    def _cell_align(self, cell: Dict[str, Any]) -> str:
        explicit = cell.get("align") or self.data.get("cell_align") or self.data.get("default_align")
        if explicit in ("left", "center", "right"):
            return str(explicit)
        c0, c1 = int(cell["c0"]), int(cell["c1"])
        text = str(cell.get("text", ""))
        if c0 == 0 and c1 - c0 == 1:
            if len(text.replace("\n", "")) <= 4:
                return "center"
            return "center"
        return "left"

    def _is_header_band(self, band: int) -> bool:
        if self.data.get("header_bold") is False:
            return False
        headers = self.data.get("header_bands")
        if headers is None:
            return int(band) == 0
        return int(band) in {int(b) for b in headers}

    def _cell_bold(self, cell: Dict[str, Any], band: int) -> bool:
        if cell.get("bold") is not None:
            return bool(cell.get("bold"))
        return self._is_header_band(band)

    def _anchor_cells(self) -> List[Tuple[int, List[Dict[str, Any]]]]:
        anchors: List[Tuple[int, List[Dict[str, Any]]]] = []
        for row in self.data.get("rows", []):
            cells = row.get("cells") or []
            if not cells:
                continue
            band = row.get("band")
            if band is None:
                band = len(anchors)
            anchors.append((int(band), cells))
        return anchors

    def _band_count(self, anchors: List[Tuple[int, List[Dict[str, Any]]]]) -> int:
        explicit = self.data.get("band_count")
        if explicit:
            return int(explicit)
        max_end = 0
        for band, cells in anchors:
            for cell in cells:
                rs = int(cell.get("row_span", 1) or 1)
                max_end = max(max_end, band + rs)
        return max(max_end, len(anchors))

    def _illegal_split_bands(
        self,
        anchors: List[Tuple[int, List[Dict[str, Any]]]],
        band_count: int,
    ) -> set[int]:
        illegal: set[int] = set()
        for band, cells in anchors:
            for cell in cells:
                rs = max(1, int(cell.get("row_span", 1) or 1))
                end = min(band_count, band + rs)
                for split in range(band + 1, end):
                    illegal.add(split)
        return illegal

    def _legal_chunk_fits(
        self,
        band_start: int,
        chunk_len: int,
        illegal: set[int],
        band_count: int,
    ) -> List[int]:
        fits: List[int] = []
        for fit in range(1, chunk_len + 1):
            split = band_start + fit
            if split > band_count:
                break
            if split not in illegal:
                fits.append(fit)
        return fits

    def _pick_chunk_fit(
        self,
        band_start: int,
        chunk_heights: List[float],
        usable: float,
        illegal: set[int],
        band_count: int,
    ) -> int:
        legal = self._legal_chunk_fits(
            band_start, len(chunk_heights), illegal, band_count
        )
        best = 0
        for fit in legal:
            if sum(chunk_heights[:fit]) <= usable:
                best = max(best, fit)
        if best > 0:
            return best

        acc = 0.0
        fit = 0
        for h in chunk_heights:
            if acc + h > usable:
                break
            fit += 1
            acc += h
        return fit

    def _compute_band_heights(
        self,
        anchors: List[Tuple[int, List[Dict[str, Any]]]],
        band_count: int,
    ) -> List[float]:
        heights = [self.row_min_h] * band_count
        for band, cells in anchors:
            for cell in cells:
                rs = max(1, int(cell.get("row_span", 1) or 1))
                end = min(band_count, band + rs)
                inner_w = self._inner_width(int(cell["c0"]), int(cell["c1"]))
                bold = self._cell_bold(cell, band)
                need = _autofit_row_height(
                    self._cell_text(cell),
                    inner_w,
                    self.fonts,
                    EMBED_FONT_SIZE,
                    min_height=self.row_min_h,
                )
                # 加粗表头用黑体测宽再估高，避免折行高度偏紧
                if bold:
                    from radiation_detection_report.generator import _cell_lines, _text_block_height

                    lines = _cell_lines(
                        self._cell_text(cell),
                        inner_w,
                        self.fonts,
                        EMBED_FONT_SIZE,
                        bold=True,
                    )
                    need = max(
                        self.row_min_h,
                        _text_block_height(len(lines), EMBED_FONT_SIZE) + 2 * CELL_PAD_Y,
                    )
                span_sum = sum(heights[band:end])
                if need > span_sum:
                    extra = (need - span_sum) / rs
                    for k in range(band, end):
                        heights[k] += extra
        return heights

    def _band_height_sum(
        self,
        band_heights: List[float],
        start_band: int,
        end_band: int,
    ) -> float:
        return sum(band_heights[start_band:end_band])

    def _cell_text(self, cell: Dict[str, Any]) -> str:
        text = str(cell.get("text", ""))
        wrap_n = 0
        try:
            wrap_n = int(cell.get("wrap_chars") or 0)
        except (TypeError, ValueError):
            wrap_n = 0
        if wrap_n <= 0:
            return text
        import re

        clean = re.sub(r"[\s\u3000]+", "", text)
        if not clean:
            return text
        return "\n".join(clean[i : i + wrap_n] for i in range(0, len(clean), wrap_n))

    def _rowspan_lines(
        self, cell: Dict[str, Any], anchor_band: int
    ) -> Tuple[List[str], float, str, bool]:
        text = self._cell_text(cell).strip()
        c0, c1 = int(cell["c0"]), int(cell["c1"])
        max_w = self._inner_width(c0, c1)
        bold = self._cell_bold(cell, anchor_band)
        font_size = self._cell_font_size(cell)
        align = self._cell_align(cell)
        lines = _cell_lines(text, max_w, self.fonts, font_size, bold=bold) if text else []
        return lines, font_size, align, bold

    def _decide_rowspan_text_mode(
        self,
        block_h: float,
        seg_inner_h: float,
        rem_inner_h: float,
    ) -> Tuple[str, str]:
        """
        返回 (本段模式, 续段模式)：full / none / split。
        两边都放得下 → 两边都放完整表头；只一边够 → 只放那边；都不够 → 拆分。
        """
        can_here = seg_inner_h + 0.5 >= block_h
        can_there = rem_inner_h + 0.5 >= block_h
        if can_here and can_there:
            return "full", "full"
        if can_here and not can_there:
            return "full", "none"
        if not can_here and can_there:
            return "none", "full"
        return "split", "split"

    def _draw_rowspan_text_segment(
        self,
        cell: Dict[str, Any],
        segment_y0: float,
        segment_y1: float,
        anchor_band: int,
        seg_start_band: int,
        seg_end_band: int,
        band_heights: List[float],
        *,
        text_mode: str = "split",
        line_start: int = 0,
    ) -> int:
        """绘制 rowspan 文本；返回下一段应继续的 line_start（split 模式）。"""
        lines, font_size, align, bold = self._rowspan_lines(cell, anchor_band)
        if not lines or text_mode == "none":
            return line_start
        c0, c1 = int(cell["c0"]), int(cell["c1"])
        x0, x1 = self._cell_x(c0, c1)
        inner = fitz.Rect(
            x0 + CELL_PAD_X,
            segment_y0 + CELL_PAD_Y,
            x1 - CELL_PAD_X,
            segment_y1 - CELL_PAD_Y,
        )
        if inner.height <= 1 or inner.width <= 1:
            return line_start
        leading = _line_leading(font_size)
        ascent = _text_ascent(font_size)
        # 基线须落在 [inner.y0+ascent, inner.y1 - descent] 内，避免压线
        min_base = inner.y0 + ascent
        max_base = inner.y1 - font_size * 0.22

        def _paint(line_list: List[str], first_base: float) -> None:
            for i, line in enumerate(line_list):
                by = first_base + i * leading
                if by < min_base - 0.2 or by > max_base + 0.2:
                    continue
                _draw_line(
                    self.page,
                    line,
                    by,
                    inner,
                    self.fonts,
                    font_size,
                    align,
                    bold=bold,
                )

        if text_mode == "full":
            block_h = _text_block_height(len(lines), font_size)
            first_base = inner.y0 + max(0.0, (inner.height - block_h) / 2.0) + ascent
            # 若居中仍可能越界，改为顶对齐并裁切
            if first_base < min_base:
                first_base = min_base
            _paint(lines, first_base)
            return len(lines)

        # split：从 line_start 起，能放下几行画几行
        usable_lines = []
        for i in range(line_start, len(lines)):
            trial_h = _text_block_height(len(usable_lines) + 1, font_size)
            if trial_h > inner.height + 0.5 and usable_lines:
                break
            usable_lines.append(lines[i])
        if not usable_lines and line_start < len(lines):
            # 至少尝试一行（仍受 max_base 裁切）
            usable_lines = [lines[line_start]]
        block_h = _text_block_height(len(usable_lines), font_size)
        # 本段能放下全部剩余行时竖直居中，避免长单元格上下留白不均
        if (
            line_start == 0
            and line_start + len(usable_lines) >= len(lines)
            and block_h <= inner.height + 0.5
        ):
            first_base = inner.y0 + max(0.0, (inner.height - block_h) / 2.0) + ascent
            if first_base < min_base:
                first_base = min_base
        else:
            first_base = inner.y0 + ascent
        _paint(usable_lines, first_base)
        return line_start + len(usable_lines)

    def _draw_cell_segment(
        self,
        cell: Dict[str, Any],
        y0: float,
        y1: float,
        anchor_band: int,
        seg_start_band: int,
        seg_end_band: int,
        band_heights: List[float],
        *,
        draw_text: bool,
        text_mode: str = "split",
        line_start: int = 0,
    ) -> int:
        c0, c1 = int(cell["c0"]), int(cell["c1"])
        x0, x1 = self._cell_x(c0, c1)
        rect = fitz.Rect(x0, y0, x1, y1)
        _draw_cell_border(self.page, rect)
        if not draw_text:
            return line_start
        rs = max(1, int(cell.get("row_span", 1) or 1))
        if rs > 1:
            return self._draw_rowspan_text_segment(
                cell,
                y0,
                y1,
                anchor_band,
                seg_start_band,
                seg_end_band,
                band_heights,
                text_mode=text_mode,
                line_start=line_start,
            )
        _draw_cell_content(
            self.page,
            rect,
            self._cell_text(cell),
            self.fonts,
            self._cell_font_size(cell),
            align=self._cell_align(cell),
            bold=self._cell_bold(cell, anchor_band),
            first_indent=bool(cell.get("first_indent")),
            para_indents=cell.get("para_indents") if isinstance(cell.get("para_indents"), list) else None,
        )
        return line_start

    def _draw_anchor_cell(
        self,
        cell: Dict[str, Any],
        anchor_band: int,
        band_start: int,
        band_end: int,
        y_offsets: List[float],
        band_heights: List[float],
    ) -> Optional[Dict[str, Any]]:
        rs = max(1, int(cell.get("row_span", 1) or 1))
        cell_end = anchor_band + rs
        draw_end = min(cell_end, band_end)
        local_0 = anchor_band - band_start
        local_1 = draw_end - band_start
        if local_0 < 0 or local_1 <= local_0 or local_1 > len(y_offsets) - 1:
            return None
        y0 = y_offsets[local_0]
        y1 = y_offsets[local_1]
        lines, font_size, _align, _bold = self._rowspan_lines(cell, anchor_band)
        block_h = _text_block_height(len(lines), font_size) if lines else 0.0
        seg_inner = max(0.0, (y1 - y0) - 2 * CELL_PAD_Y)
        rem_h = self._band_height_sum(band_heights, draw_end, cell_end)
        rem_inner = max(0.0, rem_h - 2 * CELL_PAD_Y) if cell_end > draw_end else 0.0
        if cell_end > band_end and rs > 1 and lines:
            mode_here, mode_tail = self._decide_rowspan_text_mode(block_h, seg_inner, rem_inner)
        else:
            mode_here, mode_tail = ("split", "split") if rs > 1 else ("full", "none")
            if rs == 1:
                mode_here = "full"
        next_line = self._draw_cell_segment(
            cell,
            y0,
            y1,
            anchor_band,
            anchor_band,
            draw_end,
            band_heights,
            draw_text=True,
            text_mode=mode_here if rs > 1 else "full",
            line_start=0,
        )
        if cell_end > band_end:
            return {
                "cell": cell,
                "anchor_band": anchor_band,
                "resume_band": band_end,
                "end_band": cell_end,
                "text_mode": mode_tail,
                "line_start": next_line if mode_tail == "split" else 0,
            }
        return None

    def _draw_rowspan_tails(
        self,
        tails: List[Dict[str, Any]],
        band_start: int,
        band_end: int,
        y_offsets: List[float],
        band_heights: List[float],
    ) -> List[Dict[str, Any]]:
        remaining: List[Dict[str, Any]] = []
        for tail in tails:
            resume = int(tail["resume_band"])
            end = int(tail["end_band"])
            anchor_band = int(tail["anchor_band"])
            if end <= band_start:
                continue
            if resume >= band_end:
                remaining.append(tail)
                continue
            draw_start = max(resume, band_start)
            draw_end = min(end, band_end)
            local_0 = draw_start - band_start
            local_1 = draw_end - band_start
            mode = str(tail.get("text_mode") or "split")
            line_start = int(tail.get("line_start") or 0)
            if local_1 > local_0 and local_1 <= len(y_offsets) - 1:
                # 续页若仍会再拆，且模式为 full/none，保持；split 则继续拆
                if mode == "full" and draw_end < end:
                    # 后面还有段：若当初判定两边都能放，续段仍 full；否则本段已是唯一可放处
                    pass
                next_line = self._draw_cell_segment(
                    tail["cell"],
                    y_offsets[local_0],
                    y_offsets[local_1],
                    anchor_band,
                    draw_start,
                    draw_end,
                    band_heights,
                    draw_text=True,
                    text_mode=mode,
                    line_start=line_start,
                )
                if mode == "split":
                    line_start = next_line
            if draw_end < end:
                tail["resume_band"] = draw_end
                tail["line_start"] = line_start
                remaining.append(tail)
        return remaining

    def _title_font_size(self) -> float:
        raw = self.data.get("title_font_size_pt")
        if raw is not None and raw != "":
            try:
                return max(8.0, min(24.0, float(raw)))
            except (TypeError, ValueError):
                pass
        return EMBED_TITLE_FONT_SIZE

    def _draw_table_title(self) -> None:
        """在嵌套表正上方居中绘制表题；换行用紧凑行距（非正文 25 磅）。"""
        title = str(self.data.get("title") or self.data.get("caption") or "").strip()
        if not title:
            return
        from radiation_detection_report.f1_paragraph_layout import wrap_plain_lines
        from radiation_detection_report.generator import (
            _draw_line,
            _text_ascent,
        )

        title_fs = self._title_font_size()
        # 表题行距随字号，约 1.25 倍，不用 host 正文 25pt
        title_ls = max(10.0, title_fs * 1.25)
        inner_w = max(8.0, self.table_right - self.table_left - 2 * CELL_PAD_X)
        entries = wrap_plain_lines(title, inner_w, self.fonts, title_fs)
        n = max(1, len(entries))
        block_h = (n - 1) * title_ls + _text_ascent(title_fs) + title_fs * 0.22
        title_h = max(
            block_h + 2 * CELL_PAD_Y,
            float(self.data.get("title_height_pt") or 0) or 0.0,
            title_fs * 1.5,
        )
        if self.y + title_h > self._usable_bottom():
            self._page_break()
        rect = fitz.Rect(self.table_left, self.y, self.table_right, self.y + title_h)
        inner = fitz.Rect(
            rect.x0 + CELL_PAD_X,
            rect.y0 + CELL_PAD_Y,
            rect.x1 - CELL_PAD_X,
            rect.y1 - CELL_PAD_Y,
        )
        base_y = inner.y0 + max(0.0, (inner.height - block_h) / 2.0) + _text_ascent(title_fs)
        for li, (line, _) in enumerate(entries):
            _draw_line(
                self.page,
                line,
                base_y + li * title_ls,
                inner,
                self.fonts,
                title_fs,
                "center",
                bold=True,
            )
        self.y += title_h
        self.host.y = self.y
        parent = getattr(self.host, "_content_embed_parent", None)
        if parent is not None:
            parent.y = self.y
            parent.page = self.page
        self._sync_f1_table_frame()

    def run(self) -> None:
        self.page = self.host.page
        parent = getattr(self.host, "_content_embed_parent", None)
        if not self.defer_finish:
            self.band_top = self.host.y
            self.y = self.host.y + EMBED_CELL_PAD
        else:
            # 与父级（正文+表）共用同一 band，避免正文与嵌套表之间出现整行分隔线
            if parent is not None:
                self.band_top = parent.band_top
            self.y = max(self.y, self.host.y + EMBED_CELL_PAD)
        self.host.y = self.y

        anchors = self._anchor_cells()
        if not anchors:
            if not self.defer_finish:
                self._finish_band()
            self.host.y = self._segment_bottom()
            return

        band_count = self._band_count(anchors)
        band_heights = self._compute_band_heights(anchors, band_count)
        illegal = self._illegal_split_bands(anchors, band_count)

        # 表题与表体不分页：先确保本页能放下 表题+至少两行，再画表题
        title_h = 0.0
        if str(self.data.get("title") or self.data.get("caption") or "").strip():
            title_h = max(
                float(self.data.get("title_height_pt") or 0) or 0.0,
                self._title_font_size() * 1.8 + 2 * CELL_PAD_Y,
            )
        total_h = title_h + sum(band_heights)
        keep_n = min(2, band_count)
        keep_h = title_h + sum(band_heights[:keep_n])
        page_room = float(self.host.content_bottom - self.host.content_top)
        title_drawn = False
        rowspan_tails: List[Dict[str, Any]] = []

        band_start = 0
        while band_start < band_count:
            if band_start == 0 and not title_drawn:
                usable0 = self._usable_bottom() - self.y
                near_top = self.y <= float(self.host.content_top) + float(self.row_min_h) * 1.5
                if (
                    not near_top
                    and usable0 < keep_h
                    and total_h <= page_room + 1.0
                ):
                    self._page_break()
                    continue
                # 再按「表题后剩余高度」预估首屏行数，避免画完表题只剩 1 行
                usable_after_title = self._usable_bottom() - self.y - title_h
                fit0 = self._pick_chunk_fit(
                    0, band_heights, max(0.0, usable_after_title), illegal, band_count
                )
                if (
                    fit0 == 1
                    and band_count > 1
                    and not near_top
                    and total_h <= page_room + 1.0
                ):
                    self._page_break()
                    continue
                self._draw_table_title()
                title_drawn = True

            chunk_heights = band_heights[band_start:]
            usable = self._usable_bottom() - self.y
            if usable < self.row_min_h:
                self._page_break()
                continue

            fit = self._pick_chunk_fit(
                band_start, chunk_heights, usable, illegal, band_count
            )
            if fit <= 0:
                self._page_break()
                continue

            # 仅能放下 1 行且后面还有行：若未在页顶，整段下移到下一页
            if (
                fit == 1
                and band_start + 1 < band_count
                and self.y > float(self.host.content_top) + float(self.row_min_h) * 1.5
                and sum(chunk_heights) <= page_room + 1.0
            ):
                self._page_break()
                continue

            y_offsets = [self.y]
            for h in chunk_heights[:fit]:
                y_offsets.append(y_offsets[-1] + h)
            band_end = band_start + fit

            rowspan_tails = self._draw_rowspan_tails(
                rowspan_tails, band_start, band_end, y_offsets, band_heights
            )

            new_tails: List[Dict[str, Any]] = []
            for band, cells in anchors:
                if band < band_start or band >= band_end:
                    continue
                for cell in cells:
                    tail = self._draw_anchor_cell(
                        cell, band, band_start, band_end, y_offsets, band_heights
                    )
                    if tail is not None:
                        new_tails.append(tail)
            rowspan_tails.extend(new_tails)

            self.y = y_offsets[-1]
            self.host.y = self.y
            if parent is not None:
                parent.y = self.y
                parent.page = self.page
            self._sync_f1_table_frame()
            band_start = band_end

        if not self.defer_finish:
            self._finish_band()
        self.host.y = self._segment_bottom()


def embed_generic_table(host: Any, row_def: Dict[str, Any], embed: Dict[str, Any]) -> None:
    table_data = _resolve_embed_table(host, embed)
    F1GenericTableEmbedBuilder(host, row_def, table_data, embed).run()
