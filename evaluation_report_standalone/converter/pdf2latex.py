#!/usr/bin/env python3
"""Convert PDF to LaTeX with fixed-width tables and composite figures.

Pipeline (recommended when both files exist):
  PDF + companion DOCX  ->  word2md (tables/text)  +  PDF figure compositing  ->  LaTeX

Tables use PDF-detected column width ratios; figures under one caption are merged
into a single image preserving layout from the PDF coordinates.
"""

from __future__ import annotations

import argparse
import io
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF
import pdfplumber
from PIL import Image

from converter.md2latex import (
    IMAGE_RE,
    MergeAnchor,
    _row_needs_hline_after,
    convert_cell_content,
    convert_inline,
    html_rows_to_anchors,
    html_table_has_merges,
    parse_figure_caption_line,
    parse_html_table,
    process_lines,
)
from converter.word2md import convert_docx_to_markdown

STANDALONE_PREAMBLE = textwrap.dedent(
    r"""
    \documentclass[12pt,a4paper]{article}
    \usepackage[fontset=fandol]{ctex}
    \usepackage{array,multirow,graphicx,geometry,float}
    \geometry{margin=2.5cm}
    \newcolumntype{P}[1]{>{\raggedright\arraybackslash}p{#1}}
    \newlength{\tabrestwidth}
    \begin{document}
    \zihao{-4}\songti
    """
).strip()

STANDALONE_POSTAMBLE = r"\end{document}"

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class PdfPageLayout:
    """Single PDF page size and orientation (A4 portrait ≈ 595×842 pt)."""

    page_index: int
    width_pt: float
    height_pt: float
    rotation: int
    orientation: str  # "portrait" | "landscape"

    @property
    def page_number(self) -> int:
        return self.page_index + 1


def extract_page_layouts(pdf_path: Path) -> list[PdfPageLayout]:
    """Read each page's MediaBox size and infer portrait/landscape.

    Landscape when width > height (after applying rotation metadata).
    A4 reference: portrait 595×842 pt, landscape 842×595 pt.
    """
    doc = fitz.open(pdf_path)
    layouts: list[PdfPageLayout] = []
    for i, page in enumerate(doc):
        w, h = page.rect.width, page.rect.height
        rot = int(page.rotation) % 360
        if rot in (90, 270):
            landscape = h > w
        else:
            landscape = w > h
        layouts.append(
            PdfPageLayout(
                page_index=i,
                width_pt=w,
                height_pt=h,
                rotation=rot,
                orientation="landscape" if landscape else "portrait",
            )
        )
    doc.close()
    return layouts


def format_page_layouts(layouts: list[PdfPageLayout]) -> str:
    lines = ["page\torientation\twidth_pt\theight_pt\trotation"]
    for p in layouts:
        lines.append(
            f"{p.page_number}\t{p.orientation}\t{p.width_pt:.1f}\t{p.height_pt:.1f}\t{p.rotation}"
        )
    return "\n".join(lines)


@dataclass
class PdfImageRect:
    page_index: int
    xref: int
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class PdfFigureGroup:
    page_index: int
    caption: str
    images: list[PdfImageRect] = field(default_factory=list)


@dataclass
class PdfTableMeta:
    page_index: int
    bbox: tuple[float, float, float, float]
    col_fractions: list[float]


# ---------------------------------------------------------------------------
# PDF table column geometry
# ---------------------------------------------------------------------------


