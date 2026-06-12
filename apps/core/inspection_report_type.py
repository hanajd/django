"""检测提交 reportType：与任务模板库前端 JSON 及历史 App API 对齐。"""
from __future__ import annotations

import re

from utils.frontend_schema_rule_engine import _infer_report_type

# 与 CharField(max_length=64) 一致；允许 camelCase / snake_case（如 radiationQc、xray_fluoroscopy）
_SUBMIT_REPORT_TYPE_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")

# 任务模板库 _infer_report_type、auxiliary *_frontend.json、编辑器导出常见值
TEMPLATE_LIBRARY_REPORT_TYPES = frozenset(
    {
        "ct",
        "dr",
        "cr",
        "dsa",
        "mammography",
        "xray_fluoroscopy",
        "radiationQc",
    }
)

# 历史提交 API / 文档仍使用的取值（与模板库并存，不强制互转）
LEGACY_SUBMIT_REPORT_TYPES = frozenset(
    {
        "ct_qc",
        "xray_fluoroscopy",
    }
)

KNOWN_SUBMIT_REPORT_TYPES = TEMPLATE_LIBRARY_REPORT_TYPES | LEGACY_SUBMIT_REPORT_TYPES

# 常见同义写法（仅合并明确同义，不把 ct 改成 ct_qc）
_SUBMIT_REPORT_TYPE_SYNONYMS = {
    "xray": "xray_fluoroscopy",
    "fluoroscopy": "xray_fluoroscopy",
    "radiation_qc": "radiationQc",
    "radiationqc": "radiationQc",
}

_DEFAULT_SUBMIT_REPORT_TYPE = "radiationQc"


def is_valid_submit_report_type(value: str | None) -> bool:
    text = str(value or "").strip()
    return bool(text) and bool(_SUBMIT_REPORT_TYPE_PATTERN.match(text))


def _canonical_from_lowercase_key(low: str) -> str | None:
    if low in _SUBMIT_REPORT_TYPE_SYNONYMS:
        return _SUBMIT_REPORT_TYPE_SYNONYMS[low]
    if low in {x.lower() for x in KNOWN_SUBMIT_REPORT_TYPES}:
        for known in KNOWN_SUBMIT_REPORT_TYPES:
            if known.lower() == low:
                return known
    return None


def normalize_submit_report_type(
    value: str | None,
    *,
    template_id: str = "",
    template_name: str = "",
    default: str | None = None,
) -> str:
    """
    解析提交用 reportType：
    - 优先保留任务模板库原值（如 ``ct``、``radiationQc``）；
    - 空值时按 templateId/Name 推断（与 ``_infer_report_type`` 一致）；
    - 兼容历史 API 的 ``ct_qc``、``xray_fluoroscopy`` 及少量同义写法。
    """
    raw = str(value or "").strip()
    if not raw:
        inferred = _infer_report_type(template_id, template_name)
        if is_valid_submit_report_type(inferred):
            return str(inferred).strip()
        for candidate in (default, _DEFAULT_SUBMIT_REPORT_TYPE, "xray_fluoroscopy"):
            text = str(candidate or "").strip()
            if is_valid_submit_report_type(text):
                return text
        return _DEFAULT_SUBMIT_REPORT_TYPE

    if is_valid_submit_report_type(raw):
        canon = _canonical_from_lowercase_key(raw.lower().replace("-", "_"))
        return canon if canon else raw

    low = raw.lower().replace("-", "_")
    canon = _canonical_from_lowercase_key(low)
    if canon:
        return canon

    inferred = _infer_report_type(f"{template_id} {raw}", template_name)
    if is_valid_submit_report_type(inferred):
        return str(inferred).strip()

    known_hint = "、".join(sorted(KNOWN_SUBMIT_REPORT_TYPES))
    raise ValueError(
        f"reportType 无效：{raw!r}；须为任务模板库取值（如 {known_hint}）"
        "，或可由 templateId/模板名推断的简写"
    )


def submit_report_type_help_text() -> str:
    known = "、".join(sorted(KNOWN_SUBMIT_REPORT_TYPES))
    return f"与任务模板库前端 JSON 根级 reportType 一致；常见：{known}"
