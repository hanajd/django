"""检测任务接口（pending/history/start/draft/submit + taskNo 文件约束）。"""
import io
import json as json_std
import logging
import threading
import re
from datetime import datetime
from urllib.parse import quote

from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.library_access import (
    library_file_access_allowed,
    library_inspection_act_on_all_projects,
    library_user_can_assign_tasks_to_participants,
    role_can_upload_library_category,
    role_has,
)
from apps.core import pipeline_service
from apps.core.library_file_service import (
    library_file_download_response,
    save_library_binary_uploads,
)
from apps.core.models import (
    BizDevice,
    InspectionCase,
    InspectionSubmission,
    InspectionSubmissionInstrument,
    InspectedOrganization,
    LibraryFile,
    LibraryOCRProcessTask,
    LibraryProject,
    LibraryProjectWorkflowMember,
    LibraryTask,
    LibraryTaskAssignment,
    Report,
    SiteRecord,
)
from apps.core.serializers_inspection import (
    InspectionDraftSerializer,
    InspectionSubmitSerializer,
    decode_png_data_url,
)
from apps.api.api_views import LibraryOCRUploadAPIView
from apps.api.inspection_pdf_service import display_inspected_no_for_fill
from apps.api.inspection_report_make import (
    build_instruments_root_for_frontend_export,
    merge_task_template_bound_instruments_into_payload,
)
from utils.extract_frontend_template import extract_frontend_template
from utils.frontend_schema_rule_engine import build_frontend_schema_by_rules

DEFAULT_REPORT_TYPE = "xray_fluoroscopy"
AUTO_TASK_PREFIX = "ASG-"
VALID_LIBRARY_FILE_CATEGORIES = {c for c, _ in LibraryFile.CATEGORY_CHOICES}
logger = logging.getLogger(__name__)


def _ok(message: str, data=None, http_status=status.HTTP_200_OK):
    return Response({"success": True, "message": message, "data": data}, status=http_status)


def _fail(message: str, http_status=status.HTTP_400_BAD_REQUEST, errors=None):
    payload = {"success": False, "message": message}
    if errors is not None:
        payload["errors"] = errors
    return Response(payload, status=http_status)


def _build_accessible_projects_payload(user, *, include_legacy_task_no: bool = False):
    if library_inspection_act_on_all_projects(user) and not library_user_can_assign_tasks_to_participants(user):
        project_ids = sorted(
            set(LibraryProject.objects.filter(is_active=True).values_list("id", flat=True))
        )
    else:
        assignment_qs = LibraryTaskAssignment.objects.select_related("project").filter(project_id__isnull=False)
        if not library_user_can_assign_tasks_to_participants(user):
            assignment_qs = assignment_qs.filter(assignee=user)
        project_ids = sorted(set(assignment_qs.values_list("project_id", flat=True)))
    if not project_ids:
        return {"count": 0, "list": []}
    projects = list(
        LibraryProject.objects.filter(pk__in=project_ids, is_active=True)
        .order_by("-updated_at", "-id")
        .values("id", "code", "name", "description", "updated_at")
    )
    data = []
    for row in projects:
        item = {
            "projectId": row["code"],
            "projectPk": row["id"],
            "projectName": row["name"],
            "description": row.get("description") or "",
            "updatedAt": row["updated_at"].isoformat() if row.get("updated_at") else "",
        }
        if include_legacy_task_no:
            # 兼容初版前端：taskNo == 新版 projectId（委托编号）。
            item["taskNo"] = row["code"]
        data.append(item)
    return {"count": len(data), "list": data}


def _inject_frontend_context_defaults(
    frontend_obj: dict, *, project_id: str, task_no: str, inspected_display_no: str | None = None
) -> dict:
    """
    在导出的前端模板中注入任务上下文默认值：
    - commissionNo / 委托编号 <- project_id
    - inspectionNo / testnumber / 受检编号 / inspectedNo <- 项目内报告(案件)序号（与 display_inspected_no_for_fill 一致），缺省回退 task_no
    - taskNo 仍用真实任务/案件号 task_no，便于提交与路由
    """
    if not isinstance(frontend_obj, dict):
        return frontend_obj
    steps = frontend_obj.get("steps")
    if not isinstance(steps, list):
        return frontend_obj

    inspected = (str(inspected_display_no).strip() if inspected_display_no is not None else "") or task_no
    value_map = {
        "commissionNo": project_id,
        "委托编号": project_id,
        "entrustNo": project_id,
        "projectId": project_id,
        "inspectionNo": inspected,
        "testnumber": inspected,
        "受检编号": inspected,
        "inspectedNo": inspected,
        "taskNo": task_no,
    }
    state_ns = f"p:{project_id}|t:{task_no}|"

    def _iter_all_fields(steps_obj):
        for step in steps_obj:
            if not isinstance(step, dict):
                continue
            sections = step.get("sections")
            if not isinstance(sections, list):
                continue
            for section in sections:
                if not isinstance(section, dict):
                    continue
                fields = section.get("fields")
                if isinstance(fields, list):
                    for field in fields:
                        if isinstance(field, dict):
                            yield field
                matrix = section.get("matrix") if isinstance(section.get("matrix"), dict) else {}
                header_fields = matrix.get("headerFields") if isinstance(matrix.get("headerFields"), list) else []
                for field in header_fields:
                    if isinstance(field, dict):
                        yield field
                rows = matrix.get("rows") if isinstance(matrix.get("rows"), list) else []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    cells = row.get("cells") if isinstance(row.get("cells"), dict) else {}
                    for cell in cells.values():
                        if isinstance(cell, dict):
                            yield cell

    def _pick_field_keys(field: dict):
        keys = set()
        for raw in (
            field.get("id"),
            field.get("label"),
            field.get("title"),
            field.get("placeholder"),
        ):
            s = str(raw or "").strip()
            if s:
                keys.add(s)
        source = field.get("source")
        if isinstance(source, dict):
            for raw in (
                source.get("key"),
                source.get("bindKey"),
                source.get("pdfFieldId"),
            ):
                s = str(raw or "").strip()
                if s:
                    keys.add(s)
        return keys

    for field in _iter_all_fields(steps):
        for key in _pick_field_keys(field):
            if key in value_map:
                field["defaultValue"] = value_map[key]
                break
        source = field.get("source")
        if isinstance(source, dict):
            for k in ("key", "bindKey"):
                raw = str(source.get(k) or "").strip()
                if not raw:
                    continue
                if not raw.startswith(state_ns):
                    source[k] = f"{state_ns}{raw}"
            field["source"] = source

    return frontend_obj


def _inject_frontend_payload_defaults(frontend_obj: dict, payload: dict) -> dict:
    """
    按字段 source.submitPath 从后端已有 payload 注入 defaultValue。
    支持后续任意预填字段，无需再逐个硬编码。
    """
    if not isinstance(frontend_obj, dict) or not isinstance(payload, dict):
        return frontend_obj
    steps = frontend_obj.get("steps")
    if not isinstance(steps, list):
        return frontend_obj

    def _get_by_path(data: dict, path: str):
        cur = data
        for seg in (path or "").split("."):
            key = str(seg or "").strip()
            if not key:
                continue
            if not isinstance(cur, dict):
                return None
            cur = cur.get(key)
            if cur is None:
                return None
        return cur

    def _coerce_field_default(field: dict, raw_value):
        field_type = str(field.get("type") or "").strip().lower()
        if field_type == "boolean":
            if isinstance(raw_value, bool):
                return raw_value
            if isinstance(raw_value, (int, float)):
                return bool(raw_value)
            if isinstance(raw_value, str):
                text = raw_value.strip().lower()
                if text in {"1", "true", "yes", "on", "y"}:
                    return True
                if text in {"0", "false", "no", "off", "n"}:
                    return False
            return None
        if field_type in {"number"}:
            if isinstance(raw_value, (int, float)):
                return raw_value
            if isinstance(raw_value, str):
                text = raw_value.strip()
                if not text:
                    return None
                try:
                    return float(text) if "." in text else int(text)
                except ValueError:
                    return None
            return None
        if field_type in {"text", "textarea", "date", "signature"}:
            if isinstance(raw_value, (dict, list)):
                return None
            return raw_value
        if field_type in {"radio", "select"}:
            # Flutter 侧枚举选项的 defaultValue 为 String；payload 中若为 bool（如历史勾选字段）
            # 会导致 type 'bool' is not a subtype of type 'String' in type cast。
            if isinstance(raw_value, bool):
                enum_ref = str(field.get("enumRef") or "")
                if enum_ref == "yesNo":
                    return "yes" if raw_value else "no"
                return None
            if raw_value is None:
                return None
            if isinstance(raw_value, (dict, list)):
                return None
            s = str(raw_value).strip()
            return s if s else None
        if field_type == "instrument_select":
            if isinstance(raw_value, str):
                s = raw_value.strip()
                return s if s else None
            if isinstance(raw_value, dict):
                sid = str(raw_value.get("id") or raw_value.get("instrumentId") or "").strip()
                return sid or None
            if raw_value is not None and not isinstance(raw_value, (dict, list)):
                s = str(raw_value).strip()
                return s if s else None
            return None
        # 其余类型保持原值（table/complex 等）
        return raw_value

    def _iter_all_fields(steps_obj):
        for step in steps_obj:
            if not isinstance(step, dict):
                continue
            sections = step.get("sections")
            if not isinstance(sections, list):
                continue
            for section in sections:
                if not isinstance(section, dict):
                    continue
                fields = section.get("fields")
                if isinstance(fields, list):
                    for field in fields:
                        if isinstance(field, dict):
                            yield field
                matrix = section.get("matrix") if isinstance(section.get("matrix"), dict) else {}
                header_fields = matrix.get("headerFields") if isinstance(matrix.get("headerFields"), list) else []
                for field in header_fields:
                    if isinstance(field, dict):
                        yield field
                rows = matrix.get("rows") if isinstance(matrix.get("rows"), list) else []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    cells = row.get("cells") if isinstance(row.get("cells"), dict) else {}
                    for cell in cells.values():
                        if isinstance(cell, dict):
                            yield cell

    for field in _iter_all_fields(steps):
        source = field.get("source") if isinstance(field.get("source"), dict) else {}
        submit_path = str(source.get("submitPath") or "").strip()
        if not submit_path:
            continue
        v = _get_by_path(payload, submit_path)
        if v in (None, "", []):
            continue
        coerced = _coerce_field_default(field, v)
        if coerced is None and str(field.get("type") or "").strip().lower() in {
            "boolean",
            "number",
            "text",
            "textarea",
            "date",
            "signature",
            "radio",
            "select",
        }:
            continue
        field["defaultValue"] = coerced
    return frontend_obj


