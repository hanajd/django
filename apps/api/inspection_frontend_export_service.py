"""与 ``export-frontend-json`` 完全一致的前端运行态 JSON 构建（任务 + 提交 + 坐标模板）。"""
from __future__ import annotations

import json as json_std
import logging
from typing import Any, Dict, Optional, Tuple

from django.utils import timezone

from apps.api.inspection_pdf_service import (
    _build_filled_template_fields_for_task,
    _resolve_library_task_for_task_no,
    display_inspected_no_for_fill,
)
from apps.api.inspection_report_make import merge_task_template_bound_instruments_into_payload
from apps.api.inspection_submit_payload_service import consolidate_submit_signatures, normalize_floor_plan_dynamic_data
DEFAULT_REPORT_TYPE = "xray_fluoroscopy"
from apps.core import pipeline_service
from apps.core.library_access import library_user_can_assign_tasks_to_participants
from apps.core.models import (
    InspectionCase,
    InspectionSubmission,
    LibraryFile,
    LibraryProject,
    LibraryTask,
    LibraryTaskAssignment,
)
from apps.core.project_numbering import project_task_no_for_library_task, project_tasks_ordered
from apps.core.site_record_hospital_prefill import build_site_hospital_info_prefill, merge_hospital_info_prefill
from utils.frontend_export_pipeline import build_runtime_frontend_schema

logger = logging.getLogger(__name__)


class InspectionFrontendExportError(Exception):
    """无法生成与现场 export-frontend-json 一致的 JSON。"""


