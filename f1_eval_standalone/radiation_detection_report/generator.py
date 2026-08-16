"""
根据版式 JSON 自由添加检测点并生成「工作场所放射防护检测结果」表格 PDF。
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import fitz

from radiation_detection_report.md_rich_text import (
    has_md_bold,
    has_md_rich,
    parse_bold_segments,
    parse_rich_segments,
    script_baseline_delta,
    script_font_size,
    strip_md_markup,
)

FONT_SIZE_XIAO_SI = 12.0
FONT_SIZE_WU_HAO = 10.5
CELL_PAD_X = 2.0
CELL_PAD_Y = 2.0

HAS_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z0-9]")
# 含勾选框等符号；SimSun 常缺 ☑，需走 Segoe UI Symbol
SYMBOL_RE = re.compile(r"[≤≥μ％°\u03bc□■☑☐☒✓√△▲]")

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR = os.path.join(PACKAGE_DIR, "fonts")
DEFAULT_LAYOUT_PATH = os.path.join(PACKAGE_DIR, "layout", "default_layout.json")


def default_layout_path() -> str:
    return DEFAULT_LAYOUT_PATH


def _first_existing(paths: Sequence[str]) -> Optional[str]:
    for path in paths:
        if path and os.path.exists(path):
            return path
    return None


def _insert_font(page: fitz.Page, fontname: str, fontfile: str) -> bool:
    try:
        page.insert_font(fontname=fontname, fontfile=fontfile)
        return True
    except Exception:
        return False


@dataclass
class TableLayout:
    page_width: float = 595.3
    page_height: float = 841.9
    section_title: str = "1.1工作场所放射防护检测结果"
    title_x_left: float = 79.44
    title_x_right: float = 244.44
    title_y_top: float = 76.13
    title_height: float = 14.0
    table_x_left: float = 70.73
    table_x_right: float = 524.57
    table_y_top: float = 90.12
    continuation_y_top: float = 72.0
    footer_margin_below_table: float = 92.0
    content_bottom: float = 750.0
    col_point_id: Tuple[float, float] = (70.73, 120.60)
    col_location_main: Tuple[float, float] = (120.60, 198.45)
    col_location_sub: Tuple[float, float] = (198.45, 326.00)
    col_location_full: Tuple[float, float] = (120.60, 326.00)
    col_result: Tuple[float, float] = (326.00, 410.80)
    col_standard: Tuple[float, float] = (410.80, 489.10)
    col_evaluation: Tuple[float, float] = (489.10, 524.57)
    h_condition: float = 33.57
    h_header: float = 40.50
    h_simple: float = 21.75
    h_complex_sub: float = 22.0
    header_labels: Dict[str, str] = field(default_factory=dict)
    repeat_header_on_new_page: bool = True
    condition_on_first_page_only: bool = True

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "TableLayout":
        t = data.get("table", {})
        cols = t.get("columns", {})
        pag = data.get("pagination", {})
        st = data.get("section_title", {})

        def col(name: str, default: Tuple[float, float]) -> Tuple[float, float]:
            c = cols.get(name, {})
            return (float(c.get("x0", default[0])), float(c.get("x1", default[1])))

        point_id = col("point_id", (70.73, 120.60))
        # 复杂点位左列与右子列必须分开；JSON 里 location_main 可能被简单行拉宽
        location_main = (
            float(cols.get("location_main", {}).get("x0", 120.6)),
            float(t.get("complex_location_main_right", cols.get("location_main", {}).get("x1", 198.45))),
        )
        if location_main[1] > 210.0:
            location_main = (location_main[0], 198.45)
        location_sub = col("location_sub", (198.45, 326.0))
        if location_sub[0] < location_main[1] - 0.5:
            location_sub = (location_main[1], location_sub[1])
        location_full = (location_main[0], location_sub[1])

        page_h = float(data.get("page_size", {}).get("height", 841.9))
        content_bottom = float(
            pag.get("content_bottom") or pag.get("table_max_bottom") or (page_h - 92.0)
        )
        footer_margin = float(pag.get("footer_margin_below_table") or (page_h - content_bottom))

        return cls(
            page_width=float(data.get("page_size", {}).get("width", 595.3)),
            page_height=page_h,
            section_title=str(st.get("text", "1.1工作场所放射防护检测结果")),
            title_x_left=float(st.get("x_left", 79.44)),
            title_x_right=float(st.get("x_right", 244.44)),
            title_y_top=float(st.get("y_top", 76.13)),
            table_x_left=float(t.get("x_left", 70.73)),
            table_x_right=float(t.get("x_right", 524.57)),
            table_y_top=float(t.get("y_top", 90.12)),
            continuation_y_top=float(pag.get("continuation_y_top", 72.0)),
            footer_margin_below_table=footer_margin,
            content_bottom=content_bottom,
            col_point_id=point_id,
            col_location_main=location_main,
            col_location_sub=location_sub,
            col_location_full=location_full,
            col_result=col("result", (326.00, 410.80)),
            col_standard=col("standard", (410.80, 489.10)),
            col_evaluation=col("evaluation", (489.10, 524.57)),
            h_condition=float(t.get("row_heights", {}).get("condition", 33.57)),
            h_header=float(t.get("row_heights", {}).get("header", 40.50)),
            h_simple=float(t.get("row_heights", {}).get("simple", 21.75)),
            h_complex_sub=float(t.get("row_heights", {}).get("complex_sub", 22.0)),
            header_labels=dict(t.get("header_labels", {})),
            repeat_header_on_new_page=bool(pag.get("repeat_header_on_new_page", True)),
            condition_on_first_page_only=bool(pag.get("include_condition_on_first_page_only", True)),
        )


@dataclass
class FontResources:
    """page 字体名用于绘制；metric 字体对象用于测宽（嵌入名无法 get_text_length）。"""

    song: str = "china-s"
    song_bold: str = "china-s"
    times: str = "Times-Roman"
    times_bold: str = "Times-Bold"
    symbol: str = "china-s"
    song_path: Optional[str] = None
    song_bold_path: Optional[str] = None
    times_path: Optional[str] = None
    symbol_path: Optional[str] = None
    song_metric: Optional[fitz.Font] = None
    song_bold_metric: Optional[fitz.Font] = None
    times_metric: Optional[fitz.Font] = None
    times_bold_metric: Optional[fitz.Font] = None
    symbol_metric: Optional[fitz.Font] = None

    def page_font(self, char: str, *, bold: bool = False) -> str:
        if HAS_CJK_RE.search(char):
            return self.song_bold if bold else self.song
        if LATIN_RE.search(char):
            return self.times_bold if bold else self.times
        if SYMBOL_RE.search(char) and self.symbol != self.song:
            return self.symbol
        return self.song_bold if bold else self.song

    def metric_font(self, char: str, *, bold: bool = False) -> fitz.Font:
        if HAS_CJK_RE.search(char):
            if bold and self.song_bold_metric:
                return self.song_bold_metric
            if self.song_metric:
                return self.song_metric
        if LATIN_RE.search(char):
            if bold and self.times_bold_metric:
                return self.times_bold_metric
            if self.times_metric:
                return self.times_metric
        if SYMBOL_RE.search(char) and self.symbol_metric:
            return self.symbol_metric
        if bold and self.song_bold_metric:
            return self.song_bold_metric
        if self.song_metric:
            return self.song_metric
        if self.times_metric:
            return self.times_metric
        return self.song_metric or self.times_metric or fitz.Font("china-s")


def _load_font_resources() -> FontResources:
    res = FontResources()
    song_path = _first_existing(
        [
            os.path.join(FONTS_DIR, "SIMSUN.TTC"),
            os.path.join(FONTS_DIR, "simsun.ttc"),
            os.path.join(FONTS_DIR, "SIMSUN.TTF"),
            r"C:\Windows\Fonts\simsun.ttc",
        ]
    )
    times_path = _first_existing(
        [
            os.path.join(FONTS_DIR, "TIMES.TTF"),
            os.path.join(FONTS_DIR, "times.ttf"),
            r"C:\Windows\Fonts\times.ttf",
        ]
    )
    # 表头等“加粗”统一用普通宋体，不另嵌粗体字库
    song_bold_path = None
    symbol_path = _first_existing(
        [
            os.path.join(FONTS_DIR, "SEGUISYM.TTF"),
            os.path.join(FONTS_DIR, "seguisym.ttf"),
            r"C:\Windows\Fonts\seguisym.ttf",
            r"C:\Windows\Fonts\SegoeUISymbol.ttf",
        ]
    )
    res.song_path = song_path
    res.song_bold_path = song_bold_path
    res.times_path = times_path
    res.symbol_path = symbol_path
    if song_path:
        try:
            res.song_metric = fitz.Font(fontfile=song_path)
        except Exception:
            pass
    if song_bold_path:
        try:
            res.song_bold_metric = fitz.Font(fontfile=song_bold_path)
        except Exception:
            pass
    if times_path:
        try:
            res.times_metric = fitz.Font(fontfile=times_path)
        except Exception:
            pass
    try:
        res.times_bold_metric = fitz.Font("tibo")
    except Exception:
        res.times_bold_metric = res.times_metric
    if symbol_path:
        try:
            res.symbol_metric = fitz.Font(fontfile=symbol_path)
        except Exception:
            pass
    return res


def _register_fonts(page: fitz.Page, base: FontResources) -> FontResources:
    """把 fonts/ 字体嵌入当前页，返回带 page 字体名的资源。"""
    res = FontResources(
        song=base.song,
        song_bold=base.song_bold,
        times=base.times,
        times_bold=base.times_bold,
        symbol=base.symbol,
        song_path=base.song_path,
        song_bold_path=base.song_bold_path,
        times_path=base.times_path,
        symbol_path=base.symbol_path,
        song_metric=base.song_metric,
        song_bold_metric=base.song_bold_metric,
        times_metric=base.times_metric,
        times_bold_metric=base.times_bold_metric,
        symbol_metric=base.symbol_metric,
    )
    if base.song_path and _insert_font(page, "F_SONG", base.song_path):
        res.song = "F_SONG"
    if base.song_bold_path and _insert_font(page, "F_SONG_BOLD", base.song_bold_path):
        res.song_bold = "F_SONG_BOLD"
    elif res.song != "china-s":
        res.song_bold = res.song
    if base.times_path and _insert_font(page, "F_TIMES", base.times_path):
        res.times = "F_TIMES"
    res.times_bold = "tibo"
    if base.symbol_path and _insert_font(page, "F_SYMBOL", base.symbol_path):
        res.symbol = "F_SYMBOL"
    return res


def _char_width(char: str, fonts: FontResources, fontsize: float, *, bold: bool = False) -> float:
    mf = fonts.metric_font(char, bold=bold)
    try:
        return float(mf.text_length(char, fontsize=fontsize))
    except Exception:
        return fontsize * (0.5 if LATIN_RE.search(char) else 1.0)


def _measure_text(
    text: str, fonts: FontResources, fontsize: float, *, bold: bool = False
) -> float:
    if has_md_rich(text):
        total = 0.0
        for seg, seg_bold, script in parse_rich_segments(text, default_bold=bold):
            fs = script_font_size(fontsize, script)
            for ch in seg:
                total += _char_width(ch, fonts, fs, bold=seg_bold)
        return total
    return sum(_char_width(ch, fonts, fontsize, bold=bold) for ch in text)


_LINEHEAD_PUNCT = set(
    "，。、；：！？）》」』】〉,.;:!?%‰℃）" "'" '"' "”’"
)


def _wrap_text_lines(
    text: str,
    max_width: float,
    fonts: FontResources,
    fontsize: float,
    *,
    bold: bool = False,
) -> List[str]:
    if not text:
        return [""]
    plain = text.replace("\n", "")
    if not has_md_rich(plain):
        lines: List[str] = []
        current = ""
        i = 0
        while i < len(plain):
            ch = plain[i]
            trial = current + ch
            if current and _measure_text(trial, fonts, fontsize, bold=bold) > max_width:
                # 行首禁则：能放下则并入；否则回退一字与标点同行（不超宽画出格线）
                if ch in _LINEHEAD_PUNCT:
                    if _measure_text(trial, fonts, fontsize, bold=bold) <= max_width + 0.05:
                        current = trial
                        i += 1
                        while i < len(plain) and plain[i] in _LINEHEAD_PUNCT:
                            t2 = current + plain[i]
                            if _measure_text(t2, fonts, fontsize, bold=bold) > max_width + 0.05:
                                break
                            current = t2
                            i += 1
                        lines.append(current)
                        current = ""
                        continue
                    if len(current) >= 2:
                        lines.append(current[:-1])
                        current = current[-1] + ch
                        i += 1
                        while i < len(plain) and plain[i] in _LINEHEAD_PUNCT:
                            t2 = current + plain[i]
                            if _measure_text(t2, fonts, fontsize, bold=bold) > max_width + 0.05:
                                break
                            current = t2
                            i += 1
                        continue
                lines.append(current)
                current = ch
            else:
                current = trial
            i += 1
        if current:
            lines.append(current)
        return lines or [""]

    from radiation_detection_report.md_rich_text import rich_segments_to_marked

    atoms: List[Tuple[str, bool, str]] = []
    for seg, seg_bold, script in parse_rich_segments(plain, default_bold=bold):
        if script:
            atoms.append((seg, seg_bold, script))
        else:
            for ch in seg:
                atoms.append((ch, seg_bold, ""))

    lines: List[str] = []
    current: List[Tuple[str, bool, str]] = []
    current_w = 0.0
    for atom in atoms:
        t, b, sc = atom
        fs = script_font_size(fontsize, sc)
        w = sum(_char_width(ch, fonts, fs, bold=b) for ch in t)
        if current and current_w + w > max_width:
            lines.append(rich_segments_to_marked(current))
            current = [atom]
            current_w = w
        else:
            current.append(atom)
            current_w += w
    if current:
        lines.append(rich_segments_to_marked(current))
    return lines or [""]


def _line_leading(fontsize: float) -> float:
    return fontsize * 1.35


def _text_ascent(fontsize: float) -> float:
    return fontsize * 0.85


def _cell_lines(
    text: str,
    max_width: float,
    fonts: FontResources,
    fontsize: float,
    *,
    bold: bool = False,
) -> List[str]:
    return [
        ln
        for ln, _ in _paragraph_line_entries(text, max_width, fonts, fontsize, bold=bold)
    ]


def _paragraph_line_entries(
    text: str,
    max_width: float,
    fonts: FontResources,
    fontsize: float,
    *,
    bold: bool = False,
) -> List[Tuple[str, bool]]:
    """返回 (行文本, 是否段落末行)。末行按 Word 规则左对齐。"""
    entries: List[Tuple[str, bool]] = []
    for para in text.split("\n"):
        wrapped = _wrap_text_lines(para, max_width, fonts, fontsize, bold=bold)
        for i, line in enumerate(wrapped):
            entries.append((line, i == len(wrapped) - 1))
    return entries or [("", True)]


def _word_justify_extra_gap(line: str, max_w: float, fonts: FontResources, fontsize: float) -> Optional[float]:
    """
    Word 式两端对齐：优先左对齐；仅当字距拉伸不大时才微调填满行宽。
    返回 None 表示该行左对齐。
    """
    if len(line) <= 1:
        return None
    line_w = _measure_text(line, fonts, fontsize)
    if line_w >= max_w * 0.88:
        return None
    gaps = len(line) - 1
    if gaps <= 0:
        return None
    need = max_w - line_w
    per_gap = need / gaps
    max_per_gap = max(0.6, fontsize * 0.08)
    if per_gap > max_per_gap:
        return None
    return per_gap


def _text_block_height(n_lines: int, fontsize: float) -> float:
    if n_lines <= 0:
        return 0.0
    leading = _line_leading(fontsize)
    ascent = _text_ascent(fontsize)
    descent = fontsize * 0.22
    # 含 descent，竖直居中时上下留白更对称
    return (n_lines - 1) * leading + ascent + descent


def _first_baseline_y(inner: fitz.Rect, n_lines: int, fontsize: float) -> float:
    block_h = _text_block_height(n_lines, fontsize)
    return inner.y0 + max(0.0, (inner.height - block_h) / 2.0) + _text_ascent(fontsize)


def _autofit_row_height(
    text: str,
    inner_width: float,
    fonts: FontResources,
    fontsize: float,
    min_height: float = 0.0,
) -> float:
    """按折行后的文本块高度计算行高（含单元格内边距）。"""
    if not str(text).strip():
        return max(min_height, fontsize + 2 * CELL_PAD_Y)
    lines = _cell_lines(str(text), max(8.0, inner_width), fonts, fontsize)
    block_h = _text_block_height(len(lines), fontsize) + 2 * CELL_PAD_Y
    return max(min_height, block_h)


def format_complex_location(name: str, distance: str = "") -> str:
    """复杂点位主格：名称与距离同一行，距离接在末尾（如 铅玻璃观察窗C外表面30cm）。"""
    name = name.strip()
    distance = distance.strip()
    if not distance:
        return name
    return f"{name}{distance}"


def resolve_complex_location(point: Dict[str, Any]) -> str:
    """支持 location 含换行，或 location_name + location_distance 字段。"""
    if point.get("location_distance") is not None:
        name = str(point.get("location_name") or point.get("location", "")).replace("\n", "").strip()
        return format_complex_location(name, str(point.get("location_distance", "")))
    return str(point.get("location", ""))


def _draw_cell_border(page: fitz.Page, rect: fitz.Rect) -> None:
    page.draw_rect(rect, color=(0, 0, 0), width=0.6)


def _inner_rect(rect: fitz.Rect) -> fitz.Rect:
    return fitz.Rect(
        rect.x0 + CELL_PAD_X,
        rect.y0 + CELL_PAD_Y,
        rect.x1 - CELL_PAD_X,
        rect.y1 - CELL_PAD_Y,
    )


def _draw_rich_segments(
    page: fitz.Page,
    segments: Sequence[Tuple[str, bool, str]],
    baseline: float,
    x_start: float,
    fonts: FontResources,
    fontsize: float,
    *,
    letter_gap: Optional[float] = None,
    color: Tuple[float, float, float] = (0, 0, 0),
) -> None:
    flat_parts: List[Tuple[str, bool, str]] = []
    for seg, bold, script in segments:
        flat_parts.append((seg, bold, script))
    flat = "".join(seg for seg, _, _ in flat_parts)
    if letter_gap is not None and letter_gap > 0 and len(flat) > 1:
        x = x_start
        idx = 0
        for seg, bold, script in flat_parts:
            fs = script_font_size(fontsize, script)
            dy = script_baseline_delta(fontsize, script)
            for ch in seg:
                fn = fonts.page_font(ch, bold=bold)
                page.insert_text(
                    (x, baseline + dy), ch, fontname=fn, fontsize=fs, color=color
                )
                x += _char_width(ch, fonts, fs, bold=bold)
                if idx < len(flat) - 1:
                    x += letter_gap
                idx += 1
        return
    x = x_start
    for seg, bold, script in flat_parts:
        fs = script_font_size(fontsize, script)
        dy = script_baseline_delta(fontsize, script)
        for ch in seg:
            fn = fonts.page_font(ch, bold=bold)
            page.insert_text(
                (x, baseline + dy), ch, fontname=fn, fontsize=fs, color=color
            )
            x += _char_width(ch, fonts, fs, bold=bold)


def _draw_line(
    page: fitz.Page,
    line: str,
    baseline: float,
    inner: fitz.Rect,
    fonts: FontResources,
    fontsize: float,
    align: str,
    letter_gap: Optional[float] = None,
    color: Tuple[float, float, float] = (0, 0, 0),
    *,
    bold: bool = False,
) -> None:
    max_w = max(8.0, inner.width)
    segments = parse_rich_segments(line, default_bold=bold)
    line_w = _measure_text(line, fonts, fontsize, bold=bold)
    extra_gap = letter_gap
    if extra_gap is None and align == "justify" and line_w < max_w:
        plain_len = sum(len(seg) for seg, _, _ in segments)
        if plain_len > 1:
            extra_gap = (max_w - line_w) / (plain_len - 1)
    if extra_gap is not None and extra_gap > 0:
        _draw_rich_segments(
            page, segments, baseline, inner.x0, fonts, fontsize, letter_gap=extra_gap, color=color
        )
        return
    if align == "left":
        x = inner.x0
    else:
        x = inner.x0 + max(0.0, (inner.width - line_w) / 2.0)
    _draw_rich_segments(page, segments, baseline, x, fonts, fontsize, color=color)


def _evaluation_text_color(text: str) -> Tuple[float, float, float]:
    return (1.0, 0.0, 0.0) if str(text).strip() == "不合格" else (0.0, 0.0, 0.0)


def _col_inner_width(col: Tuple[float, float]) -> float:
    return max(8.0, col[1] - col[0] - 2 * CELL_PAD_X)


def _autofit_cells_height(
    cells: Sequence[Tuple[str, float]],
    fonts: FontResources,
    fontsize: float,
    *,
    min_height: float,
) -> float:
    height = min_height
    for text, inner_w in cells:
        if not str(text).strip():
            continue
        height = max(
            height,
            _autofit_row_height(str(text), inner_w, fonts, fontsize, min_height=min_height),
        )
    return height


def normalize_band_heights(heights: Sequence[float], target: float) -> List[float]:
    """将多行带高按比例缩放，使总和与目标高度一致（避免合并格与子行底边错位）。"""
    if not heights:
        return []
    vals = [float(h) for h in heights]
    total = sum(vals)
    if total <= 0:
        even = target / len(vals)
        return [even] * len(vals)
    if abs(total - target) < 0.05:
        return vals
    scale = target / total
    scaled = [h * scale for h in vals]
    scaled[-1] += target - sum(scaled)
    return scaled


def _draw_table_hline(
    page: fitz.Page,
    y: float,
    x_left: float,
    x_right: float,
    *,
    width: float = 0.6,
) -> None:
    page.draw_line((x_left, y), (x_right, y), color=(0, 0, 0), width=width)


def _draw_cell_content(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    fonts: FontResources,
    fontsize: float,
    align: str = "center",
    color: Tuple[float, float, float] = (0, 0, 0),
    *,
    bold: bool = False,
    first_indent: bool = False,
    para_indents: Optional[Sequence[bool]] = None,
) -> None:
    if not text:
        return
    inner = _inner_rect(rect)
    max_w = max(8.0, inner.width)

    # 按段折行：逐段可独立首行缩进
    entries: List[Tuple[str, bool, bool, float]] = []  # line, is_para_last, is_para_first, indent_w
    paras = str(text).split("\n")
    for pi, para in enumerate(paras):
        want = first_indent
        if para_indents is not None and pi < len(para_indents):
            want = bool(para_indents[pi])
        indent_w = (2.0 * fontsize) if want else 0.0
        first_w = max(8.0, max_w - indent_w) if want else max_w
        if not para:
            entries.append(("", True, True, indent_w))
            continue
        from radiation_detection_report.md_rich_text import has_md_rich, rich_segments_to_marked, parse_rich_segments

        if not want:
            for i, (ln, last) in enumerate(
                _paragraph_line_entries(para, max_w, fonts, fontsize, bold=bold)
            ):
                entries.append((ln, last, i == 0, 0.0))
            continue

        atoms: List[Tuple[str, bool, str]] = []
        for seg, seg_bold, script in parse_rich_segments(para, default_bold=bold):
            if script:
                atoms.append((seg, seg_bold, script))
            else:
                for ch in seg:
                    atoms.append((ch, seg_bold, ""))
        lines_atoms: List[List[Tuple[str, bool, str]]] = []
        cur: List[Tuple[str, bool, str]] = []
        cur_w = 0.0
        line_i = 0
        for atom in atoms:
            t, b, sc = atom
            fs = script_font_size(fontsize, sc)
            w = sum(_char_width(ch, fonts, fs, bold=b) for ch in t)
            limit = first_w if line_i == 0 else max_w
            if cur and cur_w + w > limit:
                lines_atoms.append(cur)
                cur = [atom]
                cur_w = w
                line_i += 1
            else:
                cur.append(atom)
                cur_w += w
        if cur:
            lines_atoms.append(cur)
        if not lines_atoms:
            entries.append(("", True, True, indent_w))
        else:
            for i, la in enumerate(lines_atoms):
                if not has_md_rich(para) and not any(a[2] or a[1] for a in la):
                    marked = "".join(a[0] for a in la)
                else:
                    marked = rich_segments_to_marked(la)
                entries.append((marked, i == len(lines_atoms) - 1, i == 0, indent_w))

    leading = _line_leading(fontsize)
    block_h = _text_block_height(len(entries), fontsize)
    if block_h > inner.height:
        base_y = inner.y0 + _text_ascent(fontsize)
    else:
        base_y = _first_baseline_y(inner, len(entries), fontsize)
    for li, (line, is_last_in_para, is_first_in_para, indent_w) in enumerate(entries):
        baseline = base_y + li * leading
        if baseline > inner.y1 + fontsize * 0.1:
            break
        draw_inner = inner
        if is_first_in_para and indent_w > 0:
            draw_inner = fitz.Rect(inner.x0 + indent_w, inner.y0, inner.x1, inner.y1)
        if align == "word_justify":
            extra = None if is_last_in_para else _word_justify_extra_gap(line, max(8.0, draw_inner.width), fonts, fontsize)
            _draw_line(
                page,
                line,
                baseline,
                draw_inner,
                fonts,
                fontsize,
                "left",
                letter_gap=extra,
                color=color,
                bold=bold,
            )
        else:
            use_align = "left" if (is_first_in_para and indent_w > 0) else align
            _draw_line(
                page, line, baseline, draw_inner, fonts, fontsize, use_align, color=color, bold=bold
            )


class RadiationTableBuilder:
    def __init__(self, layout: TableLayout, report_data: Dict[str, Any]) -> None:
        self.layout = layout
        self.data = report_data
        self.doc = fitz.open()
        self.page: Optional[fitz.Page] = None
        self.font_base = _load_font_resources()
        self.fonts: FontResources = self.font_base
        self.y: float = 0.0
        self.page_index = 0
        self.title_drawn = False

    def _new_page(self, *, continuation: bool) -> None:
        self.page_index += 1
        self.page = self.doc.new_page(width=self.layout.page_width, height=self.layout.page_height)
        self.fonts = _register_fonts(self.page, self.font_base)
        if continuation:
            self.y = self.layout.continuation_y_top
            if self.layout.repeat_header_on_new_page:
                self._draw_header_row()
        else:
            if not self.title_drawn:
                self._draw_section_title()
                self.title_drawn = True
            self.y = self.layout.table_y_top

    def _ensure_space(self, height: float) -> None:
        if self.page is None:
            self._new_page(continuation=False)
            return
        if self.y + height > self.layout.content_bottom:
            self._new_page(continuation=True)

    def _draw_section_title(self) -> None:
        assert self.page is not None
        L = self.layout
        rect = fitz.Rect(L.title_x_left, L.title_y_top, L.table_x_right, L.title_y_top + L.title_height)
        _draw_cell_content(
            self.page, rect, L.section_title, self.fonts, FONT_SIZE_XIAO_SI, align="left"
        )

    def _col_rect(self, y0: float, y1: float, col: Tuple[float, float]) -> fitz.Rect:
        return fitz.Rect(col[0], y0, col[1], y1)

    def _draw_header_row(self) -> None:
        self._ensure_space(self.layout.h_header)
        y0, y1 = self.y, self.y + self.layout.h_header
        L = self.layout
        labels = L.header_labels or {
            "point_id": "序号",
            "location": "检测点位置",
            "result": "报出值D，μSv/h",
            "standard": "标准要求",
            "evaluation": "结果评价",
        }
        cells = [
            (self._col_rect(y0, y1, L.col_point_id), labels.get("point_id", "序号"), "center"),
            (fitz.Rect(L.col_location_full[0], y0, L.col_location_full[1], y1), labels.get("location", "检测点位置"), "center"),
            (self._col_rect(y0, y1, L.col_result), labels.get("result", "检测结果\n(μSv/h)"), "center"),
            (self._col_rect(y0, y1, L.col_standard), labels.get("standard", "标准要求\n(μSv/h)"), "center"),
            (self._col_rect(y0, y1, L.col_evaluation), labels.get("evaluation", "结果\n评价"), "center"),
        ]
        for rect, text, align in cells:
            _draw_cell_border(self.page, rect)
            _draw_cell_content(self.page, rect, text, self.fonts, FONT_SIZE_XIAO_SI, align=align)
        self.y = y1

    def _draw_condition_row(self, text: str) -> None:
        inner_w = max(8.0, self.layout.table_x_right - self.layout.table_x_left - 2 * CELL_PAD_X)
        row_h = _autofit_row_height(
            text, inner_w, self.fonts, FONT_SIZE_XIAO_SI, min_height=self.layout.h_condition
        )
        self._ensure_space(row_h)
        y0, y1 = self.y, self.y + row_h
        rect = fitz.Rect(self.layout.table_x_left, y0, self.layout.table_x_right, y1)
        _draw_cell_border(self.page, rect)
        _draw_cell_content(self.page, rect, text, self.fonts, FONT_SIZE_XIAO_SI, align="left")
        self.y = y1

    def _simple_row_height(self, point: Dict[str, Any]) -> float:
        L = self.layout
        return _autofit_cells_height(
            [
                (str(point.get("id", "")), _col_inner_width(L.col_point_id)),
                (str(point.get("location", "")), _col_inner_width(L.col_location_full)),
                (str(point.get("result", "")), _col_inner_width(L.col_result)),
                (str(point.get("standard", "≤2.5")), _col_inner_width(L.col_standard)),
                (str(point.get("evaluation", "合格")), _col_inner_width(L.col_evaluation)),
            ],
            self.fonts,
            FONT_SIZE_XIAO_SI,
            min_height=L.h_simple,
        )

    def _complex_sub_row_height(self, sub: Dict[str, Any]) -> float:
        L = self.layout
        return _autofit_cells_height(
            [
                (str(sub.get("location_sub", "")), _col_inner_width(L.col_location_sub)),
                (str(sub.get("result", "")), _col_inner_width(L.col_result)),
                (str(sub.get("standard", "≤2.5")), _col_inner_width(L.col_standard)),
                (str(sub.get("evaluation", "")), _col_inner_width(L.col_evaluation)),
            ],
            self.fonts,
            FONT_SIZE_XIAO_SI,
            min_height=L.h_complex_sub,
        )

    def _complex_layout_heights(
        self, point: Dict[str, Any], subs: Sequence[Dict[str, Any]]
    ) -> Tuple[float, List[float]]:
        L = self.layout
        sub_heights = [self._complex_sub_row_height(s) for s in subs]
        main_h = _autofit_row_height(
            resolve_complex_location(point),
            _col_inner_width(L.col_location_main),
            self.fonts,
            FONT_SIZE_XIAO_SI,
            min_height=L.h_complex_sub,
        )
        total_h = max(main_h, sum(sub_heights) if sub_heights else L.h_complex_sub)
        return total_h, normalize_band_heights(sub_heights, total_h)

    def _draw_simple_row(self, point: Dict[str, Any]) -> None:
        row_h = self._simple_row_height(point)
        self._ensure_space(row_h)
        y0, y1 = self.y, self.y + row_h
        L = self.layout
        eval_text = str(point.get("evaluation", ""))
        cells = [
            (self._col_rect(y0, y1, L.col_point_id), str(point.get("id", "")), "center", (0, 0, 0)),
            (fitz.Rect(L.col_location_full[0], y0, L.col_location_full[1], y1), str(point.get("location", "")), "left", (0, 0, 0)),
            (self._col_rect(y0, y1, L.col_result), str(point.get("result", "")), "center", (0, 0, 0)),
            (self._col_rect(y0, y1, L.col_standard), str(point.get("standard", "≤2.5")), "center", (0, 0, 0)),
            (self._col_rect(y0, y1, L.col_evaluation), eval_text, "center", _evaluation_text_color(eval_text)),
        ]
        for rect, text, align, color in cells:
            _draw_cell_border(self.page, rect)
            _draw_cell_content(self.page, rect, text, self.fonts, FONT_SIZE_XIAO_SI, align=align, color=color)
        _draw_table_hline(self.page, y1, L.table_x_left, L.table_x_right)
        self.y = y1

    def _draw_complex_point(self, point: Dict[str, Any]) -> None:
        subs = point.get("sub_rows") or []
        if not subs:
            self._draw_simple_row(point)
            return
        total_h, sub_heights = self._complex_layout_heights(point, subs)
        self._ensure_space(total_h)
        y0 = self.y
        y1 = y0 + total_h
        L = self.layout

        pid = str(point.get("id", ""))
        id_rect = fitz.Rect(L.col_point_id[0], y0, L.col_point_id[1], y1)
        main_rect = fitz.Rect(L.col_location_main[0], y0, L.col_location_main[1], y1)
        _draw_cell_border(self.page, id_rect)
        _draw_cell_border(self.page, main_rect)
        _draw_cell_content(self.page, id_rect, pid, self.fonts, FONT_SIZE_XIAO_SI, align="center")
        _draw_cell_content(
            self.page, main_rect, resolve_complex_location(point), self.fonts, FONT_SIZE_XIAO_SI, align="left"
        )

        sy = y0
        for i, (sub, sub_h) in enumerate(zip(subs, sub_heights)):
            sy0, sy1 = sy, sy + sub_h
            eval_text = str(sub.get("evaluation", ""))
            cells = [
                (fitz.Rect(L.col_location_sub[0], sy0, L.col_location_sub[1], sy1), str(sub.get("location_sub", "")), "left", (0, 0, 0)),
                (self._col_rect(sy0, sy1, L.col_result), str(sub.get("result", "")), "center", (0, 0, 0)),
                (self._col_rect(sy0, sy1, L.col_standard), str(sub.get("standard", "≤2.5")), "center", (0, 0, 0)),
                (self._col_rect(sy0, sy1, L.col_evaluation), eval_text, "center", _evaluation_text_color(eval_text)),
            ]
            for rect, text, align, color in cells:
                _draw_cell_border(self.page, rect)
                _draw_cell_content(self.page, rect, text, self.fonts, FONT_SIZE_XIAO_SI, align=align, color=color)
            if i < len(subs) - 1:
                _draw_table_hline(self.page, sy1, L.col_location_sub[0], L.table_x_right)
            sy = sy1
        _draw_table_hline(self.page, y1, L.table_x_left, L.table_x_right)
        self.y = y1

    def _background_row_height(self, bg: Dict[str, Any]) -> float:
        L = self.layout
        label_w = _col_inner_width((L.col_point_id[0], L.col_location_sub[1]))
        value_w = _col_inner_width((L.col_result[0], L.col_evaluation[1]))
        return _autofit_cells_height(
            [
                (str(bg.get("label", "本底值（μSv/h）")), label_w),
                (str(bg.get("value", "")), value_w),
            ],
            self.fonts,
            FONT_SIZE_XIAO_SI,
            min_height=L.h_simple,
        )

    def _draw_background_row(self, bg: Dict[str, Any]) -> None:
        h = self._background_row_height(bg)
        self._ensure_space(h)
        y0, y1 = self.y, self.y + h
        L = self.layout
        label_rect = fitz.Rect(L.col_point_id[0], y0, L.col_location_sub[1], y1)
        value_rect = fitz.Rect(L.col_result[0], y0, L.col_evaluation[1], y1)
        _draw_cell_border(self.page, label_rect)
        _draw_cell_border(self.page, value_rect)
        _draw_cell_content(self.page, label_rect, str(bg.get("label", "本底值（μSv/h）")), self.fonts, FONT_SIZE_XIAO_SI, align="center")
        _draw_cell_content(self.page, value_rect, str(bg.get("value", "")), self.fonts, FONT_SIZE_XIAO_SI, align="center")
        self.y = y1

    def _draw_notes_row(self, notes: Sequence[str]) -> None:
        text = "\n".join(str(n) for n in notes)
        inner_w = max(8.0, self.layout.table_x_right - self.layout.table_x_left - 2 * CELL_PAD_X)
        min_notes_h = _text_block_height(1, FONT_SIZE_WU_HAO) + 2 * CELL_PAD_Y
        row_h = _autofit_row_height(text, inner_w, self.fonts, FONT_SIZE_WU_HAO, min_height=min_notes_h)
        self._ensure_space(row_h)
        y0, y1 = self.y, self.y + row_h
        rect = fitz.Rect(self.layout.table_x_left, y0, self.layout.table_x_right, y1)
        _draw_cell_border(self.page, rect)
        _draw_cell_content(self.page, rect, text, self.fonts, FONT_SIZE_WU_HAO, align="left")
        self.y = y1

    def build(self) -> fitz.Document:
        self._new_page(continuation=False)
        if self.layout.condition_on_first_page_only:
            cond = self.data.get("condition", "")
            if cond:
                self._draw_condition_row(cond)
        self._draw_header_row()
        for point in self.data.get("points", []):
            if point.get("type", "simple") == "complex":
                self._draw_complex_point(point)
            else:
                self._draw_simple_row(point)
        bg = self.data.get("background")
        if bg:
            self._draw_background_row(bg)
        notes = self.data.get("notes") or []
        if notes:
            self._draw_notes_row(notes)
        return self.doc

    def save(self, output_path: str) -> None:
        doc = self.build()
        doc.save(output_path)
        doc.close()


def load_layout(path: str) -> TableLayout:
    with open(path, "r", encoding="utf-8") as f:
        return TableLayout.from_json(json.load(f))


def load_report_data(path: str) -> Dict[str, Any]:
    from radiation_detection_report.report_evaluation import apply_report_evaluations

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return apply_report_evaluations(data)


def generate_table_pdf(layout_path: str, data_path: str, output_pdf: str) -> None:
    layout = load_layout(layout_path)
    data = load_report_data(data_path)
    RadiationTableBuilder(layout, data).save(output_pdf)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate radiation detection result table PDF.")
    parser.add_argument("--layout", default=DEFAULT_LAYOUT_PATH, help="Layout JSON path")
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", default="radiation_table_output.pdf")
    args = parser.parse_args()
    generate_table_pdf(args.layout, args.data, args.output)
    print(f"PDF written: {args.output}")


if __name__ == "__main__":
    main()