def _logical_column_boundaries(table) -> list[float]:
    data = table.extract() or []
    if not data:
        return []
    ncols = max(len(row) for row in data)
    left = [None] * ncols
    right = [None] * ncols
    for row in table.rows:
        for ci, cell in enumerate(row.cells):
            if cell is None or ci >= ncols:
                continue
            x0, _t, x1, _b = cell
            if left[ci] is None or x0 < left[ci]:
                left[ci] = x0
            if right[ci] is None or x1 > right[ci]:
                right[ci] = x1
    bounds: list[float] = []
    for i in range(ncols):
        if left[i] is not None and right[i] is not None:
            bounds.extend([left[i], right[i]])
    if not bounds:
        x0, _t, x1, _b = table.bbox
        step = (x1 - x0) / ncols
        return [x0 + step * i for i in range(ncols + 1)]
    edges = sorted(set(bounds))
    if len(edges) < ncols + 1:
        x0, _t, x1, _b = table.bbox
        return [x0 + (x1 - x0) * i / ncols for i in range(ncols + 1)]
    while len(edges) > ncols + 1:
        # merge closest adjacent edges
        gaps = [(edges[i + 1] - edges[i], i) for i in range(len(edges) - 1)]
        _gap, idx = min(gaps)
        edges.pop(idx + 1)
    while len(edges) < ncols + 1:
        edges.append(edges[-1] + 1.0)
    return edges[: ncols + 1]


def column_fractions_from_pdf_table(table) -> list[float]:
    edges = _logical_column_boundaries(table)
    if len(edges) < 2:
        return []
    widths = [max(edges[i + 1] - edges[i], 1.0) for i in range(len(edges) - 1)]
    total = sum(widths)
    return [w / total for w in widths]


def extract_pdf_table_meta(pdf_path: Path) -> list[PdfTableMeta]:
    meta: list[PdfTableMeta] = []
    with pdfplumber.open(pdf_path) as pdf:
        for pi, page in enumerate(pdf.pages):
            for table in page.find_tables():
                fr = column_fractions_from_pdf_table(table)
                meta.append(PdfTableMeta(pi, table.bbox, fr))
    return meta


# ---------------------------------------------------------------------------
# Fixed-width LaTeX tables (P{} columns, single-level \\dimexpr)
# ---------------------------------------------------------------------------

FIRST_COL_CM = 2.0


def _table_mylen_setup(ncols: int) -> str:
    if ncols <= 1:
        return ""
    rest = ncols - 1
    sep = 2 * rest
    rules = ncols + 1
    return (
        f"\\setlength{{\\tabrestwidth}}{{\\dimexpr"
        f"\\textwidth - {FIRST_COL_CM}cm - {sep}\\tabcolsep - {rules}\\arrayrulewidth"
        f"\\relax}}"
    )


def _col_widths(ncols: int, fractions: list[float] | None) -> tuple[list[str], list[int]]:
    """Return (LaTeX width exprs, weight numerators for rest columns)."""
    if ncols <= 0:
        return [], []
    if ncols == 1:
        return ["\\textwidth"], []
    rest = ncols - 1
    if fractions and len(fractions) == ncols and fractions[0] < 0.2:
        weights = [max(int(round(f * 1000)), 1) for f in fractions[1:]]
        total = sum(weights) or 1
        widths = [f"{FIRST_COL_CM}cm"]
        for w in weights:
            widths.append(f"\\dimexpr\\tabrestwidth*{w}/{total}\\relax")
        return widths, weights
    widths = [f"{FIRST_COL_CM}cm"] + [f"\\dimexpr\\tabrestwidth/{rest}\\relax"] * rest
    return widths, [1] * rest


def _span_width(
    start: int,
    span: int,
    widths: list[str],
    weights: list[int],
) -> str:
    if span <= 1:
        return widths[start]
    gap = f"{(span - 1) * 2}\\tabcolsep + {span - 1}\\arrayrulewidth"
    total = sum(weights) or 1
    if start == 0 and widths[0].endswith("cm"):
        rest_span = span - 1
        if rest_span <= 0:
            return widths[0]
        wsum = sum(weights[:rest_span])
        return f"\\dimexpr {FIRST_COL_CM}cm + \\tabrestwidth*{wsum}/{total} + {gap}\\relax"
    wsum = sum(weights[start - 1 : start - 1 + span])
    return f"\\dimexpr \\tabrestwidth*{wsum}/{total} + {gap}\\relax"


