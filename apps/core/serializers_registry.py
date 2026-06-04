"""业务主数据与案件 / 原始记录 / 报告 API 序列化。"""
from typing import Optional

from rest_framework import serializers

from apps.core.models import (
    BizContact,
    BizDevice,
    InspectionCase,
    InstrumentCatalog,
    InspectedOrganization,
    LibraryTaskAssignment,
    Report,
    SiteRecord,
)


def resolve_registry_created_by(
    case: Optional[InspectionCase], library_task_id: Optional[int]
) -> Optional[int]:
    """
    根据案件关联的文件库项目及任务分配，解析现场记录/报告的创建者用户主键。
    指定 library_task 时取该项目下该任务的最近一条分配；未指定时若全部分配对象唯一则取其用户。
    """
    if case is None or not case.library_project_id:
        return None
    qs = LibraryTaskAssignment.objects.filter(project_id=case.library_project_id)
    if library_task_id:
        row = qs.filter(library_task_id=library_task_id).order_by("-created_at").first()
        return row.assignee_id if row else None
    assignee_ids = list(qs.values_list("assignee_id", flat=True).distinct())
    if len(assignee_ids) == 1:
        return assignee_ids[0]
    return None


class InspectedOrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = InspectedOrganization
        fields = (
            "id",
            "name",
            "address",
            "credit_code",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")

    def create(self, validated_data):
        validated_data["created_by"] = self.context["request"].user
        return super().create(validated_data)


class BizContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = BizContact
        fields = (
            "id",
            "organization",
            "name",
            "phone",
            "title",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")

    def create(self, validated_data):
        validated_data["created_by"] = self.context["request"].user
        return super().create(validated_data)


class BizDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = BizDevice
        fields = (
            "id",
            "organization",
            "name",
            "model",
            "serial_no",
            "manufacturer",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")

    def create(self, validated_data):
        validated_data["created_by"] = self.context["request"].user
        return super().create(validated_data)


class InstrumentCatalogSerializer(serializers.ModelSerializer):
    inventoryStatus = serializers.SerializerMethodField()
    checkoutProjectId = serializers.IntegerField(
        source="checkout_project_id", read_only=True, allow_null=True
    )
    checkoutProjectCode = serializers.SerializerMethodField()

    class Meta:
        model = InstrumentCatalog
        fields = (
            "id",
            "code",
            "name",
            "model",
            "calibration_org",
            "certificate_no",
            "certificate_valid_until",
            "remarks",
            "is_active",
            "inventoryStatus",
            "checkoutProjectId",
            "checkoutProjectCode",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")

    def get_inventoryStatus(self, obj) -> str:
        return "in_stock" if obj.checkout_project_id is None else "checked_out"

    def get_checkoutProjectCode(self, obj) -> str | None:
        if obj.checkout_project_id and obj.checkout_project:
            return obj.checkout_project.code
        return None


class InspectionCaseSerializer(serializers.ModelSerializer):
    devices = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=BizDevice.objects.all(),
        required=False,
    )

    class Meta:
        model = InspectionCase
        fields = (
            "id",
            "case_no",
            "inspected_organization",
            "primary_contact",
            "devices",
            "notes",
            "library_project",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")

    def create(self, validated_data):
        devices = validated_data.pop("devices", [])
        validated_data["created_by"] = self.context["request"].user
        obj = InspectionCase.objects.create(**validated_data)
        obj.devices.set(devices)
        return obj

    def update(self, instance, validated_data):
        devices = validated_data.pop("devices", None)
        for k, v in validated_data.items():
            setattr(instance, k, v)
        instance.save()
        if devices is not None:
            instance.devices.set(devices)
        return instance


class SiteRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteRecord
        fields = (
            "id",
            "case",
            "record_no",
            "record_date",
            "payload_json",
            "library_task",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")

    def create(self, validated_data):
        case = validated_data.get("case")
        lt = validated_data.get("library_task")
        uid = resolve_registry_created_by(case, lt.pk if lt else None)
        if uid:
            validated_data["created_by_id"] = uid
        else:
            validated_data["created_by"] = self.context["request"].user
        return super().create(validated_data)


class ReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = Report
        fields = (
            "id",
            "case",
            "site_record",
            "report_no",
            "version",
            "status",
            "issued_at",
            "library_task",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")

    def validate(self, attrs):
        site_record = attrs.get("site_record")
        case = attrs.get("case")
        if getattr(self, "instance", None) and self.instance is not None:
            site_record = site_record or self.instance.site_record
            case = case or self.instance.case
        if site_record and case and site_record.case_id != case.pk:
            raise serializers.ValidationError(
                {"site_record": "原始记录必须属于同一案件。"}
            )
        return attrs

    def create(self, validated_data):
        case = validated_data.get("case")
        lt = validated_data.get("library_task")
        uid = resolve_registry_created_by(case, lt.pk if lt else None)
        if uid:
            validated_data["created_by_id"] = uid
        else:
            validated_data["created_by"] = self.context["request"].user
        return super().create(validated_data)