def _inject_instruments_root_into_frontend_export(
    frontend_obj: dict, payload: dict, *, task_obj=None
) -> dict:
    """
    导出给 App 的前端 JSON 根级带上检测仪器初值：
    - instruments：已与任务模板绑定合并后的列表（与提交接口 submit 中 instruments 同形）；
    - 不再写入 instrumentCatalogOptions；下拉数据由前端走登记/仪器台账 API。
    """
    if not isinstance(frontend_obj, dict):
        return frontend_obj
    frontend_obj.pop("instrumentCatalogOptions", None)
    bundle = build_instruments_root_for_frontend_export(task_obj=task_obj, payload=payload)
    frontend_obj.update(bundle)
    return frontend_obj


def _resolve_task_template_json(task_obj):
    """
    查找任务关联的最新 JSON 模板文件。
    返回: (template_file_id, template_file_name, error_message)
    """
    template_qs = (
        LibraryFile.objects.filter(
            category=LibraryFile.CATEGORY_TEMPLATE,
            library_tasks=task_obj,
        )
        .order_by("-created_at", "-id")
        .distinct()
    )
    template_lf = next((lf for lf in template_qs if (lf.original_name or "").lower().endswith(".json")), None)
    if template_lf is None:
        return None, "", "任务下缺少 JSON 模板"
    return template_lf.pk, template_lf.original_name, ""


def _parse_client_datetime(value):
    """解析客户端时间并在时区启用时转换为 aware datetime。"""
    if not value:
        return None
    dt = parse_datetime(value)
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _persist_submit_payload_file(user, task_no: str, case: InspectionCase, project, payload: dict):
    """将最终提交内容写入文件库「检测提交」分类，并关联 taskNo 对应项目。"""
    ts = timezone.localtime().strftime("%Y%m%d%H%M%S")
    filename = f"{task_no}_submit_{ts}.json".replace("/", "_")
    raw = json_std.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    wrapped = type("UploadLike", (), {"read": lambda self: raw, "name": filename})()
    save_library_binary_uploads(
        user,
        [wrapped],
        LibraryFile.CATEGORY_INSPECTION_SUBMIT,
        link_entity=LibraryFile.LINK_ENTITY_INSPECTION_CASE,
        link_object_id=case.pk,
        project_ids=[project.pk],
    )
    # 检测提交 JSON 仅通过项目/案件关联；报告合并按 taskNo 文件名与项目内任务序号匹配（见 inspection_pdf_service）。


def _persist_submit_signature_files(user, task_no: str, case: InspectionCase, project, submission: InspectionSubmission):
    """将提交签名写入文件库「检测提交」分类，便于后台直接查看和下载。"""
    role_bytes_pairs = [
        ("inspector", submission.sign_author_png),
        ("checker", submission.sign_reviewer_png),
        ("accompanyingPerson", submission.sign_approver_png),
    ]
    wrapped_files = []
    ts = timezone.localtime().strftime("%Y%m%d%H%M%S")
    safe_task_no = (task_no or "").replace("/", "_")
    for role, raw in role_bytes_pairs:
        if not raw:
            continue
        content = bytes(raw)
        if not content:
            continue
        filename = f"{safe_task_no}_signature_{role}_{ts}.png"
        wrapped_files.append(type("UploadLike", (), {"read": lambda self, b=content: b, "name": filename})())
    if not wrapped_files:
        return
    save_library_binary_uploads(
        user,
        wrapped_files,
        LibraryFile.CATEGORY_INSPECTION_SUBMIT,
        link_entity=LibraryFile.LINK_ENTITY_INSPECTION_CASE,
        link_object_id=case.pk,
        project_ids=[project.pk],
    )


from apps.api.inspection_pdf_service import (
    _build_filled_template_fields_for_task,
    _pick_submit_generation_tasks,
    _persist_filled_pdf_from_submit,
    _resolve_library_task_for_task_no,
    _resolve_report_task_for_case,
    load_report_payload_for_manual_export,
    resolve_submit_payload_for_report,
)


