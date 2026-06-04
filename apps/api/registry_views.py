"""业务主数据、案件 / 原始记录 / 报告 REST API 与带联动的文件上传。"""
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.library_access import (
    library_file_access_allowed,
    role_can_upload_library_category,
    role_has,
)
from apps.core.library_file_service import parse_project_ids, save_library_binary_uploads
from apps.core.models import (
    BizContact,
    BizDevice,
    InspectionCase,
    InstrumentCatalog,
    InspectedOrganization,
    LibraryFile,
    LibraryProject,
    Report,
    SiteRecord,
)
from apps.core.serializers_registry import (
    BizContactSerializer,
    BizDeviceSerializer,
    InspectionCaseSerializer,
    InstrumentCatalogSerializer,
    InspectedOrganizationSerializer,
    ReportSerializer,
    SiteRecordSerializer,
)


class RegistryReadPermission(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and role_has(request.user, "perm_file_library")
        )


class RegistryWritePermission(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and role_has(request.user, "perm_biz_registry")
        )


class RegistryViewMixin:
    def get_permissions(self):
        if self.action in ("list", "retrieve", "trace"):
            return [IsAuthenticated(), RegistryReadPermission()]
        return [IsAuthenticated(), RegistryReadPermission(), RegistryWritePermission()]


def _library_file_brief(request, lf: LibraryFile) -> dict:
    return {
        "id": lf.pk,
        "original_name": lf.original_name,
        "category": lf.category,
        "size": lf.size,
        "link_entity": lf.link_entity,
        "link_object_id": lf.link_object_id,
        "created_at": lf.created_at.isoformat(),
        "download_url": request.build_absolute_uri(
            reverse("api_library_file_download", kwargs={"pk": lf.pk})
        ),
    }


def _files_for_entity(request, entity: str, object_id: int) -> list:
    qs = LibraryFile.objects.filter(
        link_entity=entity, link_object_id=object_id
    ).order_by("-created_at")
    out = []
    for lf in qs:
        if library_file_access_allowed(request.user, lf):
            out.append(_library_file_brief(request, lf))
    return out


class InspectedOrganizationViewSet(RegistryViewMixin, viewsets.ModelViewSet):
    queryset = InspectedOrganization.objects.all()
    serializer_class = InspectedOrganizationSerializer
    filter_backends = (filters.SearchFilter, filters.OrderingFilter)
    search_fields = ("name", "address", "credit_code")
    ordering_fields = ("id", "name", "updated_at", "created_at")
    ordering = ("-updated_at",)


class BizContactViewSet(RegistryViewMixin, viewsets.ModelViewSet):
    queryset = BizContact.objects.select_related("organization").all()
    serializer_class = BizContactSerializer
    filter_backends = (filters.SearchFilter, filters.OrderingFilter)
    search_fields = ("name", "phone", "title")
    ordering_fields = ("id", "name", "updated_at")
    ordering = ("-updated_at",)


class BizDeviceViewSet(RegistryViewMixin, viewsets.ModelViewSet):
    queryset = BizDevice.objects.select_related("organization").all()
    serializer_class = BizDeviceSerializer
    filter_backends = (filters.SearchFilter, filters.OrderingFilter)
    search_fields = ("name", "model", "serial_no", "manufacturer")
    ordering_fields = ("id", "name", "updated_at")
    ordering = ("-updated_at",)


class InstrumentCatalogViewSet(RegistryViewMixin, viewsets.ReadOnlyModelViewSet):
    queryset = InstrumentCatalog.objects.filter(is_active=True)
    serializer_class = InstrumentCatalogSerializer
    filter_backends = (filters.SearchFilter, filters.OrderingFilter)
    search_fields = ("code", "name", "model", "certificate_no", "calibration_org", "remarks")
    ordering_fields = ("id", "code", "name", "certificate_valid_until", "updated_at", "created_at")
    ordering = ("code",)


class InspectionCaseViewSet(RegistryViewMixin, viewsets.ModelViewSet):
    queryset = (
        InspectionCase.objects.select_related(
            "inspected_organization",
            "primary_contact",
            "library_project",
            "created_by",
        )
        .prefetch_related("devices")
        .all()
    )
    serializer_class = InspectionCaseSerializer
    filter_backends = (filters.SearchFilter, filters.OrderingFilter)
    search_fields = ("case_no", "notes")
    ordering_fields = ("id", "case_no", "created_at", "updated_at")
    ordering = ("-created_at",)


class SiteRecordViewSet(RegistryViewMixin, viewsets.ModelViewSet):
    queryset = SiteRecord.objects.select_related(
        "case",
        "case__library_project",
        "library_task",
        "created_by",
    ).all()
    serializer_class = SiteRecordSerializer
    filter_backends = (filters.SearchFilter, filters.OrderingFilter)
    search_fields = ("record_no",)
    ordering_fields = ("id", "record_no", "record_date", "created_at")
    ordering = ("-created_at",)


