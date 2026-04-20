"""检测报告提交接口序列化器（taskNo 绑定项目任务）。"""
import base64
import re

from rest_framework import serializers


TASK_NO_PATTERN = re.compile(r"^(XF-\d{8}-\d+|ASG-\d+)$")


def decode_png_data_url(data_url: str):
    if not data_url:
        return None
    prefix = "data:image/png;base64,"
    if not isinstance(data_url, str) or not data_url.startswith(prefix):
        raise serializers.ValidationError("签名格式必须为 data:image/png;base64,")
    encoded = data_url[len(prefix):]
    try:
        return base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise serializers.ValidationError("签名 Base64 数据无效") from exc


class InspectionSubmitSerializer(serializers.Serializer):
    taskNo = serializers.CharField(max_length=64)
    reportType = serializers.CharField(max_length=64)
    createdAt = serializers.DateTimeField()
    updatedAt = serializers.DateTimeField()
    reportInfo = serializers.DictField()
    hospitalInfo = serializers.DictField()
    equipmentInfo = serializers.DictField()
    instruments = serializers.ListField(child=serializers.DictField(), allow_empty=True)
    testResult = serializers.DictField()
    signatures = serializers.DictField(required=False, allow_null=True, default=dict)
    conclusion = serializers.DictField()

    def validate(self, attrs):
        task_no = attrs.get("taskNo", "")
        if not TASK_NO_PATTERN.match(task_no):
            raise serializers.ValidationError({"taskNo": "格式必须为 XF-YYYYMMDD-序号 或 ASG-数字"})
        if attrs.get("reportType") != "xray_fluoroscopy":
            raise serializers.ValidationError({"reportType": "必须为 xray_fluoroscopy"})

        test_result = attrs.get("testResult") or {}
        if test_result.get("isDsaDevice") is False and test_result.get("dsa") not in (None, {}, ""):
            raise serializers.ValidationError(
                {"testResult": "当 isDsaDevice=false 时，dsa 字段必须为 null 或空对象"}
            )
        signatures = attrs.get("signatures") or {}
        attrs["_author_png"] = decode_png_data_url(signatures.get("author"))
        attrs["_reviewer_png"] = decode_png_data_url(signatures.get("reviewer"))
        attrs["_approver_png"] = decode_png_data_url(signatures.get("approver"))
        return attrs


class InspectionDraftSerializer(serializers.Serializer):
    reportInfo = serializers.DictField(required=False)
    hospitalInfo = serializers.DictField(required=False)
    equipmentInfo = serializers.DictField(required=False)
    instruments = serializers.ListField(child=serializers.DictField(), required=False)
    testResult = serializers.DictField(required=False)
    signatures = serializers.DictField(required=False, allow_null=True)
    conclusion = serializers.DictField(required=False)

    def validate(self, attrs):
        signatures = attrs.get("signatures") or {}
        attrs["_author_png"] = decode_png_data_url(signatures.get("author"))
        attrs["_reviewer_png"] = decode_png_data_url(signatures.get("reviewer"))
        attrs["_approver_png"] = decode_png_data_url(signatures.get("approver"))
        return attrs