class _InspectionTaskAccessMixin:
    """统一 taskNo -> 案件/项目 访问控制。"""

    @staticmethod
    def _project_public_id(project: LibraryProject) -> str:
        """
        对外项目编号（projectId）：
        - 若项目 code 已是 yyyymmNN，直接使用；
        - 否则按创建月份 + 当月序号动态生成（历史项目也能得到稳定编号）。
        """
        code = str(getattr(project, "code", "") or "").strip()
        if re.fullmatch(r"\d{8}", code):
            return code
        created_at = getattr(project, "created_at", None) or timezone.now()
        dt = timezone.localtime(created_at)
        month_prefix = dt.strftime("%Y%m")
        month_start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if dt.month == 12:
            next_month_start = dt.replace(year=dt.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            next_month_start = dt.replace(month=dt.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
        same_month_ids = list(
            LibraryProject.objects.filter(
                created_at__gte=month_start,
                created_at__lt=next_month_start,
            )
            .order_by("created_at", "id")
            .values_list("id", flat=True)
        )
        try:
            idx = same_month_ids.index(project.id) + 1
        except ValueError:
            idx = 1
        return f"{month_prefix}{idx:02d}"

    @staticmethod
    def _resolve_project(project_identifier):
        raw = str(project_identifier or "").strip()
        if not raw:
            return None
        project = LibraryProject.objects.filter(code=raw, is_active=True).first()
        if project is not None:
            return project
        if re.fullmatch(r"\d{8}", raw):
            month_prefix = raw[:6]
            try:
                seq = int(raw[6:])
            except ValueError:
                seq = 0
            if seq > 0:
                try:
                    year = int(month_prefix[:4])
                    month = int(month_prefix[4:6])
                    month_start = timezone.make_aware(datetime(year, month, 1), timezone.get_current_timezone())
                    if month == 12:
                        next_month_start = timezone.make_aware(datetime(year + 1, 1, 1), timezone.get_current_timezone())
                    else:
                        next_month_start = timezone.make_aware(datetime(year, month + 1, 1), timezone.get_current_timezone())
                    same_month_projects = list(
                        LibraryProject.objects.filter(
                            created_at__gte=month_start,
                            created_at__lt=next_month_start,
                            is_active=True,
                        ).order_by("created_at", "id")
                    )
                    if 1 <= seq <= len(same_month_projects):
                        return same_month_projects[seq - 1]
                except Exception:
                    pass
        try:
            pid = int(raw)
        except ValueError:
            return None
        return LibraryProject.objects.filter(pk=pid, is_active=True).first()

    def _build_file_download_url(self, request, *, task_no: str, pk: int, category: str, project: LibraryProject | None = None) -> str:
        """
        生成文件下载 URL，优先使用 project+task 的 v2 路由，兼容旧 task 路由。
        避免路由名变更导致的 NoReverseMatch -> HTML 500 页面。
        """
        project_id = ""
        if project is not None:
            try:
                project_id = self._project_public_id(project)
            except Exception:
                project_id = ""
        if project_id:
            try:
                return request.build_absolute_uri(
                    reverse(
                        "api_inspection_project_task_file_download",
                        kwargs={"project_id": project_id, "task_no": task_no, "pk": pk, "category": category},
                    )
                )
            except NoReverseMatch:
                pass
            try:
                return request.build_absolute_uri(
                    reverse(
                        "api_v2_inspection_project_task_file_download",
                        kwargs={"project_id": project_id, "task_no": task_no, "pk": pk, "category": category},
                    )
                )
            except NoReverseMatch:
                pass
        return request.build_absolute_uri(
            reverse(
                "api_inspection_task_file_download",
                kwargs={"task_no": task_no, "pk": pk, "category": category},
            )
        )

    @staticmethod
    def _build_project_task_no(project: LibraryProject, library_task: LibraryTask) -> str:
        if project is None or library_task is None:
            return ""
        task_ids = list(project.library_tasks.order_by("code", "id").values_list("id", flat=True))
        try:
            idx = task_ids.index(library_task.id) + 1
        except ValueError:
            return ""
        return f"{idx:02d}"

    @staticmethod
    def _resolve_project_task_by_no(project: LibraryProject, task_no: str):
        raw = str(task_no or "").strip()
        if not raw:
            return None
        try:
            idx = int(raw)
        except ValueError:
            return None
        if idx <= 0:
            return None
        tasks = list(project.library_tasks.order_by("code", "id"))
        if idx > len(tasks):
            return None
        return tasks[idx - 1]

    @staticmethod
    def _has_project_membership(user, project: LibraryProject) -> bool:
        if user is None or project is None:
            return False
        if library_user_can_assign_tasks_to_participants(user):
            return True
        if library_inspection_act_on_all_projects(user):
            return True
        return LibraryTaskAssignment.objects.filter(project=project, assignee=user).exists()

    @staticmethod
    def _get_or_create_project_task_assignment(*, project: LibraryProject, library_task: LibraryTask, user):
        """
        项目新增任务后，已拥有该项目访问权的用户可懒创建 assignment，避免列表/详情仍停留旧快照。
        """
        if project is None or library_task is None or user is None:
            return None
        if library_user_can_assign_tasks_to_participants(user):
            return (
                LibraryTaskAssignment.objects.filter(project=project, library_task=library_task)
                .order_by("id")
                .first()
            )
        if not _InspectionTaskAccessMixin._has_project_membership(user, project):
            return None
        assignment, _ = LibraryTaskAssignment.objects.get_or_create(
            project=project,
            library_task=library_task,
            assignee=user,
            defaults={"assigned_by": None},
        )
        LibraryProjectWorkflowMember.ensure_for_project_assignment(project, user)
        return assignment

    @staticmethod
    def _build_assignment_task_no(assignment: LibraryTaskAssignment) -> str:
        if assignment.project_id and assignment.library_task_id:
            project = getattr(assignment, "project", None)
            library_task = getattr(assignment, "library_task", None)
            if project is not None and library_task is not None:
                new_no = _InspectionTaskAccessMixin._build_project_task_no(project, library_task)
                if new_no:
                    return new_no
        return f"{AUTO_TASK_PREFIX}{assignment.pk}"

    @staticmethod
    def _parse_assignment_task_no(task_no: str):
        if not (task_no or "").startswith(AUTO_TASK_PREFIX):
            return None
        raw = (task_no or "")[len(AUTO_TASK_PREFIX) :].strip()
        try:
            aid = int(raw)
        except ValueError:
            return None
        return aid if aid > 0 else None

    @staticmethod
    def _default_org():
        org, _ = InspectedOrganization.objects.get_or_create(
            name="待补充受检单位",
            defaults={"address": "", "credit_code": ""},
        )
        return org

    def _ensure_case_for_assignment(self, assignment: LibraryTaskAssignment):
        task_no = self._build_assignment_task_no(assignment)
        case = InspectionCase.objects.filter(case_no=task_no).first()
        if case:
            return case
        return InspectionCase.objects.create(
            case_no=task_no,
            inspected_organization=self._default_org(),
            notes=f"自动生成任务：{assignment.library_task.name}",
            library_project=assignment.project,
            primary_contact=None,
            created_by=assignment.assigned_by or assignment.assignee,
        )

    def _ensure_case_for_assignment_internal(self, assignment: LibraryTaskAssignment):
        """
        使用全局唯一内部编号（ASG-<id>）确保 assignment 可稳定映射到唯一 case，
        避免不同项目共用展示 taskNo（如 01）时触发 case_no 冲突。
        """
        task_no = f"{AUTO_TASK_PREFIX}{assignment.pk}"
        case = InspectionCase.objects.filter(case_no=task_no).first()
        if case:
            return case
        return InspectionCase.objects.create(
            case_no=task_no,
            inspected_organization=self._default_org(),
            notes=f"自动生成任务：{assignment.library_task.name}",
            library_project=assignment.project,
            primary_contact=None,
            created_by=assignment.assigned_by or assignment.assignee,
        )

    @staticmethod
    def _resolve_case_project(request, task_no: str):
        case = (
            InspectionCase.objects.select_related("library_project", "inspected_organization", "primary_contact")
            .filter(case_no=task_no)
            .first()
        )
        if case is None:
            mixin = _InspectionTaskAccessMixin()
            aid = mixin._parse_assignment_task_no(task_no)
            if aid:
                aqs = LibraryTaskAssignment.objects.select_related("project", "assignee", "assigned_by", "library_task")
                if library_user_can_assign_tasks_to_participants(request.user):
                    assignment = aqs.filter(pk=aid).first()
                else:
                    assignment = aqs.filter(pk=aid, assignee=request.user).first()
                if assignment and assignment.project_id:
                    case = mixin._ensure_case_for_assignment(assignment)
        if case is None:
            return None, None, _fail("任务不存在", status.HTTP_404_NOT_FOUND)
        if not case.library_project_id:
            return None, None, _fail("该 taskNo 未绑定后台项目", status.HTTP_400_BAD_REQUEST)
        if library_user_can_assign_tasks_to_participants(request.user):
            return case, case.library_project, None
        assigned = LibraryTaskAssignment.objects.filter(
            assignee=request.user, project_id=case.library_project_id
        ).exists()
        if not assigned:
            return None, None, _fail("当前用户无权操作该任务", status.HTTP_403_FORBIDDEN)
        return case, case.library_project, None

    @staticmethod
    def _device_from_case(case: InspectionCase):
        return case.devices.order_by("id").first()

    def _submission_or_init(self, case, project):
        obj = (
            InspectionSubmission.objects.filter(task_no=case.case_no, case=case)
            .prefetch_related("instruments")
            .first()
        )
        if obj:
            return obj
        return InspectionSubmission(
            task_no=case.case_no,
            report_type=DEFAULT_REPORT_TYPE,
            status=InspectionSubmission.STATUS_PENDING,
            case=case,
            project=project,
            created_at_remote=case.created_at,
            updated_at_remote=case.updated_at,
            report_info={},
            hospital_info={},
            equipment_info={},
            test_result={},
            conclusion={},
        )

    def _prefill_dict(self, case: InspectionCase):
        org: InspectedOrganization = case.inspected_organization
        contact = case.primary_contact
        dev: BizDevice = self._device_from_case(case)
        report_info = {
            "reportNo": case.case_no,
            "projectName": case.notes or (case.library_project.name if case.library_project_id else ""),
            "inspectionType": "",
            "inspectionCategory": "",
            "inspectionMethod": "",
            "deviceCount": case.devices.count(),
            "inspectors": "",
        }
        hospital_info = {
            "name": org.name if org else "",
            "address": (org.address if org else "") or "",
            "contactPerson": (contact.name if contact else "") or "",
            "contactPhone": (contact.phone if contact else "") or "",
        }
        equipment_info = {
            "deviceName": (dev.name if dev else "") or "",
            "model": (dev.model if dev else "") or "",
            "manufacturer": (dev.manufacturer if dev else "") or "",
            "serialNo": (dev.serial_no if dev else "") or "",
            "location": "",
            "ratedParams": "",
            "testStandard": "",
            "evalStandard": "",
        }
        return report_info, hospital_info, equipment_info

    @staticmethod
    def _serialize_instruments(obj: InspectionSubmission):
        return [
            {
                "name": x.name,
                "identifier": x.identifier,
                "certificateNo": x.certificate_no,
                "validUntil": x.valid_until.isoformat() if x.valid_until else None,
                "enabled": x.enabled,
            }
            for x in obj.instruments.all().order_by("id")
        ]

    def _serialize_task_detail(self, case: InspectionCase, obj: InspectionSubmission):
        prefill_report, prefill_hospital, prefill_equipment = self._prefill_dict(case)
        has_payload = bool(obj.pk)
        return {
            "taskNo": case.case_no,
            "reportType": obj.report_type or DEFAULT_REPORT_TYPE,
            "status": obj.status or InspectionSubmission.STATUS_PENDING,
            "createdAt": (obj.created_at_remote or case.created_at).isoformat(),
            "updatedAt": (obj.updated_at_remote or case.updated_at).isoformat(),
            "reportInfo": (obj.report_info or prefill_report) if has_payload else prefill_report,
            "hospitalInfo": (obj.hospital_info or prefill_hospital) if has_payload else prefill_hospital,
            "equipmentInfo": (obj.equipment_info or prefill_equipment) if has_payload else prefill_equipment,
            "instruments": self._serialize_instruments(obj) if has_payload else [],
            "testResult": obj.test_result if (obj.test_result and has_payload) else None,
            "signatures": (
                {
                    "author": bool(obj.sign_author_png),
                    "reviewer": bool(obj.sign_reviewer_png),
                    "approver": bool(obj.sign_approver_png),
                    "signDate": obj.sign_date.isoformat() if obj.sign_date else None,
                }
                if has_payload and (obj.sign_author_png or obj.sign_reviewer_png or obj.sign_approver_png or obj.sign_date)
                else None
            ),
            "conclusion": obj.conclusion if (obj.conclusion and has_payload) else None,
        }

    def _ensure_task_under_project(self, request, project_id: str, task_no: str):
        project = self._resolve_project(project_id)
        if project is None:
            return None, None, _fail("projectId 无效", status.HTTP_400_BAD_REQUEST)
        library_task = self._resolve_project_task_by_no(project, task_no)
        if library_task is None:
            return None, None, _fail("taskNo 无效或不属于当前项目", status.HTTP_404_NOT_FOUND)
        assignment = self._get_or_create_project_task_assignment(
            project=project,
            library_task=library_task,
            user=request.user,
        )
        if assignment is None:
            return None, None, _fail("当前用户无权访问该项目任务", status.HTTP_403_FORBIDDEN)
        full_task_no = f"{AUTO_TASK_PREFIX}{assignment.pk}"
        case, resolved_project, err_resp = self._resolve_case_project(request, full_task_no)
        if err_resp is not None:
            # 兜底：直接按 assignment 内部唯一编号创建/解析 case，规避展示 taskNo 冲突。
            case = self._ensure_case_for_assignment_internal(assignment)
            if case.library_project_id != project.pk:
                case.library_project = project
                case.save(update_fields=["library_project", "updated_at"])
            return case, project, None
        if resolved_project.pk != project.pk:
            # 兼容历史：当展示 taskNo（如 01）跨项目冲突导致解析到其他项目 case 时，回退内部编号。
            case = self._ensure_case_for_assignment_internal(assignment)
            if case.library_project_id != project.pk:
                case.library_project = project
                case.save(update_fields=["library_project", "updated_at"])
            return case, project, None
        return case, resolved_project, None


class InspectionPendingAPIView(_InspectionTaskAccessMixin, APIView):
    """兼容接口：返回当前用户可访问项目列表（初版 taskNo 对应新版 projectId）。"""

    permission_classes = [IsAuthenticated]

    def _build_response(self, request, forced_status_filter: str = ""):
        page = max(1, int(request.GET.get("page", "1") or "1"))
        page_size = max(1, min(100, int(request.GET.get("pageSize", "20") or "20")))
        device_type = (request.GET.get("deviceType") or "").strip()
        status_filter = forced_status_filter or request.GET.get("status", "pending")
        if status_filter not in ("pending", "history"):
            status_filter = "pending"

        ass_qs = LibraryTaskAssignment.objects.filter(assignee=request.user, project_id__isnull=False).select_related(
            "project", "library_task", "assigned_by"
        )
        assignments = list(ass_qs.order_by("-created_at"))
        case_by_no = {}
        assigned_at_map = {}
        for a in assignments:
            task_no = self._build_assignment_task_no(a)
            if task_no in case_by_no:
                continue
            case = self._ensure_case_for_assignment(a)
            case_by_no[task_no] = case
            assigned_at_map[task_no] = a.created_at
        submission_map = {
            s.task_no: s
            for s in InspectionSubmission.objects.filter(task_no__in=case_by_no.keys()).select_related("case")
        }
        allowed_status_pending = {
            InspectionSubmission.STATUS_PENDING,
            InspectionSubmission.STATUS_IN_PROGRESS,
            InspectionSubmission.STATUS_REJECTED,
        }
        allowed_status_history = {
            InspectionSubmission.STATUS_SUBMITTED,
            InspectionSubmission.STATUS_APPROVED,
            InspectionSubmission.STATUS_REJECTED,
        }

        rows = []
        for task_no, case in case_by_no.items():
            sub = submission_map.get(task_no)
            status_val = sub.status if sub else InspectionSubmission.STATUS_PENDING
            if status_filter == "pending" and status_val not in allowed_status_pending:
                continue
            if status_filter == "history" and status_val not in allowed_status_history:
                continue
            if device_type and device_type != DEFAULT_REPORT_TYPE:
                continue
            report_info, hospital_info, equipment_info = self._prefill_dict(case)
            if sub and sub.report_info:
                report_info = sub.report_info
            if sub and sub.hospital_info:
                hospital_info = sub.hospital_info
            if sub and sub.equipment_info:
                equipment_info = sub.equipment_info
            rows.append(
                {
                    "taskNo": task_no,
                    "reportType": DEFAULT_REPORT_TYPE,
                    "status": status_val,
                    "createdAt": (sub.created_at_remote if sub else case.created_at).isoformat(),
                    "assignedAt": assigned_at_map.get(task_no, case.updated_at).isoformat(),
                    "reportInfo": report_info,
                    "hospitalInfo": hospital_info,
                    "equipmentInfo": equipment_info,
                }
            )
        pager = Paginator(rows, page_size)
        page_obj = pager.get_page(page)
        return _ok(
            "获取成功",
            {
                "total": pager.count,
                "page": page_obj.number,
                "pageSize": page_size,
                "list": list(page_obj.object_list),
            },
        )

    def get(self, request):
        # pending 接口改为项目列表入口：前端应先选项目，再查项目下任务。
        payload = _build_accessible_projects_payload(request.user, include_legacy_task_no=True)
        for item in payload.get("list", []):
            project = LibraryProject.objects.filter(pk=item.get("projectPk")).first()
            if project is None:
                continue
            pid = self._project_public_id(project)
            item["projectId"] = pid
            item["taskNo"] = pid
        return _ok("获取成功", payload)


class InspectionPendingLegacyAPIView(InspectionPendingAPIView):
    """v1 兼容：返回旧版待检测任务列表。"""

    def get(self, request):
        return self._build_response(request, forced_status_filter="pending")


class InspectionProjectListAPIView(APIView):
    """返回当前用户有权限访问的项目列表。"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        payload = _build_accessible_projects_payload(request.user, include_legacy_task_no=False)
        for item in payload.get("list", []):
            project = LibraryProject.objects.filter(pk=item.get("projectPk")).first()
            if project is None:
                continue
            item["projectId"] = _InspectionTaskAccessMixin._project_public_id(project)
        return _ok("获取成功", payload)


class InspectionDetailAPIView(_InspectionTaskAccessMixin, APIView):
    """按 taskNo 查询检测报告详情。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, task_no: str):
        case, _project, err_resp = self._resolve_case_project(request, task_no)
        if err_resp is not None:
            return err_resp
        obj = self._submission_or_init(case, case.library_project)
        return _ok("获取成功", self._serialize_task_detail(case, obj))


class InspectionStartAPIView(_InspectionTaskAccessMixin, APIView):
    """标记任务为检测中。"""

    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, task_no: str):
        case, project, err_resp = self._resolve_case_project(request, task_no)
        if err_resp is not None:
            return err_resp
        obj = InspectionSubmission.objects.filter(task_no=task_no, case=case).first()
        if obj is None:
            obj = InspectionSubmission.objects.create(
                task_no=task_no,
                report_type=DEFAULT_REPORT_TYPE,
                status=InspectionSubmission.STATUS_IN_PROGRESS,
                case=case,
                project=project,
                created_at_remote=case.created_at,
                updated_at_remote=timezone.now(),
                created_by=request.user,
            )
        else:
            if (
                obj.status == InspectionSubmission.STATUS_IN_PROGRESS
                and obj.created_by_id
                and obj.created_by_id != request.user.id
            ):
                return _fail("任务已被其他人开始检测", status.HTTP_409_CONFLICT)
            obj.status = InspectionSubmission.STATUS_IN_PROGRESS
            obj.started_at = obj.started_at or timezone.now()
            obj.updated_at_remote = timezone.now()
            obj.created_by = request.user
            obj.save(update_fields=["status", "started_at", "updated_at_remote", "created_by", "updated_at"])
        started_at = obj.started_at or timezone.now()
        return _ok(
            "任务已开始检测",
            {"taskNo": task_no, "status": obj.status, "startedAt": started_at.isoformat()},
        )


