#!/usr/bin/env python3
"""Convert MinerU detection-report Markdown to LaTeX matching DSA.pdf layout."""

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
    convert_cell_content,
    convert_inline,
    html_table_has_merges,
    html_rows_to_anchors,
    parse_html_table,
    _format_merged_latex_cell,
    _row_needs_hline_after,
)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates" / "detection_report"

CN_SECTION_RE = re.compile(r"^#\s*([一二三四五六七八九十]+)[、．.]\s*(.+)$")
SUB_SECTION_RE = re.compile(r"^(\d+\.\d+)\s+(.+)$")
REPORT_NO_RE = re.compile(r"报告编号[：:]\s*(.+)")
ORDERED_ITEM_RE = re.compile(r"^(\d+)\.\s*(.+)$")
EMAIL_RE = re.compile(r"E-mail[：:]\s*(.+)", re.I)
ZIP_RE = re.compile(r"邮编[：:]\s*(\d+)")
ADDRESS_RE = re.compile(r"地址[：:]\s*(.+?)(?=E-mail|邮编|$)", re.I)

DEFAULT_COMPANY = "江西辐射剂量检测院有限公司"
DEFAULT_COMPANY_EN = "Radiation Dose Detection Research Institute of Jiangxi"


@dataclass
class DetectionMeta:
    company: str = DEFAULT_COMPANY
    company_en: str = DEFAULT_COMPANY_EN
    reportno: str = ""
    project_title: str = ""
    test_type: str = ""
    report_date: str = ""
    address: str = "南昌市顺外路 392 号君嘉广场 2 号楼 9 楼西"
    zipcode: str = "330029"
    email: str = "jxfs2018@163.com"
    contact_phone: str = "0791-88225536"
    contact_address: str = "南昌市顺外路392 号君嘉广场2号楼9楼西"


@dataclass
class BuildOptions:
    meta: DetectionMeta = field(default_factory=DetectionMeta)


def split_report_number(reportno: str) -> tuple[str, str]:
    reportno = normalize_spaces(reportno)
    m = re.search(r"\s+([A-Za-z]{2,}(?:[-/][A-Za-z0-9]+)*)\s*$", reportno)
    if m:
        return reportno[: m.start(1)].strip(), m.group(1).strip()
    return reportno, ""


def normalize_email(email: str) -> str:
    e = re.sub(r"\$\s*@\s*", "@", email.strip())
    e = e.replace("$", "")
    e = re.sub(r"\s*@\s*", "@", e)
    return e.strip()


def format_dr_cell(anchor, ncols: int) -> str:
    if not anchor.text.strip() and anchor.col == 0 and anchor.rowspan >= 5:
        if anchor.rowspan > 1:
            return rf"\multirow{{{anchor.rowspan}}}{{*}}{{\DRVertMainInstruments}}"
        return r"\DRVertMainInstruments"
    return _format_merged_latex_cell(anchor)


def table_col_spec(ncols: int, nrows: int) -> str:
    if ncols == 4 and nrows <= 12:
        return (
            r"|>{\raggedright\arraybackslash}p{0.22\textwidth}"
            r"|>{\raggedright\arraybackslash}p{0.26\textwidth}"
            r"|>{\centering\arraybackslash}p{0.22\textwidth}"
            r"|>{\centering\arraybackslash}p{0.22\textwidth}|"
        )
    w = max(0.9 / max(ncols, 1), 0.07)
    parts: list[str] = []
    for i in range(ncols):
        if i == 0 and ncols >= 6:
            align = r">{\raggedright\arraybackslash}"
        else:
            align = r">{\centering\arraybackslash}"
        parts.append(align + "p{" + f"{w:.3f}" + r"\textwidth}")
    return "|" + "|".join(parts) + "|"