def _format_fixed_cell(anchor: MergeAnchor, widths: list[str], weights: list[int]) -> str:
    text = convert_cell_content(anchor.text)
    if anchor.colspan > 1 and anchor.rowspan > 1:
        w = _span_width(anchor.col, anchor.colspan, widths, weights)
        inner = rf"\multirow{{{anchor.rowspan}}}{{*}}{{{text}}}"
        return rf"\multicolumn{{{anchor.colspan}}}{{|P{{{w}}}|}}{{{inner}}}"
    if anchor.colspan > 1:
        w = _span_width(anchor.col, anchor.colspan, widths, weights)
        return rf"\multicolumn{{{anchor.colspan}}}{{|P{{{w}}}|}}{{{text}}}"
    if anchor.rowspan > 1:
        return rf"\multirow{{{anchor.rowspan}}}{{*}}{{{text}}}"
    return text


def merged_anchors_to_fixed_latex(
    nrows: int,
    ncols: int,
    anchors: dict[tuple[int, int], MergeAnchor],
    col_fractions: list[float] | None = None,
    caption: str | None = None,
) -> str:
    widths, weights = _col_widths(ncols, col_fractions)
    colspec = "|" + "|".join(f"P{{{w}}}" for w in widths) + "|"

    lines = ["\\vspace{10mm}", "\\begin{center}"]
    setup = _table_mylen_setup(ncols)
    if setup:
        lines.append(setup)
    if caption:
        lines.append(f"\\textbf{{{convert_inline(caption)}}}\\par\\vspace{{2mm}}")
    lines.append(f"\\begin{{tabular}}{{{colspec}}}")
    lines.append("\\hline")

    skip: set[tuple[int, int]] = set()
    for anchor in anchors.values():
        if anchor.rowspan > 1:
            for r in range(anchor.row + 1, anchor.row + anchor.rowspan):
                for c in range(anchor.col, anchor.col + anchor.colspan):
                    skip.add((r, c))

    for ri in range(nrows):
        parts: list[str] = []
        ci = 0
        while ci < ncols:
            if (ri, ci) in skip:
                parts.append("")
                ci += 1
                continue
            anchor = anchors.get((ri, ci))
            if anchor is None:
                parts.append("")
                ci += 1
                continue
            parts.append(_format_fixed_cell(anchor, widths, weights))
            ci += anchor.colspan
        if parts:
            lines.append(" & ".join(parts) + r" \\")
            if ri + 1 < nrows and _row_needs_hline_after(ri, anchors):
                lines.append("\\hline")
            elif ri == nrows - 1:
                lines.append("\\hline")

    lines.extend(["\\end{tabular}", "\\end{center}"])
    return "\n".join(lines)


