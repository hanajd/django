"""将报告工作区中的「模板不变结构」回写到 LaTeX 模板库。

分工原则（对照三份报批稿）：
- 关键词 / files/*.tex：项目个性化（单位、人员、源项、现场数据等）
- chapters/*.tex + main.tex 骨架：评价报告通用结构（章节、法规条文框架、表头骨架、
  \\input / \\newcommand 引用点），应由模板库持有

「保存到模板」只回写章节/骨架 .tex，不把当前项目的 files/*.tex 个性化正文写入模板。
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings

from .chapter_manifest import chapter_by_key, editable_chapters_for
from .keywords_service import normalize_tabular_blank_lines
from .latex_template_service import LatexTemplateError, get_template
from .models import EvaluationLatexTemplate, EvaluationReport
from .template_preview import TemplatePreviewError, ensure_editable_latex, template_root_for_library


# 报批稿 PDF → 模板键（评价报告类型）
REFERENCE_PDF_TO_TEMPLATE: dict[str, str] = {
    "250221-02 YP大余县人民医院（直线加速器、CT模拟定位机）C版 报批稿.pdf": "yp_linac250221",
    "250420 KP 萍乡市人民医院（后装治疗机）c版 报批稿.pdf": "kp250420",
    "250454 KP 上饶市立医院（加速器）C版 报批稿 .pdf": "kp_linac250454",
}


def library_write_roots(tpl: EvaluationLatexTemplate) -> list[Path]:
    """Bundled 模板可能同时存在 latex/ 与 media/.../latex_templates/，一并写入。"""
    roots: list[Path] = []
    primary = template_root_for_library(tpl)
    roots.append(primary)
    media_name = (tpl.bundled_dir or tpl.key).strip()
    if media_name:
        media = Path(settings.EVALUATION_LATEX_TEMPLATE_ROOT) / media_name
        try:
            if media.is_dir() and media.resolve() != primary.resolve():
                roots.append(media.resolve())
        except OSError:
            if media.is_dir():
                roots.append(media)
    # 去重
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        key = str(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def write_library_tex(
    tpl: EvaluationLatexTemplate,
    relpath: str,
    content: str,
    *,
    allow_system: bool = False,
) -> list[str]:
    """写入模板库 .tex；allow_system=True 时允许改内置模板。"""
    if not tpl.is_editable and not allow_system:
        raise TemplatePreviewError("该模板为只读，不可保存")
    rel = (relpath or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        raise TemplatePreviewError("非法路径")
    body = normalize_tabular_blank_lines(content)
    written: list[str] = []
    for root in library_write_roots(tpl):
        target = (root / rel).resolve()
        if not str(target).startswith(str(root.resolve())):
            raise TemplatePreviewError("非法路径")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        written.append(f"{root.name}/{rel}")
    return written


def push_chapter_to_library_template(
    report: EvaluationReport,
    chapter_key: str,
    *,
    content: str | None = None,
) -> dict:
    """把工作区某一章的结构回写到该报告绑定的模板库。"""
    spec = chapter_by_key(chapter_key, report.latex_template_key)
    if spec is None:
        raise LatexTemplateError(f"未知章节：{chapter_key}")
    tpl = get_template(report.latex_template_key)
    work = ensure_editable_latex(report)
    src = work / spec.relpath
    if content is None:
        if not src.is_file():
            raise LatexTemplateError(f"工作区缺少文件：{spec.relpath}")
        content = src.read_text(encoding="utf-8")
    paths = write_library_tex(tpl, spec.relpath, content, allow_system=True)
    return {
        "template_key": tpl.key,
        "template_name": tpl.name,
        "chapter": spec.key,
        "relpath": spec.relpath,
        "written": paths,
    }


def push_all_editable_chapters_to_library_template(report: EvaluationReport) -> dict:
    """回写当前报告全部可编辑章节到模板库（不含 files/*.tex 个性化）。"""
    tpl = get_template(report.latex_template_key)
    work = ensure_editable_latex(report)
    results = []
    for spec in editable_chapters_for(report.latex_template_key):
        src = work / spec.relpath
        if not src.is_file():
            continue
        paths = write_library_tex(
            tpl, spec.relpath, src.read_text(encoding="utf-8"), allow_system=True
        )
        results.append({"chapter": spec.key, "relpath": spec.relpath, "written": paths})
    return {
        "template_key": tpl.key,
        "template_name": tpl.name,
        "chapters": results,
        "count": len(results),
    }
