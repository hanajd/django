"""
GBZ/T 表 F.1 正文段落：25 磅行距、首行缩进两字、
（1）（2）等编号条目同样仅首行缩进、段间不额外空行。
"""

from __future__ import annotations

import re
from typing import List, Sequence, Tuple

import fitz

from radiation_detection_report.generator import (
    FontResources,
    _draw_line,
    _inner_rect,
    _measure_text,
    _text_ascent,
)

# (行文本, 左侧缩进 pt)
F1LineEntry = Tuple[str, float]

_SECTION_HEADING = re.compile(
    r"^("
    r"\d+(?:\.\d+)+\s*"  # 1.1 / 1.1.1
    r"|第[一二三四五六七八九十百千万\d]+[章节条部分]\s*"
    r"|附录\s*[A-Za-z0-9]"
    r")"
)
# 一级标题「1.法律…」「2.主要标准」（半角/全角点后接标题字）
_TOP_SECTION = re.compile(r"^\d+\s*[.．、]\s*\S")
# （1）CT / (1)DR 等「仅序号+短标题」行（无句号/冒号长句）
_SHORT_ENUM_LABEL = re.compile(
    r"^[（(]\d+[）)]\s*\S.{0,24}$"
)
# （1）(1) 1）1、1． 一、；不含 1.1 这类章节号
_NUMBERED_LIST_START = re.compile(
    r"^("
    r"[（(]\d+[）)]"
    r"|\d+[）)、．]"
    r"|[一二三四五六七八九十百千万]+[、．.]"
    r")"
)


def is_section_heading(line: str) -> bool:
    """带序号的章节/条目标题（非正文段落）——此类永不首行缩进。"""
    s = line.strip()
    if not s:
        return False
    if s.endswith("。"):
        return False
    if _SECTION_HEADING.match(s):
        return True
    # 1.法律、法规… / 2.主要标准
    if _TOP_SECTION.match(s) and not re.match(r"^\d+[）)]", s):
        return True
    if re.match(r"^\d+\.\d+", s):
        return True
    if re.match(r"^表\s*\d", s) or (re.match(r"^图\s*\d", s) and len(s) < 60):
        return True
    return False


def is_short_enum_label(line: str) -> bool:
    """（1）CT 这类仅「序号+设备名」的短行，不缩进。"""
    s = line.strip()
    if not s or is_section_heading(s):
        return False
    if "工作原理" in s or "：" in s or ":" in s or "。" in s:
        return False
    if not numbered_list_marker(s):
        return False
    return bool(_SHORT_ENUM_LABEL.match(s)) or len(s) <= 28


def numbered_list_marker(text: str) -> str:
    """返回编号前缀；非编号条目返回空串。"""
    s = text.strip()
    if not s or is_section_heading(s):
        return ""
    m = _NUMBERED_LIST_START.match(s)
    return m.group(1) if m else ""


def is_numbered_list_start(text: str) -> bool:
    """正文中的带序号条目（与正文一样仅首行缩进）。"""
    return bool(numbered_list_marker(text))


def split_f1_paragraphs(text: str) -> List[str]:
    if not str(text).strip():
        return []
    raw = str(text).strip()
    if re.search(r"\n\s*\n", raw):
        parts = re.split(r"\n\s*\n+", raw)
    else:
        parts = raw.split("\n")
    out: List[str] = []
    for part in parts:
        flat = part.replace("\n", "").strip()
        if flat:
            out.append(flat)
    return out


def f1_first_line_indent(fonts: FontResources, font_size: float) -> float:
    return _measure_text("　　", fonts, font_size)


# 禁止落在行首的常见标点（尽量与上一字同处一行，但不允许画出单元格）
_LINEHEAD_PUNCT = set(
    "，。、；：！？）》」』】〉,.;:!?%‰℃）" "'" '"' "”’"
)


def _consume_first_wrapped_line(
    text: str,
    max_width: float,
    fonts: FontResources,
    font_size: float,
) -> Tuple[str, str]:
    """
    取第一行。行首禁则标点：能放入则并入本行；放不下则回退本行末字，
    与标点一起换到下一行——避免「。」单独占行，也避免标点超宽画出表格线。
    """
    if not text:
        return "", ""
    formed = ""
    consumed = 0
    for ch in text:
        trial = formed + ch
        if formed and _measure_text(trial, fonts, font_size) > max_width:
            break
        formed = trial
        consumed += 1
    rest = text[consumed:]
    while rest and rest[0] in _LINEHEAD_PUNCT:
        trial = formed + rest[0]
        if _measure_text(trial, fonts, font_size) <= max_width + 0.05:
            formed = trial
            rest = rest[1:]
            continue
        # 放不下：回退一字，与后续标点一并留给下一行
        if len(formed) >= 2:
            rest = formed[-1] + rest
            formed = formed[:-1]
        break
    rest = rest.lstrip()
    # 极端窄列：本行仍空则至少吃一字，避免死循环
    if not formed and rest:
        formed, rest = rest[0], rest[1:]
    return formed, rest