class InspectionDraftAPIView(_InspectionTaskAccessMixin, APIView):
    """保存草稿（允许部分字段）。"""

    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, task_no: str):
        case, project, err_resp = self._resolve_case_project(request, task_no)
        if err_resp is not None:
            return err_resp
        serializer = InspectionDraftSerializer(data=request.data or {})
        if not serializer.is_valid():
            return _fail("草稿保存失败：数据验证错误", status.HTTP_422_UNPROCESSABLE_ENTITY, serializer.errors)
        payload = serializer.validated_data
        obj = InspectionSubmission.objects.filter(task_no=task_no, case=case).first()
        if obj is None:
            obj = InspectionSubmission.objects.create(
                task_no=task_no,
                report_type=DEFAULT_REPORT_TYPE,
                status=InspectionSubmission.STATUS_IN_PROGRESS,
                case=case,
                project=project,
                created_at_remote=case.created_at,
                updated_at_remote=timezone.now(),
                created_by=request.user,
            )
        fields = ["status", "updated_at_remote", "draft_saved_at", "created_by", "updated_at"]
        obj.status = InspectionSubmission.STATUS_IN_PROGRESS
        obj.updated_at_remote = timezone.now()
        obj.draft_saved_at = timezone.now()
        obj.created_by = request.user
        if "reportInfo" in payload:
            obj.report_info = payload.get("reportInfo") or {}
            fields.append("report_info")
        if "hospitalInfo" in payload:
            obj.hospital_info = payload.get("hospitalInfo") or {}
            fields.append("hospital_info")
        if "equipmentInfo" in payload:
            obj.equipment_info = payload.get("equipmentInfo") or {}
            fields.append("equipment_info")
        if "testResult" in payload:
            obj.test_result = payload.get("testResult") or {}
            fields.append("test_result")
        if "conclusion" in payload:
            obj.conclusion = payload.get("conclusion") or {}
            fields.append("conclusion")
        if "signatures" in payload:
            signatures = payload.get("signatures") or {}
            sign_date = signatures.get("signDate")
            if sign_date:
                sign_date = _parse_client_datetime(sign_date)
                if sign_date is None:
                    return _fail("草稿保存失败：signatures.signDate 格式无效", status.HTTP_422_UNPROCESSABLE_ENTITY)
            obj.sign_author_png = payload.get("_author_png")
            obj.sign_reviewer_png = payload.get("_reviewer_png")
            obj.sign_approver_png = payload.get("_approver_png")
            obj.sign_date = sign_date
            fields.extend(["sign_author_png", "sign_reviewer_png", "sign_approver_png", "sign_date"])
        obj.raw_payload = request.data
        fields.append("raw_payload")
        obj.save(update_fields=list(dict.fromkeys(fields)))

        if "instruments" in payload:
            InspectionSubmissionInstrument.objects.filter(submission=obj).delete()
            ins_rows = []
            for item in (payload.get("instruments") or []):
                raw_valid_until = item.get("validUntil")
                valid_until = _parse_client_datetime(raw_valid_until) if raw_valid_until else None
                ins_rows.append(
                    InspectionSubmissionInstrument(
                        submission=obj,
                        name=(item.get("name") or "").strip(),
                        identifier=(item.get("identifier") or "").strip(),
                        certificate_no=(item.get("certificateNo") or "").strip(),
                        valid_until=valid_until,
                        enabled=bool(item.get("enabled", False)),
                    )
                )
            if ins_rows:
                InspectionSubmissionInstrument.objects.bulk_create(ins_rows)
        return _ok(
            "草稿已保存",
            {
                "taskNo": task_no,
                "status": obj.status,
                "draftSavedAt": obj.draft_saved_at.isoformat() if obj.draft_saved_at else None,
            },
        )


