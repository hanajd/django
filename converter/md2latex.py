#!/usr/bin/env python3
"""Convert Markdown reports to LaTeX (single file or split chapters)."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass
from html import unescape as html_unescape
from html.parser import HTMLParser
from pathlib import Path

# Legacy multi-hash headings (## / ### / ####)
LEGACY_CHAPTER_RE = re.compile(r"^##\s+(\d[\d\.]*)\s+(.+)$")
LEGACY_SUBSECTION_RE = re.compile(r"^###\s+(.+)$")
LEGACY_SUBSUBSECTION_RE = re.compile(r"^####\s+(.+)$")
LEGACY_PARAGRAPH_RE = re.compile(r"^#####\s+(.+)$")
LEGACY_ATTACHMENT_STOP_RE = re.compile(r"^##\s+附件")

HASH_HEADING_RE = re.compile(r"^#\s+(.+)$")
HEADING_LINE_RE = re.compile(r"^#{1,6}\s+(.+)$")
TABLE_CAPTION_RE = re.compile(r"^表\s*[\d\\.\-]+\s+(.+)$")
FIGURE_CAPTION_RE = re.compile(r"^图\s*([\d\\.\-]+(?:\.\d+-\d+)?)\s+(.+)$")
FIGURE_REF_SEE_RE = re.compile(
    r"见图\s*([\d\\.\-]+(?:-\d+(?:\.\d+-\d+)?)?)\s*(?:所示)?"
)
FIGURE_REF_AS_RE = re.compile(r"如图\s*([\d\\.\-]+(?:-\d+)?)\s*所示")
FIGURE_REF_SHOWN_RE = re.compile(r"图\s*([\d\\.\-]+(?:-\d+(?:\.\d+-\d+)?)?)\s+所示")
FIGURE_LATEX_REF_RE = re.compile(r"图~\\ref\{[^}]+\}")
LATEX_CMD_RE = re.compile(r"~?\\[a-zA-Z@]+(?:\[[^\]]*\])?(?:\{[^{}]*\})*")
TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$")
ESCAPED_TABLE_ROW_RE = re.compile(r"^\\\|(.+)\\\|\s*$")
ORDERED_LIST_RE = re.compile(r"^(\d+)\.\s+(.+)$")
CN_ORDERED_LIST_RE = re.compile(r"^[（(](\d+)[）)]\s*(.+)$")
BULLET_LIST_RE = re.compile(r"^-\s+(.+)$")
BLOCKQUOTE_RE = re.compile(r"^>\s*(.*)$")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
HTML_TABLE_RE = re.compile(r"<table\b.*?</table>", re.IGNORECASE | re.DOTALL)
SECTION_NUM_RE = re.compile(r"^[\d]+(?:\.[\d]+)*(?:、[\d]+(?:\.[\d]+)*)?\s+")

VARIABLE_VALUE_TO_CMD: dict[str, str] = {}

CHAPTER1_HEADER = r"""% ======================================================================
% 正文 第1章
% ======================================================================
\clearpage
\pagenumbering{arabic}
\setcounter{page}{1}

\pagestyle{fancy}
\fancyhf{}
\lhead{\zihao{-5}\songti \buildunit\project\reporttype}
\rhead{\zihao{-5}\songti \reportno}
\lfoot{\zihao{-5}\songti \company\quad 编制}
\rfoot{\zihao{-5}\songti 第\thepage\ 页 \quad 共\pageref{LastPage} 页}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}

\fancypagestyle{plain}{%
    \fancyhf{}%
    \lhead{\zihao{-5}\songti \buildunit\project\reporttype}%
    \rhead{\zihao{-5}\songti \reportno}%
    \lfoot{\zihao{-5}\songti \company\quad 编制}%
    \rfoot{\zihao{-5}\songti 第\thepage\ 页 \quad 共\pageref{LastPage} 页}%
    \renewcommand{\headrulewidth}{0pt}%
    \renewcommand{\footrulewidth}{0pt}%
}

\zihao{-4}\songti

"""

APPENDIX_HEADER = r"""% ======================================================================
% 附件
% ======================================================================
\clearpage
\phantomsection
\addcontentsline{toc}{chapter}{附件}

\renewcommand{\thechapter}{}
\setcounter{chapter}{0}
\renewcommand{\thesection}{附件\arabic{section}}
\setcounter{section}{0}

\chapter*{附件}
\markboth{附件}{附件}

