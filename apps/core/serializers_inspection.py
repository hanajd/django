"""检测报告提交接口序列化器（taskNo 绑定项目任务）。"""
import base64

from rest_framework import serializers


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


def decode_png_base64_any(value: str):
    """
    兼容签名图片两种格式：
    - data:image/png;base64,xxxx
    - 纯 base64（无 data URL 前缀）
    """
    if not value:
        return None
    if not isinstance(value, str):
        raise serializers.ValidationError("签名格式必须为字符串")
    text = value.strip()
    if not text:
        return None
    if text.startswith("data:image/png;base64,"):
        return decode_png_data_url(text)
    try:
        return base64.b64decode(text, validate=True)
    except Exception as exc:
        raise serializers.ValidationError("签名 Base64 数据无效") from exc


def _pick_signature_text(signatures: dict, dynamic_data: dict, keys: tuple[str, ...]) -> str:
    for k in keys:
        v = signatures.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    for k in keys:
        v = dynamic_data.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


class InspectionSubmitSerializer(serializers.Serializer):
    # 暂不校验 taskNo 格式；实际任务号以 URL / 视图层解析为准
    taskNo = serializers.CharField(max_length=64)
    templateId = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    reportType = serializers.CharField(max_length=64)
    createdAt = serializers.DateTimeField()
    updatedAt = serializers.DateTimeField()
    reportInfo = serializers.DictField(allow_null=True)
    hospitalInfo = serializers.DictField(allow_null=True)
    equipmentInfo = serializers.DictField(allow_null=True)
    instruments = serializers.ListField(child=serializers.DictField(), allow_empty=True)
    testResult = serializers.DictField(allow_null=True)
    dynamicData = serializers.DictField(required=False, allow_null=True, default=dict)
    signatures = serializers.DictField(required=False, allow_null=True, default=dict)
    conclusion = serializers.DictField(required=False, allow_null=True, default=dict)

    def validate(self, attrs):
        allowed_report_types = {"xray_fluoroscopy", "ct_qc"}
        report_type = str(attrs.get("reportType") or "").strip()
        if report_type not in allowed_report_types:
            raise serializers.ValidationError(
                {"reportType": "reportType 必须为 xray_fluoroscopy 或 ct_qc"}
            )
        # 兼容前端传 null：提交阶段按空对象处理，避免 422 中断流程。
        for key in ("reportInfo", "hospitalInfo", "equipmentInfo", "testResult", "conclusion"):
            if attrs.get(key) is None:
                attrs[key] = {}
        if attrs.get("dynamicData") is None:
            attrs["dynamicData"] = {}

        test_result = attrs.get("testResult") or {}
        if test_result.get("isDsaDevice") is False and test_result.get("dsa") not in (None, {}, ""):
            raise serializers.ValidationError(
                {"testResult": "当 isDsaDevice=false 时，dsa 字段必须为 null 或空对象"}
            )
        signatures = attrs.get("signatures") or {}
        dynamic_data = attrs.get("dynamicData") or {}
        author_text = _pick_signature_text(
            signatures,
            dynamic_data,
            ("author", "inspector", "mainInspector", "检测员", "f76"),
        )
        reviewer_text = _pick_signature_text(
            signatures,
            dynamic_data,
            ("reviewer", "checker", "校核员及校核日期", "校核", "f78"),
        )
        approver_text = _pick_signature_text(
            signatures,
            dynamic_data,
            ("approver", "authorizedSignatory", "accompanyingPerson", "受检单位陪同人", "f77"),
        )
        attrs["_author_png"] = decode_png_base64_any(author_text)
        attrs["_reviewer_png"] = decode_png_base64_any(reviewer_text)
        attrs["_approver_png"] = decode_png_base64_any(approver_text)
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
        attrs["_author_png"] = decode_png_base64_any(signatures.get("author"))
        attrs["_reviewer_png"] = decode_png_base64_any(signatures.get("reviewer"))
        attrs["_approver_png"] = decode_png_base64_any(signatures.get("approver"))
        return attrs
