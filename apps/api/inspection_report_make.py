"""报告制作：检测提交 → HTMLPDF 模板回填、bindings 合并、字段归一化与 PDF 落库。

与 `inspection_pdf_service` 中的任务解析、现场记录 JSON 合并等解耦，便于维护。
"""
from __future__ import annotations

import base64
import os
import re
from datetime import date, datetime
from difflib import SequenceMatcher
from collections.abc import Sequence
from typing import AbstractSet, Any

from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.api.inspection_submit_placeholder_maps import (
    DEFAULT_SUBMIT_PLACEHOLDER_MAP_ID,
    SUBMIT_PLACEHOLDER_MAPS,
    _parse_source_updated_at,
    build_submit_value_mapping,
    resolve_mapping_rules,
)
from apps.core import htmlpdf_service, pipeline_service
from apps.core.library_file_service import save_library_binary_uploads
from apps.core.models import (
    InspectionCase,
    InspectionSubmission,
    InstrumentCatalog,
    LibraryFile,
    LibraryTask,
)
from utils.pdf_merge import extract_modality_abbr_from_title, merged_device_phrase_for_evaluation
from utils.report_fill_helpers import merge_number_tokens_from_source, split_contact_name_phone

# 报告质控区 steps 常把「检测条件/检测结果」绑到 testResult.test3、field30、testResult2 等 legacy 键；
# 前端 type=number 易写入 0 或占位，若优先于 submitPath 会覆盖现场模板 dynamicData 解析出的正确词条。
_REPORT_DEFER_LEGACY_QC_SUBMIT_PATH = re.compile(
    r"^testResult\.((?:test|field)\d+|testResult\d*)$",
    re.IGNORECASE,
)


def _report_defer_legacy_qc_submit_path(submit_path: str) -> bool:
    s = str(submit_path or "").strip()
    return bool(_REPORT_DEFER_LEGACY_QC_SUBMIT_PATH.match(s))


def _normalize_test_year_yyyy(y: object) -> str:
    """检测日期年份：规范为 4 位数字（yyyy）；无法按整数解析时退回去空白后的原字符串。"""
    if y is None:
        return ""
    ys = str(y).strip()
    if not ys:
        return ""
    if ys.isdigit():
        n = int(ys)
        if 0 <= n <= 9999:
            return f"{n:04d}"
    return ys


# 模板 submitPath 常指向完整 ISO（如 2026-05-11T00:00:00.000），需拆入「检测日期_年」等只收片段的槽位
_DATE_PART_YEAR_KEYS = frozenset({"检测日期_年", "检测年"})
_DATE_PART_MONTH_KEYS = frozenset({"检测日期_月", "检测月"})
_DATE_PART_DAY_KEYS = frozenset({"检测日期_日", "检测日"})


def _parse_loose_datetime_for_submit(val: object) -> datetime | None:
    """解析检测日期：支持 datetime/date、ISO 含 T、仅日期；与 Django parse_datetime 互补。"""
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, date):
        return datetime(val.year, val.month, val.day, 0, 0, 0)
    s = str(val).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    d = parse_datetime(s)
    if isinstance(d, datetime):
        return d
    head = s.split("T", 1)[0].strip()
    try:
        parts = head.split("-")
        if len(parts) >= 3:
            y, m, d2 = int(parts[0]), int(parts[1]), int(parts[2])
            return datetime(y, m, d2, 0, 0, 0)
    except (TypeError, ValueError, OverflowError):
        return None
    return None


def _coerce_iso_datetime_value_for_date_part_key(key: str, val: object) -> object:
    """若语义键为检测日期年/月/日槽，且值为完整 ISO，则只写入对应片段（年为四位 yyyy）。"""
    k = str(key or "").strip()
    if not k:
        return val
    if k not in _DATE_PART_YEAR_KEYS and k not in _DATE_PART_MONTH_KEYS and k not in _DATE_PART_DAY_KEYS:
        return val
    dt = _parse_loose_datetime_for_submit(val)
    if dt is None:
        return val
    if k in _DATE_PART_YEAR_KEYS:
        return f"{dt.year:04d}"
    if k in _DATE_PART_MONTH_KEYS:
        return str(dt.month)
    if k in _DATE_PART_DAY_KEYS:
        return str(dt.day)
    return val


def _coerce_scalar_for_field_date_part_slots(field: dict, raw_sp: object) -> str:
    """submitPath 等直读提交 JSON 的路径：若当前 PDF 格语义为「检测日期_年/月/日」，把 ISO 拆成片段再写入。"""
    if isinstance(raw_sp, bool):
        return str(raw_sp)
    val_out = "" if raw_sp is None else str(raw_sp).strip()
    if not val_out:
        return val_out
    for ck in _field_semantic_candidate_keys(field):
        co = _coerce_iso_datetime_value_for_date_part_key(ck, val_out)
        if co != val_out:
            return str(co)
    return val_out


def _normalize_iso_strings_in_test_date_part_slots(value_mapping: dict) -> None:
    """将已写入 value_mapping 的「年/月/日」槽位上的完整 ISO 字符串规范为数字片段（幂等）。"""
    if not isinstance(value_mapping, dict):
        return
    for key_set, part in (
        (_DATE_PART_YEAR_KEYS, "year"),
        (_DATE_PART_MONTH_KEYS, "month"),
        (_DATE_PART_DAY_KEYS, "day"),
    ):
        for sk in key_set:
            if sk not in value_mapping:
                continue
            cur = value_mapping.get(sk)
            cs = "" if cur is None else str(cur).strip()
            if not cs or len(cs) <= 4 and "-" not in cs and "T" not in cs:
                continue
            dt = _parse_loose_datetime_for_submit(cur)
            if dt is None:
                continue
            if part == "year":
                value_mapping[sk] = f"{dt.year:04d}"
            elif part == "month":
                value_mapping[sk] = str(dt.month)
            else:
                value_mapping[sk] = str(dt.day)


def _hospital_info_inspected_unit_name(hi: dict) -> str:
    """hospitalInfo 中受检侧机构名（无模板语境时的兜底顺序）。

    注意：不同库模板「受检单位」submitPath 可能挂在 inspection2（如 JS-001）或 inspection（如 JS-117）；
    现场 PDF 回填应以模板 submitPath 写入 value_mapping 为准，本函数仅用于派生映射补缺。
    """
    if not isinstance(hi, dict):
        return ""
    for k in (
        "inspection2",
        "name",
        "inspectedUnit",
        "inspectedOrganization",
        "hospitalName",
        "entityName",
        "commissionedUnit",
        "inspection",
    ):
        v = hi.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if v not in (None, "") and not isinstance(v, (dict, list)):
            s = str(v).strip()
            if s:
                return s
    return ""


def _hospital_info_inspected_unit_address(hi: dict) -> str:
    if not isinstance(hi, dict):
        return ""
    for k in ("address", "inspectionAddress", "unitAddress", "hospitalAddress"):
        v = hi.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if v not in (None, "") and not isinstance(v, (dict, list)):
            s = str(v).strip()
            if s:
                return s
    return ""


def _build_submit_value_mapping(source_data: dict, map_id: str | None = None):
    mid = (map_id or "").strip() or DEFAULT_SUBMIT_PLACEHOLDER_MAP_ID
    if mid not in SUBMIT_PLACEHOLDER_MAPS:
        mid = DEFAULT_SUBMIT_PLACEHOLDER_MAP_ID
    return build_submit_value_mapping(source_data, mid)


def _resolve_submitter_display_name(
    inspection_case: InspectionCase | None,
    project,
    task_no: str,
    source_data: dict,
) -> str:
    """主要检测人员等：优先检测提交记录/提交 JSON 文件的提交用户（可追溯现场编制人）。"""
    tn = (task_no or "").strip()

    def _user_disp(u: User | None) -> str:
        if u is None:
            return ""
        fn = (u.get_full_name() or "").strip()
        if fn:
            return fn
        return (u.username or "").strip()

    if inspection_case is not None and project is not None:
        if tn:
            sub = (
                InspectionSubmission.objects.filter(
                    task_no=tn, case_id=inspection_case.pk, project_id=project.pk
                )
                .select_related("created_by")
                .order_by("-submitted_at", "-updated_at", "-id")
                .first()
            )
            if sub is not None:
                s = _user_disp(sub.created_by)
                if s:
                    return s
        lf = (
            LibraryFile.objects.filter(
                category=LibraryFile.CATEGORY_INSPECTION_SUBMIT,
                link_entity=LibraryFile.LINK_ENTITY_INSPECTION_CASE,
                link_object_id=inspection_case.pk,
                projects=project,
            )
            .select_related("created_by")
            .order_by("-created_at", "-id")
            .first()
        )
        if lf is not None:
            s = _user_disp(lf.created_by)
            if s:
                return s

    for k in ("submitterName", "submitter", "inspectorName", "authorName"):
        v = source_data.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    uobj = source_data.get("user") or source_data.get("submittedBy")
    if isinstance(uobj, dict):
        n = (uobj.get("name") or uobj.get("displayName") or uobj.get("username") or "").strip()
        if n:
            return n
    ri = source_data.get("reportInfo")
    if isinstance(ri, dict):
        for k in ("inspector", "author", "preparedBy", "inspectorName"):
            v = ri.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _build_submit_derived_value_mapping(
    source_data: dict,
    project=None,
    task_no: str = "",
    task_obj=None,
    inspection_case=None,
    *,
    manual_device_count: int | None = None,
):
    from apps.api.inspection_pdf_service import _display_project_id, _display_task_no, display_inspected_no_for_fill

    def _s(v):
        return "" if v is None else str(v)

    report_info = source_data.get("reportInfo") or {}
    hospital_info = source_data.get("hospitalInfo") or {}
    equipment_info = source_data.get("equipmentInfo") or {}
    test_result = source_data.get("testResult") or {}
    hospital_name = _hospital_info_inspected_unit_name(hospital_info)
    model = str(equipment_info.get("model") or "")
    device_name = str(equipment_info.get("deviceName") or "")
    year = str(report_info.get("year") or "").strip()
    month = str(report_info.get("month") or "").strip()
    day = str(report_info.get("day") or "").strip()
    if not (year and month and day):
        d = _parse_loose_datetime_for_submit(test_result.get("testDate"))
        if d is not None:
            year, month, day = f"{d.year:04d}", str(d.month), str(d.day)
    if not (year and month and day):
        d = _parse_loose_datetime_for_submit(report_info.get("testDate"))
        if d is not None:
            year, month, day = f"{d.year:04d}", str(d.month), str(d.day)
    if not (year and month and day):
        dt_upd = _parse_source_updated_at(source_data)
        if dt_upd:
            year, month, day = f"{dt_upd.year:04d}", str(dt_upd.month), str(dt_upd.day)
    year_yyyy = _normalize_test_year_yyyy(year)
    month_s = str(month).strip() if month not in (None, "") else ""
    day_s = str(day).strip() if day not in (None, "") else ""
    date_cn_full = ""
    month_z, day_z = month_s, day_s
    if year_yyyy and month_s and day_s:
        try:
            mi, di = int(str(month_s).strip()), int(str(day_s).strip())
            month_z = f"{mi:02d}"
            day_z = f"{di:02d}"
            date_cn_full = f"{year_yyyy}年{month_z}月{day_z}日"
        except (TypeError, ValueError):
            date_cn_full = f"{year_yyyy}年{month_s}月{day_s}日"

    def _test_line(block: dict):
        if not isinstance(block, dict):
            return ""
        return (
            f"{block.get('controlMode') or ''}：{block.get('kv') or ''}kV，{block.get('ma') or ''}mA，\n"
            f"平板探测器尺寸D={block.get('size') or ''}mm \n标准水模"
        )

    kerma_typical = test_result.get("kermaTypical") or {}
    kerma_max_normal = test_result.get("kermaMaxNormal") or {}
    kerma_max_high = test_result.get("kermaMaxHigh") or {}
    high_contrast = test_result.get("highContrast") or {}
    low_contrast = test_result.get("lowContrast") or {}
    screen_kerma = test_result.get("screenKerma") or {}
    abc = test_result.get("abc") or {}
    dsa = test_result.get("dsa") or {}

    def _mv(block: dict, *fallback_keys: str) -> str:
        if not isinstance(block, dict):
            return ""
        if "measuredValue" in block and block.get("measuredValue") not in (None, ""):
            return _s(block.get("measuredValue"))
        for k in fallback_keys:
            if k in block and block.get(k) not in (None, ""):
                return _s(block.get(k))
        return ""

    if manual_device_count is not None:
        try:
            device_count = max(0, int(manual_device_count))
        except (TypeError, ValueError):
            device_count = 0
    else:
        # 单份报告对应一台受检设备，默认台数为 1；多案合成导出由调用方传入 manual_device_count。
        device_count = 1

    submitter_name = _resolve_submitter_display_name(inspection_case, project, task_no, source_data)
    if not submitter_name and project is not None:
        names = (
            User.objects.filter(site_records__case__library_project=project)
            .order_by("username")
            .values_list("username", flat=True)
            .distinct()
        )
        submitter_name = "、".join([n for n in names if n])
    testman = submitter_name

    project_code = _display_project_id(project)
    inspected_display = display_inspected_no_for_fill(inspection_case, project, task_no)

    is_report = bool(
        task_obj is not None and getattr(task_obj, "output_target", None) == LibraryTask.OUTPUT_REPORT
    )
    report_template_name = (task_obj.name or "").strip() if is_report else ""
    project_title = ""
    if is_report and (hospital_name or report_template_name):
        project_title = f"{hospital_name}{report_template_name}"

    n_eval = max(1, int(device_count)) if device_count else 1
    title_for_abbr = report_template_name or device_name or model or ""
    ab = extract_modality_abbr_from_title(title_for_abbr) or (device_name or model or "设备").strip()[:32] or "设备"
    dev_eval = merged_device_phrase_for_evaluation([ab] * n_eval, n_eval)
    org_for_eval = (hospital_name or "").strip()
    assessment = (
        f"应委托方要求，依据相关检测标准，对{org_for_eval}{dev_eval}进行了质量控制检测，结果表明：\n"
        f"所检设备的质量控制相关参数均符合相关标准要求。"
    )

    contact_name, contact_phone = split_contact_name_phone(
        str(hospital_info.get("contactPerson") or ""),
        str(hospital_info.get("contactPhone") or ""),
    )
    ccp_combined = str(hospital_info.get("commissionContactPhone") or "").strip()
    if ccp_combined:
        if not contact_phone:
            n2, p2 = split_contact_name_phone(ccp_combined, "")
            contact_name = (contact_name or n2).strip()
            contact_phone = (contact_phone or p2).strip()
        elif not contact_name:
            n2, p2 = split_contact_name_phone(ccp_combined, contact_phone)
            contact_name = (n2 or contact_name).strip()
    if not contact_name and not contact_phone:
        dd = source_data.get("dynamicData")
        if isinstance(dd, dict):
            for dk, dv in dd.items():
                sk = str(dk or "")
                if "委托单位联系人" in sk and "电话" in sk:
                    contact_name, contact_phone = split_contact_name_phone(str(dv or "").strip(), "")
                    break

    contact_name = (contact_name or str(hospital_info.get("contactPerson") or "")).strip()
    contact_phone = (contact_phone or str(hospital_info.get("contactPhone") or "")).strip()
    addr = _hospital_info_inspected_unit_address(hospital_info)
    rated = str(equipment_info.get("ratedParams") or "")
    mfr = str(equipment_info.get("manufacturer") or "")
    serial = str(equipment_info.get("serialNo") or "")
    loc = str(equipment_info.get("location") or "")

    derived: dict = {
        "projectId": project_code,
        "entrustNo": project_code,
        "委托编号": project_code,
        "commissionNo": project_code,
        "taskNo": inspected_display,
        "inspectedNo": inspected_display,
        "受检编号": inspected_display,
        "testnumber": inspected_display,
        "deviceCount": str(device_count),
        "testman": testman,
        "assessment": assessment,
        "testDate": date_cn_full,
        "kerma_test": _test_line(kerma_typical),
        "kermaMax_test": _test_line({"controlMode": "最大比释动能", **(kerma_max_normal if isinstance(kerma_max_normal, dict) else {})}),
        "kermaMaxNormal_test": _test_line(kerma_max_normal),
        "kermaMaxHigh_test": _test_line(kerma_max_high),
        "highContrast_test": _test_line(high_contrast),
        "lowContrast_test": _test_line(low_contrast),
        "screenKerma_test": _test_line(screen_kerma),
        "abc_test": _test_line(abc),
        "dsa_doseFirst_test": _test_line((dsa.get("doseFirst") or {}) if isinstance(dsa, dict) else {}),
        "dsa_doseSecond_test": _test_line((dsa.get("doseSecond") or {}) if isinstance(dsa, dict) else {}),
        "dsa_test": _test_line(dsa if isinstance(dsa, dict) else {}),
        "kerma_result": _s(kerma_typical.get("calcValue")) if isinstance(kerma_typical, dict) and kerma_typical.get("calcValue") not in (None, "") else _mv(kerma_typical, "measuredValue", "result"),
        "kermaMaxNormal_reult": _s(kerma_max_normal.get("calcValue")) if isinstance(kerma_max_normal, dict) and kerma_max_normal.get("calcValue") not in (None, "") else _mv(kerma_max_normal, "measuredValue", "result"),
        "kermaMaxHigh_result": _s(kerma_max_high.get("calcValue")) if isinstance(kerma_max_high, dict) and kerma_max_high.get("calcValue") not in (None, "") else _mv(kerma_max_high, "measuredValue", "result"),
        "abc_result": _s(abc.get("measuredValue", "")) if isinstance(abc, dict) else "",
        # *_result 按你的要求优先使用 calcValue（缺失时回退 measuredValue/result）
        "hightContrast_result": _s(high_contrast.get("calcValue")) if isinstance(high_contrast, dict) and high_contrast.get("calcValue") not in (None, "") else _mv(high_contrast, "measuredValue", "result"),
        "lowContrast_result": _s(low_contrast.get("calcValue")) if isinstance(low_contrast, dict) and low_contrast.get("calcValue") not in (None, "") else _mv(low_contrast, "measuredValue", "result"),
        "screenKerma_result": _s(screen_kerma.get("calcValue")) if isinstance(screen_kerma, dict) and screen_kerma.get("calcValue") not in (None, "") else _mv(screen_kerma, "measuredValue", "result"),
    }

    # 与 HTMLPDF 模板常见中文 placeholder/id 对齐（原 value_mapping 多为英文键，字段名优先匹配会落空）
    derived["受检单位"] = hospital_name
    derived["受检单位名称"] = hospital_name
    derived["受检单位地址"] = addr
    derived["联系人"] = contact_name
    derived["联系电话"] = contact_phone
    derived["联系人电话"] = contact_phone
    derived["受检设备台数"] = f"{device_count}台"
    derived["设备名称"] = device_name
    derived["设备型号"] = model
    derived["额定参数"] = rated
    derived["生产厂家"] = mfr
    derived["设备编号"] = serial
    derived["设备所在场所"] = loc
    derived["主要检测人员"] = submitter_name
    derived["评价"] = assessment
    if year_yyyy:
        derived["检测年"] = year_yyyy
        derived["检测日期_年"] = year_yyyy
    if year_yyyy and month_s and day_s:
        derived["检测日期"] = date_cn_full or f"{year_yyyy}年{month_s}月{day_s}日"
        derived["检测月"] = month_z
        derived["检测日"] = day_z
        derived["检测日期_月"] = month_z
        derived["检测日期_日"] = day_z

    if project_title:
        derived["项目名称"] = project_title
        derived["projectTitle"] = project_title

    # 委托单位：自定义委托且正文有名称则用该名称；否则（含未搜到委托单位）回退为受检单位名称
    _mode = str(hospital_info.get("commissionOrgMode") or "").strip()
    _corg = ""
    if _mode == "customCommission":
        _corg = str(
            hospital_info.get("commissionOrganization")
            or hospital_info.get("commissionName")
            or hospital_info.get("entrustOrganization")
            or hospital_info.get("commission")
            or ""
        ).strip()
    if not _corg:
        _corg = hospital_name.strip() if hospital_name else ""
    if _corg:
        derived["委托单位名称"] = _corg
        derived["委托单位_委托单位名称"] = _corg
        derived["commissionOrganization"] = _corg

    if is_report:
        now = timezone.localtime()
        ry, rm, rd = str(now.year), str(now.month), str(now.day)
        derived["报告日期"] = f"{ry}年{rm}月{rd}日"
        derived["报告年"] = ry
        derived["报告月"] = rm
        derived["报告日"] = rd
        # 签发日期：与报告生成日一致（模板常见「签发日期」与「报告日期」分列）
        derived["签发日期"] = derived["报告日期"]
        derived["签发年"] = ry
        derived["签发月"] = rm
        derived["签发日"] = rd
        derived["报告签发日期"] = derived["报告日期"]

    return derived


def _build_template_binding_value_mapping(source_data: dict, bindings: dict[str, object]) -> dict[str, object]:
    if not isinstance(bindings, dict):
        return {}
    rules = bindings.get("value_rules")
    if not isinstance(rules, list):
        return {}
    normalized_rules = []
    for row in rules:
        if not isinstance(row, dict):
            continue
        key = row.get("key") or row.get("id") or row.get("fieldId") or row.get("placeholder")
        if not key:
            continue
        normalized = {"key": key}
        for k, v in row.items():
            if k in ("key", "id", "fieldId", "placeholder"):
                continue
            normalized[k] = v
        normalized_rules.append(normalized)
    return resolve_mapping_rules(source_data, normalized_rules)


def _fill_template_fields_with_submit(
    source_data: dict,
    template_fields: list,
    map_id: str | None = None,
    project=None,
    task_no: str = "",
    bindings: dict[str, object] | None = None,
    task_obj=None,
    inspection_case=None,
    *,
    manual_device_count: int | None = None,
    report_template_steps: list | None = None,
    template_constants: dict | None = None,
    template_enums: dict | None = None,
    template_lookup_tables: dict | None = None,
    ordered_submit_payloads: Sequence[dict] | None = None,
):
    # 允许通过环境变量强制使用旧版回填逻辑，便于问题回滚定位。
    if str(os.environ.get("INSPECTION_FILL_USE_LEGACY", "0")).strip().lower() in {"1", "true", "yes", "on"}:
        return _fill_template_fields_with_submit_legacy(
            source_data,
            template_fields,
            map_id=map_id,
            project=project,
            task_no=task_no,
            bindings=bindings,
            task_obj=task_obj,
            inspection_case=inspection_case,
            manual_device_count=manual_device_count,
        )
    return _fill_template_fields_with_submit_enhanced(
        source_data,
        template_fields,
        map_id=map_id,
        project=project,
        task_no=task_no,
        bindings=bindings,
        task_obj=task_obj,
        inspection_case=inspection_case,
        manual_device_count=manual_device_count,
        report_template_steps=report_template_steps,
        template_constants=template_constants,
        template_enums=template_enums,
        template_lookup_tables=template_lookup_tables,
        ordered_submit_payloads=ordered_submit_payloads,
    )


def _fill_template_fields_with_submit_legacy(
    source_data: dict,
    template_fields: list,
    map_id: str | None = None,
    project=None,
    task_no: str = "",
    bindings: dict[str, object] | None = None,
    task_obj=None,
    inspection_case=None,
    *,
    manual_device_count: int | None = None,
):
    """
    旧版回填逻辑（保留原行为）：
    - 文本/勾选仅按 field.id 或 field.placeholder 取值
    - 签名仅按 signature_map[field_key]，回退 author/reviewer/approver
    """
    signatures = source_data.get("signatures") or {}
    value_mapping = _build_submit_value_mapping(source_data, map_id)
    binding_map = _build_template_binding_value_mapping(source_data, bindings or {})
    if binding_map:
        value_mapping.update(binding_map)
    if task_obj is not None and project is not None:
        merged_site_sem: dict = {}
        for _st, parsed in _iter_parsed_templates_for_backfill(task_no, project, task_obj):
            steps = _parsed_template_steps_list(parsed)
            flat_schema = _flatten_unified_form_steps_to_fields(steps)
            if flat_schema:
                _apply_template_field_sources_to_mapping(value_mapping, source_data, flat_schema)
            _inject_step_section_qc_key_aliases(value_mapping, steps)
            site_fields = parsed.get("fields") or []
            if isinstance(site_fields, list):
                _apply_template_field_sources_to_mapping(value_mapping, source_data, site_fields)
            _merge_mapping_fill_empty(merged_site_sem, _build_site_template_dynamic_semantic_mapping(parsed, source_data))
        _merge_site_dynamic_semantics_into_value_mapping(value_mapping, merged_site_sem)
    # 提交内嵌 steps（若有）再补缺；dynamicData 须先经现场记录模板解释 pdfFieldId，报告侧仅按占位符词条匹配
    _merge_mapping_fill_empty(value_mapping, _build_dynamic_data_semantic_mapping(source_data))
    submit_steps_legacy = source_data.get("steps")
    if isinstance(submit_steps_legacy, list):
        _inject_step_section_qc_key_aliases(value_mapping, submit_steps_legacy)
    value_mapping.update(
        _build_submit_derived_value_mapping(
            source_data,
            project=project,
            task_no=task_no,
            task_obj=task_obj,
            inspection_case=inspection_case,
            manual_device_count=manual_device_count,
        )
    )
    _apply_template_field_sources_to_mapping(
        value_mapping,
        source_data,
        template_fields,
        overwrite=True,
        skip_report_legacy_qc_submit_path=(
            task_obj is not None
            and getattr(task_obj, "output_target", None) == LibraryTask.OUTPUT_REPORT
        ),
    )
    _inject_hospital_equipment_cn_aliases(value_mapping, source_data)
    _inject_radio_enum_slug_checkbox_aliases(value_mapping, source_data)
    _inject_dose_rate_unit_mutex_pdf_aliases(
        value_mapping,
        source_data,
        extra_triplets=_iter_dose_rate_unit_mutex_triplets_from_steps(
            _merged_site_steps_for_instrument_backfill(task_no, project, task_obj)
        ),
    )
    _inject_unified_yesno_to_library_dsa_artifact_pdf_aliases(value_mapping, source_data)
    _inject_kerma_max_high_dose_mode_pdf_aliases(value_mapping, source_data)
    _inject_environment_location_backfill_aliases(value_mapping, source_data)
    _inject_abc_circled_reading_aliases(value_mapping, source_data)
    _inject_unified_template_brightness_submitpath_aliases(value_mapping, source_data)
    _merge_mapping_fill_empty(
        value_mapping,
        _build_instrument_text_aliases_from_submit(
            source_data,
            _merged_site_steps_for_instrument_backfill(task_no, project, task_obj),
            task_obj=task_obj,
        ),
    )
    _normalize_iso_strings_in_test_date_part_slots(value_mapping)
    signature_map = {}
    if isinstance(bindings, dict):
        raw_sig_map = bindings.get("signature_map")
        if isinstance(raw_sig_map, dict):
            signature_map = raw_sig_map

    for field in template_fields:
        if not isinstance(field, dict):
            continue
        field_key = field.get("id") or field.get("placeholder")
        field_type = (field.get("fieldType") or "").lower()
        if field_type == "text":
            field["content"] = value_mapping.get(field_key, "")
            continue
        if field_type == "check":
            raw = value_mapping.get(field_key, False)
            if isinstance(raw, bool):
                field["checked"] = raw
            elif raw in (0, 1):
                field["checked"] = bool(raw)
            else:
                field["checked"] = _coerce_picked_value_for_pdf_checkbox(field, raw)
            continue
        if field_type == "image":
            sig_key = signature_map.get(field_key) if isinstance(signature_map, dict) else None
            if not isinstance(sig_key, str) or not sig_key:
                sig_key = field_key if field_key in ("author", "reviewer", "approver") else ""
            if sig_key:
                field["imageData"] = signatures.get(sig_key) or ""
    return template_fields


