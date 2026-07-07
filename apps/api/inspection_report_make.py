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

from django.conf import settings
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
from utils.pdf_merge import (
    build_single_report_cover_title_line,
    extract_modality_abbr_from_title,
    merged_device_phrase_for_evaluation,
)
from utils.report_fill_helpers import (
    merge_number_tokens_from_source,
    qc_condition_text_has_unit_markers,
    split_contact_name_phone,
)

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


def _submit_text_looks_like_date_or_datetime(s: str) -> bool:
    """ISO/纯日期字符串不能当作受检单位等机构名称。"""
    t = (s or "").strip()
    if not t:
        return False
    if _parse_loose_datetime_for_submit(t) is not None:
        if re.match(r"^\d{4}-\d{2}-\d{2}", t):
            return True
        if "T" in t and re.search(r"\d{4}-\d{2}-\d{2}T", t):
            return True
    if re.fullmatch(r"\d{4}年\d{1,2}月\d{1,2}日?", t):
        return True
    return False


def _submit_text_looks_like_measurement_number(s: str) -> bool:
    """纯数值（如环境温度 27.69）不能当作单位名称。"""
    t = (s or "").strip()
    if not t:
        return False
    if re.fullmatch(r"-?\d+(?:\.\d+)?", t):
        return True
    return False


def _is_plausible_inspected_unit_name(s: str) -> bool:
    t = (s or "").strip()
    if not t:
        return False
    if _submit_text_looks_like_date_or_datetime(t):
        return False
    if _submit_text_looks_like_measurement_number(t):
        return False
    return True


def _is_plausible_commission_organization_name(s: str) -> bool:
    t = (s or "").strip()
    if not t:
        return False
    if t in ("sameInspection", "customCommission"):
        return False
    return _is_plausible_inspected_unit_name(t)


def _iter_site_template_fields(parsed: dict | None):
    """遍历现场记录 unified_form_template steps 下全部栏位。"""
    if not isinstance(parsed, dict):
        return
    steps = parsed.get("steps")
    if not isinstance(steps, list):
        form = parsed.get("formSchema") or parsed.get("form_schema")
        if isinstance(form, dict):
            steps = form.get("steps")
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
        for sec in step.get("sections") or []:
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


def _pick_ei_value_by_site_field_semantics(
    ei: dict,
    parsed: dict | None,
    *label_candidates: str,
) -> str:
    """按现场模板 hierarchyKey/label/id 定位 pdfFieldId，再从 equipmentInfo 取值。"""
    if not isinstance(ei, dict) or not label_candidates:
        return ""
    want = {str(x).strip() for x in label_candidates if str(x).strip()}
    if not want:
        return ""

    def _field_matches(fld: dict) -> bool:
        for key in ("hierarchyKey", "label", "title", "id"):
            v = str(fld.get(key) or "").strip()
            if v in want:
                return True
        return False

    fields_iter: list[dict] = []
    if isinstance(parsed, dict):
        fields_iter.extend(f for f in (_iter_site_template_fields(parsed)) if isinstance(f, dict))
        for fld in parsed.get("fields") or []:
            if isinstance(fld, dict):
                fields_iter.append(fld)
    for fld in fields_iter:
        if not _field_matches(fld):
            continue
        pid = str(fld.get("pdfFieldId") or fld.get("id") or "").strip()
        if pid and ei.get(pid) not in (None, ""):
            return str(ei.get(pid)).strip()
    return ""


def _inspected_unit_pdf_field_ids_from_site_template(parsed: dict | None) -> list[str]:
    """现场模板 hierarchyKey/label 为「受检单位」的 pdfFieldId（JS009→f6，JS001→f3）。"""
    if not isinstance(parsed, dict):
        return []
    ids: list[str] = []
    seen: set[str] = set()
    for fld in _iter_site_template_fields(parsed):
        hk = str(fld.get("hierarchyKey") or "").strip()
        label = str(fld.get("label") or fld.get("title") or fld.get("id") or "").strip()
        if hk not in ("受检单位", "受检单位名称") and label not in ("受检单位", "受检单位名称"):
            continue
        pid = str(fld.get("pdfFieldId") or fld.get("id") or "").strip().lower()
        if not re.fullmatch(r"f\d+", pid, flags=re.I) or pid in seen:
            continue
        seen.add(pid)
        ids.append(pid)
    return ids


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
    part = _test_date_ymd_slot_part_for_field(field)
    if part:
        dt = _parse_loose_datetime_for_submit(val_out)
        if dt is not None:
            return _format_test_date_ymd_slot_part(part, dt)
    for ck in _field_semantic_candidate_keys(field):
        co = _coerce_iso_datetime_value_for_date_part_key(ck, val_out)
        if co != val_out:
            return str(co)
    return val_out


def _test_date_ymd_slot_part_for_field(field: dict) -> str | None:
    """
    现场记录表头「检测日期」拆为三格：slotNo 1=年、2=月、3=日（或 f3/f4/f5、检测日期_年月日*）。
    """
    if not isinstance(field, dict):
        return None
    table = field.get("table") if isinstance(field.get("table"), dict) else {}
    try:
        slot_no = int(table.get("slotNo") or 0)
    except (TypeError, ValueError):
        slot_no = 0
    if slot_no == 1:
        return "year"
    if slot_no == 2:
        return "month"
    if slot_no == 3:
        return "day"
    fid = str(field.get("id") or field.get("pdfFieldId") or field.get("fieldId") or "").strip()
    if fid == "f3" or fid == "检测日期_年月日":
        return "year"
    if fid == "f4" or fid == "检测日期_年月日2":
        return "month"
    if fid == "f5" or fid == "检测日期_年月日3":
        return "day"
    label = str(field.get("label") or field.get("hierarchyKey") or "").strip()
    if "年月日3" in label:
        return "day"
    if "年月日2" in label:
        return "month"
    if "年月日" in label and "检测日期" in label:
        return "year"
    return None


def _format_test_date_ymd_slot_part(part: str, dt) -> str:
    if part == "year":
        return f"{int(dt.year):04d}"
    if part == "month":
        return f"{int(dt.month):02d}"
    if part == "day":
        return f"{int(dt.day):02d}"
    return ""


def _assembled_test_date_from_header_slots(
    source_data: dict,
    value_mapping: dict | None = None,
) -> datetime | None:
    """现场记录表头检测日期三格（f3/f4/f5 或 检测日期_年月日*）组装为 datetime。"""
    if not isinstance(source_data, dict):
        return None
    dd = source_data.get("dynamicData") if isinstance(source_data.get("dynamicData"), dict) else {}
    ri = source_data.get("reportInfo") if isinstance(source_data.get("reportInfo"), dict) else {}
    vm = value_mapping if isinstance(value_mapping, dict) else {}

    def _part(*keys: str) -> str:
        for k in keys:
            for src in (dd, vm, ri):
                if not isinstance(src, dict):
                    continue
                raw = src.get(k)
                if raw not in (None, ""):
                    return str(raw).strip()
        return ""

    y = _part("year", "f3", "检测日期_年月日", "检测日期_年", "检测年")
    m = _part("month", "f4", "检测日期_年月日2", "检测日期_月", "检测月")
    d = _part("day", "f5", "检测日期_年月日3", "检测日期_日", "检测日")
    if not (y and m and d):
        return None
    iso_slots = sum(
        1 for p in (y, m, d) if re.match(r"^\d{4}-\d{1,2}-\d{1,2}", str(p).strip())
    )
    if iso_slots >= 2:
        return None
    if re.match(r"^\d{4}-\d{1,2}-\d{1,2}", y) and iso_slots == 1:
        parsed = _parse_loose_datetime_for_submit(y)
        if parsed is not None:
            return parsed
    try:
        yi = int(_normalize_test_year_yyyy(y) or y)
        mi = int(str(m).strip())
        di = int(str(d).strip())
        if 1900 <= yi <= 2100 and 1 <= mi <= 12 and 1 <= di <= 31:
            return datetime(yi, mi, di, 0, 0, 0)
    except (TypeError, ValueError, OverflowError):
        return None
    return None


def _format_test_date_cn(dt: datetime) -> str:
    return f"{dt.year}年{dt.month:02d}月{dt.day:02d}日"


def _inspection_type_radio_truthy(v: object) -> bool:
    """检测类型 radio/checkbox：纯数值（如 f504=0.334）是质控读数，不是勾选项。"""
    if v is True:
        return True
    s = str(v or "").strip()
    if not s or s == "/":
        return False
    sl = s.lower()
    if sl in ("true", "1", "yes", "是", "on"):
        return True
    if sl in ("false", "0", "no", "否", "off"):
        return False
    try:
        float(s.replace(",", ""))
        return False
    except ValueError:
        pass
    return False


def _resolve_test_date_datetime(source_data: dict, value_mapping: dict | None = None) -> object | None:
    """解析提交中的检测日期（ISO 或 reportInfo/testResult 年月日）。"""
    if not isinstance(source_data, dict):
        return None
    vm = value_mapping if isinstance(value_mapping, dict) else {}
    ri = source_data.get("reportInfo") if isinstance(source_data.get("reportInfo"), dict) else {}
    tr = source_data.get("testResult") if isinstance(source_data.get("testResult"), dict) else {}
    dd = source_data.get("dynamicData") if isinstance(source_data.get("dynamicData"), dict) else {}

    dt_asm = _assembled_test_date_from_header_slots(source_data, vm)
    if dt_asm is not None:
        return dt_asm

    candidates_iso = [
        tr.get("testDate"),
        ri.get("testDate"),
        vm.get("testDate"),
        vm.get("检测日期"),
    ]
    year = str(ri.get("year") or vm.get("检测日期_年") or vm.get("检测年") or "").strip()
    month = str(ri.get("month") or vm.get("检测日期_月") or vm.get("检测月") or "").strip()
    day = str(ri.get("day") or vm.get("检测日期_日") or vm.get("检测日") or "").strip()
    for raw in candidates_iso:
        dt = _parse_loose_datetime_for_submit(raw)
        if dt is not None:
            return dt
    if year and month and day:
        try:
            return datetime(
                int(_normalize_test_year_yyyy(year) or year),
                int(str(month).strip()),
                int(str(day).strip()),
            )
        except (TypeError, ValueError):
            pass
    f4_raw = dd.get("f4")
    f5_raw = dd.get("f5")
    f3_only = f4_raw in (None, "", "/") and f5_raw in (None, "", "/")
    if f3_only:
        for raw in (dd.get("f3"), ri.get("f3"), vm.get("f3")):
            dt = _parse_loose_datetime_for_submit(raw)
            if dt is not None:
                return dt
    return _parse_source_updated_at(source_data)


def _pick_test_date_table_slot_value(
    field: dict, source_data: dict, value_mapping: dict
) -> str | None:
    """优化规则章节：检测日期三格按年/月/日回填（零填充月日）。"""
    part = _test_date_ymd_slot_part_for_field(field)
    if not part:
        return None
    dt = _resolve_test_date_datetime(source_data, value_mapping)
    if dt is None:
        return None
    return _format_test_date_ymd_slot_part(part, dt)


def _inject_test_date_split_pdf_field_aliases(value_mapping: dict, source_data: dict) -> None:
    """将检测日期拆入 f3/f4/f5 及派生键，供基本信息章按语义/pdfFieldId 读取。"""
    if not isinstance(value_mapping, dict) or not isinstance(source_data, dict):
        return
    dt = _resolve_test_date_datetime(source_data, value_mapping)
    if dt is None:
        return
    parts = {
        "year": _format_test_date_ymd_slot_part("year", dt),
        "month": _format_test_date_ymd_slot_part("month", dt),
        "day": _format_test_date_ymd_slot_part("day", dt),
    }
    slot_keys = {
        "year": ("f3", "检测日期_年月日", "检测日期_年", "检测年"),
        "month": ("f4", "检测日期_年月日2", "检测日期_月", "检测月"),
        "day": ("f5", "检测日期_年月日3", "检测日期_日", "检测日"),
    }
    for part, keys in slot_keys.items():
        val = parts.get(part) or ""
        if not val:
            continue
        for k in keys:
            if value_mapping.get(k) in (None, ""):
                value_mapping[k] = val


def _inject_report_test_date_pdf_field_aliases(
    value_mapping: dict,
    source_data: dict,
    report_template_fields: list | None = None,
) -> None:
    """报告「三、检测结果」等处的检测日期：用现场表头 f3/f4/f5 解析值覆盖误映射的 f 槽。"""
    if not isinstance(value_mapping, dict) or not isinstance(source_data, dict):
        return
    dt = _resolve_test_date_datetime(source_data, value_mapping)
    if dt is None:
        return
    date_cn = _format_test_date_cn(dt)
    value_mapping["检测日期"] = date_cn
    value_mapping["testDate"] = date_cn
    flat: list = []
    if isinstance(report_template_fields, list):
        _walk_template_field_dicts(report_template_fields, flat)
    for field in flat:
        if not isinstance(field, dict):
            continue
        blob = " ".join(
            str(field.get(k) or "")
            for k in ("id", "label", "title", "placeholder", "fieldId")
        )
        if "检测日期" not in blob:
            continue
        pid = _field_pdf_id(field)
        if pid:
            value_mapping[pid] = date_cn


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


_PREFERRED_INSPECTED_UNIT_PDF_FIELD_IDS = ("f6", "f5", "f4", "f2")
_PREFERRED_INSPECTED_ADDRESS_PDF_FIELD_IDS = ("f8", "f9", "f10", "f3", "f22")
_NON_ADDRESS_LITERALS = frozenset(
    {
        "委托单位",
        "同受检单位",
        "验收检测",
        "状态检测",
        "定期检测",
        "现场检测",
        "委托检测",
    }
)


def _scalar_submit_text_value(v: object) -> str:
    if v in (None, "") or isinstance(v, (dict, list, bool)):
        return ""
    return str(v).strip()


def _inspected_unit_from_pdf_field_slots(blob: dict, *, preferred_ids: tuple[str, ...]) -> str:
    """App/模拟提交常把受检单位写在 hospitalInfo/dynamicData 的 pdfFieldId 槽（如 f6）。"""
    if not isinstance(blob, dict):
        return ""
    for pid in preferred_ids:
        s = _scalar_submit_text_value(blob.get(pid))
        if s and _is_plausible_inspected_unit_name(s):
            return s
    for k in sorted(blob.keys()):
        if not isinstance(k, str) or not re.fullmatch(r"f\d+", k, flags=re.IGNORECASE):
            continue
        s = _scalar_submit_text_value(blob.get(k))
        if s and _is_plausible_inspected_unit_name(s):
            return s
    return ""


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
        "受检单位",
        "受检单位名称",
    ):
        v = hi.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if v not in (None, "") and not isinstance(v, (dict, list)):
            s = str(v).strip()
            if s:
                return s
    return _inspected_unit_from_pdf_field_slots(hi, preferred_ids=_PREFERRED_INSPECTED_UNIT_PDF_FIELD_IDS)


def _resolve_inspected_unit_name_from_submit(
    source_data: dict,
    *,
    site_template_parsed: dict | None = None,
) -> str:
    hi = source_data.get("hospitalInfo") if isinstance(source_data.get("hospitalInfo"), dict) else {}
    dd = source_data.get("dynamicData") if isinstance(source_data.get("dynamicData"), dict) else {}
    template_pids = _inspected_unit_pdf_field_ids_from_site_template(site_template_parsed)
    for pid in template_pids:
        for blob in (dd, hi):
            s = _scalar_submit_text_value(blob.get(pid))
            if _is_plausible_inspected_unit_name(s):
                return s
    for blob in (dd, hi):
        for key in ("受检单位", "受检单位名称", "f6", "f7", "f5", "f4", "f2", "f3"):
            v = blob.get(key)
            if isinstance(v, str) and v.strip():
                s = v.strip()
                if _is_plausible_inspected_unit_name(s):
                    return s
            if v not in (None, "") and not isinstance(v, (dict, list)):
                s = str(v).strip()
                if _is_plausible_inspected_unit_name(s):
                    return s
        name = _hospital_info_inspected_unit_name(blob)
        if name and _is_plausible_inspected_unit_name(name):
            return name
    return ""


def _is_plausible_inspected_unit_address(text: str) -> bool:
    s = (text or "").strip()
    if not s or s in _NON_ADDRESS_LITERALS:
        return False
    if re.match(r"^\d{4}-\d{2}-\d{2}", s) or re.search(r"\d{4}-\d{2}-\d{2}T", s):
        return False
    # 含地址常见字样优先判定为地址（避免被下方「像单位名称」启发式误杀）
    if any(ch in s for ch in "路街巷号村镇区市县省园楼室栋层"):
        return True
    if "地址" in s and len(s) >= 4:
        return True
    if len(s) < 4:
        return False
    # 无街道/行政区划线索且整段像单位名称 → 非地址
    if _is_plausible_inspected_unit_name(s) and len(s) <= 40:
        return False
    return len(s) >= 6


def _inspected_address_from_pdf_field_slots(blob: dict, *, preferred_ids: tuple[str, ...]) -> str:
    if not isinstance(blob, dict):
        return ""
    for pid in preferred_ids:
        s = _scalar_submit_text_value(blob.get(pid))
        if s and _is_plausible_inspected_unit_address(s):
            return s
    for k in sorted(blob.keys()):
        if not isinstance(k, str) or not re.fullmatch(r"f\d+", k, flags=re.IGNORECASE):
            continue
        s = _scalar_submit_text_value(blob.get(k))
        if s and _is_plausible_inspected_unit_address(s):
            return s
    return ""


def _looks_like_contact_cell_text(text: str) -> bool:
    s = (text or "").strip()
    if not s or s in _NON_ADDRESS_LITERALS:
        return False
    if re.search(r"\d{7,}", s):
        return True
    if re.search(r"[\d\-]{6,}", s) and re.search(r"[\u4e00-\u9fff]", s):
        return True
    return len(s) <= 36 and bool(re.search(r"\d", s)) and bool(re.search(r"[\u4e00-\u9fff]", s))


def _resolve_inspected_unit_contact_from_submit(source_data: dict) -> tuple[str, str]:
    """受检单位联系人/电话：优先 hospitalInfo.contactPerson + contactPhone。"""
    if not isinstance(source_data, dict):
        return "", ""
    hi = source_data.get("hospitalInfo") if isinstance(source_data.get("hospitalInfo"), dict) else {}
    name = str(hi.get("contactPerson") or "").strip()
    phone = str(hi.get("contactPhone") or "").strip()
    if name or phone:
        return name, phone
    for blob in (
        hi,
        source_data.get("dynamicData") if isinstance(source_data.get("dynamicData"), dict) else {},
    ):
        if not isinstance(blob, dict):
            continue
        for key in ("联系人", "受检单位联系人", "contactPerson"):
            raw = blob.get(key)
            if raw not in (None, "") and not isinstance(raw, (dict, list, bool)):
                cand = str(raw).strip()
                if cand and not _submit_text_looks_like_measurement_number(cand):
                    name = (name or cand).strip()
        for key in ("联系电话", "联系人电话", "contactPhone"):
            raw = blob.get(key)
            if raw not in (None, "") and not isinstance(raw, (dict, list, bool)):
                cand = str(raw).strip()
                if cand and re.search(r"\d", cand):
                    phone = (phone or cand).strip()
    if not phone and name:
        name, phone = split_contact_name_phone(name, phone)
    return name, phone


def _commission_org_mode_is_custom(source_data: dict) -> bool:
    if not isinstance(source_data, dict):
        return False
    hi = source_data.get("hospitalInfo") if isinstance(source_data.get("hospitalInfo"), dict) else {}
    dd = source_data.get("dynamicData") if isinstance(source_data.get("dynamicData"), dict) else {}
    mode = str(hi.get("commissionOrgMode") or dd.get("commissionOrgMode") or "").strip()
    if mode == "customCommission":
        return True
    if mode == "sameInspection":
        return False
    same_checked = _truthy_checkbox_value(hi.get("f94")) or _truthy_checkbox_value(dd.get("f94"))
    custom_checked = _truthy_checkbox_value(hi.get("f95")) or _truthy_checkbox_value(dd.get("f95"))
    if custom_checked and not same_checked:
        return True
    if same_checked and not custom_checked:
        return False
    return custom_checked


def _resolve_commission_organization_from_submit(source_data: dict) -> str:
    """委托单位名称：自定义委托时取名称，否则回退受检单位名称。"""
    if not isinstance(source_data, dict):
        return ""
    hi = source_data.get("hospitalInfo") if isinstance(source_data.get("hospitalInfo"), dict) else {}
    dd = source_data.get("dynamicData") if isinstance(source_data.get("dynamicData"), dict) else {}
    hospital_name = _resolve_inspected_unit_name_from_submit(source_data)
    if not _commission_org_mode_is_custom(source_data):
        return hospital_name.strip() if hospital_name else ""
    for blob in (hi, dd):
        if not isinstance(blob, dict):
            continue
        for key in (
            "f937",
            "commissionOrganization",
            "commissionName",
            "entrustOrganization",
            "commission",
            "委托单位名称",
            "委托单位_委托单位名称",
        ):
            raw = blob.get(key)
            if raw in (None, "") or isinstance(raw, (dict, list, bool)):
                continue
            cand = str(raw).strip()
            if _is_plausible_commission_organization_name(cand):
                return cand
    return hospital_name.strip() if hospital_name else ""


def _resolve_commission_contact_from_submit(source_data: dict) -> tuple[str, str]:
    """从现场记录「委托单位联系人/电话」整格拆分为联系人姓名与联系电话。"""
    if not isinstance(source_data, dict):
        return "", ""
    hi = source_data.get("hospitalInfo") if isinstance(source_data.get("hospitalInfo"), dict) else {}
    dd = source_data.get("dynamicData") if isinstance(source_data.get("dynamicData"), dict) else {}

    def _try_split(text: str) -> tuple[str, str]:
        s = str(text or "").strip()
        if not s or not _looks_like_contact_cell_text(s):
            return "", ""
        return split_contact_name_phone(s, "")

    for blob in (dd, hi):
        for key in ("f4", "委托单位联系人/电话", "委托单位联系人电话"):
            raw = blob.get(key) if isinstance(blob, dict) else None
            if raw in (None, ""):
                continue
            name, phone = _try_split(str(raw).strip())
            if name or phone:
                return name, phone
    for blob in (hi, dd):
        if not isinstance(blob, dict):
            continue
        ccp = str(blob.get("commissionContactPhone") or "").strip()
        if ccp:
            name, phone = _try_split(ccp)
            if name or phone:
                return name, phone
    for dk, dv in (dd or {}).items():
        sk = str(dk or "")
        if "委托单位联系人" in sk and "电话" in sk and dv not in (None, ""):
            name, phone = _try_split(str(dv).strip())
            if name or phone:
                return name, phone
    return "", ""