class InspectionSubmitByTaskAPIView(_InspectionTaskAccessMixin, APIView):
    """按 taskNo 提交完整检测结果。"""

    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, task_no: str):
        case, project, err_resp = self._resolve_case_project(request, task_no)
        if err_resp is not None:
            return err_resp
        payload = dict(request.data or {})
        payload["taskNo"] = task_no
        payload["reportType"] = payload.get("reportType") or DEFAULT_REPORT_TYPE
        now_iso = timezone.now().isoformat()
        payload["createdAt"] = payload.get("createdAt") or now_iso
        payload["updatedAt"] = payload.get("updatedAt") or now_iso

        serializer = InspectionSubmitSerializer(data=payload)
        if not serializer.is_valid():
            logger.warning(
                "inspection_submit validation failed task_no=%s user_id=%s errors=%s reportType=%r has_signatures=%s",
                task_no,
                getattr(request.user, "id", None),
                serializer.errors,
                payload.get("reportType"),
                bool(payload.get("signatures")),
            )
            return _fail("提交失败：数据验证错误", status.HTTP_422_UNPROCESSABLE_ENTITY, serializer.errors)
        data = serializer.validated_data
        signatures = data.get("signatures") or {}
        sign_date = signatures.get("signDate")
        if sign_date:
            sign_date = _parse_client_datetime(sign_date)
            if sign_date is None:
                logger.warning(
                    "inspection_submit invalid signDate task_no=%s user_id=%s raw_signDate=%r",
                    task_no,
                    getattr(request.user, "id", None),
                    signatures.get("signDate"),
                )
                return _fail("提交失败：signatures.signDate 不是合法 ISO8601 时间", status.HTTP_422_UNPROCESSABLE_ENTITY)

        obj, _ = InspectionSubmission.objects.update_or_create(
            task_no=task_no,
            case=case,
            defaults={
                "report_type": data["reportType"],
                "status": InspectionSubmission.STATUS_SUBMITTED,
                "case": case,
                "project": project,
                "created_at_remote": data["createdAt"],
                "updated_at_remote": data["updatedAt"],
                "report_info": data["reportInfo"],
                "hospital_info": data["hospitalInfo"],
                "equipment_info": data["equipmentInfo"],
                "test_result": data["testResult"],
                "conclusion": data["conclusion"],
                "raw_payload": request.data,
                "sign_author_png": data.get("_author_png"),
                "sign_reviewer_png": data.get("_reviewer_png"),
                "sign_approver_png": data.get("_approver_png"),
                "sign_date": sign_date,
                "submitted_at": timezone.now(),
                "created_by": request.user,
            },
        )

        InspectionSubmissionInstrument.objects.filter(submission=obj).delete()
        ins_rows = []
        for item in (data.get("instruments") or []):
            raw_valid_until = item.get("validUntil")
            valid_until = _parse_client_datetime(raw_valid_until) if raw_valid_until else None
            if raw_valid_until and valid_until is None:
                logger.warning(
                    "inspection_submit invalid instrument validUntil task_no=%s user_id=%s raw_validUntil=%r",
                    task_no,
                    getattr(request.user, "id", None),
                    raw_valid_until,
                )
                return _fail("提交失败：instruments.validUntil 不是合法 ISO8601 时间", status.HTTP_422_UNPROCESSABLE_ENTITY)
            ins_rows.append(
                InspectionSubmissionInstrument(
                    submission=obj,
                    name=(item.get("name") or "").strip(),
                    identifier=(item.get("identifier") or "").strip(),
                    certificate_no=(item.get("certificateNo") or "").strip(),
                    valid_until=valid_until,
                    enabled=bool(item.get("enabled", False)),
                )
            )
        if ins_rows:
            InspectionSubmissionInstrument.objects.bulk_create(ins_rows)
        _persist_submit_payload_file(request.user, task_no, case, project, request.data)
        _persist_submit_signature_files(request.user, task_no, case, project, obj)
        ph_map_id = (request.data.get("placeholderMapId") or request.data.get("placeholder_map_id") or "").strip() or None
        generation_results = []
        for task_obj in _pick_submit_generation_tasks(task_no, project):
            filled_fields, template_pdf_id, fill_reason, template_json_name = _build_filled_template_fields_for_task(
                task_obj,
                request.data,
                map_id=ph_map_id,
                project=project,
                task_no=task_no,
                inspection_case=case,
            )
            if not filled_fields:
                generation_results.append(
                    {
                        "taskCode": task_obj.code,
                        "outputTarget": task_obj.output_target,
                        "ok": False,
                        "reason": fill_reason or "模板填充失败",
                    }
                )
                continue
            ok, pdf_reason, _pdf_lf = _persist_filled_pdf_from_submit(
                request.user,
                task_no,
                case,
                project,
                filled_fields,
                template_pdf_id=template_pdf_id,
                template_json_name=template_json_name,
                task_obj=task_obj,
            )
            generation_results.append(
                {
                    "taskCode": task_obj.code,
                    "outputTarget": task_obj.output_target,
                    "ok": bool(ok),
                    "reason": pdf_reason or "",
                }
            )
        return _ok(
            "提交成功",
            {
                "taskNo": task_no,
                "status": obj.status,
                "submittedAt": obj.submitted_at.isoformat() if obj.submitted_at else None,
                "pdfGeneration": generation_results,
            },
        )


class InspectionSubmitAPIView(InspectionSubmitByTaskAPIView):
    """兼容旧路径：POST /inspections/submit（body 内 taskNo）。"""

    def post(self, request):
        task_no = (request.data.get("taskNo") or "").strip()
        if not task_no:
            return _fail("缺少 taskNo", status.HTTP_400_BAD_REQUEST)
        return super().post(request, task_no=task_no)


class InspectionHistoryAPIView(InspectionPendingAPIView):
    """历史任务列表。"""

    def get(self, request):
        return self._build_response(request, forced_status_filter="history")