def _build_field_to_pdf_reverse_index(bindings: dict[str, object] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(bindings, dict):
        return out
    raw_field_to_pdf = bindings.get("field_to_pdf")
    if not isinstance(raw_field_to_pdf, list):
        return out
    for row in raw_field_to_pdf:
        if not isinstance(row, dict):
            continue
        field_id = str(row.get("fieldId") or "").strip()
        if not field_id:
            continue
        # 不按 pdfFieldId 建反查：多模板/多份 JSON 间 pdfFieldId 不稳定，报告回填以 placeholder/title 对齐
        for key in (
            str(row.get("placeholder") or "").strip(),
            str(row.get("title") or "").strip(),
        ):
            if key:
                out[key] = field_id
    return out


def _field_semantic_candidate_keys(field: dict) -> list[str]:
    """
    报告回填优先按「输入框语义名」匹配：id / placeholder / title / originalPlaceholder，
    以及顶层 label、source 上的 placeholder/title/label（与 HTMLPDF / 统一表单导出一致）。
    pdfFieldId 仅作定位兜底，见 _pick_value_for_field。
    """
    raw = [
        str(field.get("id") or "").strip(),
        str(field.get("placeholder") or "").strip(),
        str(field.get("title") or "").strip(),
        str(field.get("label") or "").strip(),
        str(field.get("originalPlaceholder") or "").strip(),
    ]
    src = field.get("source")
    if isinstance(src, dict):
        for attr in ("placeholder", "title", "label", "bindKey", "key"):
            v = src.get(attr)
            if isinstance(v, str) and v.strip():
                raw.append(v.strip())
    seen: set[str] = set()
    out: list[str] = []
    for k in raw:
        if not k:
            continue
        collapsed = k.replace("\u3000", " ").strip()
        variants = [k, collapsed, collapsed.rstrip("：:").strip()]
        for v in variants:
            if v and v not in seen:
                seen.add(v)
                out.append(v)
    return out


# DSA/对比灵敏度等多行表：占位符 id 仅差「0.4mm / 7mm / 0.2mm」等厚度时，模糊匹配会串到相邻行。
_MM_THICKNESS_IN_SEMANTIC_KEY_RE = re.compile(r"\d+(?:\.\d+)?mm", re.IGNORECASE)


def _mm_thickness_tokens_in_semantic_key(s: str) -> frozenset[str]:
    if not s:
        return frozenset()
    return frozenset(m.group(0).lower() for m in _MM_THICKNESS_IN_SEMANTIC_KEY_RE.finditer(str(s)))


def _field_mm_thickness_tokens_for_qc_disambiguation(field: dict) -> frozenset[str]:
    """从控件语义名（通常取长 id）提取厚度标记集合；无标记时返回空集表示不做厚度约束。"""
    cands = [k for k in _field_semantic_candidate_keys(field) if k]
    if not cands:
        return frozenset()
    blob = max(cands, key=len)
    toks = _mm_thickness_tokens_in_semantic_key(blob)
    if toks:
        return toks
    return _mm_thickness_tokens_in_semantic_key(" ".join(cands))


def _value_is_field_label_echo(field: dict, val: object) -> bool:
    """
    库模板里常见「content / defaultValue = 占位符全文」；若当作 dynamicData 缺省写入 value_mapping，
    会把输入框名称填进报告。真值不应与当前控件语义名逐字相同。
    """
    if val is None or isinstance(val, (dict, list)):
        return False
    s = str(val).strip()
    if not s:
        return False
    for k in _field_semantic_candidate_keys(field):
        if k and s == str(k).strip():
            return True
    return False


def _value_looks_like_unresolved_qc_domain_id(val: object) -> bool:
    """
    dynamicData / submitPath 偶发把「整段 PDF 域 id」当默认值写回，检测条件格会原样进报告。
    术者位防护区 id 很长且含多段数字，不得当作有效测量或条件正文。
    """
    if val is None or isinstance(val, (dict, list, bool)):
        return False
    s = str(val).strip()
    if len(s) < 28:
        return False
    if "透视防护区检测平面上周围剂量当量率" not in s:
        return False
    if s.endswith("_检测条件") or s.endswith("_检测结果"):
        return True
    if "_术者位" in s and ("_检测条件" in s or "_检测结果" in s):
        return True
    return False


def _field_candidate_keys(field: dict) -> list[str]:
    """含 pdfFieldId，用于签名等需在语义名之后仍可按内部编号命中的场景。"""
    sem = _field_semantic_candidate_keys(field)
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    pid = str(field.get("pdfFieldId") or src.get("pdfFieldId") or "").strip()
    if pid and pid not in sem:
        return sem + [pid]
    return sem


_CIRCLED_NUMBER_RE = re.compile(r"[\u2460-\u2473]")


def _field_semantic_text_has_circled_number(field: dict) -> bool:
    """
    字段语义名中含 ①②③…⑳ 时，不得走「检测条件/检测结果」数字位合并：
    占位符里的 20mmAl、C1、m^2 等会与单次测量值冲突，导致格子看似未填或错乱。
    """
    for k in _field_candidate_keys(field):
        if k and _CIRCLED_NUMBER_RE.search(str(k)):
            return True
    return False


def _is_single_item_verdict_text_field(field: dict) -> bool:
    """单项判定格：值应在制作报告时由标准+检测结果计算，不用现场记录 JSON 直接回填。"""
    if str(field.get("type") or "").lower() == "verdict":
        return True
    if str(field.get("rule") or "").strip():
        return True
    for k in _field_candidate_keys(field):
        if "单项判定" in str(k or ""):
            return True
    return False


def _report_verdict_field_content_is_fail(s: str) -> bool:
    """单项判定最终文案是否视为不合格（与 verdict 默认 failLabel 一致）。"""
    t = (s or "").strip()
    if not t:
        return False
    return "不合格" in t


def _report_template_field_shows_fail_verdict(field: dict) -> bool:
    if not isinstance(field, dict) or not _is_single_item_verdict_text_field(field):
        return False
    return _report_verdict_field_content_is_fail(str(field.get("content") or ""))


def _patch_report_evaluation_field_after_verdicts(template_fields: list | None) -> None:
    """
    「评价」首段与合成报告一致；若任意单项判定为不合格，第二句改为提示核对/复测。
    须在单项判定第二遍推算完成后调用。
    """
    if not isinstance(template_fields, list):
        return
    ok_tail = "所检设备的质量控制相关参数均符合相关标准要求。"
    fail_tail = "所检设备的质量控制相关参数有部分不合格，请仔细核对或重新检测。"
    flat: list = []
    _walk_template_field_dicts(template_fields, flat)
    for f in flat:
        if not isinstance(f, dict):
            continue
        if (f.get("fieldType") or "").lower() != "text":
            continue
        keys = _field_semantic_candidate_keys(f)
        if not any((k or "").strip() == "评价" for k in keys):
            continue
        cur = str(f.get("content") or "")
        if fail_tail in cur or ok_tail not in cur:
            continue
        f["content"] = cur.replace(ok_tail, fail_tail, 1)


def _find_template_field_by_semantic_key(template_fields: list, key: str):
    if not key or not isinstance(template_fields, list):
        return None
    want = str(key or "").strip()
    if not want:
        return None
    flat: list = []
    _walk_template_field_dicts(template_fields, flat)
    for f in flat:
        if not isinstance(f, dict):
            continue
        for k in ("placeholder", "id", "title", "originalPlaceholder"):
            if str(f.get(k) or "").strip() == want:
                return f
        src = f.get("source")
        if isinstance(src, dict):
            for sk in ("placeholder", "title", "label", "bindKey", "key"):
                if str(src.get(sk) or "").strip() == want:
                    return f
    return None


def _nested_get_for_submit(data: object, path_dots: str):
    """按 `a.b.c` 从提交 JSON 嵌套 dict 取值（用于 field.source.submitPath）。"""
    parts = [p for p in str(path_dots or "").split(".") if p]
    if not parts:
        return None
    cur: object = data
    for p in parts:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _nested_get_for_submit_with_rated_fallback(data: object, path_dots: str):
    """
    与 _nested_get_for_submit 相同，但当路径为 testResult.kv / testResult.ma 且取值为空时，
    回退到 equipmentInfo.kv / equipmentInfo.ma（统一表单 schema 常把额定参数挂在 testResult，实际提交在 equipmentInfo）。
    """
    val = _nested_get_for_submit(data, path_dots)
    if val not in (None, ""):
        return val
    sp = str(path_dots or "").strip()
    if sp == "testResult.kv":
        return _nested_get_for_submit(data, "equipmentInfo.kv")
    if sp == "testResult.ma":
        return _nested_get_for_submit(data, "equipmentInfo.ma")
    return val


def _inject_submit_dot_path_aliases(value_mapping: dict, source_data: dict, max_depth: int = 10) -> None:
    """
    将 reportInfo / hospitalInfo / equipmentInfo / testResult / conclusion 展成点号键，
    与模板里 `hospitalInfo.name` 等 field id 对齐；不覆盖已有非空值。
    """

    def walk(obj: object, prefix: str, depth: int) -> None:
        if depth <= 0 or not isinstance(obj, dict):
            return
        for k, v in obj.items():
            nk = str(k)
            path = f"{prefix}.{nk}" if prefix else nk
            if isinstance(v, dict) and v:
                walk(v, path, depth - 1)
                continue
            if isinstance(v, list):
                continue
            if v in (None, ""):
                continue
            if value_mapping.get(path) not in (None, ""):
                continue
            value_mapping[path] = v

    for root in ("reportInfo", "hospitalInfo", "equipmentInfo", "testResult", "conclusion"):
        block = source_data.get(root)
        if isinstance(block, dict) and block:
            walk(block, root, max_depth)


def _apply_template_field_sources_to_mapping(
    value_mapping: dict,
    source_data: dict,
    fields: list,
    *,
    overwrite: bool = False,
    skip_report_legacy_qc_submit_path: bool = False,
) -> None:
    """
    根据模板 fields 上的 source.submitPath，把 **真实提交 JSON** 中的值写到各输入框语义键（id/placeholder/title）。
    用于现场记录模板与报告模板之间的字段对齐：同源 submitPath → 多份模板可共用取值逻辑。
    overwrite=True 时在键上强制采用提交值（用于在派生字段之后仍以 submit JSON 为准）。
    递归处理嵌套 fields/children（表格内单元格）。
    skip_report_legacy_qc_submit_path：报告任务下跳过 testResult.test3 / field30 等 legacy 质控键，
    避免把数字占位写入词条，压过现场模板 dynamicData 解析结果（见 _report_defer_legacy_qc_submit_path）。
    """
    flat: list = []
    _walk_template_field_dicts(fields, flat)
    for f in flat:
        if not isinstance(f, dict):
            continue
        if _is_single_item_verdict_text_field(f):
            continue
        src = f.get("source")
        if not isinstance(src, dict):
            continue
        sp = str(src.get("submitPath") or "").strip()
        if not sp:
            continue
        if skip_report_legacy_qc_submit_path and _report_defer_legacy_qc_submit_path(sp):
            continue
        val = _nested_get_for_submit_with_rated_fallback(source_data, sp)
        if val is None or val == "":
            continue
        if isinstance(val, (dict, list)):
            continue
        for key in _field_semantic_candidate_keys(f):
            if not key:
                continue
            coerced = _coerce_iso_datetime_value_for_date_part_key(key, val)
            if not overwrite and value_mapping.get(key) not in (None, ""):
                continue
            value_mapping[key] = coerced
        pid = _field_pdf_id(f)
        if pid:
            coerced_pid = _coerce_iso_datetime_value_for_date_part_key(pid, val)
            if overwrite or value_mapping.get(pid) in (None, ""):
                value_mapping[pid] = coerced_pid


def _walk_template_field_dicts(fields: list | None, acc: list) -> None:
    """递归展开模板 fields（含表格/分组子 fields）。"""
    if not isinstance(fields, list):
        return
    for f in fields:
        if not isinstance(f, dict):
            continue
        acc.append(f)
        nested = f.get("fields") or f.get("children")
        if isinstance(nested, list):
            _walk_template_field_dicts(nested, acc)


def _flatten_unified_form_steps_to_fields_deep(steps: list | None) -> list:
    """
    递归展开 steps 下全部控件（含 section 内嵌套 fields/children、matrix 单元格子树）。
    `_iter_schema_fields_from_steps` 仅一层 section.fields，会漏掉分组内 instrument_select 等深层控件。
    """
    acc: list = []
    if not isinstance(steps, list):
        return acc
    for step in steps:
        if not isinstance(step, dict):
            continue
        _walk_template_field_dicts(step.get("fields") if isinstance(step.get("fields"), list) else None, acc)
        sections = step.get("sections")
        if not isinstance(sections, list):
            continue
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            _walk_template_field_dicts(sec.get("fields") if isinstance(sec.get("fields"), list) else None, acc)
            m = sec.get("matrix") if isinstance(sec.get("matrix"), dict) else {}
            _walk_template_field_dicts(
                m.get("headerFields") if isinstance(m.get("headerFields"), list) else None, acc
            )
            for row in m.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                cells = row.get("cells") if isinstance(row.get("cells"), dict) else {}
                for cell in cells.values():
                    if isinstance(cell, dict):
                        _walk_template_field_dicts([cell], acc)
    return acc


def _field_pdf_id(field: dict) -> str:
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    return str(src.get("pdfFieldId") or field.get("pdfFieldId") or "").strip()


def _attach_step_schema_to_pdf_fields(fields: list | None, steps: list | None) -> None:
    """
    统一模板里 submitPath 在 steps 的字段上，而 pdf.fields 只有 id/rect/pdfFieldId。
    按 pdfFieldId 把 steps 里对应控件的 source（及可选判定文案）挂到坐标行上，供回填与按域精确取值。
    """
    if not isinstance(fields, list) or not isinstance(steps, list):
        return
    by_pid: dict[str, dict] = {}
    for fld in _iter_schema_fields_from_steps(steps):
        pid = _field_pdf_id(fld)
        if pid:
            by_pid[pid] = fld
    flat: list = []
    _walk_template_field_dicts(fields, flat)
    for box in flat:
        if not isinstance(box, dict):
            continue
        pid = _field_pdf_id(box)
        if not pid or pid not in by_pid:
            continue
        sch = by_pid[pid]
        src = sch.get("source")
        if isinstance(src, dict) and src:
            prev = box.get("source")
            merged = dict(prev) if isinstance(prev, dict) else {}
            for k, v in src.items():
                if v not in (None, ""):
                    merged[k] = v
            box["source"] = merged
        for jc in ("judgmentCriterionText", "judgmentCriterion", "criterionText", "判定标准"):
            t = str(sch.get(jc) or "").strip()
            if t and not str(box.get(jc) or "").strip():
                box[jc] = t
        for ek in ("rule", "passLabel", "failLabel", "dependsOn", "formula", "type", "label"):
            ev = sch.get(ek)
            if ev in (None, ""):
                continue
            if isinstance(ev, list) and not ev:
                continue
            if box.get(ek) in (None, ""):
                box[ek] = ev


def _find_template_field_by_pdf_field_id(template_fields: list | None, pid: str):
    if not pid or not isinstance(template_fields, list):
        return None
    flat: list = []
    _walk_template_field_dicts(template_fields, flat)
    for f in flat:
        if isinstance(f, dict) and _field_pdf_id(f) == pid:
            return f
    return None


def _peer_result_pdf_field_id_for_verdict(verdict_field_pid: str, steps: list | None) -> str:
    """
    在 steps 文档序中，从「单项判定」控件向前找最近一个「检测结果/计算结果/报出值」，
    解决同模板内重复 id（如两格均叫 高对比度分辨力_检测结果）时不能靠语义键唯一定位的问题。
    """
    if not verdict_field_pid or not isinstance(steps, list):
        return ""
    ordered = list(_iter_schema_fields_from_steps(steps))
    idx = None
    for i, fld in enumerate(ordered):
        if _field_pdf_id(fld) == verdict_field_pid:
            idx = i
            break
    if idx is None or idx < 1:
        return ""
    for j in range(idx - 1, -1, -1):
        lab = str(ordered[j].get("label") or "").strip()
        if lab in ("检测结果", "计算结果", "报出值"):
            return _field_pdf_id(ordered[j])
    return ""


def _format_numeric_criterion_fragment(v: float | int) -> str:
    if isinstance(v, float) and abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return str(v).rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def _inject_verdict_criteria_from_form_constants(value_mapping: dict, constants: dict | None) -> None:
    """
    从 unified 模板 formSchema.constants 注入常见质控项的判定标准句（仅补缺），
    供单项判定推算「合格/不合格」。不同医院模板可继续用字段 judgmentCriterionText 覆盖。
    """
    if not isinstance(value_mapping, dict) or not isinstance(constants, dict):
        return
    rules: list[tuple[str, tuple[str, ...], str]] = [
        ("highContrastResolutionLimit", ("高对比度分辨力",), "≥{v}"),
        ("imageUniformityLimit", ("图像均匀性",), "≤{v}"),
        ("distanceErrorLimit", ("测距误差",), "≤{v}"),
        ("kapDeviationLimit", ("KAP指示偏离",), "≤{v}"),
        (
            "typicalAirKermaMgyMinLimit",
            ("透视受检者入射体表空气比释动能率典型值",),
            "<={v}",
        ),
        (
            "airKermaMaxOrdinaryMgyMinLimit",
            ("透视受检者入射体表空气比释动能率最大值_普通剂量模式",),
            "<={v}",
        ),
        (
            "airKermaMaxHighDoseMgyMinLimit",
            ("透视受检者入射体表空气比释动能率最大值_高剂量模式",),
            "<={v}",
        ),
    ]
    lcm = constants.get("lowContrastResolutionMaxMm")
    if lcm not in (None, ""):
        rules.insert(1, ("lowContrastResolutionMaxMm", ("低对比度分辨力",), "<={v}"))
    else:
        rules.insert(1, ("lowContrastResolutionLimit", ("低对比度分辨力",), "≥{v}"))
    for ck, prefixes, fmt in rules:
        raw = constants.get(ck)
        if raw in (None, ""):
            continue
        try:
            num = float(raw)
        except (TypeError, ValueError):
            continue
        frag = _format_numeric_criterion_fragment(num)
        text = fmt.format(v=frag)
        for p in prefixes:
            key = f"{p}_判定标准"
            if value_mapping.get(key) in (None, ""):
                value_mapping[key] = text


def _inject_default_report_row_verdict_criteria(value_mapping: dict) -> None:
    """
    模板未带套表「判定标准」列时的行级缺省（仅补缺）。
    可与 formSchema.constants.typicalAirKermaMgyMinLimit 等覆盖配合使用。
    """
    if not isinstance(value_mapping, dict):
        return
    for row_prefix, crit in (
        ("透视受检者入射体表空气比释动能率典型值", "<=25.0"),
        ("透视受检者入射体表空气比释动能率最大值_普通剂量模式", "<=88.0"),
        ("透视受检者入射体表空气比释动能率最大值_高剂量模式", "<=176.0"),
    ):
        k = f"{row_prefix}_判定标准"
        if value_mapping.get(k) in (None, ""):
            value_mapping[k] = crit


# 透视防护区术者位逐点：报告/现场常无独立「判定标准」列，constants 可覆盖；未配置时用常用验收参照（机构应以 constants 修正）。
_DEFAULT_SHIELD_ZONE_AMBIENT_DOSE_EQ_RATE_USVH = 400.0


def _inject_shield_zone_operator_row_criteria(
    value_mapping: dict, constants: dict | None = None
) -> None:
    """
    为「透视防护区…术者位×高度」各检测结果行补缺同行「…_判定标准」，供单项判定推算合格/不合格。
    优先：value_mapping 内已有防护区类 _判定标准 且全文一致时整表复用；
    其次：formSchema.constants.shieldZoneAmbientDoseEqRateLimit（或 shieldZoneDoseRateLimit）；
    最后：内置默认限值（仅当完全无参照时）。
    """
    if not isinstance(value_mapping, dict):
        return
    marker = "透视防护区检测平面上周围剂量当量率"
    const = constants if isinstance(constants, dict) else {}
    samples: list[str] = []
    for k, v in value_mapping.items():
        if not isinstance(k, str) or marker not in k or "术者位" not in k:
            continue
        if not k.endswith("_判定标准"):
            continue
        if v in (None, "") or isinstance(v, (dict, list)):
            continue
        t = str(v).strip()
        if t and t not in samples:
            samples.append(t)
    crit_text = ""
    if len(samples) == 1:
        crit_text = samples[0]
    if not crit_text:
        lim = const.get("shieldZoneAmbientDoseEqRateLimit")
        if lim in (None, ""):
            lim = const.get("shieldZoneDoseRateLimit")
        if lim not in (None, ""):
            try:
                crit_text = "≤" + _format_numeric_criterion_fragment(float(lim))
            except (TypeError, ValueError):
                crit_text = ""
    if not crit_text:
        crit_text = "≤" + _format_numeric_criterion_fragment(_DEFAULT_SHIELD_ZONE_AMBIENT_DOSE_EQ_RATE_USVH)
    for k, v in list(value_mapping.items()):
        if not isinstance(k, str) or marker not in k or "术者位" not in k:
            continue
        pfx = None
        if k.endswith("_检测结果"):
            pfx = k[: -len("_检测结果")]
        elif k.endswith("_计算结果"):
            pfx = k[: -len("_计算结果")]
        if not pfx:
            continue
        ck = f"{pfx}_判定标准"
        if value_mapping.get(ck) not in (None, ""):
            continue
        value_mapping[ck] = crit_text


def _shield_zone_criterion_text_for_verdict_field(verdict_key: str, vm: dict) -> str:
    """术者位防护区：同行「…_判定标准」或同身份（术者位+体位）下任一条判定标准句。"""
    if not isinstance(verdict_key, str) or not isinstance(vm, dict):
        return ""
    if "透视防护区检测平面上周围剂量当量率" not in verdict_key or "术者位" not in verdict_key:
        return ""
    if not verdict_key.endswith("_单项判定"):
        return ""
    base = verdict_key[: -len("_单项判定")]
    ck = f"{base}_判定标准"
    v = vm.get(ck)
    if v not in (None, "") and not isinstance(v, (dict, list)):
        return str(v).strip()
    probe = f"{base}_检测结果"
    id_probe = _shield_zone_row_identity_tuple(probe)
    if not id_probe:
        return ""
    loc = (id_probe[0], id_probe[1], id_probe[2])
    for k, v2 in vm.items():
        if not isinstance(k, str) or not k.endswith("_判定标准"):
            continue
        if "透视防护区检测平面上周围剂量当量率" not in k or "术者位" not in k:
            continue
        oid = _shield_zone_row_identity_tuple(k)
        if not oid or (oid[0], oid[1], oid[2]) != loc:
            continue
        if v2 not in (None, "") and not isinstance(v2, (dict, list)):
            return str(v2).strip()
    return ""


def _primary_field_identity(field: dict) -> str:
    """区分「同一 pdfFieldId」是否被不同语义占位复用（如 f3 同时用于检测日期年与亮度检测结果③）。"""
    for k in ("placeholder", "id", "title", "label"):
        v = field.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    for k in ("placeholder", "title", "label", "key"):
        v = src.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _iter_schema_fields_from_steps(steps: list | None):
    """遍历 unified_form_template `steps` 下全部控件（含 matrix 单元格），与提交 JSON 内 steps 结构一致。"""
    if not isinstance(steps, list):
        return
    for step in steps:
        if not isinstance(step, dict):
            continue
        fl = step.get("fields")
        if isinstance(fl, list):
            for f in fl:
                if isinstance(f, dict):
                    yield f
        sections = step.get("sections")
        if not isinstance(sections, list):
            continue
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            fl2 = sec.get("fields")
            if isinstance(fl2, list):
                for f in fl2:
                    if isinstance(f, dict):
                        yield f
            m = sec.get("matrix") if isinstance(sec.get("matrix"), dict) else {}
            for hf in m.get("headerFields") or []:
                if isinstance(hf, dict):
                    yield hf
            for row in m.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                cells = row.get("cells") if isinstance(row.get("cells"), dict) else {}
                for cell in cells.values():
                    if isinstance(cell, dict):
                        yield cell


def _iter_submit_schema_fields(source_data: dict):
    steps = source_data.get("steps")
    if not isinstance(steps, list):
        return
    yield from _iter_schema_fields_from_steps(steps)


def _apply_computed_fields_from_steps_to_mapping(
    value_mapping: dict,
    steps: list | None,
    *,
    template_constants: dict | None,
    template_enums: dict | None,
    template_lookup_tables: dict | None,
) -> None:
    """
    将 steps 中 type=computed 且 formula 非空的字段写入 value_mapping（仅补缺键）。
    含 row.* 的公式依赖矩阵行上下文，当前跳过（由前端算好写入提交 JSON 即可）。
    """
    if not isinstance(value_mapping, dict) or not isinstance(steps, list):
        return
    try:
        from utils.dynamic_form_expression import eval_computed_formula
    except ImportError:
        return
    const = template_constants if isinstance(template_constants, dict) else {}
    enums = template_enums if isinstance(template_enums, dict) else {}
    lts = template_lookup_tables if isinstance(template_lookup_tables, dict) else {}
    for _ in range(32):
        changed = 0
        for f in _iter_schema_fields_from_steps(steps):
            if str(f.get("type") or "").lower() != "computed":
                continue
            fid = str(f.get("id") or "").strip()
            formula = str(f.get("formula") or "").strip()
            if not fid or not formula:
                continue
            if "row." in formula:
                continue
            if value_mapping.get(fid) not in (None, ""):
                continue
            try:
                res = eval_computed_formula(
                    formula,
                    value_mapping,
                    constants=const,
                    enums=enums,
                    lookup_tables=lts,
                    row=None,
                )
            except Exception:
                res = None
            if res is None:
                continue
            value_mapping[fid] = res if isinstance(res, str) else str(res)
            changed += 1
        if not changed:
            break


def format_instrument_display_line(*, model: str, name: str, code: str, cert_date: date | None) -> str:
    """
    检测仪器单元格展示：型号、名称、编号以空格分隔，后接（有效期至yyyy年mm月dd日）。
    无证书日期则省略括号整段。
    """
    model = (model or "").strip()
    name = (name or "").strip()
    code = (code or "").strip()
    body = " ".join([x for x in (model, name, code) if x])
    if cert_date is None:
        return body
    try:
        suffix = cert_date.strftime("%Y年%m月%d日")
        return f"{body}（有效期至{suffix}）"
    except Exception:
        return body


def _parse_date_like_for_instrument_display(v: object) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    s = str(v).strip()
    if not s:
        return None
    dt = parse_datetime(s)
    if dt is not None:
        return dt.date()
    try:
        head = s.split("T", 1)[0]
        parts = head.split("-")
        if len(parts) >= 3:
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            return date(y, m, d)
    except Exception:
        return None
    return None


def format_instrument_display_from_submit_item(it: dict) -> str:
    if not isinstance(it, dict):
        return ""
    model = str(it.get("model") or "").strip()
    name = str(
        it.get("name")
        or it.get("instrumentName")
        or it.get("instrumentType")
        or ""
    ).strip()
    code = str(it.get("identifier") or it.get("code") or it.get("serialNumber") or "").strip()
    cert = _parse_date_like_for_instrument_display(
        it.get("certificateValidUntil")
        or it.get("certificate_valid_until")
        or it.get("validUntil")
    )
    return format_instrument_display_line(model=model, name=name, code=code, cert_date=cert)


def format_instrument_display_from_catalog(inst: InstrumentCatalog) -> str:
    return format_instrument_display_line(
        model=inst.model or "",
        name=inst.name or "",
        code=inst.code or "",
        cert_date=inst.certificate_valid_until,
    )


def instrument_catalog_to_payload_dict(inst: InstrumentCatalog) -> dict:
    """与检测提交 instruments[] 条目对齐，供下拉与回填使用。"""
    vu = ""
    if inst.certificate_valid_until:
        d = inst.certificate_valid_until
        dt = timezone.make_aware(datetime(d.year, d.month, d.day, 0, 0, 0))
        vu = dt.isoformat()
    sid = str(int(inst.pk))
    return {
        "id": sid,
        "instrumentId": sid,
        "identifier": inst.code,
        "name": inst.name,
        "model": inst.model or "",
        "certificateNo": inst.certificate_no or "",
        "validUntil": vu,
        "enabled": True,
    }


def merge_task_template_bound_instruments_into_payload(
    payload: dict | None, task_obj, project_obj=None
) -> dict:
    """
    检测仪器优先级：
    1）项目 ``assigned_instrument_ids``（派工按种类分配的具体编号）或任务模板旧版固定 id；
    2）前端已提交非空 instruments：完全以前端为准。
    任务模板 ``bindingMode=kinds`` 时无项目分配则不预填编号。
    """
    out = dict(payload or {})
    if task_obj is None:
        return out
    from utils.task_bound_instruments import bound_instrument_ids_for_legacy_list

    if project_obj is not None:
        from apps.core.instrument_inventory_service import resolved_instrument_ids_for_project

        binding = resolved_instrument_ids_for_project(project_obj)
    else:
        from utils.task_bound_instruments import normalize_task_bound_instruments

        binding = normalize_task_bound_instruments(
            getattr(task_obj, "bound_instrument_ids", None) or []
        )
    raw_ids = bound_instrument_ids_for_legacy_list(binding)
    cur = out.get("instruments")
    if not isinstance(cur, list):
        cur = []
    else:
        cur = list(cur)
    if cur:
        out["instruments"] = cur
        return out
    if not raw_ids:
        out["instruments"] = []
        return out
    merged: list = []
    seen: set[str] = set()
    for x in raw_ids:
        try:
            pk = int(x)
        except (TypeError, ValueError):
            continue
        sid = str(pk)
        if sid in seen:
            continue
        inst = InstrumentCatalog.objects.filter(pk=pk, is_active=True).first()
        if inst is None:
            inst = InstrumentCatalog.objects.filter(pk=pk).first()
        if inst is None:
            continue
        merged.append(instrument_catalog_to_payload_dict(inst))
        seen.add(sid)
    out["instruments"] = merged
    return out


def build_instrument_catalog_options_for_frontend() -> list[dict]:
    """启用仪器主数据列表（供服务端或专用接口使用；前端导出 JSON 不再嵌入全表）。"""
    opts: list[dict] = []
    for row in (
        InstrumentCatalog.objects.filter(is_active=True)
        .order_by("code", "id")
        .values("id", "code", "name", "model", "certificate_no", "certificate_valid_until")
    ):
        cv = row.get("certificate_valid_until")
        opts.append(
            {
                "id": str(int(row["id"])),
                "code": row["code"] or "",
                "name": row["name"] or "",
                "model": row["model"] or "",
                "certificateNo": (row.get("certificate_no") or "") or "",
                "certificateValidUntil": cv.isoformat() if cv else None,
            }
        )
    return opts


_INSTRUMENT_DD_ENABLED_RE = re.compile(r"^instrument(\d+)Enabled$", re.I)
_INSTRUMENT_DD_TEST_RE = re.compile(r"^testInstrument(\d+)$", re.I)
_INSTRUMENT_SELECT_ID_TEST = re.compile(r"^testInstrument(\d+)$", re.I)
_INSTRUMENT_SELECT_ID_NUM = re.compile(r"^instrument(\d+)$", re.I)
# 库 PDF / 统一表单常见：「检测仪器_仪器1」、纯中文「仪器2」
_INSTRUMENT_CN_SLOT_IN_ID = re.compile(r"检测仪器_仪器(\d+)", re.I)
_INSTRUMENT_PLAIN_SLOT_ID = re.compile(r"^仪器(\d+)$", re.I)
_INSTRUMENT_DD_MAX_SLOTS = 32

_DOSE_RATE_UNIT_ENUM_SLUGS = frozenset({"mgypermin", "ugypermin", "ugypersec", "ngypersec"})


def _instrument_slot_index_for_instrument_select_field(field_id: str, field: dict | None = None) -> int | None:
    """从 schema 字段 id / instrumentScope / submitPath 推断检测仪器槽位（1-based）。"""
    if isinstance(field, dict):
        scope = str(field.get("instrumentScope") or "").strip()
        if scope == "qualityControl":
            return 1
        if scope == "radiationProtection":
            return 2
        try:
            rs = int(field.get("registrySlot") or 0)
            if rs > 0:
                return rs
        except (TypeError, ValueError):
            pass
        sp = str(field.get("submitPath") or "").strip()
        if sp.endswith("instruments.qualityControl") or ".qualityControl" in sp:
            return 1
        if sp.endswith("instruments.radiationProtection") or ".radiationProtection" in sp:
            return 2
    s = str(field_id or "").strip()
    if not s:
        return None
    m = _INSTRUMENT_SELECT_ID_TEST.match(s)
    if m:
        return int(m.group(1))
    m = _INSTRUMENT_SELECT_ID_NUM.match(s)
    if m:
        n = int(m.group(1))
        return n if n >= 1 else None
    m = _INSTRUMENT_CN_SLOT_IN_ID.search(s)
    if m:
        n = int(m.group(1))
        return n if n >= 1 else None
    m = _INSTRUMENT_PLAIN_SLOT_ID.match(s)
    if m:
        n = int(m.group(1))
        return n if n >= 1 else None
    if s.lower() == "instrument":
        return 1
    # JS-117 等：单格「检测仪器」字段 id 为 testInstrument（非 testInstrument1）
    if s.lower() == "testinstrument":
        return 1
    return None


def _collect_instrument_slot_lines_from_steps_instrument_select(
    source_data: dict, site_steps_merged: list | None
) -> dict[int, str]:
    """
    按库模板 steps 中 type=instrument_select 的 pdfFieldId，从 submit.dynamicData[fXX] 取仪器主键/对象并解析为展示行。
    仅处理 instrument_select，避免把同 pdfFieldId 上的普通数字控件误当仪器。
    """
    out: dict[int, str] = {}
    if not isinstance(source_data, dict) or not site_steps_merged:
        return out
    dd = source_data.get("dynamicData")
    if not isinstance(dd, dict):
        dd = {}
    dd = _normalize_dynamic_data_f_slots(dd)
    guard = _instrument_guard_scalar_tokens(source_data)
    for fld in _flatten_unified_form_steps_to_fields_deep(site_steps_merged):
        if not isinstance(fld, dict):
            continue
        if str(fld.get("type") or "").lower() != "instrument_select":
            continue
        fid = str(fld.get("id") or "").strip()
        slot = _instrument_slot_index_for_instrument_select_field(fid, fld)
        if slot is None or slot < 1 or slot > _INSTRUMENT_DD_MAX_SLOTS:
            continue
        en = dd.get(f"instrument{slot}Enabled")
        if en is False:
            continue
        src = fld.get("source") if isinstance(fld.get("source"), dict) else {}
        pid = str(src.get("pdfFieldId") or fld.get("pdfFieldId") or "").strip()
        raw = None
        if pid:
            raw = dd.get(pid)
        if raw in (None, ""):
            raw = fld.get("value")
            if isinstance(raw, (dict, list, bool)):
                raw = None
        if raw in (None, ""):
            continue
        line = ""
        if isinstance(raw, dict):
            line = format_instrument_display_from_submit_item(raw)
        elif isinstance(raw, bool):
            continue
        elif isinstance(raw, int):
            rs = str(raw)
            line = (_resolve_instrument_id_display(source_data, rs) or "").strip()
        elif isinstance(raw, float) and raw.is_integer():
            rs = str(int(raw))
            line = (_resolve_instrument_id_display(source_data, rs) or "").strip()
        else:
            rs = str(raw).strip()
            if rs:
                line = (_resolve_instrument_id_display(source_data, rs) or "").strip()
        if line:
            ls = line.strip()
            if ls in guard:
                continue
            out[slot] = line
    return out


def _collect_instrument_slot_lines_from_steps_instruments_text_bind(
    source_data: dict, site_steps_merged: list | None
) -> dict[int, str]:
    """
    库模板将整表 instruments 绑在 type=text/textarea 上（如 JS-117 testInstrument + f67），
    仅写 dynamicData.f67 而不落根级 instruments[] 时，从该 pdfFieldId 槽取展示串。
    与 instrument_select 分支互补；不处理 instrument_select（避免重复）。
    """
    out: dict[int, str] = {}
    if not isinstance(source_data, dict) or not site_steps_merged:
        return out
    dd = source_data.get("dynamicData")
    if not isinstance(dd, dict):
        dd = {}
    dd = _normalize_dynamic_data_f_slots(dd)
    guard = _instrument_guard_scalar_tokens(source_data)
    for fld in _flatten_unified_form_steps_to_fields_deep(site_steps_merged):
        if not isinstance(fld, dict):
            continue
        if str(fld.get("type") or "").lower() == "instrument_select":
            continue
        ftyp = str(fld.get("type") or "").lower()
        if ftyp and ftyp not in ("text", "textarea"):
            continue
        src = fld.get("source") if isinstance(fld.get("source"), dict) else {}
        sp = str(src.get("submitPath") or fld.get("submitPath") or "").strip()
        if sp not in ("instruments", "instruments.qualityControl", "instruments.radiationProtection"):
            continue
        fid = str(fld.get("id") or "").strip()
        slot = _instrument_slot_index_for_instrument_select_field(fid, fld)
        if slot is None or slot < 1 or slot > _INSTRUMENT_DD_MAX_SLOTS:
            slot = 1
        pid = str(src.get("pdfFieldId") or fld.get("pdfFieldId") or "").strip()
        if not pid:
            continue
        raw = _unwrap_dynamic_data_cell_value(dd.get(pid))
        if raw in (None, ""):
            raw = fld.get("value")
            if isinstance(raw, (dict, list, bool)):
                raw = None
        if raw in (None, ""):
            continue
        line = ""
        if isinstance(raw, dict):
            line = format_instrument_display_from_submit_item(raw)
        elif isinstance(raw, bool):
            continue
        elif isinstance(raw, int):
            rs = str(raw)
            line = (_resolve_instrument_id_display(source_data, rs) or "").strip()
        elif isinstance(raw, float) and raw.is_integer():
            rs = str(int(raw))
            line = (_resolve_instrument_id_display(source_data, rs) or "").strip()
        else:
            rs = str(raw).strip()
            if rs:
                line = (_resolve_instrument_id_display(source_data, rs) or "").strip() or rs
        if not (line or "").strip():
            continue
        ls = line.strip()
        if ls in guard:
            continue
        out[slot] = ls
    return out


def _instrument_submit_dict_is_empty(it: dict) -> bool:
    """根级 instruments[] 条目是否无任何可展示字段（前端可能占位空 dict）。"""
    if not isinstance(it, dict):
        return True
    for k in (
        "id",
        "instrumentId",
        "name",
        "instrumentName",
        "instrumentType",
        "model",
        "identifier",
        "code",
        "serialNumber",
    ):
        v = it.get(k)
        if v is not None and str(v).strip():
            return False
    return True


def _instruments_submit_looks_like_named_table_rows(raw_list: list) -> bool:
    """统一表单「检测仪器」table：多行带只读 instrumentType（与单条台账 instrument 区分，避免误判）。"""
    n = 0
    for it in raw_list:
        if isinstance(it, dict) and str(it.get("instrumentType") or "").strip():
            n += 1
            if n >= 2:
                return True
    return False


def _collect_instrument_slot_lines_from_table_instruments(source_data: dict, raw_list: list) -> dict[int, str]:
    """仅处理带 instrumentType 的 table 行：按原顺序取 enabled 为真的行生成 检测仪器1… 槽位。"""
    out: dict[int, str] = {}
    if not isinstance(raw_list, list):
        return out
    slot = 0
    for it in raw_list:
        if not isinstance(it, dict):
            continue
        if it.get("enabled") is False:
            continue
        if not str(it.get("instrumentType") or "").strip():
            continue
        if _instrument_submit_dict_is_empty(it):
            continue
        line = format_instrument_display_from_submit_item(it)
        if not (line or "").strip():
            sid0 = str(it.get("id") or it.get("instrumentId") or "").strip()
            if sid0:
                line = (_resolve_instrument_id_display(source_data, sid0) or "").strip()
        if not line:
            continue
        slot += 1
        if slot > _INSTRUMENT_DD_MAX_SLOTS:
            break
        out[slot] = line
    return out


_JS001_INSTRUMENT_FALLBACK_F_SLOT_KEYS: tuple[tuple[str, ...], ...] = (
    ("f19", "f20", "f23", "f24"),
    ("f21", "f22", "f139", "f140"),
)


def _try_fill_instrument_slots_from_dd_f_key_rows(
    out: dict[int, str], dd: dict, max_idx: int, source_data: dict
) -> None:
    """
    instruments[] / testInstrument 皆缺时，从 App 常见「四联 f 号」解析多台仪器主键（须命中 InstrumentCatalog，避免质控数值误认）。
    """
    if not isinstance(out, dict) or not isinstance(dd, dict) or not isinstance(source_data, dict):
        return
    guard = _instrument_guard_scalar_tokens(source_data)

    def _slot_line_is_usable(idx: int) -> bool:
        t = (out.get(idx) or "").strip()
        return bool(t) and t not in guard

    usable = sum(1 for j in range(1, max_idx + 1) if _slot_line_is_usable(j))
    if usable >= 2:
        return
    for tup in _JS001_INSTRUMENT_FALLBACK_F_SLOT_KEYS:
        cand: dict[int, str] = {}
        for i, fk in enumerate(tup, start=1):
            if i > max_idx:
                break
            if dd.get(f"instrument{i}Enabled") is False:
                continue
            v = dd.get(fk)
            if isinstance(v, float) and v.is_integer():
                v = int(v)
            if not isinstance(v, int) or v < 1:
                continue
            if not InstrumentCatalog.objects.filter(pk=v).exists():
                continue
            rs = str(v)
            line = (_resolve_instrument_id_display(source_data, rs) or "").strip()
            if line:
                cand[i] = line
        if len(cand) < 2:
            continue
        for i, ln in cand.items():
            cur = (out.get(i) or "").strip()
            if (not cur) or cur in guard:
                out[i] = ln
        return


def _collect_instrument_slot_lines_from_submit(
    source_data: dict, site_steps_merged: list | None = None
) -> dict[int, str]:
    """
    按槽位下标（1-based）收集检测仪器展示行：合并根级 instruments[] 与 dynamicData 中
    instrument{n}Enabled / testInstrument{n} / instrument{n}（部分客户端只写 dynamicData）；
    并可按库模板 steps 中 instrument_select 的 pdfFieldId 从 dynamicData 补缺。
    返回 {槽位: 文案}，与 PDF「检测仪器{n}」下标对齐，不压缩空槽。
    根级 instruments 行若仅含主键等缺字段，会回查 InstrumentCatalog 拼 §3 展示串（与登记接口一致）。
    """
    if not isinstance(source_data, dict):
        return {}
    dd = source_data.get("dynamicData")
    if not isinstance(dd, dict):
        dd = {}
    dd = _normalize_dynamic_data_f_slots(dd)
    raw_list = source_data.get("instruments")
    if not isinstance(raw_list, list):
        raw_list = []
    if raw_list and _instruments_submit_looks_like_named_table_rows(raw_list):
        table_out = _collect_instrument_slot_lines_from_table_instruments(source_data, raw_list)
        if table_out:
            return table_out
    max_idx = len(raw_list)
    for k in dd:
        if not isinstance(k, str):
            continue
        m = _INSTRUMENT_DD_ENABLED_RE.match(k) or _INSTRUMENT_DD_TEST_RE.match(k)
        if m:
            max_idx = max(max_idx, int(m.group(1)))
    max_idx = min(max_idx, _INSTRUMENT_DD_MAX_SLOTS)
    out: dict[int, str] = {}
    for i in range(1, max_idx + 1):
        line = ""
        raw = dd.get(f"testInstrument{i}")
        if isinstance(raw, dict):
            line = format_instrument_display_from_submit_item(raw)
        elif raw is not None and not isinstance(raw, bool):
            if isinstance(raw, int):
                rs = str(raw)
            elif isinstance(raw, float) and raw.is_integer():
                rs = str(int(raw))
            else:
                rs = str(raw).strip()
            if rs:
                line = (_resolve_instrument_id_display(source_data, rs) or "").strip()
        if not line:
            raw_plain = dd.get(f"instrument{i}")
            if isinstance(raw_plain, dict):
                line = format_instrument_display_from_submit_item(raw_plain)
            elif raw_plain is not None and not isinstance(raw_plain, bool):
                if isinstance(raw_plain, int):
                    rs = str(raw_plain)
                elif isinstance(raw_plain, float) and raw_plain.is_integer():
                    rs = str(int(raw_plain))
                else:
                    rs = str(raw_plain).strip()
                if rs:
                    line = (_resolve_instrument_id_display(source_data, rs) or "").strip()
        if not line and i - 1 < len(raw_list):
            it = raw_list[i - 1]
            if isinstance(it, dict) and not _instrument_submit_dict_is_empty(it):
                line = format_instrument_display_from_submit_item(it)
                if not (line or "").strip():
                    sid0 = str(it.get("id") or it.get("instrumentId") or "").strip()
                    if sid0:
                        line = (_resolve_instrument_id_display(source_data, sid0) or "").strip()
        en = dd.get(f"instrument{i}Enabled")
        if en is False and not line:
            continue
        if line:
            out[i] = line
    if site_steps_merged:
        for si, ln in _collect_instrument_slot_lines_from_steps_instrument_select(
            source_data, site_steps_merged
        ).items():
            if not ln:
                continue
            prev = (out.get(si) or "").strip()
            if not prev:
                out[si] = ln
        for si, ln in _collect_instrument_slot_lines_from_steps_instruments_text_bind(
            source_data, site_steps_merged
        ).items():
            if not ln:
                continue
            prev = (out.get(si) or "").strip()
            if not prev:
                out[si] = ln
    _try_fill_instrument_slots_from_dd_f_key_rows(out, dd, max_idx, source_data)
    return out


def _build_instrument_bindings_for_task(
    task_obj, instruments: list, project_obj=None
) -> list[dict]:
    """
    质控/防护仪器绑定元数据：与 instruments.qualityControl / instruments.radiationProtection 对齐。
    栏位渲染在「受检设备主要检测仪器及检测人员」章（sectionType=instrumentPersonnel）。
    """
    bindings: list[dict] = []
    if project_obj is not None:
        from apps.core.instrument_inventory_service import resolved_instrument_ids_for_project

        binding = resolved_instrument_ids_for_project(project_obj)
    else:
        from utils.task_bound_instruments import normalize_task_bound_instruments

        binding = normalize_task_bound_instruments(
            getattr(task_obj, "bound_instrument_ids", None) or [] if task_obj is not None else []
        )
    default_id = ""
    if isinstance(instruments, list) and instruments:
        default_id = str(
            instruments[0].get("id")
            or instruments[0].get("instrumentId")
            or instruments[0].get("identifier")
            or ""
        ).strip()
    qc_ids = list(binding.get("qualityControl") or [])
    rp_ids = list(binding.get("radiationProtection") or [])
    qc_id = str(qc_ids[0]) if qc_ids else default_id
    rp_id = str(rp_ids[0]) if rp_ids else ""
    bindings.append(
        {
            "scope": "qualityControl",
            "sectionType": "instrumentPersonnel",
            "label": "主要检测仪器_质量控制（性能）检测",
            "submitPath": "instruments.qualityControl",
            "submitBucket": "instruments",
            "registrySlot": 1,
            "defaultInstrumentId": qc_id or None,
            "defaultInstrumentIds": [str(x) for x in qc_ids] if qc_ids else None,
        }
    )
    bindings.append(
        {
            "scope": "radiationProtection",
            "sectionType": "instrumentPersonnel",
            "label": "主要检测仪器_工作场所放射防护检测",
            "submitPath": "instruments.radiationProtection",
            "submitBucket": "instruments",
            "registrySlot": 2,
            "defaultInstrumentId": rp_id or None,
            "defaultInstrumentIds": [str(x) for x in rp_ids] if rp_ids else None,
        }
    )
    return bindings


def build_instruments_root_for_frontend_export(
    *, task_obj=None, project_obj=None, payload: dict | None = None
) -> dict:
    """
    生成写入「前端导出 JSON」根级的仪器块（不再内嵌全库仪器表）：
    - instruments：见 merge_task_template_bound_instruments_into_payload（无提交用模板绑定，有提交以前端为准）；
    - instrumentBindings：质控/防护两章仪器格与台账 id 的对应关系；
    - 全量下拉数据请前端调用登记/台账接口（如 GET …/registry/instruments/）。
    """
    merged = merge_task_template_bound_instruments_into_payload(
        dict(payload or {}), task_obj, project_obj=project_obj
    )
    raw = merged.get("instruments")
    instruments = list(raw) if isinstance(raw, list) else []
    out: dict = {"instruments": instruments}
    if task_obj is not None:
        out["instrumentBindings"] = _build_instrument_bindings_for_task(
            task_obj, instruments, project_obj=project_obj
        )
    return out


def _instrument_guard_scalar_tokens(source_data: dict) -> frozenset[str]:
    """
    常被误挂到 instrument_select / 与仪器同 pdf 槽位的非仪器字面值（委托编号、受检编号等）。
    用于避免把 reportInfo.commissionNo 等原样当作「仪器展示行」。
    """
    out: set[str] = set()
    if not isinstance(source_data, dict):
        return frozenset()
    ri = source_data.get("reportInfo") if isinstance(source_data.get("reportInfo"), dict) else {}
    for k in ("commissionNo", "entrustNo", "inspectionNo", "projectId", "year", "month", "day"):
        v = ri.get(k)
        if v not in (None, ""):
            t = str(v).strip()
            if t:
                out.add(t)
    for k in ("taskNo", "projectId"):
        v = source_data.get(k)
        if v not in (None, ""):
            t = str(v).strip()
            if t:
                out.add(t)
    hi = source_data.get("hospitalInfo") if isinstance(source_data.get("hospitalInfo"), dict) else {}
    for k in ("inspection",):
        v = hi.get(k)
        if v not in (None, ""):
            t = str(v).strip()
            if t:
                out.add(t)
    return frozenset(out)


def _build_instrument_text_aliases_from_submit(
    source_data: dict, site_steps_merged: list | None = None, task_obj=None
) -> dict[str, str]:
    """
    为 PDF 占位符补缺：检测仪器1/仪器1…，以及「检测仪器列表」「检测仪器汇总」合并串（中文分号，二者同文）。
    仅产出非空串；由 _merge_mapping_fill_empty 写入，不覆盖已有映射。
    下标与模板槽位一致，见 _collect_instrument_slot_lines_from_submit。
    instruments 为空时按任务模板 bound_instrument_ids 合并主数据（与 merge_task_template_bound_instruments_into_payload 一致）。
    """
    out: dict[str, str] = {}
    if not isinstance(source_data, dict):
        return out
    sd: dict = source_data
    if task_obj is not None:
        raw_ins = source_data.get("instruments")
        if not (isinstance(raw_ins, list) and raw_ins):
            merged = merge_task_template_bound_instruments_into_payload(dict(source_data), task_obj)
            inst2 = merged.get("instruments")
            if isinstance(inst2, list) and inst2:
                sd = dict(source_data)
                sd["instruments"] = inst2
    slot_lines = _collect_instrument_slot_lines_from_submit(sd, site_steps_merged)
    for idx, line in sorted(slot_lines.items()):
        out[f"检测仪器{idx}"] = line
        out[f"仪器{idx}"] = line
        out[f"检测仪器_仪器{idx}"] = line
    if slot_lines.get(1):
        out["主要检测仪器_质量控制（性能）检测"] = slot_lines[1]
    if slot_lines.get(2):
        out["主要检测仪器_工作场所放射防护检测"] = slot_lines[2]
    if slot_lines:
        merged = "；".join(slot_lines[k] for k in sorted(slot_lines))
        out["检测仪器列表"] = merged
        out["检测仪器汇总"] = merged
        out["检测仪器"] = merged
    return out


def _resolve_instrument_id_display(source_data: dict, iid: str) -> str:
    """将 instrument_select / 台账主键解析为 PDF 展示用整行文案；提交行缺字段时回退 InstrumentCatalog。"""
    if not iid or not isinstance(source_data, dict):
        return ""
    s = str(iid).strip()
    if not s:
        return ""
    guard = _instrument_guard_scalar_tokens(source_data)
    if s in guard:
        return ""
    lst = source_data.get("instruments")
    if isinstance(lst, list):
        for it in lst:
            if not isinstance(it, dict):
                continue
            for key in ("id", "instrumentId"):
                rid = str(it.get(key) or "").strip()
                if rid and rid == s:
                    full = format_instrument_display_from_submit_item(it)
                    name = str(it.get("name") or it.get("instrumentName") or "").strip()
                    merged = (full or name).strip()
                    if merged:
                        return merged
                    break
            rid_code = str(it.get("identifier") or it.get("code") or "").strip()
            if rid_code and rid_code == s:
                full = format_instrument_display_from_submit_item(it)
                if (full or "").strip():
                    return full.strip()
    try:
        pk = int(s)
    except (ValueError, TypeError):
        pk = None
    if pk is not None:
        inst = InstrumentCatalog.objects.filter(pk=pk).first()
        if inst is not None:
            return format_instrument_display_from_catalog(inst) or ""
    inst_code = InstrumentCatalog.objects.filter(code=s).first()
    if inst_code is not None:
        return format_instrument_display_from_catalog(inst_code) or ""
    return ""


def _maybe_instrument_select_display(field: dict, source_data: dict, picked: str) -> str:
    """instrument_select：提交值为仪器 id 时，在报告中替换为仪器名称（若 instruments 列表可解析）。"""
    if str(field.get("type") or "").lower() != "instrument_select":
        return picked
    if not isinstance(picked, str) or not picked.strip():
        return picked
    ps = picked.strip()
    resolved = _resolve_instrument_id_display(source_data, ps)
    if resolved:
        return resolved
    if ps in _instrument_guard_scalar_tokens(source_data):
        return ""
    return picked


def _flatten_unified_form_steps_to_fields(steps: list | None) -> list:
    """将 steps 树摊平为控件 dict 列表，供 submitPath 与 _walk 类逻辑使用。"""
    return [f for f in _iter_schema_fields_from_steps(steps)]


def _parsed_template_steps_list(parsed: dict | None) -> list:
    if not isinstance(parsed, dict):
        return []
    steps = parsed.get("steps") if isinstance(parsed.get("steps"), list) else []
    if not steps and isinstance(parsed.get("form_schema"), dict):
        s2 = parsed["form_schema"].get("steps")
        if isinstance(s2, list):
            steps = s2
    return steps


def _collect_ambiguous_pdf_field_ids_from_steps(steps: list | None) -> frozenset[str]:
    by_pid: dict[str, set[str]] = {}
    for f in _iter_schema_fields_from_steps(steps):
        pid = _field_pdf_id(f)
        ident = _primary_field_identity(f)
        if not pid or not ident:
            continue
        by_pid.setdefault(pid, set()).add(ident)
    return frozenset(pid for pid, idents in by_pid.items() if len(idents) > 1)


def _collect_ambiguous_pdf_field_ids_from_flat_fields(fields: list | None) -> frozenset[str]:
    """HTMLPDF 坐标 fields 树内，同一 pdfFieldId 对应多个不同语义占位时视为歧义。"""
    by_pid: dict[str, set[str]] = {}
    flat: list = []
    _walk_template_field_dicts(fields if isinstance(fields, list) else None, flat)
    for f in flat:
        pid = _field_pdf_id(f)
        ident = _primary_field_identity(f)
        if not pid or not ident:
            continue
        by_pid.setdefault(pid, set()).add(ident)
    return frozenset(pid for pid, idents in by_pid.items() if len(idents) > 1)


def _inject_hospital_equipment_cn_aliases(value_mapping: dict, source_data: dict) -> None:
    """
    将 hospitalInfo / equipmentInfo 按常见中文 placeholder 再挂一批键，避免模板用语与默认英文键不一致时落空。
    仅补缺，不覆盖已有非空映射。
    """
    hi = source_data.get("hospitalInfo") if isinstance(source_data.get("hospitalInfo"), dict) else {}
    ei = source_data.get("equipmentInfo") if isinstance(source_data.get("equipmentInfo"), dict) else {}
    tr = source_data.get("testResult") if isinstance(source_data.get("testResult"), dict) else {}

    def put(keys: list[str], val: object) -> None:
        if val in (None, ""):
            return
        if isinstance(val, (dict, list)):
            return
        s = str(val).strip()
        if not s:
            return
        for key in keys:
            if not key:
                continue
            if value_mapping.get(key) not in (None, ""):
                continue
            value_mapping[key] = s

    hospital_display = _hospital_info_inspected_unit_name(hi)
    put(
        ["受检单位名称", "医院名称", "单位名称", "医疗机构名称", "受检单位", "hospitalname"],
        hospital_display,
    )
    addr = _hospital_info_inspected_unit_address(hi)
    put(["受检单位地址", "单位地址", "地址"], addr)
    # 联系人/电话：优先拆分「委托单位联系人/电话」整格（commissionContactPhone）
    _cn, _ph = split_contact_name_phone(
        str(hi.get("contactPerson") or ""),
        str(hi.get("contactPhone") or ""),
    )
    _ccp = str(hi.get("commissionContactPhone") or "").strip()
    if _ccp:
        if not _ph:
            n2, p2 = split_contact_name_phone(_ccp, "")
            _cn = (_cn or n2).strip()
            _ph = (_ph or p2).strip()
        elif not _cn:
            n2, p2 = split_contact_name_phone(_ccp, _ph)
            _cn = (n2 or _cn).strip()
    _cn = (_cn or str(hi.get("contactPerson") or "")).strip()
    _ph = (_ph or str(hi.get("contactPhone") or "")).strip()
    put(["联系人", "委托单位联系人", "受检单位联系人"], _cn or hi.get("contactPerson"))
    put(["联系电话", "联系人电话", "电话", "手机"], _ph or hi.get("contactPhone"))
    # 委托单位联系人/电话：整格原文仍挂上，便于单列长文本模板
    put(
        [
            "委托单位联系人/电话",
            "委托单位联系人",
            "委托联系人",
            "委托单位联系人电话",
        ],
        hi.get("commissionContactPhone"),
    )
    # 委托单位名称（与 derived 一致，仅补缺）
    _mode = str(hi.get("commissionOrgMode") or "").strip()
    _hosp = str(hi.get("name") or hi.get("inspection") or "").strip()
    if _mode == "customCommission":
        _org = str(
            hi.get("commissionOrganization")
            or hi.get("commissionName")
            or hi.get("entrustOrganization")
            or hi.get("commission")
            or ""
        ).strip()
    else:
        _org = _hosp
    put(
        ["委托单位名称", "委托单位_委托单位名称", "commissionOrganization"],
        _org,
    )
    put(
        ["设备型号", "型号", "设备规格型号", "model"],
        ei.get("deviceModel") or ei.get("model"),
    )
    serial = ei.get("serialNo")
    if serial in (None, ""):
        serial = ei.get("noDevice")
    put(["设备编号", "序列号", "SN", "产品编号", "设备序列号"], serial)
    mfr = ei.get("manufacturer")
    if mfr in (None, ""):
        mfr = ei.get("manufacturerProduction")
    put(["生产厂家", "制造商", "生产厂", "企业名称", "manufacturer"], mfr)
    put(["设备所在场所", "所在场所", "场所", "安装地点", "使用场所"], ei.get("location"))
    put(["设备名称", "仪器名称"], ei.get("deviceName"))
    put(["额定参数", "额定"], ei.get("ratedParams"))
    # 报告 PDF 常见域名「额定参数_kV/_mA」；f14/f15 与 schema 中 kv/ma 复用会被判为 ambiguous，dynamicData 直挂被跳过
    rated_kv = ei.get("kv")
    if rated_kv in (None, ""):
        rated_kv = tr.get("kv")
    rated_ma = ei.get("ma")
    if rated_ma in (None, ""):
        rated_ma = tr.get("ma")
    put(
        [
            "额定参数_kV",
            "额定kV",
            "额定 kV",
            "testResult.kv",
            "equipmentInfo.kv",
            "ratedkV",
        ],
        rated_kv,
    )
    put(
        [
            "额定参数_mA",
            "额定mA",
            "额定 mA",
            "testResult.ma",
            "equipmentInfo.ma",
            "ratedmA",
        ],
        rated_ma,
    )


def _inject_radio_enum_slug_checkbox_aliases(value_mapping: dict, source_data: dict) -> None:
    """
    互斥 radio 的枚举 value 常只在 dynamicData（如 f86、f84）或根 hospitalInfo 中；
    为报告占位 keyword 补缺「验收检测/状态检测」「同受检单位」等布尔键（不覆盖已有非空）。
    """
    if not isinstance(source_data, dict) or not isinstance(value_mapping, dict):
        return
    hi = source_data.get("hospitalInfo") if isinstance(source_data.get("hospitalInfo"), dict) else {}
    dd = source_data.get("dynamicData") if isinstance(source_data.get("dynamicData"), dict) else {}
    tt = str(hi.get("testType") or dd.get("f86") or "").strip().lower()
    if tt == "acceptance":
        value_mapping.setdefault("验收检测", True)
        value_mapping.setdefault("状态检测", False)
    elif tt == "status":
        value_mapping.setdefault("验收检测", False)
        value_mapping.setdefault("状态检测", True)
    com_lc = str(hi.get("commissionOrgMode") or dd.get("f84") or "").strip().lower().replace(" ", "")
    if com_lc == "customcommission":
        value_mapping.setdefault("同受检单位", False)
    elif com_lc == "sameinspection":
        value_mapping.setdefault("同受检单位", True)


def _inject_environment_location_backfill_aliases(value_mapping: dict, source_data: dict) -> None:
    """
    温湿度、设备所在场所：新旧 submit 键与前端字段 id 互认，仅补缺已有映射中的空位，避免回填串位或落空。
    - 温度：testResult.temperature ↔ reportInfo.environmentTempC
    - 湿度：testResult.humidity ↔ reportInfo.environmentHumidity
    - 场所：equipmentInfo.location ↔ equipmentInfo.device4
    """
    tr = source_data.get("testResult") if isinstance(source_data.get("testResult"), dict) else {}
    ri = source_data.get("reportInfo") if isinstance(source_data.get("reportInfo"), dict) else {}
    ei = source_data.get("equipmentInfo") if isinstance(source_data.get("equipmentInfo"), dict) else {}

    temp = tr.get("temperature")
    if temp in (None, ""):
        temp = ri.get("environmentTempC")
    if temp not in (None, ""):
        s = str(temp).strip()
        if s:
            for key in (
                "testResult.temperature",
                "reportInfo.environmentTempC",
                "environmentTempC",
                "temperature",
                "环境温度/湿度_°C",
            ):
                if value_mapping.get(key) in (None, ""):
                    value_mapping[key] = s

    hum = tr.get("humidity")
    if hum in (None, ""):
        hum = ri.get("environmentHumidity")
    if hum not in (None, ""):
        s = str(hum).strip()
        if s:
            for key in (
                "testResult.humidity",
                "reportInfo.environmentHumidity",
                "environmentHumidity",
                "humidity",
                "RH",
                "环境温度/湿度_%RH",
            ):
                if value_mapping.get(key) in (None, ""):
                    value_mapping[key] = s

    loc = ei.get("location")
    if loc in (None, ""):
        loc = ei.get("device4")
    if loc not in (None, ""):
        s = str(loc).strip()
        if s:
            for key in (
                "equipmentInfo.location",
                "equipmentInfo.device4",
                "location",
                "device4",
                "设备所在场所",
            ):
                if value_mapping.get(key) in (None, ""):
                    value_mapping[key] = s


def _abc_reading_value_at(raw: object, index_zero_based: int) -> object | None:
    """c1Readings / c2Readings 可能是 list 或 dict（下标 0/1/2 或 \"0\"/\"1\"/\"2\"）。"""
    if isinstance(raw, list):
        if 0 <= index_zero_based < len(raw):
            v = raw[index_zero_based]
            return None if v in (None, "") else v
        return None
    if isinstance(raw, dict):
        for key in (index_zero_based, str(index_zero_based)):
            if key in raw and raw[key] not in (None, ""):
                return raw[key]
        return None
    return None


def _inject_abc_circled_reading_aliases(value_mapping: dict, source_data: dict | None = None) -> None:
    """
    默认映射使用 abc_c1Readings_* / abc_c1Average 等英文键；JS-001 等 PDF 模板使用中文长 id（含 ①②③ 或 _C1/_C2）。
    仅补缺，不覆盖已有非空映射。
    """
    if isinstance(source_data, dict):
        tr = source_data.get("testResult")
        if isinstance(tr, dict):
            abc = tr.get("abc")
            if isinstance(abc, dict):
                for alts, prefix_en in (
                    (("c1Readings", "c1_readings"), "abc_c1Readings_"),
                    (("c2Readings", "c2_readings"), "abc_c2Readings_"),
                ):
                    raw_list = None
                    for ak in alts:
                        if ak in abc and abc.get(ak) is not None:
                            raw_list = abc.get(ak)
                            break
                    if raw_list is None:
                        continue
                    for j in range(3):
                        sk = f"{prefix_en}{j + 1}"
                        if value_mapping.get(sk) not in (None, ""):
                            continue
                        v = _abc_reading_value_at(raw_list, j)
                        if v not in (None, ""):
                            value_mapping[sk] = str(v).strip()

    circled = ("\u2460", "\u2461", "\u2462")
    pairs = (
        ("abc_c1Readings_", "自动亮度控制_20mmAl时的亮度C1（cd/m^2）_检测结果_"),
        ("abc_c2Readings_", "自动亮度控制_20mmAl1.5mmCu时的亮度C2（cd/m^2）_检测结果_"),
    )
    for prefix_en, prefix_cn in pairs:
        for i, circ in enumerate(circled, start=1):
            src_k = f"{prefix_en}{i}"
            dst_k = f"{prefix_cn}{circ}"
            if value_mapping.get(dst_k) not in (None, ""):
                continue
            v = value_mapping.get(src_k)
            if v in (None, ""):
                continue
            value_mapping[dst_k] = v

    scalar_pairs = (
        ("abc_c1Average", "自动亮度控制_20mmAl时的亮度C1（cd/m^2）_检测结果_C1"),
        ("abc_c2Average", "自动亮度控制_20mmAl1.5mmCu时的亮度C2（cd/m^2）_检测结果_C2"),
        ("abc_cAverage", "自动亮度控制_检测结果_平均值"),
        ("abc_report", "自动亮度控制_报出值_%"),
        ("abc_calc", "自动亮度控制_计算结果_Ec（%）"),
    )
    for src_k, dst_k in scalar_pairs:
        if value_mapping.get(dst_k) not in (None, ""):
            continue
        v = value_mapping.get(src_k)
        if v in (None, ""):
            continue
        value_mapping[dst_k] = v


def _inject_unified_template_brightness_submitpath_aliases(
    value_mapping: dict, source_data: dict | None = None
) -> None:
    """
    JS-001（unified_form_template/v2）：前端 steps 里 ①②③ 的 submitPath 为 testResult.field101/102/103、
    C2 行为 field107/108/109；PDF 域 id/placeholder 却是长中文。报告模板坐标 fields 通常不带 source.submitPath，
    且 pdfFieldId（f1、f2…）多页重复时 dynamicData 无法可靠挂到长 id。
    将 testResult.fieldN（value_mapping 或原始 submit）写到 PDF 长 placeholder 键；有值则覆盖，以前端提交为准。
    """
    rows = (
        ("testResult.field101", "自动亮度控制_20mmAl时的亮度C1（cd/m^2）_检测结果_①"),
        ("testResult.field102", "自动亮度控制_20mmAl时的亮度C1（cd/m^2）_检测结果_②"),
        ("testResult.field103", "自动亮度控制_20mmAl时的亮度C1（cd/m^2）_检测结果_③"),
        ("testResult.field107", "自动亮度控制_20mmAl1.5mmCu时的亮度C2（cd/m^2）_检测结果_①"),
        ("testResult.field108", "自动亮度控制_20mmAl1.5mmCu时的亮度C2（cd/m^2）_检测结果_②"),
        ("testResult.field109", "自动亮度控制_20mmAl1.5mmCu时的亮度C2（cd/m^2）_检测结果_③"),
        ("testResult.c1", "自动亮度控制_20mmAl时的亮度C1（cd/m^2）_检测结果_C1"),
        ("testResult.c2", "自动亮度控制_20mmAl1.5mmCu时的亮度C2（cd/m^2）_检测结果_C2"),
        ("testResult.field106", "自动亮度控制_检测结果_平均值"),
        ("testResult.field97", "自动亮度控制_报出值_%"),
        ("testResult.field104", "自动亮度控制_计算结果_Ec（%）"),
    )
    for src_k, dst_k in rows:
        v = value_mapping.get(src_k)
        if (v is None or v == "") and isinstance(source_data, dict):
            v = _nested_get_for_submit(source_data, src_k)
        if v is None or v == "" or isinstance(v, (dict, list)):
            continue
        s = str(v).strip()
        if not s:
            continue
        value_mapping[dst_k] = s


# 用于从 value_mapping 键名反推行前缀 P；**必须按更长后缀优先匹配**（见 _find_best_site_row_prefix_for_report_base）。
_QC_SITE_ROW_SUFFIX_MARKERS: tuple[str, ...] = (
    "_检测结果_kV",
    "_检测结果_mA",
    "_检测条件_kV",
    "_检测条件_mA",
    "_检测条件_s",
    "_检测结果_s",
    "_检测条件_mm",
    "_检测结果_mm",
    "_计算结果",
    "_报出值",
    "_检测值",
    "_检测结果",
)

_QC_SITE_ROW_SUFFIX_MARKERS_BY_LEN: tuple[str, ...] = tuple(
    sorted(_QC_SITE_ROW_SUFFIX_MARKERS, key=len, reverse=True)
)

# 报告 PDF 与现场记录对「透视防护区…术者位」行词条命名不一致时的双向对齐（避免串位到其它术者位/高度）。
_SHIELD_ZONE_OP_SYNONYMS: tuple[tuple[str, str], ...] = (
    ("床侧第一术者位（距球管中心60cm处）", "床侧第一术者位60cm（距球管中心处）"),
    ("床侧第二术者位（距球管中心120cm处）", "床侧第二术者位120cm（距球管中心处）"),
)
_SHIELD_ZONE_BODY_SYNONYMS: tuple[tuple[str, str], ...] = (
    ("_足部_", "_20cm（足部）_"),
    ("_下肢_", "_80cm（下肢）_"),
    ("_腹部_", "_105cm（腹部）_"),
    ("_胸部_", "_125cm（胸部）_"),
    ("_头部_", "_155cm（头部）_"),
)


def _shield_zone_placeholder_key_variants(key: str) -> set[str]:
    if "透视防护区检测平面上周围剂量当量率" not in key:
        return {key}
    out: set[str] = {key}
    changed = True
    while changed:
        changed = False
        for cur in tuple(out):
            for a, b in _SHIELD_ZONE_OP_SYNONYMS:
                if a in cur:
                    nxt = cur.replace(a, b)
                    if nxt not in out:
                        out.add(nxt)
                        changed = True
                if b in cur:
                    nxt = cur.replace(b, a)
                    if nxt not in out:
                        out.add(nxt)
                        changed = True
    for cur in tuple(out):
        for s, t in _SHIELD_ZONE_BODY_SYNONYMS:
            if s in cur:
                out.add(cur.replace(s, t))
            if t in cur:
                out.add(cur.replace(t, s))
    return out


def _inject_shield_zone_report_site_key_aliases(value_mapping: dict) -> None:
    """同一测量值在报告/现场模板下的占位符键名不同，仅向空键补缺别名，减少相似度误配。"""
    if not isinstance(value_mapping, dict):
        return
    marker = "透视防护区检测平面上周围剂量当量率"
    if not any(isinstance(k, str) and marker in k for k in value_mapping.keys()):
        return
    for k, v in list(value_mapping.items()):
        if not isinstance(k, str) or marker not in k:
            continue
        if v in (None, "") or isinstance(v, (dict, list)):
            continue
        for alt in _shield_zone_placeholder_key_variants(k):
            if alt == k:
                continue
            if value_mapping.get(alt) in (None, ""):
                value_mapping[alt] = v


_SHIELD_ZONE_WHOLE_ROW_TAILS: tuple[str, ...] = tuple(
    sorted(
        ("_单项判定", "_判定标准", "_检测结果", "_计算结果", "_报出值", "_检测值", "_检测条件"),
        key=len,
        reverse=True,
    )
)


def _shield_zone_split_row_tail(key: str) -> tuple[str, str] | None:
    """术者位防护区整行类键：拆成「列前缀+术者/体位段」与行尾（_检测结果 等）。"""
    if not isinstance(key, str) or "透视防护区检测平面上周围剂量当量率" not in key:
        return None
    if "术者位" not in key:
        return None
    for t in _SHIELD_ZONE_WHOLE_ROW_TAILS:
        if key.endswith(t):
            return key[: -len(t)], t
    return None


def _shield_zone_row_identity_tuple(key: str) -> tuple[str, str, str, str] | None:
    """
    将「距球管中心 60/120/141cm」及「_足部_ / _20cm（足部）」等异名统一为同一身份，
    供跨模板键名复制测量值（报告 PDF 与现场词条距离数字常不一致）。
    返回 (列前缀如 透视…(μSv/h), OP1|OP2, 体位码, 行尾如 _检测结果)。
    """
    sp = _shield_zone_split_row_tail(key)
    if not sp:
        return None
    base, tail = sp
    col = _shield_zone_rate_column_prefix(base)
    if not col:
        return None
    mid = base[len(col) :].lstrip("_")
    if "第一术者位" in mid or ("第一" in mid and "术者位" in mid):
        op = "OP1"
    elif "第二术者位" in mid or ("第二" in mid and "术者位" in mid):
        op = "OP2"
    else:
        return None
    body = "BLOCK"
    if "_足部" in mid or "_20cm（足部）" in mid:
        body = "FOOT"
    elif "_下肢" in mid or "_80cm（下肢）" in mid:
        body = "LEG"
    elif "_腹部" in mid or "_105cm（腹部）" in mid:
        body = "ABD"
    elif "_胸部" in mid or "_125cm（胸部）" in mid:
        body = "CHST"
    elif "_头部" in mid or "_155cm（头部）" in mid:
        body = "HEAD"
    return (col, op, body, tail)


_SHIELD_ZONE_MEASUREMENT_ROW_SUFFIXES: tuple[str, ...] = tuple(
    sorted(("_检测结果", "_计算结果", "_报出值", "_检测值"), key=len, reverse=True)
)


def _shield_zone_match_site_prefix_for_report_row_base(row_base: str, vm: dict) -> str | None:
    """
    报告行基名与现场键仅「距球管中心 N cm」等表述不同时，用术者位+体位身份对齐现场行前缀 P。
    """
    if not row_base or not isinstance(vm, dict):
        return None
    probe = f"{str(row_base).strip()}_检测结果"
    idt = _shield_zone_row_identity_tuple(probe)
    if not idt:
        return None
    loc = (idt[0], idt[1], idt[2])
    for k in vm.keys():
        if not isinstance(k, str):
            continue
        oidt = _shield_zone_row_identity_tuple(k)
        if not oidt or (oidt[0], oidt[1], oidt[2]) != loc:
            continue
        for suf in _SHIELD_ZONE_MEASUREMENT_ROW_SUFFIXES:
            if k.endswith(suf):
                return k[: -len(suf)]
    return None


def _inject_shield_zone_distance_agnostic_aliases(value_mapping: dict) -> None:
    """
    报告行写「（距球管中心141cm处）_足部」，现场写「60cm（距球管中心处）_20cm（足部）」时，
    仅凭字符串相似度无法对齐；按术者位次序+高度段身份把已有非 junk 值复制到同身份的其它键。
    """
    if not isinstance(value_mapping, dict):
        return
    marker = "透视防护区检测平面上周围剂量当量率"
    by_id: dict[tuple[str, str, str, str], list[str]] = {}
    for k in value_mapping.keys():
        if not isinstance(k, str) or marker not in k:
            continue
        idt = _shield_zone_row_identity_tuple(k)
        if not idt:
            continue
        by_id.setdefault(idt, []).append(k)

    def _scalar_ok(v) -> bool:
        if v in (None, "") or isinstance(v, (dict, list, bool)):
            return False
        return not _value_looks_like_unresolved_qc_domain_id(v)

    for _idt, ks in by_id.items():
        good: list[tuple[str, str]] = []
        for k in ks:
            v = value_mapping.get(k)
            if _scalar_ok(v):
                good.append((k, str(v).strip()))
        if not good:
            continue
        tail = _idt[3]
        picked = good[0][1]
        if tail in ("_检测结果", "_检测值", "_报出值", "_计算结果"):
            for _k, sv in good:
                if re.fullmatch(r"-?\d+(\.\d+)?", sv):
                    picked = sv
                    break
            else:
                picked = good[0][1]
        for k in ks:
            cur = value_mapping.get(k)
            if _scalar_ok(cur):
                continue
            value_mapping[k] = picked


def _shield_zone_rate_column_prefix(base: str) -> str | None:
    """从行基名取「透视防护区…(μSv/h)」列前缀，用于共用检测条件 kV/mA。"""
    m = re.match(r"(透视防护区检测平面上周围剂量当量率\([^)]+\))", str(base or "").strip())
    return m.group(1) if m else None


def _shield_zone_base_has_body_height_token(base: str) -> bool:
    """是否已带到足部/高度段（非术者位块级检测条件）。"""
    b = str(base or "")
    return bool(
        re.search(r"_(20cm|80cm|105cm|125cm|155cm)（(?:足部|下肢|腹部|胸部|头部)）", b)
        or re.search(r"_(足部|下肢|腹部|胸部|头部)(_|$)", b)
    )


def _qc_row_prefix_strip_unit_parens(s: str) -> str:
    """透视…典型值/(mGy/min) 与 透视…典型值 对齐比较时去掉 /(单位) 与空白。"""
    t = str(s or "").strip()
    t = re.sub(r"/\([^)/]*\)", "", t)
    t = re.sub(r"（[^）/]*）", "", t)
    return re.sub(r"\s+", "", t)


def _truthy_checkbox_value(v) -> bool:
    if v is True or v == 1:
        return True
    s = str(v).strip().lower()
    return s in {"1", "true", "yes", "on", "是"}


# 库报告 JS-001 等：典型值/最大值「单位」互斥三勾在 PDF 上为 f93–f95、f97–f99…，
# 提交 dynamicData 常在每行「首格」写整组 radio 枚举（如仅 f93），须向同组空键复制供按 pdfFieldId 取值。
_DOSE_RATE_UNIT_LIB_PDF_MUTEX_TRIPLETS: tuple[tuple[str, str, str], ...] = (
    ("f93", "f94", "f95"),
    ("f97", "f98", "f99"),
    ("f103", "f104", "f105"),
    ("f117", "f118", "f119"),
)

_F_PDF_FIELD_ID_NUM = re.compile(r"^f(\d+)$", re.I)

_KERMA_MAX_HIGH_DOSE_MODE_PDF_YES = (
    "透视受检者入射体表空气比释动能率最大/(mGy/min)（仅验收检测）_高剂量率模式_是"
)
_KERMA_MAX_HIGH_DOSE_MODE_PDF_NO = (
    "透视受检者入射体表空气比释动能率最大/(mGy/min)（仅验收检测）_高剂量率模式_否"
)

# 库 JS-001 PDF：统一表单用 f122 / f124 单选 yesNo，库模板为「是/否」或「有/无」双勾且 f 号与前端签名图等可能冲突，用长 id + 空闲 f 槽补缺。
_LIB_JS001_DSA_SECTION_YES = "以下仅为DSA设备检测项目_是"
_LIB_JS001_DSA_SECTION_NO = "以下仅为DSA设备检测项目_否"
_LIB_JS001_ARTIFACT_NO = "伪影_检测结果_减影中是否有各种明显伪影_无"
_LIB_JS001_ARTIFACT_YES = "伪影_检测结果_减影中是否有各种明显伪影_有"


def _yesno_sl_norm(raw: object) -> str:
    return re.sub(r"\s+", "", str(raw).strip().lower()) if raw not in (None, "") else ""


def _yesno_pick_is_yes(sl: str) -> bool:
    return sl in ("yes", "y", "是", "1", "true", "on", "有")


def _yesno_pick_is_no(sl: str) -> bool:
    return sl in ("no", "n", "否", "0", "false", "off", "无")


def _pdf_f_slot_likely_image_payload(val) -> bool:
    if not isinstance(val, str):
        return False
    t = val.strip()
    return bool(t.startswith("iVBOR") or t.startswith("/9j"))


def _inject_unified_yesno_to_library_dsa_artifact_pdf_aliases(
    value_mapping: dict, source_data: dict
) -> None:
    """
    统一表单 `testResult.dsaEquipmentSectionApplicable` / `yesnoP3R0` 或 dynamicData f122、f124
    与库 PDF 双勾 id 对齐；避免 f122=yes 被当成剂量单位 slug 导致整组单位勾全亮，并修复伪影有/无同勾。
    """
    if not isinstance(value_mapping, dict) or not isinstance(source_data, dict):
        return
    tr = source_data.get("testResult") if isinstance(source_data.get("testResult"), dict) else {}
    dd = source_data.get("dynamicData") if isinstance(source_data.get("dynamicData"), dict) else {}

    raw_dsa = tr.get("dsaEquipmentSectionApplicable")
    if raw_dsa in (None, ""):
        raw_dsa = dd.get("f122")
    if raw_dsa in (None, ""):
        raw_dsa = value_mapping.get("f122")
    if raw_dsa not in (None, ""):
        sl = _yesno_sl_norm(raw_dsa)
        if _yesno_pick_is_yes(sl) or _yesno_pick_is_no(sl):
            yes = _yesno_pick_is_yes(sl)
            value_mapping.setdefault(_LIB_JS001_DSA_SECTION_YES, "yes" if yes else "no")
            value_mapping.setdefault(_LIB_JS001_DSA_SECTION_NO, "yes" if yes else "no")
            if not _pdf_f_slot_likely_image_payload(value_mapping.get("f127")):
                value_mapping.setdefault("f127", "yes" if yes else "no")
            if not _pdf_f_slot_likely_image_payload(value_mapping.get("f128")):
                value_mapping.setdefault("f128", "yes" if yes else "no")

    raw_art = tr.get("yesnoP3R0")
    if raw_art in (None, ""):
        raw_art = dd.get("f124")
    if raw_art in (None, ""):
        raw_art = value_mapping.get("f124")
    if raw_art not in (None, ""):
        sl = _yesno_sl_norm(raw_art)
        if _yesno_pick_is_yes(sl) or _yesno_pick_is_no(sl):
            yes = _yesno_pick_is_yes(sl)
            value_mapping.setdefault(_LIB_JS001_ARTIFACT_YES, "yes" if yes else "no")
            value_mapping.setdefault(_LIB_JS001_ARTIFACT_NO, "yes" if yes else "no")
            if not _pdf_f_slot_likely_image_payload(value_mapping.get("f129")):
                value_mapping.setdefault("f129", "yes" if yes else "no")
            if not _pdf_f_slot_likely_image_payload(value_mapping.get("f130")):
                value_mapping.setdefault("f130", "yes" if yes else "no")


def _dose_rate_submit_value_to_enum_slug(raw: object) -> str | None:
    """将提交中的剂量率单位规范为 mgypermin / ugypermin / ugypersec / ngypersec；无法识别则 None。"""
    if raw in (None, "") or isinstance(raw, (dict, list, bool)):
        return None
    if isinstance(raw, (int, float)):
        return None
    s = str(raw).strip()
    if not s:
        return None
    sl = re.sub(r"\s+", "", s.lower().replace("μ", "u").replace("µ", "u"))
    if sl in _DOSE_RATE_UNIT_ENUM_SLUGS:
        return sl
    slash = {
        "mgy/min": "mgypermin",
        "ugy/min": "ugypermin",
        "ugy/s": "ugypersec",
        "ngy/s": "ngypersec",
    }
    if sl in slash:
        return slash[sl]
    return None


def _iter_dose_rate_unit_mutex_triplets_from_steps(site_steps_merged: list | None) -> list[tuple[str, str, str]]:
    """
    从统一表单 steps 中 enumRef=doseRateUnit 的 radio 取首格 pdfFieldId（如 f103），
    假定同格三勾在 PDF 上为连续 fN、fN+1、fN+2（与 htmlpdf 坐标 boxing 一致）。
    """
    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    if not site_steps_merged:
        return out
    for fld in _flatten_unified_form_steps_to_fields_deep(site_steps_merged):
        if not isinstance(fld, dict):
            continue
        if str(fld.get("type") or "").lower() != "radio":
            continue
        if str(fld.get("enumRef") or "").strip() != "doseRateUnit":
            continue
        src = fld.get("source") if isinstance(fld.get("source"), dict) else {}
        pid = str(src.get("pdfFieldId") or fld.get("pdfFieldId") or "").strip()
        m = _F_PDF_FIELD_ID_NUM.match(pid)
        if not m:
            continue
        n = int(m.group(1))
        tup = (f"f{n}", f"f{n + 1}", f"f{n + 2}")
        if tup not in seen:
            seen.add(tup)
            out.append(tup)
    return out


def _pdf_field_slot_eligible_for_dose_rate_unit_enum_mirror(
    key: str, dd: dict, value_mapping: dict
) -> bool:
    """
    仅当 f 号在提交与映射中均为空、或已是剂量单位枚举时，才允许从同组首格复制枚举；
    避免把质控读数（整数）、控制方式（auto）、其它短串误改成 uGyPerSec 等导致整组勾框全亮或串味。
    """
    if not key:
        return False
    for bag in (value_mapping, dd):
        if not isinstance(bag, dict):
            continue
        raw = bag.get(key)
        if raw in (None, ""):
            continue
        if isinstance(raw, (dict, list)):
            return False
        if isinstance(raw, bool):
            return False
        if isinstance(raw, (int, float)):
            return False
        st = str(raw).strip()
        if not st:
            continue
        if _dose_rate_submit_value_to_enum_slug(st) is not None:
            return True
        sl = re.sub(r"\s+", "", st.lower().replace("μ", "u").replace("µ", "u"))
        if sl in ("auto", "manual", "yes", "no", "acceptance", "status", "customcommission", "sameinspection"):
            return False
        if st.isdigit():
            return False
        return False
    return True


def _inject_dose_rate_unit_mutex_pdf_aliases(
    value_mapping: dict,
    source_data: dict,
    *,
    extra_triplets: Sequence[tuple[str, str, str]] | None = None,
) -> None:
    """互斥单位三勾：提交只写首 pdfFieldId 时，向同组另两格补缺同一枚举字符串（不覆盖已有非空）。"""
    if not isinstance(value_mapping, dict) or not isinstance(source_data, dict):
        return
    dd = source_data.get("dynamicData") if isinstance(source_data.get("dynamicData"), dict) else {}
    merged_rows: list[tuple[str, str, str]] = []
    seen_row: set[tuple[str, str, str]] = set()
    for row in tuple(extra_triplets or ()) + _DOSE_RATE_UNIT_LIB_PDF_MUTEX_TRIPLETS:
        if row not in seen_row:
            seen_row.add(row)
            merged_rows.append(row)
    for a, b, c in merged_rows:
        v = value_mapping.get(a)
        if v in (None, ""):
            v = dd.get(a)
        if v in (None, ""):
            continue
        s = str(v).strip()
        if _dose_rate_submit_value_to_enum_slug(s) is None:
            continue
        if value_mapping.get(b) in (None, "") and _pdf_field_slot_eligible_for_dose_rate_unit_enum_mirror(
            b, dd, value_mapping
        ):
            value_mapping[b] = s
        if value_mapping.get(c) in (None, "") and _pdf_field_slot_eligible_for_dose_rate_unit_enum_mirror(
            c, dd, value_mapping
        ):
            value_mapping[c] = s


def _inject_kerma_max_high_dose_mode_pdf_aliases(value_mapping: dict, source_data: dict) -> None:
    """
    库 PDF「透视…最大…（仅验收检测）」行：高剂量率模式 是/否 常为 f101、f102；
    统一表单多把 yes/no 写在 f101。补缺 f102 及长 id 键，避免「否」格或长占位符落空。
    """
    if not isinstance(value_mapping, dict) or not isinstance(source_data, dict):
        return
    dd = source_data.get("dynamicData") if isinstance(source_data.get("dynamicData"), dict) else {}
    raw = dd.get("f101")
    if raw in (None, ""):
        raw = value_mapping.get("f101")
    if raw in (None, ""):
        return
    sl = re.sub(r"\s+", "", str(raw).strip().lower())
    yes = sl in ("yes", "y", "是", "1", "true", "on")
    no = sl in ("no", "n", "否", "0", "false", "off")
    if not yes and not no:
        return
    if dd.get("f102") in (None, "") and value_mapping.get("f102") in (None, ""):
        value_mapping.setdefault("f102", "no" if yes else "yes")
    if yes:
        value_mapping.setdefault(_KERMA_MAX_HIGH_DOSE_MODE_PDF_YES, "yes")
        value_mapping.setdefault(_KERMA_MAX_HIGH_DOSE_MODE_PDF_NO, "yes")
    else:
        value_mapping.setdefault(_KERMA_MAX_HIGH_DOSE_MODE_PDF_YES, "no")
        value_mapping.setdefault(_KERMA_MAX_HIGH_DOSE_MODE_PDF_NO, "no")


def _dose_rate_unit_checkbox_pair_coerce(cp: dict | None, sl: str) -> bool | None:
    """
    模板 checkboxPair.pairKind=dose_rate_unit 时，仅按 option 列与提交枚举比对。
    行 id 中普遍含「/(mGy/min)」等字样，不得用整段 blob 是否含 mgy/min 判断（否则三格同勾）。
    """
    if not isinstance(cp, dict):
        return None
    if str(cp.get("pairKind") or "").strip().lower() != "dose_rate_unit":
        return None
    opt = str(cp.get("option") or "").strip()
    if not opt:
        return None
    ol = opt.replace("μ", "u").replace(" ", "").lower()
    if ol == "ugy/s":
        return sl == "ugypersec"
    if ol == "ugy/min":
        return sl == "ugypermin"
    if ol == "mgy/min":
        return sl == "mgypermin"
    if ol == "ngy/s":
        return sl == "ngypersec"
    if sl in ("mgypermin", "ugypermin", "ugypersec", "ngypersec"):
        return False
    return None


def _coerce_picked_value_for_pdf_checkbox(field: dict, picked) -> bool:
    """
    报告 PDF 勾框回填：不得对任意非空字符串做 bool()（否则 controlMode 枚举如 auto/manual 均为 True，
    「自动控制」「手动控制」两个独立 check 会同时被勾选）。

    前端互斥 radio 常提交枚举 value（acceptance/status、yes/no、auto/manual、mGyPerMin 等），
    与 PDF 占位「验收检测」「是/否」「μGy/min」等对齐；其中 yes/no 须在将「no」判为假之前按域语义处理。
    """
    if picked is True or picked == 1:
        return True
    if picked is False or picked in (0, None):
        return False
    s = str(picked).strip()
    if not s:
        return False
    sl = s.lower()
    dose_slug = _dose_rate_submit_value_to_enum_slug(s)
    if dose_slug:
        sl = dose_slug
    parts: list[str] = []
    for attr in (
        "originalPlaceholder",
        "placeholder",
        "id",
        "title",
        "label",
        "fieldId",
        "pdfFieldId",
        "name",
    ):
        t = field.get(attr)
        if isinstance(t, str) and t.strip():
            parts.append(t.strip())
    src = field.get("source")
    src_d = src if isinstance(src, dict) else {}
    if src_d:
        for attr in ("placeholder", "title", "label", "key", "submitPath"):
            t = src_d.get(attr)
            if isinstance(t, str) and t.strip():
                parts.append(t.strip())
    blob = " ".join(parts)
    blob_lc = blob.lower().replace(" ", "")
    bns = re.sub(r"\s+", "", blob)

    # 减影伪影「有 / 无」：id 以 _有/_无 结尾，与 yes/no 枚举对齐（避免 yes 让「无」也判真导致双勾）
    if "伪影" in blob and ("明显伪影" in blob or "减影" in blob):
        if bns.endswith("无") or bns.endswith("_无"):
            return sl in ("no", "n", "否", "0", "false", "off", "无")
        if bns.endswith("有") or bns.endswith("_有"):
            return sl in ("yes", "y", "是", "1", "true", "on", "有")

    # yesNo：是 / 否 两个勾框（picked 同为 yes 或 no）
    if "是否" not in bns:
        if bns.endswith("否") or bns.endswith("_否") or "□否" in blob or blob.strip() == "否":
            return sl in ("no", "否", "0", "false", "off")
        if bns.endswith("是") or bns.endswith("_是") or "□是" in blob or blob.strip() == "是":
            return sl in ("yes", "是", "1", "true", "on", "开")

    if "状态检测" in blob:
        return sl == "status" or s == "状态检测"
    if "验收检测" in blob:
        return sl == "acceptance" or s == "验收检测"

    if "同受检单位" in blob:
        return sl in ("sameinspection", "same_inspection", "sameinspectionunit")
    if "委托单位" in blob and not any(
        x in blob for x in ("联系人", "名称", "电话", "编号", "地址", "委托编号")
    ):
        return sl in ("customcommission", "custom_commission")

    cp0 = field.get("checkboxPair") if isinstance(field.get("checkboxPair"), dict) else None
    cpk = str(cp0.get("pairKind") or "").strip().lower() if cp0 else ""
    if cp0 and cpk == "dose_rate_unit":
        dslug = _dose_rate_submit_value_to_enum_slug(s)
        if dslug:
            hit = _dose_rate_unit_checkbox_pair_coerce(cp0, dslug)
            if hit is not None:
                return hit
        return False

    if sl in ("mgypermin", "ugypermin", "ugypersec", "ngypersec"):
        bn = blob.replace("μ", "u").replace(" ", "").lower()
        if sl == "mgypermin":
            if "mgy/min" in bn:
                return True
            return "mgy" in bn and "min" in bn and "ugy" not in bn
        if sl == "ugypermin":
            return "ugy/min" in bn
        if sl == "ugypersec":
            return "ugy/s" in bn
        if sl == "ngypersec":
            return "ngy/s" in bn or ("ngy" in bn and "/s" in bn and "ugy" not in bn)

    sp_lc = str(src_d.get("submitPath") or "").lower()
    fid0 = str(field.get("fieldId") or field.get("id") or "").lower()
    if sl in ("auto", "manual"):
        if "手动控制" in blob or "manualcontrol" in blob_lc or "manual_control" in blob_lc:
            return sl == "manual" or s in {"手动控制", "手动"}
        if "自动控制" in blob or "autocontrol" in blob_lc or "auto_control" in blob_lc:
            return sl in ("auto", "automatic") or s in {"自动控制", "自动"}
        if "manual" in fid0 and "controlmode" in sp_lc.replace(".", ""):
            return sl == "manual" or s in {"手动控制", "手动"}
        if "auto" in fid0 and "manual" not in fid0 and "controlmode" in sp_lc.replace(".", ""):
            return sl in ("auto", "automatic") or s in {"自动控制", "自动"}

    if sl in {"0", "false", "no", "off", "否", "关"}:
        return False
    if sl in {"1", "true", "yes", "on", "是", "开"}:
        return True
    # KAP 面积乘积单位：提交常为枚举串，PDF 为四个独立勾选项。
    # 1) blob 仅有 pdfFieldId、无「KAP指示偏离」长 id 时，用 f55–f58 与枚举直接对齐；
    # 2) 长 id 存在时仍按尾部 mGycm^2 / mGym^2 等与提交 slug 比对（兼容 ² 与 ^2）。
    pid_kap = str(field.get("pdfFieldId") or src_d.get("pdfFieldId") or "").strip().lower()
    kap_fid_to_slug = {"f55": "mGy_cm2", "f56": "mGy_m2", "f57": "uGy_m2", "f58": "uGy_cm2"}
    kap_slug_to_tail = {
        "mGy_cm2": "mGycm^2",
        "mGy_m2": "mGym^2",
        "uGy_m2": "μGym^2",
        "uGy_cm2": "μGycm^2",
    }

    def _kap_slug_from_picked_str(raw_s: str) -> str | None:
        t = str(raw_s or "").strip()
        if not t:
            return None
        tl = re.sub(r"\s+", "", t.lower().replace("μ", "u").replace("µ", "u"))
        for sk in kap_slug_to_tail:
            if tl == re.sub(r"\s+", "", sk.lower().replace("μ", "u").replace("µ", "u")):
                return sk
        return None

    def _kap_tail_in_blob(tail: str) -> bool:
        b0 = blob.replace("μ", "u").replace("µ", "u")
        t0 = tail.replace("μ", "u").replace("µ", "u")
        if tail in blob or t0 in b0:
            return True
        if "^2" in tail:
            alt = tail.replace("^2", "²")
            if alt in blob or alt.replace("μ", "u").replace("µ", "u") in b0:
                return True
        return False

    if pid_kap in kap_fid_to_slug:
        exp_slug = kap_fid_to_slug[pid_kap]
        ps_res = _kap_slug_from_picked_str(str(picked).strip()) if not isinstance(picked, bool) else None
        if ps_res is not None:
            return ps_res == exp_slug
    if "KAP指示偏离" in blob and "检测结果" in blob:
        ps = str(picked).strip()
        psn = _kap_slug_from_picked_str(ps) or (ps if ps in kap_slug_to_tail else None)
        if psn and psn in kap_slug_to_tail:
            tail = kap_slug_to_tail[psn]
            return _kap_tail_in_blob(tail)
    return _truthy_checkbox_value(picked)


def _value_mapping_scalar_str(value_mapping: dict, key: str) -> str:
    if not key or not isinstance(value_mapping, dict):
        return ""
    v = value_mapping.get(key)
    if v in (None, "") or isinstance(v, (dict, list, bool)):
        return ""
    return str(v).strip()


def _report_kerma_typical_vs_max_prefix_ok(report_base: str, site_pfx: str) -> bool:
    """透视比释动能「典型值」行与「最大值」行现场前缀必须互斥，避免相似度并列时串到同一套 kV/mA。"""
    rb = str(report_base or "")
    sp = str(site_pfx or "")
    if "典型值" in rb:
        if "最大" in sp and "典型" not in sp:
            return False
        return "典型" in sp or "典型值" in sp
    if "最大值" in rb:
        if "典型值" in sp and "最大" not in sp:
            return False
        return "最大" in sp
    return True


def _shield_zone_report_base_synonym_variants(report_base: str) -> list[str]:
    """术者位报告行名与现场模板用语不一致时，多_variant 参与相似度。"""
    rb = str(report_base or "").strip()
    if not rb or "透视防护区检测平面上周围剂量当量率" not in rb or "术者位" not in rb:
        return [rb]
    out: list[str] = [rb]
    for a, b in _SHIELD_ZONE_OP_SYNONYMS:
        if a in rb:
            out.append(rb.replace(a, b))
        if b in rb:
            out.append(rb.replace(b, a))
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        if x and x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def _find_best_site_row_prefix_for_report_base(report_base: str, value_mapping: dict) -> str | None:
    """
    报告格行名「透视…典型值」与现场 PDF 词条「透视…典型值/(mGy/min)_检测结果_kV」等通过去单位后的相似度对齐，
    返回现场侧行前缀 P（含 /(mGy/min) 等），用于拼检测条件或取计算结果/报出值。
    """
    if not report_base or not isinstance(value_mapping, dict):
        return None
    rb = _qc_row_prefix_strip_unit_parens(report_base)
    if not rb:
        return None
    rb_variants = [_qc_row_prefix_strip_unit_parens(x) for x in _shield_zone_report_base_synonym_variants(report_base)]
    rb_variants = [x for x in rb_variants if x]
    best_pfx: str | None = None
    best_sc = 0.0
    for k in value_mapping.keys():
        if not isinstance(k, str):
            continue
        matched_suf = None
        for suf in _QC_SITE_ROW_SUFFIX_MARKERS_BY_LEN:
            if k.endswith(suf):
                matched_suf = suf
                break
        if not matched_suf:
            continue
        pfx = k[: -len(matched_suf)]
        if not pfx:
            continue
        if not _report_kerma_typical_vs_max_prefix_ok(report_base, pfx):
            continue
        pb = _qc_row_prefix_strip_unit_parens(pfx)
        row_best = 0.0
        for rbn in rb_variants:
            sc = SequenceMatcher(None, rbn, pb).ratio()
            if rbn and (rbn in pb or pb in rbn):
                sc = max(sc, 0.93)
            row_best = max(row_best, sc)
        sc = row_best
        if sc < 0.68:
            continue
        if sc > best_sc + 1e-9:
            best_sc = sc
            best_pfx = pfx
        elif abs(sc - best_sc) < 1e-9 and (best_pfx is None or len(pfx) > len(best_pfx)):
            best_pfx = pfx
    return best_pfx


def _testresult_scalar(source_data: dict, *keys: str) -> str:
    tr = source_data.get("testResult") if isinstance(source_data.get("testResult"), dict) else {}
    for k in keys:
        v = tr.get(k)
        if v in (None, "") or isinstance(v, (dict, list, bool)):
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def _vm_scalar_first_key(vm: dict, keys: tuple[str, ...]) -> str:
    for k in keys:
        s = _value_mapping_scalar_str(vm, k)
        if s:
            return s
    return ""


def _vm_scalar_scan_prefix_suffix(
    vm: dict, prefix: str, suffix_options: tuple[str, ...]
) -> str:
    """在同一现场行前缀下，按后缀列举与键名扫描取值（表头可能在检测条件/检测结果列）。"""
    for suf in suffix_options:
        suf2 = suf if suf.startswith("_") else f"_{suf}"
        full = f"{prefix}{suf2}"
        s = _value_mapping_scalar_str(vm, full)
        if s:
            return s
    for k, raw in vm.items():
        if not isinstance(k, str):
            continue
        if raw in (None, "") or isinstance(raw, (dict, list, bool)):
            continue
        if not k.startswith(prefix):
            continue
        for suf in suffix_options:
            suf2 = suf if suf.startswith("_") else f"_{suf}"
            if k.endswith(suf2):
                return str(raw).strip()
    return ""


def _resolve_control_mode_label(P: str, vm: dict, source_data: dict) -> str:
    if _truthy_checkbox_value(vm.get(f"{P}_自动控制")) or _truthy_checkbox_value(
        vm.get(f"{P}_检测结果_自动控制")
    ) or _truthy_checkbox_value(vm.get(f"{P}_检测条件_自动控制")):
        if not _truthy_checkbox_value(vm.get(f"{P}_手动控制")) and not _truthy_checkbox_value(
            vm.get(f"{P}_检测结果_手动控制")
        ) and not _truthy_checkbox_value(vm.get(f"{P}_检测条件_手动控制")):
            return "自动控制"
    if _truthy_checkbox_value(vm.get(f"{P}_手动控制")) or _truthy_checkbox_value(
        vm.get(f"{P}_检测结果_手动控制")
    ) or _truthy_checkbox_value(vm.get(f"{P}_检测条件_手动控制")):
        return "手动控制"
    cm = _testresult_scalar(source_data, "controlmode10").lower()
    if cm == "auto":
        return "自动控制"
    if cm == "manual":
        return "手动控制"
    return ""


def _compose_qc_condition_multiline_block(
    site_prefix: str, value_mapping: dict, source_data: dict
) -> str | None:
    """
    检测条件多行块：
    第一行：自动控制/手动（若有）+ kV + mA（有则写，空格分隔）；
    第二行：平板探测器尺寸D=…mm（若有）；
    末行：标准水模。
    kV/mA/mm 可在「检测条件」或「检测结果」类域名下，按前缀 P 扫描。
    """
    P = site_prefix
    vm = value_mapping
    kv = _vm_scalar_first_key(
        vm,
        (
            f"{P}_检测结果_kV",
            f"{P}_检测条件_kV",
            f"{P}_kV",
        ),
    ) or _vm_scalar_scan_prefix_suffix(
        vm, P, ("_检测结果_kV", "_检测条件_kV", "_kV")
    )
    if not kv:
        kv = _testresult_scalar(source_data, "kv2", "kv")
    ma = _vm_scalar_first_key(
        vm,
        (
            f"{P}_检测结果_mA",
            f"{P}_检测条件_mA",
            f"{P}_mA",
        ),
    ) or _vm_scalar_scan_prefix_suffix(
        vm, P, ("_检测结果_mA", "_检测条件_mA", "_mA")
    )
    if not ma:
        ma = _testresult_scalar(source_data, "ma2", "ma")
    mm = _vm_scalar_first_key(
        vm,
        (
            f"{P}_普通剂量模式_检测结果_mm",
            f"{P}_检测结果_mm",
            f"{P}_检测条件_mm",
            f"{P}_mm",
        ),
    ) or _vm_scalar_scan_prefix_suffix(
        vm, P, ("_普通剂量模式_检测结果_mm", "_检测结果_mm", "_检测条件_mm", "_mm")
    )
    if not mm:
        mm = _testresult_scalar(source_data, "mm")
    sec = _vm_scalar_first_key(
        vm,
        (
            f"{P}_检测条件_s",
            f"{P}_检测结果_s",
            f"{P}_脉冲_s",
            f"{P}_s",
        ),
    ) or _vm_scalar_scan_prefix_suffix(
        vm, P, ("_检测条件_s", "_检测结果_s", "_脉冲_s", "_s")
    )
    if not sec:
        sec = _testresult_scalar(source_data, "pulseS", "exposureS", "s")
    mode_label = _resolve_control_mode_label(P, vm, source_data)
    if not any((mode_label, kv, ma, mm, sec)):
        return None
    lines: list[str] = []
    kv_parts: list[str] = []
    if kv:
        kv_parts.append(f"{kv}kV")
    if ma:
        kv_parts.append(f"{ma}mA")
    if sec:
        kv_parts.append(f"{sec}s")
    top_line = "；".join(kv_parts) if kv_parts else ""
    if mode_label and top_line:
        lines.append(f"{mode_label} {top_line}")
    elif mode_label:
        lines.append(mode_label)
    elif top_line:
        lines.append(top_line)
    if mm:
        lines.append(f"平板探测器尺寸D={mm}mm")
    lines.append("标准水模")
    return "\n".join(lines)


def _pick_primary_measurement_for_site_row(site_prefix: str, value_mapping: dict) -> str | None:
    """报告「检测结果」格：仅用与现场行前缀 P 绑定的词条，禁止回落到全局 computedResult（多行会串值）。"""
    P = site_prefix
    vm = value_mapping
    for sk in (f"{P}_计算结果", f"{P}_报出值", f"{P}_检测值"):
        got = _value_mapping_scalar_str(vm, sk)
        if got:
            return got
    for tail in (
        "_检测结果_lp/mm",
        "_检测结果_尺寸（mm）",
        "_检测结果_尺寸(mm)",
        "_报出值_mm",
        "_检测结果_RV1.0%（mm）",
    ):
        got = _value_mapping_scalar_str(vm, f"{P}{tail}")
        if got:
            return got
    got = _value_mapping_scalar_str(vm, f"{P}_检测结果")
    if got:
        low = got.lower()
        if "kv" not in low and "ma" not in low and len(got) < 80:
            return got
    return None


def _dsa_report_field_rejects_value_key(field: dict, key: str) -> bool:
    """DSA/伪影 专项报告格禁止误配到透视防护区、比释动能等长词条。"""
    cands = _field_semantic_candidate_keys(field)
    if not any(str(c).startswith("DSA") or str(c).startswith("伪影") for c in cands):
        return False
    if "透视防护区" in key or "入射体表空气比释动能率" in key:
        return True
    if any(str(c).startswith("DSA动态范围") for c in cands):
        return "DSA动态范围" not in key
    if any(str(c).startswith("DSA对比灵敏度") for c in cands):
        return "DSA对比灵敏度" not in key
    if any(str(c).startswith("伪影") for c in cands):
        return "伪影" not in key
    return "DSA" not in key


def _pick_value_by_placeholder_keyword_in_mapping(
    field: dict, value_mapping: dict, *, allow_bool: bool = False
):
    """
    在精确键未命中时，用字段的 placeholder / id / title 与 value_mapping 的键做关键词匹配：
    相等、长串包含、去单位后的相似度。优先最长占位符，避免 pdfFieldId 与合并数据串位。
    allow_bool=True 时可用于勾选框（命中布尔/0/1）。
    """
    if not isinstance(value_mapping, dict):
        return None
    needles: list[str] = []
    seen: set[str] = set()
    for attr in ("originalPlaceholder", "placeholder", "id", "title", "label"):
        v = str(field.get(attr) or "").strip()
        if v and v not in seen:
            seen.add(v)
            needles.append(v)
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    for attr in ("placeholder", "title", "label", "bindKey", "key"):
        v = str(src.get(attr) or "").strip()
        if v and v not in seen:
            seen.add(v)
            needles.append(v)
    needles.sort(key=len, reverse=True)
    cands_joined = " ".join(k for k in _field_semantic_candidate_keys(field) if k)
    ftoks_mm = _field_mm_thickness_tokens_for_qc_disambiguation(field)
    guard_qc_condition_cell = (
        "检测条件" in cands_joined
        and "判定标准" not in cands_joined
        and not _field_semantic_text_has_circled_number(field)
    )
    best_key = None
    best_score = 0.0
    for ph in needles:
        if len(ph) < 3:
            continue
        phn = _qc_row_prefix_strip_unit_parens(ph)
        if len(phn) < 3:
            continue
        ph_has_cjk = re.search(r"[\u4e00-\u9fff]", ph) is not None
        for key, raw in value_mapping.items():
            if not isinstance(key, str):
                continue
            if _dsa_report_field_rejects_value_key(field, key):
                continue
            if ftoks_mm:
                ktoks = _mm_thickness_tokens_in_semantic_key(key)
                if ktoks and ftoks_mm != ktoks:
                    continue
            if guard_qc_condition_cell:
                if "判定标准" in key or "单项判定" in key:
                    continue
                if not _is_whole_cell_qc_condition_mapping_key(key):
                    continue
            if raw in (None, "") or isinstance(raw, (dict, list)):
                continue
            if isinstance(raw, str):
                rs = raw.strip()
                if rs in ("sameInspection", "customCommission") and (
                    "联系人" in ph or ("电话" in ph and "检测类型" not in ph)
                ):
                    continue
            if isinstance(raw, bool) and not allow_bool:
                continue
            if allow_bool and isinstance(raw, bool):
                kn = _qc_row_prefix_strip_unit_parens(key)
                if len(kn) < 3:
                    continue
                sc = 1.0 if ph == key else (0.93 if len(ph) >= 6 and ph in key else 0.0)
                if sc == 0.0:
                    r = SequenceMatcher(None, phn, kn).ratio()
                    if r >= 0.78:
                        sc = r * 0.96
                if (
                    ph_has_cjk
                    and re.search(r"[\u4e00-\u9fff]", key) is None
                    and ph not in key
                    and key not in ph
                ):
                    sc = 0.0
                if sc > best_score:
                    best_score, best_key = sc, key
                continue
            kn = _qc_row_prefix_strip_unit_parens(key)
            if len(kn) < 3:
                continue
            sc = 0.0
            if ph == key:
                sc = 1.0
            elif len(ph) >= 6 and ph in key:
                sc = 0.93 + min(len(ph), 200) / 20000.0
            elif len(key) >= 10 and key in ph:
                sc = 0.89
            else:
                r = SequenceMatcher(None, phn, kn).ratio()
                if r >= 0.78:
                    sc = r * 0.96
            if len(phn) >= 8 and (phn in kn or kn in phn):
                sc = max(sc, 0.86)
            if (
                ph_has_cjk
                and re.search(r"[\u4e00-\u9fff]", key) is None
                and ph not in key
                and key not in ph
            ):
                sc = 0.0
            if sc > best_score:
                best_score = sc
                best_key = key
        if best_score >= 0.95:
            break
    if best_key is None or best_score < 0.64:
        return None
    v = value_mapping.get(best_key)
    if v in (None, "") or isinstance(v, (dict, list)):
        return None
    if _value_is_field_label_echo(field, v):
        return None
    if isinstance(v, bool):
        return v if allow_bool else None
    if allow_bool and v in (0, 1):
        return bool(v)
    out = str(v).strip()
    if _value_looks_like_unresolved_qc_domain_id(out):
        return None
    return out


def _pick_site_row_composed_condition_or_primary_result(
    field: dict,
    value_mapping: dict,
    source_data: dict,
    task_obj,
) -> str | None:
    """报告导出：按现场模板长词条行对齐，拼检测条件或取主检测数值。"""
    if task_obj is None or getattr(task_obj, "output_target", None) != LibraryTask.OUTPUT_REPORT:
        return None
    if _field_semantic_text_has_circled_number(field):
        return None
    cands = [k for k in _field_semantic_candidate_keys(field) if k]
    rid = max(cands, key=len) if cands else ""
    if not rid:
        return None
    vm = value_mapping if isinstance(value_mapping, dict) else {}
    if rid.endswith("_检测条件"):
        base = rid[: -len("_检测条件")]
        # 术者位各行（含足部/胸部等）的 kV、mA、s 均挂在列前缀「透视防护区…(μSv/h)_检测条件_*」下，
        # 不得用行级 base 去拼 P，否则匹配不上列字段、检测条件只剩空或误配。
        if "术者位" in base and "透视防护区检测平面上周围剂量当量率" in base:
            rate_pfx = _shield_zone_rate_column_prefix(base)
            if rate_pfx:
                block = _compose_qc_condition_multiline_block(rate_pfx, vm, source_data)
                if block:
                    return block
        P = _find_best_site_row_prefix_for_report_base(base, vm)
        if not P:
            return None
        return _compose_qc_condition_multiline_block(P, vm, source_data)
    if rid.endswith("_检测结果"):
        joined = " ".join(cands)
        if "检测条件" in joined:
            return None
        base = rid[: -len("_检测结果")]
        P = None
        if "术者位" in base and "透视防护区检测平面上周围剂量当量率" in base:
            P = _shield_zone_match_site_prefix_for_report_row_base(base, vm)
        if not P:
            P = _find_best_site_row_prefix_for_report_base(base, vm)
        if not P:
            return None
        return _pick_primary_measurement_for_site_row(P, vm)
    return None


_KAP_AREA_SLUG_TO_TAIL = {
    "mGy_cm2": "mGycm^2",
    "mGy_m2": "mGym^2",
    "uGy_m2": "μGym^2",
    "uGy_cm2": "μGycm^2",
}

# 统一表单 / PDF 与 kapAreaProductUnit 枚举同槽的 f55–f58（见库 JS-117 等模板）。
_KAP_PDF_FIELD_ID_TO_SLUG: dict[str, str] = {
    "f55": "mGy_cm2",
    "f56": "mGy_m2",
    "f57": "uGy_m2",
    "f58": "uGy_cm2",
}


def _slug_from_kap_pdf_slot_values(value_mapping: dict, dynamic_data: dict) -> str:
    """
    落库提交常无 steps，testResult 亦可能为 null，但 dynamicData.f56 等槽位会存整组单选的枚举串（如 mGy_m2）。
    """
    vm = value_mapping if isinstance(value_mapping, dict) else {}
    dd = dynamic_data if isinstance(dynamic_data, dict) else {}
    for m in (vm, dd):
        for fid in _KAP_PDF_FIELD_ID_TO_SLUG:
            v = m.get(fid)
            if v is None or v == "":
                continue
            if isinstance(v, str):
                vs = v.strip()
                if vs in _KAP_AREA_SLUG_TO_TAIL:
                    return vs
    for m in (vm, dd):
        for fid, expected in _KAP_PDF_FIELD_ID_TO_SLUG.items():
            v = m.get(fid)
            if v is True or v == 1:
                return expected
            if isinstance(v, str) and v.strip().lower() in {"true", "yes", "on", "1"}:
                return expected
    return ""


def _field_is_commission_or_inspection_no_slot(field: dict) -> bool:
    """
    JS-117 等模板中「委托编号」与「检测仪器」可能共用同一 pdfFieldId（如 f1），
    attach 后两格会带上同一 submitPath=instruments；此类格不得走仪器整表合并逻辑。
    """
    blob = " ".join(
        str(field.get(a) or "") for a in ("id", "fieldId", "placeholder", "originalPlaceholder", "title", "label")
    )
    blob += " " + " ".join(_field_semantic_candidate_keys(field))
    b = blob.lower()
    return any(
        tok in b
        for tok in (
            "委托编号",
            "commissionno",
            "受检编号",
            "inspectionno",
            "entrustno",
            "项目编号",
        )
    )


def _reject_commission_org_enum_as_textfield_value(field: dict, ft: str, val) -> bool:
    """委托单位模式枚举不得经模糊键匹配写入联系人/电话等纯文本格（如与 f51 同值串位）。"""
    if (ft or "").lower() != "text":
        return False
    if val is None or isinstance(val, bool):
        return False
    s = str(val).strip()
    if s not in ("sameInspection", "customCommission"):
        return False
    cj = " ".join(_field_semantic_candidate_keys(field))
    cjl = cj.lower()
    if "commissionorgmode" in cjl or "同受检单位" in cj:
        return False
    if "联系人" in cj or "联系电话" in cj or "联系人/" in cj:
        return True
    return False


def _inject_kap_area_product_unit_checkbox_aliases(value_mapping: dict, source_data: dict | None = None) -> None:
    """
    KAP 指示偏离：前端单选 testResult.kapAreaProductUnit（mGy_cm2 / mGy_m2 / uGy_m2 / uGy_cm2）；
    PDF 模板仍为四个勾选项（KAP指示偏离_检测结果_*），回填时只应勾选一个。
    """
    slug = ""
    tr: dict = {}
    dd: dict = {}
    if isinstance(source_data, dict):
        tr = source_data.get("testResult") if isinstance(source_data.get("testResult"), dict) else {}
        slug = str(tr.get("kapAreaProductUnit") or "").strip()
        raw_dd = source_data.get("dynamicData")
        if isinstance(raw_dd, dict):
            dd = raw_dd
    if not slug:
        slug = str(value_mapping.get("testResult.kapAreaProductUnit") or "").strip()
    if (not slug or slug not in _KAP_AREA_SLUG_TO_TAIL) and tr:
        legacy_keys = (("field58", "mGy_cm2"), ("field55", "mGy_m2"), ("field56", "uGy_m2"), ("field57", "uGy_cm2"))
        for fk, s in legacy_keys:
            v = tr.get(fk)
            if v is True or v == 1 or str(v).strip().lower() in {"1", "true", "yes", "on"}:
                slug = s
                break
    if not slug or slug not in _KAP_AREA_SLUG_TO_TAIL:
        slug = _slug_from_kap_pdf_slot_values(value_mapping if isinstance(value_mapping, dict) else {}, dd)
    if not slug or slug not in _KAP_AREA_SLUG_TO_TAIL:
        return
    prefix = "KAP指示偏离_检测结果_"
    for s, tail in _KAP_AREA_SLUG_TO_TAIL.items():
        value_mapping[prefix + tail] = s == slug
    for fid, s in _KAP_PDF_FIELD_ID_TO_SLUG.items():
        value_mapping[fid] = s == slug
    value_mapping["testResult.kapAreaProductUnit"] = slug


def _verdict_base_from_field_key(verdict_key: str) -> str:
    """从「…_单项判定」或「…单项判定」类 id/placeholder 得到前缀，用于拼同行检测结果键。"""
    v = str(verdict_key or "").strip()
    if v.endswith("_单项判定"):
        return v[: -len("_单项判定")]
    if v.endswith("单项判定") and len(v) > len("单项判定"):
        return v[: -len("单项判定")]
    return ""


def _verdict_result_placeholder_keys(verdict_key: str) -> list[str]:
    base = _verdict_base_from_field_key(verdict_key)
    if not base:
        return []
    return [
        f"{base}_检测结果",
        f"{base}_计算结果",
        f"{base}_报出值",
        f"{base}_检测值",
        f"{base}检测结果",
        f"{base}计算结果",
        f"{base}报出值",
        f"{base}检测值",
    ]


def _verdict_row_bases_for_peer_match(verdict_key: str) -> list[str]:
    """
    从「…_单项判定」类键得到由长到短的前缀列表，用于匹配同行「检测结果」或判定标准。
    例：透视…典型值_标准水模 → 先整段，再去尾段得到 透视…典型值，以命中上一小节的检测结果。
    """
    b = _verdict_base_from_field_key(verdict_key)
    out: list[str] = []
    seen: set[str] = set()
    while b and b not in seen:
        seen.add(b)
        out.append(b)
        if "_" not in b:
            break
        b = b.rsplit("_", 1)[0]
    return out


def _verdict_is_shield_operator_height_row_key(verdict_key: str) -> bool:
    """防护区「术者位 + 足部/胸部等高度段」行：截短前缀会与邻行极度相似，禁止用于模糊测量值匹配。"""
    vk = str(verdict_key or "")
    if "术者位" not in vk:
        return False
    if re.search(r"_(足部|下肢|腹部|胸部|头部)_", vk):
        return True
    return bool(re.search(r"_\d+cm（(?:足部|下肢|腹部|胸部|头部)）", vk))


def _verdict_fuzzy_base_chain(verdict_key: str) -> list[str]:
    """
    「术者位×高度」等多行共用同一段长前缀时，模糊匹配只用最长前缀，避免截短后与邻行串格。
    其它行仍保留由长到短的前缀链，供小节标题不一致时回退。
    """
    bases = _verdict_row_bases_for_peer_match(verdict_key)
    if not bases:
        return bases
    if _verdict_is_shield_operator_height_row_key(verdict_key):
        return bases[:1]
    return bases


def _verdict_measurement_placeholder_key_candidates(verdict_key: str) -> list[str]:
    """单项判定格可能对应的检测结果占位键（含截短前缀），顺序即尝试顺序。"""
    keys: list[str] = []
    seen: set[str] = set()
    for base in _verdict_row_bases_for_peer_match(verdict_key):
        for suf in ("_检测结果", "_计算结果", "_报出值", "_检测值"):
            k = f"{base}{suf}"
            if k not in seen:
                seen.add(k)
                keys.append(k)
        for suf in ("检测结果", "计算结果", "报出值", "检测值"):
            k = f"{base}_{suf}"
            if k not in seen:
                seen.add(k)
                keys.append(k)
    keys.extend(_verdict_result_placeholder_keys(verdict_key))
    out: list[str] = []
    seen2: set[str] = set()
    for k in keys:
        if k not in seen2:
            seen2.add(k)
            out.append(k)
    return out


def _normalize_token_for_similarity(s: str) -> str:
    t = str(s or "").strip()
    if not t:
        return ""
    t = re.sub(r"[（(][^）)]*[）)]", "", t)
    t = t.replace("/", "_").replace("／", "_")
    t = re.sub(r"\s+", "", t)
    return t


def _string_similarity_ratio(a: str, b: str) -> float:
    na = _normalize_token_for_similarity(a)
    nb = _normalize_token_for_similarity(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _best_similar_mapping_key(
    target: str,
    value_mapping: dict,
    *,
    key_filter,
    min_ratio: float = 0.52,
    thickness_tokens: frozenset[str] | None = None,
) -> str | None:
    if not target or not isinstance(value_mapping, dict):
        return None
    best_k = None
    best_r = 0.0
    for k, v in value_mapping.items():
        if not isinstance(k, str) or not k.strip():
            continue
        if not key_filter(k):
            continue
        if thickness_tokens:
            kt = _mm_thickness_tokens_in_semantic_key(k)
            if kt and thickness_tokens != kt:
                continue
        if v in (None, "") or isinstance(v, (dict, list)):
            continue
        r = _string_similarity_ratio(target, k)
        if r > best_r:
            best_r, best_k = r, k
    if best_k is not None and best_r >= min_ratio:
        return best_k
    return None


def _is_whole_cell_qc_condition_mapping_key(k: str) -> bool:
    """
    可作为「整格检测条件」做相似度回填的 value_mapping 键。
    排除：判定标准/单项判定列；kV、mA、尺寸、自动手动等子栏（这些由多行拼接组装）。
    """
    if not isinstance(k, str) or not k.strip():
        return False
    if "判定标准" in k or "单项判定" in k:
        return False
    kl = k.lower()
    if "judgment" in kl or "criterion" in kl:
        return False
    if "_检测条件" not in k and not k.endswith("检测条件") and k != "检测条件":
        return False
    for tail in (
        "_kV",
        "_mA",
        "_mm",
        "_cm",
        "_自动控制",
        "_手动控制",
        "_尺寸",
    ):
        if k.endswith(tail):
            return False
    return True


def _pick_qc_condition_result_by_similarity(
    field: dict,
    value_mapping: dict,
    *,
    want_condition: bool,
    min_ratio: float = 0.52,
    min_ratio_loose: float = 0.42,
) -> str | None:
    """
    报告格 id 与现场词条不完全一致时，在 value_mapping 的同类键（_检测条件 / _检测结果 等）中按最大相似度匹配。
    """
    if not isinstance(value_mapping, dict):
        return None
    candidates = [k for k in _field_semantic_candidate_keys(field) if k]
    target = max(candidates, key=len, default="") or ""
    if not target:
        return None

    def _is_result_key(k: str) -> bool:
        return (
            "_检测结果" in k
            or "_计算结果" in k
            or "_报出值" in k
        )

    def _is_condition_key(k: str) -> bool:
        return _is_whole_cell_qc_condition_mapping_key(k)

    pred = _is_condition_key if want_condition else _is_result_key
    ftoks = _mm_thickness_tokens_in_semantic_key(target)
    thick = ftoks if ftoks else None
    hit = _best_similar_mapping_key(
        target, value_mapping, key_filter=pred, min_ratio=min_ratio, thickness_tokens=thick
    )
    if not hit:
        hit = _best_similar_mapping_key(
            target,
            value_mapping,
            key_filter=pred,
            min_ratio=min_ratio_loose,
            thickness_tokens=thick,
        )
    if not hit:
        return None
    v = value_mapping.get(hit)
    if v in (None, "") or isinstance(v, (dict, list)):
        return None
    out = str(v).strip()
    if _value_looks_like_unresolved_qc_domain_id(out):
        return None
    return out


def _inject_step_section_qc_key_aliases(value_mapping: dict, steps: list | None) -> None:
    """
    用 steps 中小节 title + 控件 label 生成与报告 PDF 常见 id 对齐的词条（如 透视…典型值_检测条件）。
    单项判定仍由判定标准与检测结果推算，不在此写入 value_mapping。
    """
    if not isinstance(value_mapping, dict) or not isinstance(steps, list):
        return
    label_to_suffix = {
        "检测条件": "_检测条件",
        "检测结果": "_检测结果",
        "计算结果": "_计算结果",
        "报出值": "_报出值",
    }

    def put_key(key: str, val: object) -> None:
        if not key or val in (None, "") or isinstance(val, (dict, list)):
            return
        if value_mapping.get(key) not in (None, ""):
            return
        value_mapping[key] = val

    for step in steps:
        if not isinstance(step, dict):
            continue
        sections = step.get("sections")
        if not isinstance(sections, list):
            continue
        prev_sec_title = ""
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            st = str(sec.get("title") or "").strip()
            fields = sec.get("fields")
            if not isinstance(fields, list):
                continue
            for fld in fields:
                if not isinstance(fld, dict):
                    continue
                label = str(fld.get("label") or "").strip()
                val = None
                for lk in _submit_field_semantic_label_keys(fld):
                    v = value_mapping.get(lk)
                    if v not in (None, "") and not isinstance(v, (dict, list)):
                        val = v
                        break
                if val is None:
                    continue
                if _value_is_field_label_echo(fld, val):
                    continue
                if label in label_to_suffix and st:
                    put_key(f"{st}{label_to_suffix[label]}", val)
            if st:
                prev_sec_title = st


def _fuzzy_measured_text_for_verdict(
    verdict_key: str, value_mapping: dict, want_suffix: str = "_检测结果"
) -> str:
    """
    在 value_mapping 中按与单项判定前缀的相似度选取检测结果类键。
    按前缀由长到短逐级取「当前前缀下」最佳匹配，禁止跨前缀全局取最大相似度（否则防护区多行互串）。
    """
    if not isinstance(value_mapping, dict):
        return ""
    bases = _verdict_fuzzy_base_chain(verdict_key)
    pool: list[tuple[str, str]] = []
    for k, v in value_mapping.items():
        if not isinstance(k, str) or v in (None, "") or isinstance(v, (dict, list)):
            continue
        if "判定标准" in k or "单项判定" in k:
            continue
        if want_suffix == "_检测结果":
            ends_m = (
                k.endswith("_检测结果")
                or k.endswith("检测结果")
                or k.endswith("_计算结果")
                or k.endswith("计算结果")
                or k.endswith("_报出值")
                or k.endswith("报出值")
                or k.endswith("_检测值")
                or k.endswith("检测值")
            )
            if not ends_m:
                continue
        pool.append((k, str(v).strip()))
    if not pool:
        return ""

    def _key_row_prefix_for_similarity(key: str) -> str:
        prefix = key
        for suf in (
            "_检测结果",
            "_计算结果",
            "_报出值",
            "_检测值",
            "检测结果",
            "计算结果",
            "报出值",
            "检测值",
        ):
            if key.endswith(suf):
                prefix = key[: -len(suf)].rstrip("_")
                break
        return _qc_row_prefix_strip_unit_parens(prefix)

    for bi, base in enumerate(bases):
        bn = _qc_row_prefix_strip_unit_parens(base)
        if not bn:
            continue
        best_v, best_r = "", 0.0
        for k, sv in pool:
            pn = _key_row_prefix_for_similarity(k)
            if not pn:
                continue
            r = _string_similarity_ratio(bn, pn)
            if r > best_r:
                best_r, best_v = r, sv
        thr = 0.74 if bi == 0 else 0.60
        if len(bn) >= 55:
            thr = max(thr, 0.82)
        if best_r >= thr and best_v:
            return best_v
    return ""


def _fuzzy_criterion_text_for_verdict_key(verdict_key: str, value_mapping: dict) -> str:
    """
    从 value_mapping 的判定标准类键推断标准句。
    精确键走完整前缀链；模糊匹配按 _verdict_fuzzy_base_chain 逐级取最佳，禁止全局串到其它行（如典型值 ≤25.0）。
    """
    if not isinstance(value_mapping, dict):
        return ""
    for base in _verdict_row_bases_for_peer_match(verdict_key):
        for suffix in ("_判定标准", "_验收", "_状态"):
            k = f"{base}{suffix}"
            v = value_mapping.get(k)
            if v not in (None, "") and not isinstance(v, (dict, list)):
                return str(v).strip()
    bases = _verdict_fuzzy_base_chain(verdict_key)
    for bi, base in enumerate(bases):
        bn = _qc_row_prefix_strip_unit_parens(base)
        if not bn:
            continue
        best_t, best_r = "", 0.0
        for k, v in value_mapping.items():
            if not isinstance(k, str) or v in (None, "") or isinstance(v, (dict, list)):
                continue
            if "判定标准" not in k and "验收" not in k:
                continue
            prefix = k
            for tail in ("_判定标准", "判定标准", "_验收", "验收"):
                if k.endswith(tail):
                    prefix = k[: -len(tail)].rstrip("_")
                    break
            pn = _qc_row_prefix_strip_unit_parens(prefix)
            r = _string_similarity_ratio(bn, pn)
            if r > best_r:
                best_r, best_t = r, str(v).strip()
        thr = 0.74 if bi == 0 else 0.60
        if len(bn) >= 55:
            thr = max(thr, 0.80)
        if best_r >= thr and best_t:
            return best_t
    return ""


def _placeholder_text_looks_like_criterion(s: str) -> bool:
    t = str(s or "").strip()
    if len(t) < 3:
        return False
    if re.search(r"[≤≥≦≧＜＞<>]", t) and re.search(r"\d", t):
        return True
    if ("不超过" in t or "不低于" in t or "不大于" in t) and re.search(r"\d", t):
        return True
    return False


def _matrix_value_column_id_for_title(matrix: dict, title: str) -> str | None:
    t = str(title or "").strip()
    if not t:
        return None
    for vc in matrix.get("valueColumns") or []:
        if not isinstance(vc, dict):
            continue
        if str(vc.get("title") or "").strip() == t:
            cid = str(vc.get("id") or "").strip()
            if cid:
                return cid
    return None


def _matrix_row_header_serial_meta(matrix: dict) -> tuple[str | None, bool]:
    """返回 (序号列 id, 该列是否为 merge=auto)。"""
    for rc in matrix.get("rowHeaderColumns") or []:
        if not isinstance(rc, dict):
            continue
        tit = str(rc.get("title") or "").strip()
        if tit in ("序号", "行序号"):
            cid = str(rc.get("id") or "").strip()
            merge_auto = str(rc.get("merge") or "").strip().lower() == "auto"
            return (cid or None, merge_auto)
    return (None, False)


def _matrix_serial_anchor_rows_1based(
    rows: list, serial_col_id: str | None, serial_merge_auto: bool
) -> list[int]:
    """
    行序号/序号列：仅在非空格产生锚点；merge=auto 时连续相同显示值只保留首行锚点。
    物理第 r 行（1-based）使用 max{锚点|锚点<=r} 所在行的「判定标准」。
    """
    if not isinstance(rows, list) or not rows:
        return [1]
    if not serial_col_id:
        return list(range(1, len(rows) + 1))
    anchors: list[int] = []
    prev_display: str | None = None
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        hdr = row.get("headers") if isinstance(row.get("headers"), dict) else {}
        v = str(hdr.get(serial_col_id) or "").strip()
        if not v:
            continue
        if serial_merge_auto and prev_display is not None and v == prev_display:
            continue
        anchors.append(i + 1)
        prev_display = v
    if not anchors:
        return [1]
    return anchors


def _criterion_text_from_matrix_cell(
    cell: dict | None, value_mapping: dict | None, source_data: dict | None
) -> str:
    if not isinstance(cell, dict):
        return ""
    for k in ("judgmentCriterionText", "judgmentCriterion", "criterionText", "判定标准"):
        t = str(cell.get(k) or "").strip()
        if t:
            return t
    dv = cell.get("defaultValue")
    if isinstance(dv, str) and _placeholder_text_looks_like_criterion(dv):
        return dv.strip()
    tx = cell.get("text")
    if isinstance(tx, str) and tx.strip():
        return tx.strip()
    src = cell.get("source") if isinstance(cell.get("source"), dict) else {}
    sp = str(src.get("submitPath") or "").strip()
    if not sp:
        return ""
    vm = value_mapping if isinstance(value_mapping, dict) else {}
    got = vm.get(sp)
    if got not in (None, "") and not isinstance(got, (dict, list)):
        return str(got).strip()
    if isinstance(source_data, dict):
        nested = _nested_get_for_submit(source_data, sp)
        if nested not in (None, "") and not isinstance(nested, (dict, list)):
            return str(nested).strip()
    return ""


def _matrix_criterion_text_for_verdict_pdf_field(
    pdf_field_id: str,
    steps: list | None,
    value_mapping: dict | None,
    source_data: dict | None,
) -> str:
    """
    在 layout=matrixTable 的节中，按「判定标准」列表头定位列，再按「序号/行序号」列的锚点行
    （含合并行：仅首行或非空序号格为锚）映射到单项判定所在物理行应采用的判定标准句。
    """
    if not pdf_field_id or not isinstance(steps, list):
        return ""
    vm = value_mapping if isinstance(value_mapping, dict) else {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        for sec in step.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            if str(sec.get("layout") or "").lower() != "matrixtable":
                continue
            m = sec.get("matrix") if isinstance(sec.get("matrix"), dict) else {}
            rows = m.get("rows")
            if not isinstance(rows, list) or not rows:
                continue
            crit_col_id = _matrix_value_column_id_for_title(m, "判定标准")
            if not crit_col_id:
                continue
            serial_col_id, serial_merge_auto = _matrix_row_header_serial_meta(m)
            anchors_1b = _matrix_serial_anchor_rows_1based(rows, serial_col_id, serial_merge_auto)
            verdict_i0: int | None = None
            for i, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                cells = row.get("cells") if isinstance(row.get("cells"), dict) else {}
                for c in cells.values():
                    if not isinstance(c, dict):
                        continue
                    if _field_pdf_id(c) != pdf_field_id:
                        continue
                    lab = str(c.get("label") or "").strip()
                    if lab in ("", "单项判定"):
                        verdict_i0 = i
                        break
                if verdict_i0 is not None:
                    break
            if verdict_i0 is None:
                continue
            phys_1b = verdict_i0 + 1
            leq = [a for a in anchors_1b if a <= phys_1b]
            if leq:
                use_anchor = max(leq)
            else:
                use_anchor = 1
            use_anchor = min(max(use_anchor, 1), len(rows))
            src_row = rows[use_anchor - 1]
            if not isinstance(src_row, dict):
                continue
            cells = src_row.get("cells") if isinstance(src_row.get("cells"), dict) else {}
            static_cells = src_row.get("staticCells") if isinstance(src_row.get("staticCells"), dict) else {}
            cell = cells.get(crit_col_id)
            if cell is None:
                cell = static_cells.get(crit_col_id)
            text = _criterion_text_from_matrix_cell(cell, vm, source_data)
            if text.strip():
                return text.strip()
    return ""


def _get_judgment_criterion_text(
    field: dict,
    value_mapping: dict | None = None,
    report_steps: list | None = None,
    source_data: dict | None = None,
) -> str:
    for k in ("judgmentCriterionText", "judgmentCriterion", "criterionText", "判定标准"):
        t = str(field.get(k) or "").strip()
        if t:
            return t
    pid = _field_pdf_id(field)
    if pid and isinstance(report_steps, list):
        mc = _matrix_criterion_text_for_verdict_pdf_field(
            pid, report_steps, value_mapping, source_data
        )
        if mc:
            return mc
    for k in ("originalPlaceholder", "placeholder"):
        t = str(field.get(k) or "").strip()
        if _placeholder_text_looks_like_criterion(t):
            return t
    vm = value_mapping if isinstance(value_mapping, dict) else {}
    for fk in _field_candidate_keys(field):
        ks = str(fk or "").strip()
        if "单项判定" not in ks:
            continue
        crit = _shield_zone_criterion_text_for_verdict_field(ks, vm)
        if crit:
            return crit
        crit = _fuzzy_criterion_text_for_verdict_key(ks, vm)
        if crit:
            return crit
        for base in _verdict_row_bases_for_peer_match(ks):
            for ck in (
                f"{base}_判定标准",
                f"{base}_验收标准",
                f"{base}_限值",
            ):
                syn = {"id": ck, "placeholder": ck, "originalPlaceholder": ck, "fieldType": "text"}
                hit = _pick_value_by_placeholder_keyword_in_mapping(syn, vm, allow_bool=False)
                if isinstance(hit, str) and hit.strip():
                    return hit.strip()
    return ""


def _is_signature_image_text(v) -> bool:
    if not isinstance(v, str):
        return False
    text = v.strip()
    if not text:
        return False
    if text.startswith("data:image/png;base64,"):
        text = text.split(",", 1)[1]
    try:
        base64.b64decode(text, validate=True)
        return True
    except Exception:
        return False


def _merge_mapping_fill_empty(base: dict, extra: dict) -> None:
    """仅补充 base 中仍为空缺的键，避免覆盖已有占位符映射。"""
    for k, v in (extra or {}).items():
        if not k:
            continue
        if base.get(k) not in (None, ""):
            continue
        base[k] = v


_F_SLOT_DD_KEY = re.compile(r"^[fF]\d+\Z")


def _unwrap_dynamic_data_cell_value(val: object) -> object:
    """部分客户端将 dynamicData[fN] 写成 { value / text / content }；解包为可写入 PDF 的标量。"""
    if isinstance(val, dict) and val:
        for kk in ("value", "text", "content", "displayValue"):
            inner = val.get(kk)
            if inner in (None, ""):
                continue
            if isinstance(inner, (dict, list)):
                continue
            return inner
    return val


def _normalize_dynamic_data_f_slots(dd: dict | None) -> dict:
    """将 dynamicData 中 F8、f8 等槽键规范为 f8，与模板 pdfFieldId 一致。"""
    if not isinstance(dd, dict) or not dd:
        return dd if isinstance(dd, dict) else {}
    out = {**dd}
    for k, v in list(dd.items()):
        if not isinstance(k, str):
            continue
        ks = k.strip()
        if not _F_SLOT_DD_KEY.match(ks):
            continue
        canon = ks.lower()
        if canon == ks:
            continue
        if out.get(canon) in (None, "") and v not in (None, ""):
            out[canon] = v
    return out


def _merge_site_dynamic_semantics_into_value_mapping(value_mapping: dict, merged_site_sem: dict) -> None:
    """合并「库现场模板 + dynamicData」解释的词条。

    先补缺；再对「当前值等于键名」（常见占位回声）或明显桩值用 dynamicData 侧真值覆盖，
    否则 submitPath 已写入标签串「受检单位」会占住键，导致 f8 有值仍无法进入 PDF。
    """
    if not isinstance(value_mapping, dict) or not isinstance(merged_site_sem, dict):
        return
    _merge_mapping_fill_empty(value_mapping, merged_site_sem)
    junk_stubs = frozenset({"-", "—", "－", "/", "无", "暂无", "N/A", "n/a"})
    for k, v in merged_site_sem.items():
        if not k:
            continue
        if v in (None, "") or isinstance(v, (dict, list, bool)):
            continue
        if isinstance(v, str) and not v.strip():
            continue
        v_use = _coerce_iso_datetime_value_for_date_part_key(k, v)
        cur = value_mapping.get(k)
        if cur in (None, ""):
            value_mapping[k] = v_use
            continue
        if not isinstance(cur, str):
            continue
        cs = cur.strip()
        if not cs:
            value_mapping[k] = v_use
            continue
        ks = str(k).strip()
        if cs == ks:
            value_mapping[k] = v_use
            continue
        if cs in junk_stubs:
            value_mapping[k] = v_use
            continue


def _submit_field_semantic_label_keys(fld: dict) -> list[str]:
    """前端 steps 里单控件可参与报告匹配的语义名（与模板 placeholder/title 对齐，不用 pdfFieldId）。"""
    raw: list[str] = []
    for attr in ("id", "placeholder", "title", "label", "name", "originalPlaceholder"):
        v = fld.get(attr)
        if isinstance(v, str) and v.strip():
            raw.append(v.strip())
    src = fld.get("source")
    if isinstance(src, dict):
        for attr in ("placeholder", "title", "label", "originalPlaceholder", "bindKey", "key"):
            v = src.get(attr)
            if isinstance(v, str) and v.strip():
                raw.append(v.strip())
    seen: set[str] = set()
    out: list[str] = []
    for k in raw:
        collapsed = k.replace("\u3000", " ").strip()
        for v in (k, collapsed, collapsed.rstrip("：:").strip()):
            if v and v not in seen:
                seen.add(v)
                out.append(v)
    return out


def _build_dynamic_data_semantic_mapping_from_steps(
    steps: list | None,
    source_data: dict,
    ambiguous_pdf_ids: AbstractSet[str] | None = None,
) -> dict[str, Any]:
    """
    在给定 **schema steps**（来自库中现场记录模板或提交 JSON 内嵌 steps）上，
    用各控件 source.pdfFieldId 对应 submit.dynamicData 的值，挂到 placeholder/title/id 等词条。
    pdfFieldId 仅在「当前模板」语境下有效，故必须以模板 steps 为桥，不能拿报告 PDF 的 pdfFieldId 直查。
    """
    ambiguous = frozenset(ambiguous_pdf_ids or ())
    dd = source_data.get("dynamicData")
    if not isinstance(dd, dict):
        dd = {}
    dd = _normalize_dynamic_data_f_slots(dd)
    out: dict[str, Any] = {}

    def add_for_field(val: object, fld: dict) -> None:
        if val is None or val == "":
            return
        if isinstance(val, (dict, list)):
            return
        if isinstance(val, bool):
            ftype = str(fld.get("type") or "").lower()
            src_d = fld.get("source") if isinstance(fld.get("source"), dict) else {}
            ach = str(src_d.get("anchorType") or "").lower()
            if ftype not in ("boolean", "bool", "checkbox") and ach != "check":
                return
        for key in _submit_field_semantic_label_keys(fld):
            if out.get(key) not in (None, ""):
                continue
            out[key] = _coerce_iso_datetime_value_for_date_part_key(key, val)
        src2 = fld.get("source") if isinstance(fld.get("source"), dict) else {}
        pid_w = str(src2.get("pdfFieldId") or fld.get("pdfFieldId") or "").strip()
        if (
            pid_w
            and pid_w not in ambiguous
            and val not in (None, "")
            and not isinstance(val, (dict, list))
        ):
            if out.get(pid_w) in (None, ""):
                out[pid_w] = val

    def walk_fields(fields: list) -> None:
        if not isinstance(fields, list):
            return
        for fld in fields:
            if not isinstance(fld, dict):
                continue
            src = fld.get("source") if isinstance(fld.get("source"), dict) else {}
            pid = str(src.get("pdfFieldId") or fld.get("pdfFieldId") or "").strip()
            val = fld.get("value")
            if val in (None, "") and fld.get("defaultValue") not in (None, ""):
                dv = fld.get("defaultValue")
                if not _value_is_field_label_echo(fld, dv):
                    val = dv
            # 与 _build_dynamic_data_semantic_mapping_from_flat_pdf_fields 一致：歧义 f 号仍读 dd，语义键靠唯一条目 id。
            if val in (None, "") and pid:
                val = _unwrap_dynamic_data_cell_value(dd.get(pid))
            add_for_field(val, fld)
            nested = fld.get("fields") or fld.get("children")
            if isinstance(nested, list):
                walk_fields(nested)

    def walk_matrix_section(sec: dict) -> None:
        m = sec.get("matrix") if isinstance(sec.get("matrix"), dict) else {}
        walk_fields(m.get("headerFields"))
        for row in m.get("rows") or []:
            if not isinstance(row, dict):
                continue
            cells = row.get("cells") if isinstance(row.get("cells"), dict) else {}
            walk_fields(list(cells.values()))

    for step in steps or []:
        if not isinstance(step, dict):
            continue
        walk_fields(step.get("fields"))
        sections = step.get("sections")
        if not isinstance(sections, list):
            continue
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            walk_fields(sec.get("fields"))
            walk_matrix_section(sec)
    return out


def _build_dynamic_data_semantic_mapping_from_flat_pdf_fields(
    fields: list | None,
    source_data: dict,
    ambiguous_pdf_ids: AbstractSet[str] | None = None,
) -> dict[str, Any]:
    """
    库模板 `pdf.fields`  materialize 后的坐标表：控件上直接带 pdfFieldId / id / placeholder。
    用其将 dynamicData 映射到词条（与 steps 互补，部分模板以坐标表为主）。
    """
    ambiguous = frozenset(ambiguous_pdf_ids or ())
    dd = source_data.get("dynamicData")
    if not isinstance(dd, dict):
        dd = {}
    dd = _normalize_dynamic_data_f_slots(dd)
    out: dict[str, Any] = {}
    flat: list = []
    _walk_template_field_dicts(fields if isinstance(fields, list) else None, flat)
    for fld in flat:
        if not isinstance(fld, dict):
            continue
        pid = _field_pdf_id(fld)
        val = fld.get("value")
        if val in (None, "") and str(fld.get("content") or "").strip():
            cand = fld.get("content")
            if not _value_is_field_label_echo(fld, cand):
                val = cand
        if val in (None, "") and fld.get("defaultValue") not in (None, ""):
            dv = fld.get("defaultValue")
            if not _value_is_field_label_echo(fld, dv):
                val = dv
        # 歧义 pdfFieldId：仍从 dynamicData 取同一 f 槽（多格复写同值），
        # 唯一条目 id 已在下方 _field_semantic_candidate_keys 循环写入，避免共享 f 号键串位。
        if val in (None, "") and pid:
            val = _unwrap_dynamic_data_cell_value(dd.get(pid))
        if val is None or val == "":
            continue
        if isinstance(val, (dict, list)):
            continue
        if isinstance(val, bool):
            ft0 = str(fld.get("fieldType") or fld.get("type") or "").lower()
            src_d = fld.get("source") if isinstance(fld.get("source"), dict) else {}
            ach0 = str(src_d.get("anchorType") or "").lower()
            if (
                ft0 != "check"
                and ach0 != "check"
                and ft0 not in ("boolean", "bool")
            ):
                continue
        for key in _field_semantic_candidate_keys(fld):
            if not key:
                continue
            if out.get(key) not in (None, ""):
                continue
            out[key] = _coerce_iso_datetime_value_for_date_part_key(key, val)
        pid_w = _field_pdf_id(fld)
        if (
            pid_w
            and pid_w not in ambiguous
            and val not in (None, "")
            and not isinstance(val, (dict, list))
        ):
            if out.get(pid_w) in (None, ""):
                out[pid_w] = val
    return out


_DD_PDF_FIELD_ID_KEY = re.compile(r"^f\d+$", re.IGNORECASE)


def _merge_dynamic_data_placeholder_named_keys(dd: dict | None, into: dict[str, Any]) -> None:
    """
    dynamicData 中除 f 号外的键视为已与 PDF 占位符 / 域 id 一致，直接并入语义映射（跨模板勿用 f 号对齐）。
    仅补缺，不覆盖 into 已有非空值。
    """
    if not isinstance(dd, dict) or not isinstance(into, dict):
        return
    for k, v in dd.items():
        ks = str(k or "").strip()
        if not ks or _DD_PDF_FIELD_ID_KEY.match(ks):
            continue
        v = _unwrap_dynamic_data_cell_value(v)
        if v in (None, "") or isinstance(v, (dict, list, bool)):
            continue
        if into.get(ks) not in (None, ""):
            continue
        into[ks] = _coerce_iso_datetime_value_for_date_part_key(ks, v)


def _template_id_from_parsed_template(parsed: dict | None) -> str:
    if not isinstance(parsed, dict):
        return ""
    meta = parsed.get("template_meta") if isinstance(parsed.get("template_meta"), dict) else {}
    return str(meta.get("templateId") or parsed.get("templateId") or "").strip()


def _resolve_parsed_site_template_for_payload(
    pay: dict,
    site_chain: list[tuple[Any, dict]],
    index: int,
) -> dict | None:
    """
    将单份检测提交与库中现场记录模板 JSON 配对：优先 templateId 一致，否则按勾选顺序与来源任务链下标对齐。
    """
    if not site_chain:
        return None
    tid = str(pay.get("templateId") or "").strip()
    if tid:
        for _st, parsed in site_chain:
            if not isinstance(parsed, dict):
                continue
            if _template_id_from_parsed_template(parsed) == tid:
                return parsed
    j = min(max(index, 0), len(site_chain) - 1)
    _st2, parsed2 = site_chain[j]
    return parsed2 if isinstance(parsed2, dict) else None


def _build_site_template_dynamic_semantic_mapping(parsed: dict, source_data: dict) -> dict[str, Any]:
    """
    单份现场记录库模板 JSON（parse_template_json）：先按模板自身 steps + pdf.fields
    用 **本模板语境下** 的 pdfFieldId 解释 submit.dynamicData，产出「占位符 / id / title 词条 → 值」。
    另将 dynamicData 中以非 f 号命名的键视为占位符直挂（多模板场景勿依赖 f 号跨表映射）。
    """
    out: dict[str, Any] = {}
    sd = source_data
    dd0 = source_data.get("dynamicData") if isinstance(source_data.get("dynamicData"), dict) else None
    if isinstance(dd0, dict) and dd0:
        sd = dict(source_data)
        sd["dynamicData"] = _normalize_dynamic_data_f_slots(dd0)
    steps = _parsed_template_steps_list(parsed)
    if steps:
        amb = _collect_ambiguous_pdf_field_ids_from_steps(steps)
        chunk = _build_dynamic_data_semantic_mapping_from_steps(steps, sd, amb)
        _merge_mapping_fill_empty(out, chunk)
    site_fields = parsed.get("fields") if isinstance(parsed.get("fields"), list) else []
    if site_fields:
        amb2 = _collect_ambiguous_pdf_field_ids_from_flat_fields(site_fields)
        chunk2 = _build_dynamic_data_semantic_mapping_from_flat_pdf_fields(
            site_fields, sd, amb2
        )
        _merge_mapping_fill_empty(out, chunk2)
    dd = sd.get("dynamicData")
    if isinstance(dd, dict):
        _merge_dynamic_data_placeholder_named_keys(dd, out)
    return out


def _build_dynamic_data_semantic_mapping(
    source_data: dict, ambiguous_pdf_ids: AbstractSet[str] | None = None
) -> dict[str, Any]:
    """
    将 submit.dynamicData 经 **提交 JSON 自带的 steps**（若有）转挂到控件词条。
    落库提交常不含 steps，此时应依赖 `_build_site_template_dynamic_semantic_mapping`（库模板）。
    """
    steps = source_data.get("steps")
    if not isinstance(steps, list):
        return {}
    amb = ambiguous_pdf_ids
    if amb is None:
        amb = _collect_ambiguous_pdf_field_ids_from_steps(steps)
    return _build_dynamic_data_semantic_mapping_from_steps(steps, source_data, amb)


def _collect_signature_values(source_data: dict) -> dict[str, str]:
    """
    从 submit payload 中收集签名值：
    1) 顶层 signatures 对象（兼容 author/reviewer/approver 及扩展角色）
    2) 前端回传的 schema 字段（type=signature）中提取 value/defaultValue，并按 submitPath 归并
    """
    out: dict[str, str] = {}

    # 1) 顶层 signatures
    signatures = source_data.get("signatures")
    if isinstance(signatures, dict):
        for k, v in signatures.items():
            key = str(k or "").strip()
            if key and _is_signature_image_text(v):
                out[key] = v

    # 2) schema fields where type=signature
    steps = source_data.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            sections = step.get("sections")
            if not isinstance(sections, list):
                continue
            for sec in sections:
                if not isinstance(sec, dict):
                    continue
                fields = sec.get("fields")
                if not isinstance(fields, list):
                    continue
                for fld in fields:
                    if not isinstance(fld, dict):
                        continue
                    if str(fld.get("type") or "").lower() != "signature":
                        continue
                    val = fld.get("value")
                    if not _is_signature_image_text(val):
                        val = fld.get("defaultValue")
                    if not _is_signature_image_text(val):
                        continue
                    src = fld.get("source") if isinstance(fld.get("source"), dict) else {}
                    submit_path = str(src.get("submitPath") or "").strip()
                    # signatures.xxx -> xxx
                    if submit_path.startswith("signatures."):
                        k = submit_path.split(".", 1)[1].strip()
                        if k:
                            out[k] = val
                    fid = str(fld.get("id") or "").strip()
                    if fid:
                        out[fid] = val
                    # 一对多签名框：将同一签名值扩展到所有关联 pdfFieldId，
                    # 以便模板回填阶段命中每个 image 字段。
                    src_pdf_id = str(src.get("pdfFieldId") or "").strip()
                    if src_pdf_id:
                        out[src_pdf_id] = val
                    src_pdf_ids = src.get("pdfFieldIds")
                    if isinstance(src_pdf_ids, list):
                        for pid in src_pdf_ids:
                            p = str(pid or "").strip()
                            if p:
                                out[p] = val

    # 3) dynamicData 直连签名（常见为 f76/f77/f78）
    dynamic_data = source_data.get("dynamicData")
    if isinstance(dynamic_data, dict):
        for k, v in dynamic_data.items():
            key = str(k or "").strip()
            if not key:
                continue
            if _is_signature_image_text(v):
                out[key] = v

    # 常见角色别名兜底
    if "author" not in out and "inspector" in out:
        out["author"] = out["inspector"]
    if "reviewer" not in out and "checker" in out:
        out["reviewer"] = out["checker"]
    if "approver" not in out and "authorizedSignatory" in out:
        out["approver"] = out["authorizedSignatory"]
    return out


def _fill_template_fields_with_submit_enhanced(
    source_data: dict,
    template_fields: list,
    map_id: str | None = None,
    project=None,
    task_no: str = "",
    bindings: dict[str, object] | None = None,
    task_obj=None,
    inspection_case=None,
    *,
    manual_device_count: int | None = None,
    report_template_steps: list | None = None,
    template_constants: dict | None = None,
    template_enums: dict | None = None,
    template_lookup_tables: dict | None = None,
    ordered_submit_payloads: Sequence[dict] | None = None,
):
    """
    增强回填逻辑（以 submitted JSON 为主）：
    1) value_mapping 含库现场模板解释的 dynamicData→占位符词条，及 field_to_pdf 等；
    2) 优先按 id/placeholder/title 精确命中，其次按占位符关键词与词条键（含包含、去单位相似度）匹配；
    3) pdfFieldId 仅在上述之后作为兜底，减少多模板合并后 f 编号串位；
    4) 特殊「典型值」检测条件/检测结果仍可在末段做行对齐拼接；单项判定第二遍推算。
    """
    signature_values = _collect_signature_values(source_data)
    value_mapping = _prepare_backfill_value_mapping(
        source_data,
        map_id=map_id,
        bindings=bindings if isinstance(bindings, dict) else {},
        project=project,
        task_no=task_no,
        task_obj=task_obj,
        inspection_case=inspection_case,
        report_template_fields=template_fields if isinstance(template_fields, list) else [],
        manual_device_count=manual_device_count,
        report_template_steps=report_template_steps if isinstance(report_template_steps, list) else [],
        template_constants=template_constants if isinstance(template_constants, dict) else {},
        ordered_submit_payloads=ordered_submit_payloads,
    )
    _apply_computed_fields_from_steps_to_mapping(
        value_mapping,
        report_template_steps if isinstance(report_template_steps, list) else None,
        template_constants=template_constants if isinstance(template_constants, dict) else {},
        template_enums=template_enums if isinstance(template_enums, dict) else {},
        template_lookup_tables=(
            template_lookup_tables if isinstance(template_lookup_tables, dict) else {}
        ),
    )
    _inject_kap_area_product_unit_checkbox_aliases(value_mapping, source_data)
    signature_map = {}
    if isinstance(bindings, dict):
        raw_sig_map = bindings.get("signature_map")
        if isinstance(raw_sig_map, dict):
            signature_map = raw_sig_map
    reverse_field_map = _build_field_to_pdf_reverse_index(bindings)
    flat_report_fields: list = []
    _walk_template_field_dicts(template_fields, flat_report_fields)

    try:
        from utils.verdict_from_criterion import verdict_from_measurement
    except ImportError:
        verdict_from_measurement = None

    def _scalar_fillable(v, *, allow_bool: bool = False):
        if v is None or v == "":
            return None
        if isinstance(v, bool):
            return v if allow_bool else None
        if isinstance(v, (dict, list)):
            return None
        return v

    def _pick_value_for_field(field: dict):
        if _is_single_item_verdict_text_field(field):
            return ""
        ft = (field.get("fieldType") or "text").lower()
        deferred_raw_sp = None
        candidates = _field_semantic_candidate_keys(field)
        joined_for_cond = " ".join([k for k in candidates if k]).replace("（", "(").replace("）", ")")
        # 报告「检测条件」：必须先做多行拼接/现场映射（kV、mA、尺寸等），再读 submitPath。
        # 否则 steps 误将数字域（如 testResult.test11）绑到 f49 时会抢先回填，违背「按提交 JSON 中真实检测条件项」组合的要求。
        if (
            task_obj is not None
            and getattr(task_obj, "output_target", None) == LibraryTask.OUTPUT_REPORT
            and "检测条件" in joined_for_cond
            and "判定标准" not in joined_for_cond
            and not _field_semantic_text_has_circled_number(field)
        ):
            row_cond_pri = _pick_site_row_composed_condition_or_primary_result(
                field, value_mapping, source_data, task_obj
            )
            if row_cond_pri:
                return row_cond_pri
            sim_cond = _pick_qc_condition_result_by_similarity(
                field, value_mapping, want_condition=True
            )
            if sim_cond is not None and not _value_looks_like_unresolved_qc_domain_id(sim_cond):
                return sim_cond
        src_pf = field.get("source") if isinstance(field.get("source"), dict) else {}
        sp_direct = str(src_pf.get("submitPath") or "").strip()
        if sp_direct:
            raw_sp = _nested_get_for_submit_with_rated_fallback(source_data, sp_direct)
            # submitPath=instruments 绑定整表时，_nested_get 得到 list，原先会跳过并误用同 pdfFieldId 的委托编号等。
            if str(sp_direct) == "instruments" and not _field_is_commission_or_inspection_no_slot(field):
                site_merged = report_template_steps if isinstance(report_template_steps, list) else None
                slot_lines = _collect_instrument_slot_lines_from_submit(source_data, site_merged)
                fid_raw = str(field.get("fieldId") or field.get("id") or "")
                m_slot = re.search(r"(?:仪器|instrument|testInstrument)(\d+)", fid_raw, re.I)
                # 仅当 id 含「仪器2」等下标时按槽位取单行；泛化「检测仪器」大区不得默认 idx=1 否则只填第一台。
                if m_slot:
                    idx = int(m_slot.group(1))
                    line_slot = (slot_lines.get(idx) or "").strip()
                    if line_slot:
                        if ft == "check":
                            return _coerce_picked_value_for_pdf_checkbox(field, line_slot)
                        return line_slot
                raw_list_sp = raw_sp if isinstance(raw_sp, list) else source_data.get("instruments")
                if isinstance(raw_list_sp, list):
                    if m_slot is None and ft == "text":
                        merged_lines: list[str] = []
                        if slot_lines:
                            for kix in sorted(slot_lines):
                                ln = (slot_lines.get(kix) or "").strip()
                                if ln:
                                    merged_lines.append(ln)
                        if not merged_lines:
                            for it in raw_list_sp:
                                if not isinstance(it, dict) or _instrument_submit_dict_is_empty(it):
                                    continue
                                line2 = format_instrument_display_from_submit_item(it)
                                if not (line2 or "").strip():
                                    sid0 = str(it.get("id") or it.get("instrumentId") or "").strip()
                                    if sid0:
                                        line2 = (_resolve_instrument_id_display(source_data, sid0) or "").strip()
                                if (line2 or "").strip():
                                    merged_lines.append(line2.strip())
                        if merged_lines:
                            return "；".join(merged_lines)
                    for it in raw_list_sp:
                        if not isinstance(it, dict) or _instrument_submit_dict_is_empty(it):
                            continue
                        line2 = format_instrument_display_from_submit_item(it)
                        if not (line2 or "").strip():
                            sid0 = str(it.get("id") or it.get("instrumentId") or "").strip()
                            if sid0:
                                line2 = (_resolve_instrument_id_display(source_data, sid0) or "").strip()
                        if (line2 or "").strip():
                            if ft == "check":
                                return _coerce_picked_value_for_pdf_checkbox(field, line2)
                            return line2
            if raw_sp not in (None, "") and not isinstance(raw_sp, (dict, list)):
                if _value_looks_like_unresolved_qc_domain_id(raw_sp):
                    raw_sp = None
            if raw_sp not in (None, "") and not isinstance(raw_sp, (dict, list)):
                if not _value_is_field_label_echo(field, raw_sp):
                    defer_legacy = (
                        task_obj is not None
                        and getattr(task_obj, "output_target", None) == LibraryTask.OUTPUT_REPORT
                        and _report_defer_legacy_qc_submit_path(sp_direct)
                    )
                    if defer_legacy:
                        deferred_raw_sp = raw_sp
                    else:
                        if ft == "check":
                            if isinstance(raw_sp, bool):
                                return raw_sp
                            return _coerce_picked_value_for_pdf_checkbox(field, raw_sp)
                        return _coerce_scalar_for_field_date_part_slots(field, raw_sp)
        if ft == "text":
            fid_agg = str(field.get("id") or field.get("fieldId") or "").strip()
            if fid_agg in ("检测仪器", "testInstrument"):
                ml_agg = (value_mapping.get("检测仪器列表") or value_mapping.get("检测仪器汇总") or "").strip()
                if ml_agg:
                    return ml_agg
        for k in candidates:
            if k and k in value_mapping:
                got = _scalar_fillable(value_mapping.get(k), allow_bool=(ft == "check"))
                if got is not None and not _value_is_field_label_echo(field, got):
                    if _reject_commission_org_enum_as_textfield_value(field, ft, got):
                        continue
                    return got
        kw = _pick_value_by_placeholder_keyword_in_mapping(
            field, value_mapping, allow_bool=(ft == "check")
        )
        if kw is not None and kw != "":
            if not _reject_commission_org_enum_as_textfield_value(field, ft, kw):
                return kw
        for k in candidates:
            mapped_fid = reverse_field_map.get(k)
            if mapped_fid and mapped_fid in value_mapping:
                got = _scalar_fillable(value_mapping.get(mapped_fid), allow_bool=(ft == "check"))
                if got is not None and not _value_is_field_label_echo(field, got):
                    if _reject_commission_org_enum_as_textfield_value(field, ft, got):
                        continue
                    return got
        # 报告模板 pdfFieldId 与提交 dynamicData 不按名对齐，禁止在此用报告域 id 直查 dynamicData；
        # 值应已通过现场记录模板写入 value_mapping 的占位符词条。
        # 语义兜底：设备编号(SN/序列号/产品编号) 等同义标题统一回填 equipmentInfo.serialNo。
        joined = " ".join([k for k in candidates if k]).replace("（", "(").replace("）", ")")
        if (
            ("设备编号" in joined and ("SN" in joined or "序列号" in joined or "产品编号" in joined))
            or ("serialno" in joined.lower())
        ):
            ei2 = source_data.get("equipmentInfo") if isinstance(source_data.get("equipmentInfo"), dict) else {}
            serial_no = (
                value_mapping.get("serialNo")
                or ei2.get("serialNo")
                or ei2.get("noDevice")
                or ""
            )
            return serial_no
        if "额定参数" in joined:
            jc = joined.replace(" ", "")
            is_kv_cell = "_kV" in jc or joined.rstrip().endswith("kV")
            is_ma_cell = "_mA" in jc or joined.rstrip().endswith("mA")
            if not is_kv_cell and not is_ma_cell:
                kv_r = _value_mapping_scalar_str(value_mapping, "额定参数_kV") or _value_mapping_scalar_str(
                    value_mapping, "额定kV"
                )
                ma_r = _value_mapping_scalar_str(value_mapping, "额定参数_mA") or _value_mapping_scalar_str(
                    value_mapping, "额定mA"
                )
                ei2 = source_data.get("equipmentInfo") if isinstance(source_data.get("equipmentInfo"), dict) else {}
                tr2 = source_data.get("testResult") if isinstance(source_data.get("testResult"), dict) else {}
                if not kv_r:
                    rkv = ei2.get("kv") if ei2.get("kv") not in (None, "") else tr2.get("kv")
                    kv_r = "" if rkv in (None, "") else str(rkv).strip()
                if not ma_r:
                    rma = ei2.get("ma") if ei2.get("ma") not in (None, "") else tr2.get("ma")
                    ma_r = "" if rma in (None, "") else str(rma).strip()
                segs = []
                if kv_r:
                    segs.append(f"{kv_r}kV")
                if ma_r:
                    segs.append(f"{ma_r}mA")
                if segs:
                    return "；".join(segs)
            elif is_kv_cell and not is_ma_cell:
                ei2 = source_data.get("equipmentInfo") if isinstance(source_data.get("equipmentInfo"), dict) else {}
                tr2 = source_data.get("testResult") if isinstance(source_data.get("testResult"), dict) else {}
                got = _scalar_fillable(ei2.get("kv") if ei2.get("kv") not in (None, "") else tr2.get("kv"))
                return "" if got is None else got
            elif is_ma_cell and not is_kv_cell:
                ei2 = source_data.get("equipmentInfo") if isinstance(source_data.get("equipmentInfo"), dict) else {}
                tr2 = source_data.get("testResult") if isinstance(source_data.get("testResult"), dict) else {}
                got = _scalar_fillable(ei2.get("ma") if ei2.get("ma") not in (None, "") else tr2.get("ma"))
                return "" if got is None else got
        if "项目名称" in joined:
            return value_mapping.get("项目名称") or value_mapping.get("projectTitle") or ""
        if "受检设备台数" in joined:
            return value_mapping.get("受检设备台数") or value_mapping.get("deviceCount") or ""
        if "签发" in joined and "日期" in joined:
            return (
                value_mapping.get("签发日期")
                or value_mapping.get("报告签发日期")
                or value_mapping.get("报告日期")
                or ""
            )
        if "委托单位名称" in joined or joined.rstrip().endswith("委托单位名称"):
            return (
                value_mapping.get("委托单位名称")
                or value_mapping.get("委托单位_委托单位名称")
                or value_mapping.get("commissionOrganization")
                or ""
            )
        if "联系电话" in joined:
            return (
                value_mapping.get("联系电话")
                or value_mapping.get("联系人电话")
                or value_mapping.get("电话")
                or ""
            )
        if "联系人" in joined and "联系电话" not in joined:
            if "委托单位联系人" in joined or "联系人/" in joined:
                return (
                    value_mapping.get("委托单位联系人/电话")
                    or value_mapping.get("commissionContactPhone")
                    or ""
                )
            return value_mapping.get("联系人") or value_mapping.get("委托单位联系人") or ""
        if not _field_semantic_text_has_circled_number(field):
            if "检测条件" in joined and (
                task_obj is None
                or getattr(task_obj, "output_target", None) != LibraryTask.OUTPUT_REPORT
            ):
                row_cond_first = _pick_site_row_composed_condition_or_primary_result(
                    field, value_mapping, source_data, task_obj
                )
                if row_cond_first:
                    return row_cond_first
                sim = _pick_qc_condition_result_by_similarity(
                    field, value_mapping, want_condition=True
                )
                if sim is not None:
                    return sim
            if "检测结果" in joined or "计算结果" in joined or "报出值" in joined:
                sim = _pick_qc_condition_result_by_similarity(
                    field, value_mapping, want_condition=False
                )
                if sim is not None:
                    return sim
        row_aligned = _pick_site_row_composed_condition_or_primary_result(
            field, value_mapping, source_data, task_obj
        )
        if row_aligned:
            return row_aligned
        pid0 = _field_pdf_id(field)
        if pid0:
            got0 = _scalar_fillable(value_mapping.get(pid0), allow_bool=(ft == "check"))
            if got0 is not None and not _value_is_field_label_echo(field, got0):
                if _reject_commission_org_enum_as_textfield_value(field, ft, got0):
                    pass
                elif ft == "check" and isinstance(got0, str):
                    return _coerce_picked_value_for_pdf_checkbox(field, got0)
                else:
                    return got0
        if deferred_raw_sp is not None and not isinstance(
            deferred_raw_sp, bool
        ) and not _value_is_field_label_echo(field, deferred_raw_sp):
            if ft == "check":
                return _coerce_picked_value_for_pdf_checkbox(field, deferred_raw_sp)
            return _coerce_scalar_for_field_date_part_slots(field, deferred_raw_sp)
        return ""

    def _report_merge_condition_result_text(field: dict, picked) -> str:
        """
        报告模板中「检测条件/检测结果」类字段：仅用 submit 映射到的 picked 与 PDF 原文数字位对齐合并。
        无提交值时不得把 PDF 整段原文写入 content（否则整页像占位符）。
        picked 可能为 int/float（value_mapping 数值），须先转 str。
        """
        picked_s = "" if picked is None else str(picked).strip()
        if not task_obj or task_obj.output_target != LibraryTask.OUTPUT_REPORT:
            return picked_s
        if _field_semantic_text_has_circled_number(field):
            return picked_s
        keys = [k for k in _field_candidate_keys(field) if k]
        joined = " ".join(keys).replace("（", "(").replace("）", ")")
        if "检测条件" not in joined and "检测结果" not in joined:
            return picked_s
        tmpl = str(field.get("originalPlaceholder") or field.get("placeholder") or "").strip()
        if not picked_s:
            return ""
        if _value_looks_like_unresolved_qc_domain_id(picked_s):
            return ""
        # 术者位防护区：PDF 占位符常为整段域 id（含 60/120/141 等多段数字），做数字位替换会把现场值打乱或拟合成“假占位符”。
        if "透视防护区" in joined and "术者位" in joined:
            return picked_s
        if not tmpl or not re.search(r"\d", tmpl):
            return picked_s
        return merge_number_tokens_from_source(tmpl, picked_s)

    def _auto_verdict_text_if_applicable(field: dict, picked: str) -> str:
        """
        优先：模板 steps 上 type=verdict 的 `rule` 表达式（与前端动态表单一致）求布尔值 → passLabel/failLabel。
        否则：模板字段含判定标准（judgmentCriterionText 等）时，按同行测量值与标准句走 verdict_from_criterion。
        """
        rule_fe = str(field.get("rule") or "").strip()
        if rule_fe:
            try:
                from utils.dynamic_form_expression import evaluate_verdict_rule

                vb = evaluate_verdict_rule(
                    rule_fe,
                    value_mapping,
                    constants=template_constants if isinstance(template_constants, dict) else {},
                    enums=template_enums if isinstance(template_enums, dict) else {},
                    lookup_tables=(
                        template_lookup_tables if isinstance(template_lookup_tables, dict) else {}
                    ),
                    row=None,
                )
            except Exception:
                vb = None
            if vb is True:
                return str(field.get("passLabel") or "合格").strip()
            if vb is False:
                return str(field.get("failLabel") or "不合格").strip()
        if not verdict_from_measurement:
            return picked
        try:
            from utils.verdict_from_criterion import parse_first_number as _v_parse_num
        except ImportError:
            _v_parse_num = None
        crit = _get_judgment_criterion_text(
            field,
            value_mapping,
            report_template_steps if isinstance(report_template_steps, list) else None,
            source_data,
        )
        if not crit:
            sp_nc = str((field.get("source") or {}).get("submitPath") or "").strip()
            if sp_nc:
                fb0 = _nested_get_for_submit_with_rated_fallback(source_data, sp_nc)
                if fb0 not in (None, "") and not isinstance(fb0, (dict, list)):
                    s0 = str(fb0).strip()
                    if s0 in ("合格", "不合格") and not _value_is_field_label_echo(field, fb0):
                        return s0
            return picked
        vkey = ""
        for k in _field_candidate_keys(field):
            ks = str(k or "").strip()
            if "单项判定" in ks:
                vkey = ks
                break
        if not vkey:
            return picked
        measured = ""
        vpid = _field_pdf_id(field)
        peer_pid = _peer_result_pdf_field_id_for_verdict(
            vpid, report_template_steps if isinstance(report_template_steps, list) else None
        )
        if peer_pid:
            pm = _find_template_field_by_pdf_field_id(template_fields, peer_pid)
            if pm:
                measured = str(pm.get("content") or "").strip()
                if _value_is_field_label_echo(pm, measured):
                    measured = ""
            if not measured:
                mpid = str(value_mapping.get(peer_pid) or "").strip()
                if pm and _value_is_field_label_echo(pm, mpid):
                    measured = ""
                else:
                    measured = mpid
        peer_keys = _verdict_measurement_placeholder_key_candidates(vkey)
        if not measured:
            for rk in peer_keys:
                peer = _find_template_field_by_semantic_key(template_fields, rk)
                if peer:
                    mc = str(peer.get("content") or "").strip()
                    if mc and not _value_is_field_label_echo(peer, mc):
                        measured = mc
                        break
        if not measured:
            seen_rk: set[str] = set()
            for rk in peer_keys:
                for cand in (rk, *tuple(_shield_zone_placeholder_key_variants(rk))):
                    ck = str(cand or "").strip()
                    if not ck or ck in seen_rk:
                        continue
                    seen_rk.add(ck)
                    mv = value_mapping.get(ck)
                    if mv not in (None, "") and not isinstance(mv, (dict, list)):
                        pseudo_pf = {
                            "id": ck,
                            "placeholder": ck,
                            "originalPlaceholder": ck,
                            "fieldType": "text",
                        }
                        if not _value_is_field_label_echo(pseudo_pf, mv):
                            measured = str(mv).strip()
                            break
                if measured not in (None, ""):
                    break
        if not measured:
            for rk in peer_keys:
                peer = _find_template_field_by_semantic_key(template_fields, rk)
                if peer:
                    measured = _pick_value_for_field(peer) or ""
                if measured not in (None, ""):
                    break
        if not measured:
            for rk in peer_keys:
                syn_m = {
                    "id": rk,
                    "placeholder": rk,
                    "originalPlaceholder": rk,
                    "fieldType": "text",
                }
                hitm = _pick_value_by_placeholder_keyword_in_mapping(
                    syn_m, value_mapping, allow_bool=False
                )
                if isinstance(hitm, str) and hitm.strip():
                    measured = hitm.strip()
                    break
        if not measured and peer_keys:
            longest_pk = max(peer_keys, key=len)
            if longest_pk:
                sim_m = _pick_qc_condition_result_by_similarity(
                    {"id": longest_pk, "placeholder": longest_pk, "fieldType": "text"},
                    value_mapping,
                    want_condition=False,
                    min_ratio=0.62,
                    min_ratio_loose=0.52,
                )
                if sim_m:
                    measured = sim_m
        if not measured:
            measured = _fuzzy_measured_text_for_verdict(vkey, value_mapping)
        if measured in (None, ""):
            return picked
        measured_s = str(measured).strip()
        for pk in peer_keys:
            if pk and measured_s == str(pk).strip():
                return picked
        if measured_s in ("合格", "不合格"):
            return measured_s
        if _placeholder_text_looks_like_criterion(measured_s):
            return picked
        if _v_parse_num is not None and _v_parse_num(measured_s) is None:
            return picked
        auto = verdict_from_measurement(str(measured), crit)
        if auto:
            return auto
        sp_ver = str((field.get("source") or {}).get("submitPath") or "").strip()
        if sp_ver:
            fb = _nested_get_for_submit_with_rated_fallback(source_data, sp_ver)
            if fb not in (None, "") and not isinstance(fb, (dict, list)):
                fbs = str(fb).strip()
                if fbs in ("合格", "不合格") and not _value_is_field_label_echo(field, fb):
                    return fbs
        return picked

    def _pick_signature_for_field(field: dict):
        candidates = _field_candidate_keys(field)
        if isinstance(signature_map, dict):
            for key in candidates:
                if not key:
                    continue
                mapped = signature_map.get(key)
                if isinstance(mapped, str) and mapped:
                    picked = signature_values.get(mapped) or ""
                    if picked:
                        return picked

        if isinstance(signature_map, dict):
            for _, row in signature_map.items():
                if not isinstance(row, dict):
                    continue
                image_path = str(row.get("image") or "").strip()
                name_path = str(row.get("name") or "").strip()
                if not image_path:
                    continue
                # 仅在字段名命中 role/name 关键词时采用该映射
                hit = False
                for key in candidates:
                    if not key:
                        continue
                    if key in ("author", "reviewer", "approver"):
                        hit = True
                    if "检测员" in key and ("preparedBy" in name_path or "author" in image_path):
                        hit = True
                    if "校核" in key and ("reviewedBy" in name_path or "reviewer" in image_path):
                        hit = True
                    if "批准" in key and ("approvedBy" in name_path or "approver" in image_path):
                        hit = True
                if not hit:
                    continue
                if image_path.startswith("signatures."):
                    sig_key = image_path.split(".", 1)[1]
                else:
                    sig_key = image_path
                if sig_key:
                    picked = signature_values.get(sig_key) or ""
                    if picked:
                        return picked

        # 优先按模板字段候选键直接命中签名池（支持 f76/f77/f78、中文 id、语义 id）
        for key in candidates:
            if not key:
                continue
            picked = signature_values.get(key) or ""
            if picked:
                return picked

        for key in candidates:
            if key in (
                "author",
                "reviewer",
                "approver",
                "inspector",
                "mainInspector",
                "checker",
                "authorizedSignatory",
                "accompanyingPerson",
            ):
                return signature_values.get(key) or ""

        # 无显式映射时按中文语义兜底，确保多页同类签名框都能命中。
        joined = " ".join([k for k in candidates if k]).strip()
        if joined:
            if ("校核" in joined) or ("复核" in joined):
                return signature_values.get("checker") or signature_values.get("reviewer") or ""
            if ("检测员" in joined) or ("检验员" in joined):
                return (
                    signature_values.get("inspector")
                    or signature_values.get("author")
                    or signature_values.get("mainInspector")
                    or ""
                )
            if ("陪同" in joined) or ("受检单位" in joined):
                return signature_values.get("accompanyingPerson") or ""
        return ""

    for field in flat_report_fields:
        if not isinstance(field, dict):
            continue
        field_type = (field.get("fieldType") or "").lower()
        if field_type == "text":
            if _is_single_item_verdict_text_field(field):
                field["content"] = ""
            else:
                picked = _pick_value_for_field(field)
                picked = _report_merge_condition_result_text(field, picked)
                if (
                    isinstance(picked, str)
                    and picked.strip()
                    and _value_is_field_label_echo(field, picked)
                ):
                    picked = ""
                if isinstance(picked, bool):
                    picked = ""
                elif picked is not None and not isinstance(picked, str):
                    picked = str(picked).strip()
                picked = _maybe_instrument_select_display(field, source_data, picked)
                field["content"] = picked or ""
            continue
        if field_type == "check":
            field["checked"] = _coerce_picked_value_for_pdf_checkbox(
                field, _pick_value_for_field(field)
            )
            continue
        if field_type == "image":
            field["imageData"] = _pick_signature_for_field(field)
    for field in flat_report_fields:
        if not isinstance(field, dict):
            continue
        if (field.get("fieldType") or "").lower() != "text":
            continue
        if not _is_single_item_verdict_text_field(field):
            continue
        c0 = field.get("content")
        field["content"] = _auto_verdict_text_if_applicable(
            field, "" if c0 is None else str(c0)
        )
    if task_obj is not None and getattr(task_obj, "output_target", None) == LibraryTask.OUTPUT_REPORT:
        if any(
            _report_template_field_shows_fail_verdict(f)
            for f in flat_report_fields
            if isinstance(f, dict)
        ):
            _patch_report_evaluation_field_after_verdicts(template_fields)
    return template_fields


def _merge_pdf_template_bindings(base: dict | None, overlay: dict | None) -> dict:
    """
    合并 HTMLPDF 模板 bindings：overlay 在同名规则上覆盖 base。
    用于把「现场记录任务」模板里的 value_rules / field_to_pdf / signature_map 并入报告模板。
    """
    base = dict(base) if isinstance(base, dict) else {}
    overlay = dict(overlay) if isinstance(overlay, dict) else {}
    if not overlay:
        return base
    if not base:
        return overlay
    out = {**base}
    br = base.get("value_rules")
    orules = overlay.get("value_rules")
    if isinstance(orules, list):
        rules_by_key: dict[str, dict] = {}
        for row in br or []:
            if isinstance(row, dict) and row.get("key"):
                rules_by_key[str(row["key"])] = row
        for row in orules:
            if isinstance(row, dict) and row.get("key"):
                rules_by_key[str(row["key"])] = row
        out["value_rules"] = list(rules_by_key.values())
    bf = base.get("field_to_pdf")
    oftp = overlay.get("field_to_pdf")
    if isinstance(oftp, list):
        by_fid: dict[str, dict] = {}
        for row in bf or []:
            if isinstance(row, dict) and row.get("fieldId"):
                by_fid[str(row["fieldId"])] = row
        for row in oftp:
            if isinstance(row, dict) and row.get("fieldId"):
                by_fid[str(row["fieldId"])] = row
        out["field_to_pdf"] = list(by_fid.values())
    bs = base.get("signature_map") if isinstance(base.get("signature_map"), dict) else {}
    osm = overlay.get("signature_map") if isinstance(overlay.get("signature_map"), dict) else {}
    if osm:
        out["signature_map"] = {**bs, **osm}
    for k, v in overlay.items():
        if k in ("value_rules", "field_to_pdf", "signature_map"):
            continue
        if v is not None:
            out[k] = v
    return out


def _load_merged_template_bindings_for_library_task(library_task: LibraryTask | None) -> dict:
    """
    读取某文件库任务下全部 JSON 模板中的 bindings 并合并（先旧后新，新覆盖旧）。
    不要求 fields 含坐标，便于仅有 schema/bindings 的模板参与映射。
    """
    if library_task is None:
        return {}
    acc: dict = {}
    template_qs = (
        LibraryFile.objects.filter(category=LibraryFile.CATEGORY_TEMPLATE, library_tasks=library_task)
        .order_by("created_at", "id")
        .distinct()
    )
    for lf in template_qs:
        if not (lf.original_name or "").lower().endswith(".json"):
            continue
        try:
            template_path = pipeline_service.library_absolute_path(lf.relative_path)
            raw_text = template_path.read_text(encoding="utf-8", errors="replace")
            parsed = htmlpdf_service.parse_template_json(raw_text)
        except Exception:
            continue
        b = parsed.get("bindings")
        if isinstance(b, dict) and b:
            acc = _merge_pdf_template_bindings(acc, b)
    return acc


def _merged_bindings_for_report_task(
    report_task: LibraryTask,
    project,
    task_no: str,
    report_template_bindings: dict,
) -> dict:
    """
    报告回填：在报告模板 bindings 之上，叠加上
    1) 报告任务配置的现场记录来源任务模板 JSON 中的 bindings；
    2) 当前提交 taskNo 对应的现场记录任务模板（若提交来自现场记录任务，与 schema 一致）。
    顺序：来源任务（配置顺序）→ 提交关联现场任务 → 报告模板（最后生效，报告专用规则优先）。
    """
    from apps.api.inspection_pdf_service import _resolve_library_task_for_task_no

    if report_task.output_target != LibraryTask.OUTPUT_REPORT:
        return report_template_bindings if isinstance(report_template_bindings, dict) else {}
    if project is None:
        return report_template_bindings if isinstance(report_template_bindings, dict) else {}
    acc: dict = {}
    for st in _report_site_record_source_tasks(project, report_task):
        acc = _merge_pdf_template_bindings(acc, _load_merged_template_bindings_for_library_task(st))
    assign_task = _resolve_library_task_for_task_no(task_no, project)
    if assign_task is not None and assign_task.output_target == LibraryTask.OUTPUT_SITE_RECORD:
        acc = _merge_pdf_template_bindings(acc, _load_merged_template_bindings_for_library_task(assign_task))
    return _merge_pdf_template_bindings(acc, report_template_bindings if isinstance(report_template_bindings, dict) else {})


def _build_filled_template_fields_for_task(
    task_obj,
    payload: dict,
    map_id: str | None = None,
    project=None,
    task_no: str = "",
    inspection_case=None,
    *,
    manual_device_count: int | None = None,
    ordered_submit_payloads: Sequence[dict] | None = None,
):
    if task_obj is None:
        return None, None, "未找到可用任务模板", ""
    template_qs = (
        LibraryFile.objects.filter(category=LibraryFile.CATEGORY_TEMPLATE, library_tasks=task_obj)
        .order_by("-created_at")
        .distinct()
    )
    template_json_rows = [row for row in template_qs if row.original_name.lower().endswith(".json")]
    if not template_json_rows:
        return None, None, "任务下缺少 JSON 模板", ""
    candidates = []
    for template_json_lf in template_json_rows:
        try:
            template_path = pipeline_service.library_absolute_path(template_json_lf.relative_path)
            raw_text = template_path.read_text(encoding="utf-8", errors="replace")
            parsed = htmlpdf_service.parse_template_json(raw_text)
        except Exception:
            continue
        fields = parsed.get("fields") or []
        if not isinstance(fields, list) or not fields:
            continue
        report_steps = parsed.get("steps") if isinstance(parsed.get("steps"), list) else []
        _attach_step_schema_to_pdf_fields(fields, report_steps)
        form_schema = parsed.get("form_schema") if isinstance(parsed.get("form_schema"), dict) else {}
        tpl_constants: dict = {}
        if isinstance(form_schema.get("constants"), dict):
            tpl_constants = dict(form_schema["constants"])
        elif isinstance(parsed.get("constants"), dict):
            tpl_constants = dict(parsed["constants"])
        tpl_enums: dict = {}
        if isinstance(form_schema.get("enums"), dict):
            tpl_enums = dict(form_schema["enums"])
        elif isinstance(parsed.get("enums"), dict):
            tpl_enums = dict(parsed["enums"])
        tpl_lookup: dict = {}
        if isinstance(form_schema.get("lookupTables"), dict):
            tpl_lookup = dict(form_schema["lookupTables"])
        elif isinstance(parsed.get("lookupTables"), dict):
            tpl_lookup = dict(parsed["lookupTables"])
        has_layout_field = any(
            isinstance(f, dict)
            and (
                (
                    "page" in f
                    and (
                        all(k in f for k in ("x0", "y0", "x1", "y1"))
                        or all(k in f for k in ("x", "y", "w", "h"))
                    )
                )
                or (
                    isinstance(f.get("rect"), (list, tuple))
                    and len(f.get("rect") or []) >= 5
                )
            )
            for f in fields
        )
        if not has_layout_field:
            continue
        source_pdf_template_id = None
        source_pdf = parsed.get("source_pdf") or {}
        if isinstance(source_pdf, dict):
            raw_pdf_id = source_pdf.get("template_file_id")
            try:
                source_pdf_template_id = int(raw_pdf_id) if raw_pdf_id is not None else None
            except (TypeError, ValueError):
                source_pdf_template_id = None
        template_bindings = parsed.get("bindings") or {}
        candidates.append(
            (
                template_json_lf,
                fields,
                source_pdf_template_id,
                template_bindings,
                report_steps,
                tpl_constants,
                tpl_enums,
                tpl_lookup,
            )
        )
    if not candidates:
        return None, None, "任务下 JSON 模板不是 HTMLPDF 字段模板（fields 缺少 page 与坐标）", ""
    if len(candidates) > 1:
        return None, None, "任务下存在多个可用 HTMLPDF JSON 模板，请只保留一个", ""
    (
        template_json_lf,
        fields,
        source_pdf_template_id,
        template_bindings,
        report_steps,
        tpl_constants,
        tpl_enums,
        tpl_lookup,
    ) = candidates[0]
    template_bindings = _merged_bindings_for_report_task(
        task_obj, project, task_no, template_bindings if isinstance(template_bindings, dict) else {}
    )
    payload_eff = merge_task_template_bound_instruments_into_payload(payload, task_obj)
    return (
        _fill_template_fields_with_submit(
            payload_eff,
            fields,
            map_id,
            project=project,
            task_no=task_no,
            bindings=template_bindings,
            task_obj=task_obj,
            inspection_case=inspection_case,
            manual_device_count=manual_device_count,
            report_template_steps=report_steps,
            template_constants=tpl_constants,
            template_enums=tpl_enums,
            template_lookup_tables=tpl_lookup,
            ordered_submit_payloads=ordered_submit_payloads,
        ),
        source_pdf_template_id,
        "",
        template_json_lf.original_name,
    )


def _build_filled_template_fields_from_submit(task_no: str, project, payload: dict, map_id: str | None = None):
    from apps.api.inspection_pdf_service import _resolve_library_task_for_task_no

    task_obj = _resolve_library_task_for_task_no(task_no, project)
    if task_obj is None:
        return None, None, "未找到对应任务，无法按任务模板导出", ""
    return _build_filled_template_fields_for_task(
        task_obj,
        payload,
        map_id=map_id,
        project=project,
        task_no=task_no,
    )
def _report_site_record_source_tasks(project, report_task):
    if report_task is not None:
        lst = list(
            report_task.report_source_tasks.filter(output_target=LibraryTask.OUTPUT_SITE_RECORD).order_by("code", "id")
        )
        if lst:
            return lst
    return list(project.library_tasks.filter(output_target=LibraryTask.OUTPUT_SITE_RECORD).order_by("code", "id"))


def _load_first_parsed_template_json_for_task(library_task: LibraryTask | None) -> dict | None:
    """读取任务下首个含 fields 的 JSON 模板（不要求坐标），用于 submitPath 与 bindings。"""
    if library_task is None:
        return None
    template_qs = (
        LibraryFile.objects.filter(category=LibraryFile.CATEGORY_TEMPLATE, library_tasks=library_task)
        .order_by("-created_at", "-id")
        .distinct()
    )
    for lf in template_qs:
        if not (lf.original_name or "").lower().endswith(".json"):
            continue
        try:
            template_path = pipeline_service.library_absolute_path(lf.relative_path)
            raw_text = template_path.read_text(encoding="utf-8", errors="replace")
            parsed = htmlpdf_service.parse_template_json(raw_text)
        except Exception:
            continue
        fields = parsed.get("fields")
        if isinstance(fields, list) and fields:
            return parsed if isinstance(parsed, dict) else None
    return None


def _iter_site_record_parsed_templates_for_report(
    task_no: str, project, report_task: LibraryTask | None
):
    """
    按提交 taskNo 与报告任务解析「对应现场记录模板」JSON（解析顺序）：
    1) 当前提交所属现场记录任务（与 submitted json 同源）；
    2) 报告任务配置的现场记录来源任务；
    3) 项目下其余现场记录任务（_report_site_record_source_tasks 顺序）。
    """
    from apps.api.inspection_pdf_service import _resolve_library_task_for_task_no

    if project is None or report_task is None or report_task.output_target != LibraryTask.OUTPUT_REPORT:
        return
    seen: set[int] = set()
    ordered: list[LibraryTask] = []
    assign = _resolve_library_task_for_task_no(task_no, project)
    if assign is not None and assign.output_target == LibraryTask.OUTPUT_SITE_RECORD:
        ordered.append(assign)
        seen.add(int(assign.pk))
    for st in _report_site_record_source_tasks(project, report_task):
        tid = int(st.pk)
        if tid not in seen:
            ordered.append(st)
            seen.add(tid)
    for st in ordered:
        parsed = _load_first_parsed_template_json_for_task(st)
        if parsed is not None:
            yield st, parsed


def _iter_parsed_templates_for_backfill(task_no: str, project, task_obj):
    """
    回填前解析「应作为 pdfFieldId 语义参照」的库模板：
    - 输出目标为报告：沿用现场记录来源任务链（与提交 taskNo 对齐）；
    - 输出目标为现场记录：仅当前任务自身 JSON 模板（与导出 PDF 为同一份坐标/域定义）。
    """
    if task_obj is None or project is None:
        return
    if getattr(task_obj, "output_target", None) == LibraryTask.OUTPUT_REPORT:
        yield from _iter_site_record_parsed_templates_for_report(task_no, project, task_obj)
        return
    if getattr(task_obj, "output_target", None) == LibraryTask.OUTPUT_SITE_RECORD:
        parsed = _load_first_parsed_template_json_for_task(task_obj)
        if parsed is not None:
            yield task_obj, parsed


def _merged_site_steps_for_instrument_backfill(
    task_no: str,
    project,
    task_obj,
    *,
    report_steps: list | None = None,
) -> list:
    """合并回填链上各份库模板的 steps，并可选附上报告模板 steps，供 instrument_select + pdfFieldId 解析。"""
    merged: list = []
    if task_obj is None or project is None:
        if isinstance(report_steps, list):
            merged.extend(report_steps)
        return merged
    for _st, parsed in _iter_parsed_templates_for_backfill(task_no, project, task_obj):
        merged.extend(_parsed_template_steps_list(parsed))
    if isinstance(report_steps, list):
        merged.extend(report_steps)
    return merged


def _prepare_backfill_value_mapping(
    source_data: dict,
    *,
    map_id: str | None,
    bindings: dict,
    project,
    task_no: str,
    task_obj,
    inspection_case,
    report_template_fields: list,
    manual_device_count: int | None = None,
    report_template_steps: list | None = None,
    template_constants: dict | None = None,
    ordered_submit_payloads: Sequence[dict] | None = None,
) -> dict:
    """
    回填值准备（以 submitted JSON 为主）：
    1. 默认 submit → 占位符映射；
    2. 合并后模板 bindings.value_rules（仍只从提交 JSON 取值）；
    3. **库中现场记录模板**：steps 与 pdf.fields 上的 submitPath → 语义键（仍用合并后的 source_data）；
       dynamicData：**多份提交时**按勾选顺序将「每一份的 dynamicData」仅在 **对应现场模板** 内用 pdfFieldId
       转为 placeholder/id 词条后合并（仅补缺），**禁止**把不同模板的 f 号混进同一张 dynamicData 再映射；
       若 dynamicData 已直接以占位符长键命名，亦并入同一语义层（见 _merge_dynamic_data_placeholder_named_keys）。
    4. 报告模板 fields 的 submitPath（仅补缺词条对齐）；
    5. 点号路径别名（如 hospitalInfo.name）；
    6. 提交 JSON 内嵌 steps（若有）对 dynamicData 再补缺；
    7. 项目/任务派生字段；
    8. 再次按报告模板 submitPath 强制覆盖；报告控件取值仅通过占位符/标题等与 value_mapping 词条匹配。
    """
    steps_for_report = list(report_template_steps) if isinstance(report_template_steps, list) else []
    const_for_tpl = template_constants if isinstance(template_constants, dict) else {}
    mid = (map_id or "").strip() or DEFAULT_SUBMIT_PLACEHOLDER_MAP_ID
    value_mapping: dict = dict(build_submit_value_mapping(source_data, mid))
    binding_map = _build_template_binding_value_mapping(source_data, bindings or {})
    if binding_map:
        value_mapping.update(binding_map)
    if task_obj is not None and project is not None:
        merged_site_sem: dict = {}
        site_chain = list(_iter_parsed_templates_for_backfill(task_no, project, task_obj))
        for _st, parsed in site_chain:
            steps = _parsed_template_steps_list(parsed)
            flat_schema = _flatten_unified_form_steps_to_fields(steps)
            if flat_schema:
                _apply_template_field_sources_to_mapping(value_mapping, source_data, flat_schema)
            _inject_step_section_qc_key_aliases(value_mapping, steps)
            site_fields = parsed.get("fields") or []
            if isinstance(site_fields, list):
                _apply_template_field_sources_to_mapping(value_mapping, source_data, site_fields)

        layered = (
            list(ordered_submit_payloads)
            if ordered_submit_payloads is not None and len(ordered_submit_payloads) > 0
            else None
        )
        if layered:
            for i, pay in enumerate(layered):
                if not isinstance(pay, dict):
                    continue
                parsed_one = _resolve_parsed_site_template_for_payload(pay, site_chain, i)
                if parsed_one is None:
                    continue
                dd = pay.get("dynamicData") if isinstance(pay.get("dynamicData"), dict) else {}
                _merge_mapping_fill_empty(
                    merged_site_sem,
                    _build_site_template_dynamic_semantic_mapping(parsed_one, {"dynamicData": dd}),
                )
        else:
            for _st, parsed in site_chain:
                _merge_mapping_fill_empty(
                    merged_site_sem, _build_site_template_dynamic_semantic_mapping(parsed, source_data)
                )
        _merge_site_dynamic_semantics_into_value_mapping(value_mapping, merged_site_sem)
    _skip_rl_sp = task_obj is not None and getattr(
        task_obj, "output_target", None
    ) == LibraryTask.OUTPUT_REPORT
    if isinstance(report_template_fields, list):
        _apply_template_field_sources_to_mapping(
            value_mapping,
            source_data,
            report_template_fields,
            skip_report_legacy_qc_submit_path=_skip_rl_sp,
        )
    if steps_for_report:
        flat_rep_steps = _flatten_unified_form_steps_to_fields(steps_for_report)
        if flat_rep_steps:
            _apply_template_field_sources_to_mapping(
                value_mapping,
                source_data,
                flat_rep_steps,
                skip_report_legacy_qc_submit_path=_skip_rl_sp,
            )
        _inject_step_section_qc_key_aliases(value_mapping, steps_for_report)
    _inject_verdict_criteria_from_form_constants(value_mapping, const_for_tpl)
    _inject_default_report_row_verdict_criteria(value_mapping)
    _inject_shield_zone_operator_row_criteria(value_mapping, const_for_tpl)
    _inject_submit_dot_path_aliases(value_mapping, source_data)
    _merge_mapping_fill_empty(value_mapping, _build_dynamic_data_semantic_mapping(source_data))
    # 派生字段仅补缺：避免覆盖现场模板 submitPath 已写入的词条（如 JS-117 受检单位在 inspection，
    # JS-001 在 inspection2；统一用「模板先写、派生后补」避免互相压错）。
    _merge_mapping_fill_empty(
        value_mapping,
        _build_submit_derived_value_mapping(
            source_data,
            project=project,
            task_no=task_no,
            task_obj=task_obj,
            inspection_case=inspection_case,
            manual_device_count=manual_device_count,
        ),
    )
    if isinstance(report_template_fields, list):
        _apply_template_field_sources_to_mapping(
            value_mapping,
            source_data,
            report_template_fields,
            overwrite=True,
            skip_report_legacy_qc_submit_path=_skip_rl_sp,
        )
    if (
        task_obj is not None
        and getattr(task_obj, "output_target", None) == LibraryTask.OUTPUT_REPORT
        and isinstance(bindings, dict)
    ):
        from apps.core.htmlpdf_report_mapping_service import apply_report_site_field_map_to_value_mapping

        apply_report_site_field_map_to_value_mapping(
            value_mapping,
            source_data,
            bindings,
            report_parsed_template={
                "fields": report_template_fields if isinstance(report_template_fields, list) else []
            },
        )
    _inject_hospital_equipment_cn_aliases(value_mapping, source_data)
    _inject_radio_enum_slug_checkbox_aliases(value_mapping, source_data)
    _inject_dose_rate_unit_mutex_pdf_aliases(
        value_mapping,
        source_data,
        extra_triplets=_iter_dose_rate_unit_mutex_triplets_from_steps(
            _merged_site_steps_for_instrument_backfill(
                task_no, project, task_obj, report_steps=steps_for_report or None
            )
        ),
    )
    _inject_unified_yesno_to_library_dsa_artifact_pdf_aliases(value_mapping, source_data)
    _inject_kerma_max_high_dose_mode_pdf_aliases(value_mapping, source_data)
    _inject_environment_location_backfill_aliases(value_mapping, source_data)
    _inject_abc_circled_reading_aliases(value_mapping, source_data)
    _inject_unified_template_brightness_submitpath_aliases(value_mapping, source_data)
    submit_steps = source_data.get("steps")
    if isinstance(submit_steps, list):
        _inject_step_section_qc_key_aliases(value_mapping, submit_steps)
    _inject_shield_zone_report_site_key_aliases(value_mapping)
    _inject_shield_zone_distance_agnostic_aliases(value_mapping)
    inst_aliases = _build_instrument_text_aliases_from_submit(
        source_data,
        _merged_site_steps_for_instrument_backfill(
            task_no, project, task_obj, report_steps=steps_for_report or None
        ),
        task_obj=task_obj,
    )
    _merge_mapping_fill_empty(value_mapping, inst_aliases)
    # 单 PDF 文本格 id「检测仪器」常与委托编号等同槽 f1，submitPath 未挂上 instruments 时只会命中单行；强制与列表别名一致。
    _ins_merged = (inst_aliases.get("检测仪器列表") or inst_aliases.get("检测仪器汇总") or "").strip()
    if _ins_merged:
        value_mapping["检测仪器"] = _ins_merged
    # 与检测仪器同槽 f1 时，仪器合并串可能写入 f1，覆盖委托编号；用提交中的委托编号恢复。
    ri0 = source_data.get("reportInfo") if isinstance(source_data.get("reportInfo"), dict) else {}
    dd0 = source_data.get("dynamicData") if isinstance(source_data.get("dynamicData"), dict) else {}
    com_fill = str(ri0.get("commissionNo") or dd0.get("f1") or "").strip()
    if com_fill and "；" not in com_fill:
        value_mapping.setdefault("委托编号", com_fill)
        value_mapping.setdefault("commissionNo", com_fill)
        vf1 = str(value_mapping.get("f1") or "").strip()
        if not vf1:
            value_mapping["f1"] = com_fill
        elif vf1 != com_fill and ("；" in vf1 or len(vf1) > len(com_fill) + 8):
            value_mapping["f1"] = com_fill
    # KAP 单位：须在 value_mapping 全部合并后再注入，避免后续 _merge 或步骤别名用 f56 串值覆盖布尔/长键。
    _normalize_iso_strings_in_test_date_part_slots(value_mapping)
    return value_mapping


def _resolve_report_task_for_case(task_no: str, project):
    """
    解析当前案件应使用的报告任务。
    优先规则：
    1) taskNo 直接对应且输出目标=report 的任务；
    2) 项目中第一个输出目标=report 的任务。
    """
    from apps.api.inspection_pdf_service import _resolve_library_task_for_task_no

    task_obj = _resolve_library_task_for_task_no(task_no, project)
    if task_obj is not None and task_obj.output_target == LibraryTask.OUTPUT_REPORT:
        return task_obj
    return (
        project.library_tasks.filter(output_target=LibraryTask.OUTPUT_REPORT)
        .order_by("code", "id")
        .first()
    )


def _normalize_fields_for_htmlpdf(fields):
    """扁平化嵌套 fields，并按页码、纵坐标、横坐标排序，减少叠字顺序导致的乱序。"""
    normalized = []
    skipped = 0

    def _append_one(f: dict) -> None:
        nonlocal skipped
        if not isinstance(f, dict):
            skipped += 1
            return
        page = f.get("page")
        if page in (None, ""):
            skipped += 1
            return
        if all(k in f for k in ("x0", "y0", "x1", "y1")):
            try:
                x0 = float(f.get("x0"))
                y0 = float(f.get("y0"))
                x1 = float(f.get("x1"))
                y1 = float(f.get("y1"))
            except (TypeError, ValueError):
                skipped += 1
                return
        elif all(k in f for k in ("x", "y", "w", "h")):
            try:
                x = float(f.get("x"))
                y = float(f.get("y"))
                w = float(f.get("w"))
                h = float(f.get("h"))
            except (TypeError, ValueError):
                skipped += 1
                return
            if w <= 0 or h <= 0:
                skipped += 1
                return
            x0, y0, x1, y1 = x, y, x + w, y + h
        else:
            skipped += 1
            return
        text_val = f.get("value")
        if text_val in (None, ""):
            text_val = f.get("content", "")
        row: dict = {
            "page": page,
            "x0": x0,
            "y0": y0,
            "x1": x1,
            "y1": y1,
            "fieldType": (f.get("fieldType") or "text"),
            "value": text_val or "",
            "checked": bool(f.get("checked", False)),
            "imageData": f.get("imageData") or "",
        }
        # build_filled_pdf 依赖 id/placeholder 等做「检测仪器」两端对齐；勿在导出前剥掉语义键。
        for k in ("id", "placeholder", "originalPlaceholder", "title", "label", "fieldId", "pdfFieldId"):
            v = f.get(k)
            if isinstance(v, str) and v.strip():
                row[k] = v.strip()
        normalized.append(row)

    def _walk(rows):
        for f in rows or []:
            if not isinstance(f, dict):
                continue
            _append_one(f)
            nested = f.get("fields") or f.get("children")
            if isinstance(nested, list):
                _walk(nested)

    _walk(fields)

    def _sort_key(r):
        try:
            p = int(r["page"])
        except (TypeError, ValueError):
            p = 0
        return (p, float(r.get("y0") or 0), float(r.get("x0") or 0))

    normalized.sort(key=_sort_key)
    return normalized, skipped


def _sanitize_export_pdf_name_fragment(s: str, max_len: int = 72) -> str:
    """导出 PDF 展示文件名用片段：去掉路径非法字符，压缩空白与过长片段。"""
    s = (s or "").strip()
    s = re.sub(r'[/\\:*?"<>|\r\n\x00-\x1f]', "_", s)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if len(s) > max_len:
        s = s[:max_len].rstrip("_")
    return s or "x"


def build_exported_inspection_pdf_original_name(
    project,
    task_obj,
    task_no: str,
    *,
    output_category: str,
) -> str:
    """
    用户可读导出 PDF 文件名：含项目/任务片段，并以 __task__{taskNo}-现场记录|报告.pdf 结尾，
    便于文件库「现场记录」解析 taskNo，同时兼容旧版「{taskNo}-现场记录.pdf」。
    """
    safe_task = (task_no or "").strip().replace("/", "_") or "unknown"
    kind = "报告" if output_category == LibraryFile.CATEGORY_REPORT else "现场记录"
    suffix = f"__task__{safe_task}-{kind}.pdf"
    pc = _sanitize_export_pdf_name_fragment(
        f"{getattr(project, 'code', '')}_{getattr(project, 'name', '')}", 80
    )
    tc = ""
    if task_obj is not None:
        tc = _sanitize_export_pdf_name_fragment(
            f"{getattr(task_obj, 'code', '')}_{getattr(task_obj, 'name', '')}", 96
        )
    friendly = pc if not tc else f"{pc}_{tc}"
    friendly = friendly.replace("__", "_")
    friendly = _sanitize_export_pdf_name_fragment(friendly, 140)
    max_total = 220
    if len(friendly) + len(suffix) > max_total:
        friendly = friendly[: max(1, max_total - len(suffix))].rstrip("_")
    return f"{friendly}{suffix}"


def build_merged_report_pdf_original_name(project, commission_no: str = "") -> str:
    """
    文件库「多份报告合并」导出 PDF 的展示文件名：{委托编号}{项目名称}.pdf
    委托编号优先取各份报告溯源到的 reportInfo.commissionNo（由调用方传入）；缺省时仅用项目名称。
    """
    return f"{merged_report_commission_project_basename(project, commission_no)}.pdf"


def merged_report_commission_project_basename(project, commission_no: str = "") -> str:
    """
    合并报告在文件名与封面「项目名称」第二行共用的主文案：{委托编号}{项目名称}（无分隔符）。
    空委托编号不占位；片段经路径安全过滤。与 build_report_merge_overlay 叠印一致。
    """
    com_raw = str(commission_no or "").strip()
    com = _sanitize_export_pdf_name_fragment(com_raw, 48) if com_raw else ""
    pn_raw = (getattr(project, "name", "") or "").strip() if project is not None else ""
    pn = _sanitize_export_pdf_name_fragment(pn_raw, 120) if pn_raw else ""
    core = f"{com}{pn}".strip("_")
    if not core and project is not None:
        cc = str(getattr(project, "code", "") or "").strip()
        nm = str(getattr(project, "name", "") or "").strip()
        if cc and nm:
            core = _sanitize_export_pdf_name_fragment(f"{cc}_{nm}", 140)
        elif cc or nm:
            core = _sanitize_export_pdf_name_fragment(cc or nm, 140)
    if not core:
        core = "合并报告"
    return core


def _persist_filled_pdf_from_submit(
    user,
    task_no: str,
    case: InspectionCase,
    project,
    filled_fields: list,
    template_pdf_id=None,
    template_json_name: str = "",
    task_obj=None,
):
    from apps.api.inspection_pdf_service import _resolve_library_task_for_task_no

    if not isinstance(filled_fields, list) or not filled_fields:
        return False, "无可用填充字段", None
    normalized_fields, skipped_fields = _normalize_fields_for_htmlpdf(filled_fields)
    if not normalized_fields:
        return False, "模板字段缺少有效页码或坐标（支持 x/y/w/h 或 x0/y0/x1/y1）", None
    if task_obj is None:
        task_obj = _resolve_library_task_for_task_no(task_no, project)
    if task_obj is None:
        return False, "未找到对应任务模板，无法导出", None
    template_pdf_lf = None
    if template_pdf_id:
        template_pdf_lf = (
            LibraryFile.objects.filter(pk=template_pdf_id, category=LibraryFile.CATEGORY_TEMPLATE, projects=project)
            .distinct()
            .first()
        )
    template_qs = (
        LibraryFile.objects.filter(category=LibraryFile.CATEGORY_TEMPLATE, library_tasks=task_obj)
        .order_by("-created_at")
        .distinct()
    )
    if template_pdf_lf is None:
        template_pdf_rows = [row for row in template_qs if row.original_name.lower().endswith(".pdf")]
        if not template_pdf_rows:
            return False, "项目下缺少 PDF 模板", None
        if len(template_pdf_rows) > 1:
            return False, "项目下存在多个 PDF 模板，请先保证每个任务项目仅有一个 PDF 模板", None
        template_pdf_lf = template_pdf_rows[0]
    try:
        source_pdf = pipeline_service.library_absolute_path(template_pdf_lf.relative_path)
        pdf_bytes = htmlpdf_service.build_filled_pdf(normalized_fields, source_pdf)
    except Exception as exc:
        return False, f"PDF 渲染失败: {exc}", None
    output_category = LibraryFile.CATEGORY_REPORT if task_obj.output_target == LibraryTask.OUTPUT_REPORT else LibraryFile.CATEGORY_SITE_RECORD
    filename = build_exported_inspection_pdf_original_name(
        project, task_obj, task_no, output_category=output_category
    )
    wrapped = type("UploadLike", (), {"read": lambda self: pdf_bytes, "name": filename})()
    created, _ = save_library_binary_uploads(
        user, [wrapped], output_category,
        link_entity=LibraryFile.LINK_ENTITY_INSPECTION_CASE,
        link_object_id=case.pk, project_ids=[project.pk],
        enforce_storage_quota=False,
    )
    # 导出 PDF 仅挂项目/案件，不写入任务模板 M2M（任务模板只绑定「模板」类文件）。
    if not created:
        return False, "PDF 保存失败（save_library_binary_uploads 未创建记录）", None
    new_lf = LibraryFile.objects.filter(pk=created[0]["id"]).first()
    if skipped_fields:
        return True, f"导出成功，但已跳过 {skipped_fields} 个不合法模板字段", new_lf
    return True, "", new_lf