def _sanitize_inspected_unit_address_mapping(value_mapping: dict) -> None:
    if not isinstance(value_mapping, dict):
        return
    for key in ("受检单位地址", "单位地址", "地址", "inspectionAddress", "hospitalAddress"):
        raw = value_mapping.get(key)
        if not isinstance(raw, str):
            continue
        s = raw.strip()
        if not s or s in _NON_ADDRESS_LITERALS or _is_plausible_inspected_unit_name(s):
            value_mapping.pop(key, None)
            continue
        if not _is_plausible_inspected_unit_address(s):
            value_mapping.pop(key, None)


_REPORT_DECORATIVE_FIELD_IDS = frozenset(
    {"下划线", "下划线2", "下划线3", "年月日", "栏位26", "栏位27", "栏位28"}
)
_REPORT_GENERIC_SLOT_LABEL_RE = re.compile(r"^栏位\d+$")
_REPORT_BASIC_INFO_SLOT_SEMANTICS: dict[str, tuple[str, ...]] = {
    "f1": ("委托编号", "commissionNo"),
    "f20": ("委托编号", "commissionNo"),
    "f2": ("受检单位名称", "受检单位"),
    "f21": ("受检单位名称", "受检单位"),
    "f3": ("受检单位地址", "单位地址", "地址"),
    "f22": ("受检单位地址", "单位地址", "地址"),
    "f4": ("联系人",),
    "f23": ("联系人",),
    "f5": ("联系电话", "联系人电话"),
    "f24": ("联系电话", "联系人电话"),
    "f6": ("主要检测人员", "testman"),
    "f7": ("委托单位名称", "委托单位", "commissionOrganization"),
    "f26": ("委托单位名称", "commissionOrganization"),
}
_DSA_REPORT_BASIC_INFO_SLOT_SEMANTICS: dict[str, tuple[str, ...]] = {
    "f5": ("委托编号", "commissionNo"),
    "f6": ("受检单位名称", "受检单位"),
    "f7": ("受检单位地址", "单位地址", "地址"),
    "f8": ("联系人",),
    "f9": ("联系电话", "联系人电话"),
    "f10": ("主要检测人员", "testman"),
    "f11": ("委托单位名称", "commissionOrganization", "委托单位_委托单位名称"),
}
_DR1_REPORT_BASIC_INFO_SLOT_SEMANTICS: dict[str, tuple[str, ...]] = {
    "f6": ("委托编号", "commissionNo"),
    "f7": ("受检单位名称", "受检单位"),
    "f8": ("受检单位地址", "单位地址", "地址"),
    "f9": ("联系人",),
    "f10": ("联系电话", "联系人电话"),
    "f11": ("主要检测人员", "testman"),
    "f12": ("委托单位名称", "commissionOrganization", "委托单位_委托单位名称"),
}
_REPORT_BASIC_INFO_CONTACT_PIDS = frozenset({"f4", "f5", "f23", "f24"})
_REPORT_BASIC_INFO_ADDRESS_PIDS = frozenset({"f3", "f22"})
_REPORT_BASIC_INFO_RED_FIELD_LABELS = frozenset(
    {
        "委托编号",
        "受检单位名称",
        "受检单位地址",
        "联系人",
        "联系电话",
        "主要检测人员",
        "委托单位",
        "委托单位名称",
    }
)


def _is_report_basic_info_red_mapped_field(field: dict) -> bool:
    """基本情况表取值格：走 HTMLPDF 红字映射，不得被叠印定版框误判为叠印栏位。"""
    if not isinstance(field, dict):
        return False
    fid = str(field.get("id") or "").strip()
    flabel = str(field.get("label") or field.get("title") or "").strip()
    if fid in _REPORT_BASIC_INFO_RED_FIELD_LABELS or flabel in _REPORT_BASIC_INFO_RED_FIELD_LABELS:
        return True
    for k in _field_semantic_candidate_keys(field):
        if (k or "").strip() in _REPORT_BASIC_INFO_RED_FIELD_LABELS:
            return True
    return False


def _pdf_field_id_numeric(pid: str) -> int:
    m = re.fullmatch(r"f(\d+)", str(pid or "").strip(), flags=re.IGNORECASE)
    return int(m.group(1)) if m else 10**9


def _semantic_text_from_value_mapping(value_mapping: dict, keys: tuple[str, ...]) -> str:
    if not isinstance(value_mapping, dict):
        return ""
    for key in keys:
        raw = value_mapping.get(key)
        if raw in (None, "") or isinstance(raw, (dict, list, bool)):
            continue
        s = str(raw).strip()
        if s:
            return s
    return ""


def _basic_info_slot_semantics_for_task(task_obj) -> dict[str, tuple[str, ...]]:
    from apps.core.report_template_profiles import get_report_template_profile

    prof = get_report_template_profile(task_obj)
    if prof.code == "dr-1":
        return dict(_DR1_REPORT_BASIC_INFO_SLOT_SEMANTICS)
    if prof.contact_pdf_field_ids == ("f8", "f9"):
        return dict(_DSA_REPORT_BASIC_INFO_SLOT_SEMANTICS)
    if prof.contact_pdf_field_ids == ("f9", "f10") and prof.code != "dr-1":
        return dict(_DSA_REPORT_BASIC_INFO_SLOT_SEMANTICS)
    base = dict(_REPORT_BASIC_INFO_SLOT_SEMANTICS)
    allowed = tuple(prof.htmlpdf_basic_info_pdf_field_ids or ())
    if allowed:
        allowed_set = frozenset(str(pid).strip().lower() for pid in allowed if str(pid).strip())
        filtered = {
            pid: keys for pid, keys in base.items() if str(pid).strip().lower() in allowed_set
        }
        if "f7" in base and "f7" not in filtered:
            filtered["f7"] = base["f7"]
        return filtered
    return base


def _strip_report_cover_overlay_pdf_ids_from_mapping(value_mapping: dict, task_obj) -> None:
    """封面叠印栏位（四号）勿走 HTMLPDF 小四回填。"""
    if not _is_report_output_task(task_obj) or not isinstance(value_mapping, dict):
        return
    from apps.core.report_template_profiles import get_report_template_profile

    prof = get_report_template_profile(task_obj)
    for pid in prof.cover_overlay_pdf_field_ids or ():
        value_mapping.pop(str(pid), None)
    for key in ("受检单位",):
        if key in value_mapping and prof.cover_overlay_pdf_field_ids:
            value_mapping.pop(key, None)


def _reconcile_report_basic_info_value_mapping(
    value_mapping: dict,
    source_data: dict,
    report_template_fields: list | None,
    *,
    task_obj=None,
) -> None:
    """
    报告基本情况页：现场 dynamicData 的 f 号与报告 pdfFieldId 语义不同（如现场 f7=联系人、报告 f7=委托单位名称）。
    在映射末尾按语义键回写 f 槽，并清空装饰栏位，避免串位与页底乱字。
    """
    if not _is_report_output_task(task_obj) or not isinstance(value_mapping, dict):
        return

    from apps.core.report_template_profiles import get_report_template_profile

    prof = get_report_template_profile(task_obj)
    slot_semantics = _basic_info_slot_semantics_for_task(task_obj)
    contact_pids = tuple(prof.contact_pdf_field_ids or _REPORT_BASIC_INFO_CONTACT_PIDS)

    commission_pids = {
        pid for pid, keys in slot_semantics.items() if "委托单位名称" in keys
    }
    for key in list(value_mapping.keys()):
        if key in commission_pids or key in (
            "委托单位名称",
            "commissionOrganization",
            "委托单位_委托单位名称",
        ):
            raw = value_mapping.get(key)
            if raw in (None, ""):
                continue
            if not _is_plausible_commission_organization_name(str(raw)):
                value_mapping.pop(key, None)

    contact_name, contact_phone = _resolve_inspected_unit_contact_from_submit(source_data)
    if not contact_name and not contact_phone:
        contact_name, contact_phone = _resolve_commission_contact_from_submit(source_data)
    for key in ("委托单位联系人/电话", "委托单位联系人电话", "commissionContactPhone"):
        ccp = _semantic_text_from_value_mapping(value_mapping, (key,))
        if ccp:
            n2, p2 = split_contact_name_phone(ccp, "")
            contact_name = (contact_name or n2).strip()
            contact_phone = (contact_phone or p2).strip()
    if contact_name or contact_phone:
        value_mapping["联系人"] = contact_name
        value_mapping["受检单位联系人"] = contact_name
        value_mapping["联系电话"] = contact_phone
        value_mapping["联系人电话"] = contact_phone

    for pid in contact_pids:
        raw = value_mapping.get(pid)
        if raw in (None, ""):
            continue
        if isinstance(raw, bool) or str(raw).strip().lower() in ("false", "true"):
            value_mapping.pop(pid, None)
        elif _submit_text_looks_like_measurement_number(str(raw).strip()):
            value_mapping.pop(pid, None)

    addr = _resolve_inspected_unit_address_from_submit(source_data)
    if addr:
        value_mapping["受检单位地址"] = addr

    insp_type = _resolve_inspection_type_display(source_data, report_task=task_obj)
    if insp_type:
        value_mapping["检测类型"] = insp_type

    flat: list = []
    if isinstance(report_template_fields, list):
        _walk_template_field_dicts(report_template_fields, flat)
    decorative_pids: set[str] = set()
    for fld in flat:
        fid = str(fld.get("id") or "").strip()
        pid = _field_pdf_id(fld)
        if fid in _REPORT_DECORATIVE_FIELD_IDS and pid:
            decorative_pids.add(pid)
            value_mapping.pop(pid, None)

    site_f7 = value_mapping.get("f7")
    if site_f7 not in (None, "") and _looks_like_contact_cell_text(str(site_f7)):
        value_mapping.pop("f7", None)

    for pid, sem_keys in slot_semantics.items():
        if pid in decorative_pids:
            continue
        if pid in contact_pids:
            continue
        val = _semantic_text_from_value_mapping(value_mapping, sem_keys)
        if not val:
            if pid in _REPORT_BASIC_INFO_ADDRESS_PIDS:
                value_mapping.pop(pid, None)
            continue
        if pid in _REPORT_BASIC_INFO_ADDRESS_PIDS:
            if not _is_plausible_inspected_unit_address(val):
                value_mapping.pop(pid, None)
                continue
        value_mapping[pid] = val

    if contact_name:
        for pid in contact_pids:
            if pid in decorative_pids:
                continue
            sem = " ".join(slot_semantics.get(pid, ()))
            if "联系人" in sem and "电话" not in sem:
                value_mapping[pid] = contact_name
    if contact_phone:
        for pid in contact_pids:
            if pid in decorative_pids:
                continue
            sem = " ".join(slot_semantics.get(pid, ()))
            if "联系电话" in sem or sem.endswith("电话") or "联系人电话" in sem:
                value_mapping[pid] = contact_phone

    corg = _resolve_commission_organization_from_submit(source_data)
    if corg and _is_plausible_commission_organization_name(corg):
        for pid, sem_keys in slot_semantics.items():
            if "委托单位名称" in sem_keys:
                value_mapping[pid] = corg
        value_mapping["委托单位名称"] = corg
        value_mapping["委托单位_委托单位名称"] = corg
        value_mapping["commissionOrganization"] = corg


def _hospital_info_inspected_unit_address(hi: dict) -> str:
    if not isinstance(hi, dict):
        return ""
    for k in (
        "address",
        "inspectionAddress",
        "unitAddress",
        "hospitalAddress",
        "受检单位地址",
    ):
        v = hi.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if v not in (None, "") and not isinstance(v, (dict, list)):
            s = str(v).strip()
            if s:
                return s
    return _inspected_address_from_pdf_field_slots(
        hi, preferred_ids=_PREFERRED_INSPECTED_ADDRESS_PDF_FIELD_IDS
    )


def _resolve_inspected_unit_address_from_submit(source_data: dict) -> str:
    hi = source_data.get("hospitalInfo") if isinstance(source_data.get("hospitalInfo"), dict) else {}
    dd = source_data.get("dynamicData") if isinstance(source_data.get("dynamicData"), dict) else {}
    for blob in (hi, dd):
        addr = _hospital_info_inspected_unit_address(blob)
        if addr:
            return addr
    return ""


def _inspection_type_from_report_task(report_task=None) -> str | None:
    """从报告任务 name/code 推断检测类型（状态/验收报告模板）。"""
    if report_task is None:
        return None
    name = str(getattr(report_task, "name", "") or "").strip()
    code = str(getattr(report_task, "code", "") or "").strip().lower()
    blob = f"{name} {code}"
    if "状态检测" in name or "-status-" in code or code.endswith("-status-report"):
        return "状态检测"
    if "验收检测" in name or "-accept-" in code or code.endswith("-accept-report"):
        return "验收检测"
    if "定期检测" in name or "periodic" in code:
        return "定期检测"
    if "状态" in blob and "验收" not in blob:
        return "状态检测"
    if "验收" in blob:
        return "验收检测"
    return None


def _inspection_type_from_library_task(library_task=None) -> str | None:
    """
    从现场/报告任务模板元数据推断检测类型（验收 vs 状态）。
    用于：验收现场记录表仅用验收判定，状态现场记录表仅用状态判定。
    """
    if library_task is None:
        return None
    hit = _inspection_type_from_report_task(library_task)
    if hit:
        return hit
    from apps.core.models import LibraryTask

    if getattr(library_task, "output_target", None) == LibraryTask.OUTPUT_SITE_RECORD:
        try:
            for rt in library_task.report_target_tasks.all()[:8]:
                hit = _inspection_type_from_report_task(rt)
                if hit:
                    return hit
        except Exception:
            pass
        try:
            from apps.core.library_task_template_binding_service import get_task_template_pair

            _pdf, json_lf = get_task_template_pair(library_task)
            if json_lf is not None:
                rel = str(getattr(json_lf, "relative_path", "") or "")
                if "001-验收检测" in rel:
                    return "验收检测"
                if "002-状态检测" in rel:
                    return "状态检测"
        except Exception:
            pass
    return None


def _test_type_enum_from_inspection_type(inspection_type: str) -> str:
    """前端 hospitalInfo.testType：acceptance | status。"""
    from utils.conditional_field_rules import judgment_criteria_bucket_from_inspection_type

    bucket = judgment_criteria_bucket_from_inspection_type(inspection_type)
    return "status" if bucket == "status" else "acceptance"


