#!/usr/bin/env python3
"""Build LaTeX from MinerU auto/*.json using bbox coordinates (0–1000 normalized)."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from converter.detection_report_md2latex import html_table_to_body_tabular, normalize_email
from converter.md2latex import convert_inline

PAGE_W_PT = 595.3
PAGE_H_PT = 841.9
NORM = 1000.0
FONT_SNAP = [10.45, 12.0, 14.05, 18.0, 21.95, 26.05, 42.0]

TYPE_Z = {"table": 0, "image": 1, "text": 2, "discarded": 3}


def load_content_list(auto_dir: Path) -> list[dict[str, Any]]:
    path = auto_dir / "DSA_content_list.json"
    if not path.is_file():
        for candidate in auto_dir.glob("*_content_list.json"):
            path = candidate
            break
    if not path.is_file():
        raise FileNotFoundError(f"No *_content_list.json in {auto_dir}")
    return json.loads(path.read_text(encoding="utf-8"))


def bbox_to_pt(bbox: list[float]) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = bbox
    left = x0 / NORM * PAGE_W_PT
    top = y0 / NORM * PAGE_H_PT
    width = (x1 - x0) / NORM * PAGE_W_PT
    height = (y1 - y0) / NORM * PAGE_H_PT
    return left, top, width, height


def snap_font_size(size_pt: float) -> float:
    return min(FONT_SNAP, key=lambda s: abs(s - size_pt))


def estimate_font_size(bbox: list[float], text: str, text_level: int | None = None) -> float:
    _, _, width, height = bbox_to_pt(bbox)
    if text_level == 1:
        if height >= 75:
            return 42.0
        if height >= 48:
            return 26.05
        if height >= 28:
            return 21.95
        return 14.05
    if height <= 16:
        return 10.45
    if height > 18 and len(text) < 30:
        return snap_font_size(max(12.0, height * 0.45))
    chars = max(len(text.replace(" ", "")), 1)
    lines_est = max(1, round(height / 16))
    size = min(height / lines_est * 0.82, 14.05)
    return snap_font_size(max(10.45, size))


def clean_text(text: str) -> str:
    text = normalize_email(text) if "@" in text or "$" in text else text
    text = re.sub(r"\s+o\s*$", "", text.strip())
    return text


def is_english_line(text: str) -> bool:
    letters = re.findall(r"[A-Za-z]", text)
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    return len(letters) > len(cjk) * 2


def font_open(size: float, text: str, text_level: int | None) -> str:
    if is_english_line(text) and text_level != 1:
        return rf"\fontsize{{{size:.2f}bp}}{{{size * 1.25:.2f}bp}}\selectfont\rmfamily "
    if size >= 36:
        return rf"\fontsize{{{size:.2f}bp}}{{{size * 1.2:.2f}bp}}\selectfont\songti "
    if size >= 20:
        return rf"\fontsize{{{size:.2f}bp}}{{{size * 1.28:.2f}bp}}\selectfont\songti "
    return rf"\fontsize{{{size:.2f}bp}}{{{size * 1.35:.2f}bp}}\selectfont\songti "


def html_to_resizable_tabular(html: str, width_pt: float, height_pt: float) -> str:
    body = html_table_to_body_tabular(html, wrap_env=False)
    inner = body.strip()
    # Scale to bbox width; cap height when table is taller than bbox.
    return (
        rf"\begin{{minipage}}[t]{{{width_pt:.2f}pt}}"
        rf"\begin{{adjustbox}}{{width=\linewidth,max height={height_pt:.2f}pt,center}}{{%"
        + inner
        + "}}\\end{adjustbox}\\end{minipage}"
    )


def render_text_node(
    text: str,
    bbox: list[float],
    *,
    text_level: int | None = None,
    force_size: float | None = None,
) -> str:
    left, top, width, height = bbox_to_pt(bbox)
    text = clean_text(text)
    if not text:
        return ""
    size = force_size or estimate_font_size(bbox, text, text_level)
    body = convert_inline(text)
    fo = font_open(size, text, text_level)
    align = "align=center" if width > PAGE_W_PT * 0.5 and len(text) < 40 else "align=left"
    return (
        rf"\node[anchor=north west,inner sep=0pt,text width={width:.2f}pt,{align}] "
        rf"at ([x={left:.2f}pt,y={-top:.2f}pt]current page.north west) {{{fo}{body}}};"
    )


def render_table_node(item: dict[str, Any]) -> str:
    html = item.get("table_body") or ""
    if not html:
        return ""
    left, top, width, height = bbox_to_pt(item["bbox"])
    parts: list[str] = []
    captions = item.get("table_caption") or []
    if captions:
        cap = clean_text(captions[0])
        x0, y0, x1, _y1 = item["bbox"]
        cap_bbox = [x0, max(0.0, y0 - 18.0), x1, y0]
        parts.append(render_text_node(cap, cap_bbox, force_size=12.0))
    tab = html_to_resizable_tabular(html, width, height)
    parts.append(
        rf"\node[anchor=north west,inner sep=0pt] "
        rf"at ([x={left:.2f}pt,y={-top:.2f}pt]current page.north west) {{{tab}}};"
    )
    return "\n".join(p for p in parts if p)


def render_image_node(item: dict[str, Any], auto_dir: Path) -> str:
    path = item.get("img_path") or ""
    if not path:
        return ""
    img_file = auto_dir / path.replace("\\", "/")
    if not img_file.is_file():
        img_file = auto_dir.parent / path.replace("\\", "/")
    if not img_file.is_file():
        return f"% missing image: {path}"
    rel = path.replace("\\", "/")
    left, top, width, height = bbox_to_pt(item["bbox"])
    return (
        rf"\node[anchor=north west,inner sep=0pt] "
        rf"at ([x={left:.2f}pt,y={-top:.2f}pt]current page.north west) "
        rf"{{\includegraphics[width={width:.2f}pt,height={height:.2f}pt]{{{rel}}}}};"
    )


def render_element(item: dict[str, Any], auto_dir: Path) -> str:
    kind = item.get("type", "text")
    if kind == "table":
        return render_table_node(item)
    if kind == "image":
        return render_image_node(item, auto_dir)
    text = item.get("text") or ""
    if not text.strip():
        return ""
    return render_text_node(
        text,
        item["bbox"],
        text_level=item.get("text_level"),
        force_size=10.45 if kind == "discarded" else None,
    )


def render_page(page_idx: int, items: list[dict[str, Any]], auto_dir: Path) -> str:
    ordered = sorted(
        items,
        key=lambda e: (
            e.get("bbox", [0, 0, 0, 0])[1],
            e.get("bbox", [0, 0, 0, 0])[0],
            TYPE_Z.get(e.get("type", "text"), 9),
        ),
    )
    nodes = [render_element(item, auto_dir) for item in ordered]
    body = "\n".join(n for n in nodes if n)
    if page_idx > 0:
        prefix = "\\newpage\n\\thispagestyle{empty}\n"
    else:
        prefix = "\\thispagestyle{empty}\n"
    return (
        prefix
        + "\\begin{tikzpicture}[remember picture,overlay]\n"
        + body
        + "\n\\end{tikzpicture}\n"
    )


def render_main_tex() -> str:
    return r"""\documentclass[12pt,a4paper]{article}
