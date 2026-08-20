"""评价报告与文件库集成。

上传文件写入文件库时保留 original_name（用户原始文件名）；
同步至 LaTeX 工作区时由 appendix_service 复制并重命名为标准化路径。
"""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.urls import reverse

from apps.core.library_file_service import save_library_binary_uploads
from apps.core.models import LibraryFile

from .appendix_service import apply_attachments_to_latex, report_latex_dir
from .appendix_template import upload_slot_defs_for_report
from .conversion_preview import media_url_for_library_file, preview_kind_for_file
from .models import EvaluationReport, EvaluationReportUpload, EvaluationReportUploadFile
from .upload_staging import list_staging_files


@transaction.atomic
def ensure_upload_slots(report: EvaluationReport) -> list[EvaluationReportUpload]:
    """根据模板 13-appendix.tex 的 \\section 同步上传槽位。"""
    slot_defs = upload_slot_defs_for_report(report)
    valid_keys = {slot_def.key for slot_def in slot_defs}
    created: list[EvaluationReportUpload] = []

    for order, slot_def in enumerate(slot_defs):
        obj, is_new = EvaluationReportUpload.objects.get_or_create(
            report=report,
            slot_key=slot_def.key,
            defaults={
                "label": slot_def.label,
                "library_category": slot_def.library_category,
                "sort_order": order,
                "required": slot_def.required,
                "appendix_section": slot_def.appendix_section,
                "appendix_anchor": slot_def.appendix_anchor or "section",
            },
        )
        if not is_new:
            update_fields: list[str] = []
            if obj.label != slot_def.label:
                obj.label = slot_def.label
                update_fields.append("label")
            if obj.sort_order != order:
                obj.sort_order = order
                update_fields.append("sort_order")
            if obj.appendix_section != slot_def.appendix_section:
                obj.appendix_section = slot_def.appendix_section
                update_fields.append("appendix_section")
            anchor = slot_def.appendix_anchor or "section"
            if obj.appendix_anchor != anchor:
                obj.appendix_anchor = anchor
                update_fields.append("appendix_anchor")
            if update_fields:
                update_fields.append("updated_at")
                obj.save(update_fields=update_fields)
        created.append(obj)

    for orphan in report.uploads.filter(slot_key__startswith="attachment_").exclude(
        slot_key__in=valid_keys
    ):
        if orphan.files.exists():
            orphan.label = f"{orphan.label}（模板已移除）"
            orphan.save(update_fields=["label", "updated_at"])
        else:
            orphan.delete()

    return created


def _library_category_constant(name: str) -> str:
    mapping = {
        "evaluation_form": LibraryFile.CATEGORY_EVALUATION_FORM,
        "attachment": LibraryFile.CATEGORY_ATTACHMENT,
    }
    return mapping.get(name, LibraryFile.CATEGORY_ATTACHMENT)


def _sync_attachment_latex(report: EvaluationReport) -> None:
    if not report.uploads.filter(slot_key__startswith="attachment_").exists():
        return
    try:
        apply_attachments_to_latex(report, report_latex_dir(report))
    except FileNotFoundError:
        pass


@transaction.atomic
def add_slot_files(
    report: EvaluationReport,
    slot: EvaluationReportUpload,
    uploaded_files,
    user,
    *,
    replace: bool = False,
) -> list[EvaluationReportUploadFile]:
    files = [f for f in uploaded_files if f]
    if not files:
        raise ValueError("请选择文件")

    if slot.slot_key == "evaluation_form":
        replace = True
        if len(files) > 1:
            raise ValueError("评价信息表只能上传一个文件")

    if replace:
        slot.files.all().delete()
        slot.preview_md = ""
        slot.preview_tex = ""
        slot.save(update_fields=["preview_md", "preview_tex", "updated_at"])
        base_order = 0
    else:
        base_order = slot.files.count()

    category = _library_category_constant(slot.library_category)
    rows: list[EvaluationReportUploadFile] = []

    for offset, uploaded in enumerate(files):
        created, skipped = save_library_binary_uploads(
            user,
            [uploaded],
            category,
            link_entity=LibraryFile.LINK_ENTITY_EVALUATION_REPORT,
            link_object_id=report.pk,
        )
        if skipped:
            raise ValueError(skipped[0].get("reason") or "上传失败")
        if not created:
            raise ValueError("上传失败")
        lf = LibraryFile.objects.get(pk=created[0]["id"])
        row = EvaluationReportUploadFile.objects.create(
            upload=slot,
            library_file=lf,
            sort_order=base_order + offset,
        )
        rows.append(row)

    _normalize_sort_orders(slot)

    if report.status == EvaluationReport.Status.DRAFT:
        report.status = EvaluationReport.Status.UPLOADING
        report.save(update_fields=["status", "updated_at"])

    if slot.slot_key.startswith("attachment_"):
        _sync_attachment_latex(report)
    return rows


