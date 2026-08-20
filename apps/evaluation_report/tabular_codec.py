"""LaTeX tabular/longtable ↔ structured grid (with merges, align, col widths)."""

from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from converter.md2latex import convert_inline
_CMD = re.compile(r"\\(textbf|textit|emph)\{((?:[^{}]|\{[^{}]*\})*)\}")
_MATH = re.compile(r"\$([^$]+)\$")
_SPEC = re.compile(r"\\([#$%&_{}])")


def _strip_tex_comments(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        buf: list[str] = []
        i = 0
        while i < len(line):
            if line[i] == "%" and (i == 0 or line[i - 1] != "\\"):
                break
            buf.append(line[i])
            i += 1
        lines.append("".join(buf))
    return "\n".join(lines)


_LAYOUT_CMDS = frozenset(
    {
        "raggedright",
        "raggedleft",
        "centering",
        "hfill",
        "vfill",
        "noindent",
        "newline",
        "par",
        "small",
        "large",
        "normalsize",
        "bfseries",
        "itshape",
        "ttfamily",
        "rmfamily",
        "sffamily",
        "arraybackslash",
        "justifying",
        "Centering",
        "RaggedRight",
        "RaggedLeft",
        "newline",
        "linebreak",
    }
)


def _read_group(s: str, i: int) -> tuple[str, int]:
    """Read a ``{...}`` group starting at index ``i`` (must be ``{``). Return (inner, next_i)."""
    if i >= len(s) or s[i] != "{":
        return "", i
    depth = 0
    j = i
    while j < len(s):
        ch = s[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1 : j], j + 1
        j += 1
    return s[i + 1 :], len(s)


def _unwrap_makecell(text: str) -> str:
    """\\makecell[opt]{a\\\\b} → a\\nb (keep inner TeX for further processing)."""
    out: list[str] = []
    i = 0
    while i < len(text):
        if text.startswith(r"\makecell", i):
            j = i + len(r"\makecell")
            while j < len(text) and text[j].isspace():
                j += 1
            if j < len(text) and text[j] == "[":
                while j < len(text) and text[j] != "]":
                    j += 1
                j = min(len(text), j + 1)
                while j < len(text) and text[j].isspace():
                    j += 1
            if j < len(text) and text[j] == "{":
                inner, j = _read_group(text, j)
                inner = inner.replace(r"\\", "\n")
                out.append(inner)
                i = j
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _unwrap_parbox(text: str) -> str:
    """``\\parbox[pos]{width}{content}`` → content (keep inner TeX)."""
    s = text.strip()
    if not s.startswith(r"\parbox"):
        return text
    i = len(r"\parbox")
    while i < len(s) and s[i].isspace():
        i += 1
    if i < len(s) and s[i] == "[":
        while i < len(s) and s[i] != "]":
            i += 1
        i = min(len(s), i + 1)
        while i < len(s) and s[i].isspace():
            i += 1
    if i >= len(s) or s[i] != "{":
        return text
    _, i = _read_group(s, i)  # width
    while i < len(s) and s[i].isspace():
        i += 1
    if i >= len(s) or s[i] != "{":
        return text
    inner, _ = _read_group(s, i)
    return inner


def _plain(text: str) -> str:
    """Cell text for editor: keep ``$math$``, unwrap makecell/parbox, drop layout cmds."""
    if not text:
        return ""
    s = _strip_tex_comments(text).replace("~", " ").strip()
    # If a full merge command leaked into cell text, unwrap instead of mangling.
    if s.startswith(r"\multirow") or s.startswith(r"\multicolumn"):
        return _parse_cell_token(s)[0]
    s = _unwrap_parbox(s)
    s = _unwrap_makecell(s)
    # Normalize \( ... \) to $...$ so math survives command stripping.
    s = re.sub(r"\\\((.+?)\\\)", r"$\1$", s)
    maths: list[str] = []

    def _save(m: re.Match[str]) -> str:
        maths.append(m.group(0))
        return f"\x00M{len(maths) - 1}\x00"

    s = re.sub(r"\$[^$]+\$", _save, s)
    s = _CMD.sub(r"\2", s)
    s = _SPEC.sub(r"\1", s)

    def _cmd_repl(m: re.Match[str]) -> str:
        name = m.group(1)
        arg = m.group(2)
        # Multi-arg layout wrappers must not be reduced to their first brace arg
        # (otherwise ``\multirow{6}{=}{正文}`` becomes ``6{=}{正文}`` residue).
        if name in {"multirow", "multicolumn", "makecell", "multirowcell", "parbox"}:
            return " "
        if name in {"hspace", "hspace*", "vspace", "vspace*", "kern", "hskip", "vskip"}:
            # Length args like 2em / 1em / 10mm are spacing, not visible text.
            return "  "
        if name in {"newline", "linebreak"}:
            return "\n"
        if name in _LAYOUT_CMDS:
            return f" {arg} " if arg is not None else " "
        if name in {"mathrm", "mathbf", "mathit", "textrm", "textbf", "textit"}:
            return arg if arg is not None else ""
        if arg is not None:
            return arg
        return ""

    s = re.sub(r"\\([a-zA-Z]+)\*?(?:\[[^\]]*\])?(?:\{([^{}]*)\})?", _cmd_repl, s)
    # Drop leftover length tokens accidentally left as text (e.g. bare 2em).
    s = re.sub(r"(?<![\w.])\d+(?:\.\d+)?(?:em|ex|mu|pt|bp|mm|cm|in|sp)\b", "", s)
    # Drop orphan multirow/multicolumn brace residue like ``6{=}{`` if any remains.
    s = re.sub(r"\b\d+\{[*=]?\}?\{?", "", s)
    s = s.replace(r"\\", "\n")
    for i, m in enumerate(maths):
        s = s.replace(f"\x00M{i}\x00", m)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    return s.strip()


@dataclass
class ColSpec:
    """One column descriptor from LaTeX colspec."""

    kind: str = "l"  # l c r p m b X
    width: str = ""  # e.g. 2.0cm / 0.25\\textwidth
    raw: str = "l"
    prefix: str = ""  # e.g. >{\\centering\\arraybackslash}

    def to_latex(self) -> str:
        if self.kind in {"p", "m", "b"} and self.width:
            body = f"{self.kind}{{{self.width}}}"
        elif self.kind == "X":
            body = "X"
        else:
            body = self.kind if self.kind in {"l", "c", "r"} else "l"
        return f"{self.prefix}{body}" if self.prefix else body


@dataclass
class GridCell:
    text: str = ""
    colspan: int = 1
    rowspan: int = 1
    align: str = ""  # l c r or ""
    col_spec: str = ""  # raw \\multicolumn 2nd arg, e.g. m{6.0cm}|
    skip: bool = False  # covered by rowspan/colspan origin


@dataclass
class TableModel:
    env: str = "tabular"  # tabular | longtable | tabularx
    bordered: bool = True
    cols: list[ColSpec] = field(default_factory=list)
    cells: list[list[GridCell]] = field(default_factory=list)  # logical grid
    caption: str = ""
    label: str = ""
    placement: str = "H"  # table[H]
    hlines: list[bool] = field(default_factory=list)  # after each row (incl. header rule)
    clines: list[list[str]] = field(default_factory=list)  # after each row: ["3-7", ...]
    body_input: str = ""  # e.g. files/staffList — data rows live in \\input
    header_rows: int = 0  # rows kept in chapter before \\input

    def to_dict(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "bordered": self.bordered,
            "cols": [asdict(c) for c in self.cols],
            "cells": [[asdict(c) for c in row] for row in self.cells],
            "caption": self.caption,
            "label": self.label,
            "placement": self.placement,
            "hlines": list(self.hlines),
            "clines": [list(x) for x in self.clines],
            "body_input": self.body_input,
            "header_rows": self.header_rows,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "TableModel":
        if not data:
            return cls(cols=[ColSpec("l"), ColSpec("l")], cells=[[GridCell(), GridCell()], [GridCell(), GridCell()]])
        cols = [ColSpec(**c) if isinstance(c, dict) else ColSpec() for c in (data.get("cols") or [])]
        cells: list[list[GridCell]] = []
        for row in data.get("cells") or []:
            cells.append([GridCell(**c) if isinstance(c, dict) else GridCell() for c in row])
        return cls(
            env=str(data.get("env") or "tabular"),
            bordered=bool(data.get("bordered", True)),
            cols=cols or [ColSpec("l"), ColSpec("l")],
            cells=cells or [[GridCell(), GridCell()]],
            caption=str(data.get("caption") or ""),
            label=str(data.get("label") or ""),
            placement=str(data.get("placement") or "H"),
            hlines=list(data.get("hlines") or []),
            clines=[list(x) for x in (data.get("clines") or [])],
            body_input=str(data.get("body_input") or ""),
            header_rows=int(data.get("header_rows") or 0),
        )


_COL_TOKEN = re.compile(
    r"\||"
    r"([lcr])|"
    r"([pmb])\{([^{}]+)\}|"
    r">\{[^}]*\}|"
    r"<\{[^}]*\}|"
    r"@\{[^}]*\}|"
    r"!\{[^}]*\}"
)


def parse_colspec(spec: str) -> tuple[list[ColSpec], bool]:
    """Parse ``|l|c|p{2cm}|`` → column list + bordered flag."""
    bordered = "|" in spec
    cols: list[ColSpec] = []
    i = 0
    s = spec.strip()
    pending_prefix = ""
    while i < len(s):
        if s[i] == "|":
            i += 1
            continue
        if s[i] in "lcrX":
            cols.append(ColSpec(kind=s[i], raw=s[i], prefix=pending_prefix))
            pending_prefix = ""
            i += 1
            continue
        if s[i] in "pmb" and i + 1 < len(s) and s[i + 1] == "{":
            kind = s[i]
            j = i + 2
            depth = 1
            while j < len(s) and depth:
                if s[j] == "{":
                    depth += 1
                elif s[j] == "}":
                    depth -= 1
                j += 1
            width = s[i + 2 : j - 1]
            cols.append(
                ColSpec(
                    kind=kind,
                    width=width,
                    raw=f"{kind}{{{width}}}",
                    prefix=pending_prefix,
                )
            )
            pending_prefix = ""
            i = j
            continue
        # skip >{} <{} @{} !{} — keep >{\centering...} as column prefix
        if s[i] in "><@!" and i + 1 < len(s) and s[i + 1] == "{":
            opener = s[i]
            j = i + 2
            depth = 1
            while j < len(s) and depth:
                if s[j] == "{":
                    depth += 1
                elif s[j] == "}":
                    depth -= 1
                j += 1
            inner = s[i + 2 : j - 1]
            if opener == ">" and (
                "centering" in inner or "arraybackslash" in inner or "Ragged" in inner
            ):
                pending_prefix = f">{{{inner}}}"
            i = j
            continue
        i += 1
    if not cols:
        cols = [ColSpec("l"), ColSpec("l")]
    return cols, bordered


def _split_row_cells(row_tex: str) -> list[str]:
    """Split a row on ``&`` respecting braces."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    i = 0
    while i < len(row_tex):
        ch = row_tex[i]
        if ch == "{":
            depth += 1
            buf.append(ch)
        elif ch == "}":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "&" and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf).strip())
    return parts


def _align_from_spec(spec: str) -> str:
    cols, _ = parse_colspec(spec)
    if cols and cols[0].kind in {"l", "c", "r"}:
        return cols[0].kind
    if cols and cols[0].kind in {"p", "m", "b"}:
        # Paragraph columns are usually centered in report tables.
        if "centering" in (spec or ""):
            return "c"
        return "c"
    return "c"


def _parse_multirow_inner(inner: str) -> tuple[str, int]:
    """Parse optional ``\\multirow{n}{*}{text}`` → (text, rowspan)."""
    inner = inner.strip()
    if not inner.startswith(r"\multirow"):
        return _plain(inner), 1
    i = len(r"\multirow")
    while i < len(inner) and inner[i].isspace():
        i += 1
    n_s, i = _read_group(inner, i)
    while i < len(inner) and inner[i].isspace():
        i += 1
    _, i = _read_group(inner, i)  # width *
    while i < len(inner) and inner[i].isspace():
        i += 1
    text, _ = _read_group(inner, i)
    try:
        rs = max(1, int(n_s))
    except ValueError:
        rs = 1
    return _plain(text), rs


def _parse_cell_token(token: str) -> tuple[str, int, int, str, str]:
    """Return text, colspan, rowspan, align, col_spec."""
    token = token.strip()
    if token.startswith(r"\multicolumn"):
        i = len(r"\multicolumn")
        while i < len(token) and token[i].isspace():
            i += 1
        n_s, i = _read_group(token, i)
        while i < len(token) and token[i].isspace():
            i += 1
        align_s, i = _read_group(token, i)
        while i < len(token) and token[i].isspace():
            i += 1
        content, _ = _read_group(token, i)
        try:
            cs = max(1, int(n_s))
        except ValueError:
            cs = 1
        text, rs = _parse_multirow_inner(content)
        return text, cs, rs, _align_from_spec(align_s), align_s.strip()
    if token.startswith(r"\multirow"):
        text, rs = _parse_multirow_inner(token)
        return text, 1, rs, "", ""
    return _plain(token), 1, 1, "", ""


def extract_tabular_chunk(latex: str) -> tuple[str, str, str]:
    """Return (env, colspec, body_inside) from a table/tabular/longtable/tabularx chunk."""
    env = "tabular"
    if r"\begin{longtable}" in latex:
        env = "longtable"
    elif r"\begin{tabularx}" in latex:
        env = "tabularx"
    elif r"\begin{tabular}" in latex:
        env = "tabular"
    marker = f"\\begin{{{env}}}"
    idx = latex.find(marker)
    if idx < 0:
        return env, "|l|l|", latex
    i = idx + len(marker)
    while i < len(latex) and latex[i].isspace():
        i += 1
    # optional [pos] for longtable
    if i < len(latex) and latex[i] == "[":
        while i < len(latex) and latex[i] != "]":
            i += 1
        i = min(len(latex), i + 1)
        while i < len(latex) and latex[i].isspace():
            i += 1
    # tabularx: first group is total width (e.g. \linewidth), then colspec
    if env == "tabularx" and i < len(latex) and latex[i] == "{":
        _, i = _read_group(latex, i)
        while i < len(latex) and latex[i].isspace():
            i += 1
    if i < len(latex) and latex[i] == "{":
        colspec, i = _read_group(latex, i)
    else:
        colspec = "|l|l|"
    end_m = re.search(rf"\\end\{{{env}\}}", latex[i:])
    body = latex[i : i + end_m.start()] if end_m else latex[i:]
    if env == "longtable":
        body = _strip_longtable_chrome(body)
    return env, colspec, body


def _strip_longtable_chrome(body: str) -> str:
    """Remove caption/label and longtable head/foot chrome; keep one header + data rows.

    Typical structure::

        \\caption{...}\\label{...}\\\\
        <header rows>
        \\endfirsthead
        <repeated header>
        \\endhead
        ...
        \\endlastfoot
        <data rows>
    """
    if not body:
        return body
    # Caption / label often sit inside longtable and must not become cells.
    body = re.sub(r"\\caption\*?\{(?:[^{}]|\{[^{}]*\})*\}", "", body)
    body = re.sub(r"\\label\{[^}]+\}", "", body)

    first = re.search(r"\\endfirsthead\b", body)
    last = re.search(r"\\endlastfoot\b", body)
    if first and last and last.start() > first.start():
        header = body[: first.start()]
        data = body[last.end() :]
        # Drop orphan \\ left after removing caption on its own line.
        header = re.sub(r"^\s*\\\\\s*", "", header.strip())
        header = re.sub(r"\\\\\s*$", r"\\\\", header.strip())
        data = data.lstrip()
        if header and data:
            return header + "\n" + data
        return header or data

    if last:
        return body[last.end() :].lstrip()

    # No lastfoot: drop firsthead/head/foot markers but keep remaining rows once.
    for marker in (r"\\endfirsthead\b", r"\\endhead\b", r"\\endfoot\b"):
        m = re.search(marker, body)
        if m:
            # Keep text before first marker as header candidate; after last known head as data.
            pass
    head = re.search(r"\\endhead\b", body)
    if first:
        header = re.sub(r"^\s*\\\\\s*", "", body[: first.start()].strip())
        rest = body[first.end() :]
        # Remove duplicate head/foot sections if present.
        rest = re.sub(
            r".*?\\end(?:head|foot|lastfoot)\b",
            "",
            rest,
            count=3,
            flags=re.S,
        )
        rest = rest.lstrip()
        if header and rest:
            return header + "\n" + rest
        return header or rest
    if head:
        return body[head.end() :].lstrip()
    return body


def latex_table_to_model(latex: str, caption: str = "") -> TableModel:
    """Parse LaTeX table/tabular/longtable into TableModel."""
    cap_m = re.search(r"\\caption\{((?:[^{}]|\{[^{}]*\})*)\}", latex)
    if cap_m and not caption:
        caption = _plain(cap_m.group(1))
    lab_m = re.search(r"\\label\{([^}]+)\}", latex)
    label = lab_m.group(1).strip() if lab_m else ""

    env, colspec, body = extract_tabular_chunk(latex)
    colspec = _strip_tex_comments(colspec)
    body = _strip_tex_comments(body)
    cols, bordered = parse_colspec(colspec)
    declared_ncols = max(1, len(cols))
    ncols = declared_ncols

    body = body.replace("\r\n", "\n").lstrip()
    # Opening \\hline is the top border (re-emitted on serialize), not a row rule.
    if body.startswith(r"\hline"):
        body = body[len(r"\hline") :].lstrip()
    # Split on \\ outside braces. Paragraph columns sometimes use \\ as an
    # in-cell line break before the row has filled ``declared_ncols`` columns —
    # treat those as newlines, not row ends.
    rough_rows: list[str] = []
    buf: list[str] = []
    depth = 0
    i = 0
    while i < len(body):
        if body[i] == "{":
            depth += 1
            buf.append(body[i])
            i += 1
        elif body[i] == "}":
            depth = max(0, depth - 1)
            buf.append(body[i])
            i += 1
        elif depth == 0 and body[i : i + 2] == r"\\":
            row_so_far = "".join(buf)
            covered = 0
            for t in _split_row_cells(row_so_far):
                covered += _parse_cell_token(t)[1]
            rest = body[i + 2 :].lstrip()
            row_boundary = (
                not rest
                or rest.startswith(r"\hline")
                or rest.startswith(r"\cline")
                or rest.startswith(r"\end")
                or rest.startswith(r"\toprule")
                or rest.startswith(r"\midrule")
                or rest.startswith(r"\bottomrule")
                or rest.startswith(r"\morecmidrules")
            )
            if covered < declared_ncols and rest and not row_boundary:
                buf.append("\n")
                i += 2
                continue
            rough_rows.append(row_so_far)
            buf = []
            i += 2
        else:
            buf.append(body[i])
            i += 1
    if buf or not rough_rows:
        rough_rows.append("".join(buf))
    logical: list[tuple[list[tuple[str, int, int, str, str]], bool, list[str]]] = []
    pending_hline = False
    pending_clines: list[str] = []
    for rough in rough_rows:
        has_hline = r"\hline" in rough
        row_clines = re.findall(r"\\cline\{([^}]+)\}", rough)
        rough = rough.replace(r"\hline", "")
        rough = re.sub(r"\\cline\{[^}]+\}", "", rough)
        rough = rough.strip()
        if not rough:
            if logical and (has_hline or row_clines):
                cells0, _, cl0 = logical[-1]
                logical[-1] = (
                    cells0,
                    has_hline or pending_hline,
                    (cl0 + row_clines) if not has_hline else [],
                )
                if has_hline:
                    logical[-1] = (cells0, True, [])
                pending_hline = False
                pending_clines = []
            elif has_hline:
                pending_hline = True
                pending_clines = []
            elif row_clines:
                pending_clines.extend(row_clines)
            continue
        tokens = _split_row_cells(rough)
        cells = [_parse_cell_token(t) for t in tokens]
        # Rules after a row appear at the start of the next rough chunk — attach
        # them to the previous logical row, not the current one.
        if logical and row_clines and not has_hline:
            cells0, h0, c0 = logical[-1]
            logical[-1] = (cells0, h0, list(c0) + list(row_clines))
            row_clines = []
        if logical and has_hline:
            cells0, _, c0 = logical[-1]
            logical[-1] = (cells0, True, [])
            has_hline = False
        logical.append(
            (cells, has_hline or pending_hline, list(pending_clines) + row_clines)
        )
        pending_hline = False
        pending_clines = []

    # Occupancy grid
    occ: list[list[GridCell | None]] = []
    hlines: list[bool] = []
    clines_out: list[list[str]] = []

    def ensure_row(r: int) -> None:
        while len(occ) <= r:
            occ.append([None] * ncols)
            hlines.append(False)
            clines_out.append([])

    def ensure_cols(n: int) -> None:
        nonlocal ncols
        # Never invent columns beyond the colspec; otherwise free slots appear
        # to the right of earlier rows and later data is glued onto the header.
        n = min(max(n, 1), max(declared_ncols, 1))
        while ncols < n:
            cols.append(ColSpec("l"))
            ncols += 1
            for row in occ:
                row.append(None)

    for cells, hline, row_clines in logical:
        # find first row with a free slot within declared width
        r = 0
        while True:
            ensure_row(r)
            if any(occ[r][c] is None for c in range(declared_ncols)):
                break
            r += 1
        # Fresh rows that start with real text in column 1 (no leading empty
        # placeholders) should not be shoved under an active multirow — jump to
        # the first row where column 0 is free (common in longtable plans).
        if cells and (cells[0][0] or "").strip() and cells[0][1] == 1:
            while True:
                ensure_row(r)
                if occ[r][0] is None:
                    break
                r += 1
        ensure_row(r)
        hlines[r] = hline
        clines_out[r] = list(row_clines)
        c = 0
        for text, cs, rs, align, col_spec in cells:
            cs = max(1, min(cs, declared_ncols))
            rs = max(1, rs)
            while True:
                ensure_row(r)
                if c >= declared_ncols:
                    r += 1
                    c = 0
                    ensure_row(r)
                    continue
                if c + cs > declared_ncols:
                    r += 1
                    c = 0
                    continue
                ensure_cols(declared_ncols)
                if occ[r][c] is not None:
                    if not text and cs == 1 and rs == 1:
                        # Placeholder under a rowspan/colspan above.
                        c += 1
                        break
                    # Non-empty text on a covered cell: advance within row, or wrap.
                    c += 1
                    continue
                ensure_row(r + rs - 1)
                for dr in range(rs):
                    ensure_row(r + dr)
                    for dc in range(cs):
                        if dr == 0 and dc == 0:
                            occ[r][c] = GridCell(
                                text=text,
                                colspan=cs,
                                rowspan=rs,
                                align=align,
                                col_spec=col_spec,
                                skip=False,
                            )
                        else:
                            occ[r + dr][c + dc] = GridCell(skip=True)
                c += cs
                break

    # Drop trailing all-empty columns that only appeared due to bad splits.
    while ncols > declared_ncols:
        col = ncols - 1
        useful = False
        for row in occ:
            cell = row[col] if col < len(row) else None
            if cell is not None and not cell.skip and (cell.text or cell.colspan > 1 or cell.rowspan > 1):
                useful = True
                break
        if useful:
            break
        for row in occ:
            if len(row) > col:
                row.pop()
        if cols:
            cols.pop()
        ncols -= 1

    cells_out: list[list[GridCell]] = []
    for row in occ:
        row = (row + [None] * ncols)[:ncols]
        cells_out.append([x if x is not None else GridCell() for x in row])

    if not cells_out:
        cells_out = [[GridCell() for _ in range(ncols)]]
        hlines = [True]
        clines_out = [[]]

    return TableModel(
        env="longtable" if env == "longtable" else ("tabularx" if env == "tabularx" else "tabular"),
        bordered=bordered,
        cols=cols[:ncols],
        cells=cells_out,
        caption=caption,
        label=label,
        hlines=(hlines + [True] * len(cells_out))[: len(cells_out)],
        clines=(clines_out + [[] for _ in cells_out])[: len(cells_out)],
    )


def _cell_align_spec(
    align: str,
    bordered: bool,
    default: str = "c",
    *,
    at_left: bool = False,
) -> str:
    """Spec for \\multicolumn in a bordered tabular.

    Interior merges use ``c|`` (right rule only) so borders are not doubled.
    Merges that start at the first column need a leading ``|``.
    """
    a = align if align in {"l", "c", "r"} else default
    if bordered:
        return f"|{a}|" if at_left else f"{a}|"
    return a


def _normalize_multicolumn_align(align: str, *, at_left: bool, bordered: bool) -> str:
    """Drop doubled left ``|`` on interior \\multicolumn specs (e.g. ``|c|`` → ``c|``)."""
    raw = (align or "").strip()
    if not bordered or not raw:
        return raw or _cell_align_spec("", bordered, at_left=at_left)
    if not at_left and raw.startswith("|"):
        raw = raw[1:]
    if not raw:
        return _cell_align_spec("", bordered, at_left=at_left)
    return raw


def _cline_specs_avoiding_active_rowspans(
    cells: list[list[GridCell]],
    row_index: int,
    ncols: int,
) -> list[str]:
    """
    Build ``\\cline{a-b}`` specs for columns not covered by a rowspan that continues
    past ``row_index``. Used when a full ``\\hline`` would cut through ``\\multirow``.
    """
    covered: set[int] = set()
    for rr in range(0, row_index + 1):
        if rr >= len(cells):
            break
        row = cells[rr]
        ci = 0
        while ci < ncols:
            cell = row[ci] if ci < len(row) else None
            if cell is None or cell.skip:
                ci += 1
                continue
            cs = max(1, int(cell.colspan or 1))
            rs = max(1, int(cell.rowspan or 1))
            if rr + rs - 1 > row_index:
                for c in range(ci, min(ncols, ci + cs)):
                    covered.add(c)
            ci += cs
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


def _append_row_border_rules(
    lines: list[str],
    *,
    cells: list[list[GridCell]],
    row_index: int,
    ncols: int,
    bordered: bool,
    draw_hline: bool,
    row_clines: list[str],
) -> None:
    """Append \\hline or auto/explicit \\cline after a row."""
    if not bordered:
        return
    specs = [c for c in (row_clines or []) if c]
    if specs:
        for spec in specs:
            lines.append(rf"\cline{{{spec}}}")
        return
    blocked = False
    for rr in range(0, row_index + 1):
        if rr >= len(cells):
            break
        for cell in cells[rr]:
            if (
                cell
                and not cell.skip
                and rr + max(1, cell.rowspan) - 1 > row_index
            ):
                blocked = True
                break
        if blocked:
            break
    if blocked:
        # Full \\hline would cut \\multirow — draw partial rules on free columns
        for spec in _cline_specs_avoiding_active_rowspans(cells, row_index, ncols):
            lines.append(rf"\cline{{{spec}}}")
        return
    if draw_hline:
        lines.append("\\hline")


def _format_table_row_parts(
    row: list[GridCell],
    ncols: int,
    bordered: bool,
    cols: list[ColSpec] | None = None,
) -> list[str]:
    parts: list[str] = []
    ci = 0
    while ci < ncols:
        cell = row[ci] if ci < len(row) else GridCell(skip=True)
        if cell.skip:
            # Rowspan continuation needs an empty cell so ``&`` column count stays correct.
            # Colspan-covered slots are never visited (origin advances ``ci`` by colspan).
            parts.append("")
            ci += 1
            continue
        raw = (cell.text or "").strip()
        # Keep personalized macros / raw TeX cells as-is (e.g. \\sourcetype).
        from .structured_blocks import kw_marks_to_commands

        raw = kw_marks_to_commands(raw)
        cell_src = kw_marks_to_commands(cell.text or "")
        if raw.startswith("\\") and (
            re.fullmatch(r"\\[a-zA-Z]+(?:\{\})?", raw)
            or raw.startswith(r"\makecell")
            or raw.startswith(r"\multicolumn")
            or raw.startswith(r"\multirow")
        ):
            text = raw
        else:
            text = convert_inline(cell_src)
        cs, rs = max(1, cell.colspan), max(1, cell.rowspan)
        if cs > 1:
            align = (cell.col_spec or "").strip() or _cell_align_spec(
                cell.align, bordered, at_left=(ci == 0)
            )
            align = _normalize_multicolumn_align(
                align, at_left=(ci == 0), bordered=bordered
            )
            # Prefer summed m-column widths so long merged notes wrap in-cell
            if cols and not re.search(r"[mpb]\s*\{", align):
                widths: list[float] = []
                for j in range(ci, min(ncols, ci + cs)):
                    if j >= len(cols):
                        break
                    wm = re.match(r"([\d.]+)\s*cm", (cols[j].width or "").strip())
                    if wm:
                        widths.append(float(wm.group(1)))
                if len(widths) == cs:
                    total = sum(widths)
                    align = (
                        f"|m{{{total:.1f}cm}}|"
                        if (ci == 0 and bordered)
                        else (f"m{{{total:.1f}cm}}|" if bordered else f"m{{{total:.1f}cm}}")
                    )
            # Restore centering for paragraph-width merges (common in report headers).
            cell_body = text
            if (
                cell_body
                and not cell_body.lstrip().startswith("\\")
                and ("m{" in align or "p{" in align or "b{" in align)
            ):
                cell_body = r"\centering " + cell_body
            if rs > 1:
                parts.append(
                    rf"\multicolumn{{{cs}}}{{{align}}}{{\multirow{{{rs}}}{{=}}{{{cell_body}}}}}"
                )
            else:
                parts.append(rf"\multicolumn{{{cs}}}{{{align}}}{{{cell_body}}}")
        elif rs > 1:
            mr_w = "="
            if cols and ci < len(cols):
                wm = re.match(r"([\d.]+)\s*cm", (cols[ci].width or "").strip())
                if wm:
                    mr_w = f"{float(wm.group(1)):.1f}cm"
            body = text
            if body and not body.lstrip().startswith("\\"):
                body = r"\centering " + body
            parts.append(rf"\multirow{{{rs}}}{{{mr_w}}}{{{body}}}")
        else:
            parts.append(text)
        ci += cs
    return parts


def model_body_rows_to_latex(model: TableModel) -> str:
    """Serialize only data rows (after header_rows) for files/*.tex."""
    cols = model.cols or [ColSpec("l")]
    ncols = len(cols)
    cells = model.cells or []
    start = max(0, int(model.header_rows or 0))
    lines: list[str] = []
    for ri, row in enumerate(cells[start:], start=start):
        while len(row) < ncols:
            row.append(GridCell(skip=True))
        parts = _format_table_row_parts(row, ncols, model.bordered, cols=cols)
        if not parts:
            lines.append(r"\\")
        else:
            lines.append(" & ".join(parts) + r" \\")
        draw_hline = True
        row_clines: list[str] = []
        if model.clines and ri < len(model.clines):
            row_clines = [c for c in model.clines[ri] if c]
        if model.hlines and ri < len(model.hlines):
            draw_hline = model.hlines[ri]
        _append_row_border_rules(
            lines,
            cells=cells,
            row_index=ri,
            ncols=ncols,
            bordered=bool(model.bordered),
            draw_hline=draw_hline,
            row_clines=row_clines,
        )
    # Comment out the final EOL: ``\input`` of a file ending in newline inside
    # ``tabular`` starts a phantom empty row (vertical stubs below the bottom rule).
    text = "\n".join(lines)
    if text and not text.rstrip().endswith("%"):
        text = text.rstrip() + "%"
    return text


def model_to_latex(model: TableModel) -> str:
    """Serialize TableModel → LaTeX table/longtable."""
    cols = model.cols or [ColSpec("l")]
    ncols = len(cols)
    # ensure rectangular
    cells = model.cells or [[GridCell() for _ in cols]]
    for row in cells:
        while len(row) < ncols:
            row.append(GridCell(skip=True))

    if model.bordered:
        colspec = "|" + "|".join(c.to_latex() for c in cols) + "|"
    else:
        colspec = "".join(c.to_latex() for c in cols)

    env = model.env if model.env in {"longtable", "tabularx"} else "tabular"
    lines: list[str] = []
    wrap_table = env in {"tabular", "tabularx"}
    if wrap_table:
        lines.append(f"\\begin{{table}}[{model.placement or 'H'}]")
        lines.append("\\centering")
        if model.caption:
            lines.append(f"\\caption{{{convert_inline(model.caption)}}}")
        if getattr(model, "label", ""):
            lines.append(f"\\label{{{model.label}}}")
    if env == "tabularx":
        # Prefer >{\centering}X when cells are center-aligned and bordered
        x_cols: list[str] = []
        for c in cols:
            if c.kind == "X":
                x_cols.append(r">{\centering\arraybackslash}X")
            else:
                x_cols.append(c.to_latex())
        if model.bordered:
            x_spec = "|" + "|".join(x_cols) + "|"
        else:
            x_spec = "".join(x_cols)
        lines.append(f"\\begin{{tabularx}}{{\\linewidth}}{{{x_spec}}}")
    else:
        lines.append(f"\\begin{{{env}}}{{{colspec}}}")
    if model.bordered:
        lines.append("\\hline")

    body_input = (model.body_input or "").strip()
    header_n = max(0, int(model.header_rows or 0)) if body_input else len(cells)

    for ri, row in enumerate(cells[:header_n] if body_input else cells):
        parts = _format_table_row_parts(row, ncols, model.bordered, cols=cols)
        # Entirely covered by a rowspan above → keep blank-row height with a strut.
        if not parts:
            cover_cs = 0
            for rr in range(ri, -1, -1):
                for cell in cells[rr]:
                    if (
                        cell
                        and not cell.skip
                        and rr + max(1, cell.rowspan) - 1 >= ri
                    ):
                        cover_cs = max(1, cell.colspan)
                        break
                if cover_cs:
                    break
            if cover_cs >= ncols and model.bordered:
                lines.append(
                    rf"\multicolumn{{{ncols}}}{{|c|}}{{\rule{{0pt}}{{2.8em}}}} \\"
                )
            elif cover_cs >= ncols:
                lines.append(rf"\multicolumn{{{ncols}}}{{c}}{{\rule{{0pt}}{{2.8em}}}} \\")
            else:
                lines.append(r"\rule{0pt}{2.8em} \\")
        else:
            lines.append(" & ".join(parts) + r" \\")
        draw_hline = True
        row_clines: list[str] = []
        if model.clines and ri < len(model.clines):
            row_clines = [c for c in model.clines[ri] if c]
        if model.hlines and ri < len(model.hlines):
            draw_hline = model.hlines[ri]
        _append_row_border_rules(
            lines,
            cells=cells,
            row_index=ri,
            ncols=ncols,
            bordered=bool(model.bordered),
            draw_hline=draw_hline,
            row_clines=row_clines,
        )

    if body_input:
        rel = body_input.replace("\\", "/").strip()
        if not rel.endswith(".tex"):
            rel = f"{rel}.tex"
        # Must use \\erpTabInput (\\@@input), not \\input — see main.tex.
        lines.append(f"\\erpTabInput{{{rel}}}")

    lines.append(f"\\end{{{env}}}")
    if wrap_table:
        lines.append("\\end{table}")
    return "\n".join(lines)


def patch_cell_texts_in_latex(
    original: str, old_model: TableModel, new_model: TableModel
) -> str:
    """Replace cell plain texts inside an existing tabular chunk (preserve layout)."""
    out = original
    old_cells = [c for row in old_model.cells for c in row if not c.skip]
    new_cells = [c for row in new_model.cells for c in row if not c.skip]
    for oc, nc in zip(old_cells, new_cells):
        if (oc.text or "") == (nc.text or ""):
            continue
        new_tex = convert_inline((nc.text or "").replace("\n", r"\\"))
        old_tex = oc.text or ""
        if old_tex and old_tex in out:
            out = out.replace(old_tex, new_tex, 1)
            continue
        # Date row etc.: plain text differs from TeX (\\hspace{2em})
        if old_tex.startswith("签发时间") and "签发时间" in out:
            m = re.search(
                r"(签发时间：)(?:\\hspace\{[^}]+\}|[^\\&])*?(?=\\\\|\})",
                out,
            )
            if m:
                out = out[: m.start()] + "签发时间：" + new_tex.replace("签发时间：", "", 1) + out[m.end() :]
    return out


def table_structure_equal(a: TableModel, b: TableModel) -> bool:
    """True when grid shape / merges / hlines match (cell texts ignored)."""
    if len(a.cells) != len(b.cells) or len(a.cols) != len(b.cols):
        return False
    for ra, rb in zip(a.cells, b.cells):
        if len(ra) != len(rb):
            return False
        for ca, cb in zip(ra, rb):
            if bool(ca.skip) != bool(cb.skip):
                return False
            if ca.skip:
                continue
            if max(1, ca.colspan) != max(1, cb.colspan):
                return False
            if max(1, ca.rowspan) != max(1, cb.rowspan):
                return False
    n = len(a.cells)
    ha = list(a.hlines or [])
    hb = list(b.hlines or [])
    ha = (ha + [True] * n)[:n]
    hb = (hb + [True] * n)[:n]
    return ha == hb


def _cell_inner_tex(token: str) -> str:
    """Innermost TeX content of a cell token (unwrap multicolumn/multirow)."""
    token = (token or "").strip()
    if not token:
        return ""
    if token.startswith(r"\multicolumn"):
        i = len(r"\multicolumn")
        while i < len(token) and token[i].isspace():
            i += 1
        _, i = _read_group(token, i)
        while i < len(token) and token[i].isspace():
            i += 1
        _, i = _read_group(token, i)
        while i < len(token) and token[i].isspace():
            i += 1
        content, _ = _read_group(token, i)
        return _cell_inner_tex(content)
    if token.startswith(r"\multirow"):
        i = len(r"\multirow")
        while i < len(token) and token[i].isspace():
            i += 1
        _, i = _read_group(token, i)
        while i < len(token) and token[i].isspace():
            i += 1
        _, i = _read_group(token, i)
        while i < len(token) and token[i].isspace():
            i += 1
        text, _ = _read_group(token, i)
        return text
    return token


def _multicolumn_align_from_token(token: str) -> str:
    token = (token or "").strip()
    if not token.startswith(r"\multicolumn"):
        return ""
    i = len(r"\multicolumn")
    while i < len(token) and token[i].isspace():
        i += 1
    _, i = _read_group(token, i)
    while i < len(token) and token[i].isspace():
        i += 1
    align_s, _ = _read_group(token, i)
    return (align_s or "").strip()


def _tabular_anchor_tokens(latex: str) -> dict[tuple[int, int], str]:
    """Map (row, col) of non-skip anchors → original cell token strings."""
    env, colspec, body = extract_tabular_chunk(latex)
    cols, _ = parse_colspec(_strip_tex_comments(colspec))
    declared_ncols = max(1, len(cols))
    body = _strip_tex_comments(body).replace("\r\n", "\n")

    rough_rows: list[str] = []
    buf: list[str] = []
    depth = 0
    i = 0
    while i < len(body):
        if body[i] == "{":
            depth += 1
            buf.append(body[i])
            i += 1
        elif body[i] == "}":
            depth = max(0, depth - 1)
            buf.append(body[i])
            i += 1
        elif depth == 0 and body[i : i + 2] == r"\\":
            row_so_far = "".join(buf)
            covered = sum(_parse_cell_token(t)[1] for t in _split_row_cells(row_so_far))
            rest = body[i + 2 :].lstrip()
            row_boundary = (
                not rest
                or rest.startswith(r"\hline")
                or rest.startswith(r"\cline")
                or rest.startswith(r"\end")
            )
            if covered < declared_ncols and rest and not row_boundary:
                buf.append("\n")
                i += 2
                continue
            rough_rows.append(row_so_far)
            buf = []
            i += 2
        else:
            buf.append(body[i])
            i += 1
    if buf or not rough_rows:
        rough_rows.append("".join(buf))

    tokens_out: dict[tuple[int, int], str] = {}
    occ: list[list[bool]] = []
    ncols = declared_ncols

    def ensure_row(r: int) -> None:
        while len(occ) <= r:
            occ.append([False] * ncols)

    for rough in rough_rows:
        rough = rough.replace(r"\hline", "")
        rough = re.sub(r"\\cline\{[^}]+\}", "", rough).strip()
        if not rough:
            continue
        r = 0
        while True:
            ensure_row(r)
            if any(not occ[r][c] for c in range(ncols)):
                break
            r += 1
        ensure_row(r)
        c = 0
        for token in _split_row_cells(rough):
            text, cs, rs, _align, _col_spec = _parse_cell_token(token)
            while True:
                ensure_row(r)
                if c >= declared_ncols and not text and cs == 1 and rs == 1:
                    c += 1
                    break
                while ncols < max(c + 1, c + cs, declared_ncols):
                    ncols += 1
                    for row in occ:
                        row.append(False)
                if c < ncols and occ[r][c]:
                    if not text and cs == 1 and rs == 1:
                        c += 1
                        break
                    c += 1
                    continue
                ensure_row(r + rs - 1)
                tokens_out[(r, c)] = token.strip()
                for dr in range(rs):
                    ensure_row(r + dr)
                    for dc in range(cs):
                        occ[r + dr][c + dc] = True
                c += cs
                break
    return tokens_out


def rebuild_front_matter_tabular(
    original: str, old_model: TableModel, new_model: TableModel
) -> str:
    """Rebuild a bare tabular for front-matter when merges/structure change.

    Keeps the original colspec and preserves unchanged cells' raw TeX
    (macros like ``\\reportname``, ``\\hspace``, ``\\raggedright``, …).
    Does **not** wrap in ``table`` environment.
    """
    env, colspec, _body = extract_tabular_chunk(original)
    if env not in {"tabular", "tabularx", "longtable"}:
        env = "tabular"
    cols = new_model.cols or old_model.cols or [ColSpec("l")]
    ncols = len(cols)
    cells = new_model.cells or []
    for row in cells:
        while len(row) < ncols:
            row.append(GridCell(skip=True))
    token_map = _tabular_anchor_tokens(original)
    bordered = bool(new_model.bordered)

    lines: list[str] = [f"\\begin{{{env}}}{{{colspec}}}"]
    if bordered:
        lines.append("\\hline")

    for ri, row in enumerate(cells):
        parts: list[str] = []
        ci = 0
        while ci < ncols:
            cell = row[ci] if ci < len(row) else GridCell(skip=True)
            if cell.skip:
                ci += 1
                continue
            cs, rs = max(1, cell.colspan), max(1, cell.rowspan)
            old_tok = token_map.get((ri, ci), "")
            old_cell = None
            if (
                ri < len(old_model.cells)
                and ci < len(old_model.cells[ri])
                and not old_model.cells[ri][ci].skip
            ):
                old_cell = old_model.cells[ri][ci]
            if old_tok and old_cell is not None and max(1, old_cell.colspan) == cs:
                inner = _cell_inner_tex(old_tok)
                align_spec = _multicolumn_align_from_token(old_tok)
            else:
                raw = (cell.text or "").strip()
                from .structured_blocks import kw_marks_to_commands

                raw = kw_marks_to_commands(raw)
                cell_src = kw_marks_to_commands(cell.text or "")
                if raw.startswith("\\") and (
                    re.fullmatch(r"\\[a-zA-Z]+(?:\{\})?", raw)
                    or raw.startswith(r"\makecell")
                    or raw.startswith(r"\rule")
                ):
                    inner = raw
                else:
                    inner = convert_inline(cell_src)
                align_spec = ""
            if not align_spec:
                align_spec = _cell_align_spec(
                    cell.align, bordered, at_left=(ci == 0)
                )
            align_spec = _normalize_multicolumn_align(
                align_spec, at_left=(ci == 0), bordered=bordered
            )
            if cs > 1 and rs > 1:
                parts.append(
                    rf"\multicolumn{{{cs}}}{{{align_spec}}}{{\multirow{{{rs}}}{{=}}{{{inner}}}}}"
                )
            elif cs > 1:
                parts.append(rf"\multicolumn{{{cs}}}{{{align_spec}}}{{{inner}}}")
            elif rs > 1:
                parts.append(rf"\multirow{{{rs}}}{{=}}{{{inner}}}")
            else:
                parts.append(inner)
            ci += cs
        if not parts:
            # Continuation under a vertical merge: keep blank-row height
            # (declaration signature row used \rule{0pt}{2.8em}).
            cover_cs = 0
            for rr in range(ri, -1, -1):
                for cell in cells[rr]:
                    if (
                        cell
                        and not cell.skip
                        and rr + max(1, cell.rowspan) - 1 >= ri
                    ):
                        cover_cs = max(1, cell.colspan)
                        break
                if cover_cs:
                    break
            if cover_cs >= ncols and bordered:
                lines.append(
                    rf"\multicolumn{{{ncols}}}{{|c|}}{{\rule{{0pt}}{{2.8em}}}} \\"
                )
            elif cover_cs >= ncols:
                lines.append(rf"\multicolumn{{{ncols}}}{{c}}{{\rule{{0pt}}{{2.8em}}}} \\")
            else:
                lines.append(r"\rule{0pt}{2.8em} \\")
        else:
            lines.append(" & ".join(parts) + r" \\")

        draw_hline = True
        row_clines: list[str] = []
        if new_model.clines and ri < len(new_model.clines):
            row_clines = [c for c in new_model.clines[ri] if c]
        if new_model.hlines and ri < len(new_model.hlines):
            draw_hline = new_model.hlines[ri]
        _append_row_border_rules(
            lines,
            cells=cells,
            row_index=ri,
            ncols=ncols,
            bordered=bordered,
            draw_hline=draw_hline,
            row_clines=row_clines,
        )

    lines.append(f"\\end{{{env}}}")
    return "\n".join(lines)


def model_to_preview_html(model: TableModel) -> str:
    """Read-only HTML preview for the main editor (math left as $...$ for KaTeX).

    Caption is omitted here — the editor renders numbered 表题 above the table.
    """
    parts = ['<table class="se-preview-table">']
    # colgroup widths — distribute by cm when available
    if model.cols:
        parts.append("<colgroup>")
        cm_vals: list[float] = []
        for col in model.cols:
            m = re.match(r"([\d.]+)\s*cm", col.width or "")
            cm_vals.append(float(m.group(1)) if m else 0.0)
        known = [v for v in cm_vals if v > 0]
        # If only some columns have explicit cm, don't let them take 100%.
        if known and len(known) < len(cm_vals):
            avg = (sum(known) / len(known)) if known else 1.0
            cm_vals = [v if v > 0 else avg for v in cm_vals]
        total_cm = sum(cm_vals) or 0.0
        for col, cm in zip(model.cols, cm_vals):
            style = ""
            if total_cm > 0 and cm > 0:
                pct = max(4.0, cm / total_cm * 100.0)
                style = f' style="width:{pct:.1f}%"'
            elif col.width:
                m = re.match(r"([\d.]+)\s*cm", col.width)
                if m:
                    px = max(48, int(float(m.group(1)) * 28))
                    style = f' style="width:{px}px"'
                elif "mylen" in (col.width or "") or "dimexpr" in (col.width or ""):
                    style = ' style="width:22%"'
            elif col.kind == "X":
                style = ' style="width:50%"'
            parts.append(f"<col{style}>")
        parts.append("</colgroup>")
    for row in model.cells:
        visible = any(not c.skip for c in row)
        if not visible:
            # Continuation under rowspan — keep blank-line height (empty <tr> collapses).
            parts.append('<tr class="se-rowspan-pad" style="height:2.8em"></tr>')
            continue
        parts.append("<tr>")
        for cell in row:
            if cell.skip:
                continue
            attrs = []
            if cell.colspan > 1:
                attrs.append(f'colspan="{cell.colspan}"')
            rs = max(1, cell.rowspan)
            if rs > 1:
                attrs.append(f'rowspan="{rs}"')
            styles = ["white-space:pre-wrap", "word-break:break-word"]
            # Keep blank space under vertically merged cells (e.g. 签发时间).
            if rs > 1:
                styles.append("vertical-align:top")
                styles.append(f"height:{2.6 * rs:.1f}em")
            else:
                styles.append("vertical-align:middle")
            if cell.align == "c":
                styles.append("text-align:center")
            elif cell.align == "r":
                styles.append("text-align:right")
            elif cell.align == "l":
                styles.append("text-align:left")
            attrs.append(f'style="{";".join(styles)}"')
            attr = (" " + " ".join(attrs)) if attrs else ""
            parts.append(f"<td{attr}>{_cell_html(cell.text)}</td>")
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def _cell_html(text: str) -> str:
    """Escape cell text but keep ``$math$`` / personalized ``§kw§`` as hooks."""
    if not text:
        return ""

    out: list[str] = []
    last = 0
    # Prefer kw marks over math when scanning left-to-right.
    token_re = re.compile(
        r"§kw\{([a-zA-Z]+)\}§(.*?)§/kw§|\$([^$]+)\$",
        re.S,
    )
    for m in token_re.finditer(text):
        out.append(html.escape(text[last : m.start()]).replace("\n", "<br>"))
        if m.group(1) is not None:
            cmd = m.group(1)
            body = m.group(2) or ""
            out.append(
                f'<span class="se-kw" contenteditable="false" data-cmd="{html.escape(cmd, quote=True)}" '
                f'title="个性化字段：\\{html.escape(cmd, quote=True)}">'
                f"{html.escape(body).replace(chr(10), '<br>')}</span>"
            )
        else:
            latex = m.group(3) or ""
            out.append(
                f'<span class="se-math" contenteditable="false" data-latex="{html.escape(latex, quote=True)}"></span>'
            )
        last = m.end()
    out.append(html.escape(text[last:]).replace("\n", "<br>"))
    return "".join(out)