def html_table_to_fixed_latex(
    html: str,
    col_fractions: list[float] | None = None,
    caption: str | None = None,
) -> str:
    rows = parse_html_table(html)
    if html_table_has_merges(rows):
        nrows, ncols, anchors = html_rows_to_anchors(rows)
        return merged_anchors_to_fixed_latex(nrows, ncols, anchors, col_fractions, caption)
    converted = [[convert_cell_content(cell.text) for cell in row] for row in rows]
    ncols = max((len(r) for r in converted), default=0)
    widths, _weights = _col_widths(ncols, col_fractions)
    colspec = "|" + "|".join(f"P{{{w}}}" for w in widths) + "|"
    lines = ["\\vspace{10mm}", "\\begin{center}"]
    setup = _table_mylen_setup(ncols)
    if setup:
        lines.append(setup)
    if caption:
        lines.append(f"\\textbf{{{convert_inline(caption)}}}\\par\\vspace{{2mm}}")
    lines.extend([f"\\begin{{tabular}}{{{colspec}}}", "\\hline"])
    for row in converted:
        padded = row + [""] * (ncols - len(row))
        lines.append(" & ".join(padded) + r" \\")
        lines.append("\\hline")
    lines.extend(["\\end{tabular}", "\\end{center}"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# PDF figure detection & compositing
# ---------------------------------------------------------------------------


def _lines_from_words(words: list[dict]) -> list[tuple[float, str]]:
    if not words:
        return []
    rows: dict[float, list[dict]] = {}
    for w in words:
        key = round(w["top"], 1)
        rows.setdefault(key, []).append(w)
    lines: list[tuple[float, str]] = []
    for top in sorted(rows):
        parts = sorted(rows[top], key=lambda w: w["x0"])
        text = "".join(p["text"] for p in parts).strip()
        if text:
            lines.append((top, text))
    return lines


def _page_image_rects(doc: fitz.Document, page_index: int) -> list[PdfImageRect]:
    page = doc[page_index]
    rects: list[PdfImageRect] = []
    seen: set[tuple[int, tuple[float, float, float, float]]] = set()
    for info in page.get_images(full=True):
        xref = info[0]
        for r in page.get_image_rects(xref):
            key = (xref, (round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)))
            if key in seen:
                continue
            seen.add(key)
            area = (r.x1 - r.x0) * (r.y1 - r.y0)
            if area < 800:
                continue
            rects.append(PdfImageRect(page_index, xref, r.x0, r.y0, r.x1, r.y1))
    return sorted(rects, key=lambda x: (x.y0, x.x0))


def _is_figure_caption(text: str) -> bool:
    plain = re.sub(r"\s+", "", text)
    return bool(plain) and plain.endswith("图") and len(plain) >= 4


def extract_pdf_figure_groups(doc: fitz.Document, pdf_path: Path) -> list[PdfFigureGroup]:
    groups: list[PdfFigureGroup] = []
    with pdfplumber.open(pdf_path) as pdf:
        for pi, page in enumerate(pdf.pages):
            lines = _lines_from_words(page.extract_words() or [])
            captions = [(top, text) for top, text in lines if _is_figure_caption(text)]
            imgs = _page_image_rects(doc, pi)
            if not captions:
                continue
            used: set[int] = set()

            def _take_images(cap_top: float, prefer_below: bool) -> list[PdfImageRect]:
                related: list[PdfImageRect] = []
                for idx, img in enumerate(imgs):
                    if idx in used:
                        continue
                    if prefer_below:
                        ok = img.y1 <= cap_top + 12 and cap_top - img.y1 < 420
                    else:
                        ok = img.y0 >= cap_top - 8 and img.y0 < cap_top + 420
                    if ok:
                        related.append(img)
                        used.add(idx)
                return related

            for cap_top, cap_text in captions:
                related = _take_images(cap_top, prefer_below=True)
                if not related:
                    related = _take_images(cap_top, prefer_below=False)
                if related:
                    groups.append(PdfFigureGroup(pi, cap_text, related))
    return groups


def _render_clip(doc: fitz.Document, page_index: int, rect: PdfImageRect, dpi: int = 180) -> Image.Image:
    page = doc[page_index]
    clip = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y1)
    mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")


def composite_figure(group: PdfFigureGroup, doc: fitz.Document, dpi: int = 180) -> Image.Image:
    if len(group.images) == 1:
        return _render_clip(doc, group.page_index, group.images[0], dpi)

    min_x = min(i.x0 for i in group.images)
    min_y = min(i.y0 for i in group.images)
    max_x = max(i.x1 for i in group.images)
    max_y = max(i.y1 for i in group.images)
    scale = dpi / 72.0
    canvas_w = max(1, int((max_x - min_x) * scale))
    canvas_h = max(1, int((max_y - min_y) * scale))
    canvas = Image.new("RGB", (canvas_w, canvas_h), "white")

    for img in group.images:
        piece = _render_clip(doc, group.page_index, img, dpi)
        px = int((img.x0 - min_x) * scale)
        py = int((img.y0 - min_y) * scale)
        piece = piece.resize(
            (max(1, int((img.x1 - img.x0) * scale)), max(1, int((img.y1 - img.y0) * scale))),
            Image.Resampling.LANCZOS,
        )
        canvas.paste(piece, (px, py))
    return canvas


def _normalize_caption(text: str) -> str:
    return re.sub(r"[\s\*]+", "", text)