def wrap_paragraph(
    para: str,
    max_width: float,
    fonts: FontResources,
    font_size: float,
    *,
    first_indent: float = 0.0,
    hang_indent: float = 0.0,
    left_indent: float = 0.0,
) -> List[F1LineEntry]:
    """
    first_indent: 仅首行额外右移（正文首行缩进两字）。
    hang_indent: 续行相对首行再右移（一般不用）。
    left_indent: 整段左缩进。
    """
    entries: List[F1LineEntry] = []
    rest = para.replace("\n", "").strip()
    first_physical = True
    while rest:
        if first_physical:
            offset = left_indent + first_indent
        else:
            offset = left_indent + hang_indent
        line_w = max(8.0, max_width - offset)
        line, rest = _consume_first_wrapped_line(rest, line_w, fonts, font_size)
        if not line:
            break
        entries.append((line, offset))
        first_physical = False
    return entries


def wrap_plain_lines(
    text: str,
    max_width: float,
    fonts: FontResources,
    font_size: float,
) -> List[F1LineEntry]:
    """按显式换行拆分并自动折行，无首行缩进（用于居中选项等非正文）。"""
    if not str(text).strip():
        return [("", 0.0)]
    entries: List[F1LineEntry] = []
    for raw_line in str(text).split("\n"):
        rest = raw_line.strip()
        if not rest:
            continue
        while rest:
            line, rest = _consume_first_wrapped_line(rest, max(8.0, max_width), fonts, font_size)
            if not line:
                break
            entries.append((line, 0.0))
            rest = rest.lstrip()
    return entries or [("", 0.0)]


def f1_paragraph_line_entries(
    text: str,
    max_width: float,
    fonts: FontResources,
    font_size: float,
    *,
    first_indent: bool = True,
    para_indents: Sequence[bool] | None = None,
) -> List[F1LineEntry]:
    """
    按段排版。章节标题 /（1）CT 短标永不缩进；
    其余优先尊重 para_indents，否则跟随 first_indent。
    """
    entries: List[F1LineEntry] = []
    paras = split_f1_paragraphs(text)
    two_chars = f1_first_line_indent(fonts, font_size)
    for i, para in enumerate(paras):
        # 标题类、设备短标：始终不缩进（即使用户全局开了首行缩进）
        if is_section_heading(para) or is_short_enum_label(para):
            want = False
        elif para_indents is not None and i < len(para_indents):
            want = bool(para_indents[i])
        else:
            want = bool(first_indent)

        indent = two_chars if want else 0.0
        entries.extend(
            wrap_paragraph(
                para,
                max_width,
                fonts,
                font_size,
                first_indent=indent,
                hang_indent=0.0,
                left_indent=0.0,
            )
        )
    return entries or [("", 0.0)]


def draw_f1_paragraph_entries(
    page,
    rect,
    entries: Sequence[F1LineEntry],
    fonts: FontResources,
    font_size: float,
    line_spacing: float,
    *,
    align: str = "left",
    vertical_center: bool = False,
) -> None:
    if not entries:
        return
    inner = _inner_rect(rect)
    if vertical_center:
        descent = font_size * 0.22
        block_h = (len(entries) - 1) * line_spacing + _text_ascent(font_size) + descent
        base_y = inner.y0 + max(0.0, (inner.height - block_h) / 2.0) + _text_ascent(font_size)
    else:
        base_y = inner.y0 + _text_ascent(font_size)
    for li, (line, offset) in enumerate(entries):
        baseline = base_y + li * line_spacing
        # 兼容旧数据：bool True/False 曾表示是否两字首行缩进
        if isinstance(offset, bool):
            pad = f1_first_line_indent(fonts, font_size) if offset else 0.0
        else:
            pad = float(offset or 0.0)
        x0 = inner.x0 + pad
        line_inner = fitz.Rect(x0, inner.y0, inner.x1, inner.y1)
        _draw_line(page, line, baseline, line_inner, fonts, font_size, align)
