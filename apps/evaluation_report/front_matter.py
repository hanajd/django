"""Dedicated front-matter page models (cover / qualification / declaration)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from converter.project_vars import extract_newcommand_definitions

from .keywords_service import project_keyword_map
from .models import EvaluationReport
from .structured_blocks import (
    INCLUDEGRAPHICS_FULL_RE,
    INCLUDEGRAPHICS_RE,
    latex_to_editable_text,
    parse_chapter_blocks,
)
from .tabular_codec import latex_table_to_model, model_to_preview_html


def load_main_macros(main_tex: Path) -> dict[str, str]:
    if not main_tex.is_file():
        return {}
    text = main_tex.read_text(encoding="utf-8")
    return extract_newcommand_definitions(text)


def resolve_macros(report: EvaluationReport, latex_root: Path) -> dict[str, str]:
    """DB keywords override main.tex ``\\newcommand`` defaults."""
    macros = load_main_macros(latex_root / "main.tex")
    for cmd, val in project_keyword_map(report).items():
        if (val or "").strip():
            macros[cmd] = val.strip()
    return macros


def expand_macros(text: str, macros: dict[str, str]) -> str:
    if not text:
        return ""

    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        return macros.get(name, m.group(0))

    # See _expand_macros_local: avoid \\b so macros expand before CJK text.
    out = text
    for _ in range(6):
        nxt = re.sub(r"\\([a-zA-Z]+)(?![a-zA-Z])", repl, out)
        if nxt == out:
            break
        out = nxt
    return out


@dataclass
class PageChrome:
    """Header / footer strings for the editor chrome (body pages)."""

    lhead: str = ""
    rhead: str = ""
    lfoot: str = ""
    rfoot: str = ""
    show: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "lhead": self.lhead,
            "rhead": self.rhead,
            "lfoot": self.lfoot,
            "rfoot": self.rfoot,
            "show": self.show,
        }


def body_page_chrome(macros: dict[str, str]) -> PageChrome:
    buildunit = macros.get("buildunit", "")
    project = macros.get("project", "")
    reporttype = macros.get("reporttype", "")
    reportno = macros.get("reportno", "")
    company = macros.get("company", "")
    return PageChrome(
        lhead=f"{buildunit}{project}{reporttype}",
        rhead=reportno,
        lfoot=f"{company}  编制",
        rfoot="第 N 页  共 M 页",
        show=True,
    )


def cover_chrome() -> PageChrome:
    return PageChrome(show=False)


def qualification_chrome(macros: dict[str, str]) -> PageChrome:
    buildunit = macros.get("buildunit", "")
    project = macros.get("project", "")
    reporttype = macros.get("reporttype", "") or (
        (macros.get("radiationhazard", "") or "") + (macros.get("reportkind", "") or "")
    )
    reportno = macros.get("reportno", "")
    return PageChrome(
        lhead=f"{buildunit}{project}{reporttype}",
        rhead=reportno,
        lfoot="",
        rfoot="",
        show=True,
    )


def declaration_chrome(macros: dict[str, str]) -> PageChrome:
    company = macros.get("company", "")
    buildunit = macros.get("buildunit", "")
    project = macros.get("project", "")
    reporttype = macros.get("reporttype", "") or (
        (macros.get("radiationhazard", "") or "") + (macros.get("reportkind", "") or "")
    )
    reportno = macros.get("reportno", "")
    return PageChrome(
        lhead=f"{buildunit}{project}{reporttype}",
        rhead=reportno,
        lfoot=(
            f"评价单位：{company}\n"
            "联系地址：江西省南昌市青山湖区顺外路392号君嘉广场2号楼9楼西\n"
            "邮政编码：330029\n"
            "联系电话：0791-88225536\n"
            "E-mail：jxfs2018@163.com"
        ),
        rfoot="",
        show=True,
    )


@dataclass
class CoverPage:
    logo_path: str = ""
    logo_width: str = "4.6cm"
    reportno: str = ""
    reportver: str = ""
    buildunit: str = ""
    project: str = ""
    radiationhazard: str = ""
    reportkind: str = ""
    mitnote: str = ""
    company: str = ""
    printdate: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "logo_path": self.logo_path,
            "logo_width": self.logo_width,
            "reportno": self.reportno,
            "reportver": self.reportver,
            "buildunit": self.buildunit,
            "project": self.project,
            "radiationhazard": self.radiationhazard,
            "reportkind": self.reportkind,
            "mitnote": self.mitnote,
            "company": self.company,
            "printdate": self.printdate,
        }


# 资质证书默认框（旋转后最终宽×高），比 geometry 版心更大以便铺满
QUAL_FRAME_WIDTH_CM = 18.48
QUAL_FRAME_HEIGHT_CM = 25.09
QUAL_FRAME_WIDTH_MM = QUAL_FRAME_WIDTH_CM * 10.0
QUAL_FRAME_HEIGHT_MM = QUAL_FRAME_HEIGHT_CM * 10.0


@dataclass
class QualificationPage:
    title: str = ""
    image_path: str = ""
    image_width: str = f"{QUAL_FRAME_WIDTH_CM}cm"
    image_angle: int = 90
    image_scale: float = 1.0
    is_placeholder: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "image_path": self.image_path,
            "image_width": self.image_width,
            "image_angle": self.image_angle,
            "image_scale": self.image_scale,
            "is_placeholder": self.is_placeholder,
            "page_w_mm": 210.0,
            "page_h_mm": 297.0,
            "margin_left_mm": (210.0 - QUAL_FRAME_WIDTH_MM) / 2.0,
            "margin_right_mm": (210.0 - QUAL_FRAME_WIDTH_MM) / 2.0,
            "margin_top_mm": (297.0 - QUAL_FRAME_HEIGHT_MM) / 2.0,
            "margin_bottom_mm": (297.0 - QUAL_FRAME_HEIGHT_MM) / 2.0,
            # 预览虚线框 = 证书默认框
            "text_w_mm": QUAL_FRAME_WIDTH_MM,
            "text_h_mm": QUAL_FRAME_HEIGHT_MM,
            "frame_w_cm": QUAL_FRAME_WIDTH_CM,
            "frame_h_cm": QUAL_FRAME_HEIGHT_CM,
        }


@dataclass
class DeclarationPage:
    heading: str = ""
    reportno: str = ""
    statement: str = ""
    table_preview_html: str = ""
    table_block_id: str = ""
    table_caption: str = ""  # always empty for this page
    signyear: str = ""
    signmonth: str = ""
    signday: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "heading": self.heading,
            "reportno": self.reportno,
            "statement": self.statement,
            "table_preview_html": self.table_preview_html,
            "table_block_id": self.table_block_id,
            "table_caption": self.table_caption,
            "signyear": self.signyear,
            "signmonth": self.signmonth,
            "signday": self.signday,
        }


def parse_cover(tex: str, macros: dict[str, str]) -> CoverPage:
    logo_path = ""
    logo_width = "4.6cm"
    m = INCLUDEGRAPHICS_FULL_RE.search(tex)
    if m:
        opts, logo_path = m.group(1) or "", m.group(2)
        wm = re.search(r"width\s*=\s*([^,\]]+)", opts or "", re.I)
        if wm:
            logo_width = wm.group(1).strip()
    return CoverPage(
        logo_path=logo_path,
        logo_width=logo_width,
        reportno=macros.get("reportno", ""),
        reportver=macros.get("reportver", ""),
        buildunit=macros.get("buildunit", ""),
        project=macros.get("project", ""),
        radiationhazard=macros.get("radiationhazard", ""),
        reportkind=macros.get("reportkind", ""),
        mitnote=macros.get("mitnote", ""),
        company=macros.get("company", ""),
        printdate=macros.get("printdate", ""),
    )


def _match_is_in_line_comment(tex: str, start: int) -> bool:
    """True if ``start`` sits after an unescaped ``%`` on the same line."""
    line_start = tex.rfind("\n", 0, start) + 1
    prefix = tex[line_start:start]
    return bool(re.search(r"(?<!\\)%", prefix))


def _first_active_includegraphics(tex: str) -> re.Match[str] | None:
    for m in INCLUDEGRAPHICS_FULL_RE.finditer(tex):
        if not _match_is_in_line_comment(tex, m.start()):
            return m
    return None


def _parse_qualification_scale(width_opt: str) -> float:
    """Parse width opt relative to default certificate frame → scale in (0, 2]."""
    raw = (width_opt or "").strip().replace(" ", "")
    if not raw:
        return 1.0
    # 0.9\textwidth / \textwidth (legacy)
    m = re.match(
        r"^(?P<num>\d+(?:\.\d+)?)?\\(?:textwidth|textheight)$",
        raw,
        re.I,
    )
    if m:
        if m.group("num"):
            try:
                return max(0.1, min(2.0, float(m.group("num"))))
            except ValueError:
                return 1.0
        return 1.0
    # 18.48cm / 15.708cm → scale vs default frame width
    m2 = re.match(r"^(?P<num>\d+(?:\.\d+)?)cm$", raw, re.I)
    if m2:
        try:
            return max(0.1, min(2.0, float(m2.group("num")) / QUAL_FRAME_WIDTH_CM))
        except ValueError:
            return 1.0
    return 1.0


def _qualification_size_opts(angle: int, scale: float) -> str:
    sc = max(0.1, min(2.0, float(scale)))
    w = QUAL_FRAME_WIDTH_CM * sc
    h = QUAL_FRAME_HEIGHT_CM * sc
    w_s = f"{w:.4f}".rstrip("0").rstrip(".")
    h_s = f"{h:.4f}".rstrip("0").rstrip(".")
    return f"angle={angle},width={w_s}cm,height={h_s}cm,keepaspectratio"


def parse_qualification(tex: str) -> QualificationPage:
    image_path = ""
    image_width = f"{QUAL_FRAME_WIDTH_CM}cm"
    image_angle = 90
    image_scale = 1.0
    is_placeholder = True
    # Ignore \\includegraphics inside comments (placeholder docs used to fool the parser).
    m = _first_active_includegraphics(tex)
    if m:
        opts, image_path = m.group(1) or "", m.group(2)
        wm = re.search(r"width\s*=\s*([^,\]]+)", opts or "", re.I)
        if wm:
            image_width = wm.group(1).strip()
            image_scale = _parse_qualification_scale(image_width)
        am = re.search(r"angle\s*=\s*(-?\d+)", opts or "", re.I)
        if am:
            try:
                image_angle = int(am.group(1)) % 360
            except ValueError:
                image_angle = 90
        is_placeholder = False
    return QualificationPage(
        title="",
        image_path=image_path,
        image_width=image_width,
        image_angle=image_angle,
        image_scale=image_scale,
        is_placeholder=is_placeholder,
    )


def parse_declaration(tex: str, macros: dict[str, str]) -> DeclarationPage:
    # Big report title removed from declaration page; keep fields empty for UI.
    heading = ""
    reportno = ""

    # Statement paragraph: prose after 声明 title
    statement = ""
    sm = re.search(
        r"(?:声明\\par\}|声明\\par).*?\\justifying\s*(?:\\noindent\s*)?(.*?)(?:\\par\s*)?\\vspace",
        tex,
        re.S,
    )
    if not sm:
        sm = re.search(
            r"\\justifying\s*(?:\\noindent\s*)?(.*?)(?:\\par\s*)?\\vspace",
            tex,
            re.S,
        )
    if sm:
        statement = latex_to_editable_text(expand_macros(sm.group(1), macros))
    else:
        # fallback: longest CJK-heavy line that is not layout
        best = ""
        for line in tex.splitlines():
            s = line.strip()
            if s.startswith("%") or s.startswith("\\"):
                continue
            cjk = len(re.findall(r"[\u4e00-\u9fff]", s))
            if cjk > 40 and cjk > len(best):
                best = s
        statement = latex_to_editable_text(expand_macros(best, macros))

    table_preview = ""
    table_id = ""
    _, blocks = parse_chapter_blocks(tex)
    for b in blocks:
        if b.type == "table":
            table_id = b.id
            # force empty caption — declaration tabular has none
            model = latex_table_to_model(expand_macros(b.latex, macros), "")
            model.caption = ""
            table_preview = model_to_preview_html(model)
            break

    return DeclarationPage(
        heading=heading,
        reportno=reportno,
        statement=statement,
        table_preview_html=table_preview,
        table_block_id=table_id,
        table_caption="",
        signyear=(macros.get("signyear") or "").strip(),
        signmonth=(macros.get("signmonth") or "").strip(),
        signday=(macros.get("signday") or "").strip(),
    )


def ensure_declaration_sign_macros(tex: str) -> tuple[str, bool]:
    """声明页签发时间改为使用 \\signyear/\\signmonth/\\signday。"""
    if r"\signyear" in tex and r"\signday" in tex:
        return tex, False
    new, n = re.subn(
        r"签发时间：(?:\\hspace\{[^}]+\}|[^\n\\])*?年"
        r"(?:\\hspace\{[^}]+\}|[^\n\\])*?月"
        r"(?:\\hspace\{[^}]+\}|[^\n\\])*?日"
        r"(?:\s*\\hspace\{[^}]+\})?",
        r"签发时间：\\signyear\\hspace{0.5em}年\\hspace{1em}"
        r"\\signmonth\\hspace{0.5em}月\\hspace{1em}"
        r"\\signday\\hspace{0.5em}日",
        tex,
        count=1,
    )
    return new, n > 0


def patch_main_macros(main_tex: Path, updates: dict[str, str]) -> None:
    """Update ``\\newcommand{\\cmd}{...}`` values in main.tex."""
    if not main_tex.is_file():
        return
    text = main_tex.read_text(encoding="utf-8")
    out = text
    for cmd, val in updates.items():
        if val is None:
            continue
        val = str(val).strip()
        needle = "\\newcommand{\\" + cmd + "}"
        idx = out.find(needle)
        if idx < 0:
            continue
        brace = out.find("{", idx + len(needle))
        if brace < 0:
            continue
        depth = 0
        k = brace
        while k < len(out):
            ch = out[k]
            if ch == "\\" and k + 1 < len(out) and out[k + 1] in "{}":
                k += 2
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    out = out[: brace + 1] + val + out[k:]
                    break
            k += 1
    if out != text:
        main_tex.write_text(out, encoding="utf-8")


def patch_cover_logo(tex: str, path: str, width: str | None = None) -> str:
    path = path.replace("\\", "/").strip()
    if not path:
        return tex
    w = (width or "4.62cm").strip() or "4.62cm"
    inc = f"\\includegraphics[width={w}]{{{path}}}"
    # Prefer replacing an existing includegraphics.
    if INCLUDEGRAPHICS_RE.search(tex):

        def repl(m: re.Match[str]) -> str:
            opts = m.group(1) or ""
            if width:
                if re.search(r"width\s*=", opts, re.I):
                    opts = re.sub(
                        r"width\s*=\s*[^,\]]+",
                        f"width={width}",
                        opts,
                        count=1,
                        flags=re.I,
                    )
                else:
                    opts = f"width={width}" + ("," + opts if opts.strip() else "")
            return f"\\includegraphics[{opts}]{{{path}}}"

        return INCLUDEGRAPHICS_FULL_RE.sub(repl, tex, count=1)
    # Template placeholder uses \rule{width}{height} (same as qualification page).
    if re.search(r"\\rule\{[^}]+\}\{[^}]+\}", tex):
        return re.sub(r"\\rule\{[^}]+\}\{[^}]+\}", lambda _m: inc, tex, count=1)
    return tex


def patch_qualification_image(
    tex: str,
    path: str,
    width: str | None = None,
    *,
    angle: int | None = None,
    scale: float | None = None,
) -> str:
    """Insert/replace certificate image; page-centered, default 18.48cm × 25.09cm."""
    path = path.replace("\\", "/").strip()
    if not path:
        return tex
    ang = 90 if angle is None else int(angle) % 360
    sc = 1.0 if scale is None else max(0.1, min(2.0, float(scale)))
    opts = _qualification_size_opts(ang, sc)
    inc = f"\\includegraphics[{opts}]{{{path}}}"

    # Prefer replacing an existing includegraphics / rule (TikZ node content).
    if re.search(r"\\rule\{[^}]+\}\{[^}]+\}", tex):
        tex = re.sub(r"\\rule\{[^}]+\}\{[^}]+\}", lambda _m: inc, tex, count=1)
    else:
        m = _first_active_includegraphics(tex)
        if m:
            tex = tex[: m.start()] + inc + tex[m.end() :]
        else:
            node = (
                "\\begin{tikzpicture}[remember picture, overlay]\n"
                "  \\node[anchor=center, inner sep=0pt] at ([yshift=-12mm]current page.center) {%\n"
                f"    {inc}%\n"
                "  };\n"
                "\\end{tikzpicture}\n"
                "\\null\n"
            )
            m2 = re.search(r"(\\InsertBlankPage|\\clearpage)\s*$", tex, re.M)
            if m2:
                tex = tex[: m2.start()] + node + tex[m2.start() :]
            else:
                tex = tex + "\n" + node

    # 旧稿若仍锚定纸心，改为下移 12mm，避免遮挡页眉
    tex = re.sub(
        r"at\s*\(\s*current page\.center\s*\)",
        r"at ([yshift=-12mm]current page.center)",
        tex,
        count=1,
    )
    return tex


def patch_declaration_statement(tex: str, statement_plain: str) -> str:
    """Replace the declaration body paragraph; keep surrounding layout."""
    from .structured_blocks import plain_to_latex_paragraph

    body = plain_to_latex_paragraph(statement_plain).strip()
    if not body:
        return tex
    # Keep as a single justifying paragraph ending with \par
    if not body.endswith(r"\par"):
        body = body.rstrip() + r"\par"

    m = re.search(
        r"(\\justifying\s*(?:\\noindent\s*)?)(.*?)(\\par\s*)?(\\vspace)",
        tex,
        re.S,
    )
    if m:
        return tex[: m.start(2)] + "\n" + body + "\n" + tex[m.start(4) :]
    m2 = re.search(r"(\\justifying\s*)(.*?)(\\vspace)", tex, re.S)
    if m2:
        return tex[: m2.start(2)] + body + "\n\n" + tex[m2.start(3) :]
    return tex