def _resolve_inspection_type_display(
    source_data: dict,
    project=None,
    report_task=None,
    site_task=None,
) -> str:
    """从现场记录 radio 槽或 reportInfo 解析检测类型展示文案。"""
    hi = source_data.get("hospitalInfo") if isinstance(source_data.get("hospitalInfo"), dict) else {}
    dd = source_data.get("dynamicData") if isinstance(source_data.get("dynamicData"), dict) else {}
    ri = source_data.get("reportInfo") if isinstance(source_data.get("reportInfo"), dict) else {}

    def _truthy(v) -> bool:
        return _inspection_type_radio_truthy(v)

    tt = str(hi.get("testType") or "").strip().lower()
    if tt in {"acceptance", "验收", "验收检测"}:
        return "验收检测"
    if tt in {"status", "状态", "状态检测"}:
        return "状态检测"

    from_task = _inspection_type_from_report_task(report_task)
    if from_task:
        explicit_type_keys = (
            "f501",
            "f502",
            "f503",
            "f504",
            "f609",
            "f610",
            "f42",
            "f43",
            "检测类型_状态检测",
            "状态检测",
            "检测类型_验收检测",
            "验收检测",
        )
        if not any(_inspection_type_radio_truthy(b.get(k)) for b in (hi, dd) for k in explicit_type_keys):
            return from_task

    blobs = (hi, dd)
    if any(_truthy(b.get("f613")) for b in blobs):
        return "定期检测"
    status_keys = (
        "f501",
        "f502",
        "f609",
        "f42",
        "检测类型_状态检测",
        "状态检测",
    )
    accept_keys = (
        "f504",
        "f610",
        "f43",
        "检测类型_验收检测",
        "验收检测",
    )
    status_hit = any(_truthy(b.get(k)) for b in blobs for k in status_keys)
    accept_hit = any(_truthy(b.get(k)) for b in blobs for k in accept_keys)
    if status_hit and not accept_hit:
        return "状态检测"
    if accept_hit and not status_hit:
        return "验收检测"
    if status_hit and accept_hit:
        from_task = _inspection_type_from_report_task(report_task)
        if from_task:
            return from_task
    for blob in blobs:
        tt = str(blob.get("testType") or blob.get("f504") or "").strip().lower()
        if tt in {"acceptance", "验收", "验收检测"}:
            return "验收检测"
        if tt in {"status", "状态", "状态检测"}:
            return "状态检测"
    for raw in (
        ri.get("inspectionType"),
        ri.get("reportType"),
        hi.get("testType"),
        dd.get("f86"),
        dd.get("检测类型"),
    ):
        t = str(raw or "").strip()
        if not t:
            continue
        tl = t.lower()
        if tl in {"acceptance", "验收", "验收检测"} or t == "验收检测":
            return "验收检测"
        if tl in {"status", "状态", "状态检测"} or t == "状态检测":
            return "状态检测"
        if tl in {"periodic", "定期", "定期检测"} or t == "定期检测":
            return "定期检测"
        if "状态" in t:
            return "状态检测"
        if "验收" in t:
            return "验收检测"
    from_task = _inspection_type_from_report_task(report_task)
    if from_task:
        return from_task
    from_site = _inspection_type_from_library_task(site_task)
    if from_site:
        return from_site
    proj_name = str(getattr(project, "name", "") if project is not None else "").strip()
    return "状态检测" if "状态" in proj_name else "验收检测"


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
    has_radiation_protection: bool = False,
    site_template_parsed: dict | None = None,
):
    from apps.api.inspection_pdf_service import _display_project_id, _display_task_no, display_inspected_no_for_fill

    def _s(v):
        return "" if v is None else str(v)

    report_info = source_data.get("reportInfo") or {}
    hospital_info = source_data.get("hospitalInfo") or {}
    equipment_info = source_data.get("equipmentInfo") or {}
    test_result = source_data.get("testResult") or {}
    hospital_name = _resolve_inspected_unit_name_from_submit(
        source_data,
        site_template_parsed=site_template_parsed,
    )
    _eq_sem = _equipment_semantics_from_pdf_slots(
        equipment_info,
        test_result,
        site_template_parsed=site_template_parsed,
    )
    model = _eq_sem["model"]
    device_name = _eq_sem["deviceName"]
    year = str(report_info.get("year") or "").strip()
    month = str(report_info.get("month") or "").strip()
    day = str(report_info.get("day") or "").strip()
    if not (year and month and day):
        d_asm = _assembled_test_date_from_header_slots(source_data)
        if d_asm is not None:
            year, month, day = f"{d_asm.year:04d}", str(d_asm.month), str(d_asm.day)
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

    is_report = bool(
        task_obj is not None and getattr(task_obj, "output_target", None) == LibraryTask.OUTPUT_REPORT
    )
    from apps.api.inspection_pdf_service import resolve_report_device_count_for_project

    device_count = resolve_report_device_count_for_project(
        project,
        task_no=task_no,
        report_task=task_obj if is_report else None,
        manual_override=manual_device_count,
    )

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

    report_template_name = (task_obj.name or "").strip() if is_report else ""
    project_title = ""
    if is_report and (hospital_name or report_template_name):
        project_title = f"{hospital_name}{report_template_name}"

    n_eval = max(1, int(device_count)) if device_count else 1
    title_for_abbr = report_template_name or device_name or model or ""
    ab = extract_modality_abbr_from_title(title_for_abbr) or (device_name or model or "设备").strip()[:32] or "设备"
    org_for_eval = (hospital_name or "").strip()
    if is_report:
        try:
            from radiation_detection_report.report_pdf_postprocess import report_task_is_qc_performance_overlay

            qc_task = report_task_is_qc_performance_overlay(task_obj)
            qc_with_rp = has_radiation_protection and qc_task
        except Exception:
            qc_task = False
            qc_with_rp = False
        cover_line = build_single_report_cover_title_line(
            ab,
            has_radiation_protection=has_radiation_protection,
            qc_with_radiation_protection=qc_with_rp,
        )
        from utils.pdf_merge import build_single_report_evaluation_body

        insp_type = _resolve_inspection_type_display(source_data, project, report_task=task_obj)
        from radiation_detection_report.report_pdf_postprocess import probe_submit_has_fail_verdict

        has_fail = probe_submit_has_fail_verdict(
            source_data,
            project=project,
            case=inspection_case,
            report_task=task_obj,
            task_no=task_no,
        )
        assessment = build_single_report_evaluation_body(
            org_for_eval,
            ab,
            has_radiation_protection=has_radiation_protection,
            qc_with_radiation_protection=qc_with_rp,
            has_fail=has_fail,
            inspection_type=insp_type,
        )
    else:
        from utils.pdf_merge import format_qc_detection_for_evaluation

        dev_eval = merged_device_phrase_for_evaluation([ab] * n_eval, n_eval)
        insp_type = _resolve_inspection_type_display(source_data, project, report_task=task_obj)
        qc_phrase = format_qc_detection_for_evaluation(insp_type)
        assessment = (
            f"应委托方要求，依据相关检测标准，对{org_for_eval}{dev_eval}进行了{qc_phrase}，结果表明：\n"
            f"所检设备的质量控制相关参数均符合相关标准要求。"
        )

    contact_name, contact_phone = _resolve_inspected_unit_contact_from_submit(source_data)
    if not contact_name and not contact_phone:
        contact_name, contact_phone = _resolve_commission_contact_from_submit(source_data)
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
    addr = _resolve_inspected_unit_address_from_submit(source_data)
    rated = _eq_sem["ratedParams"]
    mfr = _eq_sem["manufacturer"]
    serial = _eq_sem["serialNo"]
    loc = _eq_sem["location"]

    derived: dict = {
        "projectId": project_code,
        "entrustNo": project_code,
        "委托编号": project_code,
        "commissionNo": project_code,
        "taskNo": inspected_display,
        "inspectedNo": inspected_display,
        "受检编号": inspected_display,
        "testnumber": inspected_display,
        "编号1": inspected_display,
        "编号2": inspected_display,
        "编号3": inspected_display,
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
    _ccp_out = ccp_combined or (f"{contact_name}{contact_phone}".strip() if (contact_name or contact_phone) else "")
    if _ccp_out:
        derived["委托单位联系人/电话"] = _ccp_out
        derived["commissionContactPhone"] = _ccp_out
    if not is_report:
        derived["受检设备台数"] = f"{device_count}台"
    derived["设备名称"] = device_name
    derived["设备型号"] = model
    derived["额定参数"] = rated
    derived["生产厂家"] = mfr
    derived["设备编号"] = serial
    derived["设备所在场所"] = loc
    derived["主要检测人员"] = submitter_name
    if not is_report:
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

    if project_title and not is_report:
        derived["项目名称"] = project_title
        derived["projectTitle"] = project_title

    # 委托单位：自定义委托取名称，否则与受检单位名称一致
    _corg = _resolve_commission_organization_from_submit(source_data)
    if _corg and _is_plausible_commission_organization_name(_corg):
        derived["委托单位名称"] = _corg
        derived["委托单位_委托单位名称"] = _corg
        derived["commissionOrganization"] = _corg

    derived["检测类型"] = _resolve_inspection_type_display(source_data, project, report_task=task_obj)

    if is_report:
        now = timezone.localtime()
        ry, rm, rd = str(now.year), str(now.month), str(now.day)
        derived["报告日期"] = f"{ry}年{rm}月{rd}日"
        derived["报告年"] = ry
        derived["报告月"] = rm
        derived["报告日"] = rd
        from utils.pdf_merge import format_gan_jcy_institute_report_no

        report_no = format_gan_jcy_institute_report_no(
            project_code,
            has_radiation_protection=has_radiation_protection,
        )
        derived["报告编号"] = report_no
        derived["report_no_display"] = report_no
        from apps.core.report_template_profiles import get_report_template_profile

        _prof_derived = get_report_template_profile(task_obj)
        if _prof_derived.f1_slot == "report_no":
            derived["f1"] = report_no

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
    source_pdf_path: str | None = None,
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
        source_pdf_path=source_pdf_path,
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
    is_report_output_legacy = (
        task_obj is not None
        and getattr(task_obj, "output_target", None) == LibraryTask.OUTPUT_REPORT
    )
    report_reserved_pdf_field_ids = (
        _collect_template_pdf_field_ids(template_fields)
        if is_report_output_legacy
        else frozenset()
    )
    if task_obj is not None and project is not None:
        merged_site_sem: dict = {}
        for _st, parsed in _iter_parsed_templates_for_backfill(task_no, project, task_obj):
            steps = _parsed_template_steps_list(parsed)
            flat_schema = _flatten_unified_form_steps_to_fields(steps)
            if flat_schema:
                _apply_template_field_sources_to_mapping(
                    value_mapping,
                    source_data,
                    flat_schema,
                    skip_pdf_field_ids=report_reserved_pdf_field_ids or None,
                )
            _inject_step_section_qc_key_aliases(value_mapping, steps)
            site_fields = parsed.get("fields") or []
            if isinstance(site_fields, list):
                _apply_template_field_sources_to_mapping(
                    value_mapping,
                    source_data,
                    site_fields,
                    skip_pdf_field_ids=report_reserved_pdf_field_ids or None,
                )
            _merge_mapping_fill_empty(merged_site_sem, _build_site_template_dynamic_semantic_mapping(parsed, source_data))
        _merge_site_dynamic_semantics_into_value_mapping(
            value_mapping,
            merged_site_sem,
            skip_pdf_field_ids=report_reserved_pdf_field_ids or None,
        )
    # 提交内嵌 steps（若有）再补缺；映射层同时保留语义键与 pdfFieldId（按章节在取值时区分严格/优化）
    _merge_mapping_fill_empty(value_mapping, _build_dynamic_data_semantic_mapping(source_data))
    _merge_dynamic_data_pdf_field_ids_into_value_mapping(
        value_mapping,
        source_data,
        report_pdf_field_ids=report_reserved_pdf_field_ids,
    )
    _inject_test_date_split_pdf_field_aliases(value_mapping, source_data)
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
            fill_font_pt=_htmlpdf_fill_font_pt(task_obj),
        ),
    )
    _inject_instrument_scope_pdf_field_ids(
        value_mapping,
        source_data,
        _merged_site_steps_for_instrument_backfill(task_no, project, task_obj),
        fill_font_pt=_htmlpdf_fill_font_pt(task_obj),
        skip_pdf_field_ids=report_reserved_pdf_field_ids or None,
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
            picked = signatures.get(sig_key) or "" if sig_key else ""
            if not picked:
                picked = _pick_dynamic_image_for_pdf_field(field, source_data)
            field["imageData"] = picked
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


_REPORT_SUMMARY_OVERLAY_MANAGED_KEYS = frozenset(
    {
        "项目名称",
        "评价",
        "受检设备台数",
        "受检工作场所",
        "projectTitle",
        "assessment",
        "deviceCount",
        "医院名称1",
        "设备类型1",
        "医院名称2",
        "设备类型2",
        "设备类型2_2",
    }
)
# 勿按 pdfFieldId 判定签字保留：f 号随模板变化（如 ct-1 报告 f13–f16 为设备型号/额定参数等，非签字栏）。
_REPORT_PRESERVE_ORIGINAL_LABELS = frozenset(
    {
        "编制人",
        "审核人",
        "授权签字人",
        "授权人签字",
        "签发日期",
        "年月日",
        "检测类别",
        "检测项目",
    }
)

# 基本情况表取值列左界（与 cbct-linac 等报告模板实测一致，评价叠印/擦除不得侵入左侧标签列）
_REPORT_BASIC_INFO_VALUE_X0_PT = 176.0


def _field_id_is_generic_report_slot_label(field: dict) -> bool:
    """报告 PDF 上仅有序号占位（栏位N / 下划线N）的格：禁止语义模糊回填。"""
    if not isinstance(field, dict):
        return False
    fid = str(field.get("id") or field.get("placeholder") or "").strip()
    if _REPORT_GENERIC_SLOT_LABEL_RE.fullmatch(fid):
        return True
    if fid.startswith("下划线") and fid not in _REPORT_DECORATIVE_FIELD_IDS:
        return True
    return fid in _REPORT_DECORATIVE_FIELD_IDS


def _collect_template_pdf_field_ids(fields: list | None) -> frozenset[str]:
    """收集模板 fields 树内全部 pdfFieldId（报告任务用于隔离现场 dynamicData f 槽）。"""
    pids: set[str] = set()
    flat: list = []
    if isinstance(fields, list):
        _walk_template_field_dicts(fields, flat)
    for f in flat:
        if not isinstance(f, dict):
            continue
        pid = _field_pdf_id(f)
        if pid:
            pids.add(pid)
    return frozenset(pids)


_SITE_PDF_FIELD_ID_SLOT_RE = re.compile(r"^f\d+$", re.I)


def _collect_report_reserved_pdf_field_ids(
    report_template_fields: list | None,
    report_template_steps: list | None = None,
) -> frozenset[str]:
    """报告 PDF 已占用的 pdfFieldId；现场链不得在无显式映射时直写同 f 槽。"""
    pids: set[str] = set(_collect_template_pdf_field_ids(report_template_fields))
    for fld in _iter_schema_fields_from_steps(report_template_steps):
        if not isinstance(fld, dict):
            continue
        pid = _field_pdf_id(fld)
        if pid:
            pids.add(pid)
    return frozenset(pids)


def _pdf_field_id_is_report_reserved(
    pid: str, reserved_pdf_field_ids: frozenset[str]
) -> bool:
    s = str(pid or "").strip().lower()
    if not s or not reserved_pdf_field_ids:
        return False
    return s in {p.lower() for p in reserved_pdf_field_ids}


def _site_semantic_key_blocked_for_report(
    key: str, reserved_pdf_field_ids: frozenset[str]
) -> bool:
    ks = str(key or "").strip()
    if not ks or not _SITE_PDF_FIELD_ID_SLOT_RE.match(ks):
        return False
    return _pdf_field_id_is_report_reserved(ks, reserved_pdf_field_ids)


def _report_semantic_key_is_preserve_original(ks: str) -> bool:
    """报告 PDF 须保持模板原样的栏位（含签发日期1/2/3 等变体 id）。"""
    s = (ks or "").strip()
    if not s:
        return False
    if s in _REPORT_PRESERVE_ORIGINAL_LABELS:
        return True
    if s.startswith("签发日期"):
        return True
    if "授权" in s and "签字" in s:
        return True
    return False


def _report_semantic_key_is_overlay_managed(ks: str) -> bool:
    """报告 PDF 由终稿叠印写入、HTMLPDF 阶段须留空的栏位。"""
    s = (ks or "").strip()
    if not s:
        return False
    if s in _REPORT_SUMMARY_OVERLAY_MANAGED_KEYS:
        return True
    if "受检设备台数" in s or s in ("设备台数", "受检工作场所"):
        return True
    if s.startswith("项目名称"):
        return True
    if s in ("项目基本情况 名称", "项目基本情况名称"):
        return True
    if s.startswith("评价"):
        return True
    if s in ("医院名称2", "设备类型2", "设备类型2_2"):
        return True
    return False


def _equipment_semantics_from_pdf_slots(
    ei: dict,
    tr: dict | None = None,
    *,
    site_template_parsed: dict | None = None,
) -> dict[str, str]:
    """从 equipmentInfo 解析设备信息；有现场模板时按 hierarchyKey/label 定位 f 槽（CT：f12 名称、f13 型号）。"""
    if not isinstance(ei, dict):
        ei = {}
    if not isinstance(tr, dict):
        tr = {}

    def _pick(*keys: str) -> str:
        for key in keys:
            v = ei.get(key)
            if v not in (None, ""):
                return str(v).strip()
        return ""

    device_name = (
        _pick_ei_value_by_site_field_semantics(ei, site_template_parsed, "设备名称")
        or _pick("deviceName", "name", "f11", "f12")
    )
    model = (
        _pick_ei_value_by_site_field_semantics(ei, site_template_parsed, "设备型号")
        or _pick("model", "deviceModel", "f12", "f13")
    )
    serial = (
        _pick_ei_value_by_site_field_semantics(
            ei,
            site_template_parsed,
            "设备编号(SN/序列号/产品编号)",
            "设备编号",
            "序列号",
            "产品编号",
        )
        or _pick("serialNo", "noDevice", "f15")
    )
    mfr = (
        _pick_ei_value_by_site_field_semantics(ei, site_template_parsed, "生产厂家", "制造商", "生产厂")
        or _pick("manufacturer", "manufacturerProduction", "f16")
    )
    kv_s = (
        _pick_ei_value_by_site_field_semantics(ei, site_template_parsed, "额定参数_kV", "额定kV")
        or _pick("kv", "f11", "f13")
    )
    ma_s = (
        _pick_ei_value_by_site_field_semantics(ei, site_template_parsed, "额定参数_mA", "额定mA")
        or _pick("ma", "f14")
    )
    if not kv_s and tr.get("kv") not in (None, ""):
        kv_s = str(tr.get("kv")).strip()
    if not ma_s and tr.get("ma") not in (None, ""):
        ma_s = str(tr.get("ma")).strip()
    rated = _pick("ratedParams")
    if not rated:
        if kv_s and ma_s:
            rated = f"{kv_s}kV/{ma_s}mA"
        elif kv_s:
            rated = f"{kv_s}kV"
        elif ma_s:
            rated = f"{ma_s}mA"
    return {
        "deviceName": device_name,
        "model": model,
        "serialNo": serial,
        "manufacturer": mfr,
        "ratedParams": rated,
        "location": _pick("location", "f17"),
        "kv": kv_s,
        "ma": ma_s,
    }


def _is_report_preserve_original_pdf_field(field: dict, *, task_obj=None) -> bool:
    """报告 PDF 签字/日期栏保持模板原样，不做映射、回填或叠印（仅 report 任务）。"""
    if not _is_report_output_task(task_obj):
        return False
    ft = (field.get("fieldType") or "").lower()
    for k in _field_semantic_candidate_keys(field):
        ks = (k or "").strip()
        if _report_semantic_key_is_preserve_original(ks):
            return True
    if ft == "signature" and any(
        x in _REPORT_PRESERVE_ORIGINAL_LABELS
        for x in _field_semantic_candidate_keys(field)
    ):
        return True
    return False


def _is_report_output_task(task_obj) -> bool:
    from apps.core.models import LibraryTask

    return (
        task_obj is not None
        and getattr(task_obj, "output_target", None) == LibraryTask.OUTPUT_REPORT
    )


def _htmlpdf_fill_font_pt(task_obj=None) -> float:
    """现场记录五号 10.5pt；报告小四 12pt。"""
    return (
        htmlpdf_service.REPORT_FILL_FONT_PT
        if _is_report_output_task(task_obj)
        else htmlpdf_service.DEFAULT_FONT_PT
    )


def _is_report_cover_overlay_pdf_field(field: dict, *, task_obj=None) -> bool:
    if not _is_report_output_task(task_obj):
        return False
    from apps.core.report_template_profiles import get_report_template_profile

    prof = get_report_template_profile(task_obj)
    pids = prof.cover_overlay_pdf_field_ids or ()
    if not pids:
        return False
    pid = _field_pdf_id(field)
    if pid and pid in pids:
        try:
            page = int(field.get("page") or 0)
        except (TypeError, ValueError):
            page = 0
        if page in (0, 1):
            return True
    return False


def _field_bbox_for_summary_overlay_check(field: dict) -> tuple[int, float, float, float, float] | None:
    """解析栏位 page/x0/y0/x1/y1，供与合并报告定版叠印框比对。"""
    if not isinstance(field, dict):
        return None
    page = field.get("page")
    rect = field.get("rect")
    if page in (None, "") and isinstance(rect, (list, tuple)) and len(rect) >= 5:
        try:
            page = int(rect[0])
        except (TypeError, ValueError):
            page = None
    try:
        page_i = int(page)
    except (TypeError, ValueError):
        return None
    if all(k in field for k in ("x0", "y0", "x1", "y1")):
        try:
            return (
                page_i,
                float(field["x0"]),
                float(field["y0"]),
                float(field["x1"]),
                float(field["y1"]),
            )
        except (TypeError, ValueError):
            pass
    anchor = field.get("pdfAnchor")
    if isinstance(anchor, dict):
        arect = anchor.get("rect")
        if isinstance(arect, (list, tuple)) and len(arect) >= 4:
            try:
                pg = int(anchor.get("page") or page_i)
                return pg, float(arect[0]), float(arect[1]), float(arect[2]), float(arect[3])
            except (TypeError, ValueError):
                pass
    if isinstance(rect, (list, tuple)) and len(rect) >= 5:
        try:
            return (
                int(rect[0]),
                float(rect[1]),
                float(rect[2]),
                float(rect[3]),
                float(rect[4]),
            )
        except (TypeError, ValueError):
            pass
    if all(k in field for k in ("x", "y", "w", "h")):
        try:
            x, y, w, h = float(field["x"]), float(field["y"]), float(field["w"]), float(field["h"])
            if w > 0 and h > 0:
                return page_i, x, y, x + w, y + h
        except (TypeError, ValueError):
            pass
    return None


def _rect_overlap_area(
    a: tuple[int, float, float, float, float],
    b: tuple[int, float, float, float, float],
) -> float:
    if int(a[0]) != int(b[0]):
        return 0.0
    w = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    h = max(0.0, min(a[4], b[4]) - max(a[2], b[2]))
    return w * h


def _field_overlaps_fixed_summary_overlay_rect(field: dict, *, task_obj=None) -> bool:
    """与合并报告定版框（台数/评价/项目名称）重叠的栏位不走 HTMLPDF 红字。"""
    if _is_report_basic_info_red_mapped_field(field):
        return False
    bbox = _field_bbox_for_summary_overlay_check(field)
    if not bbox:
        return False
    from apps.core.report_template_profiles import get_report_template_profile

    prof = get_report_template_profile(task_obj)
    pid = _field_pdf_id(field)
    if pid and pid in (prof.htmlpdf_basic_info_pdf_field_ids or ()):
        return False
    from utils.pdf_merge import _MERGE_OVERLAY_FIXED_RECT_1BASED_XYWH

    field_area = max(1.0, (bbox[3] - bbox[1]) * (bbox[4] - bbox[2]))
    for key in (
        "summary_device_count",
        "summary_evaluation",
        "summary_project_name",
        "summary_inspected_org_value",
    ):
        tup = _MERGE_OVERLAY_FIXED_RECT_1BASED_XYWH.get(key)
        if not tup:
            continue
        pg, rx, ry, rw, rh = tup
        fixed = (int(pg), float(rx), float(ry), float(rx + rw), float(ry + rh))
        overlap = _rect_overlap_area(bbox, fixed)
        if overlap >= min(field_area * 0.12, 18.0):
            return True
    return False


def _is_report_summary_overlay_managed_field(field: dict, *, task_obj=None) -> bool:
    """
    报告 PDF 基本情况页由叠印写入的栏位：回填阶段须留空。
    仅对 output_target=report 生效；现场记录模板即使 f 号相同也不得走此分支。
    """
    if not _is_report_output_task(task_obj):
        return False
    if _is_report_basic_info_red_mapped_field(field):
        return False
    if _is_report_cover_overlay_pdf_field(field, task_obj=task_obj):
        return True
    from apps.core.report_template_profiles import get_report_template_profile

    prof = get_report_template_profile(task_obj)
    pid = _field_pdf_id(field)
    if pid and pid in (prof.cover_overlay_pdf_field_ids or ()):
        return True
    if pid and pid in (prof.summary_overlay_pdf_field_ids or ()):
        return True
    if _field_overlaps_fixed_summary_overlay_rect(field, task_obj=task_obj):
        return True
    for k in _field_semantic_candidate_keys(field):
        ks = (k or "").strip()
        if not ks:
            continue
        if _report_semantic_key_is_overlay_managed(ks):
            return True
    return False


def _strip_report_summary_overlay_keys_from_mapping(value_mapping: dict) -> None:
    for k in list(value_mapping.keys()):
        ks = str(k or "").strip()
        if _report_semantic_key_is_overlay_managed(ks):
            value_mapping.pop(k, None)
            continue
        if _report_semantic_key_is_preserve_original(ks):
            value_mapping.pop(k, None)
            continue


def _strip_summary_overlay_pdf_field_ids_from_mapping(
    value_mapping: dict,
    *,
    report_template_fields: list | None = None,
    report_template_steps: list | None = None,
    task_obj=None,
) -> None:
    """剔除基本情况叠印区 pdfFieldId（如 f49），避免红字与终稿叠印「N台」叠印。"""
    if not _is_report_output_task(task_obj) or not isinstance(value_mapping, dict):
        return
    flat: list = []
    if isinstance(report_template_fields, list):
        _walk_template_field_dicts(report_template_fields, flat)
    if isinstance(report_template_steps, list):
        _walk_template_field_dicts(
            _flatten_unified_form_steps_to_fields(report_template_steps), flat
        )
    from apps.core.report_template_profiles import get_report_template_profile

    prof = get_report_template_profile(task_obj)
    for pid in prof.summary_overlay_pdf_field_ids or ():
        if pid:
            value_mapping.pop(str(pid), None)
    for f in flat:
        if not isinstance(f, dict):
            continue
        if not _is_report_summary_overlay_managed_field(f, task_obj=task_obj):
            continue
        pid = _field_pdf_id(f)
        if pid:
            value_mapping.pop(pid, None)


def _strip_report_preserve_original_keys_from_mapping(value_mapping: dict) -> None:
    """签字/日期/检测项目等栏保持模板 PDF 原样：从语义映射中剔除，避免回填叠字。"""
    if not isinstance(value_mapping, dict):
        return
    for k in list(value_mapping.keys()):
        if _report_semantic_key_is_preserve_original(str(k or "").strip()):
            value_mapping.pop(k, None)


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
    if field.get("_syntheticQcVerdict"):
        return True
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
    fail_tail = "存在参数不符合相关标准。"
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


def _merge_dynamic_data_pdf_field_ids_into_value_mapping(
    value_mapping: dict,
    source_data: dict,
    *,
    overwrite: bool = False,
    report_pdf_field_ids: set[str] | frozenset[str] | None = None,
) -> None:
    """将 submit.dynamicData 按 pdfFieldId（f 号）写入 value_mapping，不扩散到 placeholder 长键。"""
    if not isinstance(value_mapping, dict) or not isinstance(source_data, dict):
        return
    dd = source_data.get("dynamicData")
    if not isinstance(dd, dict):
        return
    dd = _normalize_dynamic_data_f_slots(dd)
    reserved_report_pids = {
        str(pid or "").strip().lower()
        for pid in (report_pdf_field_ids or ())
        if str(pid or "").strip()
    }
    from apps.api.inspection_submit_payload_service import (
        _dynamic_signature_field_ids,
        _is_stored_media_path,
        _looks_like_inline_image,
    )

    sig_field_ids = _dynamic_signature_field_ids(source_data)

    for k, v in dd.items():
        pid = str(k or "").strip()
        if not pid or not re.match(r"^f\d+$", pid, re.I):
            continue
        if v in (None, "") or isinstance(v, (dict, list)):
            continue
        if pid not in sig_field_ids and isinstance(v, str):
            s = v.strip()
            if s and (_is_stored_media_path(s) or _looks_like_inline_image(s)):
                continue
        if reserved_report_pids and pid.lower() in reserved_report_pids:
            continue
        if not overwrite and value_mapping.get(pid) not in (None, ""):
            continue
        value_mapping[pid] = v


def _apply_template_field_sources_to_mapping(
    value_mapping: dict,
    source_data: dict,
    fields: list,
    *,
    overwrite: bool = False,
    skip_report_legacy_qc_submit_path: bool = False,
    pdf_field_id_only: bool | None = None,
    task_obj=None,
    skip_pdf_field_ids: frozenset[str] | None = None,
) -> None:
    """
    根据模板 fields 上的 source.submitPath，把 **真实提交 JSON** 中的值写入 value_mapping。
    pdf_field_id_only=True（默认）时只写 pdfFieldId 键，避免 placeholder 模糊键串到相邻格。
    overwrite=True 时在键上强制采用提交值（用于在派生字段之后仍以 submit JSON 为准）。
    skip_report_legacy_qc_submit_path：报告任务下跳过 testResult.test3 / field30 等 legacy 质控键。
    skip_pdf_field_ids：报告生成时跳过现场模板对报告已占用 f 槽的直写（语义键仍写入）。
    """
    if pdf_field_id_only is None and task_obj is not None and isinstance(fields, list) and fields:
        pdf_field_id_only = None
    elif pdf_field_id_only is None:
        pdf_field_id_only = False
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
        field_strict = (
            pdf_field_id_only
            if pdf_field_id_only is not None
            else _field_use_strict_pdf_field_id_only(f, task_obj)
        )
        pid = _field_pdf_id(f)
        if pid:
            coerced_pid = _coerce_iso_datetime_value_for_date_part_key(pid, val)
            pid_blocked = skip_pdf_field_ids and _pdf_field_id_is_report_reserved(
                pid, skip_pdf_field_ids
            )
            if not pid_blocked and (overwrite or value_mapping.get(pid) in (None, "")):
                value_mapping[pid] = coerced_pid
        if field_strict:
            continue
        for key in _field_semantic_candidate_keys(f):
            if not key:
                continue
            coerced = _coerce_iso_datetime_value_for_date_part_key(key, val)
            if not overwrite and value_mapping.get(key) not in (None, ""):
                continue
            value_mapping[key] = coerced


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


def _backfill_fuzzy_match_enabled() -> bool:
    """
    是否启用回填模糊匹配（占位符关键词、相似度等）。
    已与前端约定仅按 pdfFieldId 精确回填；恒为 False（环境变量不再生效）。
    """
    return False


_STRICT_PDF_FIELD_SECTION_KEYS = frozenset(
    {
        "site_qc_performance",
        "site_layout_diagram",
        "site_radiation_protection",
    }
)

_OPTIMIZED_BACKFILL_SECTION_KEYS = frozenset(
    {
        "site_unit_basic",
        "site_device_basic",
        "site_instruments_staff",
    }
)


def _field_template_section_key(field: dict) -> str:
    if not isinstance(field, dict):
        return ""
    return str(
        field.get("templateSectionKey")
        or field.get("sectionKey")
        or ""
    ).strip()


def _field_use_strict_pdf_field_id_only(field: dict, task_obj=None) -> bool:
    """
    质控检测 / 质量控制（性能）/ 工作场所放射防护检测 / 平面布局章：
    仅 pdfFieldId + submitPath，避免相邻格串位。
    受检单位基本信息、受检设备基本信息、仪器及检测人员章：走优化规则（语义键、日期拆分、仪器列表等）。
    """
    if not isinstance(field, dict):
        return False
    sk = _field_template_section_key(field)
    if sk in _OPTIMIZED_BACKFILL_SECTION_KEYS:
        return False
    if sk in _STRICT_PDF_FIELD_SECTION_KEYS:
        return True
    sec_title = str(field.get("templateSectionTitle") or "").strip()
    if "平面布局" in sec_title:
        return True
    if "工作场所放射防护" in sec_title or (
        "放射防护" in sec_title and "检测" in sec_title and "仪器" not in sec_title
    ):
        return True
    if "质量控制（性能）" in sec_title or sec_title == "质控检测项目":
        return True
    if "质控检测" in sec_title and "仪器" not in sec_title:
        return True
    if str(field.get("sectionType") or "").strip() == "radiationProtection":
        return True
    step_key = str(field.get("templateStepKey") or field.get("templateStepId") or "").strip()
    if step_key == "step_qc_items":
        return True
    if sk.startswith("site_qc_") or sk.startswith("site_radiation_") or sk == "site_layout_diagram":
        return True
    if task_obj is not None and getattr(task_obj, "output_target", None) == LibraryTask.OUTPUT_REPORT:
        if _field_id_is_generic_report_slot_label(field):
            return True
    return False


def _backfill_use_strict_pdf_field_id_only(task_obj=None, field: dict | None = None) -> bool:
    """兼容旧调用：无 field 时视为非严格（映射层）；有 field 时按章节判断。"""
    if isinstance(field, dict):
        return _field_use_strict_pdf_field_id_only(field, task_obj)
    return False


def _format_protection_numeric_pdf_text(field: dict, picked, *, task_obj=None) -> str:
    """现场记录第五章数值格：PDF 展示统一三位小数。"""
    if picked is None or isinstance(picked, bool):
        return "" if picked is None else str(picked)
    text = picked if isinstance(picked, str) else str(picked)
    text = text.strip()
    if not text or text == "/":
        return text
    if task_obj is not None and getattr(task_obj, "output_target", None) != LibraryTask.OUTPUT_SITE_RECORD:
        return text
    try:
        from radiation_detection_report.chapter5_field_sync import (
            PROTECTION_NUMBER_PRECISION,
            field_is_protection_chapter_numeric_cell,
            format_protection_numeric_display,
        )

        if not field_is_protection_chapter_numeric_cell(field):
            return text
        try:
            prec = int(field.get("precision") or PROTECTION_NUMBER_PRECISION)
        except (TypeError, ValueError):
            prec = PROTECTION_NUMBER_PRECISION
        return format_protection_numeric_display(text, precision=prec)
    except ImportError:
        return text


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
        jct = sch.get("judgmentCriteriaByTestType")
        if isinstance(jct, dict) and jct and not isinstance(box.get("judgmentCriteriaByTestType"), dict):
            box["judgmentCriteriaByTestType"] = dict(jct)
        fv = sch.get("fieldVerdict")
        if isinstance(fv, dict) and fv and not isinstance(box.get("fieldVerdict"), dict):
            box["fieldVerdict"] = dict(fv)
        for ek in ("rule", "passLabel", "failLabel", "dependsOn", "formula", "type", "label"):
            ev = sch.get(ek)
            if ev in (None, ""):
                continue
            if isinstance(ev, list) and not ev:
                continue
            if box.get(ek) in (None, ""):
                box[ek] = ev
        for mk in ("templateSectionKey", "templateSectionTitle", "templateStepKey", "sectionType"):
            mv = sch.get(mk)
            if mv not in (None, "") and box.get(mk) in (None, ""):
                box[mk] = mv


def _find_template_field_by_pdf_field_id(template_fields: list | None, pid: str):
    if not pid or not isinstance(template_fields, list):
        return None
    flat: list = []
    _walk_template_field_dicts(template_fields, flat)
    for f in flat:
        if isinstance(f, dict) and _field_pdf_id(f) == pid:
            return f
    return None


def _find_schema_field_by_pdf_field_id(steps: list | None, pid: str) -> dict | None:
    if not pid or not isinstance(steps, list):
        return None
    for fld in _iter_schema_fields_from_steps(steps):
        if isinstance(fld, dict) and _field_pdf_id(fld) == pid:
            return fld
    return None


def _find_site_pdf_field_by_id(site_chain: Sequence[tuple] | None, pid: str) -> dict | None:
    if not pid or not site_chain:
        return None
    for _st, parsed in site_chain:
        if not isinstance(parsed, dict):
            continue
        fields = parsed.get("fields")
        if not isinstance(fields, list):
            continue
        for row in fields:
            if not isinstance(row, dict):
                continue
            if str(row.get("id") or row.get("pdfFieldId") or "").strip() == pid:
                return row
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
        fld = ordered[j]
        lab = str(fld.get("label") or "").strip()
        hkey = str(fld.get("hierarchyKey") or fld.get("id") or "").strip()
        blob = f"{lab} {hkey}"
        if lab in ("检测结果", "计算结果", "报出值"):
            pid = _field_pdf_id(fld)
            if pid:
                return pid
        if "单项判定" in blob or "判定标准" in blob:
            continue
        if any(
            mark in blob
            for mark in ("检测结果", "计算结果", "报出值", "测量均值", "报出值D", "测量读数")
        ):
            pid = _field_pdf_id(fld)
            if pid:
                return pid
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
        ("highContrastResolutionLimit", ("高对比度分辨力", "高对比分辨力"), "≥{v}"),
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
    try:
        from utils.conditional_field_rules import resolve_field_formula_for_eval
    except ImportError:
        resolve_field_formula_for_eval = None  # type: ignore[assignment]

    for _ in range(32):
        changed = 0
        for f in _iter_schema_fields_from_steps(steps):
            fid = str(f.get("id") or f.get("pdfFieldId") or "").strip()
            if not fid:
                continue
            if resolve_field_formula_for_eval is not None:
                formula = resolve_field_formula_for_eval(
                    f,
                    value_mapping,
                    constants=const,
                    enums=enums,
                    lookup_tables=lts,
                )
            else:
                formula = str(f.get("formula") or f.get("fieldExpression") or "").strip()
            if not formula or "row." in formula:
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
            if isinstance(res, str):
                out_val = res
            else:
                try:
                    from radiation_detection_report.chapter5_field_sync import (
                        PROTECTION_NUMBER_PRECISION,
                        field_is_protection_chapter_numeric_cell,
                        format_protection_numeric_display,
                    )

                    if field_is_protection_chapter_numeric_cell(f):
                        try:
                            prec = int(f.get("precision") or PROTECTION_NUMBER_PRECISION)
                        except (TypeError, ValueError):
                            prec = PROTECTION_NUMBER_PRECISION
                        out_val = format_protection_numeric_display(res, precision=prec)
                    else:
                        out_val = str(res)
                except ImportError:
                    out_val = str(res)
            value_mapping[fid] = out_val
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


_INSTRUMENT_SCOPE_QC = "qualityControl"
_INSTRUMENT_SCOPE_RP = "radiationProtection"
_INSTRUMENT_SCOPES = (_INSTRUMENT_SCOPE_QC, _INSTRUMENT_SCOPE_RP)


def dedupe_instrument_payload_items(items: list | None) -> list:
    """
    原样返回 instruments[]（不去重）。
    同一仪器在同一项目内可同时出现在质控、防护 scope，须完整保留。
    """
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _instrument_item_with_scope(item: dict, scope: str) -> dict:
    out = dict(item)
    out["instrumentScope"] = scope
    out["registrySlot"] = 1 if scope == _INSTRUMENT_SCOPE_QC else 2
    return out


def _normalize_task_bound_kinds_export(task_obj) -> dict[str, list]:
    """任务模板 bindingMode=kinds 时解析质控/防护种类列表。"""
    from utils.task_bound_instruments import (
        BINDING_MODE_KINDS,
        binding_mode,
        normalize_task_bound_kinds,
    )

    if task_obj is None:
        return {_INSTRUMENT_SCOPE_QC: [], _INSTRUMENT_SCOPE_RP: []}
    raw = getattr(task_obj, "bound_instrument_ids", None) or []
    if binding_mode(raw) != BINDING_MODE_KINDS:
        return {_INSTRUMENT_SCOPE_QC: [], _INSTRUMENT_SCOPE_RP: []}
    kinds = normalize_task_bound_kinds(raw)
    return {
        _INSTRUMENT_SCOPE_QC: list(kinds.get(_INSTRUMENT_SCOPE_QC) or []),
        _INSTRUMENT_SCOPE_RP: list(kinds.get(_INSTRUMENT_SCOPE_RP) or []),
    }


def instrument_kind_to_payload_dict(name: str, model: str = "", *, scope: str = "") -> dict:
    """种类绑定条目（无台账编号；出库分配前仅记录 name/model）。"""
    row = {
        "id": "",
        "instrumentId": "",
        "identifier": "",
        "name": (name or "").strip(),
        "model": (model or "").strip(),
        "certificateNo": "",
        "validUntil": "",
        "enabled": True,
        "bindingSource": "templateKind",
    }
    if scope in _INSTRUMENT_SCOPES:
        return _instrument_item_with_scope(row, scope)
    return row


def instrument_kinds_bundle_for_export(task_obj) -> dict[str, list[dict]]:
    """根级 instrumentKinds：与任务模板库种类绑定一致。"""
    kinds = _normalize_task_bound_kinds_export(task_obj)
    out: dict[str, list[dict]] = {}
    for scope in _INSTRUMENT_SCOPES:
        rows = [
            {"name": spec.name, "model": spec.model or ""}
            for spec in kinds.get(scope) or []
            if (spec.name or "").strip()
        ]
        if rows:
            out[scope] = rows
    return out


def instruments_full_set_rows_from_task_kinds(task_obj) -> list[dict]:
    """根级 instruments[]：质控种类全套在前、防护全套在后。"""
    kinds = _normalize_task_bound_kinds_export(task_obj)
    out: list[dict] = []
    for scope in _INSTRUMENT_SCOPES:
        for spec in kinds.get(scope) or []:
            if not (spec.name or "").strip():
                continue
            out.append(
                instrument_kind_to_payload_dict(spec.name, spec.model or "", scope=scope)
            )
    return out


def _scoped_primary_from_task_kinds(task_obj) -> dict[str, dict]:
    """两栏 instrument_select 主选：各 scope 取模板绑定的第一种类。"""
    kinds = _normalize_task_bound_kinds_export(task_obj)
    out: dict[str, dict] = {}
    for scope in _INSTRUMENT_SCOPES:
        specs = kinds.get(scope) or []
        if not specs:
            continue
        spec = specs[0]
        if (spec.name or "").strip():
            out[scope] = instrument_kind_to_payload_dict(
                spec.name, spec.model or "", scope=scope
            )
    return out


def _attach_instrument_kinds_metadata(out: dict, task_obj) -> dict:
    kinds_export = instrument_kinds_bundle_for_export(task_obj)
    if kinds_export:
        out["instrumentKinds"] = kinds_export
    else:
        out.pop("instrumentKinds", None)
    return out


def _instrument_catalog_item_by_id(pk: int) -> dict | None:
    inst = InstrumentCatalog.objects.filter(pk=pk, is_active=True).first()
    if inst is None:
        inst = InstrumentCatalog.objects.filter(pk=pk).first()
    if inst is None:
        return None
    return instrument_catalog_to_payload_dict(inst)


def _coerce_instrument_entry(value: Any, *, scope: str) -> dict | None:
    if value is None or value is False:
        return None
    if isinstance(value, dict):
        if _instrument_submit_dict_is_empty(value):
            return None
        return _instrument_item_with_scope(value, scope)
    try:
        pk = int(value)
    except (TypeError, ValueError):
        return None
    if pk <= 0:
        return None
    row = _instrument_catalog_item_by_id(pk)
    return _instrument_item_with_scope(row, scope) if row else None


def _scoped_instruments_from_nested(raw: Any) -> dict[str, dict]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict] = {}
    for scope in _INSTRUMENT_SCOPES:
        entry = _coerce_instrument_entry(raw.get(scope), scope=scope)
        if entry:
            out[scope] = entry
    return out


