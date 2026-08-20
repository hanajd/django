"""评价报告：评价信息表转换 + yp250420 模板编译。"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from django.core.files.base import ContentFile
from django.db import close_old_connections
from django.db.models import Count

from converter.latex_compiler import LaTeXCompiler, LaTeXCompilerError
from .appendix_service import apply_attachments_to_latex
from .keywords_service import (
    apply_project_keywords_to_main_tex,
    sync_project_keywords_from_markdown,
)
from .library_integration import checklist_for_report
from .models import EvaluationReport, EvaluationReportUpload

logger = logging.getLogger(__name__)


def report_has_base_content(report: EvaluationReport) -> bool:
    work = report.work_dir
    return bool(
        work
        and (work / "latex" / "chapters" / "99-eval-info.tex").is_file()
    )


def _remove_legacy_eval_fragment_include(main_tex: Path) -> None:
    """Stop appending the whole source form after fields have been mapped."""
    if not main_tex.is_file():
        return
    content = main_tex.read_text(encoding="utf-8")
    updated = re.sub(
        r"^[ \t]*\\input\{chapters/99-eval-info\}[ \t]*\r?\n?",
        "",
        content,
        flags=re.MULTILINE,
    )
    if updated != content:
        main_tex.write_text(updated, encoding="utf-8")


def _sync_evaluation_form_fields(
    report: EvaluationReport,
    slot: EvaluationReportUpload,
    work: Path,
) -> tuple[Path, dict[str, str]]:
    """Directly extract PDF/DOCX and map values into report keywords."""
    from .conversion_preview import convert_library_file_to_markdown

    source = slot.primary_file
    if source is None:
        raise ValueError("评价信息表文件不存在")
    markdown = convert_library_file_to_markdown(source, work)
    slot.preview_md = markdown
    slot.preview_tex = ""
    slot.save(update_fields=["preview_md", "preview_tex", "updated_at"])
    extracted = sync_project_keywords_from_markdown(report, markdown)
    if not extracted:
        raise ValueError("未能从评价信息表中识别可自动填写的项目信息")

    latex_root = work / "latex"
    if latex_root.is_dir():
        apply_project_keywords_to_main_tex(report, latex_root / "main.tex")

    marker = work / "latex" / "chapters" / "99-eval-info.tex"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        "% 评价信息表已完成结构化提取；字段已写入模板变量和对应正文文件。\n",
        encoding="utf-8",
    )
    return marker, extracted


def generate_base_report(report: EvaluationReport) -> Path:
    """Extract the evaluation form and fill the editable template fields."""
    slot = report.uploads.filter(slot_key="evaluation_form").first()
    if not slot or not slot.is_uploaded:
        raise ValueError("请先上传评价信息表")

    work = report.ensure_work_dir()
    from .template_preview import ensure_editable_latex

    latex_root = ensure_editable_latex(report)
    fragment, extracted = _sync_evaluation_form_fields(report, slot, work)
    source_images = work / "images"
    target_images = latex_root / "images"
    target_images.mkdir(parents=True, exist_ok=True)
    if source_images.is_dir():
        for item in source_images.iterdir():
            if item.is_file():
                shutil.copy2(item, target_images / item.name)

    main_tex = latex_root / "main.tex"
    _remove_legacy_eval_fragment_include(main_tex)
    apply_project_keywords_to_main_tex(report, main_tex)
    report.status = EvaluationReport.Status.UPLOADING
    report.error_message = ""
    report.convert_log = (
        f"已直接提取评价信息表并自动填写 {len(extracted)} 个项目字段；"
        "未识别字段可在正文编辑或项目信息页补充。"
    )
    report.save(update_fields=["status", "error_message", "convert_log", "updated_at"])
    return fragment


def _prepare_latex_project(report: EvaluationReport, work: Path) -> tuple[Path, list[str]]:
    from .latex_template_service import template_storage_root
    from .template_preview import ensure_work_latex_fonts, ensure_work_latex_images

    src = template_storage_root(report.latex_template_key)
    dest = work / "latex"
    if dest.exists() and (dest / "main.tex").is_file():
        pass  # 保留工作区手工编辑
    else:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
    # 不再每次编译都从评价信息表重提取字段：那会覆盖封面/项目信息页已保存的修改。
    # 字段填充只应在「生成基础报告」时执行。
    ensure_work_latex_images(report, dest)
    ensure_work_latex_fonts(report, dest)
    images_src = work / "images"
    if images_src.is_dir() and any(images_src.iterdir()):
        dest_images = dest / "images"
        dest_images.mkdir(exist_ok=True)
        for item in images_src.iterdir():
            target = dest_images / item.name
            if item.is_file():
                shutil.copy2(item, target)
    _remove_legacy_eval_fragment_include(dest / "main.tex")
    apply_project_keywords_to_main_tex(report, dest / "main.tex")
    attachment_logs = apply_attachments_to_latex(report, dest)
    return dest, attachment_logs


def _missing_required_slots(report: EvaluationReport) -> list[str]:
    return [
        u.label
        for u in report.uploads.filter(required=True).annotate(
            file_count=Count("files")
        ).filter(file_count=0)
    ]


def build_report_pdf(report: EvaluationReport) -> EvaluationReport:
    checklist_for_report(report)
    missing = _missing_required_slots(report)
    if missing and not report.allow_incomplete_build:
        raise ValueError("以下必填材料尚未上传：" + "、".join(missing))

    logs: list[str] = []
    report.status = EvaluationReport.Status.CONVERTING
    report.error_message = ""
    report.save(update_fields=["status", "error_message", "updated_at"])

    try:
        work = report.ensure_work_dir()
        if missing and report.allow_incomplete_build:
            logs.append(f"测试模式：缺 {len(missing)} 项仍继续编译")

        eval_slot = report.uploads.filter(slot_key="evaluation_form").first()
        if eval_slot and eval_slot.is_uploaded:
            logs.append("使用已同步的项目字段编译（不再从评价信息表覆盖）")
        else:
            logs.append("未上传评价信息表，仅编译模板正文")

        latex_dir, attachment_logs = _prepare_latex_project(report, work)
        logs.extend(attachment_logs)
        logs.append(f"工作区模板 {report.latex_template_key}")

        report.status = EvaluationReport.Status.COMPILING
        report.convert_log = "\n".join(logs)
        report.save(update_fields=["status", "convert_log", "updated_at"])

        # 编译可能持续数分钟：释放 SQLite 连接，避免整段写锁挡住编辑页
        close_old_connections()

        compiler = LaTeXCompiler()
        result = compiler.compile_project(
            project_dir=latex_dir,
            main_file="main.tex",
            output_dir=work,
        )
        if not result.pdf_path:
            raise LaTeXCompilerError("编译未生成 PDF")

        close_old_connections()

        with result.pdf_path.open("rb") as handle:
            report.output_pdf.save(result.pdf_path.name, ContentFile(handle.read()), save=False)
        report.compile_log = result.log or ""
        report.status = EvaluationReport.Status.COMPILED
        report.error_message = ""
        report.save()
        return report

    except Exception as exc:
        logger.exception("build_report_pdf failed report=%s", report.pk)
        close_old_connections()
        report.status = EvaluationReport.Status.FAILED
        report.error_message = str(exc)
        report.convert_log = "\n".join(logs)
        report.save()
        raise


def compile_only(report: EvaluationReport) -> EvaluationReport:
    return build_report_pdf(report)
