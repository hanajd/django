"""检测任务接口（pending/history/start/draft/submit + taskNo 文件约束）。"""
import io
import json as json_std
import logging
import threading

from django.core.paginator import Paginator
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.library_access import (
    library_file_access_allowed,
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


def _persist_submit_signature_files(user, task_no: str, case: InspectionCase, project, submission: InspectionSubmission):
    """将提交签名写入文件库「检测提交」分类，便于后台直接查看和下载。"""
    role_bytes_pairs = [
        ("author", submission.sign_author_png),
        ("reviewer", submission.sign_reviewer_png),
        ("approver", submission.sign_approver_png),
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
    _build_filled_template_fields_from_submit,
    _pick_submit_generation_tasks,
    _persist_filled_pdf_from_submit,
)


class _InspectionTaskAccessMixin:
    """统一 taskNo -> 案件/项目 访问控制。"""

    @staticmethod
    def _build_assignment_task_no(assignment: LibraryTaskAssignment) -> str:
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
                if role_has(request.user, "perm_assign_tasks"):
                    assignment = aqs.filter(pk=aid).first()
                else:
                    assignment = aqs.filter(pk=aid, assignee=request.user).first()
                if assignment and assignment.project_id:
                    case = mixin._ensure_case_for_assignment(assignment)
        if case is None:
            return None, None, _fail("任务不存在", status.HTTP_404_NOT_FOUND)
        if not case.library_project_id:
            return None, None, _fail("该 taskNo 未绑定后台项目", status.HTTP_400_BAD_REQUEST)
        if role_has(request.user, "perm_assign_tasks"):
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


class InspectionPendingAPIView(_InspectionTaskAccessMixin, APIView):
    """获取当前用户待检测任务列表。"""

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
        return self._build_response(request, forced_status_filter="pending")


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
                task_obj, request.data, map_id=ph_map_id, project=project
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
            ok, pdf_reason = _persist_filled_pdf_from_submit(
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
            row["download_url"] = request.build_absolute_uri(
                reverse(
                    "api_inspection_task_file_download",
                    kwargs={"task_no": task_no, "pk": row["id"], "category": category},
                )
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
                    "download_url": request.build_absolute_uri(
                        reverse(
                            "api_inspection_task_file_download",
                            kwargs={"task_no": task_no, "pk": lf.pk, "category": lf.category},
                        )
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
            row["download_url"] = request.build_absolute_uri(
                reverse(
                    "api_inspection_task_file_download",
                    kwargs={
                        "task_no": task_no,
                        "pk": row["id"],
                        "category": LibraryFile.CATEGORY_UPLOAD,
                    },
                )
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
                        reverse("api_inspection_task_ocr_status", kwargs={"task_no": task_no, "ocr_task_id": task.pk})
                    ),
                    "autofillUrl": request.build_absolute_uri(
                        reverse(
                            "api_inspection_task_ocr_autofill",
                            kwargs={"task_no": task_no, "ocr_task_id": task.pk},
                        )
                    ),
                },
            },
            http_status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )


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
            f["download_url"] = request.build_absolute_uri(
                reverse(
                    "api_inspection_task_file_download",
                    kwargs={
                        "task_no": task_no,
                        "pk": f["id"],
                        "category": LibraryFile.CATEGORY_JSON,
                    },
                )
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
                    "downloadUrl": request.build_absolute_uri(
                        reverse(
                            "api_inspection_task_file_download",
                            kwargs={
                                "task_no": task_no,
                                "pk": lf.pk,
                                "category": LibraryFile.CATEGORY_JSON,
                            },
                        )
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
                    "downloadUrl": request.build_absolute_uri(
                        reverse(
                            "api_inspection_task_file_download",
                            kwargs={
                                "task_no": task_no,
                                "pk": lf_payload.pk,
                                "category": LibraryFile.CATEGORY_JSON,
                            },
                        )
                    ),
                },
                "generatedJsonFiles": generated_json_files,
                "payload": payload,
            },
        )
