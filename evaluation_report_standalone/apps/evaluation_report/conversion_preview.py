"""评价信息表 / 附件预览与 Markdown、LaTeX 转换。"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from django.conf import settings

from apps.core import pipeline_service
from apps.core.models import LibraryFile

from converter.md2latex import convert_markdown_to_latex
from converter.pdf2latex import STANDALONE_PREAMBLE, pdf_to_markdown
from converter.word2md import convert_docx_to_markdown

from .models import EvaluationReportUpload


def library_file_path(lf: LibraryFile) -> Path:
    return Path(pipeline_service.library_absolute_path(lf.relative_path))


def _find_companion_docx(pdf_path: Path) -> Path | None:
    for ext in (".docx", ".doc"):
        candidate = pdf_path.with_suffix(ext)
        if candidate.is_file():
            return candidate
    return None


def convert_library_file_to_markdown(lf: LibraryFile, work_dir: Path | None = None) -> str:
    path = library_file_path(lf)
    suffix = path.suffix.lower()
    work = work_dir or Path(tempfile.mkdtemp(prefix="eval_md_"))
    images_dir = work / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    if suffix == ".pdf":
        docx = _find_companion_docx(path)
        if docx:
            return convert_docx_to_markdown(docx, work / "out.md", images_dir)
        return pdf_to_markdown(path)
    if suffix in (".docx", ".doc"):
        return convert_docx_to_markdown(path, work / "out.md", images_dir)
    if suffix in (".md", ".markdown"):
        return path.read_text(encoding="utf-8")
    raise ValueError(f"不支持转为 Markdown 的格式：{suffix}")


def convert_markdown_to_tex_fragment(markdown: str, work_dir: Path) -> str:
    images_dir = work_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    md_path = work_dir / "preview.md"
    md_path.write_text(markdown, encoding="utf-8")
    body = convert_markdown_to_latex(
        markdown,
        md_path,
        images_dir,
        full_document=True,
    )
    preamble = STANDALONE_PREAMBLE.strip() if isinstance(STANDALONE_PREAMBLE, str) else ""
    if preamble and preamble in body:
        body = body.replace(preamble, "").strip()
    body = body.replace("\\begin{document}", "").replace("\\end{document}", "").strip()
    return body + "\n"


def preview_kind_for_file(lf: LibraryFile) -> str:
    name = (lf.original_name or lf.relative_path or "").lower()
    if name.endswith(".pdf"):
        return "pdf"
    if name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
        return "image"
    if name.endswith((".docx", ".doc")):
        return "docx"
    if name.endswith((".md", ".markdown")):
        return "markdown"
    return "download"


def media_url_for_library_file(lf: LibraryFile) -> str:
    rel = lf.relative_path.replace("\\", "/")
    return f"{settings.MEDIA_URL.rstrip('/')}/file_library/{rel}"


def work_media_url(report_id: int, relpath: str) -> str:
    from django.urls import reverse

    rel = relpath.replace("\\", "/").lstrip("/")
    return reverse("evaluation_report_work_media", kwargs={"pk": report_id, "relpath": rel})


def rewrite_markdown_image_urls(markdown: str, report_id: int) -> str:
    """将 Markdown 中的 images/... 相对路径改为可访问的工作目录 URL。"""

    def repl_md(match: re.Match) -> str:
        path = match.group(1).replace("\\", "/")
        return f"]({work_media_url(report_id, path)})"

    def repl_html(match: re.Match) -> str:
        path = match.group(1).replace("\\", "/")
        return f'src="{work_media_url(report_id, path)}"'

    text = re.sub(r"\]\((images/[^)]+)\)", repl_md, markdown)
    return re.sub(r'src="(images/[^"]+)"', repl_html, text)
