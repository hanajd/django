"""
Markdown 加粗（**text**）与上下标（^sup^ / ~sub~，pandoc 风格）解析。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

# (text, bold, script)  script ∈ {"", "sup", "sub"}
RichSegment = Tuple[str, bool, str]
BoldSegment = Tuple[str, bool]
BoldLine = List[BoldSegment]

_MD_BOLD = re.compile(r"\*\*([^*]+?)\*\*")
_MD_SUP = re.compile(r"\^([^\^\n]+?)\^")
_MD_SUB = re.compile(r"~([^~\n]+?)~")
_GRID_BOUNDARY = re.compile(r"^\+[\-:|\s]+\+")
_GRID_ROW_BOUNDARY = re.compile(r"^\+[-=]+\+")

SUP_SIZE_RATIO = 0.65
SUP_RISE_RATIO = 0.40
SUB_DROP_RATIO = 0.20


def strip_md_bold(text: str) -> str:
    return _MD_BOLD.sub(r"\1", text)


def strip_md_scripts(text: str) -> str:
    s = _MD_SUP.sub(r"\1", text or "")
    return _MD_SUB.sub(r"\1", s)


def strip_md_markup(text: str) -> str:
    return strip_md_scripts(strip_md_bold(text))


def has_md_bold(text: str) -> bool:
    return "**" in text and bool(_MD_BOLD.search(text))


def has_md_script(text: str) -> bool:
    t = text or ""
    return bool(_MD_SUP.search(t) or _MD_SUB.search(t))


def has_md_rich(text: str) -> bool:
    return has_md_bold(text) or has_md_script(text)


def parse_bold_segments(text: str) -> List[BoldSegment]:
    if not text:
        return [("", False)]
    segments: List[BoldSegment] = []
    pos = 0
    for m in _MD_BOLD.finditer(text):
        if m.start() > pos:
            segments.append((text[pos : m.start()], False))
        segments.append((m.group(1), True))
        pos = m.end()
    if pos < len(text):
        segments.append((text[pos:], False))
    return segments or [("", False)]


def _parse_script_in_plain(text: str, bold: bool) -> List[RichSegment]:
    """在不含 ** 的纯文本中解析 ^sup^ / ~sub~。"""
    if not text:
        return []
    if not has_md_script(text):
        return [(text, bold, "")]
    out: List[RichSegment] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "^":
            j = text.find("^", i + 1)
            if j > i + 1 and "\n" not in text[i + 1 : j]:
                out.append((text[i + 1 : j], bold, "sup"))
                i = j + 1
                continue
        if ch == "~":
            j = text.find("~", i + 1)
            if j > i + 1 and "\n" not in text[i + 1 : j]:
                out.append((text[i + 1 : j], bold, "sub"))
                i = j + 1
                continue
        # 普通字符：尽量合并
        j = i + 1
        while j < n and text[j] not in "^~":
            j += 1
        out.append((text[i:j], bold, ""))
        i = j
    return out or [(text, bold, "")]


def parse_rich_segments(text: str, *, default_bold: bool = False) -> List[RichSegment]:
    """解析加粗与上下标，返回 (text, bold, script)。"""
    if not text:
        return [("", default_bold, "")]
    if not has_md_rich(text):
        return [(text, default_bold, "")]
    out: List[RichSegment] = []
    for seg, bold in parse_bold_segments(text):
        # bold 段内再拆上下标；default_bold 仅在无 ** 时生效
        use_bold = bold or (default_bold and not has_md_bold(text))
        if bold:
            use_bold = True
        elif has_md_bold(text):
            use_bold = False
        else:
            use_bold = default_bold
        parts = _parse_script_in_plain(seg, use_bold if has_md_bold(text) else (bold or default_bold))
        # 修正：有 ** 时按 parse_bold 的 bold；无 ** 时用 default_bold
        fixed: List[RichSegment] = []
        for t, b, sc in parts:
            if has_md_bold(text):
                fixed.append((t, bold, sc))
            else:
                fixed.append((t, default_bold, sc))
        out.extend(fixed)
    return out or [("", default_bold, "")]


def segments_to_marked(segments: Sequence[BoldSegment]) -> str:
    out: List[str] = []
    for text, bold in segments:
        if not text:
            continue
        out.append(f"**{text}**" if bold else text)
    return "".join(out)


def rich_segments_to_marked(segments: Sequence[RichSegment]) -> str:
    out: List[str] = []
    for text, bold, script in segments:
        if not text:
            continue
        body = text
        if script == "sup":
            body = f"^{body}^"
        elif script == "sub":
            body = f"~{body}~"
        if bold:
            body = f"**{body}**"
        out.append(body)
    return "".join(out)


def script_font_size(fontsize: float, script: str) -> float:
    if script in ("sup", "sub"):
        return max(5.0, float(fontsize) * SUP_SIZE_RATIO)
    return float(fontsize)


def script_baseline_delta(fontsize: float, script: str) -> float:
    if script == "sup":
        return -float(fontsize) * SUP_RISE_RATIO
    if script == "sub":
        return float(fontsize) * SUB_DROP_RATIO
    return 0.0


def normalize_md_content_line(line: str) -> str:
    """去掉 pandoc 主表单元格包裹，还原嵌套表行。"""
    s = str(line).strip()
    if not s:
        return ""
    if not s.startswith("|"):
        return s
    parts = s.split("|")
    if len(parts) < 4:
        return s
    first_cell = parts[1]
    if not first_cell.strip() and len(first_cell) > 4:
        inner = "|".join(parts[2:-1] if parts[-1] == "" else parts[2:]).strip()
        if inner.startswith("|"):
            inner = inner[1:].strip()
        return f"| {inner}" if inner and not inner.startswith("|") else (inner or s)
    return s


def is_grid_boundary_line(line: str) -> bool:
    s = normalize_md_content_line(line)
    if not s:
        return False
    if _GRID_BOUNDARY.match(s):
        return True
    if _GRID_ROW_BOUNDARY.match(s):
        return True
    return bool(re.match(r"^\|\s*\+", s))


def is_table_data_line(line: str) -> bool:
    s = normalize_md_content_line(line)
    if not s:
        return False
    if is_grid_boundary_line(line):
        return False
    return s.startswith("|")


def html_escape_rich(text: str) -> str:
    """把含 ^ ^ / ~ ~ 的文本转为带 <sup>/<sub> 的 HTML（已对正文转义）。"""
    import html as _html

    if not text:
        return ""
    if not has_md_rich(text):
        return _html.escape(text).replace("\n", "<br>")
    # 按行处理，保留换行
    lines = str(text).split("\n")
    out_lines: List[str] = []
    for line in lines:
        if not has_md_rich(line):
            out_lines.append(_html.escape(line))
            continue
        parts: List[str] = []
        for seg, bold, script in parse_rich_segments(line):
            esc = _html.escape(seg)
            if bold:
                esc = f"<b>{esc}</b>"
            if script == "sup":
                esc = f"<sup>{esc}</sup>"
            elif script == "sub":
                esc = f"<sub>{esc}</sub>"
            parts.append(esc)
        out_lines.append("".join(parts))
    return "<br>".join(out_lines)


def html_to_marked(html: str) -> str:
    """
    编辑器 contenteditable HTML → 存盘标记文本。
    支持 <sup>/<sub>/<b>/<br>/<div>/<p>。
    """
    import html as _html
    import re as _re

    s = str(html or "")
    if not s.strip():
        return ""
    # 统一换行
    s = _re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = _re.sub(r"(?i)</div>\s*<div[^>]*>", "\n", s)
    s = _re.sub(r"(?i)</p>\s*<p[^>]*>", "\n", s)
    s = _re.sub(r"(?i)</div>", "\n", s)
    s = _re.sub(r"(?i)</p>", "\n", s)
    s = _re.sub(r"(?i)<div[^>]*>", "", s)
    s = _re.sub(r"(?i)<p[^>]*>", "", s)

    def _repl_sup(m: "_re.Match[str]") -> str:
        inner = _re.sub(r"<[^>]+>", "", m.group(1))
        return f"^{_html.unescape(inner)}^"

    def _repl_sub(m: "_re.Match[str]") -> str:
        inner = _re.sub(r"<[^>]+>", "", m.group(1))
        return f"~{_html.unescape(inner)}~"

    def _repl_b(m: "_re.Match[str]") -> str:
        inner = m.group(1)
        # 允许 bold 内再含已转换的标记
        return f"**{inner}**"

    s = _re.sub(r"(?is)<sup[^>]*>(.*?)</sup>", _repl_sup, s)
    s = _re.sub(r"(?is)<sub[^>]*>(.*?)</sub>", _repl_sub, s)
    s = _re.sub(r"(?is)<b[^>]*>(.*?)</b>", _repl_b, s)
    s = _re.sub(r"(?is)<strong[^>]*>(.*?)</strong>", _repl_b, s)
    s = _re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s)
    # 去掉多余空行
    s = _re.sub(r"\n{3,}", "\n\n", s)
    return s.strip("\n")


def marked_to_excel_rich(text: str):
    """
    标记文本 → openpyxl CellRichText（含真实上下标）。
    无富文本时返回普通 str。
    """
    from openpyxl.cell.rich_text import CellRichText, TextBlock
    from openpyxl.cell.text import InlineFont

    raw = str(text or "")
    if not has_md_rich(raw):
        return raw
    blocks: List[Any] = []
    # 按行拆，行间用 \n 接在前一段末尾
    lines = raw.split("\n")
    for li, line in enumerate(lines):
        segs = parse_rich_segments(line)
        for si, (seg, bold, script) in enumerate(segs):
            if not seg and not (li < len(lines) - 1 and si == len(segs) - 1):
                continue
            kwargs: Dict[str, Any] = {}
            if bold:
                kwargs["b"] = True
            if script == "sup":
                kwargs["vertAlign"] = "superscript"
            elif script == "sub":
                kwargs["vertAlign"] = "subscript"
            piece = seg
            if li < len(lines) - 1 and si == len(segs) - 1:
                piece = piece + "\n"
            if piece == "" and not kwargs:
                continue
            blocks.append(TextBlock(InlineFont(**kwargs), piece or ""))
    if not blocks:
        return raw
    # 若全部无 vertAlign/bold，仍可用普通字符串
    if all(
        (getattr(b.font, "vertAlign", None) in (None, "baseline"))
        and not getattr(b.font, "b", False)
        for b in blocks
    ):
        return raw
    return CellRichText(*blocks)


def excel_value_to_marked(val: Any) -> str:
    """Excel 单元格值 → 标记文本（导入时还原上下标）。"""
    if val is None:
        return ""
    try:
        from openpyxl.cell.rich_text import CellRichText, TextBlock
    except Exception:
        CellRichText = None  # type: ignore
        TextBlock = None  # type: ignore
    if CellRichText is not None and isinstance(val, CellRichText):
        parts: List[str] = []
        for block in val:
            if not isinstance(block, TextBlock):
                parts.append(str(block))
                continue
            t = str(block.text or "")
            font = block.font
            va = getattr(font, "vertAlign", None) if font is not None else None
            bold = bool(getattr(font, "b", False)) if font is not None else False
            if va == "superscript":
                t = f"^{t}^"
            elif va == "subscript":
                t = f"~{t}~"
            if bold:
                t = f"**{t}**"
            parts.append(t)
        return "".join(parts)
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    return str(val)