"""

CHAPTER_OUTPUT_FILES = {
    1: "04-chapter1.tex",
    2: "05-chapter2.tex",
    3: "06-chapter3.tex",
    4: "07-chapter4.tex",
    5: "08-chapter5.tex",
    6: "09-chapter6.tex",
    7: "10-chapter7.tex",
    8: "11-chapter8.tex",
    9: "12-chapter9.tex",
    10: "13-appendix.tex",
}


def strip_section_number(title: str) -> str:
    title = unescape_md(title)
    return SECTION_NUM_RE.sub("", title, count=1).strip()


def unescape_md(text: str) -> str:
    return (
        text.replace(r"\.", ".")
        .replace(r"\-", "-")
        .replace(r"\+", "+")
        .replace(r"\(", "(")
        .replace(r"\)", ")")
        .replace(r"\[", "[")
        .replace(r"\]", "]")
        .replace(r"\|", "|")
    )


def sanitize_text(text: str) -> str:
    """Remove null bytes and control characters that break LaTeX."""
    text = text.replace("\x00", "")
    return "".join(ch for ch in text if ch in "\n\t\r" or ord(ch) >= 32)


def figure_num_to_label(num: str) -> str:
    normalized = re.sub(r"\s+", "", num).replace(".", "-")
    return f"fig:{normalized}"


def strip_md_emphasis(text: str) -> str:
    text = unescape_md(text.strip())
    text = re.sub(r"\*+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_table_caption_line(line: str) -> str | None:
    """Recognize report-style (表 2.1 xxx) and Word-style (bold title ending with 表/清单)."""
    if line.strip().startswith("#"):
        return None
    plain = strip_md_emphasis(line)
    if not plain:
        return None
    numbered = TABLE_CAPTION_RE.match(plain)
    if numbered:
        return numbered.group(1).strip()
    if len(plain) > 80:
        return None
    if plain.endswith("表") or plain.endswith("清单") or plain.endswith("情况"):
        return plain
    return None


def parse_heading_before_table_caption(line: str, lines: list[str], index: int) -> str | None:
    """Markdown heading immediately above a table → table caption (Word form style)."""
    match = HEADING_LINE_RE.match(line.strip())
    if not match or not next_nonempty_is_table(lines, index):
        return None
    plain = strip_md_emphasis(match.group(1))
    return plain if plain else None


def parse_figure_caption_line(line: str) -> tuple[str, str] | None:
    """Recognize report-style (图 2.1 xxx) and Word-style titles ending with 图."""
    if line.strip().startswith("#"):
        return None
    plain = strip_md_emphasis(line)
    if not plain:
        return None
    numbered = FIGURE_CAPTION_RE.match(plain)
    if numbered:
        return numbered.group(1), numbered.group(2).strip()
    if len(plain) > 80:
        return None
    if plain.endswith("图") and not re.match(r"^图[\d\\.\-]", plain):
        return "", plain
    return None


def extract_figure_refs(text: str) -> list[str]:
    nums: list[str] = []
    for pattern in (FIGURE_REF_SEE_RE, FIGURE_REF_AS_RE):
        for match in pattern.finditer(text):
            nums.append(match.group(1))
    return nums


def convert_figure_references(text: str) -> str:
    def replace_ref(match: re.Match[str]) -> str:
        label = figure_num_to_label(match.group(1))
        return rf"图~\ref{{{label}}}"

    text = FIGURE_REF_AS_RE.sub(replace_ref, text)
    text = FIGURE_REF_SEE_RE.sub(replace_ref, text)
    text = FIGURE_REF_SHOWN_RE.sub(replace_ref, text)
    return text


def normalize_math_spaces(text: str) -> str:
    """Collapse OCR-style spaced math like '$^ { 1 9 2 }$' -> '$^{192}$'."""

    def fix_math(match: re.Match[str]) -> str:
        inner = match.group(1)
        inner = inner.replace("\x00", "")
        inner = re.sub(r"\\operatorname\s*\*", r"\\operatorname*", inner)
        inner = re.sub(r"\s+", "", inner)
        return f"${inner}$"

    return re.sub(r"\$([^$]+)\$", fix_math, text)


def escape_latex_plain(text: str) -> str:
    for char, repl in (
        ("\\", r"\textbackslash{}"),
        ("&", r"\&"),
        ("%", r"\%"),
        ("#", r"\#"),
        ("_", r"\_"),
        ("{", r"\{"),
        ("}", r"\}"),
        ("~", r"\textasciitilde{}"),
        ("^", r"\textasciicircum{}"),
    ):
        text = text.replace(char, repl)
    return text


def escape_latex_plain_preserve_figure_refs(text: str) -> str:
    parts: list[str] = []
    idx = 0
    for match in LATEX_CMD_RE.finditer(text):
        if match.start() > idx:
            parts.append(escape_latex_plain(text[idx : match.start()]))
        parts.append(match.group(0))
        idx = match.end()
    if idx < len(text):
        parts.append(escape_latex_plain(text[idx:]))
    return "".join(parts)


def apply_variable_commands(text: str, value_to_cmd: dict[str, str]) -> str:
    """Replace plain keyword values with ``\\cmd``.

    Always emit ``\\cmd{}`` (empty braces). Under XeTeX/Unicode, CJK letters
    continue a control word, so bare ``\\sourcesummary房`` is a single undefined
    csname and the following Chinese disappears from the PDF.
    """
    if not value_to_cmd:
        return text
    for value, command in sorted(value_to_cmd.items(), key=lambda item: len(item[0]), reverse=True):
        if not value or value.strip() == "":
            continue
        cmd = command
        if cmd.startswith("\\") and not cmd.endswith("}"):
            # ``\foo`` → ``\foo{}``; leave ``\foo{bar}`` untouched
            cmd = cmd + "{}"
        text = text.replace(value, cmd)
    return text


def markdown_image_to_latex(path: str, width: str = r"0.9\linewidth") -> str:
    safe_path = path.replace("\\", "/")
    return rf"\includegraphics[width={width}]{{{safe_path}}}"


def convert_cell_content(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    parts: list[str] = []
    for line in lines:
        image_match = IMAGE_RE.fullmatch(line)
        if image_match:
            parts.append(markdown_image_to_latex(image_match.group(1)))
        else:
            parts.append(convert_inline(line))
    if all(IMAGE_RE.fullmatch(line) for line in lines):
        return r" \par ".join(parts)
    return r" \\ ".join(parts)


def convert_inline(text: str) -> str:
    text = normalize_math_spaces(sanitize_text(unescape_md(text)))

    tokens: list[tuple[str, str]] = []
    idx = 0
    pattern = re.compile(
        r"\$[^$]+\$|"
        r"\\(?:textbf|href)\{(?:[^{}]|\{[^{}]*\})*\}|"
        r"\*\*.+?\*\*|"
        r"!\[[^\]]*\]\([^)]+\)|"
        r"\[[^\]]+\]\([^)]+\)"
    )

    for match in pattern.finditer(text):
        if match.start() > idx:
            tokens.append(("plain", text[idx : match.start()]))
        tokens.append(("special", match.group(0)))
        idx = match.end()
    if idx < len(text):
        tokens.append(("plain", text[idx:]))

    parts: list[str] = []
    for kind, chunk in tokens:
        if kind == "plain":
            chunk = convert_figure_references(chunk)
            chunk = apply_variable_commands(chunk, VARIABLE_VALUE_TO_CMD)
            parts.append(escape_latex_plain_preserve_figure_refs(chunk))
            continue
        if chunk.startswith("**") and chunk.endswith("**"):
            parts.append(rf"\textbf{{{convert_inline(chunk[2:-2])}}}")
            continue
        image_match = IMAGE_RE.fullmatch(chunk)
        if image_match:
            parts.append(markdown_image_to_latex(image_match.group(1)))
            continue
        link_match = re.fullmatch(r"\[([^\]]+)\]\(([^)]+)\)", chunk)
        if link_match:
            label = convert_inline(link_match.group(1))
            url = escape_latex_plain(link_match.group(2))
            parts.append(rf"\href{{{url}}}{{{label}}}")
            continue
        parts.append(chunk)

    return "".join(parts)


@dataclass
class HtmlCell:
    text: str
    colspan: int = 1
    rowspan: int = 1
    is_header: bool = False


@dataclass
class MergeAnchor:
    row: int
    col: int
    colspan: int
    rowspan: int
    text: str


class HtmlTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[HtmlCell]] = []
        self._current_row: list[HtmlCell] = []
        self._current_cell: list[str] = []
        self._in_cell = False
        self._cell_colspan = 1
        self._cell_rowspan = 1
        self._cell_is_header = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._current_row = []
        elif tag in ("td", "th"):
            self._in_cell = True
            self._current_cell = []
            attr_map = {key.lower(): (value or "") for key, value in attrs}
            self._cell_colspan = max(1, int(attr_map.get("colspan", "1") or "1"))
            self._cell_rowspan = max(1, int(attr_map.get("rowspan", "1") or "1"))
            self._cell_is_header = tag == "th"

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("td", "th") and self._in_cell:
            text = html_unescape("".join(self._current_cell))
            text = re.sub(r"\s+", " ", text).strip()
            self._current_row.append(
                HtmlCell(
                    text=text,
                    colspan=self._cell_colspan,
                    rowspan=self._cell_rowspan,
                    is_header=self._cell_is_header,
                )
            )
            self._in_cell = False
        elif tag == "tr" and self._current_row:
            self.rows.append(self._current_row)

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell.append(data)


def parse_html_table(html: str) -> list[list[HtmlCell]]:
    parser = HtmlTableParser()
    parser.feed(html)
    return parser.rows


def parse_html_table_plain(html: str) -> list[list[str]]:
    return [[cell.text for cell in row] for row in parse_html_table(html)]


def html_table_has_merges(rows: list[list[HtmlCell]]) -> bool:
    return any(cell.colspan > 1 or cell.rowspan > 1 for row in rows for cell in row)


def html_rows_to_anchors(rows: list[list[HtmlCell]]) -> tuple[int, int, dict[tuple[int, int], MergeAnchor]]:
    occupied: set[tuple[int, int]] = set()
    anchors: dict[tuple[int, int], MergeAnchor] = {}

    for ri, row in enumerate(rows):
        ci = 0
        for cell in row:
            while (ri, ci) in occupied:
                ci += 1
            anchor = MergeAnchor(
                row=ri,
                col=ci,
                colspan=cell.colspan,
                rowspan=cell.rowspan,
                text=cell.text,
            )
            anchors[(ri, ci)] = anchor
            for r in range(ri, ri + cell.rowspan):
                for c in range(ci, ci + cell.colspan):
                    occupied.add((r, c))
            ci += cell.colspan

    if not occupied:
        return 0, 0, {}
    nrows = max(r for r, _ in occupied) + 1
    ncols = max(c for _, c in occupied) + 1
    return nrows, ncols, anchors


def _row_needs_hline_after(
    row_index: int,
    anchors: dict[tuple[int, int], MergeAnchor],
) -> bool:
    """Omit \\hline when the next row still continues an active vertical merge."""
    next_row = row_index + 1
    for anchor in anchors.values():
        if anchor.rowspan > 1 and anchor.row <= row_index:
            if anchor.row + anchor.rowspan > next_row:
                return False
    return True


def _cline_specs_after_merged_row(
    row_index: int,
    ncols: int,
    anchors: dict[tuple[int, int], MergeAnchor],
) -> list[str]:
    """
    When a full \\hline would cut through \\multirow, emit \\cline for free columns.
    Specs are 1-based inclusive ranges, e.g. ``2-4``.
    """
    covered: set[int] = set()
    for anchor in anchors.values():
        if anchor.rowspan <= 1:
            continue
        if anchor.row <= row_index < anchor.row + anchor.rowspan:
            # Still continuing into the next row → do not draw across these cols
            if anchor.row + anchor.rowspan - 1 > row_index:
                for c in range(anchor.col, anchor.col + max(1, anchor.colspan)):
                    if 0 <= c < ncols:
                        covered.add(c)
    free = [c for c in range(ncols) if c not in covered]
    if not free:
        return []
    specs: list[str] = []
    start = prev = free[0]
    for c in free[1:]:
        if c == prev + 1:
            prev = c
            continue
        specs.append(f"{start + 1}-{prev + 1}" if start != prev else f"{start + 1}-{start + 1}")
        start = prev = c
    specs.append(f"{start + 1}-{prev + 1}" if start != prev else f"{start + 1}-{start + 1}")
    return specs


def _format_merged_latex_cell(
    anchor: MergeAnchor,
    *,
    col_widths_cm: list[float] | None = None,
) -> str:
    text = convert_cell_content(anchor.text)
    widths = col_widths_cm or []

    def _span_width_cm(start: int, span: int) -> float:
        if start < 0 or not widths:
            return max(2.0, 1.8 * float(span))
        chunk = widths[start : start + max(1, span)]
        if len(chunk) == max(1, span):
            return round(sum(chunk), 2)
        return max(2.0, 1.8 * float(span))

    # Interior multicolumn: right rule only; use m{…} so long notes wrap.
    if anchor.colspan > 1:
        width_cm = _span_width_cm(anchor.col, anchor.colspan)
        align = f"|m{{{width_cm}cm}}|" if anchor.col == 0 else f"m{{{width_cm}cm}}|"
        body = text if text.lstrip().startswith("\\") else rf"\centering {text}"
        if anchor.rowspan > 1:
            inner = rf"\multirow{{{anchor.rowspan}}}{{=}}{{{body}}}"
            return rf"\multicolumn{{{anchor.colspan}}}{{{align}}}{{{inner}}}"
        return rf"\multicolumn{{{anchor.colspan}}}{{{align}}}{{{body}}}"
    if anchor.rowspan > 1:
        # ``=`` = column width (array/multirow); avoids ``*`` natural-width overflow
        body = text if text.lstrip().startswith("\\") else rf"\centering {text}"
        return rf"\multirow{{{anchor.rowspan}}}{{=}}{{{body}}}"
    return text


# Body text width for A4 + ~28mm margins is ~15.4cm; leave room for rules/sep.
_TABLE_USABLE_WIDTH_CM = 14.0


def _wrapping_column_widths_cm(ncols: int) -> list[float]:
    """Equal m-column widths that fit the page text block."""
    n = max(1, int(ncols))
    w = round(max(0.85, min(_TABLE_USABLE_WIDTH_CM / n, 4.5)), 2)
    widths = [w] * n
    drift = round(_TABLE_USABLE_WIDTH_CM - sum(widths), 2)
    widths[-1] = round(max(0.85, widths[-1] + drift), 2)
    return widths


def _wrapping_col_spec(ncols: int) -> tuple[str, list[float]]:
    widths = _wrapping_column_widths_cm(ncols)
    parts = [rf">{{\centering\arraybackslash}}m{{{w}cm}}" for w in widths]
    return "|" + "|".join(parts) + "|", widths


def merged_anchors_to_latex(
    nrows: int,
    ncols: int,
    anchors: dict[tuple[int, int], MergeAnchor],
    caption: str | None = None,
) -> str:
    """
    Serialize merged HTML anchors to a bordered tabular that fits the page.

    Uses equal ``m{…cm}`` columns (not ``c``), ``\\multirow{=}``, and ``\\cline``
    where a full ``\\hline`` would cut through vertical merges.
    """
    n = max(1, int(ncols))
    col_spec, col_widths = _wrapping_col_spec(n)
    lines = ["\\begin{table}[H]", "\\centering"]
    if caption:
        lines.append(f"\\caption{{{convert_inline(caption)}}}")
    # Wide tables: tighten padding so m-widths stay inside textwidth
    if n >= 6:
        lines.append("\\setlength{\\tabcolsep}{3pt}")
        lines.append("\\small")
    elif n >= 4:
        lines.append("\\setlength{\\tabcolsep}{4pt}")
    # Very wide grids: scale to textwidth after column math (sep + rules still overflow)
    scale_wide = n >= 8
    if scale_wide:
        lines.append("\\resizebox{\\textwidth}{!}{%")
    lines.extend([f"\\begin{{tabular}}{{{col_spec}}}", "\\hline"])

    skip: set[tuple[int, int]] = set()
    for anchor in anchors.values():
        if anchor.rowspan > 1:
            for r in range(anchor.row + 1, anchor.row + anchor.rowspan):
                for c in range(anchor.col, anchor.col + anchor.colspan):
                    skip.add((r, c))

    for ri in range(nrows):
        parts: list[str] = []
        ci = 0
        while ci < n:
            if (ri, ci) in skip:
                parts.append("")
                ci += 1
                continue
            anchor = anchors.get((ri, ci))
            if anchor is None:
                parts.append("")
                ci += 1
                continue
            parts.append(_format_merged_latex_cell(anchor, col_widths_cm=col_widths))
            ci += anchor.colspan
        if parts:
            lines.append(" & ".join(parts) + r" \\")
            if ri == nrows - 1:
                lines.append("\\hline")
            elif _row_needs_hline_after(ri, anchors):
                lines.append("\\hline")
            else:
                for spec in _cline_specs_after_merged_row(ri, n, anchors):
                    lines.append(rf"\cline{{{spec}}}")

    lines.append("\\end{tabular}")
    if scale_wide:
        lines.append("}")
    lines.append("\\end{table}")
    return "\n".join(lines)


def is_table_line(line: str) -> bool:
    stripped = line.strip()
    return bool(TABLE_ROW_RE.match(stripped) or ESCAPED_TABLE_ROW_RE.match(stripped))


def parse_md_table_row(line: str) -> list[str] | None:
    stripped = line.strip()
    match = ESCAPED_TABLE_ROW_RE.match(stripped) or TABLE_ROW_RE.match(stripped)
    if not match:
        return None
    row = match.group(1)
    raw_cells = [unescape_md(cell.strip().rstrip("\\").strip()) for cell in row.split("|")]
    if is_separator_row(raw_cells):
        return None
    return [convert_inline(cell) for cell in raw_cells]


def is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?\s*-{1,}\s*:?", cell) for cell in cells)


def table_to_latex(rows: list[list[str]], caption: str | None = None) -> str:
    if not rows:
        return ""

    max_cols = max(len(row) for row in rows)
    normalized = [row + [""] * (max_cols - len(row)) for row in rows]
    header = normalized[0]
    body = normalized[1:]
    col_spec, _widths = _wrapping_col_spec(max_cols)

    lines = ["\\begin{table}[H]", "\\centering"]
    if caption:
        lines.append(f"\\caption{{{convert_inline(caption)}}}")
    if max_cols >= 6:
        lines.append("\\setlength{\\tabcolsep}{3pt}")
        lines.append("\\small")
    elif max_cols >= 4:
        lines.append("\\setlength{\\tabcolsep}{4pt}")
    lines.extend([f"\\begin{{tabular}}{{{col_spec}}}", "\\hline"])
    lines.append(" & ".join(header) + r" \\")
    lines.append("\\hline")
    for row in body:
        lines.append(" & ".join(row) + r" \\")
        lines.append("\\hline")
    lines.extend(["\\end{tabular}", "\\end{table}"])
    return "\n".join(lines)


def html_table_to_latex(html: str, caption: str | None = None) -> str:
    rows = parse_html_table(html)
    if html_table_has_merges(rows):
        nrows, ncols, anchors = html_rows_to_anchors(rows)
        return merged_anchors_to_latex(nrows, ncols, anchors, caption)
    converted = [[convert_cell_content(cell.text) for cell in row] for row in rows]
    return table_to_latex(converted, caption)


def image_to_latex(
    path: str,
    caption: str | None = None,
    label: str | None = None,
    width: str = r"0.85\linewidth",
    *,
    page: str = "",
    a3_role: str = "only",
) -> str:
    """Serialize an image.

    ``a3_role`` controls wrappers for landscape A3 pages:
    - ``only``: single A3 page (Begin…End)
    - ``first`` / ``middle`` / ``last``: consecutive A3 run — stay on A3
      paper between pages; restore A4 only after ``last``.
    """
    if (page or "").strip() == "a3landscape":
        # Fill the A3 landscape text area; margins come from \BeginAThreeLandscapePage.
        safe_path = path.replace("\\", "/")
        role = (a3_role or "only").strip().lower()
        if role not in {"only", "first", "middle", "last"}:
            role = "only"
        lines: list[str] = []
        if role in {"middle", "last"}:
            # Next sheet in the same A3 run — do not restore A4.
            lines.append(r"\clearpage")
        if role in {"only", "first"}:
            lines.append(r"\BeginAThreeLandscapePage")
        # Fill A3 text area (margins already set by BeginAThree…); leave room for caption.
        lines.extend(
            [
                r"\begin{figure}[H]",
                r"\centering",
                rf"\includegraphics[width=\textwidth,height=\dimexpr\textheight-3\baselineskip\relax,keepaspectratio]{{{safe_path}}}",
            ]
        )
        if caption:
            lines.append(f"\\caption{{{convert_inline(caption)}}}")
        if label:
            lines.append(f"\\label{{{label}}}")
        lines.append(r"\end{figure}")
        if role in {"only", "last"}:
            lines.append(r"\EndAThreeLandscapePage")
        return "\n".join(lines)
    return figures_group_to_latex([path], caption, label, width)


def figures_group_to_latex(
    paths: list[str],
    caption: str | None = None,
    label: str | None = None,
    width: str = r"0.85\linewidth",
) -> str:
    if not paths:
        return ""
    lines = ["\\begin{figure}[H]", "\\centering"]
    if len(paths) == 1:
        widths = [width]
    elif len(paths) == 2:
        widths = [r"0.45\linewidth"] * 2
    else:
        widths = [r"0.32\linewidth"] * len(paths)
    for img_path, img_width in zip(paths, widths):
        safe_path = img_path.replace("\\", "/")
        lines.append(f"\\includegraphics[width={img_width}]{{{safe_path}}}")
    if caption:
        lines.append(f"\\caption{{{convert_inline(caption)}}}")
    if label:
        lines.append(f"\\label{{{label}}}")
    lines.append("\\end{figure}")
    return "\n".join(lines)


def flush_pending_figures(
    output: list[str],
    pending_paths: list[str],
    pending_labels: list[str | None],
    caption_info: tuple[str, str] | None = None,
) -> None:
    if not pending_paths:
        return

    caption: str | None = None
    label: str | None = None
    if caption_info:
        caption = caption_info[1]
        label = figure_num_to_label(caption_info[0]) if caption_info[0] else None
    if not label and pending_labels:
        label = pending_labels[0]
    if not label and len(pending_paths) == 1:
        label = f"fig:{Path(pending_paths[0]).stem[:20]}"

    output.append(figures_group_to_latex(pending_paths, caption, label))
    output.append("")

    pending_paths.clear()
    pending_labels.clear()


def clean_hash_title(raw: str) -> str:
    title = raw.strip()
    title = re.sub(r"\.{2,}.*$", "", title)
    title = title.rstrip(".")
    return title.strip()


def parse_numbered_hash_title(raw: str) -> tuple[int, str] | None:
    title = clean_hash_title(raw).replace(" ", "")
    match = re.match(r"^((?:\d+\.)*\d+)(.*)$", title)
    if not match:
        return None
    num_str, rest = match.group(1), match.group(2).lstrip(".")
    depth = len(num_str.split("."))
    return depth, rest or num_str


def classify_hash_heading(line: str, appendix_mode: bool) -> dict | None:
    match = HASH_HEADING_RE.match(line.strip())
    if not match:
        return None

    raw = match.group(1).strip()
    cleaned = clean_hash_title(raw)

    if cleaned in {"声明", "目录", "目 录", "地"} or cleaned == "":
        return {"action": "skip"}

    if cleaned.startswith("附件"):
        compact = cleaned.replace(" ", "")
        if re.fullmatch(r"附件\.?", compact) or compact.startswith("附件.."):
            return {"action": "appendix_main"}
        if re.match(r"^附件\d+-\d+", compact):
            title = re.sub(r"^附件\d+-\d+\s*", "", cleaned).strip()
            return {"action": "heading", "level": "subsection", "title": title or cleaned}
        if re.match(r"^附件\d+", compact):
            title = re.sub(r"^附件\d+\s*", "", cleaned).strip()
            return {"action": "heading", "level": "section", "title": title or cleaned}
        if appendix_mode:
            return {"action": "heading", "level": "subsubsection", "title": cleaned}

    numbered = parse_numbered_hash_title(raw)
    if numbered:
        depth, title = numbered
        if depth == 1:
            if appendix_mode or (title and title.startswith("、")):
                display = title.lstrip("、").strip() if title else cleaned
                return {
                    "action": "heading",
                    "level": "subsubsection",
                    "title": display,
                }
            num_match = re.match(r"^((?:\d+\.)*\d+)", clean_hash_title(raw).replace(" ", ""))
            chapter_no = int(num_match.group(1).split(".")[0]) if num_match else 0
            display = strip_section_number(title) if title else ""
            if not display:
                display = re.sub(r"^\d+\.?", "", clean_hash_title(raw)).strip()
            return {
                "action": "heading",
                "level": "chapter",
                "title": display,
                "chapter_no": chapter_no,
            }
        level_map = {2: "section", 3: "subsection", 4: "subsubsection"}
        if depth in level_map:
            return {
                "action": "heading",
                "level": level_map[depth],
                "title": strip_section_number(title),
            }
        if depth >= 5:
            return {"action": "heading", "level": "subsubsection", "title": strip_section_number(title)}

    if appendix_mode:
        return {"action": "heading", "level": "subsubsection", "title": cleaned}

    skip_titles = ("概述", "工程分析", "辐射源项分析")
    if any(cleaned.startswith(x) for x in skip_titles) and ".." in raw:
        return {"action": "skip"}

    return {"action": "skip"}


def detect_format(lines: list[str]) -> str:
    hash_count = sum(1 for line in lines if HASH_HEADING_RE.match(line.strip()))
    legacy_count = sum(
        1
        for line in lines
        if LEGACY_CHAPTER_RE.match(line.strip())
        or LEGACY_SUBSECTION_RE.match(line.strip())
    )
    return "hash" if hash_count > legacy_count else "legacy"


def find_body_start_hash(lines: list[str]) -> int:
    candidates = [
        i
        for i, line in enumerate(lines)
        if re.match(r"^#\s+1\.?\s*概述", line.strip()) or line.strip() in {"# 1.概述", "# 1. 概述"}
    ]
    for idx in candidates:
        for j in range(idx + 1, min(idx + 15, len(lines))):
            if re.match(r"^#\s+1\.1", lines[j].strip()):
                return idx
    if candidates:
        return candidates[-1]
    raise ValueError("Could not find body start (expected '# 1.概述' followed by '# 1.1...').")


def find_body_start_legacy(lines: list[str]) -> int:
    for i, line in enumerate(lines):
        if LEGACY_CHAPTER_RE.match(line.strip()):
            return i
    raise ValueError("Could not find body start (expected '## 1 ...').")


def extract_body(lines: list[str], fmt: str) -> list[str]:
    start = find_body_start_hash(lines) if fmt == "hash" else find_body_start_legacy(lines)
    body: list[str] = []
    for line in lines[start:]:
        if fmt == "legacy" and LEGACY_ATTACHMENT_STOP_RE.match(line.strip()):
            break
        body.append(line.rstrip("\n"))
    return body


class ListState:
    NONE = "none"
    ORDERED = "ordered"
    BULLET = "bullet"


def close_list(state: str, output: list[str]) -> str:
    if state == ListState.ORDERED:
        output.append("\\end{enumerate}")
    elif state == ListState.BULLET:
        output.append("\\end{itemize}")
    return ListState.NONE


def next_nonempty_line(lines: list[str], start: int) -> tuple[int, str] | None:
    for j in range(start, len(lines)):
        stripped = lines[j].strip()
        if stripped:
            return j, stripped
    return None


def next_nonempty_is_table(lines: list[str], start: int) -> bool:
    found = next_nonempty_line(lines, start + 1)
    if not found:
        return False
    _, text = found
    return text.lower().startswith("<table") or is_table_line(text)


def table_caption_before(lines: list[str], table_index: int) -> tuple[int, str] | None:
    j = table_index - 1
    while j >= 0:
        stripped = lines[j].strip()
        if not stripped:
            j -= 1
            continue
        if stripped.startswith("#"):
            caption = parse_heading_before_table_caption(stripped, lines, j)
            if caption:
                return j, caption
            return None
        caption = parse_table_caption_line(stripped)
        if caption:
            return j, caption
        return None
    return None


def is_ordered_item(line: str) -> bool:
    return bool(ORDERED_LIST_RE.match(line) or CN_ORDERED_LIST_RE.match(line))


def is_bullet_item(line: str) -> bool:
    return bool(BULLET_LIST_RE.match(line))


def heading_to_latex(level: str, title: str) -> str:
    cmd = {
        "chapter": "chapter",
        "section": "section",
        "subsection": "subsection",
        "subsubsection": "subsubsection",
    }[level]
    return f"\\{cmd}{{{convert_inline(title)}}}"


def process_lines(
    lines: list[str],
    fmt: str,
    md_dir: Path,
    images_dir: Path | None,
) -> list[str]:
    output: list[str] = []
    i = 0
    list_state = ListState.NONE
    pending_caption: str | None = None
    pending_figure_paths: list[str] = []
    pending_figure_labels: list[str | None] = []
    pending_figure_refs: list[str] = []
    pending_figure_caption: tuple[str, str] | None = None
    consumed_lines: set[int] = set()
    in_blockquote = False
    appendix_mode = False

    def flush_figures(caption_info: tuple[str, str] | None = None) -> None:
        nonlocal pending_figure_refs, pending_figure_caption
        cap = caption_info or pending_figure_caption
        pending_figure_caption = None
        flush_pending_figures(
            output,
            pending_figure_paths,
            pending_figure_labels,
            cap,
        )
        pending_figure_refs = []

    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()

        if i in consumed_lines:
            i += 1
            continue

        if not stripped:
            nxt = next_nonempty_line(lines, i + 1)
            keep_list = bool(
                nxt
                and list_state == ListState.ORDERED
                and is_ordered_item(nxt[1])
            ) or bool(nxt and list_state == ListState.BULLET and is_bullet_item(nxt[1]))
            if not keep_list:
                list_state = close_list(list_state, output)
                if in_blockquote:
                    output.append("\\end{quote}")
                    in_blockquote = False
                output.append("")
            i += 1
            continue

        table_cap = parse_table_caption_line(stripped)
        if table_cap is None:
            table_cap = parse_heading_before_table_caption(stripped, lines, i)
        if table_cap and next_nonempty_is_table(lines, i):
            flush_figures()
            list_state = close_list(list_state, output)
            pending_caption = table_cap
            i += 1
            continue

        html_start = stripped.lower().find("<table")
        if html_start != -1:
            flush_figures()
            list_state = close_list(list_state, output)
            html_parts = [stripped[html_start:]]
            while i + 1 < len(lines) and "</table>" not in html_parts[-1].lower():
                i += 1
                html_parts.append(lines[i].strip())
            html = " ".join(html_parts)
            match = HTML_TABLE_RE.search(html)
            if match:
                caption = pending_caption
                pending_caption = None
                if caption is None:
                    before = table_caption_before(lines, i)
                    if before:
                        consumed_lines.add(before[0])
                        caption = before[1]
                output.append(html_table_to_latex(match.group(0), caption))
                output.append("")
            i += 1
            continue

        html_table_match = HTML_TABLE_RE.search(stripped)
        if html_table_match:
            flush_figures()
            list_state = close_list(list_state, output)
            caption = pending_caption
            pending_caption = None
            if caption is None:
                before = table_caption_before(lines, i)
                if before:
                    consumed_lines.add(before[0])
                    caption = before[1]
            output.append(html_table_to_latex(html_table_match.group(0), caption))
            output.append("")
            i += 1
            continue

        if is_table_line(stripped):
            flush_figures()
            list_state = close_list(list_state, output)
            caption = pending_caption
            pending_caption = None
            if caption is None:
                before = table_caption_before(lines, i)
                if before:
                    consumed_lines.add(before[0])
                    caption = before[1]
            table_rows: list[list[str]] = []
            while i < len(lines) and is_table_line(lines[i].strip()):
                cells = parse_md_table_row(lines[i])
                if cells:
                    table_rows.append(cells)
                i += 1
            output.append(table_to_latex(table_rows, caption))
            pending_caption = None
            output.append("")
            continue

        image_match = IMAGE_RE.match(stripped)
        if image_match:
            list_state = close_list(list_state, output)
            img_path = image_match.group(1).replace("\\", "/")
            src = md_dir / img_path
            if images_dir and src.is_file():
                dest = images_dir / Path(img_path).name
                dest.parent.mkdir(parents=True, exist_ok=True)
                if src.resolve() != dest.resolve():
                    shutil.copy2(src, dest)
                img_path = f"images/{dest.name}"

            label: str | None = None
            ref_index = len(pending_figure_paths)
            if ref_index < len(pending_figure_refs):
                label = figure_num_to_label(pending_figure_refs[ref_index])

            pending_figure_paths.append(img_path)
            pending_figure_labels.append(label)
            i += 1
            continue

        figure_caption = parse_figure_caption_line(stripped)
        if figure_caption and not stripped.startswith("#"):
            list_state = close_list(list_state, output)
            if pending_figure_paths:
                flush_figures(figure_caption)
            else:
                pending_figure_caption = figure_caption
            i += 1
            continue

        if fmt == "hash":
            info = classify_hash_heading(stripped, appendix_mode)
            if info:
                if info["action"] == "skip":
                    i += 1
                    continue
                if info["action"] == "appendix_main":
                    flush_figures()
                    appendix_mode = True
                    output.append("\\chapter*{附件}")
                    i += 1
                    continue
                if info["action"] == "heading":
                    flush_figures()
                    list_state = close_list(list_state, output)
                    if in_blockquote:
                        output.append("\\end{quote}")
                        in_blockquote = False
                    if info["level"] == "chapter" and info.get("chapter_no") == 10:
                        appendix_mode = True
                    output.append(heading_to_latex(info["level"], info["title"]))
                    i += 1
                    continue

        if fmt == "legacy":
            chapter_match = LEGACY_CHAPTER_RE.match(stripped)
            if chapter_match:
                flush_figures()
                list_state = close_list(list_state, output)
                title = strip_section_number(chapter_match.group(2))
                output.append(f"\\chapter{{{convert_inline(title)}}}")
                i += 1
                continue

            subsection_match = LEGACY_SUBSECTION_RE.match(stripped)
            if subsection_match:
                flush_figures()
                list_state = close_list(list_state, output)
                title = strip_section_number(subsection_match.group(1))
                output.append(f"\\section{{{convert_inline(title)}}}")
                i += 1
                continue

            subsubsection_match = LEGACY_SUBSUBSECTION_RE.match(stripped)
            if subsubsection_match:
                list_state = close_list(list_state, output)
                title = unescape_md(subsubsection_match.group(1))
                if next_nonempty_is_table(lines, i):
                    cap = strip_md_emphasis(title)
                    if cap:
                        flush_figures()
                        pending_caption = cap
                        i += 1
                        continue
                figure_cap = parse_figure_caption_line(title)
                if figure_cap:
                    if pending_figure_paths:
                        flush_figures(figure_cap)
                    else:
                        pending_figure_caption = figure_cap
                elif parse_table_caption_line(title):
                    flush_figures()
                    pending_caption = parse_table_caption_line(title)
                else:
                    flush_figures()
                    output.append(
                        f"\\subsection{{{convert_inline(strip_section_number(subsubsection_match.group(1)))}}}"
                    )
                i += 1
                continue

            paragraph_match = LEGACY_PARAGRAPH_RE.match(stripped)
            if paragraph_match:
                flush_figures()
                list_state = close_list(list_state, output)
                title = unescape_md(paragraph_match.group(1))
                output.append(f"\\subsubsection{{{convert_inline(title)}}}")
                i += 1
                continue

        blockquote_match = BLOCKQUOTE_RE.match(stripped)
        if blockquote_match:
            flush_figures()
            list_state = close_list(list_state, output)
            if not in_blockquote:
                output.append("\\begin{quote}")
                in_blockquote = True
            content = blockquote_match.group(1).strip()
            if content:
                output.append(convert_inline(content))
            i += 1
            continue

        if in_blockquote:
            output.append("\\end{quote}")
            in_blockquote = False

        cn_ordered = CN_ORDERED_LIST_RE.match(stripped)
        if cn_ordered:
            if list_state != ListState.ORDERED:
                if list_state == ListState.BULLET:
                    output.append("\\end{itemize}")
                output.append("\\begin{enumerate}")
                list_state = ListState.ORDERED
            output.append(f"\\item {convert_inline(cn_ordered.group(2))}")
            i += 1
            continue

        ordered_match = ORDERED_LIST_RE.match(stripped)
        if ordered_match:
            if list_state != ListState.ORDERED:
                if list_state == ListState.BULLET:
                    output.append("\\end{itemize}")
                output.append("\\begin{enumerate}")
                list_state = ListState.ORDERED
            output.append(f"\\item {convert_inline(ordered_match.group(2))}")
            i += 1
            continue

        bullet_match = BULLET_LIST_RE.match(stripped)
        if bullet_match:
            if list_state != ListState.BULLET:
                if list_state == ListState.ORDERED:
                    output.append("\\end{enumerate}")
                output.append("\\begin{itemize}")
                list_state = ListState.BULLET
            output.append(f"\\item {convert_inline(bullet_match.group(1))}")
            i += 1
            continue

        if stripped.startswith("#"):
            i += 1
            continue

        flush_figures()
        list_state = close_list(list_state, output)
        pending_figure_refs = extract_figure_refs(stripped)
        output.append(convert_inline(sanitize_text(stripped)))
        i += 1

    list_state = close_list(list_state, output)
    if in_blockquote:
        output.append("\\end{quote}")
    flush_figures()

    return output


def split_by_chapters(latex_lines: list[str]) -> dict[str, list[str]]:
    chapters: dict[str, list[str]] = {}
    current_key: str | None = None
    current_lines: list[str] = []
    chapter_index = 0
    in_appendix = False

    def flush() -> None:
        nonlocal current_lines, current_key
        if current_key and current_lines:
            chapters[current_key] = current_lines
        current_lines = []

    for line in latex_lines:
        if line.startswith("\\chapter*{") and "附件" in line:
            flush()
            in_appendix = True
            current_key = CHAPTER_OUTPUT_FILES[10]
            current_lines = [line]
            continue

        if line.startswith("\\chapter{") and not in_appendix:
            flush()
            chapter_index += 1
            current_key = CHAPTER_OUTPUT_FILES.get(chapter_index)
            if current_key is None:
                current_key = CHAPTER_OUTPUT_FILES[10]
                in_appendix = True
            current_lines = [line]
            continue

        if current_key is None:
            continue
        current_lines.append(line)

    flush()
    return chapters


def convert_markdown_to_latex(
    markdown_text: str,
    md_path: Path | None = None,
    images_dir: Path | None = None,
    apply_vars: bool = False,
    keywords_csv: Path | None = None,
    full_document: bool = False,
) -> str:
    global VARIABLE_VALUE_TO_CMD
    if apply_vars:
        from converter.project_vars import build_substitution_map, extract_project_values, load_keywords

        csv_path = keywords_csv or Path(__file__).resolve().parent / "data" / "project_keywords.csv"
        values = extract_project_values(markdown_text, load_keywords(csv_path))
        VARIABLE_VALUE_TO_CMD = dict(build_substitution_map(values))
    else:
        VARIABLE_VALUE_TO_CMD = {}

    lines = markdown_text.splitlines()
    fmt = detect_format(lines)
    if full_document:
        body = [line.rstrip("\n") for line in lines]
    else:
        body = extract_body(lines, fmt)
    md_dir = md_path.parent if md_path else Path.cwd()
    latex_lines = process_lines(body, fmt, md_dir, images_dir)
    return "\n".join(latex_lines).strip() + "\n"


def write_split_chapters(
    latex_text: str,
    output_dir: Path,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    chapters = split_by_chapters(latex_text.splitlines())
    written: list[Path] = []

    for filename, content_lines in chapters.items():
        content = "\n".join(content_lines).strip() + "\n"
        if filename == "04-chapter1.tex":
            content = CHAPTER1_HEADER + content
        if filename == "13-appendix.tex":
            content = APPENDIX_HEADER + re.sub(
                r"\\chapter\*\{附件\}\s*", "", content, count=1
            )
        path = output_dir / filename
        path.write_text(content, encoding="utf-8")
        written.append(path)

    return written


def setup_project_from_report1(project_dir: Path, template_dir: Path) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "chapters").mkdir(exist_ok=True)
    (project_dir / "images").mkdir(exist_ok=True)

    for name in ["main.tex", "chapters/00-cover.tex", "chapters/01-qualification.tex", "chapters/02-declaration.tex", "chapters/03-toc.tex"]:
        src = template_dir / name
        dest = project_dir / name
        if src.is_file() and not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert Markdown report to LaTeX.")
    parser.add_argument("input", nargs="?", help="Input markdown file")
    parser.add_argument("-o", "--output", help="Output LaTeX file (single-file mode)")
    parser.add_argument(
        "--split",
        action="store_true",
        help="Split output into report1-style chapter files",
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory for split chapter .tex files",
    )
    parser.add_argument(
        "--images-dir",
        help="Directory to store/copy images (default: sibling images/ of output-dir)",
    )
    parser.add_argument(
        "--project-dir",
        help="LaTeX project root; copies front matter from report1 if missing",
    )
    parser.add_argument(
        "--template-dir",
        default=None,
        help="Template source for front matter (default: latex/report1)",
    )
    parser.add_argument(
        "--full-document",
        action="store_true",
        help="Convert entire Markdown file (skip chapter body detection; for Word forms)",
    )
    parser.add_argument(
        "--apply-vars",
        action="store_true",
        help="Replace known project keyword values in body text with \\newcommand names",
    )
    parser.add_argument(
        "--update-main-tex",
        action="store_true",
        help="Also update main.tex \\newcommand block from Markdown (use with --project-dir)",
    )
    parser.add_argument(
        "--keywords",
        default=None,
        help="Keyword CSV path (default: project_keywords.csv)",
    )
    args = parser.parse_args()

    if not args.input:
        parser.error("Input markdown file is required.")

    input_path = Path(args.input).resolve()
    markdown_text = input_path.read_text(encoding="utf-8")
    keywords_csv = Path(args.keywords).resolve() if args.keywords else None

    if args.split:
        output_dir = Path(args.output_dir) if args.output_dir else input_path.parent / "chapters"
        images_dir = Path(args.images_dir) if args.images_dir else output_dir.parent / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        if args.project_dir:
            template_dir = Path(args.template_dir) if args.template_dir else input_path.parents[1] / "report1"
            if not template_dir.is_dir():
                template_dir = Path(__file__).resolve().parent / "latex" / "report1"
            setup_project_from_report1(Path(args.project_dir), template_dir)

        latex_text = convert_markdown_to_latex(
            markdown_text,
            input_path,
            images_dir,
            apply_vars=args.apply_vars,
            keywords_csv=keywords_csv,
            full_document=args.full_document,
        )
        if args.update_main_tex and args.project_dir:
            from converter.project_vars import extract_project_values, load_keywords, patch_main_tex, render_newcommand_block

            keywords = load_keywords(keywords_csv or Path(__file__).resolve().parent / "project_keywords.csv")
            values = extract_project_values(markdown_text, keywords)
            main_tex = Path(args.project_dir) / "main.tex"
            patch_main_tex(main_tex, render_newcommand_block(values, keywords))
            print(f"Updated project variables in {main_tex}")
        print(f"Converted {input_path.name} -> {len(written)} chapter files in {output_dir}")
        for path in written:
            print(f"  - {path.name}")
        return

    output_path = Path(args.output) if args.output else input_path.with_suffix(".tex")
    images_dir = Path(args.images_dir) if args.images_dir else output_path.parent / "images"
    latex_text = convert_markdown_to_latex(
        markdown_text,
        input_path,
        images_dir,
        apply_vars=args.apply_vars,
        keywords_csv=keywords_csv,
        full_document=args.full_document,
    )
    output_path.write_text(latex_text, encoding="utf-8")
    print(f"Written to {output_path}")


if __name__ == "__main__":
    main()