def _scope_for_instrument_item(item: dict) -> str | None:
    scope = str(item.get("instrumentScope") or "").strip()
    if scope in _INSTRUMENT_SCOPES:
        return scope
    try:
        rs = int(item.get("registrySlot") or 0)
    except (TypeError, ValueError):
        rs = 0
    if rs == 1:
        return _INSTRUMENT_SCOPE_QC
    if rs == 2:
        return _INSTRUMENT_SCOPE_RP
    return None


def _submit_instruments_list_has_scope_tags(items: list | None) -> bool:
    if not isinstance(items, list):
        return False
    for it in items:
        if isinstance(it, dict) and _scope_for_instrument_item(it):
            return True
    return False


def _root_instruments_list_from_submit(source_data: dict | None) -> list:
    """优先根级 instruments[]（用户提交的完整仪器表）。"""
    if not isinstance(source_data, dict):
        return []
    ins = source_data.get("instruments")
    if isinstance(ins, list) and ins:
        return ins
    return []


def _scoped_instrument_lists_from_items(items: list | None) -> dict[str, list[dict]]:
    """按 scope 分组的全套仪器（供 rawPayload.instruments 存档，非主选单行）。"""
    out: dict[str, list[dict]] = {s: [] for s in _INSTRUMENT_SCOPES}
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict) or it.get("enabled") is False:
            continue
        if _instrument_submit_dict_is_empty(it):
            continue
        scope = _scope_for_instrument_item(it)
        if not scope:
            continue
        out[scope].append(dict(it))
    return out


def _estimate_chars_per_line_for_field(
    field: dict | None,
    *,
    fill_font_pt: float | None = None,
) -> int:
    """按 PDF 文本域宽度与回填字号估算每行可容纳汉字数。"""
    fs = fill_font_pt if fill_font_pt is not None else htmlpdf_service.DEFAULT_FONT_PT
    return htmlpdf_service.estimate_pdf_field_chars_per_line(field, font_size=fs)