def save_composite_figures(
    pdf_path: Path,
    images_dir: Path,
    dpi: int = 180,
) -> dict[str, str]:
    """Return mapping normalized_caption -> relative image path."""
    doc = fitz.open(pdf_path)
    groups = extract_pdf_figure_groups(doc, pdf_path)
    images_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, str] = {}
    for idx, group in enumerate(groups, start=1):
        img = composite_figure(group, doc, dpi)
        name = f"figure_{idx:03d}.png"
        out = images_dir / name
        img.save(out, format="PNG")
        rel = f"images/{name}"
        mapping[_normalize_caption(group.caption)] = rel
    doc.close()
    return mapping


def _caption_lookup(caption: str, mapping: dict[str, str]) -> str | None:
    key = _normalize_caption(caption)
    if key in mapping:
        return mapping[key]
    for mk, path in mapping.items():
        if key in mk or mk in key:
            return path
    return None


def replace_md_figures_with_composites(markdown: str, caption_to_path: dict[str, str]) -> str:
    lines = markdown.splitlines()
    out: list[str] = []
    unused = list(caption_to_path.values())
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if IMAGE_RE.match(stripped):
            imgs: list[str] = []
            while i < len(lines):
                s = lines[i].strip()
                if IMAGE_RE.match(s):
                    imgs.append(s)
                    i += 1
                elif not s:
                    i += 1
                else:
                    break
            while i < len(lines) and not lines[i].strip():
                i += 1
            cap_line = lines[i].strip() if i < len(lines) else ""
            cap_info = parse_figure_caption_line(cap_line)
            path: str | None = None
            if cap_info:
                path = _caption_lookup(cap_info[1], caption_to_path)
            if path and path in unused:
                unused.remove(path)
            elif unused and cap_info and imgs:
                path = unused.pop(0)
            if path and imgs:
                out.append(f"![]({path})")
                out.append("")
                out.append(cap_line)
                i += 1
                continue
            out.extend(imgs)
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out).strip() + "\n"


# ---------------------------------------------------------------------------
# Markdown -> LaTeX (fixed tables)
# ---------------------------------------------------------------------------

_table_index = 0
_table_meta: list[PdfTableMeta] = []


def _next_table_fractions() -> list[float] | None:
    global _table_index
    if _table_index < len(_table_meta):
        fr = _table_meta[_table_index].col_fractions
        _table_index += 1
        return fr
    return None


def convert_markdown_to_fixed_latex(
    markdown: str,
    md_path: Path,
    images_dir: Path | None,
    table_meta: list[PdfTableMeta],
) -> str:
    global _table_index, _table_meta
    _table_index = 0
    _table_meta = table_meta

    from converter import md2latex as m2l

    original_html = m2l.html_table_to_latex

    def _fixed_html(html: str, caption: str | None = None) -> str:
        return html_table_to_fixed_latex(html, _next_table_fractions(), caption)

    m2l.html_table_to_latex = _fixed_html
    try:
        lines = markdown.splitlines()
        body = [ln.rstrip("\n") for ln in lines]
        latex_lines = process_lines(body, "legacy", md_path.parent, images_dir)
    finally:
        m2l.html_table_to_latex = original_html

    return "\n".join(latex_lines).strip() + "\n"


# ---------------------------------------------------------------------------
# PDF-only markdown fallback (no DOCX)
# ---------------------------------------------------------------------------