def resolve_project_for_library_task_export(
    request,
    library_task: LibraryTask,
    data: Optional[dict] = None,
    meta: Optional[dict] = None,
) -> LibraryProject | None:
    data = data if isinstance(data, dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    pid = (
        data.get("projectId")
        or data.get("project_id")
        or meta.get("projectId")
        or meta.get("project_id")
    )
    if pid not in (None, ""):
        from apps.api.inspection_views import _InspectionTaskAccessMixin

        project = _InspectionTaskAccessMixin._resolve_project(str(pid).strip())
        if project is not None:
            return project
    qs = LibraryTaskAssignment.objects.filter(
        library_task=library_task, project_id__isnull=False
    ).select_related("project")
    if not library_user_can_assign_tasks_to_participants(request.user):
        qs = qs.filter(assignee=request.user)
    ass = qs.order_by("-id").first()
    return ass.project if ass else None


def resolve_case_for_project_library_task(
    project: LibraryProject,
    library_task: LibraryTask,
) -> Tuple[InspectionCase, LibraryTaskAssignment]:
    assignment = (
        LibraryTaskAssignment.objects.filter(project=project, library_task=library_task)
        .select_related("assignee", "assigned_by")
        .order_by("-id")
        .first()
    )
    if assignment is None:
        raise InspectionFrontendExportError("任务未分配到当前项目")
    from apps.api.inspection_views import _InspectionTaskAccessMixin

    mixin = _InspectionTaskAccessMixin()
    case = mixin._ensure_case_for_assignment_internal(assignment)
    if case.library_project_id != project.pk:
        case.library_project = project
        case.save(update_fields=["library_project", "updated_at"])
    return case, assignment


def build_runtime_frontend_for_inspection_export(
    request,
    *,
    project: LibraryProject,
    library_task: LibraryTask,
    display_task_no: str | None = None,
    ph_map_id: str | None = None,
) -> Dict[str, Any]:
    """
    与 ``GET …/export-frontend-json`` 相同产物（含提交 defaultValue、filled_fields、六大章节 runtime）。
    """
    case, _assignment = resolve_case_for_project_library_task(project, library_task)
    submission_task_no = case.case_no
    tasks = project_tasks_ordered(project)
    display_no = (str(display_task_no).strip() if display_task_no else "") or (
        project_task_no_for_library_task(library_task, project, ordered_tasks=tasks) or submission_task_no
    )

    task_obj = library_task
    if task_obj is None:
        task_obj = _resolve_library_task_for_task_no(submission_task_no, project)
    if task_obj is None:
        raise InspectionFrontendExportError("未找到任务关联模板")

    submission = (
        InspectionSubmission.objects.filter(
            task_no=submission_task_no, case=case, project=project
        )
        .order_by("-updated_at", "-id")
        .first()
    )
    now_iso = timezone.now().isoformat()
    from apps.api.inspection_views import _InspectionTaskAccessMixin, _resolve_task_template_json, _resolve_task_template_pdf

    project_public_id = _InspectionTaskAccessMixin._project_public_id(project)

    if submission is None:
        payload: Dict[str, Any] = {
            "taskNo": submission_task_no,
            "projectId": project_public_id,
            "reportType": DEFAULT_REPORT_TYPE,
            "createdAt": now_iso,
            "updatedAt": now_iso,
            "reportInfo": {},
            "hospitalInfo": {},
            "equipmentInfo": {},
            "testResult": {},
            "conclusion": {},
            "signatures": {},
            "instruments": [],
        }
    else:
        payload = submission.raw_payload if isinstance(submission.raw_payload, dict) else {}
        if not payload:
            payload = {
                "reportInfo": submission.report_info or {},
                "hospitalInfo": submission.hospital_info or {},
                "equipmentInfo": submission.equipment_info or {},
                "testResult": submission.test_result or {},
                "conclusion": submission.conclusion or {},
                "signatures": {},
                "instruments": [],
            }
        payload["taskNo"] = payload.get("taskNo") or submission_task_no
        payload["projectId"] = payload.get("projectId") or project_public_id
        payload["updatedAt"] = payload.get("updatedAt") or (
            submission.updated_at_remote.isoformat() if submission.updated_at_remote else now_iso
        )

    payload = merge_task_template_bound_instruments_into_payload(payload, task_obj, project_obj=project)
    prefill_hi = build_site_hospital_info_prefill(case)
    hi_block = payload.get("hospitalInfo") if isinstance(payload.get("hospitalInfo"), dict) else {}
    payload["hospitalInfo"] = merge_hospital_info_prefill(hi_block, prefill_hi)
    payload = normalize_floor_plan_dynamic_data(payload)
    payload = consolidate_submit_signatures(payload)

    if ph_map_id is None and request is not None:
        ph_map_id = (
            request.GET.get("placeholderMapId") or request.GET.get("placeholder_map_id") or ""
        ).strip() or None

    filled_fields, _template_pdf_id, fill_reason, _template_json_name = _build_filled_template_fields_for_task(
        task_obj,
        payload,
        map_id=ph_map_id,
        project=project,
        task_no=submission_task_no,
        inspection_case=case,
    )
    if not filled_fields:
        raise InspectionFrontendExportError(fill_reason or "模板字段填充失败")

    template_json_id, _template_json_name, template_err = _resolve_task_template_json(task_obj)
    if template_json_id is None:
        raise InspectionFrontendExportError(template_err or "任务下缺少 JSON 模板")
    template_json_lf = LibraryFile.objects.filter(pk=template_json_id).first()
    if template_json_lf is None:
        raise InspectionFrontendExportError("任务下缺少 JSON 模板")

    try:
        template_path = pipeline_service.library_absolute_path(template_json_lf.relative_path)
        template_raw = template_path.read_text(encoding="utf-8", errors="replace")
        template_obj = json_std.loads(template_raw)
    except Exception as exc:
        raise InspectionFrontendExportError(f"读取任务模板失败: {exc}") from exc

    pdf_block = template_obj.get("pdf") if isinstance(template_obj.get("pdf"), dict) else {}
    if not isinstance(pdf_block, dict):
        pdf_block = {}
    pdf_block["fields"] = filled_fields
    template_obj["pdf"] = pdf_block

    pdf_path = None
    template_pdf_id, _template_pdf_name, template_pdf_err = _resolve_task_template_pdf(task_obj)
    if not template_pdf_err and template_pdf_id:
        template_pdf_lf = LibraryFile.objects.filter(pk=template_pdf_id).first()
        if template_pdf_lf is not None:
            try:
                pdf_path = str(pipeline_service.library_absolute_path(template_pdf_lf.relative_path))
            except Exception:
                pdf_path = None

    return build_runtime_frontend_schema(
        template_obj,
        filled_fields,
        pdf_path=pdf_path,
        payload_for_defaults=payload,
        project_id=project_public_id,
        task_no=submission_task_no,
        inspected_display_no=display_inspected_no_for_fill(case, project, display_no),
        task_obj=task_obj,
        project_obj=project,
    )
