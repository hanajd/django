"""现场记录 hospitalInfo 预填：委托单位最细层级名称与地址向上回退。"""
from __future__ import annotations

from apps.core.models import CommissionOrganization, InspectionCase, LibraryProject

# 与报告回填 _hospital_info_inspected_unit_name / _address 键序一致
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


def build_site_hospital_info_prefill(case: InspectionCase) -> dict:
    """
    现场记录默认 hospitalInfo：同受检单位 + 受检单位名称/地址。
    优先项目关联委托单位的完整路径名称；无委托绑定时回退案件受检单位主数据。
    """
    org = commission_org_for_case(case)
    legacy = case.inspected_organization
    contact = case.primary_contact

    name = ""
    address = ""
    if org is not None:
        name = inspected_unit_name_from_commission_org(org)
        address = inspected_unit_address_from_commission_org(org)
    if not name and legacy is not None:
        name = (legacy.name or "").strip()
    if not address and legacy is not None:
        address = (legacy.address or "").strip()

    hi: dict = {
        "commissionOrgMode": "sameInspection",
        "sameInspection": True,
        "contactPerson": (contact.name if contact else "") or "",
        "contactPhone": (contact.phone if contact else "") or "",
    }
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


def hospital_info_semantic_value(
    hi: dict | None,
    *,
    label: str,
    leaf: str,
) -> object | None:
    """
    按栏位标签从 hospitalInfo 语义键解析预填值（供 export-frontend-json 注入 defaultValue）。
    leaf 为 submitPath 末段（常为 pdfFieldId）。
    """
    if not isinstance(hi, dict):
        return None
    label = str(label or "").strip()
    leaf = str(leaf or "").strip()

    if leaf and not _is_empty_prefill_value(hi.get(leaf)):
        return hi.get(leaf)

    if label in {"委托单位_同受检单位", "同受检单位"}:
        mode = str(hi.get("commissionOrgMode") or "").strip()
        if mode == "sameInspection":
            return True
        if mode == "customCommission":
            return False
        if isinstance(hi.get("sameInspection"), bool):
            return hi.get("sameInspection")
        if leaf and isinstance(hi.get(leaf), bool):
            return hi.get(leaf)
        return True

    if label == "受检单位" or (
        "受检单位" in label and "地址" not in label and "陪同" not in label
    ):
        for k in (*_INSPECTED_NAME_KEYS, leaf):
            if k and not _is_empty_prefill_value(hi.get(k)):
                return hi.get(k)
        return None

    if label == "受检单位地址" or "受检单位地址" in label:
        for k in (*_ADDRESS_KEYS, leaf):
            if k and not _is_empty_prefill_value(hi.get(k)):
                return hi.get(k)
        return None

    if label == "commissionOrgMode" or leaf == "commissionOrgMode":
        mode = str(hi.get("commissionOrgMode") or "").strip()
        if mode:
            return mode
        return "sameInspection"

    return None