class InspectionProjectTaskListAPIView(APIView):
    """按项目返回当前用户可访问的任务列表。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, project_id: str):
        project = _InspectionTaskAccessMixin._resolve_project(project_id)
        if project is None:
            return _fail("projectId 无效", status.HTTP_400_BAD_REQUEST)
        if not _InspectionTaskAccessMixin._has_project_membership(request.user, project):
            return _fail("当前用户无权访问该项目任务", status.HTTP_403_FORBIDDEN)
        tasks = list(project.library_tasks.order_by("code", "id"))
        if not tasks:
            return _ok("获取成功", {"projectId": project.code, "count": 0, "list": []})
        rows = []
        template_cache = {}
        for idx, library_task in enumerate(tasks, start=1):
            assignment = _InspectionTaskAccessMixin._get_or_create_project_task_assignment(
                project=project,
                library_task=library_task,
                user=request.user,
            )
            if assignment is None:
                continue
            task_no = f"{idx:02d}"
            if library_task.id not in template_cache:
                template_cache[library_task.id] = _resolve_task_template_json(library_task)
            tpl_id, tpl_name, tpl_error = template_cache[library_task.id]
            rows.append(
                {
                    "assignmentId": assignment.pk,
                    "taskNo": task_no,
                    "projectId": _InspectionTaskAccessMixin._project_public_id(project),
                    "taskId": library_task.id,
                    "taskCode": library_task.code,
                    "taskName": library_task.name,
                    "outputTarget": library_task.output_target,
                    "assignedAt": assignment.created_at.isoformat() if assignment.created_at else "",
                    "frontendTemplateDownloadUrl": (
                        request.build_absolute_uri(
                            reverse(
                                "api_v2_inspection_project_task_export_frontend_json",
                                kwargs={"project_id": _InspectionTaskAccessMixin._project_public_id(project), "task_no": task_no},
                            )
                        )
                        if not tpl_error
                        else ""
                    ),
                    "frontendTemplateMeta": {
                        "templateFileId": tpl_id,
                        "templateFileName": tpl_name,
                        "exported": not bool(tpl_error),
                        "error": tpl_error or "",
                    },
                }
            )
        return _ok("获取成功", {"projectId": project.code, "count": len(rows), "list": rows})


class InspectionTaskFrontendJsonExportAPIView(_InspectionTaskAccessMixin, APIView):
    """按任务导出回填后的前端 JSON，并直接下载。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, task_no: str):
        case, project, err_resp = self._resolve_case_project(request, task_no)
        if err_resp is not None:
            return err_resp

        task_obj = _resolve_library_task_for_task_no(task_no, project)
        if task_obj is None:
            return _fail("未找到任务关联模板", status.HTTP_404_NOT_FOUND)

        submission = (
            InspectionSubmission.objects.filter(task_no=task_no, case=case, project=project)
            .order_by("-updated_at", "-id")
            .first()
        )
        now_iso = timezone.now().isoformat()
        if submission is None:
            # 导出前端空模板：无提交时也允许导出，只提供结构化空 payload + 任务上下文。
            payload = {
                "taskNo": task_no,
                "projectId": self._project_public_id(project),
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
            # 兼容旧提交：补齐填充映射依赖的任务/项目/更新时间根字段。
            payload["taskNo"] = payload.get("taskNo") or task_no
            payload["projectId"] = payload.get("projectId") or self._project_public_id(project)
            payload["updatedAt"] = payload.get("updatedAt") or (
                submission.updated_at_remote.isoformat() if submission.updated_at_remote else now_iso
            )

        payload = merge_task_template_bound_instruments_into_payload(payload, task_obj)

        ph_map_id = (
            (request.GET.get("placeholderMapId") or request.GET.get("placeholder_map_id") or "").strip() or None
        )
        filled_fields, _template_pdf_id, fill_reason, _template_json_name = _build_filled_template_fields_for_task(
            task_obj,
            payload,
            map_id=ph_map_id,
            project=project,
            task_no=task_no,
            inspection_case=case,
        )
        if not filled_fields:
            return _fail(fill_reason or "模板字段填充失败", status.HTTP_409_CONFLICT)

        template_json_id, _template_json_name, template_err = _resolve_task_template_json(task_obj)
        if template_json_id is None:
            return _fail(template_err or "任务下缺少 JSON 模板", status.HTTP_404_NOT_FOUND)
        template_json_lf = LibraryFile.objects.filter(pk=template_json_id).first()
        if template_json_lf is None:
            return _fail("任务下缺少 JSON 模板", status.HTTP_404_NOT_FOUND)

        try:
            template_path = pipeline_service.library_absolute_path(template_json_lf.relative_path)
            template_raw = template_path.read_text(encoding="utf-8", errors="replace")
            template_obj = json_std.loads(template_raw)
        except Exception as exc:
            return _fail(f"读取任务模板失败: {exc}", status.HTTP_500_INTERNAL_SERVER_ERROR)

        pdf_block = template_obj.get("pdf") if isinstance(template_obj.get("pdf"), dict) else {}
        if not isinstance(pdf_block, dict):
            pdf_block = {}
        pdf_block["fields"] = filled_fields
        template_obj["pdf"] = pdf_block

        # 任务接口默认使用规则引擎导出前端模板；失败时回退旧提取逻辑保证可用性。
        try:
            frontend_obj = build_frontend_schema_by_rules(template_obj)
        except Exception as exc:
            logger.warning("rule frontend export failed, fallback to extract_frontend_template: %s", exc)
            frontend_obj = extract_frontend_template(template_obj)
        frontend_obj = _inject_frontend_context_defaults(
            frontend_obj,
            project_id=self._project_public_id(project),
            task_no=task_no,
            inspected_display_no=display_inspected_no_for_fill(case, project, task_no),
        )
        frontend_obj = _inject_frontend_payload_defaults(frontend_obj, payload)
        frontend_obj = _inject_instruments_root_into_frontend_export(frontend_obj, payload, task_obj=task_obj)
        ts = timezone.localtime().strftime("%Y%m%d%H%M%S")
        filename = f"{task_no}_frontend_{ts}.json".replace("/", "_")
        raw = json_std.dumps(frontend_obj, ensure_ascii=False, indent=2).encode("utf-8")
        resp = FileResponse(
            io.BytesIO(raw),
            as_attachment=True,
            filename=filename,
            content_type="application/json",
        )
        resp["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
        return resp


class InspectionTaskManualExportReportAPIView(_InspectionTaskAccessMixin, APIView):
    """按任务手动导出报告：以检测提交数据为主，并与同案件现场记录 JSON（若有）合并后回填报告模板。"""

    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, task_no: str):
        case, project, err_resp = self._resolve_case_project(request, task_no)
        if err_resp is not None:
            return err_resp

        report_task = _resolve_report_task_for_case(task_no, project)
        if report_task is None:
            return _fail("项目下未找到报告任务", status.HTTP_409_CONFLICT)

        submit_payload = resolve_submit_payload_for_report(task_no, case, project)
        if not submit_payload:
            return _fail("未找到该任务的检测提交数据，无法回填报告", status.HTTP_409_CONFLICT)

        source_payload, source_reason = load_report_payload_for_manual_export(
            (case,), project, report_task, submit_payload
        )
        if not isinstance(source_payload, dict) or not source_payload:
            return _fail(source_reason or "报告数据源合并失败", status.HTTP_409_CONFLICT)

        ph_map_id = (
            (
                request.data.get("placeholderMapId")
                or request.data.get("placeholder_map_id")
                or request.query_params.get("placeholderMapId")
                or request.query_params.get("placeholder_map_id")
                or ""
            ).strip()
            or None
        )
        filled_fields, template_pdf_id, fill_reason, template_json_name = _build_filled_template_fields_for_task(
            report_task,
            source_payload,
            map_id=ph_map_id,
            project=project,
            task_no=task_no,
            inspection_case=case,
        )
        if not filled_fields:
            return _fail(fill_reason or "模板字段填充失败", status.HTTP_409_CONFLICT)

        ok, pdf_reason, created_report_lf = _persist_filled_pdf_from_submit(
            request.user,
            task_no,
            case,
            project,
            filled_fields,
            template_pdf_id=template_pdf_id,
            template_json_name=template_json_name,
            task_obj=report_task,
        )
        if not ok:
            return _fail(pdf_reason or "报告导出失败", status.HTTP_500_INTERNAL_SERVER_ERROR)

        safe_tn = (task_no or "").replace("/", "_")
        report_suffix = f"__task__{safe_tn}-报告.pdf"
        legacy_name = f"{safe_tn}-报告.pdf"
        latest_file = created_report_lf
        if latest_file is None:
            latest_file = (
                LibraryFile.objects.filter(
                    category=LibraryFile.CATEGORY_REPORT,
                    link_entity=LibraryFile.LINK_ENTITY_INSPECTION_CASE,
                    link_object_id=case.pk,
                    projects=project,
                )
                .filter(Q(original_name=legacy_name) | Q(original_name__endswith=report_suffix))
                .order_by("-created_at", "-id")
                .distinct()
                .first()
            )
        payload = {
            "taskNo": task_no,
            "projectId": self._project_public_id(project),
            "reportTask": {"id": report_task.id, "code": report_task.code, "name": report_task.name},
            "mergeNote": source_reason or "",
            "sourceSiteRecordTasks": [
                {"id": t.id, "code": t.code, "name": t.name}
                for t in report_task.report_source_tasks.filter(output_target=LibraryTask.OUTPUT_SITE_RECORD).order_by(
                    "code", "id"
                )
            ],
        }
        if latest_file is not None:
            payload["reportFile"] = {
                "id": latest_file.id,
                "name": latest_file.original_name,
                "downloadUrl": self._build_file_download_url(
                    request,
                    task_no=task_no,
                    pk=latest_file.pk,
                    category=LibraryFile.CATEGORY_REPORT,
                    project=project,
                ),
            }
        return _ok("报告导出成功", payload)


class InspectionSignatureDownloadAPIView(_InspectionTaskAccessMixin, APIView):
    """按 taskNo 下载签名 PNG。"""

    permission_classes = [IsAuthenticated]

    _ROLE_FIELD_MAP = {
        "author": "sign_author_png",
        "reviewer": "sign_reviewer_png",
        "approver": "sign_approver_png",
    }

    def get(self, request, task_no: str, role: str):
        case, _project, err_resp = self._resolve_case_project(request, task_no)
        if err_resp is not None:
            return err_resp
        field = self._ROLE_FIELD_MAP.get(role)
        if not field:
            return Response({"detail": "role 必须为 author/reviewer/approver"}, status=400)
        obj = get_object_or_404(InspectionSubmission, task_no=task_no, case=case)
        content = getattr(obj, field, None)
        if not content:
            raise Http404("签名不存在")
        return FileResponse(
            io.BytesIO(bytes(content)),
            as_attachment=True,
            filename=f"{task_no}_{role}.png",
            content_type="image/png",
        )


class InspectionSignatureUploadAPIView(_InspectionTaskAccessMixin, APIView):
    """按 taskNo 上传角色签名（报告生成后开放，格式与 submit 一致）。"""

    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser]

    _ROLE_FIELD_MAP = {
        "author": "sign_author_png",
        "reviewer": "sign_reviewer_png",
        "approver": "sign_approver_png",
    }

    def post(self, request, task_no: str, character: str):
        case, project, err_resp = self._resolve_case_project(request, task_no)
        if err_resp is not None:
            return err_resp

        role = (character or "").strip()
        field = self._ROLE_FIELD_MAP.get(role)
        if not field:
            return _fail("character 必须为 author/reviewer/approver", status.HTTP_400_BAD_REQUEST)

        has_report = (
            LibraryFile.objects.filter(
                category=LibraryFile.CATEGORY_REPORT,
                projects=project,
                link_entity=LibraryFile.LINK_ENTITY_INSPECTION_CASE,
                link_object_id=case.pk,
            )
            .distinct()
            .exists()
        )
        if not has_report:
            return _fail("报告尚未生成，暂不允许上传签名", status.HTTP_409_CONFLICT)

        payload = request.data or {}
        signatures = payload.get("signatures")
        if signatures in (None, ""):
            signatures = {}
        if not isinstance(signatures, dict):
            return _fail("signatures 必须是对象", status.HTTP_400_BAD_REQUEST)
        data_url = signatures.get(role)
        if not data_url:
            return _fail(f"signatures.{role} 不能为空", status.HTTP_400_BAD_REQUEST)
        try:
            raw = decode_png_data_url(data_url)
        except Exception as exc:
            return _fail(f"signatures.{role} 格式无效：{exc}", status.HTTP_422_UNPROCESSABLE_ENTITY)
        if not raw:
            return _fail(f"signatures.{role} 不能为空", status.HTTP_400_BAD_REQUEST)

        obj = (
            InspectionSubmission.objects.filter(task_no=task_no, case=case)
            .select_related("case", "project")
            .first()
        )
        now = timezone.now()
        if obj is None:
            obj = InspectionSubmission(
                task_no=task_no,
                report_type=DEFAULT_REPORT_TYPE,
                status=InspectionSubmission.STATUS_IN_PROGRESS,
                case=case,
                project=project,
                created_at_remote=now,
                updated_at_remote=now,
                report_info={},
                hospital_info={},
                equipment_info={},
                test_result={},
                conclusion={},
                raw_payload={},
                created_by=request.user,
            )
        setattr(obj, field, raw)
        obj.sign_date = now
        obj.project = project
        obj.updated_at_remote = now
        existing_raw = obj.raw_payload if isinstance(obj.raw_payload, dict) else {}
        existing_signatures = existing_raw.get("signatures")
        if not isinstance(existing_signatures, dict):
            existing_signatures = {}
        existing_signatures.update(
            {
                "preparedBy": signatures.get("preparedBy") or "",
                "reviewedBy": signatures.get("reviewedBy") or "",
                "approvedBy": signatures.get("approvedBy") or "",
            }
        )
        existing_raw["signatures"] = existing_signatures
        obj.raw_payload = existing_raw
        if obj.created_by_id is None:
            obj.created_by = request.user
        obj.save()

        safe_task_no = (task_no or "").replace("/", "_")
        ts = timezone.localtime(now).strftime("%Y%m%d%H%M%S")
        filename = f"{safe_task_no}_signature_{character}_{ts}.png"
        wrapped = type("UploadLike", (), {"read": lambda self, b=raw: b, "name": filename})()
        save_library_binary_uploads(
            request.user,
            [wrapped],
            LibraryFile.CATEGORY_INSPECTION_SUBMIT,
            link_entity=LibraryFile.LINK_ENTITY_INSPECTION_CASE,
            link_object_id=case.pk,
            project_ids=[project.pk],
        )

        return _ok(
            "签名上传成功",
            {
                "taskNo": task_no,
                "character": role,
                "filename": filename,
                "signDate": obj.sign_date.isoformat() if obj.sign_date else None,
                "names": {
                    "preparedBy": existing_signatures.get("preparedBy", ""),
                    "reviewedBy": existing_signatures.get("reviewedBy", ""),
                    "approvedBy": existing_signatures.get("approvedBy", ""),
                },
            },
        )