def _pack_instrument_display_lines(
    lines: list[str],
    *,
    max_chars_per_line: int = 96,
    separator: str = "；",
) -> str:
    """多台仪器尽量挤在同一行，仅当单行放不下时再换行。"""
    cleaned = [str(x or "").strip() for x in lines if str(x or "").strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    rows: list[str] = []
    current = ""
    for line in cleaned:
        if not current:
            current = line
            continue
        candidate = f"{current}{separator}{line}"
        if len(candidate) <= max_chars_per_line:
            current = candidate
        else:
            rows.append(current)
            current = line
    if current:
        rows.append(current)
    return "\n".join(rows)


def _collect_instrument_display_lines_for_scope(
    source_data: dict,
    scope: str,
    *,
    raw_list: list | None = None,
) -> list[str]:
    """从根级 instruments[] 收集某 scope 下各台仪器的展示行。"""
    if scope not in _INSTRUMENT_SCOPES:
        return []
    items = raw_list if isinstance(raw_list, list) else instruments_effective_list(source_data)
    if not isinstance(items, list):
        return []
    lines: list[str] = []
    for it in items:
        if not isinstance(it, dict) or it.get("enabled") is False:
            continue
        if _instrument_submit_dict_is_empty(it):
            continue
        item_scope = _scope_for_instrument_item(it)
        if item_scope != scope:
            continue
        line = format_instrument_display_from_submit_item(it)
        if not (line or "").strip():
            sid0 = str(it.get("id") or it.get("instrumentId") or "").strip()
            if sid0:
                line = (_resolve_instrument_id_display(source_data, sid0) or "").strip()
        if (line or "").strip():
            lines.append(line.strip())
    return lines


def _instrument_packed_text_for_scope(
    source_data: dict,
    scope: str,
    *,
    field: dict | None = None,
    raw_list: list | None = None,
    fill_font_pt: float | None = None,
) -> str:
    """现场记录仪器栏：按 scope 全套拼接，尽量单行多机，必要时换行。"""
    lines = _collect_instrument_display_lines_for_scope(
        source_data, scope, raw_list=raw_list
    )
    if not lines:
        return ""
    return _pack_instrument_display_lines(
        lines,
        max_chars_per_line=_estimate_chars_per_line_for_field(field, fill_font_pt=fill_font_pt),
    )


def _collect_instrument_scope_merged_lines_from_submit_list(
    source_data: dict,
    raw_list: list | None = None,
    *,
    field_by_slot: dict[int, dict] | None = None,
    fill_font_pt: float | None = None,
) -> dict[int, str]:
    """
    从 instruments[] 按 instrumentScope 分组，质控→槽位1、防护→槽位2；
    同 scope 全套仪器紧凑排版（；分隔，必要时 \\n 换行）。
    """
    out: dict[int, str] = {}
    field_by_slot = field_by_slot if isinstance(field_by_slot, dict) else {}
    qc_text = _instrument_packed_text_for_scope(
        source_data,
        _INSTRUMENT_SCOPE_QC,
        field=field_by_slot.get(1),
        raw_list=raw_list,
        fill_font_pt=fill_font_pt,
    )
    if qc_text:
        out[1] = qc_text
    rp_text = _instrument_packed_text_for_scope(
        source_data,
        _INSTRUMENT_SCOPE_RP,
        field=field_by_slot.get(2),
        raw_list=raw_list,
        fill_font_pt=fill_font_pt,
    )
    if rp_text:
        out[2] = rp_text
    return out


def _inject_instrument_scope_pdf_field_ids(
    value_mapping: dict,
    source_data: dict,
    site_steps_merged: list | None,
    *,
    fill_font_pt: float | None = None,
    skip_pdf_field_ids: frozenset[str] | None = None,
) -> None:
    """严格 pdfFieldId 回填：根级 instruments[] 全套按 scope 紧凑写入 instrument_select 对应 f 号。"""
    if not isinstance(value_mapping, dict) or not isinstance(source_data, dict):
        return
    if not site_steps_merged:
        return
    for fld in _flatten_unified_form_steps_to_fields_deep(site_steps_merged):
        if not isinstance(fld, dict):
            continue
        if str(fld.get("type") or "").lower() != "instrument_select":
            continue
        scope = str(fld.get("instrumentScope") or "").strip()
        if scope not in _INSTRUMENT_SCOPES:
            slot = _instrument_slot_index_for_instrument_select_field(str(fld.get("id") or ""), fld)
            if slot == 1:
                scope = _INSTRUMENT_SCOPE_QC
            elif slot == 2:
                scope = _INSTRUMENT_SCOPE_RP
            else:
                continue
        text = _instrument_packed_text_for_scope(
            source_data, scope, field=fld, fill_font_pt=fill_font_pt
        ).strip()
        if not text:
            continue
        pid = _field_pdf_id(fld)
        if not pid or value_mapping.get(pid) not in (None, ""):
            continue
        if skip_pdf_field_ids and _pdf_field_id_is_report_reserved(pid, skip_pdf_field_ids):
            continue
        value_mapping[pid] = text


def _flat_instruments_by_scope(items: list | None) -> dict[str, dict]:
    """从扁平原数组中按 instrumentScope / registrySlot 提取质控、防护主仪器（各取首个）。"""
    if not isinstance(items, list):
        return {}
    out: dict[str, dict] = {}
    for item in dedupe_instrument_payload_items(items):
        if not isinstance(item, dict):
            continue
        scope = str(item.get("instrumentScope") or "").strip()
        if scope not in _INSTRUMENT_SCOPES:
            slot = item.get("registrySlot")
            if slot == 1:
                scope = _INSTRUMENT_SCOPE_QC
            elif slot == 2:
                scope = _INSTRUMENT_SCOPE_RP
            else:
                continue
        if scope in out:
            continue
        out[scope] = _instrument_item_with_scope(item, scope)
    return out


def _project_instrument_binding(
    project_obj, task_obj=None, *, equipment_link_id: int | None = None
) -> dict[str, list[int]]:
    if project_obj is None:
        return {_INSTRUMENT_SCOPE_QC: [], _INSTRUMENT_SCOPE_RP: []}
    from apps.core.instrument_inventory_service import resolved_instrument_ids_for_project

    return resolved_instrument_ids_for_project(
        project_obj,
        task_obj=task_obj,
        equipment_link_id=equipment_link_id,
    )


def _resolve_instrument_binding_dict(
    project_obj=None, task_obj=None, *, equipment_link_id: int | None = None
) -> dict[str, list[int]]:
    """质控/防护各一套仪器 id（项目派工优先，否则任务模板 bound_instrument_ids）。"""
    if project_obj is not None:
        return _project_instrument_binding(
            project_obj, task_obj=task_obj, equipment_link_id=equipment_link_id
        )
    if task_obj is None:
        return {_INSTRUMENT_SCOPE_QC: [], _INSTRUMENT_SCOPE_RP: []}
    from utils.task_bound_instruments import normalize_task_bound_instruments

    return normalize_task_bound_instruments(
        getattr(task_obj, "bound_instrument_ids", None) or []
    )


def instruments_full_set_rows_from_binding(
    project_obj=None, task_obj=None, *, binding: dict[str, list[int]] | None = None
) -> list[dict]:
    """
    根级 instruments[] / PDF 列表：质控全套在前、防护全套在后。
    同一编号若同时绑定质控与防护，各 scope 各保留一行（scope 标签不同）。
    与两栏 instrument_select 主选（instrumentsByScope 各一条）不同。
    """
    binding = binding if binding is not None else _resolve_instrument_binding_dict(
        project_obj, task_obj
    )
    out: list[dict] = []
    for scope in _INSTRUMENT_SCOPES:
        for raw_pk in binding.get(scope) or []:
            try:
                pk = int(raw_pk)
            except (TypeError, ValueError):
                continue
            if pk <= 0:
                continue
            row = _instrument_catalog_item_by_id(pk)
            if row:
                out.append(_instrument_item_with_scope(row, scope))
    return out


def instruments_grouped_by_scope_from_binding(
    project_obj=None, task_obj=None, *, binding: dict[str, list[int]] | None = None
) -> dict[str, list[dict]]:
    """按质控/防护分组的仪器全套（供前端导出与工作台展示）。"""
    binding = binding if binding is not None else _resolve_instrument_binding_dict(
        project_obj, task_obj
    )
    out: dict[str, list[dict]] = {s: [] for s in _INSTRUMENT_SCOPES}
    for scope in _INSTRUMENT_SCOPES:
        for raw_pk in binding.get(scope) or []:
            try:
                pk = int(raw_pk)
            except (TypeError, ValueError):
                continue
            if pk <= 0:
                continue
            row = _instrument_catalog_item_by_id(pk)
            if row:
                out[scope].append(_instrument_item_with_scope(row, scope))
    return out


def _scoped_from_project_binding(binding: dict[str, list[int]]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for scope in _INSTRUMENT_SCOPES:
        ids = list(binding.get(scope) or [])
        if not ids:
            continue
        try:
            pk = int(ids[0])
        except (TypeError, ValueError):
            continue
        row = _instrument_catalog_item_by_id(pk)
        if row:
            out[scope] = _instrument_item_with_scope(row, scope)
    return out


class SubmitInstrumentsBundle:
    """质控/防护两栏与台账落库用的仪器规范化结果。"""

    __slots__ = ("scoped", "array_for_api", "ledger_rows")

    def __init__(
        self,
        scoped: dict[str, dict],
        array_for_api: list[dict],
        ledger_rows: list[dict],
    ):
        self.scoped = scoped
        self.array_for_api = array_for_api
        self.ledger_rows = ledger_rows


def coerce_submit_instruments(
    instruments_raw: Any,
    *,
    raw_payload: dict | None = None,
    project_obj=None,
    task_obj=None,
    equipment_link_id: int | None = None,
) -> SubmitInstrumentsBundle:
    """
    以根级 instruments[] 全套为准；台账 ``ledger_rows`` 按 id 去重。
    优先顺序：根级列表 → raw_payload.instruments 嵌套 → 项目/模板绑定兜底。
    """
    if equipment_link_id is None and project_obj is not None and task_obj is not None:
        eq_info = None
        if isinstance(raw_payload, dict):
            eq_info = raw_payload.get("equipmentInfo")
        if not isinstance(eq_info, dict) and isinstance(instruments_raw, dict):
            eq_info = instruments_raw.get("equipmentInfo")
        if isinstance(eq_info, dict):
            from apps.core.instrument_inventory_service import resolve_equipment_link_id_for_submit

            equipment_link_id = resolve_equipment_link_id_for_submit(
                project_obj, task_obj, eq_info
            )

    binding = _project_instrument_binding(
        project_obj, task_obj, equipment_link_id=equipment_link_id
    )
    scoped: dict[str, dict] = {}

    nested_sources: list[Any] = []
    if isinstance(raw_payload, dict):
        nested_sources.append(raw_payload.get("instruments"))
    if isinstance(instruments_raw, dict):
        nested_sources.append(instruments_raw)

    for src in nested_sources:
        for scope, entry in _scoped_instruments_from_nested(src).items():
            scoped.setdefault(scope, entry)

    if not scoped and isinstance(instruments_raw, list):
        scoped.update(_flat_instruments_by_scope(instruments_raw))

    for scope in _INSTRUMENT_SCOPES:
        if scope in scoped:
            continue
        ids = list(binding.get(scope) or [])
        if not ids:
            continue
        try:
            pk = int(ids[0])
        except (TypeError, ValueError):
            continue
        row = _instrument_catalog_item_by_id(pk)
        if row:
            scoped[scope] = _instrument_item_with_scope(row, scope)

    if task_obj is not None:
        for scope, entry in _scoped_primary_from_task_kinds(task_obj).items():
            scoped.setdefault(scope, entry)

    array_for_api: list[dict] = []
    if isinstance(instruments_raw, list) and instruments_raw:
        for it in instruments_raw:
            if isinstance(it, dict) and not _instrument_submit_dict_is_empty(it):
                array_for_api.append(it)
    if not array_for_api:
        for scope in _INSTRUMENT_SCOPES:
            entry = scoped.get(scope)
            if entry:
                array_for_api.append(entry)

    ledger_rows = dedupe_instrument_payload_items(array_for_api)
    return SubmitInstrumentsBundle(scoped, array_for_api, ledger_rows)


def apply_submit_instruments_bundle(payload: dict | None, bundle: SubmitInstrumentsBundle) -> dict:
    """写入根级 instruments[] 全套；rawPayload.instruments 按 scope 存数组副本。"""
    out = dict(payload or {})
    full_list = out.get("instruments")
    if not isinstance(full_list, list) or not full_list:
        full_list = list(bundle.array_for_api)
    out["instruments"] = list(full_list)
    raw = out.get("rawPayload")
    if not isinstance(raw, dict):
        raw = {}
    else:
        raw = dict(raw)
    raw["instruments"] = _scoped_instrument_lists_from_items(full_list)
    out["rawPayload"] = raw
    return out


def load_task_frontend_schema_steps(task_obj) -> list | None:
    """读取任务主 JSON 模板中的 formSchema.steps（或根级 steps）。"""
    if task_obj is None:
        return None
    try:
        from apps.core import library_pipeline_service as pipeline_service
        from apps.core.library_task_template_binding_service import pick_task_primary_json_template

        lf = pick_task_primary_json_template(task_obj)
        if lf is None:
            return None
        import json as json_std

        p = pipeline_service.library_absolute_path(lf.relative_path)
        blob = json_std.loads(p.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(blob, dict):
            return None
        fs = blob.get("formSchema")
        if isinstance(fs, dict) and isinstance(fs.get("steps"), list):
            return fs["steps"]
        steps = blob.get("steps")
        return steps if isinstance(steps, list) else None
    except Exception:
        return None


def collect_instrument_select_pdf_field_ids(steps: list | None) -> set[str]:
    """前端 schema 中 instrument_select 控件对应的 pdfFieldId 集合。"""
    from utils.pdf_field_formulas import normalize_pdf_field_id

    out: set[str] = set()
    for fld in _iter_schema_fields_from_steps(steps):
        if str(fld.get("type") or "").lower() != "instrument_select":
            continue
        pid = normalize_pdf_field_id(str(fld.get("pdfFieldId") or fld.get("id") or ""))
        if pid:
            out.add(pid)
    return out


def _dynamic_value_is_instrument_pollution(val: Any) -> bool:
    """判断 dynamicData 槽位是否被误写成仪器对象/列表（常见于 App 把根级 instruments 复制到 f 号）。"""
    if isinstance(val, list):
        if len(val) < 2:
            return False
        hits = 0
        for item in val:
            if not isinstance(item, dict):
                continue
            if item.get("instrumentId") is not None or item.get("identifier"):
                hits += 1
            elif item.get("name") and (item.get("id") is not None or item.get("instrumentId") is not None):
                hits += 1
        return hits >= 2
    if isinstance(val, dict):
        if str(val.get("kind") or "").strip():
            return False
        return bool(
            val.get("instrumentId") is not None
            or val.get("instrumentScope")
            or (val.get("name") and val.get("identifier"))
        )
    return False


def sanitize_instrument_pollution_in_mapping(
    mapping: dict | None,
    *,
    allowed_pdf_field_ids: set[str],
) -> dict:
    """移除非 instrument_select 槽位上的仪器列表/对象污染。"""
    from utils.pdf_field_formulas import normalize_pdf_field_id

    if not isinstance(mapping, dict):
        return {}
    out = dict(mapping)
    for key in list(out.keys()):
        k = str(key).strip()
        if not k.lower().startswith("f"):
            continue
        pid = normalize_pdf_field_id(k)
        if pid and pid in allowed_pdf_field_ids:
            continue
        if _dynamic_value_is_instrument_pollution(out.get(key)):
            del out[key]
    return out


def sanitize_submit_payload_instrument_pollution(
    payload: dict | None,
    *,
    template_steps: list | None = None,
) -> dict:
    out = dict(payload or {})
    allowed = collect_instrument_select_pdf_field_ids(template_steps)
    dd = out.get("dynamicData")
    if isinstance(dd, dict):
        out["dynamicData"] = sanitize_instrument_pollution_in_mapping(
            dd, allowed_pdf_field_ids=allowed
        )
    tr = out.get("testResult")
    if isinstance(tr, dict):
        out["testResult"] = sanitize_instrument_pollution_in_mapping(
            tr, allowed_pdf_field_ids=allowed
        )
    return out


def finalize_submit_instruments_in_payload(
    payload: dict | None,
    *,
    project_obj=None,
    task_obj=None,
    template_steps: list | None = None,
) -> tuple[dict, SubmitInstrumentsBundle]:
    """
    提交落库前统一仪器块：
    - instruments[]：现场记录所需全套仪器（质控+防护，按 scope 标记）；
    - rawPayload.instruments：与根级同内容的 scope 分组数组（非主选单行）；
    - 清理 dynamicData/testResult 中非 instrument_select 槽位上的仪器列表误写。
    """
    out = merge_task_template_bound_instruments_into_payload(
        dict(payload or {}), task_obj, project_obj=project_obj
    )
    raw_rp = out.get("rawPayload") if isinstance(out.get("rawPayload"), dict) else None
    eq_info = out.get("equipmentInfo") if isinstance(out.get("equipmentInfo"), dict) else None
    equipment_link_id = None
    if project_obj is not None and task_obj is not None and isinstance(eq_info, dict):
        from apps.core.instrument_inventory_service import resolve_equipment_link_id_for_submit

        equipment_link_id = resolve_equipment_link_id_for_submit(
            project_obj, task_obj, eq_info
        )
    bundle = coerce_submit_instruments(
        out.get("instruments"),
        raw_payload=raw_rp,
        project_obj=project_obj,
        task_obj=task_obj,
        equipment_link_id=equipment_link_id,
    )
    out = apply_submit_instruments_bundle(out, bundle)
    binding = _resolve_instrument_binding_dict(
        project_obj, task_obj, equipment_link_id=equipment_link_id
    )
    full_rows = instruments_full_set_rows_from_binding(
        project_obj, task_obj, binding=binding
    )
    if not full_rows and task_obj is not None:
        full_rows = instruments_full_set_rows_from_task_kinds(task_obj)
    if full_rows:
        out["instruments"] = full_rows
    out = _attach_instrument_kinds_metadata(out, task_obj)
    steps = template_steps
    if steps is None and task_obj is not None:
        steps = load_task_frontend_schema_steps(task_obj)
    out = sanitize_submit_payload_instrument_pollution(out, template_steps=steps)
    final_bundle = SubmitInstrumentsBundle(
        bundle.scoped,
        list(out.get("instruments") or []),
        bundle.ledger_rows,
    )
    return out, final_bundle


def instruments_effective_list(
    source_data: dict | None,
    *,
    project_obj=None,
    task_obj=None,
) -> list:
    """
    PDF/占位符用 instruments[]：优先使用提交根级 instruments（含 instrumentScope 的多台清单）；
    否则再回退项目绑定全套。两栏主选见 rawPayload.instruments / instrumentsByScope。
    """
    if not isinstance(source_data, dict):
        return []
    ins = _root_instruments_list_from_submit(source_data)
    if ins:
        return list(ins)
    full = instruments_full_set_rows_from_binding(project_obj, task_obj)
    if full:
        return full
    if task_obj is not None:
        kind_rows = instruments_full_set_rows_from_task_kinds(task_obj)
        if kind_rows:
            return kind_rows
    raw_payload = source_data.get("rawPayload") if isinstance(source_data.get("rawPayload"), dict) else None
    bundle = coerce_submit_instruments(
        source_data.get("instruments"),
        raw_payload=raw_payload,
        project_obj=project_obj,
        task_obj=task_obj,
    )
    return list(bundle.array_for_api)


def normalize_submit_instruments_for_project(
    items: list | None, project_obj, task_obj=None
) -> list:
    """兼容旧调用：返回质控+防护数组（两栏各一条，不按 id 跨栏去重）。"""
    bundle = coerce_submit_instruments(
        items, project_obj=project_obj, task_obj=task_obj
    )
    return bundle.array_for_api


def merge_task_template_bound_instruments_into_payload(
    payload: dict | None, task_obj, project_obj=None
) -> dict:
    """
    检测仪器优先级：
    1）项目 ``assigned_instrument_ids``（质控/防护各一套，与两栏输入框一致）；
    2）前端已提交：按 scope 合并质控/防护，写入 ``instruments[]`` 与 ``rawPayload.instruments``；
    3）任务模板 ``bindingMode=kinds``：写入 ``instrumentKinds`` 与种类行（无台账编号，不出库）。
    """
    out = dict(payload or {})
    if task_obj is None:
        return out
    raw_payload = out.get("rawPayload") if isinstance(out.get("rawPayload"), dict) else None
    cur = out.get("instruments")
    has_submit = bool(cur) or bool(
        isinstance(raw_payload, dict) and raw_payload.get("instruments")
    )
    binding = _resolve_instrument_binding_dict(project_obj, task_obj)
    if has_submit:
        bundle = coerce_submit_instruments(
            cur,
            raw_payload=raw_payload,
            project_obj=project_obj,
            task_obj=task_obj,
        )
        out = apply_submit_instruments_bundle(out, bundle)
        submit_list = out.get("instruments")
        if isinstance(submit_list, list) and _submit_instruments_list_has_scope_tags(submit_list):
            return _attach_instrument_kinds_metadata(out, task_obj)
        full_rows = instruments_full_set_rows_from_binding(
            project_obj, task_obj, binding=binding
        )
        if not full_rows:
            full_rows = instruments_full_set_rows_from_task_kinds(task_obj)
        if full_rows:
            out["instruments"] = full_rows
        return _attach_instrument_kinds_metadata(out, task_obj)

    scoped = _scoped_from_project_binding(binding)
    full_rows = (
        instruments_full_set_rows_from_binding(project_obj, task_obj, binding=binding)
        if scoped
        else []
    )
    if not scoped:
        scoped = _scoped_primary_from_task_kinds(task_obj)
    if not full_rows:
        full_rows = instruments_full_set_rows_from_task_kinds(task_obj)
    if not scoped and not full_rows:
        out["instruments"] = []
        raw = dict(raw_payload or {})
        raw["instruments"] = {}
        out["rawPayload"] = raw
        return _attach_instrument_kinds_metadata(out, task_obj)
    bundle = SubmitInstrumentsBundle(
        scoped,
        full_rows,
        dedupe_instrument_payload_items(full_rows),
    )
    out = apply_submit_instruments_bundle(out, bundle)
    return _attach_instrument_kinds_metadata(out, task_obj)


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
    source_data: dict,
    site_steps_merged: list | None = None,
    *,
    fill_font_pt: float | None = None,
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
    scope_merged = _collect_instrument_scope_merged_lines_from_submit_list(
        source_data, fill_font_pt=fill_font_pt
    )
    dd = source_data.get("dynamicData")
    if not isinstance(dd, dict):
        dd = {}
    dd = _normalize_dynamic_data_f_slots(dd)
    raw_list = instruments_effective_list(source_data)
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
    out: dict[int, str] = dict(scope_merged) if scope_merged else {}
    scope_locked_slots = {i for i in (1, 2) if (scope_merged.get(i) or "").strip()}
    for i in range(1, max_idx + 1):
        if i in scope_locked_slots:
            continue
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
            if not ln or si in scope_locked_slots:
                continue
            prev = (out.get(si) or "").strip()
            if not prev:
                out[si] = ln
        for si, ln in _collect_instrument_slot_lines_from_steps_instruments_text_bind(
            source_data, site_steps_merged
        ).items():
            if not ln or si in scope_locked_slots:
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

        binding = resolved_instrument_ids_for_project(project_obj, task_obj=task_obj)
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
    前端导出 JSON 根级 ``instruments[]`` + ``instrumentKinds``：
    项目派工编号优先；否则回退任务模板种类绑定（无出库）。
    下拉全库请前端走登记/台账 API；不再写 instrumentsByScope / instrumentSetsByScope / instrumentBindings。
    """
    binding = _resolve_instrument_binding_dict(project_obj, task_obj)
    instruments = instruments_full_set_rows_from_binding(
        project_obj, task_obj, binding=binding
    )
    kind_bundle = instrument_kinds_bundle_for_export(task_obj)
    if not instruments and task_obj is not None:
        instruments = instruments_full_set_rows_from_task_kinds(task_obj)
    if not instruments:
        merged = merge_task_template_bound_instruments_into_payload(
            dict(payload or {}), task_obj, project_obj=project_obj
        )
        instruments = instruments_effective_list(
            merged, project_obj=project_obj, task_obj=task_obj
        )
        if not kind_bundle and isinstance(merged.get("instrumentKinds"), dict):
            kind_bundle = merged["instrumentKinds"]
    result: dict = {"instruments": list(instruments or [])}
    if kind_bundle:
        result["instrumentKinds"] = kind_bundle
    return result


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
    source_data: dict,
    site_steps_merged: list | None = None,
    task_obj=None,
    *,
    fill_font_pt: float | None = None,
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
        raw_ins = instruments_effective_list(source_data)
        if not raw_ins:
            merged = merge_task_template_bound_instruments_into_payload(dict(source_data), task_obj)
            inst2 = instruments_effective_list(merged)
            if inst2:
                sd = dict(source_data)
                sd["instruments"] = inst2
    fp = fill_font_pt if fill_font_pt is not None else _htmlpdf_fill_font_pt(task_obj)
    slot_lines = _collect_instrument_slot_lines_from_submit(
        sd, site_steps_merged, fill_font_pt=fp
    )
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
    lst = instruments_effective_list(source_data)
    if lst:
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


def _maybe_instrument_select_display(
    field: dict,
    source_data: dict,
    picked: str,
    *,
    fill_font_pt: float | None = None,
) -> str:
    """instrument_select：已是全套拼接串则原样；单 id 时解析为展示行。"""
    if str(field.get("type") or "").lower() != "instrument_select":
        return picked
    if not isinstance(picked, str) or not picked.strip():
        return picked
    ps = picked.strip()
    if "；" in ps or "\n" in ps or len(ps) > 40:
        return ps
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    sp = str(src.get("submitPath") or field.get("submitPath") or "").strip()
    if sp in ("instruments.qualityControl", "instruments.radiationProtection"):
        scope = (
            _INSTRUMENT_SCOPE_QC if sp.endswith("qualityControl") else _INSTRUMENT_SCOPE_RP
        )
        packed = _instrument_packed_text_for_scope(
            source_data, scope, field=field, fill_font_pt=fill_font_pt
        ).strip()
        if packed:
            return packed
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
    fs = parsed.get("formSchema")
    if isinstance(fs, dict) and isinstance(fs.get("steps"), list):
        return fs["steps"]
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
    _org = _resolve_commission_organization_from_submit(source_data)
    if _org and not _is_plausible_commission_organization_name(str(_org)):
        _org = ""
    put(
        ["委托单位名称", "委托单位_委托单位名称", "commissionOrganization"],
        _org,
    )
    _eq_sem = _equipment_semantics_from_pdf_slots(ei, tr)
    put(
        ["设备型号", "型号", "设备规格型号", "model"],
        _eq_sem["model"],
    )
    put(["设备编号", "序列号", "SN", "产品编号", "设备序列号"], _eq_sem["serialNo"])
    put(["生产厂家", "制造商", "生产厂", "企业名称", "manufacturer"], _eq_sem["manufacturer"])
    put(["设备所在场所", "所在场所", "场所", "安装地点", "使用场所"], _eq_sem["location"])
    put(["设备名称", "仪器名称"], _eq_sem["deviceName"])
    put(["额定参数", "额定"], _eq_sem["ratedParams"])
    # 报告 PDF 常见域名「额定参数_kV/_mA」；f14/f15 与 schema 中 kv/ma 复用会被判为 ambiguous，dynamicData 直挂被跳过
    rated_kv = _eq_sem["kv"]
    rated_ma = _eq_sem["ma"]
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


def _criterion_prefix_discriminant(prefix: str) -> str:
    """高/低对比等易混质控行：模糊匹配判定标准时要求 discriminant 一致。"""
    s = str(prefix or "")
    for token in ("高对比度分辨力", "低对比度分辨力", "高对比分辨力", "低对比分辨力"):
        if token in s:
            return token
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
            disc_v = _criterion_prefix_discriminant(base)
            if disc_v:
                disc_k = _criterion_prefix_discriminant(prefix)
                if disc_k and disc_v != disc_k:
                    continue
            r = _string_similarity_ratio(bn, pn)
            if r > best_r:
                best_r, best_t = r, str(v).strip()
        thr = 0.74 if bi == 0 else 0.60
        if len(bn) >= 55:
            thr = max(thr, 0.80)
        if best_r >= thr and best_t:
            return best_t
    return ""


def _normalize_pass_fail_verdict_text(val: object) -> str | None:
    """从现场/报告提交值中提取「合格」或「不合格」。"""
    if val is None or isinstance(val, (dict, list, bool)):
        return None
    s = str(val).strip()
    if not s:
        return None
    if "不合格" in s:
        return "不合格"
    if s == "合格" or (s.endswith("合格") and "不合格" not in s):
        return "合格"
    return None


def normalize_template_bind_key(name: str) -> str:
    """现场/报告模板文件名或 templateId 归一化键（去空格、后缀、.json）。"""
    s = (name or "").strip().lower()
    for token in ("-状态终", "-验收终", "-状态", "-验收", "-终"):
        while s.endswith(token):
            s = s[: -len(token)]
    while s.endswith(".json"):
        s = s[:-5]
    return "".join(s.split())


def _resolve_submit_payload_for_site_task(
    site_task,
    *,
    source_data: dict,
    ordered_submit_payloads: Sequence[dict] | None,
    site_task_code: str = "",
) -> dict:
    """多份现场提交合并导出报告时，按现场任务模板匹配对应那份 payload。"""
    if not ordered_submit_payloads:
        return source_data
    payloads = [p for p in ordered_submit_payloads if isinstance(p, dict)]
    multi = len(payloads) > 1
    code_hint = normalize_template_bind_key(
        (site_task_code or "").replace("-", "").replace("_", "")
    )
    task_code = (
        (getattr(site_task, "code", None) or "").strip().lower() if site_task else ""
    )
    task_name = (getattr(site_task, "name", None) or "").strip() if site_task else ""
    code_js = ""
    m_js = re.search(r"js\d+", site_task_code or task_code, re.I)
    if m_js:
        code_js = m_js.group(0).lower()

    def _payload_matches_site_code(pay: dict) -> bool:
        if not code_hint and not task_code and not code_js:
            return False
        ptid = str(pay.get("templateId") or "").strip().lower()
        pt_key = normalize_template_bind_key(ptid)
        pt_compact = pt_key.replace("-", "")
        if task_code and task_code in ptid:
            return True
        if code_js and code_js in ptid:
            return True
        if code_hint and (code_hint in pt_compact or pt_compact in code_hint):
            return True
        return False

    for pay in payloads:
        if _payload_matches_site_code(pay):
            return pay

    if site_task is None:
        return {} if multi else source_data

    from apps.core.library_task_template_binding_service import get_task_template_pair

    _pdf, json_lf = get_task_template_pair(site_task)
    want_name = (json_lf.original_name or "").strip() if json_lf else ""
    want_key = normalize_template_bind_key(want_name)
    for pay in payloads:
        ptid = str(pay.get("templateId") or "").strip()
        if not ptid:
            continue
        pt_key = normalize_template_bind_key(ptid)
        if want_key and (want_key == pt_key or want_key in pt_key or pt_key in want_key):
            return pay
        if task_name and task_name in ptid:
            return pay
    return {} if multi else source_data


_QC_VERDICT_SYNTH_PAGES = frozenset({5, 6, 7})
_QC_VERDICT_COL_X0 = 489.0
_QC_VERDICT_COL_X1 = 515.0
_QC_VERDICT_COL_WIDTH = 26.0
_QC_RESULT_VALUE_SUFFIXES = ("_检测结果", "_报出值", "_计算结果", "_检测值")


def _pdf_field_page_xyxy(field: dict) -> tuple[int, float, float, float, float] | None:
    """从 materialized 栏位（x/y/w/h、x0..y1 或 rect）解析 page 与 xyxy 盒。"""
    if not isinstance(field, dict):
        return None
    try:
        page = int(field.get("page") or 0)
    except (TypeError, ValueError):
        page = 0
    if all(k in field for k in ("x0", "y0", "x1", "y1")):
        try:
            x0, y0, x1, y1 = (
                float(field["x0"]),
                float(field["y0"]),
                float(field["x1"]),
                float(field["y1"]),
            )
            if x1 > x0 and y1 > y0:
                return (page or 1, x0, y0, x1, y1)
        except (TypeError, ValueError):
            pass
    if all(k in field for k in ("x", "y", "w", "h")):
        try:
            x, y, w, h = (
                float(field["x"]),
                float(field["y"]),
                float(field["w"]),
                float(field["h"]),
            )
            if w > 0 and h > 0:
                return (page or 1, x, y, x + w, y + h)
        except (TypeError, ValueError):
            pass
    rect = field.get("rect")
    if isinstance(rect, (list, tuple)) and len(rect) >= 5:
        try:
            p = int(rect[0])
            x, y, w, h = float(rect[1]), float(rect[2]), float(rect[3]), float(rect[4])
            if w > 0 and h > 0:
                return (p, x, y, x + w, y + h)
        except (TypeError, ValueError):
            pass
    return None


def _resolve_library_template_pdf_path(template_file_id: int | None) -> str | None:
    """报告空白 PDF 绝对路径（用于检索「单项判定」列静态合格/不合格字）。"""
    if template_file_id in (None, ""):
        return None
    try:
        pk = int(template_file_id)
    except (TypeError, ValueError):
        return None
    lf = LibraryFile.objects.filter(pk=pk).first()
    if lf is None or not lf.relative_path:
        return None
    try:
        path = pipeline_service.library_absolute_path(lf.relative_path)
    except Exception:
        return None
    return str(path) if path.is_file() else None


def _pdf_page_qc_verdict_layout(page) -> dict:
    """
    扫描 PDF 页质控表「单项判定」列：表头 x 界与各行静态 合格/不合格 字 bbox。
    """
    header_x0 = header_x1 = None
    cells: list[tuple[float, float, float, float]] = []
    d = page.get_text("dict") or {}
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            t = re.sub(r"\s+", "", "".join(str(s.get("text") or "") for s in line.get("spans") or []))
            bb = line.get("bbox")
            if not bb or len(bb) < 4:
                continue
            x0, y0, x1, y1 = float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])
            if "单项判定" in t:
                header_x0 = x0 if header_x0 is None else min(header_x0, x0)
                header_x1 = x1 if header_x1 is None else max(header_x1, x1)
            elif t in ("合格", "不合格"):
                cells.append((x0, y0, x1, y1))
    col_x0 = header_x0
    col_x1 = header_x1
    if cells:
        cx0 = min(c[0] for c in cells)
        cx1 = max(c[2] for c in cells)
        if col_x0 is None:
            col_x0, col_x1 = cx0, cx1
        else:
            col_x0 = min(col_x0, cx0)
            col_x1 = max(col_x1, cx1)
    return {"column": (col_x0, col_x1), "cells": cells}


def _pdf_qc_verdict_cell_rect_for_row(
    layout: dict,
    y0: float,
    y1: float,
    *,
    page: int,
    result_x1: float | None = None,
) -> tuple[float, float, float, float]:
    """在「单项判定」列内定位同行应擦写替换的 合格/不合格 单元格。"""
    cells = layout.get("cells") if isinstance(layout.get("cells"), list) else []
    col = layout.get("column")
    col_x0, col_x1 = col if isinstance(col, (list, tuple)) and len(col) == 2 else (None, None)

    best: tuple[float, float, float, float] | None = None
    best_overlap = 0.0
    mid_y = (float(y0) + float(y1)) * 0.5
    for cell in cells:
        cx0, cy0, cx1, cy1 = cell
        overlap = min(float(y1), cy1) - max(float(y0), cy0)
        if overlap > best_overlap:
            best_overlap = overlap
            best = (cx0, cy0, cx1, cy1)
        elif best is None and cy0 <= mid_y <= cy1:
            best = (cx0, cy0, cx1, cy1)
    if best is not None and best_overlap > 1.0:
        cx0, cy0, cx1, cy1 = best
        if col_x0 is not None and col_x1 is not None and float(col_x1) > float(col_x0):
            return (float(col_x0), cy0, float(col_x1), cy1)
        return best
    if best is not None:
        cx0, cy0, cx1, cy1 = best
        if col_x0 is not None and col_x1 is not None and float(col_x1) > float(col_x0):
            return (float(col_x0), cy0, float(col_x1), cy1)
        return best

    if col_x0 is not None and col_x1 is not None and float(col_x1) > float(col_x0):
        return (float(col_x0), float(y0), float(col_x1), float(y1))

    if result_x1 is not None and float(result_x1) >= 430.0:
        vx0 = float(result_x1) + 1.5
        return (vx0, float(y0), vx0 + _QC_VERDICT_COL_WIDTH, float(y1))
    if page in (5, 6):
        return (_QC_VERDICT_COL_X0, float(y0), _QC_VERDICT_COL_X1, float(y1))
    return (391.0, float(y0), 391.0 + _QC_VERDICT_COL_WIDTH, float(y1))


def _load_pdf_qc_verdict_layouts(source_pdf_path: str | None) -> dict[int, dict]:
    """按 1-based 页码缓存质控表单项判定列布局。"""
    if not source_pdf_path:
        return {}
    try:
        import fitz
    except ImportError:
        return {}
    layouts: dict[int, dict] = {}
    try:
        doc = fitz.open(source_pdf_path)
        for p1 in _QC_VERDICT_SYNTH_PAGES:
            if p1 < 1 or p1 > len(doc):
                continue
            layouts[int(p1)] = _pdf_page_qc_verdict_layout(doc[int(p1) - 1])
        doc.close()
    except Exception:
        return {}
    return layouts


def _synthetic_verdict_col_rect(
    field: dict,
    *,
    page: int,
    result_x1: float | None = None,
) -> tuple[float, float]:
    """
    单项判定列紧挨同行「检测结果」列右侧（各页表格宽度不同，不可统一用 x=489）。
    第 7 页（如 1.3 CBCT 节）结果列约止于 x≈390，静态「合格」在 391–420；写至 489 会漏覆盖。
    """
    rx1 = result_x1
    if rx1 is None:
        layout = _pdf_field_page_xyxy(field)
        if layout is not None:
            rx1 = layout[3]
    if rx1 is None:
        try:
            rx1 = float(field.get("x1") or 0)
        except (TypeError, ValueError):
            rx1 = 0.0
    if rx1 and float(rx1) > 0:
        vx0 = float(rx1) + 1.5
        return vx0, vx0 + _QC_VERDICT_COL_WIDTH
    if page in (5, 6):
        return _QC_VERDICT_COL_X0, _QC_VERDICT_COL_X1
    return 391.0, 391.0 + _QC_VERDICT_COL_WIDTH


def _qc_result_row_base_from_field(field: dict) -> str:
    """从报告「检测结果/报出值」栏位 id/placeholder 提取行前缀，供单项判定配对。"""
    for k in _field_semantic_candidate_keys(field):
        ks = str(k or "").strip()
        if not ks:
            continue
        for suf in _QC_RESULT_VALUE_SUFFIXES:
            if suf in ks:
                base = ks.split(suf, 1)[0].rstrip("_").strip()
                if base:
                    return base
        if ks.endswith("检测结果") and len(ks) > len("检测结果"):
            return ks[: -len("检测结果")].rstrip("_")
    return ""


def _inject_synthetic_qc_verdict_pdf_fields(
    flat_fields: list,
    *,
    qc_pages: frozenset[int] | set[int] | None = None,
    result_col_max_x: float = 440.0,
    source_pdf_path: str | None = None,
) -> int:
    """
    报告 PDF 质控表「单项判定」列常为模板静态「合格」字、未划框；
    按 PDF 文本层检索单项判定列内同行 合格/不合格 位置合成可回填域（直接替换，非在检测结果旁叠字）。
    """
    pages = qc_pages if qc_pages is not None else _QC_VERDICT_SYNTH_PAGES
    verdict_layouts = _load_pdf_qc_verdict_layouts(source_pdf_path)
    existing = {_field_pdf_id(f) for f in flat_fields if isinstance(f, dict)}
    existing_verdict_cell: set[tuple[int, float]] = set()
    added = 0
    for f in flat_fields:
        if not isinstance(f, dict):
            continue
        if f.get("_syntheticQcVerdict"):
            continue
        layout = _pdf_field_page_xyxy(f)
        if layout is None:
            continue
        page, x0, y0, x1, y1 = layout
        if page not in pages:
            continue
        if x0 > result_col_max_x:
            continue
        base = _qc_result_row_base_from_field(f)
        if not base:
            continue
        peer_pid = _field_pdf_id(f)
        syn_pid = f"_synVerdict_{peer_pid}"
        if syn_pid in existing:
            continue
        if y1 <= y0:
            continue
        vk = f"{base}_单项判定"
        page_layout = verdict_layouts.get(int(page), {})
        vx0, vy0, vx1, vy1 = _pdf_qc_verdict_cell_rect_for_row(
            page_layout,
            y0,
            y1,
            page=int(page),
            result_x1=x1,
        )
        cell_key = (int(page), round((float(vy0) + float(vy1)) * 0.5, 1))
        if cell_key in existing_verdict_cell:
            continue
        flat_fields.append(
            {
                "id": vk,
                "placeholder": vk,
                "title": vk,
                "pdfFieldId": syn_pid,
                "page": page,
                "x": vx0,
                "y": vy0,
                "w": max(1.0, vx1 - vx0),
                "h": max(1.0, vy1 - vy0),
                "x0": vx0,
                "y0": vy0,
                "x1": vx1,
                "y1": vy1,
                "fieldType": "text",
                "content": "",
                "_syntheticQcVerdict": True,
                "_peerResultPdfFieldId": peer_pid,
            }
        )
        existing.add(syn_pid)
        existing_verdict_cell.add(cell_key)
        added += 1
    return added


def _site_checkbox_is_checked(val: object) -> bool:
    if val is True:
        return True
    if val is False or val is None:
        return False
    if isinstance(val, (int, float)):
        return int(val) != 0
    s = str(val).strip().lower()
    return s in ("1", "true", "yes", "on", "是", "开", "y")


def _site_field_row_bases_for_yesno_match(site_key: str) -> list[str]:
    sk = str(site_key or "").strip()
    if not sk:
        return []
    for suffix in ("_否", "_是"):
        if sk.endswith(suffix):
            base = sk[: -len(suffix)].rstrip("_")
            return _verdict_row_bases_for_peer_match(f"{base}_单项判定") if base else []
    if sk.endswith("否") and not sk.endswith("_否"):
        base = sk[:-1].rstrip("_")
        return _verdict_row_bases_for_peer_match(f"{base}_单项判定") if base else []
    if sk.endswith("是") and not sk.endswith("_是"):
        base = sk[:-1].rstrip("_")
        return _verdict_row_bases_for_peer_match(f"{base}_单项判定") if base else []
    return []


def _pick_site_yes_no_verdict_for_row_bases(
    row_bases: Sequence[str],
    *,
    site_chain: Sequence[tuple] | None,
    source_data: dict,
    ordered_submit_payloads: Sequence[dict] | None,
) -> str | None:
    """现场记录用「是/否」勾选项表示合格性时，读取同行结论。"""
    if not row_bases or not site_chain:
        return None
    want = [str(b or "").strip() for b in row_bases if str(b or "").strip()]
    if not want:
        return None
    fail_hit = False
    pass_hit = False
    for site_task, parsed in site_chain:
        if not isinstance(parsed, dict):
            continue
        payload = _resolve_submit_payload_for_site_task(
            site_task,
            source_data=source_data,
            ordered_submit_payloads=ordered_submit_payloads,
        )
        dd = payload.get("dynamicData") if isinstance(payload.get("dynamicData"), dict) else {}
        steps = _parsed_template_steps_list(parsed)
        for sf in _flatten_unified_form_steps_to_fields(steps):
            ftype = str(sf.get("type") or "").lower()
            if ftype not in ("boolean", "check"):
                continue
            sk = str(sf.get("label") or sf.get("hierarchyKey") or sf.get("id") or "").strip()
            if not sk:
                continue
            sf_bases = _site_field_row_bases_for_yesno_match(sk)
            if not sf_bases:
                continue
            matched = any(
                wb == sb or (wb and sb and (wb in sb or sb in wb))
                for wb in want
                for sb in sf_bases
            )
            if not matched:
                continue
            raw = None
            sp = str(sf.get("submitPath") or (sf.get("source") or {}).get("submitPath") or "").strip()
            if sp:
                raw = _nested_get_for_submit_with_rated_fallback(payload, sp)
            if raw is None:
                pid = str(sf.get("pdfFieldId") or (sf.get("source") or {}).get("pdfFieldId") or "").strip()
                if pid and pid in dd:
                    raw = dd.get(pid)
            if not _site_checkbox_is_checked(raw):
                continue
            if sk.endswith("_否") or sk.endswith("否"):
                fail_hit = True
            elif sk.endswith("_是") or sk.endswith("是"):
                pass_hit = True
    if fail_hit:
        return "不合格"
    if pass_hit:
        return "合格"
    return None


def _site_verdict_row_keys_for_report_verdict(verdict_key: str) -> list[str]:
    """现场记录「单项判定」列在 value_mapping / dynamicData 中可能的语义键。"""
    seen: set[str] = set()
    out: list[str] = []
    for base in _verdict_row_bases_for_peer_match(verdict_key):
        for suffix in ("_单项判定", "单项判定"):
            cand = f"{base}{suffix}"
            if cand and cand not in seen:
                seen.add(cand)
                out.append(cand)
    return out


def _pick_site_record_row_verdict_label(
    report_verdict_field: dict,
    *,
    value_mapping: dict,
    source_data: dict,
    site_chain: Sequence[tuple] | None = None,
    ordered_submit_payloads: Sequence[dict] | None = None,
    flat_report_fields: Sequence[dict] | None = None,
) -> str | None:
    """
    现场记录同行若已判定（存库单项判定，或测量值相对判定标准不合格），返回合格/不合格。
    仅用于报告回填时优先继承现场结论，尤其保证「现场已不合格 → 报告单项判定不合格」。
    """
    vkey = ""
    for k in _field_candidate_keys(report_verdict_field):
        if "单项判定" in str(k or ""):
            vkey = str(k).strip()
            break
    row_bases = _verdict_row_bases_for_peer_match(vkey) if vkey else []
    if not row_bases:
        peer_pid = str(report_verdict_field.get("_peerResultPdfFieldId") or "").strip()
        if peer_pid and flat_report_fields:
            for pf in flat_report_fields:
                if not isinstance(pf, dict):
                    continue
                if _field_pdf_id(pf) != peer_pid:
                    continue
                base = _qc_result_row_base_from_field(pf)
                if base:
                    row_bases = _verdict_row_bases_for_peer_match(f"{base}_单项判定")
                    vkey = vkey or f"{base}_单项判定"
                break
    if not vkey and row_bases:
        vkey = f"{row_bases[0]}_单项判定"

    for ck in _site_verdict_row_keys_for_report_verdict(vkey) if vkey else []:
        vv = _normalize_pass_fail_verdict_text(value_mapping.get(ck))
        if vv:
            return vv

    if site_chain:
        for site_task, parsed in site_chain:
            if not isinstance(parsed, dict):
                continue
            payload = _resolve_submit_payload_for_site_task(
                site_task,
                source_data=source_data,
                ordered_submit_payloads=ordered_submit_payloads,
            )
            steps = _parsed_template_steps_list(parsed)
            for sf in _flatten_unified_form_steps_to_fields(steps):
                sk = str(sf.get("label") or sf.get("hierarchyKey") or sf.get("id") or "")
                if "单项判定" not in sk:
                    continue
                sf_base = _verdict_base_from_field_key(sk)
                matched = False
                match_bases = row_bases if row_bases else _verdict_row_bases_for_peer_match(vkey)
                for rb in match_bases:
                    if sf_base == rb or (sf_base and rb and (sf_base in rb or rb in sf_base)):
                        matched = True
                        break
                if not matched:
                    continue
                sp = str(sf.get("submitPath") or (sf.get("source") or {}).get("submitPath") or "").strip()
                if sp:
                    vv = _normalize_pass_fail_verdict_text(
                        _nested_get_for_submit_with_rated_fallback(payload, sp)
                    )
                    if vv:
                        return vv
                pid = str(sf.get("pdfFieldId") or (sf.get("source") or {}).get("pdfFieldId") or "").strip()
                dd = payload.get("dynamicData") if isinstance(payload.get("dynamicData"), dict) else {}
                if pid and dd:
                    vv = _normalize_pass_fail_verdict_text(dd.get(pid))
                    if vv:
                        return vv

    if row_bases and site_chain:
        yn = _pick_site_yes_no_verdict_for_row_bases(
            row_bases,
            site_chain=site_chain,
            source_data=source_data,
            ordered_submit_payloads=ordered_submit_payloads,
        )
        if yn:
            return yn

    if not vkey:
        return None

    try:
        from utils.verdict_from_criterion import parse_first_number, verdict_from_measurement
    except ImportError:
        return None

    peer_measured = ""
    peer_pid = str(report_verdict_field.get("_peerResultPdfFieldId") or "").strip()
    if peer_pid and flat_report_fields:
        for pf in flat_report_fields:
            if not isinstance(pf, dict) or _field_pdf_id(pf) != peer_pid:
                continue
            mc = str(pf.get("content") or "").strip()
            if mc and not _normalize_pass_fail_verdict_text(mc):
                peer_measured = mc
            break

    for base in _verdict_row_bases_for_peer_match(vkey):
        measured = peer_measured or _pick_primary_measurement_for_site_row(base, value_mapping)
        if not measured:
            for rk in _verdict_measurement_placeholder_key_candidates(f"{base}_单项判定"):
                mv = value_mapping.get(rk)
                if mv in (None, "") or isinstance(mv, (dict, list)):
                    continue
                ms = str(mv).strip()
                if not ms or ms == rk or _normalize_pass_fail_verdict_text(ms):
                    continue
                if parse_first_number(ms) is not None:
                    measured = ms
                    break
        if not measured:
            continue
        crit = _shield_zone_criterion_text_for_verdict_field(f"{base}_单项判定", value_mapping)
        if not crit:
            crit = _fuzzy_criterion_text_for_verdict_key(f"{base}_单项判定", value_mapping)
        if not crit:
            for suffix in ("_判定标准", "_验收标准", "_限值"):
                cv = value_mapping.get(f"{base}{suffix}")
                if cv not in (None, "") and not isinstance(cv, (dict, list)):
                    crit = str(cv).strip()
                    break
        if not crit:
            continue
        auto = verdict_from_measurement(str(measured), crit)
        if auto in ("合格", "不合格"):
            return auto
    return None


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


def _judgment_criteria_bucket_from_inspection_type(inspection_type: str) -> str:
    from utils.conditional_field_rules import judgment_criteria_bucket_from_inspection_type

    bucket = judgment_criteria_bucket_from_inspection_type(inspection_type)
    return bucket if bucket else "status"


def _judgment_criterion_text_from_field_meta(
    field: dict | None,
    *,
    inspection_type: str = "",
    value_mapping: dict | None = None,
    source_data: dict | None = None,
) -> str:
    """从测量/判定栏位元数据读取判定标准句（含条件判定 judgmentRuleSets）。"""
    if not isinstance(field, dict):
        return ""
    constants: dict = {}
    enums: dict = {}
    lookup_tables: dict = {}
    if isinstance(source_data, dict):
        fs = source_data.get("formSchema") if isinstance(source_data.get("formSchema"), dict) else {}
        if isinstance(source_data.get("constants"), dict):
            constants = source_data["constants"]
        elif isinstance(fs.get("constants"), dict):
            constants = fs["constants"]
        if isinstance(source_data.get("enums"), dict):
            enums = source_data["enums"]
        elif isinstance(fs.get("enums"), dict):
            enums = fs["enums"]
        if isinstance(source_data.get("lookupTables"), dict):
            lookup_tables = source_data["lookupTables"]
        elif isinstance(fs.get("lookupTables"), dict):
            lookup_tables = fs["lookupTables"]
    try:
        from utils.conditional_field_rules import resolve_judgment_criteria_for_eval

        resolved = resolve_judgment_criteria_for_eval(
            field,
            inspection_type=inspection_type,
            value_mapping=value_mapping if isinstance(value_mapping, dict) else {},
            constants=constants,
            enums=enums,
            lookup_tables=lookup_tables,
        )
        if resolved:
            return resolved
    except Exception:
        pass
    for k in ("judgmentCriterionText", "judgmentCriterion", "criterionText", "判定标准"):
        t = str(field.get(k) or "").strip()
        if t:
            return t
    jct = field.get("judgmentCriteriaByTestType")
    if isinstance(jct, dict) and jct:
        bucket = _judgment_criteria_bucket_from_inspection_type(inspection_type)
        if bucket:
            preferred = str(jct.get(bucket) or "").strip()
            if preferred:
                return preferred
    fv = field.get("fieldVerdict")
    if isinstance(fv, dict):
        crit = str(fv.get("criterion") or fv.get("judgmentCriterionText") or "").strip()
        if crit:
            return crit
    return ""


def _inject_site_judgment_criteria_into_mapping(
    value_mapping: dict,
    site_chain: Sequence[tuple],
    *,
    inspection_type: str = "",
    source_data: dict | None = None,
) -> None:
    """将现场模板判定标准写入 {行前缀}_判定标准（仅补缺）；按各现场任务所属验收/状态选用对应桶。"""
    if not isinstance(value_mapping, dict):
        return
    src = source_data if isinstance(source_data, dict) else {}
    for site_task, parsed in site_chain or []:
        if not isinstance(parsed, dict):
            continue
        site_insp = (
            _inspection_type_from_library_task(site_task)
            or inspection_type
            or ""
        )
        if not site_insp:
            continue
        fields = parsed.get("fields")
        if not isinstance(fields, list):
            continue
        for row in fields:
            if not isinstance(row, dict):
                continue
            crit = _judgment_criterion_text_from_field_meta(
                row,
                inspection_type=site_insp,
                value_mapping=value_mapping,
                source_data=src,
            )
            if not crit:
                continue
            pseudo = {
                "id": str(row.get("id") or ""),
                "placeholder": str(row.get("id") or ""),
                "originalPlaceholder": str(row.get("id") or ""),
                "fieldType": "text",
            }
            base = _qc_result_row_base_from_field(pseudo)
            if base:
                ck = f"{base}_判定标准"
                if value_mapping.get(ck) in (None, ""):
                    value_mapping[ck] = crit
            fid = str(row.get("id") or "").strip()
            if fid and "判定标准" not in fid:
                ck2 = f"{fid}_判定标准"
                if value_mapping.get(ck2) in (None, ""):
                    value_mapping[ck2] = crit


def _get_judgment_criterion_text(
    field: dict,
    value_mapping: dict | None = None,
    report_steps: list | None = None,
    source_data: dict | None = None,
    *,
    peer_field: dict | None = None,
    inspection_type: str = "",
) -> str:
    for k in ("judgmentCriterionText", "judgmentCriterion", "criterionText", "判定标准"):
        t = str(field.get(k) or "").strip()
        if t:
            return t
    if isinstance(peer_field, dict):
        crit_peer = _judgment_criterion_text_from_field_meta(
            peer_field,
            inspection_type=inspection_type,
            value_mapping=value_mapping,
            source_data=source_data,
        )
        if crit_peer:
            return crit_peer
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
        if _backfill_fuzzy_match_enabled():
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


def _library_media_url_to_relative(url: str) -> str:
    """将 /media/file_library/... 或库相对路径规范为 FILE_LIBRARY_ROOT 下相对路径。"""
    s = (url or "").strip().replace("\\", "/")
    if not s:
        return ""
    media_prefix = f"{str(settings.MEDIA_URL).rstrip('/')}/file_library/"
    if s.startswith(media_prefix):
        return s[len(media_prefix) :].lstrip("/")
    for prefix in (
        "/media/file_library/",
        "media/file_library/",
        "file_library/",
    ):
        if s.startswith(prefix):
            return s[len(prefix) :].lstrip("/")
    if s.startswith("inspection_submits/") or s.startswith("site_records/"):
        return s
    return ""


def _read_library_image_file_as_pdf_base64(path_or_url: str) -> str:
    """读取落盘后的平面图/照片等资源，返回 build_filled_pdf 可用的 raw base64。"""
    rel = _library_media_url_to_relative(path_or_url)
    if not rel:
        rel = (path_or_url or "").strip().lstrip("/")
    if not rel or rel.startswith("{"):
        return ""
    try:
        from apps.core import pipeline_service

        abs_path = pipeline_service.library_absolute_path(rel)
        if not abs_path.is_file():
            return ""
        return base64.b64encode(abs_path.read_bytes()).decode("ascii")
    except (ValueError, OSError):
        return ""


def _floor_plan_diagram_image_base64(raw: str) -> str:
    """从 floorPlanDiagram JSON 字符串提取 imageBase64（无 data: 前缀）。"""
    import json

    s = (raw or "").strip()
    if not s.startswith("{"):
        return ""
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, TypeError, ValueError):
        return ""
    if not isinstance(obj, dict):
        return ""
    b64 = obj.get("imageBase64")
    if isinstance(b64, str) and b64.strip():
        text = b64.strip()
        if text.startswith("data:image"):
            return text.split(",", 1)[1]
        return text
    return ""


def _coerce_submit_image_value_to_pdf_base64(val: object) -> str:
    """将 dynamicData / submitPath 中的图片值转为 PDF 回填用 base64。"""
    val = _unwrap_dynamic_data_cell_value(val)
    if val in (None, ""):
        return ""
    if not isinstance(val, str):
        return ""
    s = val.strip()
    if not s:
        return ""
    if _is_signature_image_text(s):
        if s.startswith("data:image"):
            return s.split(",", 1)[1]
        return s
    if s.startswith(("{", "[")) and "floorPlanDiagram" in s:
        return _floor_plan_diagram_image_base64(s)
    if s.startswith(("/media", "media/", "file_library", "inspection_submits")):
        return _read_library_image_file_as_pdf_base64(s)
    return ""


def _pick_dynamic_image_for_pdf_field(field: dict, source_data: dict) -> str:
    """
    非签名类 image 域（如平面布局示意图 f689）：从 dynamicData 或 submitPath 取落盘路径/平面图 JSON。
    """
    if not isinstance(field, dict) or not isinstance(source_data, dict):
        return ""
    dd = source_data.get("dynamicData")
    if not isinstance(dd, dict):
        dd = {}
    dd = _normalize_dynamic_data_f_slots(dd)
    pid = _field_pdf_id(field)
    raw_candidates: list[object] = []
    if pid:
        raw_candidates.append(dd.get(pid))
    submit_path = str((field.get("source") or {}).get("submitPath") or "").strip()
    if submit_path:
        raw_candidates.append(
            _nested_get_for_submit_with_rated_fallback(source_data, submit_path)
        )
    sec_key = str(field.get("templateSectionKey") or "").strip()
    if sec_key == "site_layout_diagram" or pid == "f689":
        raw_candidates.append(dd.get("f686"))
    for raw in raw_candidates:
        b64 = _coerce_submit_image_value_to_pdf_base64(raw)
        if b64:
            return b64
    return ""


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


def _merge_site_dynamic_semantics_into_value_mapping(
    value_mapping: dict,
    merged_site_sem: dict,
    *,
    skip_pdf_field_ids: frozenset[str] | None = None,
) -> None:
    """合并「库现场模板 + dynamicData」解释的词条。

    先补缺；再对「当前值等于键名」（常见占位回声）或明显桩值用 dynamicData 侧真值覆盖，
    否则 submitPath 已写入标签串「受检单位」会占住键，导致 f8 有值仍无法进入 PDF。
    skip_pdf_field_ids：报告生成时勿将现场 dynamicData 的 f 槽直并入报告同 f 槽。
    """
    if not isinstance(value_mapping, dict) or not isinstance(merged_site_sem, dict):
        return
    sem = merged_site_sem
    if skip_pdf_field_ids:
        sem = {
            k: v
            for k, v in merged_site_sem.items()
            if not _site_semantic_key_blocked_for_report(k, skip_pdf_field_ids)
        }
    _merge_mapping_fill_empty(value_mapping, sem)
    junk_stubs = frozenset({"-", "—", "－", "/", "无", "暂无", "N/A", "n/a"})
    for k, v in sem.items():
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

    from apps.api.inspection_submit_payload_service import (
        _dynamic_signature_field_ids,
        _pdf_field_to_signature_role_map,
    )

    sig_field_ids = _dynamic_signature_field_ids(source_data)
    pdf_to_role = _pdf_field_to_signature_role_map(source_data)

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
                    src_pdf_id = str(src.get("pdfFieldId") or fld.get("pdfFieldId") or "").strip()
                    if src_pdf_id:
                        out[src_pdf_id] = val
                    src_pdf_ids = src.get("pdfFieldIds")
                    if isinstance(src_pdf_ids, list):
                        for pid in src_pdf_ids:
                            p = str(pid or "").strip()
                            if p:
                                out[p] = val

    # 3) dynamicData：仅模板/steps 解析出的签名 f 槽
    dynamic_data = source_data.get("dynamicData")
    if isinstance(dynamic_data, dict):
        for k, v in dynamic_data.items():
            key = str(k or "").strip()
            if not key or key not in sig_field_ids:
                continue
            if not _is_signature_image_text(v):
                continue
            out[key] = v
            role = pdf_to_role.get(key)
            if role:
                out[role] = v

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
    source_pdf_path: str | None = None,
):
    """
    增强回填逻辑（以 submitted JSON 为主）：
    1) value_mapping 含库现场模板解释的 dynamicData→占位符词条，及 field_to_pdf 等；
    2) 优先按 id/placeholder/title 精确命中，其次按占位符关键词与词条键（含包含、去单位相似度）匹配；
    3) pdfFieldId 仅在上述之后作为兜底，减少多模板合并后 f 编号串位；
    4) 特殊「典型值」检测条件/检测结果仍可在末段做行对齐拼接；单项判定第二遍推算。
    """
    signature_values = _collect_signature_values(source_data)
    from utils.frontend_schema_rule_engine import collect_signature_pdf_bindings

    _sig_template_obj = {
        "fields": template_fields if isinstance(template_fields, list) else [],
        "steps": report_template_steps if isinstance(report_template_steps, list) else [],
    }
    _, _sig_pdf_to_role = collect_signature_pdf_bindings(
        _sig_template_obj, payload=source_data if isinstance(source_data, dict) else None
    )
    _signature_pdf_ids = set(_sig_pdf_to_role.keys())
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
    site_chain_for_verdict: list = []
    if task_obj is not None and project is not None:
        site_chain_for_verdict = list(
            _iter_parsed_templates_for_backfill(task_no, project, task_obj)
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
    if (
        task_obj is not None
        and getattr(task_obj, "output_target", None) == LibraryTask.OUTPUT_REPORT
    ):
        _inject_synthetic_qc_verdict_pdf_fields(
            flat_report_fields,
            source_pdf_path=source_pdf_path,
        )

    try:
        from utils.verdict_from_criterion import verdict_from_measurement
    except ImportError:
        verdict_from_measurement = None

    insp_type_for_verdict = _resolve_inspection_type_display(
        source_data,
        project,
        report_task=task_obj
        if task_obj is not None
        and getattr(task_obj, "output_target", None) == LibraryTask.OUTPUT_REPORT
        else None,
        site_task=task_obj
        if task_obj is not None
        and getattr(task_obj, "output_target", None) == LibraryTask.OUTPUT_SITE_RECORD
        else None,
    )
    report_steps_for_verdict = (
        report_template_steps if isinstance(report_template_steps, list) else None
    )

    def _resolve_peer_field_meta(peer_pid: str) -> dict | None:
        if not peer_pid:
            return None
        meta = _find_schema_field_by_pdf_field_id(report_steps_for_verdict, peer_pid)
        if meta:
            return meta
        return _find_site_pdf_field_by_id(site_chain_for_verdict, peer_pid)

    def _evaluate_verdict_rule_text(
        verdict_field: dict,
        rule: str,
    ) -> str | None:
        rs = str(rule or "").strip()
        if not rs:
            return None
        try:
            from utils.dynamic_form_expression import evaluate_verdict_rule

            vb = evaluate_verdict_rule(
                rs,
                value_mapping,
                constants=template_constants if isinstance(template_constants, dict) else {},
                enums=template_enums if isinstance(template_enums, dict) else {},
                lookup_tables=(
                    template_lookup_tables if isinstance(template_lookup_tables, dict) else {}
                ),
                row=None,
            )
        except Exception:
            return None
        if vb is True:
            return str(verdict_field.get("passLabel") or "合格").strip()
        if vb is False:
            return str(verdict_field.get("failLabel") or "不合格").strip()
        return None

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
        if _is_report_preserve_original_pdf_field(field, task_obj=task_obj):
            return ""
        ft = (field.get("fieldType") or "text").lower()
        deferred_raw_sp = None
        candidates = _field_semantic_candidate_keys(field)
        joined_for_cond = " ".join([k for k in candidates if k]).replace("（", "(").replace("）", ")")
        # 报告质控表：显式 report→site 映射写入 value_mapping[pdfFieldId] 后，须优先于同名语义键模糊匹配。
        if (
            task_obj is not None
            and getattr(task_obj, "output_target", None) == LibraryTask.OUTPUT_REPORT
            and (
                ("检测条件" in joined_for_cond and "判定标准" not in joined_for_cond)
                or ("检测结果" in joined_for_cond and "判定标准" not in joined_for_cond)
            )
            and not _field_semantic_text_has_circled_number(field)
        ):
            pid_mapped = _field_pdf_id(field)
            if pid_mapped:
                mapped_qc = value_mapping.get(pid_mapped)
                if mapped_qc not in (None, "") and not isinstance(mapped_qc, (dict, list)):
                    mapped_s = str(mapped_qc).strip()
                    if mapped_s and not _value_is_field_label_echo(field, mapped_s):
                        return mapped_s
            if "检测条件" in joined_for_cond:
                if _backfill_fuzzy_match_enabled():
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
            if sp_direct in ("instruments.qualityControl", "instruments.radiationProtection"):
                scope = (
                    _INSTRUMENT_SCOPE_QC
                    if sp_direct.endswith("qualityControl")
                    else _INSTRUMENT_SCOPE_RP
                )
                scoped_line = _instrument_packed_text_for_scope(
                    source_data,
                    scope,
                    field=field,
                    fill_font_pt=_htmlpdf_fill_font_pt(task_obj),
                ).strip()
                if scoped_line:
                    if ft == "check":
                        return _coerce_picked_value_for_pdf_checkbox(field, scoped_line)
                    return scoped_line
            raw_sp = _nested_get_for_submit_with_rated_fallback(source_data, sp_direct)
            # submitPath=instruments 绑定整表时，_nested_get 得到 list，原先会跳过并误用同 pdfFieldId 的委托编号等。
            if str(sp_direct) == "instruments" and not _field_is_commission_or_inspection_no_slot(field):
                site_merged = report_template_steps if isinstance(report_template_steps, list) else None
                slot_lines = _collect_instrument_slot_lines_from_submit(
                    source_data,
                    site_merged,
                    fill_font_pt=_htmlpdf_fill_font_pt(task_obj),
                )
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
        use_strict = _field_use_strict_pdf_field_id_only(field, task_obj)
        if use_strict:
            pid_site = _field_pdf_id(field)
            if pid_site:
                got_site = _scalar_fillable(
                    value_mapping.get(pid_site), allow_bool=(ft == "check")
                )
                if got_site is not None and not _value_is_field_label_echo(field, got_site):
                    if not _reject_commission_org_enum_as_textfield_value(field, ft, got_site):
                        if ft == "check" and isinstance(got_site, str):
                            return _coerce_picked_value_for_pdf_checkbox(field, got_site)
                        return got_site
            if deferred_raw_sp is not None and not isinstance(
                deferred_raw_sp, bool
            ) and not _value_is_field_label_echo(field, deferred_raw_sp):
                if ft == "check":
                    return _coerce_picked_value_for_pdf_checkbox(field, deferred_raw_sp)
                return _coerce_scalar_for_field_date_part_slots(field, deferred_raw_sp)
            joined_site = " ".join([k for k in candidates if k]).replace("（", "(").replace("）", ")")
            if (
                ("设备编号" in joined_site and ("SN" in joined_site or "序列号" in joined_site or "产品编号" in joined_site))
                or ("serialno" in joined_site.lower())
            ):
                ei_site = source_data.get("equipmentInfo") if isinstance(source_data.get("equipmentInfo"), dict) else {}
                return (
                    value_mapping.get("serialNo")
                    or ei_site.get("serialNo")
                    or ei_site.get("noDevice")
                    or ""
                )
            return ""
        date_slot = _pick_test_date_table_slot_value(field, source_data, value_mapping)
        if date_slot is not None:
            return date_slot
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
        if "受检设备台数" in joined or "受检工作场所" in joined:
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
        if not _field_semantic_text_has_circled_number(field) and not use_strict:
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
        # 栏位 id/placeholder 即为「CBCT功能_检测结果1」类语义名时，末尾数字是栏位序号而非 PDF 多测量位模板。
        if tmpl and (
            _value_is_field_label_echo(field, tmpl)
            or any(tmpl == k or tmpl.rstrip("：:") == k.rstrip("：:") for k in keys)
        ):
            return picked_s
        # 术者位防护区：PDF 占位符常为整段域 id（含 60/120/141 等多段数字），做数字位替换会把现场值打乱或拟合成“假占位符”。
        if "透视防护区" in joined and "术者位" in joined:
            return picked_s
        if not tmpl or not re.search(r"\d", tmpl):
            return picked_s
        if qc_condition_text_has_unit_markers(picked_s):
            return picked_s
        return merge_number_tokens_from_source(tmpl, picked_s)

    def _auto_verdict_text_if_applicable(field: dict, picked: str) -> str:
        """
        优先：模板 steps 上 type=verdict 的 `rule` 表达式（与前端动态表单一致）求布尔值 → passLabel/failLabel。
        否则：模板字段含判定标准（judgmentCriterionText 等）时，按同行测量值与标准句走 verdict_from_criterion。
        """
        site_row_verdict = None
        if (
            task_obj is not None
            and getattr(task_obj, "output_target", None) == LibraryTask.OUTPUT_REPORT
        ):
            site_row_verdict = _pick_site_record_row_verdict_label(
                field,
                value_mapping=value_mapping,
                source_data=source_data,
                site_chain=site_chain_for_verdict,
                ordered_submit_payloads=ordered_submit_payloads,
                flat_report_fields=flat_report_fields,
            )
            if site_row_verdict == "不合格":
                return str(field.get("failLabel") or "不合格").strip()

        rule_fe = str(field.get("rule") or "").strip()
        rule_hit = _evaluate_verdict_rule_text(field, rule_fe)
        if rule_hit:
            return rule_hit
        vpid = _field_pdf_id(field)
        peer_pid = str(field.get("_peerResultPdfFieldId") or "").strip()
        if not peer_pid:
            peer_pid = _peer_result_pdf_field_id_for_verdict(vpid, report_steps_for_verdict)
        peer_meta = _resolve_peer_field_meta(peer_pid)
        if peer_meta:
            peer_fv = peer_meta.get("fieldVerdict")
            peer_rule = ""
            if isinstance(peer_fv, dict):
                peer_rule = str(peer_fv.get("rule") or "").strip()
            if not peer_rule:
                peer_rule = str(peer_meta.get("rule") or "").strip()
            peer_rule_hit = _evaluate_verdict_rule_text(field, peer_rule)
            if peer_rule_hit:
                return peer_rule_hit
        if site_row_verdict == "合格":
            return str(field.get("passLabel") or "合格").strip()
        if not verdict_from_measurement:
            return picked
        try:
            from utils.verdict_from_criterion import parse_first_number as _v_parse_num
        except ImportError:
            _v_parse_num = None
        crit = _get_judgment_criterion_text(
            field,
            value_mapping,
            report_steps_for_verdict,
            source_data,
            peer_field=peer_meta,
            inspection_type=insp_type_for_verdict,
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
            for rk in peer_keys:
                mv = value_mapping.get(rk)
                if mv in (None, "") or isinstance(mv, (dict, list)):
                    continue
                ms = str(mv).strip()
                if not ms or ms == rk or _normalize_pass_fail_verdict_text(ms):
                    continue
                pseudo_pf = {
                    "id": rk,
                    "placeholder": rk,
                    "originalPlaceholder": rk,
                    "fieldType": "text",
                }
                if not _value_is_field_label_echo(pseudo_pf, mv):
                    measured = ms
                    break
        if not measured and _backfill_fuzzy_match_enabled():
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
        if _backfill_fuzzy_match_enabled():
            for pk in _verdict_measurement_placeholder_key_candidates(vkey):
                if pk and measured_s == str(pk).strip():
                    return picked
        if measured_s in ("合格", "不合格"):
            if measured_s == "不合格":
                return str(field.get("failLabel") or "不合格").strip()
            measured = ""
            measured_s = ""
        if _placeholder_text_looks_like_criterion(measured_s):
            return picked
        crit_is_bool = str(crit or "").strip().lower() in ("true", "false")
        if (
            not crit_is_bool
            and _v_parse_num is not None
            and measured_s
            and _v_parse_num(measured_s) is None
        ):
            if not re.search(r"[≤≥≦≧＜＞<>]", measured_s):
                return picked
        auto = verdict_from_measurement(str(measured), crit) if (measured_s or crit_is_bool) else ""
        if auto == "不合格":
            return str(field.get("failLabel") or "不合格").strip()
        if auto == "合格":
            return str(field.get("passLabel") or "合格").strip()
        sp_ver = str((field.get("source") or {}).get("submitPath") or "").strip()
        if sp_ver:
            fb = _nested_get_for_submit_with_rated_fallback(source_data, sp_ver)
            if fb not in (None, "") and not isinstance(fb, (dict, list)):
                fbs = str(fb).strip()
                if fbs == "不合格" and not _value_is_field_label_echo(field, fb):
                    return fbs
                if fbs == "合格" and not _value_is_field_label_echo(field, fb) and not auto:
                    return fbs
        return picked

    def _pick_signature_for_field(field: dict):
        """签名 image 域：按模板解析的 pdfFieldId 或签字标签命中。"""
        from utils.frontend_schema_rule_engine import _signature_role_from_label

        pid = _field_pdf_id(field)
        ft = (field.get("fieldType") or "").lower()
        label = str(field.get("id") or field.get("title") or field.get("placeholder") or "")
        is_sig_slot = pid in _signature_pdf_ids or (
            ft == "image" and _signature_role_from_label(label) is not None
        )
        if not is_sig_slot:
            return ""
        picked = signature_values.get(pid) if pid else ""
        if picked:
            return picked
        role = _sig_pdf_to_role.get(pid or "")
        if role:
            picked = signature_values.get(role)
            if picked:
                return picked
        dd = source_data.get("dynamicData")
        if isinstance(dd, dict) and pid:
            val = dd.get(pid)
            if _is_signature_image_text(val):
                return val
        return ""

    for field in flat_report_fields:
        if not isinstance(field, dict):
            continue
        if _is_report_preserve_original_pdf_field(field, task_obj=task_obj):
            ft_preserve = (field.get("fieldType") or "text").lower()
            if ft_preserve == "text":
                field["content"] = ""
            elif ft_preserve == "check":
                field["checked"] = False
            elif ft_preserve == "image":
                field["imageData"] = ""
            continue
        field_type = (field.get("fieldType") or "").lower()
        if field_type == "text":
            if _is_report_summary_overlay_managed_field(field, task_obj=task_obj):
                field["content"] = ""
                continue
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
                picked = _format_protection_numeric_pdf_text(field, picked, task_obj=task_obj)
                if (
                    _is_report_output_task(task_obj)
                    and isinstance(picked, str)
                    and picked.strip()
                ):
                    joined_addr = " ".join(_field_semantic_candidate_keys(field))
                    if "受检单位地址" in joined_addr or (
                        "单位地址" in joined_addr and "名称" not in joined_addr
                    ):
                        if not _is_plausible_inspected_unit_address(picked):
                            picked = ""
                picked = _maybe_instrument_select_display(
                    field,
                    source_data,
                    picked,
                    fill_font_pt=_htmlpdf_fill_font_pt(task_obj),
                )
                field["content"] = picked or ""
            continue
        if field_type == "check":
            field["checked"] = _coerce_picked_value_for_pdf_checkbox(
                field, _pick_value_for_field(field)
            )
            continue
        if field_type == "image":
            pid_img = _field_pdf_id(field)
            from utils.frontend_schema_rule_engine import _signature_role_from_label

            label_img = str(field.get("id") or field.get("title") or field.get("placeholder") or "")
            if pid_img in _signature_pdf_ids or (
                _signature_role_from_label(label_img) is not None
            ):
                picked_img = _pick_signature_for_field(field)
            else:
                picked_img = _pick_dynamic_image_for_pdf_field(field, source_data)
            field["imageData"] = picked_img
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
    if (
        task_obj is not None
        and getattr(task_obj, "output_target", None) == LibraryTask.OUTPUT_REPORT
    ):
        for field in flat_report_fields:
            if isinstance(field, dict) and field.get("_syntheticQcVerdict"):
                template_fields.append(field)
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
    from apps.core.library_task_template_binding_service import (
        resolve_task_export_pdf_template_id,
    )

    source_pdf_template_id = resolve_task_export_pdf_template_id(
        task_obj, source_pdf_template_id
    )
    template_bindings = _merged_bindings_for_report_task(
        task_obj, project, task_no, template_bindings if isinstance(template_bindings, dict) else {}
    )
    payload_eff = merge_task_template_bound_instruments_into_payload(payload, task_obj)
    source_pdf_path = _resolve_library_template_pdf_path(source_pdf_template_id)
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
            source_pdf_path=source_pdf_path,
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
    is_report_output = (
        task_obj is not None
        and getattr(task_obj, "output_target", None) == LibraryTask.OUTPUT_REPORT
    )
    report_pdf_field_ids = (
        _collect_report_reserved_pdf_field_ids(
            report_template_fields,
            steps_for_report or None,
        )
        if is_report_output
        else frozenset()
    )
    mid = (map_id or "").strip() or DEFAULT_SUBMIT_PLACEHOLDER_MAP_ID
    value_mapping: dict = dict(build_submit_value_mapping(source_data, mid))
    explicit_site_field_snapshot: dict[str, str] = {}
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
                _apply_template_field_sources_to_mapping(
                    value_mapping,
                    source_data,
                    flat_schema,
                    task_obj=task_obj,
                    skip_pdf_field_ids=report_pdf_field_ids or None,
                )
            _inject_step_section_qc_key_aliases(value_mapping, steps)
            site_fields = parsed.get("fields") or []
            if isinstance(site_fields, list):
                _apply_template_field_sources_to_mapping(
                    value_mapping,
                    source_data,
                    site_fields,
                    task_obj=task_obj,
                    skip_pdf_field_ids=report_pdf_field_ids or None,
                )

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
                _merge_dynamic_data_pdf_field_ids_into_value_mapping(
                    value_mapping,
                    pay,
                    report_pdf_field_ids=report_pdf_field_ids,
                )
        else:
            for _st, parsed in site_chain:
                _merge_mapping_fill_empty(
                    merged_site_sem, _build_site_template_dynamic_semantic_mapping(parsed, source_data)
                )
            _merge_dynamic_data_pdf_field_ids_into_value_mapping(
                value_mapping,
                source_data,
                report_pdf_field_ids=report_pdf_field_ids,
            )
        _merge_site_dynamic_semantics_into_value_mapping(
            value_mapping,
            merged_site_sem,
            skip_pdf_field_ids=report_pdf_field_ids or None,
        )
        insp_type_for_crit = _resolve_inspection_type_display(
            source_data, project, report_task=task_obj
        )
        _inject_site_judgment_criteria_into_mapping(
            value_mapping,
            site_chain,
            inspection_type=insp_type_for_crit,
            source_data=source_data,
        )
    _skip_rl_sp = task_obj is not None and getattr(
        task_obj, "output_target", None
    ) == LibraryTask.OUTPUT_REPORT
    if isinstance(report_template_fields, list):
        _apply_template_field_sources_to_mapping(
            value_mapping,
            source_data,
            report_template_fields,
            skip_report_legacy_qc_submit_path=_skip_rl_sp,
            task_obj=task_obj,
        )
    if steps_for_report:
        flat_rep_steps = _flatten_unified_form_steps_to_fields(steps_for_report)
        if flat_rep_steps:
            _apply_template_field_sources_to_mapping(
                value_mapping,
                source_data,
                flat_rep_steps,
                skip_report_legacy_qc_submit_path=_skip_rl_sp,
                task_obj=task_obj,
            )
        _inject_step_section_qc_key_aliases(value_mapping, steps_for_report)
    _inject_verdict_criteria_from_form_constants(value_mapping, const_for_tpl)
    _inject_default_report_row_verdict_criteria(value_mapping)
    _inject_shield_zone_operator_row_criteria(value_mapping, const_for_tpl)
    _inject_submit_dot_path_aliases(value_mapping, source_data)
    _merge_mapping_fill_empty(value_mapping, _build_dynamic_data_semantic_mapping(source_data))
    _merge_dynamic_data_pdf_field_ids_into_value_mapping(
        value_mapping,
        source_data,
        report_pdf_field_ids=report_pdf_field_ids,
    )
    _inject_test_date_split_pdf_field_aliases(value_mapping, source_data)
    has_radiation_protection = False
    if is_report_output:
        try:
            from radiation_detection_report.report_pdf_integrator import probe_has_radiation_protection_from_submit
            from radiation_detection_report.report_pdf_postprocess import resolve_report_overlay_radiation_protection

            has_radiation_protection = probe_has_radiation_protection_from_submit(
                source_data,
                project=project,
                case=inspection_case,
                report_task=task_obj,
                task_no=task_no,
            )
            has_radiation_protection = resolve_report_overlay_radiation_protection(
                task_obj, has_radiation_protection
            )
        except Exception:
            has_radiation_protection = False
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
            has_radiation_protection=has_radiation_protection,
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
        from apps.core.htmlpdf_report_mapping_service import (
            apply_report_site_field_map_to_value_mapping,
            snapshot_explicit_report_site_field_mapping,
        )

        apply_report_site_field_map_to_value_mapping(
            value_mapping,
            source_data,
            bindings,
            report_parsed_template={
                "fields": report_template_fields if isinstance(report_template_fields, list) else [],
                **(
                    {"formSchema": {"steps": steps_for_report}}
                    if steps_for_report
                    else {}
                ),
            },
            ordered_submit_payloads=ordered_submit_payloads,
        )
        _inject_report_test_date_pdf_field_aliases(
            value_mapping,
            source_data,
            report_template_fields if isinstance(report_template_fields, list) else None,
        )
        explicit_site_field_snapshot = snapshot_explicit_report_site_field_mapping(
            value_mapping, bindings
        )
    else:
        explicit_site_field_snapshot = {}
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
    _inject_instrument_scope_pdf_field_ids(
        value_mapping,
        source_data,
        _merged_site_steps_for_instrument_backfill(
            task_no, project, task_obj, report_steps=steps_for_report or None
        ),
        skip_pdf_field_ids=report_pdf_field_ids or None,
    )
    # 单 PDF 文本格 id「检测仪器」常与委托编号等同槽 f1，submitPath 未挂上 instruments 时只会命中单行；强制与列表别名一致。
    _ins_merged = (inst_aliases.get("检测仪器列表") or inst_aliases.get("检测仪器汇总") or "").strip()
    if _ins_merged:
        value_mapping["检测仪器"] = _ins_merged
    # 与检测仪器同槽 f1 时，仪器合并串可能写入 f1，覆盖委托编号；用提交中的委托编号恢复。
    # 报告模板 f1 为「报告编号」（赣检测院FJ-ZK/FJ-FH），不得写入委托编号。
    ri0 = source_data.get("reportInfo") if isinstance(source_data.get("reportInfo"), dict) else {}
    dd0 = source_data.get("dynamicData") if isinstance(source_data.get("dynamicData"), dict) else {}
    com_fill = str(ri0.get("commissionNo") or ri0.get("f1") or dd0.get("f1") or "").strip()
    if com_fill and "；" not in com_fill:
        value_mapping.setdefault("委托编号", com_fill)
        value_mapping.setdefault("commissionNo", com_fill)
        if not is_report_output:
            vf1 = str(value_mapping.get("f1") or "").strip()
            if not vf1:
                value_mapping["f1"] = com_fill
            elif vf1 != com_fill and ("；" in vf1 or len(vf1) > len(com_fill) + 8):
                value_mapping["f1"] = com_fill
    if is_report_output:
        from utils.pdf_merge import format_gan_jcy_institute_report_no

        com_for_rn = str(
            value_mapping.get("委托编号")
            or value_mapping.get("commissionNo")
            or com_fill
            or ""
        ).strip()
        report_no = format_gan_jcy_institute_report_no(
            com_for_rn,
            has_radiation_protection=has_radiation_protection,
        )
        if report_no:
            value_mapping["报告编号"] = report_no
            value_mapping["report_no_display"] = report_no
            from apps.core.report_template_profiles import get_report_template_profile

            _prof = get_report_template_profile(task_obj)
            if _prof.f1_slot == "report_no":
                value_mapping["f1"] = report_no
            elif _prof.f1_slot == "commission_no" and com_fill:
                value_mapping["f1"] = com_fill
    # KAP 单位：须在 value_mapping 全部合并后再注入，避免后续 _merge 或步骤别名用 f56 串值覆盖布尔/长键。
    _normalize_iso_strings_in_test_date_part_slots(value_mapping)
    if is_report_output:
        _strip_report_summary_overlay_keys_from_mapping(value_mapping)
        _strip_summary_overlay_pdf_field_ids_from_mapping(
            value_mapping,
            report_template_fields=report_template_fields if isinstance(report_template_fields, list) else [],
            report_template_steps=steps_for_report,
            task_obj=task_obj,
        )
        _strip_report_preserve_original_keys_from_mapping(value_mapping)
        _strip_report_cover_overlay_pdf_ids_from_mapping(value_mapping, task_obj)
    _sanitize_inspected_unit_address_mapping(value_mapping)
    if is_report_output:
        _reconcile_report_basic_info_value_mapping(
            value_mapping,
            source_data,
            report_template_fields if isinstance(report_template_fields, list) else [],
            task_obj=task_obj,
        )
        if explicit_site_field_snapshot:
            from apps.core.htmlpdf_report_mapping_service import (
                apply_explicit_site_field_peer_verdicts,
                restore_explicit_report_site_field_mapping,
            )

            restore_explicit_report_site_field_mapping(value_mapping, explicit_site_field_snapshot)
            apply_explicit_site_field_peer_verdicts(
                value_mapping,
                source_data,
                bindings,
                report_parsed_template={
                    "fields": report_template_fields if isinstance(report_template_fields, list) else [],
                    **(
                        {"formSchema": {"steps": steps_for_report}}
                        if steps_for_report
                        else {}
                    ),
                },
                ordered_submit_payloads=ordered_submit_payloads,
            )
    return value_mapping


def _resolve_report_task_for_case(task_no: str, project):
    """解析当前案件应使用的报告任务（含现场记录 taskNo → 父报告）。"""
    from apps.api.inspection_pdf_service import resolve_report_task_for_case

    return resolve_report_task_for_case(task_no, project)


def _normalize_fields_for_htmlpdf(fields, *, task_obj=None):
    """扁平化嵌套 fields，并按页码、纵坐标、横坐标排序，减少叠字顺序导致的乱序。"""
    normalized = []
    skipped = 0
    report_output = _is_report_output_task(task_obj)

    def _append_one(f: dict) -> None:
        nonlocal skipped
        if not isinstance(f, dict):
            skipped += 1
            return
        if report_output and str(f.get("id") or "").strip() in _REPORT_DECORATIVE_FIELD_IDS:
            skipped += 1
            return
        if report_output and (
            _is_report_summary_overlay_managed_field(f, task_obj=task_obj)
            or _is_report_preserve_original_pdf_field(f, task_obj=task_obj)
        ):
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
            "fieldType": (f.get("fieldType") or f.get("type") or "text"),
            "value": text_val or "",
            "checked": bool(f.get("checked", False)),
            "imageData": f.get("imageData") or "",
        }
        # build_filled_pdf 依赖 id/placeholder 等做「检测仪器」两端对齐；勿在导出前剥掉语义键。
        for k in ("id", "placeholder", "originalPlaceholder", "title", "label", "fieldId", "pdfFieldId"):
            v = f.get(k)
            if isinstance(v, str) and v.strip():
                row[k] = v.strip()
        if f.get("_syntheticQcVerdict"):
            row["_syntheticQcVerdict"] = True
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

    if report_output and normalized:
        deduped: list = []
        best_by_rect: dict[tuple, dict] = {}
        for row in normalized:
            rect_key = (
                int(row.get("page") or 0),
                round(float(row.get("x0") or 0), 1),
                round(float(row.get("y0") or 0), 1),
                round(float(row.get("x1") or 0), 1),
                round(float(row.get("y1") or 0), 1),
            )
            prev = best_by_rect.get(rect_key)
            if prev is None:
                best_by_rect[rect_key] = row
                deduped.append(row)
                continue
            prev_pid = str(prev.get("pdfFieldId") or "")
            cur_pid = str(row.get("pdfFieldId") or "")
            if _pdf_field_id_numeric(cur_pid) < _pdf_field_id_numeric(prev_pid):
                deduped.remove(prev)
                best_by_rect[rect_key] = row
                deduped.append(row)
        deduped.sort(key=_sort_key)
        normalized = deduped

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
    用户可读导出 PDF 文件名：仅任务模板名称（如 JXFS-JS009_V3.0_…原始记录.pdf）。
    委托编号、项目名、taskNo 等仅存数据库关联，不写入文件名。
    """
    from apps.core.library_file_service import sanitize_library_original_filename_fragment

    raw = ""
    if task_obj is not None:
        raw = (getattr(task_obj, "name", "") or "").strip()
        if not raw:
            raw = (getattr(task_obj, "code", "") or "").strip()
    if not raw:
        raw = (task_no or "").strip().replace("/", "_") or "unknown"
    if output_category == LibraryFile.CATEGORY_REPORT:
        if "报告" not in raw and not raw.lower().endswith("report"):
            raw = f"{raw}_报告"
    base = sanitize_library_original_filename_fragment(raw, max_len=200)
    if base.lower().endswith(".pdf"):
        return base[:255]
    return f"{base}.pdf"


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