def html_table_to_body_tabular(html: str, *, wrap_env: bool = True) -> str:
    rows = parse_html_table(html)
    if not rows:
        return ""

    nrows_hint = len(rows)
    ncols_hint = max(len(r) for r in rows)
    use_small = ncols_hint >= 7 or nrows_hint >= 10
    env_name = "DRTableSmall" if use_small else "DRTable"
    font_cmd = r"\DRFontTableSmall" if use_small else r"\DRFontTable"

    tab_lines: list[str] = []
    if html_table_has_merges(rows):
        nrows, ncols, anchors = html_rows_to_anchors(rows)
        col_spec_str = table_col_spec(ncols, nrows)
        tab_lines = [rf"\begin{{tabular}}{{{col_spec_str}}}", r"\hline"]
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
                parts.append(format_dr_cell(anchor, ncols))
                ci += anchor.colspan
            tab_lines.append(" & ".join(parts) + r" \\")
            if ri + 1 < nrows and _row_needs_hline_after(ri, anchors):
                tab_lines.append(r"\hline")
            elif ri == nrows - 1:
                tab_lines.append(r"\hline")
        tab_lines.append(r"\end{tabular}")
    else:
        converted = [[convert_cell_content(cell.text) for cell in row] for row in rows]
        ncols = max(len(r) for r in converted)
        tab_lines = [rf"\begin{{tabular}}{{{table_col_spec(ncols, len(converted))}}}", r"\hline"]
        for row in converted:
            padded = row + [""] * (ncols - len(row))
            tab_lines.append(" & ".join(padded) + r" \\")
            tab_lines.append(r"\hline")
        tab_lines.append(r"\end{tabular}")

    inner = "\n".join(tab_lines)
    if not wrap_env:
        stretch = "1.05" if use_small else "1.08"
        sep = "3pt" if use_small else "4pt"
        return (
            f"{font_cmd}\\renewcommand{{\\arraystretch}}{{{stretch}}}"
            f"\\setlength{{\\tabcolsep}}{{{sep}}}\n{inner}\n"
        )
    return rf"\begin{{{env_name}}}{inner}\end{{{env_name}}}" + "\n"