class InspectionTaskFileUploadAPIView(_InspectionTaskAccessMixin, APIView):
    """按 taskNo 上传文件到对应项目（项目由后台任务分配决定）。"""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    _ALLOWED = frozenset(
        {
            LibraryFile.CATEGORY_TEMPLATE,
            LibraryFile.CATEGORY_SITE_RECORD,
            LibraryFile.CATEGORY_REPORT,
            LibraryFile.CATEGORY_ATTACHMENT,
            LibraryFile.CATEGORY_UPLOAD,
            LibraryFile.CATEGORY_JSON,
        }
    )

    def post(self, request, task_no: str):
        if not role_has(request.user, "perm_file_library"):
            return _fail("无权访问文件库", status.HTTP_403_FORBIDDEN)
        case, project, err_resp = self._resolve_case_project(request, task_no)
        if err_resp is not None:
            return err_resp

        category = (request.POST.get("category") or "").strip()
        if category not in self._ALLOWED:
            return _fail(f"不支持的 category，允许: {sorted(self._ALLOWED)}", status.HTTP_400_BAD_REQUEST)
        if not role_can_upload_library_category(request.user, category):
            return _fail("无权向该分类上传", status.HTTP_403_FORBIDDEN)

        files = list(request.FILES.getlist("files"))
        if not files:
            one = request.FILES.get("file")
            if one:
                files = [one]
        if not files:
            return _fail("缺少 files 或 file", status.HTTP_400_BAD_REQUEST)

        link_entity = (request.POST.get("link_entity") or LibraryFile.LINK_ENTITY_INSPECTION_CASE).strip()
        raw_link_id = (request.POST.get("link_object_id") or "").strip()

        if link_entity == LibraryFile.LINK_ENTITY_INSPECTION_CASE:
            link_object_id = case.pk
        else:
            if not raw_link_id:
                return _fail("link_object_id 必填", status.HTTP_400_BAD_REQUEST)
            try:
                link_object_id = int(raw_link_id)
            except ValueError:
                return _fail("link_object_id 必须为整数", status.HTTP_400_BAD_REQUEST)
            if link_entity == LibraryFile.LINK_ENTITY_SITE_RECORD:
                sr = get_object_or_404(SiteRecord, pk=link_object_id)
                if sr.case_id != case.pk:
                    return _fail("site_record 不属于当前 taskNo", status.HTTP_400_BAD_REQUEST)
            elif link_entity == LibraryFile.LINK_ENTITY_REPORT:
                rp = get_object_or_404(Report, pk=link_object_id)
                if rp.case_id != case.pk:
                    return _fail("report 不属于当前 taskNo", status.HTTP_400_BAD_REQUEST)
            else:
                return _fail("link_entity 必须是 inspection_case / site_record / report", status.HTTP_400_BAD_REQUEST)

        created, skipped = save_library_binary_uploads(
            request.user,
            files,
            category,
            link_entity=link_entity,
            link_object_id=link_object_id,
            project_ids=[project.pk],
        )
        for row in created:
            row["taskNo"] = task_no
            row["download_url"] = self._build_file_download_url(
                request,
                task_no=task_no,
                pk=row["id"],
                category=category,
                project=project,
            )
        return Response({"taskNo": task_no, "created": created, "skipped": skipped})


class InspectionTaskFileListAPIView(_InspectionTaskAccessMixin, APIView):
    """按 taskNo 列出可访问文件。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, task_no: str, category: str):
        if not role_has(request.user, "perm_file_library"):
            return _fail("无权访问文件库", status.HTTP_403_FORBIDDEN)
        _case, project, err_resp = self._resolve_case_project(request, task_no)
        if err_resp is not None:
            return err_resp
        category = (category or "").strip()
        if category not in VALID_LIBRARY_FILE_CATEGORIES:
            return _fail("category 参数无效", status.HTTP_400_BAD_REQUEST)
        qs = (
            LibraryFile.objects.filter(projects=project)
            .select_related("created_by")
            .order_by("-created_at")
            .distinct()
        )
        qs = qs.filter(category=category)
        rows = []
        for lf in qs:
            if not library_file_access_allowed(request.user, lf):
                continue
            rows.append(
                {
                    "id": lf.pk,
                    "taskNo": task_no,
                    "category": lf.category,
                    "original_name": lf.original_name,
                    "size": lf.size,
                    "link_entity": lf.link_entity,
                    "link_object_id": lf.link_object_id,
                    "created_at": lf.created_at.isoformat(),
                    "download_url": self._build_file_download_url(
                        request,
                        task_no=task_no,
                        pk=lf.pk,
                        category=lf.category,
                        project=project,
                    ),
                }
            )
        return Response({"taskNo": task_no, "count": len(rows), "results": rows})


class InspectionTaskFileDownloadAPIView(_InspectionTaskAccessMixin, APIView):
    """按 taskNo 下载指定文件（必须属于该 task 对应项目）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, task_no: str, pk: int, category: str):
        if not role_has(request.user, "perm_file_download"):
            return _fail("无权下载文件", status.HTTP_403_FORBIDDEN)
        _case, project, err_resp = self._resolve_case_project(request, task_no)
        if err_resp is not None:
            return err_resp
        category = (category or "").strip()
        if category not in VALID_LIBRARY_FILE_CATEGORIES:
            return _fail("category 参数无效", status.HTTP_400_BAD_REQUEST)
        lf_qs = LibraryFile.objects.filter(projects=project).distinct()
        lf_qs = lf_qs.filter(category=category)
        lf = get_object_or_404(lf_qs, pk=pk)
        if not library_file_access_allowed(request.user, lf):
            return _fail("无权下载该文件", status.HTTP_403_FORBIDDEN)
        return library_file_download_response(lf)


class InspectionTaskOCRUploadAPIView(_InspectionTaskAccessMixin, APIView):
    """taskNo 版 OCR 上传并创建异步流程任务。"""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, task_no: str):
        if not role_has(request.user, "perm_file_library"):
            return _fail("无权访问文件库", status.HTTP_403_FORBIDDEN)
        if not role_can_upload_library_category(request.user, LibraryFile.CATEGORY_UPLOAD):
            return _fail("无权上传 OCR 文件", status.HTTP_403_FORBIDDEN)
        _case, project, err_resp = self._resolve_case_project(request, task_no)
        if err_resp is not None:
            return err_resp
        files = list(request.FILES.getlist("files"))
        if not files:
            one = request.FILES.get("file")
            if one:
                files = [one]
        if not files:
            return _fail(
                "缺少上传文件。请使用 multipart 字段 files（多文件）或 file（单文件）。",
                status.HTTP_400_BAD_REQUEST,
            )
        created, skipped = save_library_binary_uploads(
            request.user,
            files,
            LibraryFile.CATEGORY_UPLOAD,
            project_ids=[project.pk],
        )
        task = LibraryOCRProcessTask.objects.create(
            created_by=request.user,
            project_id=project.pk,
            source_file_ids=[row["id"] for row in created],
            status=LibraryOCRProcessTask.STATUS_PENDING,
        )
        threading.Thread(
            target=LibraryOCRUploadAPIView._run_ocr_pipeline_task,
            args=(task.pk,),
            daemon=True,
        ).start()
        for row in created:
            row["taskNo"] = task_no
            row["download_url"] = self._build_file_download_url(
                request,
                task_no=task_no,
                pk=row["id"],
                category=LibraryFile.CATEGORY_UPLOAD,
                project=project,
            )
        return _ok(
            "OCR 任务已创建",
            {
                "taskNo": task_no,
                "uploaded": created,
                "skipped": skipped,
                "count": len(created),
                "ocrTask": {
                    "id": task.pk,
                    "status": task.status,
                    "statusUrl": request.build_absolute_uri(
                        self._resolve_ocr_url(
                            request,
                            ocr_task_id=task.pk,
                            task_no=task_no,
                            project=project,
                            kind="status",
                        )
                    ),
                    "autofillUrl": request.build_absolute_uri(
                        self._resolve_ocr_url(
                            request,
                            ocr_task_id=task.pk,
                            task_no=task_no,
                            project=project,
                            kind="autofill",
                        )
                    ),
                },
            },
            http_status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )

    def _resolve_ocr_url(self, request, *, ocr_task_id: int, task_no: str, project, kind: str) -> str:
        """
        OCR 跳转 URL 兼容 v1/v2 与 project-task 两套路由命名，避免 reverse 失败导致 500。
        """
        project_id = ""
        if project is not None:
            try:
                project_id = self._project_public_id(project)
            except Exception:
                project_id = ""
        if kind == "status":
            candidates = [
                ("api_inspection_project_task_ocr_status", {"project_id": project_id, "task_no": task_no, "ocr_task_id": ocr_task_id}),
                ("api_v2_inspection_project_task_ocr_status", {"project_id": project_id, "task_no": task_no, "ocr_task_id": ocr_task_id}),
                ("api_inspection_task_ocr_status", {"task_no": task_no, "ocr_task_id": ocr_task_id}),
                ("api_v2_inspection_task_ocr_status_legacy", {"task_no": task_no, "ocr_task_id": ocr_task_id}),
            ]
        else:
            candidates = [
                ("api_inspection_project_task_ocr_autofill", {"project_id": project_id, "task_no": task_no, "ocr_task_id": ocr_task_id}),
                ("api_v2_inspection_project_task_ocr_autofill", {"project_id": project_id, "task_no": task_no, "ocr_task_id": ocr_task_id}),
                ("api_inspection_task_ocr_autofill", {"task_no": task_no, "ocr_task_id": ocr_task_id}),
                ("api_v2_inspection_task_ocr_autofill_legacy", {"task_no": task_no, "ocr_task_id": ocr_task_id}),
            ]
        for name, kwargs in candidates:
            if ("project_id" in kwargs) and not kwargs.get("project_id"):
                continue
            try:
                return reverse(name, kwargs=kwargs)
            except NoReverseMatch:
                continue
        # 理论兜底：若全部路由名都不可用，返回当前请求路径，避免抛 500。
        return request.path