def _sync_site_record_rp_sequence_before_export(
    filled_fields: list,
    source_payload: dict | None,
    task_obj,
) -> None:
    """现场记录导出前：防护表序号按 PDF 表顺序重写（合并格 n~m），并回写 filled_fields。"""
    if (
        task_obj is None
        or getattr(task_obj, "output_target", None) != LibraryTask.OUTPUT_SITE_RECORD
        or not isinstance(source_payload, dict)
        or not isinstance(filled_fields, list)
    ):
        return
    from radiation_detection_report.chapter5_field_sync import (
        build_field_bindings_from_fields,
        export_field_pdf_id,
        is_rp_table_seq_field,
        normalize_site_record_rp_sequence,
    )

    flat_fields: list = []
    _walk_template_field_dicts(filled_fields, flat_fields)
    if not flat_fields:
        flat_fields = filled_fields
    bindings = None
    chapter = source_payload.get("radiationProtectionChapter")
    if isinstance(chapter, dict):
        raw_bindings = chapter.get("fieldBindings")
        if isinstance(raw_bindings, list) and raw_bindings:
            bindings = raw_bindings
    if not bindings:
        bindings = build_field_bindings_from_fields(flat_fields)
    if not bindings:
        return
    normalize_site_record_rp_sequence(
        source_payload, bindings, flat_fields, template_payload=source_payload
    )
    seq_pids = {
        export_field_pdf_id(field).lower()
        for field in flat_fields
        if isinstance(field, dict) and is_rp_table_seq_field(field)
    }
    seq_pids.discard("")
    if not seq_pids:
        return
    dd = source_payload.get("dynamicData") if isinstance(source_payload.get("dynamicData"), dict) else {}
    tr = source_payload.get("testResult") if isinstance(source_payload.get("testResult"), dict) else {}

    def _patch(rows: list) -> None:
        for field in rows or []:
            if not isinstance(field, dict):
                continue
            pid = str(field.get("pdfFieldId") or "").strip().lower()
            if pid in seq_pids:
                val = dd.get(pid)
                if val in (None, ""):
                    val = tr.get(pid)
                if val is not None:
                    field["value"] = val
            nested = field.get("fields") or field.get("children")
            if isinstance(nested, list):
                _patch(nested)

    _patch(filled_fields)