\usepackage[fontset=windows]{ctex}
\usepackage[margin=0pt]{geometry}
\usepackage{graphicx}
\usepackage{tikz}
\usepackage{array}
\usepackage{multirow}
\usepackage{calc}
\usepackage{ragged2e}
\usepackage{makecell}
\usepackage{adjustbox}
\input{coords_styles.tex}
\geometry{paper=a4paper,layout=a4paper,includeheadfoot=false}
\setlength{\parindent}{0pt}
\pagestyle{empty}
\begin{document}
\input{pages.tex}
\end{document}
"""

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates" / "detection_report"


def build_coords_latex(auto_dir: Path, output_dir: Path) -> Path:
    auto_dir = auto_dir.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        try:
            shutil.rmtree(output_dir)
        except OSError:
            pass
    output_dir.mkdir(parents=True, exist_ok=True)

    items = load_content_list(auto_dir)
    by_page: dict[int, list[dict[str, Any]]] = {}
    for item in items:
        page = int(item.get("page_idx", 0))
        by_page.setdefault(page, []).append(item)

    pages_tex: list[str] = []
    for page_idx in sorted(by_page.keys()):
        pages_tex.append(render_page(page_idx, by_page[page_idx], auto_dir))

    (output_dir / "main.tex").write_text(render_main_tex(), encoding="utf-8")
    (output_dir / "pages.tex").write_text("\n".join(pages_tex), encoding="utf-8")

    styles_src = TEMPLATE_DIR / "coords_styles.tex"
    shutil.copy2(styles_src, output_dir / "coords_styles.tex")

    for name in ("images", "imgs"):
        src = auto_dir / name
        if src.is_dir():
            shutil.copytree(src, output_dir / name)
            break

    return output_dir