class InspectionTaskOCRStatusAPIView(_InspectionTaskAccessMixin, APIView):
    """taskNo 版 OCR 异步任务状态。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, task_no: str, ocr_task_id: int):
        _case, project, err_resp = self._resolve_case_project(request, task_no)
        if err_resp is not None:
            return err_resp
        task = get_object_or_404(LibraryOCRProcessTask, pk=ocr_task_id)
        if task.project_id != project.pk:
            return _fail("该 OCR 任务不属于当前 taskNo", status.HTTP_403_FORBIDDEN)
        files = list(
            LibraryFile.objects.filter(pk__in=task.generated_json_file_ids, category=LibraryFile.CATEGORY_JSON)
            .order_by("created_at")
            .values("id", "original_name", "size", "created_at")
        )
        for f in files:
            f["download_url"] = self._build_file_download_url(
                request,
                task_no=task_no,
                pk=f["id"],
                category=LibraryFile.CATEGORY_JSON,
                project=project,
            )
        return _ok(
            "获取成功",
            {
                "taskNo": task_no,
                "ocrTaskId": task.pk,
                "status": task.status,
                "projectId": task.project_id,
                "batchId": task.batch_id,
                "errorMessage": task.error_message,
                "resultSummary": task.result_summary,
                "generatedJsonFiles": files,
            },
        )


class InspectionTaskOCRAutofillAPIView(_InspectionTaskAccessMixin, APIView):
    """taskNo 版 OCR 自动填写数据。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, task_no: str, ocr_task_id: int):
        _case, project, err_resp = self._resolve_case_project(request, task_no)
        if err_resp is not None:
            return err_resp
        task = get_object_or_404(LibraryOCRProcessTask, pk=ocr_task_id)
        if task.project_id != project.pk:
            return _fail("该 OCR 任务不属于当前 taskNo", status.HTTP_403_FORBIDDEN)
        if task.status != LibraryOCRProcessTask.STATUS_SUCCESS:
            if task.status == LibraryOCRProcessTask.STATUS_FAILED:
                return _fail("任务处理失败，无可用自动填写数据", status.HTTP_409_CONFLICT)
            return _fail("任务处理中，请稍后再试", status.HTTP_409_CONFLICT)

        ids = list(task.generated_json_file_ids or [])
        if not ids and task.batch_id:
            recovered = list(
                LibraryFile.objects.filter(
                    category=LibraryFile.CATEGORY_JSON,
                    batch_id=task.batch_id,
                )
                .order_by("created_at")
                .values_list("id", flat=True)
            )
            if recovered:
                task.generated_json_file_ids = recovered
                task.save(update_fields=["generated_json_file_ids", "updated_at"])
                ids = recovered
        if not ids:
            return _fail("任务未生成 JSON 文件", status.HTTP_404_NOT_FOUND)

        by_pk = {
            lf.pk: lf
            for lf in LibraryFile.objects.filter(
                pk__in=ids,
                category=LibraryFile.CATEGORY_JSON,
            )
        }
        ordered_files = [by_pk[i] for i in ids if i in by_pk]
        if not ordered_files:
            return _fail("关联的 JSON 文件记录已不存在", status.HTTP_404_NOT_FOUND)

        generated_json_files = []
        lf_payload = None
        payload = None
        last_read_error = None
        for lf in ordered_files:
            if not library_file_access_allowed(request.user, lf):
                continue
            generated_json_files.append(
                {
                    "id": lf.pk,
                    "name": lf.original_name,
                    "downloadUrl": self._build_file_download_url(
                        request,
                        task_no=task_no,
                        pk=lf.pk,
                        category=LibraryFile.CATEGORY_JSON,
                        project=project,
                    ),
                }
            )
            if lf_payload is not None:
                continue
            try:
                p = pipeline_service.library_absolute_path(lf.relative_path)
                text = p.read_text(encoding="utf-8", errors="replace")
                payload = json_std.loads(text)
                lf_payload = lf
            except (ValueError, OSError, json_std.JSONDecodeError) as exc:
                last_read_error = str(exc)
                continue
        if not generated_json_files:
            return _fail("无权访问生成的 JSON 文件", status.HTTP_403_FORBIDDEN)
        if lf_payload is None or payload is None:
            return _fail(
                f"JSON 文件读取或解析失败: {last_read_error or 'unknown error'}",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return _ok(
            "获取成功",
            {
                "taskNo": task_no,
                "ocrTaskId": task.pk,
                "ready": True,
                "jsonFile": {
                    "id": lf_payload.pk,
                    "name": lf_payload.original_name,
                    "downloadUrl": self._build_file_download_url(
                        request,
                        task_no=task_no,
                        pk=lf_payload.pk,
                        category=LibraryFile.CATEGORY_JSON,
                        project=project,
                    ),
                },
                "generatedJsonFiles": generated_json_files,
                "payload": payload,
            },
        )


class InspectionProjectTaskDetailAPIView(InspectionDetailAPIView):
    def get(self, request, project_id: str, task_no: str):
        case, _project, err_resp = self._ensure_task_under_project(request, project_id, task_no)
        if err_resp is not None:
            return err_resp
        obj = self._submission_or_init(case, case.library_project)
        data = self._serialize_task_detail(case, obj)
        data["projectId"] = self._project_public_id(case.library_project) if case.library_project_id else ""
        data["taskNo"] = task_no
        return _ok("获取成功", data)


class InspectionProjectTaskStartAPIView(InspectionStartAPIView):
    def post(self, request, project_id: str, task_no: str):
        case, _project, err_resp = self._ensure_task_under_project(request, project_id, task_no)
        if err_resp is not None:
            return err_resp
        resp = super().post(request, task_no=case.case_no)
        if isinstance(getattr(resp, "data", None), dict):
            data = resp.data.get("data")
            if isinstance(data, dict):
                data["taskNo"] = task_no
                data["projectId"] = self._project_public_id(case.library_project) if case.library_project_id else project_id
        return resp


class InspectionProjectTaskDraftAPIView(InspectionDraftAPIView):
    def post(self, request, project_id: str, task_no: str):
        case, _project, err_resp = self._ensure_task_under_project(request, project_id, task_no)
        if err_resp is not None:
            return err_resp
        resp = super().post(request, task_no=case.case_no)
        if isinstance(getattr(resp, "data", None), dict):
            data = resp.data.get("data")
            if isinstance(data, dict):
                data["taskNo"] = task_no
                data["projectId"] = self._project_public_id(case.library_project) if case.library_project_id else project_id
        return resp


class InspectionProjectTaskSubmitAPIView(InspectionSubmitByTaskAPIView):
    def post(self, request, project_id: str, task_no: str):
        case, _project, err_resp = self._ensure_task_under_project(request, project_id, task_no)
        if err_resp is not None:
            return err_resp
        resp = super().post(request, task_no=case.case_no)
        if isinstance(getattr(resp, "data", None), dict):
            data = resp.data.get("data")
            if isinstance(data, dict):
                data["taskNo"] = task_no
                data["projectId"] = self._project_public_id(case.library_project) if case.library_project_id else project_id
        return resp


class InspectionProjectTaskFrontendJsonExportAPIView(InspectionTaskFrontendJsonExportAPIView):
    def get(self, request, project_id: str, task_no: str):
        case, _project, err_resp = self._ensure_task_under_project(request, project_id, task_no)
        if err_resp is not None:
            return err_resp
        return super().get(request, task_no=case.case_no)


class InspectionProjectTaskManualExportReportAPIView(InspectionTaskManualExportReportAPIView):
    def post(self, request, project_id: str, task_no: str):
        case, _project, err_resp = self._ensure_task_under_project(request, project_id, task_no)
        if err_resp is not None:
            return err_resp
        return super().post(request, task_no=case.case_no)


class InspectionProjectTaskFileUploadAPIView(InspectionTaskFileUploadAPIView):
    def post(self, request, project_id: str, task_no: str):
        case, _project, err_resp = self._ensure_task_under_project(request, project_id, task_no)
        if err_resp is not None:
            return err_resp
        return super().post(request, task_no=case.case_no)


class InspectionProjectTaskFileListAPIView(InspectionTaskFileListAPIView):
    def get(self, request, project_id: str, task_no: str, category: str):
        case, _project, err_resp = self._ensure_task_under_project(request, project_id, task_no)
        if err_resp is not None:
            return err_resp
        return super().get(request, task_no=case.case_no, category=category)


class InspectionProjectTaskFileDownloadAPIView(InspectionTaskFileDownloadAPIView):
    def get(self, request, project_id: str, task_no: str, pk: int, category: str):
        case, _project, err_resp = self._ensure_task_under_project(request, project_id, task_no)
        if err_resp is not None:
            return err_resp
        return super().get(request, task_no=case.case_no, pk=pk, category=category)


class InspectionProjectTaskOCRUploadAPIView(InspectionTaskOCRUploadAPIView):
    def post(self, request, project_id: str, task_no: str):
        case, _project, err_resp = self._ensure_task_under_project(request, project_id, task_no)
        if err_resp is not None:
            return err_resp
        return super().post(request, task_no=case.case_no)


class InspectionProjectTaskOCRStatusAPIView(InspectionTaskOCRStatusAPIView):
    def get(self, request, project_id: str, task_no: str, ocr_task_id: int):
        case, _project, err_resp = self._ensure_task_under_project(request, project_id, task_no)
        if err_resp is not None:
            return err_resp
        return super().get(request, task_no=case.case_no, ocr_task_id=ocr_task_id)


class InspectionProjectTaskOCRAutofillAPIView(InspectionTaskOCRAutofillAPIView):
    def get(self, request, project_id: str, task_no: str, ocr_task_id: int):
        case, _project, err_resp = self._ensure_task_under_project(request, project_id, task_no)
        if err_resp is not None:
            return err_resp
        return super().get(request, task_no=case.case_no, ocr_task_id=ocr_task_id)


class InspectionProjectTaskSignatureDownloadAPIView(InspectionSignatureDownloadAPIView):
    def get(self, request, project_id: str, task_no: str, role: str):
        case, _project, err_resp = self._ensure_task_under_project(request, project_id, task_no)
        if err_resp is not None:
            return err_resp
        return super().get(request, task_no=case.case_no, role=role)


class InspectionProjectTaskSignatureUploadAPIView(InspectionSignatureUploadAPIView):
    def post(self, request, project_id: str, task_no: str, character: str):
        case, _project, err_resp = self._ensure_task_under_project(request, project_id, task_no)
        if err_resp is not None:
            return err_resp
        return super().post(request, task_no=case.case_no, character=character)
