"""
根据版式 JSON 自由添加检测点并生成「工作场所放射防护检测结果」表格 PDF。
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import fitz

FONT_SIZE_XIAO_SI = 12.0
FONT_SIZE_WU_HAO = 10.5
CELL_PAD_X = 2.0
CELL_PAD_Y = 2.0
PAGE_MARGIN_CM = 2.8
TABLE_BORDER_WIDTH = 0.9
TABLE_BORDER_COLOR = (0.0, 0.0, 0.0)

HAS_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z0-9]")
SYMBOL_RE = re.compile(r"[≤≥μ％°\u03bc]")

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR = os.path.join(PACKAGE_DIR, "fonts")
DEFAULT_LAYOUT_PATH = os.path.join(PACKAGE_DIR, "layout", "default_layout.json")


def default_layout_path() -> str:
    return DEFAULT_LAYOUT_PATH


def _cm_to_pt(cm: float) -> float:
    return float(cm) * 72.0 / 2.54


def page_left_margin_pt(cm: float = PAGE_MARGIN_CM) -> float:
    """页面左边距（pt），与 A4 页面设置「距左侧 2.8cm」一致。"""
    return _cm_to_pt(cm)


def _default_header_labels() -> Dict[str, str]:
    return {
        "point_id": "检测点\n编号",
        "location": "检测点位置",
        "result": "检测结果\n（μSv/h）",
        "standard": "标准要求\n（μSv/h）",
        "annual_dose": "年剂量估算\n（mSv）",
        "evaluation": "结果\n评价",
    }


def _three_cjk_column_width_pt(fonts: FontResources, fontsize: float = FONT_SIZE_XIAO_SI) -> float:
    """三汉字（小四）列宽 + 单元格内边距。"""
    sample = "汉" * 3
    return _measure_text(sample, fonts, fontsize) + 2 * CELL_PAD_X


def _evaluation_column_width_pt(fonts: FontResources, fontsize: float = FONT_SIZE_XIAO_SI) -> float:
    return _three_cjk_column_width_pt(fonts, fontsize)


def _annual_dose_column_width_pt(fonts: FontResources, fontsize: float = FONT_SIZE_XIAO_SI) -> float:
    """年剂量估算列表头宽 + 内边距。"""
    return _header_column_width_pt("年剂量估算\n（mSv）", fonts, fontsize)


def _header_column_width_pt(
    label: str,
    fonts: FontResources,
    fontsize: float = FONT_SIZE_XIAO_SI,
) -> float:
    """按表头各行最大字宽计算列宽（含单元格内边距）。"""
    text = str(label or "").strip()
    if not text:
        return 0.0
    lines = text.split("\n")
    max_w = max(_measure_text(line, fonts, fontsize) for line in lines)
    return max_w + 2 * CELL_PAD_X


def _relayout_columns_with_annual_dose(
    layout: TableLayout,
    fonts: FontResources,
    labels: Mapping[str, str],
) -> None:
    """双组表含年剂量列：收窄位置列，按表头为检测结果/标准要求/年剂量估算分配列宽。"""
    L = layout
    pid_x1 = float(L.col_point_id[1])
    table_right = float(L.table_x_right)
    min_loc_w = max(72.0, _cm_to_pt(2.5))

    result_w = _header_column_width_pt(labels.get("result", "检测结果\n（μSv/h）"), fonts)
    standard_w = _header_column_width_pt(labels.get("standard", "标准要求\n（μSv/h）"), fonts)
    annual_w = _header_column_width_pt(labels.get("annual_dose", "年剂量估算\n（mSv）"), fonts)
    eval_w = _evaluation_column_width_pt(fonts, FONT_SIZE_XIAO_SI)

    ev_x1 = table_right
    ev_x0 = ev_x1 - eval_w
    annual_x1 = ev_x0
    annual_x0 = annual_x1 - annual_w
    standard_x1 = annual_x0
    standard_x0 = standard_x1 - standard_w
    result_x1 = standard_x0
    result_x0 = result_x1 - result_w
    loc_x1 = result_x0

    if loc_x1 - pid_x1 < min_loc_w:
        loc_x1 = pid_x1 + min_loc_w
        result_x0 = loc_x1
        result_x1 = result_x0 + result_w
        standard_x0 = result_x1
        standard_x1 = standard_x0 + standard_w
        annual_x0 = standard_x1
        annual_x1 = annual_x0 + annual_w
        ev_x0 = annual_x1
        if ev_x0 + eval_w > table_right + 0.5:
            overflow = ev_x0 + eval_w - table_right
            shrink = result_w + standard_w + annual_w
            if shrink > 0 and overflow > 0:
                scale = max(0.85, (shrink - overflow) / shrink)
                result_w *= scale
                standard_w *= scale
                annual_w *= scale
                ev_x0 = table_right - eval_w
                annual_x1 = ev_x0
                annual_x0 = annual_x1 - annual_w
                standard_x1 = annual_x0
                standard_x0 = standard_x1 - standard_w
                result_x1 = standard_x0
                result_x0 = result_x1 - result_w
                loc_x1 = result_x0

    main_right = min(float(L.col_location_main[1]), loc_x1)
    if main_right <= pid_x1 + 8.0:
        main_right = pid_x1 + min(68.0, max(0.0, loc_x1 - pid_x1))

    L.col_location_main = (pid_x1, main_right)
    L.col_location_sub = (main_right, loc_x1)
    L.col_location_full = (pid_x1, loc_x1)
    L.col_result = (result_x0, result_x1)
    L.col_standard = (standard_x0, standard_x1)
    L.col_annual_dose = (annual_x0, annual_x1)
    L.col_evaluation = (ev_x0, ev_x1)


def _format_section_title_text(title: str) -> str:
    """章节标题：序号与正文之间保留一个空格，如 1.4 工作场所…。"""
    t = str(title or "").strip()
    m = re.match(r"^(\d+\.\d+)\s*(.+)$", t)
    if m:
        return f"{m.group(1)} {m.group(2).strip()}"
    return t


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
    section_title: str = "1.1 工作场所放射防护检测结果"
    title_x_left: float = 79.37
    title_x_right: float = 244.44
    title_y_top: float = 80.0
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
    col_annual_dose: Optional[Tuple[float, float]] = None
    col_evaluation: Tuple[float, float] = (489.10, 524.57)
    h_condition: float = 33.57
    h_header: float = 40.50
    h_simple: float = 21.75
    h_complex_sub: float = 22.0
    header_labels: Dict[str, str] = field(default_factory=dict)
    notes_prefix: str = "注："
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
            title_x_left=float(st.get("x_left", page_left_margin_pt())),
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
            notes_prefix=str(t.get("notes_row", {}).get("prefix") or "注："),
            repeat_header_on_new_page=bool(pag.get("repeat_header_on_new_page", True)),
            condition_on_first_page_only=bool(pag.get("include_condition_on_first_page_only", True)),
        )


@dataclass
class FontResources:
    """page 字体名用于绘制；metric 字体对象用于测宽（嵌入名无法 get_text_length）。"""

    song: str = "china-s"
    times: str = "Times-Roman"
    symbol: str = "china-s"
    song_path: Optional[str] = None
    times_path: Optional[str] = None
    symbol_path: Optional[str] = None
    song_metric: Optional[fitz.Font] = None
    times_metric: Optional[fitz.Font] = None
    symbol_metric: Optional[fitz.Font] = None

    def page_font(self, char: str) -> str:
        if HAS_CJK_RE.search(char):
            return self.song
        if LATIN_RE.search(char):
            return self.times
        if SYMBOL_RE.search(char) and self.symbol != self.song:
            return self.symbol
        return self.song

    def metric_font(self, char: str) -> fitz.Font:
        if HAS_CJK_RE.search(char) and self.song_metric:
            return self.song_metric
        if LATIN_RE.search(char) and self.times_metric:
            return self.times_metric
        if SYMBOL_RE.search(char) and self.symbol_metric:
            return self.symbol_metric
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
    symbol_path = _first_existing(
        [os.path.join(FONTS_DIR, "SEGUISYM.TTF"), os.path.join(FONTS_DIR, "seguisym.ttf")]
    )
    res.song_path = song_path
    res.times_path = times_path
    res.symbol_path = symbol_path
    if song_path:
        try:
            res.song_metric = fitz.Font(fontfile=song_path)
        except Exception:
            pass
    if times_path:
        try:
            res.times_metric = fitz.Font(fontfile=times_path)
        except Exception:
            pass
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
        times=base.times,
        symbol=base.symbol,
        song_path=base.song_path,
        times_path=base.times_path,
        symbol_path=base.symbol_path,
        song_metric=base.song_metric,
        times_metric=base.times_metric,
        symbol_metric=base.symbol_metric,
    )
    if base.song_path and _insert_font(page, "F_SONG", base.song_path):
        res.song = "F_SONG"
    if base.times_path and _insert_font(page, "F_TIMES", base.times_path):
        res.times = "F_TIMES"
    if base.symbol_path and _insert_font(page, "F_SYMBOL", base.symbol_path):
        res.symbol = "F_SYMBOL"
    return res


def _char_width(char: str, fonts: FontResources, fontsize: float) -> float:
    mf = fonts.metric_font(char)
    try:
        return float(mf.text_length(char, fontsize=fontsize))
    except Exception:
        return fontsize * (0.5 if LATIN_RE.search(char) else 1.0)


def _measure_text(text: str, fonts: FontResources, fontsize: float) -> float:
    return sum(_char_width(ch, fonts, fontsize) for ch in text)


def _protect_unit_tokens_for_wrap(text: str) -> str:
    """数字与单位之间插入 Word Joiner，避免「30」与「cm」拆到两行。"""
    joiner = "\u2060"
    return re.sub(
        r"(\d)(cm|mm|mA|mAs|kV)\b",
        lambda m: m.group(1) + joiner + m.group(2),
        str(text or ""),
        flags=re.I,
    )


def _wrap_text_lines(text: str, max_width: float, fonts: FontResources, fontsize: float) -> List[str]:
    if not text:
        return [""]
    lines: List[str] = []
    current = ""
    for ch in _protect_unit_tokens_for_wrap(text).replace("\n", ""):
        if ch == "\u2060":
            current += ch
            continue
        trial = current + ch
        if current and _measure_text(trial, fonts, fontsize) > max_width:
            lines.append(current.replace("\u2060", ""))
            current = ch
        else:
            current = trial
    if current:
        lines.append(current.replace("\u2060", ""))
    return lines or [""]


def _line_leading(fontsize: float) -> float:
    return fontsize * 1.35


def _text_ascent(fontsize: float) -> float:
    return fontsize * 0.85


def _cell_lines(text: str, max_width: float, fonts: FontResources, fontsize: float) -> List[str]:
    return [ln for ln, _ in _paragraph_line_entries(text, max_width, fonts, fontsize)]


def _paragraph_line_entries(
    text: str, max_width: float, fonts: FontResources, fontsize: float
) -> List[Tuple[str, bool]]:
    """返回 (行文本, 是否段落末行)。末行按 Word 规则左对齐。"""
    entries: List[Tuple[str, bool]] = []
    for para in text.split("\n"):
        wrapped = _wrap_text_lines(para, max_width, fonts, fontsize)
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
    return (n_lines - 1) * leading + ascent


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
    """现场记录表等仍按单元格描边；报告表改用统一网格线。"""
    page.draw_rect(rect, color=TABLE_BORDER_COLOR, width=TABLE_BORDER_WIDTH)


def _stroke_hline(page: fitz.Page, y: float, x0: float, x1: float) -> None:
    if x1 <= x0:
        return
    pad = TABLE_BORDER_WIDTH * 0.5
    page.draw_line(
        (x0 - pad, y),
        (x1 + pad, y),
        color=TABLE_BORDER_COLOR,
        width=TABLE_BORDER_WIDTH,
    )


def _stroke_vline(page: fitz.Page, x: float, y0: float, y1: float) -> None:
    if y1 <= y0:
        return
    pad = TABLE_BORDER_WIDTH * 0.5
    page.draw_line(
        (x, y0 - pad),
        (x, y1 + pad),
        color=TABLE_BORDER_COLOR,
        width=TABLE_BORDER_WIDTH,
    )


def _unique_sorted_xs(xs: Sequence[float]) -> List[float]:
    out: List[float] = []
    for x in sorted(float(v) for v in xs):
        if not out or abs(x - out[-1]) > 0.05:
            out.append(x)
    return out


def _inner_rect(rect: fitz.Rect) -> fitz.Rect:
    return fitz.Rect(
        rect.x0 + CELL_PAD_X,
        rect.y0 + CELL_PAD_Y,
        rect.x1 - CELL_PAD_X,
        rect.y1 - CELL_PAD_Y,
    )


def _draw_line(
    page: fitz.Page,
    line: str,
    baseline: float,
    inner: fitz.Rect,
    fonts: FontResources,
    fontsize: float,
    align: str,
    letter_gap: Optional[float] = None,
) -> None:
    max_w = max(8.0, inner.width)
    line_w = _measure_text(line, fonts, fontsize)
    extra_gap = letter_gap
    if extra_gap is None and align == "justify" and line_w < max_w and len(line) > 1:
        extra_gap = (max_w - line_w) / (len(line) - 1)
    if extra_gap is not None and extra_gap > 0:
        x = inner.x0
        for i, ch in enumerate(line):
            fn = fonts.page_font(ch)
            page.insert_text((x, baseline), ch, fontname=fn, fontsize=fontsize, color=(0, 0, 0))
            x += _char_width(ch, fonts, fontsize)
            if i < len(line) - 1:
                x += extra_gap
        return
    if align == "left":
        x = inner.x0
    else:
        x = inner.x0 + max(0.0, (inner.width - line_w) / 2.0)
    for ch in line:
        fn = fonts.page_font(ch)
        page.insert_text((x, baseline), ch, fontname=fn, fontsize=fontsize, color=(0, 0, 0))
        x += _char_width(ch, fonts, fontsize)


def _evaluation_text_color(text: str) -> Tuple[float, float, float]:
    try:
        from apps.core.pdf_fill_runtime_config import pdf_fill_is_test_mode

        if not pdf_fill_is_test_mode():
            return (0.0, 0.0, 0.0)
    except Exception:
        pass
    if str(text or "").strip() == "不合格":
        return (1.0, 0.0, 0.0)
    return (0.0, 0.0, 0.0)


def _draw_cell_content(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    fonts: FontResources,
    fontsize: float,
    align: str = "center",
    *,
    text_color: Tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> None:
    if not text:
        return
    inner = _inner_rect(rect)
    max_w = max(8.0, inner.width)
    if align == "word_justify":
        entries = _paragraph_line_entries(text, max_w, fonts, fontsize)
    else:
        entries = [(ln, False) for ln in _cell_lines(text, max_w, fonts, fontsize)]
    leading = _line_leading(fontsize)
    base_y = _first_baseline_y(inner, len(entries), fontsize)
    for li, (line, is_last_in_para) in enumerate(entries):
        baseline = base_y + li * leading
        if align == "word_justify":
            extra = None if is_last_in_para else _word_justify_extra_gap(line, max_w, fonts, fontsize)
            _draw_line_colored(page, line, baseline, inner, fonts, fontsize, "left", text_color, letter_gap=extra)
        else:
            _draw_line_colored(page, line, baseline, inner, fonts, fontsize, align, text_color)


def _draw_line_colored(
    page: fitz.Page,
    line: str,
    baseline: float,
    inner: fitz.Rect,
    fonts: FontResources,
    fontsize: float,
    align: str,
    text_color: Tuple[float, float, float],
    letter_gap: Optional[float] = None,
) -> None:
    max_w = max(8.0, inner.width)
    line_w = _measure_text(line, fonts, fontsize)
    extra_gap = letter_gap
    if extra_gap is None and align == "justify" and line_w < max_w and len(line) > 1:
        extra_gap = (max_w - line_w) / (len(line) - 1)
    if extra_gap is not None and extra_gap > 0:
        x = inner.x0
        for i, ch in enumerate(line):
            fn = fonts.page_font(ch)
            page.insert_text((x, baseline), ch, fontname=fn, fontsize=fontsize, color=text_color)
            x += _char_width(ch, fonts, fontsize)
            if i < len(line) - 1:
                x += extra_gap
        return
    if align == "left":
        x = inner.x0
    else:
        x = inner.x0 + max(0.0, (inner.width - line_w) / 2.0)
    for ch in line:
        fn = fonts.page_font(ch)
        page.insert_text((x, baseline), ch, fontname=fn, fontsize=fontsize, color=text_color)
        x += _char_width(ch, fonts, fontsize)


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
        self._table_section_needs_top = True
        self._apply_layout_overrides()

    def _include_annual_dose_column(self) -> bool:
        return bool(self.data.get("include_annual_dose_column"))

    def _annual_dose_header_label(self) -> str:
        labels = {**_default_header_labels(), **(self.layout.header_labels or {})}
        return labels.get("annual_dose", "年剂量估算\n（mSv）")

    def _apply_layout_overrides(self) -> None:
        """表题左距 2.8cm；编号/评价列三汉字宽；含年剂量列时收窄位置列并重排测量列宽。"""
        L = self.layout
        L.title_x_left = page_left_margin_pt()
        pid_x0 = float(L.table_x_left)
        pid_w = _three_cjk_column_width_pt(self.font_base, FONT_SIZE_XIAO_SI)
        pid_x1 = pid_x0 + pid_w
        L.col_point_id = (pid_x0, pid_x1)
        labels = {**_default_header_labels(), **(L.header_labels or {})}
        eval_w = _evaluation_column_width_pt(self.font_base, FONT_SIZE_XIAO_SI)
        ev_x1 = float(L.table_x_right)
        ev_x0 = ev_x1 - eval_w
        L.col_evaluation = (ev_x0, ev_x1)
        if self._include_annual_dose_column():
            _relayout_columns_with_annual_dose(L, self.font_base, labels)
        else:
            L.col_annual_dose = None
            L.col_location_main = (pid_x1, float(L.col_location_main[1]))
            L.col_location_full = (pid_x1, float(L.col_location_sub[1]))
            if float(L.col_standard[1]) > ev_x0 + 0.5:
                L.col_standard = (float(L.col_standard[0]), ev_x0)

    def _insert_page_watermark(self) -> None:
        if self.page is None:
            return
        try:
            from utils.pdf_merge import insert_report_page_watermark_bottom_layer

            insert_report_page_watermark_bottom_layer(
                self.page, self.layout.page_width, self.layout.page_height
            )
        except Exception:
            pass

    def _new_page(self, *, continuation: bool) -> None:
        self.page_index += 1
        self.page = self.doc.new_page(width=self.layout.page_width, height=self.layout.page_height)
        self._insert_page_watermark()
        self.fonts = _register_fonts(self.page, self.font_base)
        if continuation:
            self.y = self.layout.continuation_y_top
            if self.layout.repeat_header_on_new_page:
                self._table_section_needs_top = True
                self._draw_header_row()
            else:
                self._table_section_needs_top = True
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
        title = _format_section_title_text(L.section_title)
        x = float(L.table_x_left)
        baseline = float(L.title_y_top) + _text_ascent(FONT_SIZE_XIAO_SI)
        inner = fitz.Rect(x, L.title_y_top, L.table_x_right, L.title_y_top + L.title_height)
        _draw_line(self.page, title, baseline, inner, self.fonts, FONT_SIZE_XIAO_SI, "left")

    def _location_full_inner_width(self) -> float:
        L = self.layout
        return max(8.0, float(L.col_location_full[1] - L.col_location_full[0]) - 2 * CELL_PAD_X)

    def _location_sub_inner_width(self) -> float:
        L = self.layout
        return max(8.0, float(L.col_location_sub[1] - L.col_location_sub[0]) - 2 * CELL_PAD_X)

    def _location_main_inner_width(self) -> float:
        L = self.layout
        return max(8.0, float(L.col_location_main[1] - L.col_location_main[0]) - 2 * CELL_PAD_X)

    def _result_inner_width(self) -> float:
        L = self.layout
        return max(8.0, float(L.col_result[1] - L.col_result[0]) - 2 * CELL_PAD_X)

    def _annual_dose_inner_width(self) -> float:
        L = self.layout
        col = L.col_annual_dose
        if not col:
            return 0.0
        return max(8.0, float(col[1] - col[0]) - 2 * CELL_PAD_X)

    def _simple_row_height(self, point: Dict[str, Any]) -> float:
        loc = str(point.get("location", "") or "")
        loc_h = _autofit_row_height(
            loc,
            self._location_full_inner_width(),
            self.fonts,
            FONT_SIZE_XIAO_SI,
            min_height=self.layout.h_simple,
        )
        result_h = _autofit_row_height(
            str(point.get("result", "") or ""),
            self._result_inner_width(),
            self.fonts,
            FONT_SIZE_XIAO_SI,
            min_height=self.layout.h_simple,
        )
        annual_h = self.layout.h_simple
        if self._include_annual_dose_column():
            annual_h = _autofit_row_height(
                str(point.get("annual_dose_msv", "") or ""),
                self._annual_dose_inner_width(),
                self.fonts,
                FONT_SIZE_XIAO_SI,
                min_height=self.layout.h_simple,
            )
        return max(loc_h, result_h, annual_h)

    def _complex_row_plan(self, point: Dict[str, Any]) -> Tuple[float, List[float]]:
        subs = point.get("sub_rows") or []
        L = self.layout
        sub_heights: List[float] = []
        for sub in subs:
            sub_loc_h = _autofit_row_height(
                str(sub.get("location_sub", "") or ""),
                self._location_sub_inner_width(),
                self.fonts,
                FONT_SIZE_XIAO_SI,
                min_height=L.h_complex_sub,
            )
            sub_res_h = _autofit_row_height(
                str(sub.get("result", "") or ""),
                self._result_inner_width(),
                self.fonts,
                FONT_SIZE_XIAO_SI,
                min_height=L.h_complex_sub,
            )
            sub_heights.append(max(sub_loc_h, sub_res_h))
        main_h = _autofit_row_height(
            resolve_complex_location(point),
            self._location_main_inner_width(),
            self.fonts,
            FONT_SIZE_XIAO_SI,
            min_height=L.h_simple,
        )
        return max(sum(sub_heights), main_h), sub_heights

    def _col_rect(self, y0: float, y1: float, col: Tuple[float, float]) -> fitz.Rect:
        return fitz.Rect(col[0], y0, col[1], y1)

    def _table_column_xs_simple(self) -> List[float]:
        L = self.layout
        xs = [
            L.table_x_left,
            L.col_point_id[1],
            L.col_location_full[1],
            L.col_result[0],
            L.col_standard[0],
        ]
        if L.col_annual_dose:
            xs.append(L.col_annual_dose[0])
        xs.extend([L.col_evaluation[0], L.table_x_right])
        return _unique_sorted_xs(xs)

    def _table_column_xs_complex(self) -> List[float]:
        L = self.layout
        xs = [
            L.table_x_left,
            L.col_point_id[1],
            L.col_location_main[1],
            L.col_location_sub[1],
            L.col_result[0],
            L.col_standard[0],
        ]
        if L.col_annual_dose:
            xs.append(L.col_annual_dose[0])
        xs.extend([L.col_evaluation[0], L.table_x_right])
        return _unique_sorted_xs(xs)

    def _stroke_simple_row_grid(self, y0: float, y1: float, *, draw_top: bool = False) -> None:
        assert self.page is not None
        xs = self._table_column_xs_simple()
        for x in xs:
            _stroke_vline(self.page, x, y0, y1)
        if draw_top:
            _stroke_hline(self.page, y0, xs[0], xs[-1])
        _stroke_hline(self.page, y1, xs[0], xs[-1])

    def _stroke_complex_group_grid(
        self,
        y0: float,
        y1: float,
        internal_ys: Sequence[float],
        *,
        draw_top: bool = False,
    ) -> None:
        assert self.page is not None
        xs = self._table_column_xs_complex()
        for x in xs:
            _stroke_vline(self.page, x, y0, y1)
        if draw_top:
            _stroke_hline(self.page, y0, xs[0], xs[-1])
        pid_x1 = float(self.layout.col_point_id[1])
        main_div = float(self.layout.col_location_main[1])
        for cy in internal_ys:
            if y0 < float(cy) < y1:
                _stroke_hline(self.page, float(cy), xs[0], pid_x1)
                _stroke_hline(self.page, float(cy), main_div, xs[-1])
        _stroke_hline(self.page, y1, xs[0], xs[-1])

    def _stroke_full_width_row_grid(self, y0: float, y1: float, *, draw_top: bool = False) -> None:
        assert self.page is not None
        x0 = float(self.layout.table_x_left)
        x1 = float(self.layout.table_x_right)
        _stroke_vline(self.page, x0, y0, y1)
        _stroke_vline(self.page, x1, y0, y1)
        if draw_top:
            _stroke_hline(self.page, y0, x0, x1)
        _stroke_hline(self.page, y1, x0, x1)

    def _stroke_background_row_grid(self, y0: float, y1: float, *, draw_top: bool = False) -> None:
        assert self.page is not None
        L = self.layout
        x0 = float(L.table_x_left)
        x_mid = float(L.col_location_sub[1])
        x1 = float(L.table_x_right)
        _stroke_vline(self.page, x0, y0, y1)
        _stroke_vline(self.page, x_mid, y0, y1)
        _stroke_vline(self.page, x1, y0, y1)
        if draw_top:
            _stroke_hline(self.page, y0, x0, x1)
        _stroke_hline(self.page, y1, x0, x1)

    def _draw_header_row(self) -> None:
        self._ensure_space(self.layout.h_header)
        y0, y1 = self.y, self.y + self.layout.h_header
        L = self.layout
        labels = {**_default_header_labels(), **(L.header_labels or {})}
        cells = [
            (self._col_rect(y0, y1, L.col_point_id), labels.get("point_id", "检测点\n编号"), "center"),
            (fitz.Rect(L.col_location_full[0], y0, L.col_location_full[1], y1), labels.get("location", "检测点位置"), "left"),
            (self._col_rect(y0, y1, L.col_result), labels.get("result", "检测结果\n（μSv/h）"), "center"),
            (self._col_rect(y0, y1, L.col_standard), labels.get("standard", "标准要求\n（μSv/h）"), "center"),
        ]
        if L.col_annual_dose:
            cells.append(
                (self._col_rect(y0, y1, L.col_annual_dose), self._annual_dose_header_label(), "center")
            )
        cells.append(
            (self._col_rect(y0, y1, L.col_evaluation), labels.get("evaluation", "结果评价"), "center")
        )
        for rect, text, align in cells:
            _draw_cell_content(self.page, rect, text, self.fonts, FONT_SIZE_XIAO_SI, align=align)
        self._stroke_simple_row_grid(y0, y1, draw_top=self._table_section_needs_top)
        self._table_section_needs_top = False
        self.y = y1

    def _draw_condition_row(self, text: str) -> None:
        inner_w = max(8.0, self.layout.table_x_right - self.layout.table_x_left - 2 * CELL_PAD_X)
        row_h = _autofit_row_height(
            text, inner_w, self.fonts, FONT_SIZE_XIAO_SI, min_height=self.layout.h_condition
        )
        self._ensure_space(row_h)
        y0, y1 = self.y, self.y + row_h
        rect = fitz.Rect(self.layout.table_x_left, y0, self.layout.table_x_right, y1)
        _draw_cell_content(self.page, rect, text, self.fonts, FONT_SIZE_XIAO_SI, align="left")
        self._stroke_full_width_row_grid(y0, y1, draw_top=self._table_section_needs_top)
        self._table_section_needs_top = False
        self.y = y1

    def _draw_simple_row(self, point: Dict[str, Any]) -> None:
        row_h = self._simple_row_height(point)
        self._ensure_space(row_h)
        y0, y1 = self.y, self.y + row_h
        L = self.layout
        cells = [
            (self._col_rect(y0, y1, L.col_point_id), str(point.get("id", "")), "center"),
            (fitz.Rect(L.col_location_full[0], y0, L.col_location_full[1], y1), str(point.get("location", "")), "left"),
            (self._col_rect(y0, y1, L.col_result), str(point.get("result", "")), "center"),
            (self._col_rect(y0, y1, L.col_standard), str(point.get("standard", "≤2.5")), "center"),
        ]
        if L.col_annual_dose:
            cells.append(
                (
                    self._col_rect(y0, y1, L.col_annual_dose),
                    str(point.get("annual_dose_msv", "") or ""),
                    "center",
                )
            )
        cells.append(
            (self._col_rect(y0, y1, L.col_evaluation), str(point.get("evaluation", "合格")), "center")
        )
        for rect, text, align in cells:
            color = _evaluation_text_color(text) if rect.x0 >= L.col_evaluation[0] - 0.5 else (0.0, 0.0, 0.0)
            _draw_cell_content(
                self.page, rect, text, self.fonts, FONT_SIZE_XIAO_SI, align=align, text_color=color
            )
        self._stroke_simple_row_grid(y0, y1, draw_top=self._table_section_needs_top)
        self._table_section_needs_top = False
        self.y = y1

    def _complex_group_height(self, point: Dict[str, Any], sub_count: int) -> float:
        total_h, _ = self._complex_row_plan(point)
        return total_h

    def _draw_complex_point(self, point: Dict[str, Any]) -> None:
        subs = point.get("sub_rows") or []
        if not subs:
            self._draw_simple_row(point)
            return
        total_h, sub_heights = self._complex_row_plan(point)
        self._ensure_space(total_h)
        y0 = self.y
        y1 = y0 + total_h
        L = self.layout

        main_rect = fitz.Rect(L.col_location_main[0], y0, L.col_location_main[1], y1)
        location_text = resolve_complex_location(point)
        _draw_cell_content(self.page, main_rect, location_text, self.fonts, FONT_SIZE_XIAO_SI, align="left")

        cy = y0
        internal_ys: List[float] = []
        for i, sub in enumerate(subs):
            sh = sub_heights[i]
            sy0, sy1 = cy, cy + sh
            if i < len(subs) - 1:
                internal_ys.append(sy1)
            cells = [
                (self._col_rect(sy0, sy1, L.col_point_id), str(sub.get("id", "")), "center"),
                (fitz.Rect(L.col_location_sub[0], sy0, L.col_location_sub[1], sy1), str(sub.get("location_sub", "")), "left"),
                (self._col_rect(sy0, sy1, L.col_result), str(sub.get("result", "")), "center"),
                (self._col_rect(sy0, sy1, L.col_standard), str(sub.get("standard", "≤2.5")), "center"),
            ]
            if L.col_annual_dose:
                cells.append(
                    (
                        self._col_rect(sy0, sy1, L.col_annual_dose),
                        str(sub.get("annual_dose_msv", "") or point.get("annual_dose_msv", "") or ""),
                        "center",
                    )
                )
            cells.append(
                (self._col_rect(sy0, sy1, L.col_evaluation), str(sub.get("evaluation", "合格")), "center")
            )
            for rect, text, align in cells:
                color = _evaluation_text_color(text) if rect.x0 >= L.col_evaluation[0] - 0.5 else (0.0, 0.0, 0.0)
                _draw_cell_content(
                    self.page, rect, text, self.fonts, FONT_SIZE_XIAO_SI, align=align, text_color=color
                )
            cy = sy1
        self._stroke_complex_group_grid(
            y0,
            y1,
            internal_ys,
            draw_top=self._table_section_needs_top,
        )
        self._table_section_needs_top = False
        self.y = y1

    def _draw_background_row(self, bg: Dict[str, Any]) -> None:
        L = self.layout
        label = str(bg.get("label", "本底值（μSv/h）"))
        label_w = max(8.0, float(L.col_location_sub[1] - L.col_point_id[0]) - 2 * CELL_PAD_X)
        h = _autofit_row_height(label, label_w, self.fonts, FONT_SIZE_XIAO_SI, min_height=L.h_simple)
        self._ensure_space(h)
        y0, y1 = self.y, self.y + h
        label_rect = fitz.Rect(L.col_point_id[0], y0, L.col_location_sub[1], y1)
        value_rect = fitz.Rect(L.col_result[0], y0, L.col_evaluation[1], y1)
        _draw_cell_content(
            self.page,
            label_rect,
            str(bg.get("label", "本底值（μSv/h）")),
            self.fonts,
            FONT_SIZE_XIAO_SI,
            align="center",
        )
        _draw_cell_content(
            self.page,
            value_rect,
            str(bg.get("value", "")),
            self.fonts,
            FONT_SIZE_XIAO_SI,
            align="center",
        )
        self._stroke_background_row_grid(y0, y1, draw_top=self._table_section_needs_top)
        self._table_section_needs_top = False
        self.y = y1

    def _draw_notes_row(self, notes: Sequence[str]) -> None:
        body = "\n".join(str(n) for n in notes)
        prefix = str(self.layout.notes_prefix or "").strip()
        text = f"{prefix}\n{body}" if prefix and body and not body.startswith(prefix) else body
        inner_w = max(8.0, self.layout.table_x_right - self.layout.table_x_left - 2 * CELL_PAD_X)
        min_notes_h = _text_block_height(1, FONT_SIZE_WU_HAO) + 2 * CELL_PAD_Y
        row_h = _autofit_row_height(text, inner_w, self.fonts, FONT_SIZE_WU_HAO, min_height=min_notes_h)
        self._ensure_space(row_h)
        y0, y1 = self.y, self.y + row_h
        rect = fitz.Rect(self.layout.table_x_left, y0, self.layout.table_x_right, y1)
        _draw_cell_content(self.page, rect, text, self.fonts, FONT_SIZE_WU_HAO, align="left")
        self._stroke_full_width_row_grid(y0, y1, draw_top=self._table_section_needs_top)
        self._table_section_needs_top = False
        self.y = y1

    def _draw_points(self, points: Sequence[Dict[str, Any]]) -> None:
        for point in points:
            if point.get("type", "simple") == "complex":
                subs = [
                    sub
                    for sub in (point.get("sub_rows") or [])
                    if str(sub.get("result", "") or "").strip()
                ]
                if not subs:
                    continue
                if len(subs) != len(point.get("sub_rows") or []):
                    point = {**point, "sub_rows": subs}
                self._draw_complex_point(point)
            elif str(point.get("result", "") or "").strip():
                self._draw_simple_row(point)

    def _draw_followup_condition_row(self, condition_text: str) -> None:
        """第二组检测条件：紧接第一组表体续写，不强制分页、不重复表头。"""
        if condition_text:
            self._draw_condition_row(str(condition_text))

    def build(self) -> fitz.Document:
        self._new_page(continuation=False)
        if self.layout.condition_on_first_page_only:
            cond = self.data.get("condition", "")
            if cond:
                self._draw_condition_row(cond)
        self._draw_header_row()
        self._draw_points(self.data.get("points") or [])
        points_group2 = self.data.get("points_group2") or []
        if points_group2:
            cond2 = str(self.data.get("condition_group2", "") or "").strip()
            self._draw_followup_condition_row(cond2)
            self._draw_points(points_group2)
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
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    try:
        from radiation_detection_report.report_evaluation import apply_report_evaluations

        return apply_report_evaluations(data)
    except Exception:
        return data


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