class ReportViewSet(RegistryViewMixin, viewsets.ModelViewSet):
    queryset = Report.objects.select_related(
        "case",
        "case__inspected_organization",
        "case__primary_contact",
        "case__library_project",
        "site_record",
        "library_task",
        "created_by",
    ).all()
    serializer_class = ReportSerializer
    filter_backends = (filters.SearchFilter, filters.OrderingFilter)
    search_fields = ("report_no",)
    ordering_fields = ("id", "report_no", "status", "created_at", "issued_at")
    ordering = ("-created_at",)

    @action(detail=True, methods=["get"], url_path="trace")
    def trace(self, request, pk=None):
        report = self.get_object()
        case = report.case
        site_record = report.site_record
        org_ser = InspectedOrganizationSerializer(case.inspected_organization).data
        contact = case.primary_contact
        contact_data = (
            BizContactSerializer(contact).data if contact is not None else None
        )
        devices = BizDeviceSerializer(case.devices.all(), many=True).data
        case_data = {
            "id": case.pk,
            "case_no": case.case_no,
            "notes": case.notes,
            "inspected_organization": org_ser,
            "primary_contact": contact_data,
            "devices": devices,
        }
        return Response(
            {
                "report": ReportSerializer(report).data,
                "site_record": SiteRecordSerializer(site_record).data,
                "case": case_data,
                "files": {
                    "report": _files_for_entity(
                        request, LibraryFile.LINK_ENTITY_REPORT, report.pk
                    ),
                    "site_record": _files_for_entity(
                        request, LibraryFile.LINK_ENTITY_SITE_RECORD, site_record.pk
                    ),
                    "case": _files_for_entity(
                        request, LibraryFile.LINK_ENTITY_INSPECTION_CASE, case.pk
                    ),
                },
            }
        )


class RegistryLinkedFileUploadAPIView(APIView):
    """
    上传文件到文件库并写入 ``LibraryFile.link_entity`` / ``link_object_id``。
    支持分类：template / site_record / report / attachment。
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    _ALLOWED = frozenset(
        {
            LibraryFile.CATEGORY_TEMPLATE,
            LibraryFile.CATEGORY_SITE_RECORD,
            LibraryFile.CATEGORY_REPORT,
            LibraryFile.CATEGORY_ATTACHMENT,
        }
    )

    def post(self, request):
        if not role_has(request.user, "perm_file_library"):
            return Response({"detail": "无权访问文件库"}, status=status.HTTP_403_FORBIDDEN)

        category = (request.POST.get("category") or "").strip()
        if category not in self._ALLOWED:
            return Response(
                {"detail": f"不支持的 category，允许: {sorted(self._ALLOWED)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not role_can_upload_library_category(request.user, category):
            return Response({"detail": "无权向该分类上传"}, status=status.HTTP_403_FORBIDDEN)

        link_entity = (request.POST.get("link_entity") or "").strip()
        raw_link_id = (request.POST.get("link_object_id") or "").strip()
        link_object_id = None
        if raw_link_id:
            try:
                link_object_id = int(raw_link_id)
            except ValueError:
                return Response(
                    {"detail": "link_object_id 必须为整数"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        if bool(link_entity) != bool(link_object_id is not None and link_object_id > 0):
            return Response(
                {"detail": "link_entity 与 link_object_id 必须同时提供或同时为空"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if link_entity:
            if link_entity == LibraryFile.LINK_ENTITY_REPORT:
                get_object_or_404(Report, pk=link_object_id)
            elif link_entity == LibraryFile.LINK_ENTITY_SITE_RECORD:
                get_object_or_404(SiteRecord, pk=link_object_id)
            elif link_entity == LibraryFile.LINK_ENTITY_INSPECTION_CASE:
                get_object_or_404(InspectionCase, pk=link_object_id)
            else:
                return Response(
                    {
                        "detail": "link_entity 必须是 inspection_case / site_record / report"
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        files = list(request.FILES.getlist("files"))
        if not files:
            one = request.FILES.get("file")
            if one:
                files = [one]
        if not files:
            return Response({"detail": "缺少 files 或 file"}, status=status.HTTP_400_BAD_REQUEST)
        raw_project_ids = request.POST.getlist("project_ids")
        if not raw_project_ids:
            one_project = (request.POST.get("project_id") or "").strip()
            if one_project:
                raw_project_ids = [one_project]
        project_ids = parse_project_ids(raw_project_ids)
        if project_ids:
            active_project_ids = set(
                LibraryProject.objects.filter(pk__in=project_ids, is_active=True).values_list("id", flat=True)
            )
            if len(active_project_ids) != len(set(project_ids)):
                return Response({"detail": "存在无效 project_id"}, status=status.HTTP_400_BAD_REQUEST)
            project_ids = sorted(active_project_ids)

        created, skipped = save_library_binary_uploads(
            request.user,
            files,
            category,
            link_entity=link_entity,
            link_object_id=link_object_id,
            project_ids=project_ids,
        )
        for row in created:
            row["download_url"] = request.build_absolute_uri(
                reverse("api_library_file_download", kwargs={"pk": row["id"]})
            )
        return Response({"created": created, "skipped": skipped})