def format_body_paragraph(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if re.search(r"编\s*制", text) and re.search(r"审\s*核", text):
        return ["__SIGNATURE__"]
    if "结果表明" in text:
        parts = re.split(r"(结果表明：)", text, maxsplit=1)
        if len(parts) == 3:
            return [parts[0] + parts[1], parts[2].strip()]
    return [text]


def strip_hash(line: str) -> str:
    return line.lstrip("#").strip()


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_meta(lines: list[str]) -> DetectionMeta:
    meta = DetectionMeta()
    for line in lines[:45]:
        s = line.strip()
        if not s:
            continue
        m = REPORT_NO_RE.search(s)
        if m:
            meta.reportno = m.group(1).strip()
        m = ZIP_RE.search(s)
        if m:
            meta.zipcode = m.group(1).strip()
        m = EMAIL_RE.search(s)
        if m:
            meta.email = normalize_email(m.group(1))
        m = ADDRESS_RE.search(s)
        if m:
            meta.address = m.group(1).strip()
        if s.startswith("#") and "Radiation" in s:
            title = strip_hash(s)
            idx = title.find("Radiation")
            if idx > 0:
                meta.company = normalize_spaces(title[:idx])
            meta.company_en = title[idx:].strip()
        if "联系电话" in s:
            m = re.search(r"联系电话[：:]\s*(.+)", s)
            if m:
                meta.contact_phone = m.group(1).strip()
        if s.startswith("联系地址"):
            meta.contact_address = s.split("：", 1)[-1].strip()
        if s.startswith("检测单位"):
            meta.company = s.split("：", 1)[-1].strip() or meta.company

    for line in lines[:20]:
        s = line.strip()
        if not s.startswith("#") and "质量控制检测" in s:
            meta.project_title = normalize_spaces(s)
        if s in ("验收检测", "委托检测", "监督检测") or (
            not s.startswith("#") and len(s) < 12 and ("验收" in s or "委托" in s)
        ):
            meta.test_type = normalize_spaces(s)

    meta.email = normalize_email(meta.email)
    return meta


def parse_declaration(lines: list[str]) -> list[str]:
    items: list[str] = []
    in_decl = False
    for line in lines:
        s = line.strip()
        if s.startswith("#") and re.sub(r"\s+", "", strip_hash(s)) == "声明":
            in_decl = True
            continue
        if in_decl:
            if s.startswith("#") and CN_SECTION_RE.match(s):
                break
            m = ORDERED_ITEM_RE.match(s)
            if m:
                items.append(m.group(2).strip())
    return items


def parse_body_blocks(lines: list[str]) -> list[dict]:
    blocks: list[dict] = []
    paragraph_buf: list[str] = []
    started = False

    def flush_paragraph() -> None:
        nonlocal paragraph_buf
        if paragraph_buf:
            blocks.append({"kind": "text", "text": "\n".join(paragraph_buf)})
            paragraph_buf = []

    for line in lines:
        stripped = line.strip()
        if not started:
            if CN_SECTION_RE.match(stripped):
                started = True
            else:
                continue

        table_match = HTML_TABLE_RE.search(stripped)
        if table_match and stripped.startswith("<table"):
            flush_paragraph()
            blocks.append({"kind": "table", "html": table_match.group(0)})
            continue

        if not stripped:
            flush_paragraph()
            continue

        if stripped.startswith("#"):
            sec = CN_SECTION_RE.match(stripped)
            if sec:
                flush_paragraph()
                blocks.append({"kind": "section", "title": f"{sec.group(1)}、{sec.group(2).strip()}"})
                continue
            sub = SUB_SECTION_RE.match(strip_hash(stripped))
            if sub:
                flush_paragraph()
                blocks.append({"kind": "subsection", "title": f"{sub.group(1)} {sub.group(2).strip()}"})
                continue

        sub_inline = SUB_SECTION_RE.match(stripped)
        if sub_inline:
            flush_paragraph()
            blocks.append({"kind": "subsection", "title": f"{sub_inline.group(1)} {sub_inline.group(2).strip()}"})
            continue

        if IMAGE_RE.search(stripped):
            flush_paragraph()
            m = IMAGE_RE.search(stripped)
            if m:
                blocks.append({"kind": "image", "path": m.group(1).replace("\\", "/")})
            continue

        paragraph_buf.append(stripped)

    flush_paragraph()
    return blocks


def render_cover(meta: DetectionMeta) -> str:
    c = convert_inline
    cn, en = split_report_number(meta.reportno)
    email = normalize_email(meta.email)
    return rf"""
\thispagestyle{{coverfoot}}
\vspace*{{22mm}}
\begin{{center}}
{{\DRFontCoverCompany {c(meta.company)}}}\\[0.5em]
{{\DRFontCoverCompanyEn {c(meta.company_en)}}}
\end{{center}}

\vspace{{32mm}}
\begin{{center}}
{{\DRFontCoverMainTitle 检测报告}}\\[0.45em]
{{\DRFontCoverTestEn T\hspace{{0.35em}}E\hspace{{0.35em}}S\hspace{{0.35em}}T\hspace{{0.9em}}R\hspace{{0.35em}}E\hspace{{0.35em}}P\hspace{{0.35em}}O\hspace{{0.35em}}R\hspace{{0.35em}}T}}\\[1.6em]
{{\DRFontCoverReportNoCn 报告编号：{c(cn)}}}{{\DRFontCoverReportNoEn {c(en)}}}
\end{{center}}

\vspace{{14mm}}
\noindent\hspace{{8mm}}
\begin{{minipage}}[t]{{0.92\textwidth}}
\DRFontCoverLabel
\begin{{tabular}}[t]{{@{{}}p{{2.4cm}}@{{\hspace{{1.6cm}}}}p{{10.8cm}}@{{}}}}
项目名称 & {c(meta.project_title)} \\[1.8em]
受检单位 & \\[1.8em]
检测类型 & {c(meta.test_type)} \\[1.8em]
报告日期 & {c(meta.report_date)} \\
\end{{tabular}}
\end{{minipage}}

\vfill
\begin{{flushright}}
\DRFontCoverSeal 检测单位（检测专用印章）
\end{{flushright}}

\vspace{{8mm}}
\begin{{center}}
\DRFontFooter
{c(meta.company)} 地址：{c(meta.address)}\\
邮编：{c(meta.zipcode)}\quad E-mail：{c(email)}
\end{{center}}
\newpage
"""


def render_declaration(items: list[str], meta: DetectionMeta) -> str:
    c = convert_inline
    email = normalize_email(meta.email)
    lines = [
        r"\thispagestyle{declaration}",
        r"\vspace{6mm}",
        r"\begin{center}{\DRFontDeclTitle 声\hspace{1em}明}\end{center}",
        r"\vspace{5mm}",
        r"\DRFontDeclBody",
        r"\setlength{\itemsep}{0.55em}",
        r"\setlength{\parsep}{0pt}",
        r"\begin{enumerate}",
    ]
    for item in items:
        cleaned = re.sub(r"\s+o\s*$", "", item).strip()
        lines.append(rf"\item {c(cleaned)}")
    lines.extend(
        [
            r"\end{enumerate}",
            r"\vspace{8mm}",
            r"\DRFontDeclBody",
            rf"\noindent 检测单位：\ {c(meta.company)}\\[0.45em]",
            rf"\noindent 联系地址：{c(meta.contact_address)}\\[0.45em]",
            rf"\noindent 邮政编码：{c(meta.zipcode)}\\[0.45em]",
            rf"\noindent 联系电话：{c(meta.contact_phone)}\\[0.45em]",
            rf"\noindent E-mail：{c(email)}\\[0.45em]",
            r"\newpage",
        ]
    )
    return "\n".join(lines)


def render_body(blocks: list[dict], output_dir: Path | None = None) -> str:
    out: list[str] = [
        r"\setcounter{page}{1}",
        r"\pagestyle{detection}",
        r"\DRFontBodyText",
    ]
    for block in blocks:
        kind = block["kind"]
        if kind == "section":
            out.append(rf"\DRSection{{{convert_inline(block['title'])}}}")
        elif kind == "subsection":
            out.append(rf"\DRSubsection{{{convert_inline(block['title'])}}}")
        elif kind == "text":
            lines_in_block = [l.strip() for l in block["text"].splitlines() if l.strip()]
            processed: list[str] = []
            for line in lines_in_block:
                if re.search(r"编\s*制", line) and re.search(r"审\s*核", line):
                    processed.append("__SIGNATURE__")
                    continue
                processed.extend(format_body_paragraph(line))
            for para in processed:
                if para == "__SIGNATURE__":
                    out.append(r"\DRSignatureBlock")
                    continue
                out.append(rf"\noindent {convert_inline(para)}\\[0.35em]")
        elif kind == "table":
            out.append(html_table_to_body_tabular(block["html"]))
        elif kind == "image":
            path = block["path"]
            img_path = (output_dir / path) if output_dir else Path(path)
            if img_path.is_file():
                out.append(
                    rf"\begin{{center}}"
                    rf"\includegraphics[width=0.85\textwidth]{{{path}}}"
                    rf"\end{{center}}"
                )
            else:
                out.append(rf"% skipped missing image: {path}")

    out.append(r"\vspace{2em}")
    out.append(r"\noindent ————以下空白————")
    out.append(r"\label{LastBodyPage}")
    return "\n".join(out)


def render_main_tex(meta: DetectionMeta) -> str:
    c = convert_inline
    cn, en = split_report_number(meta.reportno)
    return (
        r"\documentclass[12pt,a4paper]{article}" + "\n"
        rf"\newcommand{{\company}}{{{c(meta.company)}}}" + "\n"
        rf"\newcommand{{\companyen}}{{{c(meta.company_en)}}}" + "\n"
        rf"\newcommand{{\reportno}}{{{c(meta.reportno)}}}" + "\n"
        rf"\newcommand{{\reportnocn}}{{{c(cn)}}}" + "\n"
        rf"\newcommand{{\reportnoen}}{{{c(en)}}}" + "\n"
        r"\input{detection_report_styles.tex}" + "\n"
        r"\begin{document}" + "\n"
        r"\input{sections/cover.tex}" + "\n"
        r"\input{sections/declaration.tex}" + "\n"
        r"\input{sections/body.tex}" + "\n"
        r"\end{document}" + "\n"
    )


def resolve_images_dir(md_path: Path) -> Path | None:
    for parent in [md_path.parent, md_path.parent.parent]:
        for name in ("images", "imgs"):
            candidate = parent / name
            if candidate.is_dir():
                return candidate
    return None


def resolve_auto_dir(md_path: Path) -> Path | None:
    parent = md_path.parent
    if any(parent.glob("*_content_list.json")):
        return parent
    nested = parent / "auto"
    if nested.is_dir() and any(nested.glob("*_content_list.json")):
        return nested
    return None


def build_detection_report(
    md_path: Path,
    output_dir: Path,
    options: BuildOptions | None = None,
    *,
    mode: str = "auto",
) -> Path:
    md_path = md_path.resolve()
    auto_dir = resolve_auto_dir(md_path)
    use_coords = mode == "coords" or (mode == "auto" and auto_dir is not None)
    if use_coords and auto_dir:
        from converter.auto_json_to_latex import build_coords_latex

        return build_coords_latex(auto_dir, output_dir)
    output_dir = output_dir.resolve()
    if output_dir.exists():
        try:
            shutil.rmtree(output_dir)
        except OSError:
            pass
    output_dir.mkdir(parents=True, exist_ok=True)

    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    meta = options.meta if options else DetectionMeta()
    extracted = extract_meta(lines)
    for field_name in (
        "company", "company_en", "reportno", "project_title", "test_type",
        "address", "zipcode", "email", "contact_phone", "contact_address",
    ):
        val = getattr(extracted, field_name)
        if val:
            setattr(meta, field_name, val)

    styles_src = TEMPLATE_DIR / "detection_report_styles.tex"
    shutil.copy2(styles_src, output_dir / "detection_report_styles.tex")

    sections_dir = output_dir / "sections"
    sections_dir.mkdir()
    (sections_dir / "cover.tex").write_text(render_cover(meta), encoding="utf-8")
    (sections_dir / "declaration.tex").write_text(render_declaration(parse_declaration(lines), meta), encoding="utf-8")
    (sections_dir / "body.tex").write_text(render_body(parse_body_blocks(lines), output_dir), encoding="utf-8")
    (output_dir / "main.tex").write_text(render_main_tex(meta), encoding="utf-8")

    images_dir = resolve_images_dir(md_path)
    if images_dir:
        dst = output_dir / images_dir.name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(images_dir, dst)

    return output_dir


def discover_markdown(path: Path) -> list[Path]:
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
            return discover_markdown(nested)
        return sorted(path.glob("*.md"))
    return []


def default_output_dir(md_path: Path, output_root: Path | None) -> Path:
    root = output_root or md_path.parent.parent / "latex"
    return root / md_path.stem


def convert_batch(inputs: list[Path], output_root: Path | None, mode: str = "auto") -> list[Path]:
    results: list[Path] = []
    for item in inputs:
        for md_path in discover_markdown(item):
            out = default_output_dir(md_path, output_root)
            results.append(build_detection_report(md_path, out, mode=mode))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert MinerU detection-report Markdown to LaTeX matching DSA.pdf."
    )
    parser.add_argument("inputs", nargs="+", help="Markdown file or folder")
    parser.add_argument("-o", "--output-dir", help="Output directory (single input)")
    parser.add_argument("--output-root", help="Batch output root")
    parser.add_argument(
        "--mode",
        choices=("auto", "coords", "semantic"),
        default="auto",
        help="auto: bbox layout when *_content_list.json exists; coords: force bbox layout",
    )
    args = parser.parse_args()

    input_paths = [Path(p) for p in args.inputs]
    if len(input_paths) == 1 and not args.output_root:
        md_files = discover_markdown(input_paths[0])
        if len(md_files) != 1:
            parser.error(f"Expected one markdown file under {input_paths[0]}")
        out = Path(args.output_dir) if args.output_dir else default_output_dir(md_files[0], None)
        result = build_detection_report(md_files[0], out, mode=args.mode)
        print(f"Generated detection report LaTeX ({args.mode}): {result}")
        return

    if args.output_dir:
        parser.error("--output-dir cannot be used with multiple inputs; use --output-root")
    results = convert_batch(input_paths, Path(args.output_root) if args.output_root else None, args.mode)
    print(f"Converted {len(results)} file(s):")
    for path in results:
        print(f"  - {path}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
