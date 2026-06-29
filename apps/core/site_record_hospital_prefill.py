"""现场记录 hospitalInfo 预填：委托单位最细层级名称与地址向上回退。"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterator, Optional

from apps.core.models import CommissionOrganization, InspectionCase, LibraryProject
from utils.report_fill_helpers import format_commission_contact_combined

# 与报告回填 _hospital_info_inspected_unit_name / _address 键序一致（语义键，非 pdfFieldId）
_INSPECTED_NAME_KEYS = (
    "inspection2",
    "name",
    "inspectedUnit",
    "inspectedOrganization",
    "hospitalName",
    "entityName",
    "inspection",
    "受检单位",
)
_ADDRESS_KEYS = (
    "address",
    "inspectionAddress",
    "unitAddress",
    "hospitalAddress",
    "受检单位地址",
)
_CONTACT_PERSON_KEYS = (
    "contactPerson",
    "联系人",
)
_CONTACT_PHONE_KEYS = (
    "contactPhone",
    "联系电话",
    "联系人电话",
)
_CONTACT_COMBINED_KEYS = (
    "commissionContactPhone",
    "委托单位联系人/电话",
    "委托单位联系人电话",
)


def commission_org_for_project(project: LibraryProject | None) -> CommissionOrganization | None:
    if project is None or not project.commission_org_id:
        return None
    org = getattr(project, "commission_org", None)
    if org is not None:
        return org
    return CommissionOrganization.objects.filter(pk=project.commission_org_id).first()


def commission_org_for_case(case: InspectionCase) -> CommissionOrganization | None:
    return commission_org_for_project(case.library_project)


def inspected_unit_name_from_commission_org(org: CommissionOrganization) -> str:
    """
    委托完整路径名称：自医院至当前绑定层级逐级拼接，无「 · 」等分隔符。
    例：张三医院 · 新院区 · 内科 → 张三医院新院区内科
    """
    parts = [(n.name or "").strip() for n in org.ancestors_chain() if (n.name or "").strip()]
    return "".join(parts)


def inspected_unit_address_from_commission_org(org: CommissionOrganization) -> str:
    """从最细层级向上查找首个非空地址（科室 → 院区 → 医院）。"""
    chain = org.ancestors_chain()
    for node in reversed(chain):
        addr = (node.address or "").strip()
        if addr:
            return addr
    return ""


def contact_from_commission_org(org: CommissionOrganization) -> tuple[str, str]:
    """从最细层级向上查找首个有效联系人（科室 → 院区 → 医院）。"""
    from apps.core.models import CommissionOrgContact

    for node in reversed(org.ancestors_chain()):
        row = (
            CommissionOrgContact.objects.filter(organization=node, is_active=True)
            .order_by("sort_order", "id")
            .first()
        )
        if row is None:
            continue
        name = (row.name or "").strip()
        phone = (row.phone or "").strip()
        if name or phone:
            return name, phone
    return "", ""


def build_site_hospital_info_prefill(case: InspectionCase) -> dict:
    """
    现场记录默认 hospitalInfo：同受检单位 + 受检单位名称/地址/联系人（语义键）。
    优先项目关联委托单位的完整路径名称、地址与联系人；无委托绑定时回退案件受检单位主数据。
    具体 pdfFieldId 槽位在 export-frontend-json 阶段按模板 hospitalInfo 章节栏位标签写入。
    """
    org = commission_org_for_case(case)
    legacy = case.inspected_organization
    contact = case.primary_contact

    name = ""
    address = ""
    contact_name = ""
    contact_phone = ""
    if org is not None:
        name = inspected_unit_name_from_commission_org(org)
        address = inspected_unit_address_from_commission_org(org)
        contact_name, contact_phone = contact_from_commission_org(org)
    if not name and legacy is not None:
        name = (legacy.name or "").strip()
    if not address and legacy is not None:
        address = (legacy.address or "").strip()
    if not contact_name and not contact_phone and contact is not None:
        contact_name = (contact.name or "").strip()
        contact_phone = (contact.phone or "").strip()

    hi: dict = {
        "commissionOrgMode": "sameInspection",
        "sameInspection": True,
    }
    if contact_name:
        for k in _CONTACT_PERSON_KEYS:
            hi[k] = contact_name
    if contact_phone:
        for k in _CONTACT_PHONE_KEYS:
            hi[k] = contact_phone
    combined = format_commission_contact_combined(contact_name, contact_phone)
    if combined:
        for k in _CONTACT_COMBINED_KEYS:
            hi[k] = combined
    if name:
        for k in _INSPECTED_NAME_KEYS:
            hi[k] = name
    if address:
        for k in _ADDRESS_KEYS:
            hi[k] = address
    return hi


def _is_empty_prefill_value(v) -> bool:
    if v is None:
        return True
    if isinstance(v, bool):
        return False
    if isinstance(v, (dict, list)):
        return not v
    return not str(v).strip()


def merge_hospital_info_prefill(existing: dict | None, prefill: dict | None) -> dict:
    """仅补缺：不覆盖用户已填写的 hospitalInfo 键。"""
    out = dict(existing or {})
    for k, v in (prefill or {}).items():
        if _is_empty_prefill_value(v):
            continue
        if k not in out or _is_empty_prefill_value(out.get(k)):
            out[k] = v
    return out


def _norm_label_compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _hospital_field_label_blob(field: dict) -> str:
    tbl = field.get("table") if isinstance(field.get("table"), dict) else {}
    return _norm_label_compact(
        " ".join(
            [
                str(field.get("label") or ""),
                str(field.get("hierarchyKey") or ""),
                str(tbl.get("fieldName") or ""),
            ]
        )
    )


def field_submit_path(field: dict) -> str:
    sp = str(field.get("submitPath") or "").strip()
    if sp:
        return sp
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    return str(src.get("submitPath") or "").strip()


def field_belongs_to_hospital_info_bucket(field: dict) -> bool:
    bucket = str(field.get("submitBucket") or "").strip()
    if bucket == "hospitalInfo":
        return True
    sp = field_submit_path(field)
    return sp.startswith("hospitalInfo.")


def hospital_info_submit_leaf(field: dict) -> str:
    sp = field_submit_path(field)
    if sp.startswith("hospitalInfo."):
        return sp.split(".", 1)[1]
    return str(field.get("pdfFieldId") or field.get("id") or "").strip()


def classify_hospital_info_field_role(field: dict) -> Optional[str]:
    """
    按栏位标签/层级键判定预填语义（不依赖 pdfFieldId 序号）。
    返回：inspected_unit_name | inspected_address | contact_combined | contact_person | contact_phone | commission_same
    """
    if not isinstance(field, dict):
        return None
    blob = _hospital_field_label_blob(field)
    if not blob:
        return None
    if "陪同" in blob:
        return None
    if "委托单位联系人" in blob and "电话" in blob:
        return "contact_combined"
    if blob in _CONTACT_PERSON_KEYS or (
        "联系人" in blob and "电话" not in blob and "委托单位" not in blob
    ):
        return "contact_person"
    if any(k in blob for k in _CONTACT_PHONE_KEYS) or blob.endswith("电话"):
        if "委托单位" not in blob or "联系人电话" in blob:
            return "contact_phone"
    if "受检单位地址" in blob or (blob.endswith("地址") and "受检" in blob):
        return "inspected_address"
    if blob == "受检单位" or ("受检单位" in blob and "地址" not in blob):
        return "inspected_unit_name"
    if blob in ("委托单位_同受检单位", "同受检单位"):
        return "commission_same"
    return None


def canonical_hospital_prefill_value(hi: dict, role: str) -> Any:
    """从 hospitalInfo 语义键解析某类预填值（与模板 f 号无关）。"""
    if not isinstance(hi, dict) or not role:
        return None
    if role == "inspected_unit_name":
        for k in _INSPECTED_NAME_KEYS:
            if not _is_empty_prefill_value(hi.get(k)):
                return hi.get(k)
    elif role == "inspected_address":
        for k in _ADDRESS_KEYS:
            if not _is_empty_prefill_value(hi.get(k)):
                return hi.get(k)
    elif role == "contact_combined":
        for k in _CONTACT_COMBINED_KEYS:
            if not _is_empty_prefill_value(hi.get(k)):
                return hi.get(k)
        name = canonical_hospital_prefill_value(hi, "contact_person")
        phone = canonical_hospital_prefill_value(hi, "contact_phone")
        combined = format_commission_contact_combined(
            str(name or ""), str(phone or "")
        )
        return combined or None
    elif role == "contact_person":
        for k in _CONTACT_PERSON_KEYS:
            if not _is_empty_prefill_value(hi.get(k)):
                return hi.get(k)
    elif role == "contact_phone":
        for k in _CONTACT_PHONE_KEYS:
            if not _is_empty_prefill_value(hi.get(k)):
                return hi.get(k)
    elif role == "commission_same":
        mode = str(hi.get("commissionOrgMode") or "").strip()
        if mode == "sameInspection":
            return True
        if mode == "customCommission":
            return False
        if isinstance(hi.get("sameInspection"), bool):
            return hi.get("sameInspection")
        return True
    return None


def _iter_steps_fields(steps: list) -> Iterator[dict]:
    for step in steps:
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


def iter_hospital_info_template_fields(frontend_obj: dict) -> Iterator[dict]:
    """遍历前端模板中 submitBucket=hospitalInfo 的栏位（含 matrix 单元格）。"""
    if not isinstance(frontend_obj, dict):
        return
    steps = frontend_obj.get("steps")
    if not isinstance(steps, list):
        return
    for field in _iter_steps_fields(steps):
        if field_belongs_to_hospital_info_bucket(field):
            yield field


def enrich_payload_hospital_info_from_frontend_chapter(
    payload: dict,
    frontend_obj: dict,
) -> dict:
    """
    按模板 hospitalInfo 章节栏位标签，将语义预填写入 payload.hospitalInfo 的实际 submitPath 槽位。
    避免硬编码 f6/f7/f8；各模板 pdfFieldId 由栏位定义决定。
    """
    if not isinstance(payload, dict) or not isinstance(frontend_obj, dict):
        return payload
    hi = payload.get("hospitalInfo")
    if not isinstance(hi, dict):
        hi = {}
        payload["hospitalInfo"] = hi
    for field in iter_hospital_info_template_fields(frontend_obj):
        role = classify_hospital_info_field_role(field)
        if not role:
            continue
        val = canonical_hospital_prefill_value(hi, role)
        if _is_empty_prefill_value(val):
            continue
        leaf = hospital_info_submit_leaf(field)
        if leaf and _is_empty_prefill_value(hi.get(leaf)):
            hi[leaf] = val
    return payload


def apply_hospital_info_chapter_default_values(
    steps: list,
    hi: dict,
    coerce_fn: Callable[[dict, Any], Any],
) -> None:
    """在 hospitalInfo 章节栏位上按标签写入 defaultValue（不依赖固定 f 序号）。"""
    if not isinstance(steps, list) or not isinstance(hi, dict):
        return
    for field in _iter_steps_fields(steps):
        if not field_belongs_to_hospital_info_bucket(field):
            continue
        role = classify_hospital_info_field_role(field)
        if not role:
            continue
        raw = canonical_hospital_prefill_value(hi, role)
        if _is_empty_prefill_value(raw):
            continue
        coerced = coerce_fn(field, raw)
        if coerced is None:
            field_type = str(field.get("type") or "").strip().lower()
            if field_type in {
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
        if coerced is not None:
            field["defaultValue"] = coerced


def hospital_info_semantic_value(
    hi: dict | None,
    *,
    label: str,
    leaf: str,
) -> object | None:
    """
    按栏位标签从 hospitalInfo 语义键解析预填值（供 export-frontend-json 注入 defaultValue）。
    leaf 为 submitPath 末段；优先按标签语义匹配，再回退 leaf 槽位（由章节映射写入）。
    """
    if not isinstance(hi, dict):
        return None
    fake_field = {
        "label": str(label or "").strip(),
        "hierarchyKey": str(label or "").strip(),
    }
    role = classify_hospital_info_field_role(fake_field)
    if role:
        val = canonical_hospital_prefill_value(hi, role)
        if not _is_empty_prefill_value(val):
            return val
    leaf = str(leaf or "").strip()
    if leaf and not _is_empty_prefill_value(hi.get(leaf)):
        return hi.get(leaf)
    return None