def save_slot_upload(
    report: EvaluationReport,
    slot: EvaluationReportUpload,
    uploaded_file,
    user,
) -> LibraryFile:
    replace = slot.slot_key == "evaluation_form"
    rows = add_slot_files(report, slot, [uploaded_file], user, replace=replace)
    return rows[0].library_file


@transaction.atomic
def remove_upload_file(
    report: EvaluationReport,
    slot: EvaluationReportUpload,
    file_id: int,
) -> None:
    row = EvaluationReportUploadFile.objects.select_related("upload").get(
        pk=file_id,
        upload=slot,
        upload__report=report,
    )
    row.delete()
    if slot.slot_key == "evaluation_form":
        slot.preview_md = ""
        slot.preview_tex = ""
        slot.save(update_fields=["preview_md", "preview_tex", "updated_at"])
    _normalize_sort_orders(slot)
    if slot.slot_key.startswith("attachment_"):
        _sync_attachment_latex(report)


@transaction.atomic
def reorder_upload_files(
    slot: EvaluationReportUpload,
    ordered_ids: list[int],
) -> None:
    files = {f.pk: f for f in slot.files.all()}
    valid_ids = [i for i in ordered_ids if i in files]
    if len(valid_ids) != len(files):
        raise ValueError("文件列表不完整")
    for order, pk in enumerate(valid_ids):
        if files[pk].sort_order != order:
            files[pk].sort_order = order
            files[pk].save(update_fields=["sort_order"])

    if slot.slot_key.startswith("attachment_"):
        _sync_attachment_latex(slot.report)


def _normalize_sort_orders(slot: EvaluationReportUpload) -> None:
    for order, row in enumerate(slot.files.order_by("sort_order", "created_at", "pk")):
        if row.sort_order != order:
            row.sort_order = order
            row.save(update_fields=["sort_order"])


def checklist_for_report(report: EvaluationReport) -> list[dict[str, Any]]:
    ensure_upload_slots(report)
    rows: list[dict[str, Any]] = []
    for slot in report.uploads.prefetch_related("files__library_file").all():
        file_rows = []
        for uf in slot.ordered_files():
            lf = uf.library_file
            file_rows.append(
                {
                    "id": uf.pk,
                    "library_file": lf,
                    "filename": lf.original_name,
                    "category_label": lf.get_category_display(),
                    "preview_kind": preview_kind_for_file(lf),
                    "media_url": media_url_for_library_file(lf),
                }
            )
        staging_file_rows: list[dict[str, Any]] = []
        if slot.slot_key.startswith("attachment_"):
            for srow in list_staging_files(report, slot.slot_key):
                staging_file_rows.append(
                    {
                        "id": srow["id"],
                        "filename": srow["original_name"],
                        "preview_kind": srow["preview_kind"],
                        "media_url": reverse(
                            "evaluation_report_staging_media",
                            kwargs={
                                "pk": report.pk,
                                "slot_key": slot.slot_key,
                                "file_id": srow["id"],
                            },
                        ),
                    }
                )
        rows.append(
            {
                "slot": slot,
                "uploaded": slot.is_uploaded,
                "files": file_rows,
                "is_attachment": slot.slot_key.startswith("attachment_"),
                "is_eval_form": slot.slot_key == "evaluation_form",
                "staging_count": len(staging_file_rows),
                "staging_files": staging_file_rows,
            }
        )
    return rows