def _grid_to_html_table(data: list[list[str | None]]) -> str:
    if not data:
        return ""
    nrows = len(data)
    ncols = max(len(r) for r in data)
    grid: list[list[str | None]] = []
    for row in data:
        padded = list(row) + [None] * (ncols - len(row))
        grid.append(padded[:ncols])

    anchors: dict[tuple[int, int], tuple[int, int, str]] = {}
    for ri in range(nrows):
        for ci in range(ncols):
            if grid[ri][ci] is None:
                continue
            if any((ri, c) in anchors for c in range(ci)):
                continue
            text = grid[ri][ci] or ""
            colspan = 1
            while ci + colspan < ncols and grid[ri][ci + colspan] is None:
                colspan += 1
            rowspan = 1
            while ri + rowspan < nrows:
                ok = True
                for c in range(ci, ci + colspan):
                    cell = grid[ri + rowspan][c]
                    if cell is not None and cell != text:
                        ok = False
                        break
                if not ok:
                    break
                if colspan == 1 and grid[ri + rowspan][ci] is not None:
                    break
                rowspan += 1
            anchors[(ri, ci)] = (colspan, rowspan, text)

    skip: set[tuple[int, int]] = set()
    for (ri, ci), (_cs, rs, _t) in anchors.items():
        for r in range(ri + 1, ri + rs):
            for c in range(ci, ci + _cs):
                skip.add((r, c))

    lines = ["<table>"]
    for ri in range(nrows):
        cells: list[str] = []
        ci = 0
        while ci < ncols:
            if (ri, ci) in skip:
                ci += 1
                continue
            anchor = anchors.get((ri, ci))
            if anchor is None:
                ci += 1
                continue
            cs, rs, text = anchor
            attrs = []
            if cs > 1:
                attrs.append(f'colspan="{cs}"')
            if rs > 1:
                attrs.append(f'rowspan="{rs}"')
            attr_s = (" " + " ".join(attrs)) if attrs else ""
            cells.append(f"<td{attr_s}>{text}</td>")
            ci += cs
        if cells:
            lines.append(f"<tr>{''.join(cells)}</tr>")
    lines.append("</table>")
    return "\n".join(lines)