def _persist_filled_pdf_from_submit(
    user,
    task_no: str,
    case: InspectionCase,
    project,
    filled_fields: list,
    template_pdf_id=None,
    template_json_name: str = "",
    task_obj=None,
    source_payload: dict | None = None,
    manual_device_count: int | None = None,
    *,
    site_record_batch=None,
    source_submit_relative_path: str | None = None,
    ordered_submit_payloads: Sequence[dict] | None = None,
):
    from apps.api.inspection_pdf_service import _resolve_library_task_for_task_no

    if not isinstance(filled_fields, list) or not filled_fields:
        return False, "无可用填充字段", None
    if task_obj is None:
        task_obj = _resolve_library_task_for_task_no(task_no, project)
    _sync_site_record_rp_sequence_before_export(filled_fields, source_payload, task_obj)
    normalized_fields, skipped_fields = _normalize_fields_for_htmlpdf(
        filled_fields, task_obj=task_obj
    )
    if not normalized_fields:
        return False, "模板字段缺少有效页码或坐标（支持 x/y/w/h 或 x0/y0/x1/y1）", None
    if task_obj is None:
        return False, "未找到对应任务模板，无法导出", None
    from apps.core.library_task_template_binding_service import (
        resolve_task_export_pdf_template_id,
    )

    template_pdf_id = resolve_task_export_pdf_template_id(task_obj, template_pdf_id)
    template_pdf_lf = None
    if template_pdf_id:
        template_pdf_lf = (
            LibraryFile.objects.filter(
                pk=template_pdf_id,
                category=LibraryFile.CATEGORY_TEMPLATE,
                library_tasks=task_obj,
            )
            .distinct()
            .first()
        )
        if template_pdf_lf is None:
            template_pdf_lf = (
                LibraryFile.objects.filter(
                    pk=template_pdf_id, category=LibraryFile.CATEGORY_TEMPLATE
                )
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
        pdf_bytes = htmlpdf_service.build_filled_pdf(
            normalized_fields,
            source_pdf,
            fill_font_pt=_htmlpdf_fill_font_pt(task_obj),
        )
        if (
            task_obj is not None
            and task_obj.output_target == LibraryTask.OUTPUT_REPORT
            and isinstance(source_payload, dict)
            and source_payload
        ):
            from radiation_detection_report.report_pdf_integrator import (
                try_enrich_report_pdf_with_radiation_table,
            )

            pdf_bytes = try_enrich_report_pdf_with_radiation_table(
                pdf_bytes,
                source_payload=source_payload,
                project=project,
                case=case,
                report_task=task_obj,
                task_no=task_no,
                manual_device_count=manual_device_count,
                ordered_submit_payloads=ordered_submit_payloads,
            )
    except Exception as exc:
        return False, f"PDF 渲染失败: {exc}", None
    output_category = LibraryFile.CATEGORY_REPORT if task_obj.output_target == LibraryTask.OUTPUT_REPORT else LibraryFile.CATEGORY_SITE_RECORD
    filename = build_exported_inspection_pdf_original_name(
        project, task_obj, task_no, output_category=output_category
    )
    wrapped = type("UploadLike", (), {"read": lambda self: pdf_bytes, "name": filename})()
    save_kwargs: dict = {
        "link_entity": LibraryFile.LINK_ENTITY_INSPECTION_CASE,
        "link_object_id": case.pk,
        "project_ids": [project.pk],
        "enforce_storage_quota": False,
    }
    if output_category == LibraryFile.CATEGORY_REPORT:
        save_kwargs["report_project"] = project
        save_kwargs["report_library_task"] = task_obj
    else:
        from apps.core.library_file_service import (
            resolve_site_record_batch_storage,
        )

        save_kwargs["site_record_batch"] = site_record_batch or resolve_site_record_batch_storage(
            project=project,
            case=case,
            task_no=task_no,
            source_payload=source_payload,
            source_submit_relative_path=source_submit_relative_path,
        )
    created, _ = save_library_binary_uploads(
        user, [wrapped], output_category,
        **save_kwargs,
    )
    if not created:
        return False, "PDF 保存失败（save_library_binary_uploads 未创建记录）", None
    new_lf = LibraryFile.objects.filter(pk=created[0]["id"]).first()
    if new_lf is not None and task_obj is not None:
        from apps.core.library_file_service import attach_files_to_tasks

        attach_files_to_tasks([new_lf.pk], [int(task_obj.pk)], user=user)
    if skipped_fields:
        return True, f"导出成功，但已跳过 {skipped_fields} 个不合法模板字段", new_lf
    return True, "", new_lf
