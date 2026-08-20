#!/usr/bin/env python3
"""Generic Markdown → GBZ/T 181 longtable LaTeX project builder (batch-capable)."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from converter.md2latex import (
    HTML_TABLE_RE,
    IMAGE_RE,
    convert_inline,
    convert_cell_content,
    html_table_has_merges,
    html_rows_to_anchors,
    parse_html_table,
    _format_merged_latex_cell,
    _row_needs_hline_after,
)

DEFAULT_TEMPLATE_DIR = (
    Path(__file__).resolve().parents[1]
    / "media"
    / "evaluation_reports"
    / "latex_templates"
    / "template"
)

CN_SECTION_RE = re.compile(r"^#\s*([一二三四五六七八九十]+)[、．.]\s*(.+)$")
SUB_SECTION_RE = re.compile(r"^(\d+\.\d+)\s+(.+)$")
SKIP_HEADING_RE = re.compile(
    r"^(江\s*西|检\s*测|报\s*告|项\s*目|受\s*检|声\s*明|Radiation|TEST|声明|说明：)"
)
REPORT_NO_RE = re.compile(r"报告编号[：:]\s*(.+)")
MD_TITLE_RE = re.compile(r"^#\s+(.+)$")

BUILD_ARTIFACTS = {"main.aux", "main.log", "main.out", "main.pdf", "main.synctex.gz"}


@dataclass
class ConvertOptions:
    layout: str = "document"
    document_title: str = ""
    table_caption: str = ""
    skip_preamble: bool = True
    vars: dict[str, str] = field(default_factory=dict)
    include_header_table: bool = True


def html_table_to_inline_tabular(html: str) -> str:
    rows = parse_html_table(html)
    if not rows:
        return ""
    if html_table_has_merges(rows):
        nrows, ncols, anchors = html_rows_to_anchors(rows)
        col_spec = "|" + "|".join(["p{0.14\\linewidth}"] * ncols) + "|"
        if ncols <= 4:
            col_spec = "|" + "|".join(["p{0.22\\linewidth}"] * ncols) + "|"
        lines = [
            rf"\resizebox{{\linewidth}}{{!}}{{%",
            rf"\begin{{tabular}}{{{col_spec}}}",
            r"\hline",
        ]
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
                parts.append(_format_merged_latex_cell(anchor))
                ci += anchor.colspan
            lines.append(" & ".join(parts) + r" \\")
            if ri + 1 < nrows and _row_needs_hline_after(ri, anchors):
                lines.append(r"\hline")
            elif ri == nrows - 1:
                lines.append(r"\hline")
        lines.extend([r"\end{tabular}", "}", ""])
        return "\n".join(lines)
    converted = [[convert_cell_content(cell.text) for cell in row] for row in rows]
    ncols = max(len(r) for r in converted)
    col_spec = "|" + "|".join(["p{0.14\\linewidth}"] * ncols) + "|"
    lines = [
        rf"\resizebox{{\linewidth}}{{!}}{{%",
        rf"\begin{{tabular}}{{{col_spec}}}",
        r"\hline",
    ]
    for row in converted:
        padded = row + [""] * (ncols - len(row))
        lines.append(" & ".join(padded) + r" \\")
        lines.append(r"\hline")
    lines.extend([r"\end{tabular}", "}", ""])
    return "\n".join(lines)


def paragraph_block(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    parts = []
    for para in re.split(r"\n\s*\n", text):
        p = para.strip()
        if not p:
            continue
        if IMAGE_RE.search(p):
            m = IMAGE_RE.search(p)
            if m:
                parts.append(
                    rf"\includegraphics[width=0.85\linewidth]{{{m.group(1).replace(chr(92), '/')}}}"
                )
            continue
        parts.append(rf"\setlength{{\parindent}}{{2em}}\indent {convert_inline(p)}")
    return r"\par ".join(parts)


def gbz_content_row(content: str) -> str:
    body = paragraph_block(content)
    if not body:
        return ""
    return rf"& \multicolumn{{8}}{{m{{13.5cm}}|}}{{{body}}} \\" + "\n"


def gbz_section_row(title: str, content: str = "") -> str:
    body = paragraph_block(content)
    return (
        rf"\multirow{{1}}{{1cm}}{{\centering {convert_inline(title)}}} "
        rf"& \multicolumn{{8}}{{m{{13.5cm}}|}}{{{body}}} \\" + "\n"
        r"\hline" + "\n"
    )


def gbz_table_block(title: str, table_html: str) -> str:
    tabular = html_table_to_inline_tabular(table_html)
    if not tabular:
        return ""
    caption = rf"\textbf{{{convert_inline(title)}}}\par\vspace{{0.5em}}" if title else ""
    inner = caption + tabular
    return (
        rf"& \multicolumn{{8}}{{m{{13.5cm}}|}}{{{inner}}} \\" + "\n"
        r"\hline" + "\n"
    )


def parse_markdown_blocks(text: str, *, skip_preamble: bool = True) -> list[dict]:
    lines = text.splitlines()
    blocks: list[dict] = []
    current_section: str | None = None
    last_subheading = ""
    paragraph_buf: list[str] = []
    started = not skip_preamble
    i = 0

    def flush_paragraph() -> None:
        nonlocal paragraph_buf, last_subheading
        if not paragraph_buf:
            return
        joined = "\n".join(paragraph_buf)
        sub = SUB_SECTION_RE.match(paragraph_buf[0].strip())
        if sub and len(paragraph_buf) == 1:
            last_subheading = f"{sub.group(1)} {sub.group(2).strip()}"
            blocks.append({"kind": "subheading", "title": last_subheading})
        else:
            blocks.append({"kind": "text", "section": current_section, "text": joined})
        paragraph_buf = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        table_match = HTML_TABLE_RE.search(stripped)
        if table_match and stripped.startswith("<table"):
            flush_paragraph()
            blocks.append(
                {
                    "kind": "table",
                    "section": current_section,
                    "html": table_match.group(0),
                    "caption": last_subheading or "",
                }
            )
            i += 1
            continue

        if not stripped:
            flush_paragraph()
            i += 1
            continue

        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            sec = CN_SECTION_RE.match(stripped)
            if sec:
                flush_paragraph()
                started = True
                current_section = f"{sec.group(1)}、{sec.group(2).strip()}"
                last_subheading = ""
                blocks.append({"kind": "heading", "title": current_section})
                i += 1
                continue
            if not started:
                i += 1
                continue
            if SKIP_HEADING_RE.search(title.replace(" ", "")):
                flush_paragraph()
                i += 1
                continue
            sub = SUB_SECTION_RE.match(title)
            if sub:
                flush_paragraph()
                last_subheading = f"{sub.group(1)} {sub.group(2).strip()}"
                blocks.append({"kind": "subheading", "title": last_subheading})
                i += 1
                continue

        if not started:
            i += 1
            continue

        paragraph_buf.append(stripped)
        i += 1

    flush_paragraph()
    return blocks


def infer_document_title(text: str, md_path: Path) -> str:
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("<"):
            continue
        if "报告编号" in s:
            continue
        if len(s) >= 6 and not SKIP_HEADING_RE.search(s.replace(" ", "")):
            return s
    return md_path.stem


def extract_metadata(text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for line in text.splitlines()[:40]:
        m = REPORT_NO_RE.search(line.strip())
        if m:
            meta["reportno"] = m.group(1).strip()
            break
    return meta


def blocks_to_longtable_rows(
    blocks: list[dict],
    *,
    document_title: str = "",
    section_header: str = "",
) -> str:
    out: list[str] = ["% Auto-generated from Markdown (GBZ/T 181 longtable rows)"]
    if document_title:
        header = section_header or "正文"
        out.append(
            rf"\multirow{{1}}{{1cm}}{{\centering {convert_inline(header)}}} "
            rf"& \multicolumn{{8}}{{m{{13.5cm}}|}}{{\centering\textbf{{{convert_inline(document_title)}}}}} \\"
        )
        out.append(r"\hline")
    for block in blocks:
        kind = block["kind"]
        if kind == "heading":
            out.append(gbz_section_row(block["title"]))
        elif kind == "subheading":
            out.append(gbz_content_row(rf"\textbf{{{convert_inline(block['title'])}}}"))
        elif kind == "text":
            out.append(gbz_content_row(block["text"]))
        elif kind == "table":
            out.append(gbz_table_block(block.get("caption") or "", block["html"]))
    return "\n".join(out)


def markdown_to_longtable_rows(text: str, md_path: Path, options: ConvertOptions) -> str:
    blocks = parse_markdown_blocks(text, skip_preamble=options.skip_preamble)
    title = options.document_title or infer_document_title(text, md_path)
    section_header = "附件" if options.layout == "skeleton" else "正文"
    return blocks_to_longtable_rows(blocks, document_title=title, section_header=section_header)


def copy_template_skeleton(template_dir: Path, output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    shutil.copytree(
        template_dir,
        output_dir,
        ignore=shutil.ignore_patterns(*BUILD_ARTIFACTS),
    )
    for name in BUILD_ARTIFACTS:
        artifact = output_dir / name
        if artifact.is_file():
            artifact.unlink()


def patch_main_tex_vars(main_path: Path, vars_map: dict[str, str]) -> None:
    if not main_path.is_file() or not vars_map:
        return
    text = main_path.read_text(encoding="utf-8")
    for key, value in vars_map.items():
        escaped = value.replace("\\", "\\\\")
        pattern = re.compile(rf"(\\newcommand{{\\{re.escape(key)}}}{{)([^}}]*)(}})")
        if pattern.search(text):
            text = pattern.sub(rf"\1{escaped}\3", text)
    main_path.write_text(text, encoding="utf-8")


def render_document_main_tex(
    template_main: Path,
    *,
    table_caption: str,
    body_input: str = "chapters/body.tex",
    include_chapters: bool = False,
) -> str:
    text = template_main.read_text(encoding="utf-8")
    if table_caption:
        text = re.sub(
            r"\\captionof\{table\}\{[^}]+\}",
            rf"\\captionof{{table}}{{{table_caption}}}",
            text,
            count=1,
        )
    chapter_inputs = "\n".join(
        rf"\input{{chapters/{i:02d}-{name}.tex}}"
        for i, name in enumerate(
            [
                "source",
                "intro",
                "class",
                "basis",
                "goal",
                "hazard",
                "layout",
                "protection",
                "health",
                "management",
                "conclusion",
            ]
        )
    )
    if include_chapters:
        body = chapter_inputs + f"\n\\input{{{body_input}}}\n"
    else:
        body = f"\\input{{{body_input}}}\n"
    pattern = re.compile(
        r"% 分章节导入.*?\\input\{chapters/11-appendix\}\s*\\\\",
        re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        raise ValueError("Could not locate chapter input block in template main.tex")
    text = text[: match.start()] + body + r"\\" + text[match.end() :]
    return text


def resolve_images_dir(md_path: Path) -> Path | None:
    for parent in [md_path.parent, md_path.parent.parent]:
        for name in ("images", "imgs"):
            candidate = parent / name
            if candidate.is_dir():
                return candidate
    return None


def copy_images(images_dir: Path | None, output_dir: Path) -> None:
    if not images_dir:
        return
    dst = output_dir / images_dir.name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(images_dir, dst)


def build_latex_project(
    md_path: Path,
    output_dir: Path,
    template_dir: Path,
    options: ConvertOptions | None = None,
) -> Path:
    opts = options or ConvertOptions()
    md_path = md_path.resolve()
    output_dir = output_dir.resolve()
    template_dir = template_dir.resolve()

    text = md_path.read_text(encoding="utf-8")
    meta = extract_metadata(text)
    vars_map = {**meta, **opts.vars}

    body_tex = markdown_to_longtable_rows(text, md_path, opts)

    if opts.layout == "body":
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / "body.tex"
        out_file.write_text(body_tex, encoding="utf-8")
        copy_images(resolve_images_dir(md_path), output_dir)
        return out_file

    copy_template_skeleton(template_dir, output_dir)

    chapters_dir = output_dir / "chapters"
    if opts.layout == "skeleton":
        chapter_file = chapters_dir / "11-appendix.tex"
    else:
        chapter_file = chapters_dir / "body.tex"
        main_tex = output_dir / "main.tex"
        caption = opts.table_caption or opts.document_title or infer_document_title(text, md_path)
        main_content = render_document_main_tex(
            template_dir / "main.tex",
            table_caption=caption,
            body_input="chapters/body.tex",
            include_chapters=False,
        )
        main_tex.write_text(main_content, encoding="utf-8")

    chapter_file.write_text(body_tex, encoding="utf-8")
    patch_main_tex_vars(output_dir / "main.tex", vars_map)
    copy_images(resolve_images_dir(md_path), output_dir)
    return output_dir


def discover_markdown_inputs(path: Path) -> list[Path]:
    path = path.resolve()
    if path.is_file() and path.suffix.lower() == ".md":
        return [path]
    if path.is_dir():
        for name in ("DSA.md", "content.md", "auto.md"):
            candidate = path / name
            if candidate.is_file():
                return [candidate]
        nested = path / "auto"
        if nested.is_dir():
            return discover_markdown_inputs(nested)
        return sorted(path.glob("*.md"))
    return []


def default_output_dir(md_path: Path, output_root: Path | None) -> Path:
    root = output_root or md_path.parent.parent / "latex"
    return root / md_path.stem


def convert_batch(
    inputs: list[Path],
    output_root: Path | None,
    template_dir: Path,
    options: ConvertOptions,
) -> list[Path]:
    results: list[Path] = []
    for item in inputs:
        for md_path in discover_markdown_inputs(item):
            out_dir = default_output_dir(md_path, output_root)
            results.append(build_latex_project(md_path, out_dir, template_dir, options))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert Markdown to a standalone GBZ/T 181 LaTeX project (batch-capable)."
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="Markdown file(s) or folder(s) containing .md (MinerU auto/ output)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        help="Output project directory (single input only; default: sibling latex/<stem>/)",
    )
    parser.add_argument(
        "--output-root",
        help="Root directory for batch output (one subfolder per markdown stem)",
    )
    parser.add_argument(
        "--template-dir",
        default=str(DEFAULT_TEMPLATE_DIR),
        help="Read-only GBZ/T 181 template skeleton (never modified)",
    )
    parser.add_argument(
        "--layout",
        choices=("document", "skeleton", "body"),
        default="document",
        help="document=standalone longtable; skeleton=copy full template+appendix; body=rows only",
    )
    parser.add_argument("--title", default="", help="Document title override")
    parser.add_argument("--table-caption", default="", help="Table caption override for main.tex")
    parser.add_argument(
        "--no-skip-preamble",
        action="store_true",
        help="Include cover/declaration lines before the first 一、 section",
    )
    parser.add_argument("--var", action="append", default=[], metavar="KEY=VALUE", help="Patch \\newcommand")
    args = parser.parse_args()

    vars_map: dict[str, str] = {}
    for item in args.var:
        if "=" in item:
            k, v = item.split("=", 1)
            vars_map[k.strip()] = v.strip()

    options = ConvertOptions(
        layout=args.layout,
        document_title=args.title,
        table_caption=args.table_caption,
        skip_preamble=not args.no_skip_preamble,
        vars=vars_map,
    )

    input_paths = [Path(p) for p in args.inputs]
    template_dir = Path(args.template_dir)

    if len(input_paths) == 1 and not args.output_root:
        md_files = discover_markdown_inputs(input_paths[0])
        if len(md_files) != 1:
            parser.error(f"Expected one markdown file under {input_paths[0]}")
        out_dir = Path(args.output_dir) if args.output_dir else default_output_dir(md_files[0], None)
        result = build_latex_project(md_files[0], out_dir, template_dir, options)
        print(f"Generated LaTeX project: {result}")
        print(f"  main.tex + chapters/{'11-appendix.tex' if options.layout == 'skeleton' else 'body.tex'}")
        return

    output_root = Path(args.output_root) if args.output_root else None
    if args.output_dir:
        parser.error("--output-dir cannot be used with multiple inputs; use --output-root")
    results = convert_batch(input_paths, output_root, template_dir, options)
    print(f"Converted {len(results)} markdown file(s):")
    for path in results:
        print(f"  - {path}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