def _clean_pdf_text(value: str | None) -> str:
    text = (value or "").replace("\uf052", "☑").replace("\uf0a3", "☐")
    text = text.replace("\uf0fe", "☑").replace("\uf0a8", "☐")
    text = re.sub(r"[\t\r\n]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)


def _grid_to_markdown_table(data: list[list[str | None]]) -> str:
    """Render a PDF table without guessing row/column spans.

    Keeping every physical column is more useful for field extraction than the
    old merge heuristic, which could accidentally swallow adjacent values.
    """
    if not data:
        return ""
    ncols = max(2, max((len(row) for row in data), default=0))
    rows: list[list[str]] = []
    for raw in data:
        row = [_clean_pdf_text(cell).replace("|", r"\|") for cell in raw]
        row.extend([""] * (ncols - len(row)))
        if any(row):
            rows.append(row[:ncols])
    if not rows:
        return ""
    lines = ["| " + " | ".join(rows[0]) + " |"]
    lines.append("| " + " | ".join(["---"] * ncols) + " |")
    lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(lines)


def _line_is_inside_table(top: float, table_bboxes: list[tuple[float, float, float, float]]) -> bool:
    return any(y0 - 1 <= top <= y1 + 1 for _x0, y0, _x1, y1 in table_bboxes)


def pdf_to_markdown(pdf_path: Path) -> str:
    """Extract a text-backed PDF directly with PyMuPDF/pdfplumber.

    No MinerU or remote service is involved. Text and tables are emitted in
    page order; text inside detected table bounds is skipped to avoid duplicate
    field values. Image-only/scanned PDFs fail clearly instead of returning an
    apparently successful but empty conversion.
    """
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)

    blocks: list[str] = []
    page_count = 0
    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)
        for page_number, page in enumerate(pdf.pages, start=1):
            tables = page.find_tables()
            table_bboxes = [tuple(table.bbox) for table in tables]
            events: list[tuple[float, int, str]] = []

            for table in tables:
                markdown_table = _grid_to_markdown_table(table.extract() or [])
                if markdown_table:
                    events.append((float(table.bbox[1]), 1, markdown_table))

            for top, raw_text in _lines_from_words(page.extract_words() or []):
                if _line_is_inside_table(top, table_bboxes):
                    continue
                text = _clean_pdf_text(raw_text)
                if not text or re.fullmatch(r"第\s*\d+\s*页共\s*\d+\s*页", text):
                    continue
                if _is_figure_caption(text):
                    text = f"**{text}**"
                events.append((top, 0, text))

            page_blocks = [content for _top, _kind, content in sorted(events)]
            if page_blocks:
                blocks.append(f"<!-- PDF page {page_number} -->")
                blocks.extend(page_blocks)

    markdown = "\n\n".join(blocks).strip() + "\n"
    searchable = re.sub(r"[\s|#*<>!/\\-]+", "", markdown)
    minimum_chars = max(80, page_count * 20)
    if len(searchable) < minimum_chars:
        raise ValueError(
            "PDF 中未检测到足够的可提取文字。该文件可能是扫描件，请上传可搜索文字的 PDF 或 DOCX。"
        )
    return markdown


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def convert_pdf_to_latex(
    pdf_path: Path,
    output_tex: Path,
    *,
    docx_path: Path | None = None,
    images_dir: Path | None = None,
    dpi: int = 180,
    standalone: bool = True,
) -> str:
    pdf_path = pdf_path.resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)

    docx = docx_path or pdf_path.with_suffix(".docx")
    md_dir = output_tex.parent
    if images_dir is None:
        images_dir = md_dir / "images"
    images_dir = images_dir.resolve()
    images_dir.mkdir(parents=True, exist_ok=True)

    md_path = md_dir / f"{pdf_path.stem}.md"
    if docx.is_file():
        markdown = convert_docx_to_markdown(docx, md_path, images_dir)
    else:
        markdown = pdf_to_markdown(pdf_path)
        md_path.write_text(markdown, encoding="utf-8")

    caption_map = save_composite_figures(pdf_path, images_dir, dpi=dpi)
    markdown = replace_md_figures_with_composites(markdown, caption_map)

    table_meta = extract_pdf_table_meta(pdf_path)
    latex = convert_markdown_to_fixed_latex(
        markdown, md_path, images_dir, table_meta
    )

    if standalone:
        full = STANDALONE_PREAMBLE + "\n\n" + latex + "\n\n" + STANDALONE_POSTAMBLE + "\n"
    else:
        full = (
            "% Auto-generated body by pdf2latex.py — include from main.tex preamble:\n"
            "% \\usepackage{array,multirow,graphicx}\n"
            "% \\newcolumntype{P}[1]{>{\\raggedright\\arraybackslash}p{#1}}\n"
            "% \\newlength{\\tabrestwidth}\n\n"
            + latex
            + "\n"
        )

    output_tex.parent.mkdir(parents=True, exist_ok=True)
    output_tex.write_text(full, encoding="utf-8")
    return full


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert PDF to LaTeX (fixed-width tables + composite figures)."
    )
    parser.add_argument("input", help="Input PDF file")
    parser.add_argument(
        "-o",
        "--output",
        help="Output .tex file (default: same name as PDF)",
    )
    parser.add_argument(
        "--docx",
        help="Companion Word file for better table structure (default: same stem .docx)",
    )
    parser.add_argument(
        "--images-dir",
        help="Directory for composite figures (default: <output-dir>/images)",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=180,
        help="DPI when rasterizing PDF figure regions (default: 180)",
    )
    parser.add_argument(
        "--list-pages",
        action="store_true",
        help="Print page size/orientation (portrait/landscape) and exit",
    )
    parser.add_argument(
        "--fragment",
        action="store_true",
        help="Output body-only fragment (no \\documentclass); default is standalone PDF-ready tex",
    )
    args = parser.parse_args()

    pdf_path = Path(args.input).resolve()
    if args.list_pages:
        for line in format_page_layouts(extract_page_layouts(pdf_path)).splitlines():
            print(line)
        return

    output = Path(args.output).resolve() if args.output else pdf_path.with_suffix(".tex")
    docx = Path(args.docx).resolve() if args.docx else None
    images_dir = Path(args.images_dir).resolve() if args.images_dir else None

    convert_pdf_to_latex(
        pdf_path,
        output,
        docx_path=docx,
        images_dir=images_dir,
        dpi=args.dpi,
        standalone=not args.fragment,
    )
    print(f"Written to {output}")
    print(f"Figures in {images_dir or output.parent / 'images'}")


if __name__ == "__main__":
    main()
