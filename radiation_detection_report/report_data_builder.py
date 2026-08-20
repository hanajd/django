"""
从现场记录 PDF 或检测提交 JSON 构建报告表输入 report_data。
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from radiation_detection_report.chapter5_field_sync import (
    CHAPTER_KEY,
    SCHEMA_KEY,
    _binding_has_dual_group,
    _protection_row_coordinate_key,
    _report_rules_indicate_dual_group,
    protection_field_column_role,
    reading_anchor_field_for_binding,
    resolve_background_row_binding,
    resolve_chapter_field_bindings,
    resolve_protection_table_layout,
)
from radiation_detection_report.report_evaluation import (
    DEFAULT_RADIATION_STANDARD,
    apply_report_evaluations,
)

_POINT_KEY_RE = re.compile(r"^(?P<location>.+?)_r(?P<row_idx>\d+)(?:_(?P<sub>.+))?$", re.I)
_RP_FIELD_ROW_RE = re.compile(
    r"^(.+?)_r(\d+)(?:_([^_]+(?:_[^_]+)*?))?_(?:测量读数(?:M\d)?|测量均值(?:Mbar)?|报出值(?:D)?(?:_\d+)?)$",
    re.I,
)
_RP_BINDING_LOC_KEYS = (
    "report_d",
    "mean_m",
    "report_d_2",
    "mean_m_2",
    "reading_1",
    "reading_2",
    "reading_3",
    "reading_1_2",
    "reading_2_2",
    "reading_3_2",
)
_RP_MEASUREMENT_COLUMN_RE = re.compile(r"测量读数|测量均值|报出值", re.I)
_RP_LOCATION_SUB_RE = re.compile(
    r"^检测点位置_r(\d+)_(.+?)_(?:测量读数|测量均值|报出值)",
    re.I,
)
_RP_ROW_MAIN_FIELD_RE = re.compile(r"^检测点位置_r(\d+)(?:_检测点位置)?$", re.I)
_RP_INVALID_MAIN_LOCATIONS = frozenset({"检测点位置", "检测项目", "检测条件"})
_RP_NON_POINT_LOCATION_MARKERS = frozenset({"本底"})
_RP_GENERIC_SLOT_LABEL_RE = re.compile(r"^栏位\d+$", re.I)
_RP_PDF_FIELD_ID_RE = re.compile(r"^f\d+$", re.I)
_RP_CONDITION_SLOT_ID_RE = re.compile(r"^(s2?|kV\d*|mA\d*|mAs\d*)$", re.I)
_RP_CALIBRATION_LOCATION_RE = re.compile(r"校准因子|能量档|137Cs|使用的检测仪器", re.I)


def _rp_location_display_text(val: str, label: str, *, field_id: str = "") -> str:
    """位置列文案：拒绝测量值/条件槽；数值 dynamicData 不覆盖标签。"""
    skip_labels = frozenset({"下划线", "状态", "检测条件", "检测项目", "检测点位置"})
    v = _norm(val)
    l = _clean_rp_location_label(label)
    fid = _norm(field_id)
    if v and not _is_invalid_rp_location_token(v):
        return v
    if l and l not in skip_labels and not _is_invalid_rp_location_token(l):
        return l
    if fid and not _is_invalid_rp_location_token(fid):
        main, _sub = _parse_rp_location_from_field_id(fid)
        if main:
            return main
    return ""


def _is_invalid_rp_location_token(raw: str) -> bool:
    """拒绝 pdfFieldId、栏位序号、检测条件槽、纯数值/kV 等非位置文案。"""
    s = _norm(raw)
    if not s or s == "/":
        return True
    if _RP_PDF_FIELD_ID_RE.fullmatch(s):
        return True
    if _RP_GENERIC_SLOT_LABEL_RE.fullmatch(s):
        return True
    if _RP_CONDITION_SLOT_ID_RE.fullmatch(s):
        return True
    if _RP_CALIBRATION_LOCATION_RE.search(s):
        return True
    if "下划线" in s:
        return True
    if s.startswith("检测条件"):
        return True
    if s in {"kV", "mA", "mAs", "s", "s2", "kV2", "mA2", "mA3", "mA4", "kV6", "kV10", "MμSv/h"}:
        return True
    if re.fullmatch(r"[\d.]+", s):
        return True
    if _is_rp_measurement_column_label(s):
        return True
    return False


def _lookup_rp_row_main_location(
    row_num: str,
    pdf_field_by_pid: Optional[Mapping[str, Mapping[str, Any]]],
    dd: Optional[Mapping[str, Any]],
) -> str:
    """从 ``检测点位置_rN`` / ``检测点位置_rN_检测点位置`` 栏位取值解析主位置。"""
    if not pdf_field_by_pid:
        return ""
    row = str(row_num or "").strip()
    if not row:
        return ""
    skip_labels = frozenset({"下划线", "状态", "检测条件", "检测项目", "检测点位置"})
    for field in pdf_field_by_pid.values():
        if not isinstance(field, dict):
            continue
        fid = str(field.get("id") or field.get("placeholder") or "")
        m = _RP_ROW_MAIN_FIELD_RE.match(fid)
        if not m or m.group(1) != row:
            continue
        pid = str(field.get("pdfFieldId") or "").strip()
        val = _cell_value(dd or {}, pid) if pid else ""
        label = _clean_rp_location_label(fid)
        text = _rp_location_display_text(val, label, field_id=fid)
        if not text or text in skip_labels:
            continue
        return _format_rp_report_main_location(text)
    return ""


def _merge_rp_report_location(main: str, sub: str) -> str:
    """单组防护表：主位置与子位置合并为整格展示文案。"""
    m = _norm(main)
    s = _norm(sub)
    if m in _RP_INVALID_MAIN_LOCATIONS:
        m = ""
    if m and s:
        if s in m:
            return _format_rp_report_main_location(m)
        if m in s:
            return _format_rp_report_main_location(s)
        return _format_rp_report_main_location(f"{m}{s}")
    if m:
        return _format_rp_report_main_location(m)
    return _format_rp_report_main_location(s)


def _flatten_single_group_report_points(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """单组数据防护表：complex 行展平为 simple 整格位置列（非主/子分列）。"""
    out: List[Dict[str, Any]] = []
    for pt in points:
        if not isinstance(pt, dict):
            continue
        if pt.get("type") == "complex":
            main = _norm(pt.get("location") or "")
            for sub_row in pt.get("sub_rows") or []:
                if not isinstance(sub_row, dict):
                    continue
                result = _norm(sub_row.get("result") or "")
                if not result:
                    continue
                out.append(
                    {
                        "type": "simple",
                        "id": sub_row.get("id"),
                        "location": _merge_rp_report_location(
                            main, str(sub_row.get("location_sub") or "")
                        ),
                        "result": result,
                        "standard": sub_row.get("standard") or DEFAULT_RADIATION_STANDARD,
                        "evaluation": sub_row.get("evaluation") or "",
                    }
                )
        else:
            out.append(dict(pt))
    return out

_DEFAULT_NOTES = [
    "1. 上表中检测结果未扣除本底值。",
    "2. 检测结果已按响应时间修正系数修正。",
]
_ANNUAL_DOSE_NOTE_BODY = "年剂量估算=最大曝光时间×每周曝光次数×50"


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip())


def _norm_result_cell(s: Any) -> str:
    """检测结果格：保留组间换行，仅去掉首尾空白。"""
    return str(s or "").strip()


def _iter_report_data_points(data: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for key in ("points", "points_group2"):
        items = data.get(key) or []
        if not isinstance(items, list):
            continue
        for pt in items:
            if isinstance(pt, dict):
                rows.append(pt)
                for sub in pt.get("sub_rows") or []:
                    if isinstance(sub, dict):
                        rows.append(sub)
    return rows


def _annual_dose_cell_has_value(raw: Any) -> bool:
    s = _norm(raw)
    if not s or s in ("/", "—", "-", "–"):
        return False
    return True


def _report_data_has_annual_dose(data: Mapping[str, Any]) -> bool:
    """仅当防护点位实际填有年剂量估算时，才追加第 3 条注释。"""
    for row in _iter_report_data_points(data):
        if _annual_dose_cell_has_value(row.get("annual_dose_msv")):
            return True
    return False


def _notes_already_have_annual_dose_formula(notes: Sequence[str]) -> bool:
    blob = re.sub(r"\s+", "", "".join(str(n) for n in notes))
    return "年剂量估算" in blob and "最大曝光时间" in blob and "每周曝光次数" in blob and "×" in blob


def _rewrite_annual_dose_formula_in_notes(notes: Sequence[str]) -> List[str]:
    """旧版用 * 的公式改成 ×；已是 × 则原样返回。"""
    out: List[str] = []
    changed = False
    for n in notes:
        s = str(n)
        if "年剂量估算" in s and "最大曝光时间" in s and "*" in s:
            s = s.replace("*", "×")
            changed = True
        out.append(s)
    return out if changed else list(notes)


def _next_note_number(notes: Sequence[str]) -> int:
    max_n = 0
    for n in notes:
        for m in re.finditer(r"(?:^|[\n；;。]\s*)(\d{1,2})\s*[.．、]", str(n)):
            max_n = max(max_n, int(m.group(1)))
    return max_n + 1 if max_n else len([x for x in notes if str(x).strip()]) + 1


def _ensure_annual_dose_note(data: Mapping[str, Any]) -> Dict[str, Any]:
    """防护表含年剂量估算时，在表末注释追加计算公式。"""
    if not isinstance(data, dict):
        return {}
    out = dict(data)
    if not _report_data_has_annual_dose(out):
        return out
    notes = [str(n).rstrip() for n in (out.get("notes") or []) if str(n).strip()]
    notes = _rewrite_annual_dose_formula_in_notes(notes)
    if _notes_already_have_annual_dose_formula(notes):
        out["notes"] = notes
        return out
    if not notes:
        notes = list(_DEFAULT_NOTES)
    notes.append(f"{_next_note_number(notes)}. {_ANNUAL_DOSE_NOTE_BODY}")
    out["notes"] = notes
    return out


def _dynamic_data(payload: Mapping[str, Any]) -> Dict[str, Any]:
    dd = payload.get("dynamicData")
    return dd if isinstance(dd, dict) else {}


def _cell_value(dd: Mapping[str, Any], pid: str) -> str:
    key = _norm(pid).lower()
    if not key:
        return ""
    raw = dd.get(key)
    if raw is None:
        raw = dd.get(pid)
    if raw is None or isinstance(raw, bool):
        return ""
    s = _norm(raw)
    return "" if s == "/" else s


def _raw_cell(dd: Mapping[str, Any], pid: str) -> Any:
    key = _norm(pid).lower()
    if not key:
        return None
    raw = dd.get(key)
    if raw is None:
        raw = dd.get(pid)
    return raw


_BINDING_MEASUREMENT_KEYS = (
    "reading_1",
    "reading_2",
    "reading_3",
    "reading_1_2",
    "reading_2_2",
    "reading_3_2",
    "mean_m",
    "mean_m_2",
    "report_d",
    "report_d_2",
    "seq_no",
)

_REPORT_RESULT_KEYS = ("report_d", "report_d_2")
_GROUP1_RESULT_KEYS = ("report_d",)
_GROUP2_RESULT_KEYS = ("report_d_2",)
_GROUP1_MEASUREMENT_KEYS = (
    "reading_1",
    "reading_2",
    "reading_3",
    "mean_m",
    "report_d",
    "seq_no",
)
_GROUP2_MEASUREMENT_KEYS = (
    "reading_1_2",
    "reading_2_2",
    "reading_3_2",
    "mean_m_2",
    "report_d_2",
    "annual_dose_msv",
    "seq_no",
)
_CONDITION_FIELD_SKIP_RE = re.compile(
    r"仪器|校准因子|能量档|137Cs|Cs校准|修正系数|探测|响应时间|序号_r|kVmAs|mAs探测",
    re.I,
)
# 仅匹配扫描检测条件语义槽位，勿把仪器校准区（如 CT 模板 f213–f218）误纳入。
_CONDITION_SLOT_FIELD_RES: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("prefix", re.compile(r"^检测条件①和②$|^检测条件_2$|^检测条件1$", re.I)),
    ("exposure_mode", re.compile(r"检测条件_曝光模式", re.I)),
    ("body_part", re.compile(r"部位", re.I)),
    ("tube", re.compile(r"检测条件_球管|球管朝", re.I)),
    ("kv", re.compile(r"^kV$|^kV2$|^电压kV$", re.I)),
    ("ma", re.compile(r"^mA$|^mA3$|^电流mA$", re.I)),
    ("s", re.compile(r"^s$|^s2$|^时间s$", re.I)),
    ("mas", re.compile(r"^mA2$|^mA4$|mAs有效", re.I)),
    ("phantom", re.compile(r"散射模体", re.I)),
)
_GROUP2_CONDITION_MARKERS = (
    "检测条件②",
    "检测条件_2",
    "检测条件_曝光模式2",
    "检测条件_球管朝向",
    "下划线2",
    "kV2",
    "s2",
    "mA2",
    "mA4",
)


def _binding_is_slash_skipped(binding: Mapping[str, Any], dd: Mapping[str, Any]) -> bool:
    """测量列或报出值出现 / 表示该项未检测，整行不进入报告表。"""
    for key in _BINDING_MEASUREMENT_KEYS:
        pid = str(binding.get(key) or "").strip()
        if not pid:
            continue
        raw = _raw_cell(dd, pid)
        if str(raw).strip() == "/":
            if key == "seq_no":
                continue
            return True
    return False


def _binding_is_slash_skipped_group(
    binding: Mapping[str, Any],
    dd: Mapping[str, Any],
    *,
    group: int,
    payload: Optional[Mapping[str, Any]] = None,
    pdf_field_by_pid: Optional[Mapping[str, Mapping[str, Any]]] = None,
    chapter: Optional[Mapping[str, Any]] = None,
) -> bool:
    """该组报出值格为 / 或未填时整组无效；读数 / 仅在无报出值时否决行。"""
    report_key = "report_d_2" if group == 2 else "report_d"
    report_pid = str(binding.get(report_key) or "").strip()
    if report_pid:
        raw_report = _raw_cell(dd, report_pid)
        if str(raw_report or "").strip() == "/":
            return True
    if _result_from_binding_group(
        binding,
        dd,
        group=group,
        payload=payload,
        pdf_field_by_pid=pdf_field_by_pid,
        chapter=chapter,
    ):
        return False
    keys = _GROUP2_MEASUREMENT_KEYS if group == 2 else _GROUP1_MEASUREMENT_KEYS
    for key in keys:
        if not key.startswith("reading_"):
            continue
        pid = str(binding.get(key) or "").strip()
        if not pid:
            continue
        raw = _raw_cell(dd, pid)
        if str(raw).strip() == "/":
            return True
    return False


def _result_from_binding_group(
    binding: Mapping[str, Any],
    dd: Mapping[str, Any],
    *,
    group: int,
    payload: Optional[Mapping[str, Any]] = None,
    pdf_field_by_pid: Optional[Mapping[str, Mapping[str, Any]]] = None,
    chapter: Optional[Mapping[str, Any]] = None,
) -> str:
    """报告表检测结果：仅报出值 D 栏位；绝不回退到测量均值。"""
    report_key = "report_d_2" if group == 2 else "report_d"
    mean_key = "mean_m_2" if group == 2 else "mean_m"
    pid = str(binding.get(report_key) or "").strip()
    if not pid:
        return ""
    mean_val = ""
    mean_pid = str(binding.get(mean_key) or "").strip()
    if mean_pid:
        mean_val = _value_from_payload(payload, mean_pid) if payload else _cell_value(dd, mean_pid)
    stored = _value_from_payload(payload, pid) if payload else _cell_value(dd, pid)
    stored = _norm(stored) if stored else ""
    if stored and stored != "/" and (not mean_val or stored != mean_val):
        return stored
    computed = ""
    if payload and pdf_field_by_pid:
        computed = _computed_report_value_from_field(
            payload,
            pid,
            pdf_field_by_pid,
            binding=binding,
            group=group,
            chapter=chapter,
        )
    if computed:
        return computed
    return ""


def _annual_dose_from_binding(binding: Mapping[str, Any], dd: Mapping[str, Any]) -> str:
    return _cell_value(dd, str(binding.get("annual_dose_msv") or ""))


def _binding_row_sort_key(binding: Mapping[str, Any]) -> Tuple[int, float, str]:
    page, y = _parse_rp_row_coordinate(str(binding.get("radiationPoint") or ""))
    return (
        page or 0,
        y if y is not None else 0.0,
        str(binding.get("radiationPoint") or ""),
    )


def _seq_id_from_binding(binding: Mapping[str, Any], dd: Mapping[str, Any]) -> str:
    pid = str(binding.get("seq_no") or "").strip()
    if not pid:
        return ""
    return _normalize_report_seq_id(_cell_value(dd, pid))


def _make_binding_data_row(
    *,
    main_loc: str,
    sub_loc: str,
    result: str,
    annual_dose_msv: str = "",
    point_id: str = "",
) -> Dict[str, Any]:
    return {
        "location_main": main_loc,
        "location_sub": sub_loc,
        "result": result,
        "annual_dose_msv": annual_dose_msv,
        "point_id": point_id,
        "standard": DEFAULT_RADIATION_STANDARD,
        "evaluation": "",
    }


def _group_binding_rows_into_complex_points(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """按主位置/子位置合并为 complex 行，与现场记录层级一致。"""
    groups: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    def _flush() -> None:
        nonlocal current
        if current is not None:
            groups.append(current)
            current = None

    for row in rows:
        if not isinstance(row, dict):
            continue
        main = _norm(row.get("location_main") or "")
        sub = _norm(row.get("location_sub") or "")
        result = _norm_result_cell(row.get("result") or "")
        if not result or result == "/":
            continue
        sub_item = {
            "id": _normalize_report_seq_id(row.get("point_id") or row.get("id") or ""),
            "location_sub": sub,
            "result": result,
            "annual_dose_msv": _norm(row.get("annual_dose_msv") or ""),
            "standard": row.get("standard") or DEFAULT_RADIATION_STANDARD,
            "evaluation": row.get("evaluation") or "",
        }
        if sub:
            if main and (current is None or _norm(current.get("location") or "") != main):
                _flush()
                current = {"type": "complex", "location": main, "sub_rows": []}
            if current is None:
                if main:
                    current = {"type": "complex", "location": main, "sub_rows": []}
                else:
                    _flush()
                    groups.append(
                        {
                            "type": "simple",
                            "location": sub,
                            "id": sub_item["id"],
                            "result": sub_item["result"],
                            "annual_dose_msv": sub_item["annual_dose_msv"],
                            "standard": sub_item["standard"],
                            "evaluation": sub_item["evaluation"],
                        }
                    )
                    continue
            current["sub_rows"].append(sub_item)
        elif main:
            _flush()
            groups.append(
                {
                    "type": "simple",
                    "location": main,
                    "id": _normalize_report_seq_id(row.get("point_id") or row.get("id") or ""),
                    "result": result,
                    "annual_dose_msv": _norm(row.get("annual_dose_msv") or ""),
                    "standard": row.get("standard") or DEFAULT_RADIATION_STANDARD,
                    "evaluation": row.get("evaluation") or "",
                }
            )
    _flush()
    return groups


def _row_is_active_group(
    binding: Mapping[str, Any],
    dd: Mapping[str, Any],
    *,
    group: int,
    payload: Optional[Mapping[str, Any]] = None,
    pdf_field_by_pid: Optional[Mapping[str, Mapping[str, Any]]] = None,
    chapter: Optional[Mapping[str, Any]] = None,
) -> bool:
    if _binding_is_slash_skipped_group(
        binding,
        dd,
        group=group,
        payload=payload,
        pdf_field_by_pid=pdf_field_by_pid,
        chapter=chapter,
    ):
        return False
    return bool(
        _result_from_binding_group(
            binding,
            dd,
            group=group,
            payload=payload,
            pdf_field_by_pid=pdf_field_by_pid,
            chapter=chapter,
        )
    )


def _row_is_active(binding: Mapping[str, Any], dd: Mapping[str, Any]) -> bool:
    """报告表仅保留有报出值的行。"""
    if _binding_is_slash_skipped(binding, dd):
        return False
    return bool(_result_from_binding(binding, dd))


def _parse_radiation_point_key(key: str) -> Tuple[str, Optional[int], Optional[str]]:
    k = _norm(key)
    m = _POINT_KEY_RE.match(k)
    if not m:
        return k, None, None
    loc = _format_rp_report_main_location(_norm(m.group("location")))
    try:
        row_idx = int(m.group("row_idx"))
    except (TypeError, ValueError):
        row_idx = None
    sub = _norm(m.group("sub") or "") or None
    return loc, row_idx, sub



def _result_from_binding(
    binding: Mapping[str, Any],
    dd: Mapping[str, Any],
    *,
    payload: Optional[Mapping[str, Any]] = None,
    pdf_field_by_pid: Optional[Mapping[str, Mapping[str, Any]]] = None,
    chapter: Optional[Mapping[str, Any]] = None,
) -> str:
    """报告表检测结果列：仅报出值 D，不用测量均值或原始读数。"""
    for group, key in ((1, "report_d"), (2, "report_d_2")):
        pid = str(binding.get(key) or "").strip()
        if not pid:
            continue
        v = _result_from_binding_group(
            binding,
            dd,
            group=group,
            payload=payload,
            pdf_field_by_pid=pdf_field_by_pid,
            chapter=chapter,
        )
        if v:
            return v
    return ""


def _point_has_report_result(point: Mapping[str, Any]) -> bool:
    if point.get("type") == "complex":
        subs = point.get("sub_rows") or []
        return any(_norm(sub.get("result") or "") for sub in subs if isinstance(sub, dict))
    return bool(_norm(point.get("result") or ""))


def _filter_points_with_results(points: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """去掉无报出值的检测点行。"""
    out: List[Dict[str, Any]] = []
    for point in points:
        if not isinstance(point, dict) or not _point_has_report_result(point):
            continue
        if point.get("type") == "complex":
            subs = [
                dict(sub)
                for sub in (point.get("sub_rows") or [])
                if isinstance(sub, dict) and _norm(sub.get("result") or "")
            ]
            if not subs:
                continue
            pt = dict(point)
            pt["sub_rows"] = subs
            out.append(pt)
        else:
            out.append(dict(point))
    return out


def _is_background_binding(binding: Mapping[str, Any]) -> bool:
    """本底水平行绑定不得进入检测点列表。"""
    pt = _norm(binding.get("radiationPoint") or "")
    if not pt:
        return False
    if "本底" in pt or "序号本底" in pt:
        return True
    return False


def _payload_bucket(payload: Mapping[str, Any], bucket: str) -> Dict[str, Any]:
    raw = payload.get(bucket)
    return raw if isinstance(raw, dict) else {}


def _value_from_payload(payload: Mapping[str, Any], pid: str) -> str:
    for src in (_dynamic_data(payload), _payload_bucket(payload, "testResult")):
        v = _cell_value(src, pid)
        if v:
            return v
    return ""


def _report_value_mapping(
    payload: Mapping[str, Any],
    *,
    chapter: Optional[Mapping[str, Any]] = None,
    pdf_field_by_pid: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    vm: Dict[str, Any] = {}
    for bucket in ("dynamicData", "testResult"):
        src = _payload_bucket(payload, bucket)
        for key, val in src.items():
            if val is None or isinstance(val, bool):
                continue
            pid = _norm(str(key))
            if not pid:
                continue
            low = pid.lower()
            if low not in vm:
                vm[low] = val
            if pid not in vm:
                vm[pid] = val
    if not _norm(vm.get("f982") or vm.get("F982")):
        try:
            from radiation_detection_report.chapter5_field_sync import (
                _chapter_g1_calibration_factor_pid,
                resolve_background_calibration_factor_pid,
            )

            factor_candidates: List[str] = []
            if isinstance(chapter, dict):
                fp = _chapter_g1_calibration_factor_pid(chapter)
                if fp:
                    factor_candidates.append(fp.lower())
                fields = (
                    list(pdf_field_by_pid.values())
                    if isinstance(pdf_field_by_pid, Mapping)
                    else []
                )
                fp2 = resolve_background_calibration_factor_pid(chapter, fields)
                if fp2:
                    factor_candidates.append(str(fp2).lower())
            for fp in ("f982", "f159", "f927"):
                if fp not in factor_candidates:
                    factor_candidates.append(fp)
            for fp in factor_candidates:
                val = vm.get(fp) or vm.get(fp.upper())
                if val not in (None, "", "/"):
                    vm["f982"] = val
                    vm["F982"] = val
                    break
        except Exception:
            pass
    return vm


def _format_computed_report_value(raw: Any, field: Optional[Mapping[str, Any]]) -> str:
    if raw is None:
        return ""
    try:
        from radiation_detection_report.chapter5_field_sync import (
            format_protection_cell_display,
            protection_numeric_cell_kind,
        )

        kind = protection_numeric_cell_kind(field) if isinstance(field, Mapping) else ""
        if kind in ("mean", "report", "reading", "other"):
            s = format_protection_cell_display(raw, field if isinstance(field, dict) else None, fixed=True)
            return "" if s == "/" else s
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return format_protection_cell_display(raw, field if isinstance(field, dict) else None, fixed=True)
    except ImportError:
        pass
    if isinstance(raw, str):
        s = _norm(raw)
        return "" if s == "/" else s
    s = _norm(raw)
    return "" if s == "/" else s


def _computed_report_value_from_field(
    payload: Mapping[str, Any],
    pid: str,
    field_by_pid: Mapping[str, Mapping[str, Any]],
    *,
    binding: Optional[Mapping[str, Any]] = None,
    group: int = 1,
    chapter: Optional[Mapping[str, Any]] = None,
) -> str:
    field = field_by_pid.get(_norm(pid).lower())
    if not isinstance(field, dict):
        return ""
    try:
        from utils.conditional_field_rules import resolve_field_formula_for_eval
        from utils.dynamic_form_expression import eval_computed_formula
        from radiation_detection_report.chapter5_field_sync import (
            manual_report_value_rules_for_binding,
        )
    except ImportError:
        return ""

    vm = _report_value_mapping(
        payload,
        chapter=chapter,
        pdf_field_by_pid=field_by_pid,
    )
    formula = resolve_field_formula_for_eval(field, vm)
    if formula and isinstance(binding, dict) and (
        "{mean}" in formula or "{mean2}" in formula or "__ROW_MEAN" in formula
    ):
        from radiation_detection_report.chapter5_field_sync import _substitute_row_tokens

        formula = _substitute_row_tokens(formula, binding, report_group=group)
    if not formula and isinstance(binding, dict):
        rules = (
            chapter.get("reportValueRules")
            if isinstance(chapter, dict) and isinstance(chapter.get("reportValueRules"), list)
            else []
        )
        manual = manual_report_value_rules_for_binding(rules, binding, report_group=group)
        if manual:
            formula = _norm(manual[0].get("expression") or manual[0].get("formula"))
    if not formula:
        return ""
    try:
        res = eval_computed_formula(formula, vm)
    except Exception:
        return ""
    return _format_computed_report_value(res, field)


def _parse_float_token(text: str) -> Optional[float]:
    m = re.search(r"[-+]?\d*\.?\d+", str(text or "").replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _parse_background_reading_token(text: str) -> Optional[float]:
    """本底读数：忽略 /、模拟占位与非数值文案。"""
    s = _norm(text)
    if not s or s == "/":
        return None
    if s.startswith("模拟_") or s.lower().startswith("mock_"):
        return None
    return _parse_float_token(s)


def _format_background_range(lo: float, hi: float) -> str:
    if abs(lo - hi) < 1e-9:
        return f"{lo:g}"
    return f"{lo:g}~{hi:g}"


def _format_condition_text(text: str) -> str:
    text = _norm(text)
    if not text:
        return ""
    if not text.startswith("检测条件"):
        text = f"检测条件：{text.lstrip('：:')}"
    if not text.endswith("。"):
        text += "。"
    return text


def _looks_like_calibration_token(text: str) -> bool:
    n = _parse_float_token(text)
    if n is None:
        return False
    return 0 < abs(n) <= 3.5


def _sanitize_extracted_condition_slots(
    slots: Optional[Mapping[str, str]],
) -> Dict[str, str]:
    """过滤 PDF 红字误落入仪器校准区的槽位（如 DSA 横版 f80/f81 的 0.98/0.85）。"""
    if not isinstance(slots, Mapping):
        return {}
    out: Dict[str, str] = {}
    for key, raw in slots.items():
        val = _norm(raw)
        if not val or val == "/":
            continue
        slot = str(key)
        if slot in ("kv", "ma", "s", "mas"):
            validators = {
                "kv": _looks_like_kv_value,
                "ma": _looks_like_ma_value,
                "s": _looks_like_s_value,
                "mas": lambda t: _looks_like_ma_value(t) and not _looks_like_calibration_token(t),
            }
            if not validators.get(slot, lambda _t: True)(val):
                continue
        elif slot in ("body_part", "tube", "prefix", "exposure_mode") and _looks_like_calibration_token(val):
            continue
        out[slot] = val
    return out


def _looks_like_kv_value(text: str) -> bool:
    n = _parse_float_token(text)
    return n is not None and 40 <= n <= 350


def _looks_like_ma_value(text: str) -> bool:
    n = _parse_float_token(text)
    return n is not None and 5 <= n <= 1000


def _looks_like_s_value(text: str) -> bool:
    n = _parse_float_token(text)
    return n is not None and 0.3 <= n <= 300 and not (0 < n <= 3.5)


def _format_param_number(text: str) -> str:
    n = _parse_float_token(text)
    if n is None:
        return _norm(text)
    if abs(n - round(n)) < 1e-9:
        return str(int(round(n)))
    return f"{n:g}"


def _compose_rp_condition_template_line(slots: Mapping[str, str]) -> str:
    """
    按现场记录版式拼整行检测条件（与 JS-009 红字行一致）：
    检测条件：曝光模式（部位：…），球管朝…照射，…kV …mA …s（…mAs）
    """
    exposure = _norm(slots.get("exposure_mode") or slots.get("prefix") or "")
    body = _norm(slots.get("body_part") or "")
    part = body or exposure
    if _looks_like_calibration_token(part):
        part = exposure if exposure and not _looks_like_calibration_token(exposure) else ""
    tube = _norm(slots.get("tube") or "")
    if tube and _looks_like_calibration_token(tube):
        tube = ""
    kv_raw = _norm(slots.get("kv") or "")
    ma_raw = _norm(slots.get("ma") or "")
    s_raw = _norm(slots.get("s") or "")
    mas_raw = _norm(slots.get("mas") or "")
    kv = _format_param_number(kv_raw) if kv_raw and _looks_like_kv_value(kv_raw) else ""
    ma = _format_param_number(ma_raw) if ma_raw and _looks_like_ma_value(ma_raw) else ""
    s_val = _format_param_number(s_raw) if s_raw and _looks_like_s_value(s_raw) else ""
    mas = (
        _format_param_number(mas_raw)
        if mas_raw and _looks_like_ma_value(mas_raw) and not _looks_like_calibration_token(mas_raw)
        else ""
    )
    if not any((part, tube, kv, ma, s_val, mas)):
        return ""
    tube_seg = f"球管朝{tube}照射" if tube else "球管朝照射"
    params: List[str] = []
    if kv:
        params.append(f"{kv}kV")
    if ma:
        params.append(f"{ma}mA")
    if s_val:
        params.append(f"{s_val}s")
    param_mid = " ".join(params)
    mas_seg = f"（{mas} mAs）" if mas else ""
    core = f"曝光模式（部位：{part}），{tube_seg}"
    if param_mid:
        core = f"{core}，{param_mid}"
    if mas_seg:
        core = f"{core}{mas_seg}"
    return _format_condition_text(core)


def _compose_rp_scan_condition(slots: Mapping[str, str]) -> str:
    """将各槽位拼成报告表检测条件行，如「CT腹部螺旋扫描，120kV，200mA，9.7s，散射模体：…」。"""
    prefix = _norm(slots.get("prefix") or "")
    exposure = _norm(slots.get("exposure_mode") or "")
    body = _norm(slots.get("body_part") or "")
    tube = _norm(slots.get("tube") or "")
    phantom = _norm(slots.get("phantom") or "")

    head = prefix
    if exposure and exposure not in head:
        head = f"{head}{exposure}" if head else exposure
    if body and body not in head and not _looks_like_calibration_token(body):
        head = f"{head}{body}" if head else body
    if head and _looks_like_calibration_token(head):
        head = ""
    if tube and not _looks_like_calibration_token(tube):
        if head:
            head = f"{head}，球管朝{tube}照射"
        else:
            head = f"球管朝{tube}照射"

    params: List[str] = []
    kv = _norm(slots.get("kv") or "")
    ma = _norm(slots.get("ma") or "")
    s_val = _norm(slots.get("s") or "")
    mas = _norm(slots.get("mas") or "")
    if kv and _looks_like_kv_value(kv):
        params.append(f"{_format_param_number(kv)}kV")
    if ma and _looks_like_ma_value(ma):
        params.append(f"{_format_param_number(ma)}mA")
    if s_val and _looks_like_s_value(s_val):
        params.append(f"{_format_param_number(s_val)}s")
    elif mas and _looks_like_ma_value(mas):
        params.append(f"{_format_param_number(mas)}mAs")

    parts: List[str] = []
    if head:
        parts.append(head)
    if params:
        parts.append("，".join(params))
    if phantom:
        parts.append(phantom if "散射模体" in phantom else f"散射模体：{phantom}")

    body_text = "，".join(parts)
    return _format_condition_text(body_text) if body_text else ""


def _field_meta_labels(field: Mapping[str, Any]) -> str:
    return " ".join(
        _norm(field.get(key) or "")
        for key in ("id", "title", "label", "hierarchyKey")
    )


def _is_rp_calibration_field(field: Mapping[str, Any]) -> bool:
    labels = _field_meta_labels(field)
    if not labels:
        return False
    if _CONDITION_FIELD_SKIP_RE.search(labels):
        return True
    fid = _norm(field.get("id") or "")
    if fid in {"检测条件1", "检测"} and "137Cs" in labels:
        return True
    if fid in {"f213", "f214", "f215", "f217", "f218"}:
        return True
    return False


def _condition_slot_for_field(field: Mapping[str, Any], *, group: int) -> Optional[str]:
    labels = _field_meta_labels(field)
    if not labels or _is_rp_calibration_field(field):
        return None
    fid = _norm(field.get("id") or "")
    is_g2 = bool(
        _GROUP2_CONDITION_FIELD_RE.search(fid)
        or _GROUP2_CONDITION_FIELD_RE.search(labels)
        or (group == 2 and fid in {"kV2", "mA3", "mA4", "s2"})
    )
    if group == 2 and not is_g2:
        return None
    if group == 1 and is_g2:
        return None
    if re.fullmatch(r"下划线[34]", fid, flags=re.I):
        return "body_part" if group == 1 else None
    if fid == "下划线2":
        return "body_part" if group == 2 else None
    if fid == "mA":
        return "ma" if group == 1 else None
    if fid in ("mA3", "mA4"):
        return "ma" if group == 2 else None
    if fid == "mA2":
        return "mas" if group == 1 else None
    for slot, pattern in _CONDITION_SLOT_FIELD_RES:
        if pattern.search(fid) or pattern.search(labels):
            if slot == "kv" and group == 2:
                return "kv"
            if slot == "ma" and group == 2:
                return "ma"
            if slot == "s" and group == 2:
                return "s"
            return slot
    return None


def _iter_form_schema_rp_fields(
    site_template_parsed: Optional[Mapping[str, Any]],
) -> List[Mapping[str, Any]]:
    if not isinstance(site_template_parsed, dict):
        return []
    form_schema = site_template_parsed.get("formSchema") or site_template_parsed.get("form_schema")
    if not isinstance(form_schema, dict):
        return []
    found: List[Mapping[str, Any]] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("id") == CHAPTER_KEY and isinstance(node.get("fields"), list):
                for item in node["fields"]:
                    if isinstance(item, dict):
                        found.append(item)
            for val in node.values():
                _walk(val)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(form_schema)
    return found


def _resolve_rp_condition_slots(
    payload: Mapping[str, Any],
    site_template_parsed: Optional[Mapping[str, Any]],
    *,
    group: int,
) -> Dict[str, str]:
    slots: Dict[str, str] = {}
    seen_fields: set[str] = set()
    candidates: List[Mapping[str, Any]] = []
    candidates.extend(_iter_form_schema_rp_fields(site_template_parsed))
    for field in _protection_pdf_fields_from_template(site_template_parsed):
        if isinstance(field, dict):
            candidates.append(field)
    for field in candidates:
        if not isinstance(field, dict):
            continue
        if str(field.get("templateSectionKey") or "").strip() != CHAPTER_KEY:
            continue
        pid = str(field.get("pdfFieldId") or field.get("id") or "").strip()
        if not pid or pid in seen_fields:
            continue
        seen_fields.add(pid)
        slot = _condition_slot_for_field(field, group=group)
        if not slot:
            continue
        val = _value_from_payload(payload, pid)
        if not val:
            continue
        if _looks_like_calibration_token(val) and slot in ("kv", "ma", "s", "mas", "body_part", "tube", "exposure_mode", "prefix"):
            continue
        if slot == "kv" and not _looks_like_kv_value(val):
            continue
        if slot in ("ma", "mas") and not _looks_like_ma_value(val):
            continue
        if slot == "s" and not _looks_like_s_value(val):
            continue
        if slot in ("body_part", "exposure_mode", "prefix", "tube"):
            if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", _norm(val)):
                continue
            if slot == "tube" and _looks_like_kv_value(val):
                continue
        page_no = 0
        rect = field.get("rect")
        if isinstance(rect, (list, tuple)) and rect:
            try:
                page_no = int(rect[0])
            except (TypeError, ValueError):
                page_no = 0
        _, y_pt = _field_pdf_xy(field)
        if page_no == 4 and y_pt > 140.0:
            continue
        slots[slot] = val
    return slots


def _condition_slots_from_extracted_preface(
    extracted: Mapping[str, Any],
) -> Dict[str, str]:
    preface = extracted.get("preface")
    if not isinstance(preface, dict):
        return {}
    cond = preface.get("condition")
    if not isinstance(cond, dict):
        return {}
    raw_slots = cond.get("slots")
    if not isinstance(raw_slots, dict):
        return {}
    return {str(k): _norm(v) for k, v in raw_slots.items() if _norm(v)}


def _is_radiation_condition_field(field_id: str) -> bool:
    """已弃用宽匹配；保留供旧调用方判断，始终返回 False。"""
    return False


_GROUP2_CONDITION_FIELD_RE = re.compile(
    r"检测条件②|检测条件_2|检测条件_曝光模式2|检测条件_球管朝向|^下划线2$|^kV2$|^s2$|^mA4$",
    re.I,
)


def _condition_for_group(
    payload: Mapping[str, Any],
    site_template_parsed: Optional[Mapping[str, Any]],
    *,
    group: int,
    extracted_slots: Optional[Mapping[str, str]] = None,
) -> str:
    slots = _resolve_rp_condition_slots(payload, site_template_parsed, group=group)
    if extracted_slots and group == 1:
        for key, val in _sanitize_extracted_condition_slots(extracted_slots).items():
            if val and key not in slots:
                slots[key] = val
    template_line = _finalize_condition_line_for_group(
        _compose_rp_condition_template_line(slots), group=group
    )
    if template_line:
        return template_line
    composed = _finalize_condition_line_for_group(_compose_rp_scan_condition(slots), group=group)
    if composed:
        return composed
    scanned = _condition_from_payload_scan(payload, group=group)
    if scanned:
        return scanned
    return ""


def _finalize_condition_line_for_group(text: str, *, group: int) -> str:
    t = _format_condition_text(text) if text else ""
    if group == 2 and t and "kV" not in t:
        return ""
    return t


def _conditions_from_template_fields(
    payload: Mapping[str, Any],
    site_template_parsed: Optional[Mapping[str, Any]],
    *,
    extracted_slots: Optional[Mapping[str, str]] = None,
) -> Tuple[str, str]:
    g1 = _condition_for_group(
        payload,
        site_template_parsed,
        group=1,
        extracted_slots=extracted_slots,
    )
    g2 = _condition_for_group(payload, site_template_parsed, group=2)
    return g1, g2


def _protection_pdf_fields_from_template(
    site_template_parsed: Optional[Mapping[str, Any]],
) -> List[Mapping[str, Any]]:
    """统一模板 v2：pdf.fields 或 parse 后根级 fields。"""
    if not isinstance(site_template_parsed, dict):
        return []
    root = site_template_parsed.get("fields")
    if isinstance(root, list) and root:
        return [f for f in root if isinstance(f, dict)]
    pdf = site_template_parsed.get("pdf")
    if isinstance(pdf, dict):
        nested = pdf.get("fields")
        if isinstance(nested, list) and nested:
            return [f for f in nested if isinstance(f, dict)]
    return []


def _field_pdf_xy(field: Mapping[str, Any]) -> Tuple[float, float]:
    rect = field.get("rect") or []
    if isinstance(rect, (list, tuple)) and len(rect) >= 3:
        try:
            return float(rect[1]), float(rect[2])
        except (TypeError, ValueError):
            pass
    try:
        return float(field.get("x") or 0.0), float(field.get("y") or 0.0)
    except (TypeError, ValueError):
        return 0.0, 0.0


_RP_LEADING_CM_VERTICAL_DISTANCE_RE = re.compile(
    r"^(\d+(?:\.\d+)?)\s*cm(.+?(?:距楼下地面|距顶棚地面))\s*$",
    re.I,
)


def _normalize_rp_vertical_distance_location(text: str) -> str:
    """
    PDF 解析常把印刷占位「170cm」粘在位置名前（170cm地面下方…距楼下地面）；
    报告表应与现场记录一致：地面下方（楼下）距楼下地面170 cm。
    """
    s = _norm(text)
    if not s:
        return ""
    m = _RP_LEADING_CM_VERTICAL_DISTANCE_RE.match(s.replace(" ", ""))
    if not m:
        return s
    num, body = m.group(1), m.group(2).strip()
    if not body:
        return s
    return f"{body}{num} cm"


def _clean_rp_location_label(raw: str) -> str:
    s = _norm(raw)
    if not s:
        return ""
    s = re.sub(r"_r\d+(?:_\w+)?$", "", s, flags=re.I)
    s = re.sub(r"_检测点位置(?:_\d+)?$", "", s, flags=re.I)
    s = re.sub(r"_?测量读数.*$", "", s, flags=re.I)
    s = re.sub(r"_?测量均值.*$", "", s, flags=re.I)
    s = re.sub(r"_?报出值.*$", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" _")
    return s


def _is_rp_measurement_column_label(raw: str) -> bool:
    s = _norm(raw)
    if not s:
        return False
    return bool(_RP_MEASUREMENT_COLUMN_RE.search(s))


def _sanitize_rp_location_sub(raw: str) -> str:
    s = _clean_rp_location_label(raw)
    if not s or _is_rp_measurement_column_label(s) or _is_invalid_rp_location_token(s):
        return ""
    if s in {"检测点位置", "报出值D", "测量均值Mbar", "MμSv/h", "/"}:
        return ""
    return s


def _format_rp_report_main_location(raw: str) -> str:
    """第五章报告表：主位置列展示文案。"""
    s = _normalize_rp_vertical_distance_location(_clean_rp_location_label(raw))
    if not s or _is_invalid_rp_location_token(s):
        return ""
    if s in {"排风口外表面cm", "排风口外表面 cm"} or ("排风" in s and "cm" in s and len(s) < 24):
        return "排风扇/排风口外表面30cm 处（距地面高度约 cm）"
    if "排风扇" in s or "排风口" in s:
        return "排风扇/排风口外表面30cm 处（距地面高度约 cm）"
    s = re.sub(r"([^\s])30cm外表面处", r"\g<1>30cm 外表面处", s)
    s = re.sub(r"([^\s）])外表面30cm处", r"\g<1>外表面30cm 处", s)
    s = re.sub(r"防护门M外表面处30cm", "防护门M（ ）外表面30cm 处", s)
    return s


def _parse_rp_location_from_field_id(
    raw: str,
    *,
    pdf_field_by_pid: Optional[Mapping[str, Mapping[str, Any]]] = None,
    dd: Optional[Mapping[str, Any]] = None,
) -> Tuple[str, str]:
    """从现场模板栏位 id 解析 (主位置, 子位置)。"""
    s = _norm(raw)
    if not s:
        return "", ""
    m = _RP_FIELD_ROW_RE.match(s)
    if m:
        main_raw = _norm(m.group(1) or "")
        row_num = m.group(2)
        sub = _sanitize_rp_location_sub(m.group(3) or "")
        if main_raw in _RP_INVALID_MAIN_LOCATIONS:
            main = _lookup_rp_row_main_location(row_num, pdf_field_by_pid, dd)
        else:
            main = _format_rp_report_main_location(main_raw)
        return main, sub
    m_loc = _RP_LOCATION_SUB_RE.match(s)
    if m_loc:
        sub = _sanitize_rp_location_sub(m_loc.group(2))
        main = _lookup_rp_row_main_location(m_loc.group(1), pdf_field_by_pid, dd)
        return main, sub
    if _is_rp_measurement_column_label(s) and not s.startswith("检测点位置_r"):
        return "", ""
    cleaned = _format_rp_report_main_location(_clean_rp_location_label(s))
    if _is_rp_measurement_column_label(cleaned):
        return "", ""
    return cleaned, ""


def _pdf_field_by_pid(pdf_fields: Optional[Sequence[Mapping[str, Any]]]) -> Dict[str, Mapping[str, Any]]:
    out: Dict[str, Mapping[str, Any]] = {}
    if not pdf_fields:
        return out
    for field in pdf_fields:
        if not isinstance(field, dict):
            continue
        pid = str(field.get("pdfFieldId") or "").strip()
        if pid:
            out[pid] = field
    return out


def _location_from_binding_measurement_field(
    binding: Mapping[str, Any],
    field_by_pid: Mapping[str, Mapping[str, Any]],
    dd: Optional[Mapping[str, Any]] = None,
) -> Tuple[str, str]:
    """从绑定行测量/报出格 field id 解析 (主位置, 子位置)。"""
    field = reading_anchor_field_for_binding(binding, field_by_pid)
    if not isinstance(field, dict):
        return "", ""
    fid = str(field.get("id") or field.get("placeholder") or "")
    if not fid or _is_rp_calibration_field(field):
        return "", ""
    if _RP_CONDITION_SLOT_ID_RE.fullmatch(_norm(fid)):
        return "", ""
    main, sub = _parse_rp_location_from_field_id(
        fid, pdf_field_by_pid=field_by_pid, dd=dd
    )
    return main, _sanitize_rp_location_sub(sub)


def _binding_measurement_semantic_key(
    binding: Mapping[str, Any],
    field_by_pid: Mapping[str, Mapping[str, Any]],
    *,
    group: int,
) -> str:
    """双组表跨页重复行：按 field id 主位置+rN+子位置去重。"""
    field = reading_anchor_field_for_binding(binding, field_by_pid)
    if not isinstance(field, dict):
        return ""
    fid = str(field.get("id") or field.get("placeholder") or "")
    m = _RP_FIELD_ROW_RE.match(fid)
    if not m:
        return ""
    main = _norm(m.group(1) or "")
    if main in _RP_INVALID_MAIN_LOCATIONS:
        return ""
    if any(marker in main for marker in _RP_NON_POINT_LOCATION_MARKERS):
        return ""
    row = m.group(2)
    sub = _sanitize_rp_location_sub(m.group(3) or "")
    return f"g{group}|{main}|r{row}|{sub}"


def _binding_is_valid_measurement_row(
    binding: Mapping[str, Any],
    field_by_pid: Mapping[str, Mapping[str, Any]],
) -> bool:
    """排除表前校准/条件槽（如 s2、kV）误绑为 report_d 的行。"""
    field = reading_anchor_field_for_binding(binding, field_by_pid)
    if not isinstance(field, dict):
        return False
    if _is_rp_calibration_field(field):
        return False
    fid = str(field.get("id") or field.get("placeholder") or "")
    if not fid or _RP_CONDITION_SLOT_ID_RE.fullmatch(_norm(fid)):
        return False
    if _RP_FIELD_ROW_RE.match(fid) or _RP_LOCATION_SUB_RE.match(fid):
        m = _RP_FIELD_ROW_RE.match(fid)
        if m and _norm(m.group(1) or "") in _RP_INVALID_MAIN_LOCATIONS:
            return False
        main_raw = _norm(m.group(1) or "")
        if any(marker in main_raw for marker in _RP_NON_POINT_LOCATION_MARKERS):
            return False
        if re.search(r"栏位\d+", fid):
            return False
        return True
    return False


def _resolve_binding_location(
    binding: Mapping[str, Any],
    pt_key: str,
    *,
    loc_index: Mapping[str, Tuple[str, str]],
    pdf_fields: Optional[Sequence[Mapping[str, Any]]] = None,
    pdf_field_by_pid: Optional[Mapping[str, Mapping[str, Any]]] = None,
    dd: Optional[Mapping[str, Any]] = None,
) -> Tuple[str, str]:
    """优先测量格 field id，再坐标索引与 radiationPoint。"""
    by_pid = pdf_field_by_pid if isinstance(pdf_field_by_pid, Mapping) else _pdf_field_by_pid(pdf_fields)
    main, sub = _location_from_binding_measurement_field(binding, by_pid, dd)
    if main or sub:
        return main, _sanitize_rp_location_sub(sub)
    row_main, row_sub = _resolve_binding_row_location(
        pt_key,
        loc_index,
        pdf_fields=pdf_fields,
        dd=dd,
    )
    if row_main:
        return row_main, _sanitize_rp_location_sub(row_sub)
    _loc_hint, _row_idx, sub_hint = _parse_radiation_point_key(pt_key)
    if sub_hint:
        sub_clean = _sanitize_rp_location_sub(sub_hint)
        if sub_clean:
            main_hint = _format_rp_report_main_location(_loc_hint) if _loc_hint else ""
            if main_hint:
                return main_hint, sub_clean
    by_pid = pdf_field_by_pid if isinstance(pdf_field_by_pid, Mapping) else _pdf_field_by_pid(pdf_fields)
    for key in _RP_BINDING_LOC_KEYS:
        pid = str(binding.get(key) or "").strip()
        if not pid:
            continue
        field = by_pid.get(pid)
        if not isinstance(field, dict):
            continue
        fid = str(field.get("id") or field.get("placeholder") or "")
        main, sub = _parse_rp_location_from_field_id(
            fid, pdf_field_by_pid=by_pid, dd=dd
        )
        if main or sub:
            return main, _sanitize_rp_location_sub(sub)
        if _is_rp_measurement_column_label(fid) and "检测点位置_r" not in fid:
            continue
    if row_main or row_sub:
        return row_main, _sanitize_rp_location_sub(row_sub)
    return "", ""


def _build_rp_row_location_index(
    pdf_fields: Sequence[Mapping[str, Any]],
    dd: Mapping[str, Any],
) -> Dict[str, Tuple[str, str]]:
    """行坐标键 rp_p{page}_y{y} → (主位置, 子位置)。"""
    if not pdf_fields:
        return {}
    layout = resolve_protection_table_layout(list(pdf_fields))
    rows: List[Tuple[int, float, str, str, str]] = []
    skip_labels = frozenset({"下划线", "状态", "检测条件", "检测项目", "检测点位置"})
    for field in pdf_fields:
        if str(field.get("templateSectionKey") or "") != CHAPTER_KEY:
            continue
        role = protection_field_column_role(field, layout)
        if role not in ("检测点位置", "位置细分"):
            continue
        row_key = _protection_row_coordinate_key(field)
        pid = str(field.get("pdfFieldId") or "").strip()
        val = _cell_value(dd, pid) if pid else ""
        fid = str(field.get("id") or field.get("placeholder") or "")
        label = _clean_rp_location_label(fid)
        text = _rp_location_display_text(val, label, field_id=fid)
        if not text or text in skip_labels:
            continue
        page = int(field.get("page") or 0)
        _x, y = _field_pdf_xy(field)
        rows.append((page, y, row_key, role, text))
    rows.sort(key=lambda t: (t[0], t[1]))
    out: Dict[str, Tuple[str, str]] = {}
    current_main = ""
    for _page, _y, row_key, role, text in rows:
        if role == "检测点位置":
            current_main = _format_rp_report_main_location(text)
            out[row_key] = (current_main, "")
        elif role == "位置细分":
            out[row_key] = (current_main, text)
    return out


def _nearest_rp_location_at_row(
    pdf_fields: Sequence[Mapping[str, Any]],
    dd: Mapping[str, Any],
    *,
    page: int,
    y: float,
    max_dy: float = 14.0,
) -> Tuple[str, str]:
    """按同行 y 最近邻匹配检测点位置/位置细分（弥补表头区无 forward-fill 的行）。"""
    if not pdf_fields:
        return "", ""
    layout = resolve_protection_table_layout(list(pdf_fields))
    skip_labels = frozenset({"下划线", "状态", "检测条件", "检测项目", "检测点位置"})
    best_main = ""
    best_sub = ""
    best_dist = max_dy + 1.0
    for field in pdf_fields:
        if str(field.get("templateSectionKey") or "") != CHAPTER_KEY:
            continue
        if int(field.get("page") or 0) != page:
            continue
        role = protection_field_column_role(field, layout)
        if role not in ("检测点位置", "位置细分"):
            continue
        _x, y0 = _field_pdf_xy(field)
        dist = abs(float(y0) - float(y))
        if dist > max_dy or dist >= best_dist:
            continue
        pid = str(field.get("pdfFieldId") or "").strip()
        val = _cell_value(dd, pid) if pid else ""
        fid = str(field.get("id") or field.get("placeholder") or "")
        label = _clean_rp_location_label(fid)
        text = _rp_location_display_text(val, label, field_id=fid)
        if not text or text in skip_labels:
            continue
        best_dist = dist
        if role == "位置细分":
            best_sub = text
        else:
            best_main = _format_rp_report_main_location(text)
            best_sub = ""
    return best_main, best_sub


def _condition_from_payload_scan(payload: Mapping[str, Any], *, group: int) -> str:
    """从提交 JSON 扫描整段检测条件文案（模板栏位不可用时的兜底）。"""
    if group == 1:
        mock = _norm(payload.get("_mockRpConditionLine") or "")
        if mock:
            return _format_condition_text(mock)
        for bucket in ("dynamicData", "testResult"):
            node = payload.get(bucket)
            if isinstance(node, dict):
                mock = _norm(node.get("_mockRpConditionLine") or "")
                if mock:
                    return _format_condition_text(mock)
    if group == 2:
        for bucket in ("dynamicData", "testResult"):
            node = payload.get(bucket) if isinstance(payload.get(bucket), dict) else {}
            if isinstance(node, dict):
                mock2 = _norm(node.get("_mockRpConditionLine2") or "")
                if mock2:
                    return _format_condition_text(mock2)
        mock = _norm(payload.get("_mockRpConditionLine") or "")
        if mock and "②" in mock:
            return _format_condition_text(mock)
        for bucket in ("dynamicData", "testResult"):
            node = payload.get(bucket)
            if isinstance(node, dict):
                mock = _norm(node.get("_mockRpConditionLine") or "")
                if mock and "②" in mock:
                    return _format_condition_text(mock)
        for bucket in ("dynamicData", "testResult"):
            node = payload.get(bucket)
            if isinstance(node, dict):
                mock = _norm(node.get("_mockRpConditionLine") or "")
                if mock and len(mock) > 20:
                    return _format_condition_text(mock)
    matches: List[str] = []
    for bucket in ("dynamicData", "testResult"):
        node = payload.get(bucket)
        if not isinstance(node, dict):
            continue
        for key, val in node.items():
            if not isinstance(val, str):
                continue
            text = val.strip()
            if not text or len(text) < 6:
                continue
            key_s = str(key)
            is_g2 = any(m in text or m in key_s for m in _GROUP2_CONDITION_MARKERS)
            if group == 2:
                if not is_g2 and not (
                    "检测条件" in text and ("②" in text or "2" in key_s or "第二" in text)
                ):
                    continue
            elif is_g2:
                continue
            if (
                "检测条件" in text
                or "散射模体" in text
                or ("螺旋" in text and "kV" in text)
                or ("kV" in text and ("mA" in text or "mAs" in text))
            ):
                matches.append(text)
    if not matches:
        return ""
    raw = matches[0] if group == 1 else matches[-1]
    return _format_condition_text(raw)


def _conditions_from_payload(
    payload: Mapping[str, Any],
    site_template_parsed: Optional[Mapping[str, Any]] = None,
    *,
    extracted_slots: Optional[Mapping[str, str]] = None,
) -> Tuple[str, str]:
    g1, g2 = _conditions_from_template_fields(
        payload,
        site_template_parsed,
        extracted_slots=extracted_slots,
    )
    return _format_condition_text(g1), _format_condition_text(g2)


def _condition_from_payload(
    payload: Mapping[str, Any],
    site_template_parsed: Optional[Mapping[str, Any]] = None,
) -> str:
    """
    从现场提交 JSON 拼装报告表首行「检测条件」。
    优先读取已落库的整段条件文案；勿用第五章测量读数字段（如 f210/f216）误拼。
    """
    cond, _cond2 = _conditions_from_payload(payload, site_template_parsed)
    return cond


def _chapter_indicates_dual_group(chapter: Optional[Mapping[str, Any]]) -> bool:
    if not isinstance(chapter, dict):
        return False
    mf = chapter.get("meanFormula")
    if isinstance(mf, dict):
        if str(mf.get("mode") or "") == "per_row_avg_dual":
            return True
        try:
            if int(mf.get("groups") or 1) >= 2:
                return True
        except (TypeError, ValueError):
            pass
    return _report_rules_indicate_dual_group(chapter)


def _bindings_have_dual_group_layout(bindings: Sequence[Mapping[str, Any]]) -> bool:
    return sum(
        1 for b in bindings if isinstance(b, dict) and _binding_has_dual_group(b)
    ) >= 3


def _detect_dual_group_report(
    bindings: Sequence[Mapping[str, Any]],
    dd: Mapping[str, Any],
    chapter: Optional[Mapping[str, Any]],
) -> bool:
    if not _chapter_indicates_dual_group(chapter) and not _bindings_have_dual_group_layout(bindings):
        return False
    active_g2 = sum(
        1
        for b in bindings
        if isinstance(b, dict)
        and not _is_background_binding(b)
        and _row_is_active_group(b, dd, group=2)
    )
    return active_g2 >= 1


def _parse_rp_row_coordinate(pt_key: str) -> Tuple[Optional[int], Optional[float]]:
    m = re.match(r"^rp_p(\d+)_y([\d.]+)$", _norm(pt_key), re.I)
    if not m:
        return None, None
    try:
        return int(m.group(1)), float(m.group(2))
    except (TypeError, ValueError):
        return None, None


def _resolve_binding_row_location(
    pt_key: str,
    loc_index: Mapping[str, Tuple[str, str]],
    *,
    pdf_fields: Optional[Sequence[Mapping[str, Any]]] = None,
    dd: Optional[Mapping[str, Any]] = None,
) -> Tuple[str, str]:
    if pt_key in loc_index:
        return loc_index[pt_key]
    page, y = _parse_rp_row_coordinate(pt_key)
    if page is None or y is None:
        return "", ""
    best: Tuple[str, str] = ("", "")
    best_y = -1.0
    for rk, pair in loc_index.items():
        pg, y0 = _parse_rp_row_coordinate(rk)
        if pg != page or y0 is None or y0 > y + 1.5:
            continue
        if y0 >= best_y:
            best_y = y0
            best = pair
    if best[0] or best[1]:
        return best
    if pdf_fields and dd is not None:
        return _nearest_rp_location_at_row(pdf_fields, dd, page=page, y=y)
    return "", ""


def _binding_is_table_data_row(pt_key: str) -> bool:
    """排除表前校准因子/条件区（与 chapter5 数据行 cutoff 一致）。"""
    page, y = _parse_rp_row_coordinate(pt_key)
    if page is None or y is None:
        return True
    if page == 5 and y < 360.0:
        return False
    return True


def _build_points_from_bindings_group(
    bindings: Sequence[Mapping[str, Any]],
    dd: Mapping[str, Any],
    *,
    group: int,
    row_locations: Optional[Mapping[str, Tuple[str, str]]] = None,
    pdf_fields: Optional[Sequence[Mapping[str, Any]]] = None,
    include_annual_dose: bool = False,
    payload: Optional[Mapping[str, Any]] = None,
    chapter: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """按 binding 顺序生成检测点，保留主位置/子位置 complex 层级。"""
    loc_index = row_locations if isinstance(row_locations, Mapping) else {}
    field_by_pid = _pdf_field_by_pid(pdf_fields)
    flat_rows: List[Dict[str, Any]] = []
    seen_semantic: set[str] = set()
    seq_counter = 1

    ordered = sorted(
        (b for b in bindings if isinstance(b, dict)),
        key=_binding_row_sort_key,
    )

    for binding in ordered:
        if _is_background_binding(binding):
            continue
        if not _row_is_active_group(
            binding,
            dd,
            group=group,
            payload=payload,
            pdf_field_by_pid=field_by_pid,
            chapter=chapter,
        ):
            continue
        pt_key = _norm(binding.get("radiationPoint") or "")
        if not pt_key or not _binding_is_valid_measurement_row(binding, field_by_pid):
            continue
        sem_key = _binding_measurement_semantic_key(binding, field_by_pid, group=group)
        if sem_key:
            if sem_key in seen_semantic:
                continue
            seen_semantic.add(sem_key)
        main_loc, sub_loc = _resolve_binding_location(
            binding,
            pt_key,
            loc_index=loc_index,
            pdf_fields=pdf_fields,
            pdf_field_by_pid=field_by_pid,
            dd=dd,
        )
        if _is_invalid_rp_location_token(main_loc) and _is_invalid_rp_location_token(sub_loc):
            continue
        if str(main_loc).startswith("rp_p") or str(sub_loc).startswith("rp_p"):
            continue
        result = _result_from_binding_group(
            binding,
            dd,
            group=group,
            payload=payload,
            pdf_field_by_pid=field_by_pid,
            chapter=chapter,
        )
        if not result or result == "/":
            continue
        annual = _annual_dose_from_binding(binding, dd) if include_annual_dose else ""
        point_id = _normalize_report_seq_id(_seq_id_from_binding(binding, dd))
        if not point_id:
            point_id = str(seq_counter)
        seq_counter += 1
        flat_rows.append(
            _make_binding_data_row(
                main_loc=main_loc,
                sub_loc=sub_loc,
                result=result,
                annual_dose_msv=annual,
                point_id=point_id,
            )
        )
    return _filter_points_with_results(_group_binding_rows_into_complex_points(flat_rows))


def _condition_line_has_kv(text: str) -> bool:
    return bool(re.search(r"\d+(?:\.\d+)?\s*kV", _norm(text), re.I))


def build_report_data_from_bindings(
    payload: Mapping[str, Any],
    bindings: Sequence[Mapping[str, Any]],
    *,
    condition: str = "",
    condition_group2: str = "",
    background_value: str = "",
    notes: Optional[Sequence[str]] = None,
    chapter: Optional[Mapping[str, Any]] = None,
    dual_group: Optional[bool] = None,
    site_template_parsed: Optional[Mapping[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """按 fieldBindings + dynamicData 生成 report_data。"""
    if not isinstance(payload, dict) or not bindings:
        return None
    dd = _dynamic_data(payload)
    if dual_group is None:
        dual_group = _detect_dual_group_report(bindings, dd, chapter)

    row_locations = _build_rp_row_location_index(
        _protection_pdf_fields_from_template(site_template_parsed),
        dd,
    )
    pdf_fields = _protection_pdf_fields_from_template(site_template_parsed)
    include_annual = bool(dual_group)
    points = _build_points_from_bindings_group(
        bindings,
        dd,
        group=1,
        row_locations=row_locations,
        pdf_fields=pdf_fields,
        include_annual_dose=include_annual,
        payload=payload,
        chapter=chapter,
    )
    points_group2: List[Dict[str, Any]] = []
    if dual_group:
        points_group2 = _build_points_from_bindings_group(
            bindings,
            dd,
            group=2,
            row_locations=row_locations,
            pdf_fields=pdf_fields,
            include_annual_dose=include_annual,
            payload=payload,
            chapter=chapter,
        )
    else:
        pass  # 单组表亦保留 complex 主/子位置层级，不再展平为整格 simple

    if not points and not points_group2:
        return None
    if not points:
        points = points_group2
        points_group2 = []
        dual_group = False

    out: Dict[str, Any] = {
        "condition": condition or "",
        "points": _finalize_report_point_ids(points),
        "background": {"label": "本底值（μSv/h）", "value": background_value or ""},
        "notes": list(notes) if notes else list(_DEFAULT_NOTES),
    }
    if dual_group and points_group2:
        out["condition_group2"] = condition_group2 or ""
        out["points_group2"] = _finalize_report_point_ids(points_group2)
        out["include_annual_dose_column"] = True
    return _ensure_annual_dose_note(apply_report_evaluations(out))


def _normalize_report_seq_id(seq_val: Any) -> str:
    """报告表序号：单行整数；拒绝范围编号、栏位占位等。"""
    s = _norm(seq_val)
    if not s or s == "/":
        return ""
    if "栏位" in s:
        return ""
    compact = re.sub(r"\s+", "", s)
    if "~" in compact or "～" in compact:
        return ""
    if re.fullmatch(r"\d+\s*[-–—]\s*\d+", s):
        return ""
    try:
        n = float(s)
        if abs(n - round(n)) > 1e-9:
            return ""
        if int(round(n)) < 1 or int(round(n)) > 500:
            return ""
    except (TypeError, ValueError):
        if not re.fullmatch(r"\d+", compact):
            return ""
        return compact
    return str(int(round(n)))


def _finalize_report_point_ids(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """报告表：每行独立整数序号；拒绝现场 n~m 范围（已在构建时用表顺序补全）。"""
    for pt in points:
        if not isinstance(pt, dict):
            continue
        if pt.get("type") == "complex":
            pt.pop("id", None)
            for sub in pt.get("sub_rows") or []:
                if not isinstance(sub, dict):
                    continue
                sid = _normalize_report_seq_id(sub.get("id") or sub.get("point_id") or "")
                sub["id"] = sid
        else:
            sid = _normalize_report_seq_id(pt.get("id") or pt.get("point_id") or "")
            if sid:
                pt["id"] = sid
            else:
                pt.pop("id", None)
    return points


def _preserve_report_point_ids(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return _finalize_report_point_ids(points)


def _renumber_report_point_ids(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """兼容别名：不再连续重编号，仅规范化现场序号。"""
    return _finalize_report_point_ids(points)


def _normalize_background_range_display(text: str) -> str:
    """规范本底报出值展示（0.087~0.092）；忽略公式原文。"""
    s = _norm(text)
    if not s:
        return ""
    if "min(" in s.lower() or "max(" in s.lower():
        return ""
    s = s.replace("～", "~")
    if "~" in s:
        lo_s, _, hi_s = s.partition("~")
        lo = _parse_float_token(lo_s)
        hi = _parse_float_token(hi_s)
        if lo is not None and hi is not None:
            return _format_background_range(lo, hi)
        return s.strip()
    n = _parse_float_token(s)
    if n is not None and re.fullmatch(r"[-+]?\d*\.?\d+", s.replace(" ", "")):
        return f"{n:g}"
    return s


def _resolve_background_binding(
    bindings: Sequence[Mapping[str, Any]],
    *,
    chapter: Optional[Mapping[str, Any]] = None,
    site_template_parsed: Optional[Mapping[str, Any]] = None,
    payload: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """第五章本底行 binding：优先章节 backgroundBinding，其次按表结构解析。"""
    if isinstance(chapter, dict):
        saved = chapter.get("backgroundBinding")
        if isinstance(saved, dict) and _norm(saved.get("report_d") or saved.get("kind") or ""):
            return dict(saved)
    pdf_fields = _protection_pdf_fields_from_template(site_template_parsed)
    built = resolve_background_row_binding(
        pdf_fields,
        chapter=chapter,
        template_payload=payload,
    )
    if built:
        return built
    for binding in bindings:
        if isinstance(binding, dict) and _is_background_binding(binding):
            row = dict(binding)
            row.setdefault("kind", "background")
            return row
    return {}


def _background_reading_numbers_from_binding(
    payload: Mapping[str, Any],
    bg_binding: Mapping[str, Any],
) -> List[float]:
    """由第五章本底行 binding 的 bg_reading_1..10 槽位取读数。"""
    dd = _dynamic_data(payload)
    tr = _payload_bucket(payload, "testResult")
    slots: List[float] = []
    for i in range(1, 11):
        pid = _norm(bg_binding.get(f"bg_reading_{i}") or bg_binding.get(f"reading_{i}") or "")
        if not pid:
            continue
        num = None
        for src in (dd, tr):
            num = _parse_background_reading_token(_cell_value(src, pid))
            if num is not None:
                break
        if num is not None and _is_plausible_background_reading(num):
            slots.append(num)
    return slots


def _background_calibration_factor_from_binding(
    payload: Mapping[str, Any],
    bg_binding: Mapping[str, Any],
    *,
    chapter: Optional[Mapping[str, Any]] = None,
    site_template_parsed: Optional[Mapping[str, Any]] = None,
) -> float:
    pid = _norm(bg_binding.get("calibration_factor") or "")
    if not pid:
        pid = _background_calibration_factor_pid(chapter, site_template_parsed)
    factor = _parse_float_token(_value_from_payload(payload, pid)) if pid else None
    if factor is None or not (0 < abs(factor) <= 3.5):
        return 1.0
    return factor


def _is_plausible_background_reading(num: float) -> bool:
    """本底读数合理区间（μSv/h 量级）；排除误填的质控/读数大值。"""
    return 0.0 < num <= 2.5


def _background_calibration_factor_pid(
    chapter: Optional[Mapping[str, Any]],
    site_template_parsed: Optional[Mapping[str, Any]],
) -> str:
    from radiation_detection_report.chapter5_field_sync import resolve_background_calibration_factor_pid

    return resolve_background_calibration_factor_pid(
        chapter,
        _protection_pdf_fields_from_template(site_template_parsed),
    )


def _background_range_from_readings(
    payload: Mapping[str, Any],
    bg_binding: Mapping[str, Any],
    *,
    chapter: Optional[Mapping[str, Any]] = None,
    site_template_parsed: Optional[Mapping[str, Any]] = None,
) -> str:
    """由第五章本底行 binding 的 ①–⑩ 读数 × 校准因子得到报出值范围。"""
    slots = _background_reading_numbers_from_binding(payload, bg_binding)
    if len(slots) < 2:
        return ""
    factor = _background_calibration_factor_from_binding(
        payload,
        bg_binding,
        chapter=chapter,
        site_template_parsed=site_template_parsed,
    )
    scaled = [n * factor for n in slots]
    return _format_background_range(min(scaled), max(scaled))


def _background_stored_range_from_payload(
    payload: Mapping[str, Any],
    bindings: Sequence[Mapping[str, Any]],
    *,
    site_template_parsed: Optional[Mapping[str, Any]] = None,
    chapter: Optional[Mapping[str, Any]] = None,
) -> str:
    """现场记录本底行 binding.report_d 槽位已填范围时直接使用。"""
    def _accept_range(raw: Any) -> str:
        s = _norm(raw)
        if not s or s == "/":
            return ""
        if "min(" in s.lower() or "max(" in s.lower():
            return ""
        compact = s.replace("～", "~")
        if "~" not in compact:
            return ""
        lo = _parse_float_token(compact.split("~", 1)[0])
        hi = _parse_float_token(compact.split("~", 1)[1])
        if lo is None or hi is None:
            return compact.strip()
        return compact.strip()

    bg_binding = _resolve_background_binding(
        bindings,
        chapter=chapter,
        site_template_parsed=site_template_parsed,
        payload=payload,
    )
    report_pid = _norm(bg_binding.get("report_d") or "")
    if report_pid:
        stored = _accept_range(_value_from_payload(payload, report_pid))
        if stored:
            return stored

    return ""


def _background_from_payload(
    payload: Mapping[str, Any],
    bindings: Sequence[Mapping[str, Any]],
    *,
    site_template_parsed: Optional[Mapping[str, Any]] = None,
    chapter: Optional[Mapping[str, Any]] = None,
) -> str:
    """
    本底水平报出值（μSv/h 范围）。
    按第五章 backgroundBinding 表结构：优先 report_d 槽位已填范围，其次 bg_reading_* 读数计算。
    """
    bg_binding = _resolve_background_binding(
        bindings,
        chapter=chapter,
        site_template_parsed=site_template_parsed,
        payload=payload,
    )
    if not bg_binding:
        return ""

    stored = _background_stored_range_from_payload(
        payload,
        bindings,
        site_template_parsed=site_template_parsed,
        chapter=chapter,
    )
    if stored:
        return stored

    computed = _background_range_from_readings(
        payload,
        bg_binding,
        site_template_parsed=site_template_parsed,
        chapter=chapter,
    )
    if computed:
        return computed

    report_pid = _norm(bg_binding.get("report_d") or "")
    if report_pid and payload:
        pdf_fields = _protection_pdf_fields_from_template(site_template_parsed)
        field_by_pid = _pdf_field_by_pid(pdf_fields)
        field = field_by_pid.get(report_pid.lower()) if field_by_pid else None
        if isinstance(field, dict):
            evaluated = _computed_report_value_from_field(
                payload,
                report_pid,
                field_by_pid,
                chapter=chapter,
            )
            evaluated = _normalize_background_range_display(evaluated)
            if evaluated and "~" in evaluated.replace("～", "~"):
                return evaluated

    return ""


def _background_value_should_prefer_candidate(current: str, candidate: str) -> bool:
    cur = _norm(current)
    cand = _norm(candidate)
    if not cand:
        return False
    if not cur:
        return True
    cand_norm = cand.replace("～", "~")
    cur_norm = cur.replace("～", "~")
    if "~" in cand_norm and "~" not in cur_norm:
        return True
    return False


def _condition_from_extracted(extracted: Mapping[str, Any]) -> str:
    slots = _condition_slots_from_extracted_preface(extracted)
    if slots:
        template_line = _compose_rp_condition_template_line(slots)
        if template_line:
            return template_line
        composed = _compose_rp_scan_condition(slots)
        if composed:
            return composed
    preface = extracted.get("preface")
    if isinstance(preface, dict):
        cond = preface.get("condition")
        if isinstance(cond, dict):
            text = _norm(cond.get("text") or "")
            if text and re.search(r"曝光模式\s*（部位", text):
                if not text.startswith("检测条件"):
                    text = f"检测条件：{text.lstrip('：:')}"
                return text
            if text and not re.search(r"曝光模式\s*（部位", text):
                if not text.startswith("检测条件"):
                    text = f"检测条件：{text}"
                return text
    return ""


def _condition_is_low_quality(text: str) -> bool:
    """判断是否为误拼的校准因子串（如「1.09 180 0.87 0.86」）。"""
    body = _norm(text)
    if not body:
        return True
    body = re.sub(r"^检测条件[：:]\s*", "", body).rstrip("。")
    tokens = [t for t in re.split(r"[\s,，;；]+", body) if t]
    if not tokens:
        return True
    nums = [_parse_float_token(t) for t in tokens]
    nums = [n for n in nums if n is not None]
    if len(nums) >= 3 and len(nums) == len(tokens):
        if sum(1 for n in nums if 0 < n <= 3.5) >= 2:
            return True
    return False


def build_report_data_from_extracted(extracted: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """将 extract_chapter5_full / extract_js009_chapter5 输出转为 report_data。"""
    if not isinstance(extracted, dict):
        return None
    raw_rows = extracted.get("raw_rows")
    if not isinstance(raw_rows, list):
        return None

    groups: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    def _flush() -> None:
        nonlocal current
        if current:
            groups.append(current)
            current = None

    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        kind = row.get("kind")
        if kind == "simple":
            _flush()
            readings = row.get("readings") or []
            if isinstance(readings, list) and any(str(x).strip() == "/" for x in readings):
                continue
            if str(row.get("report_d") or "").strip() == "/":
                continue
            result = _norm(row.get("report_d") or "")
            if not result:
                continue
            groups.append(
                {
                    "type": "simple",
                    "id": _normalize_report_seq_id(row.get("point_id") or ""),
                    "location": _norm(row.get("location") or ""),
                    "result": result,
                    "standard": DEFAULT_RADIATION_STANDARD,
                    "evaluation": "",
                }
            )
        elif kind == "complex_sub":
            main = _norm(row.get("location_main") or "")
            sub = _norm(row.get("location_sub") or "")
            result = _norm(row.get("report_d") or "")
            if main:
                _flush()
                current = {
                    "type": "complex",
                    "location": main,
                    "sub_rows": [],
                }
            if current is None:
                continue
            readings = row.get("readings") or []
            if isinstance(readings, list) and any(str(x).strip() == "/" for x in readings):
                continue
            if str(row.get("report_d") or "").strip() == "/":
                continue
            if not result:
                continue
            current["sub_rows"].append(
                {
                    "id": _normalize_report_seq_id(row.get("point_id") or ""),
                    "location_sub": sub,
                    "result": result,
                    "standard": DEFAULT_RADIATION_STANDARD,
                    "evaluation": "",
                }
            )
        elif kind in ("header", "notes", "background", "other", "empty"):
            _flush()

    _flush()

    points = _filter_points_with_results(
        [g for g in groups if g.get("type") in ("simple", "complex")]
    )
    if not points:
        return None

    bg_value = ""
    notes: List[str] = list(_DEFAULT_NOTES)
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        if row.get("kind") == "background":
            slots = row.get("slots") or []
            vals: List[str] = []
            if isinstance(slots, list):
                for slot in slots:
                    if isinstance(slot, dict):
                        v = _norm(slot.get("label") or "")
                        if v and "本底" not in v:
                            vals.append(v)
            if vals:
                nums = []
                for v in vals:
                    m = re.search(r"[-+]?\d*\.?\d+", v)
                    if m:
                        try:
                            nums.append(float(m.group(0)))
                        except ValueError:
                            pass
                if nums:
                    lo, hi = min(nums), max(nums)
                    bg_value = f"{lo:g}" if abs(lo - hi) < 1e-9 else f"{lo:g}~{hi:g}"
        elif row.get("kind") == "notes":
            t = _norm(row.get("text") or "")
            if t:
                notes = [t]

    out = {
        "condition": _condition_from_extracted(extracted),
        "points": _finalize_report_point_ids(points),
        "background": {"label": "本底值（μSv/h）", "value": bg_value},
        "notes": notes,
    }
    return _ensure_annual_dose_note(apply_report_evaluations(out))


def _enrich_report_data_from_site_extract(
    data: Dict[str, Any],
    *,
    site_pdf_path: Optional[str] = None,
) -> Dict[str, Any]:
    """用现场记录 PDF 提取结果补全检测条件、注释等表头/表尾字段。"""
    if not site_pdf_path:
        return data
    try:
        from radiation_detection_report.extract_js009_chapter5 import extract_js009_chapter5

        extracted = extract_js009_chapter5(site_pdf_path)
    except Exception:
        return data
    if not isinstance(extracted, dict):
        return data
    out = dict(data)
    payload_cond = _norm(out.get("condition"))
    extracted_cond = _norm(_condition_from_extracted(extracted))
    if extracted_cond and (
        not payload_cond
        or _condition_is_low_quality(payload_cond)
    ):
        out["condition"] = _condition_from_extracted(extracted)

    built = build_report_data_from_extracted(extracted)
    if not isinstance(built, dict):
        return out
    built_cond = _norm(built.get("condition"))
    payload_cond = _norm(out.get("condition"))
    if built_cond and (
        not payload_cond
        or _condition_is_low_quality(payload_cond)
    ):
        out["condition"] = built["condition"]
    elif not payload_cond and built_cond:
        out["condition"] = built["condition"]
    bg = out.get("background") if isinstance(out.get("background"), dict) else {}
    built_bg = built.get("background") if isinstance(built.get("background"), dict) else {}
    if not _norm(bg.get("value")) and _norm(built_bg.get("value")):
        out["background"] = {
            "label": bg.get("label") or built_bg.get("label") or "本底值（μSv/h）",
            "value": built_bg.get("value") or "",
        }
    elif _background_value_should_prefer_candidate(
        _norm(bg.get("value")), _norm(built_bg.get("value"))
    ):
        out["background"] = {
            "label": bg.get("label") or built_bg.get("label") or "本底值（μSv/h）",
            "value": built_bg.get("value") or "",
        }
    if built.get("notes") and (not out.get("notes") or out.get("notes") == list(_DEFAULT_NOTES)):
        out["notes"] = list(built.get("notes") or [])
    return out


def _truthy_submit_flag(raw: Any) -> bool:
    if raw is True:
        return True
    if raw is False or raw is None:
        return False
    return str(raw).strip().lower() in ("true", "1", "yes", "是", "y")


def _submit_bucket_flag(payload: Mapping[str, Any], *keys: str) -> bool:
    for bucket_name in ("dynamicData", "testResult", "hospitalInfo"):
        bucket = payload.get(bucket_name)
        if not isinstance(bucket, dict):
            continue
        for key in keys:
            if _truthy_submit_flag(bucket.get(key)):
                return True
    return False


def submit_inspection_scope_includes_radiation_protection(
    payload: Optional[Mapping[str, Any]],
) -> Optional[bool]:
    """
  现场记录首页「检测范围」：
  - f502：质控 + 工作场所放射防护
  - f503：仅质控（性能）→ 不含防护
  - f504：仅工作场所放射防护
  均未勾选时返回 None，由有效防护点位数据决定。
    """
    if not isinstance(payload, dict):
        return None
    both = _submit_bucket_flag(
        payload,
        "f502",
        "检测范围_质量控制（性能）检测工作场所放射检测",
    )
    qc_only = _submit_bucket_flag(
        payload,
        "f503",
        "检测范围_仅质量控制（性能）检测",
    )
    rp_only = _submit_bucket_flag(
        payload,
        "f504",
        "检测范围_仅工作场所放射防护检测",
    )
    if both:
        return True
    if rp_only:
        return True
    if qc_only:
        return False
    return None


def report_data_has_radiation_points(report_data: Optional[Mapping[str, Any]]) -> bool:
    return bool(report_data and report_data.get("points"))


def resolve_has_radiation_protection_from_submit(
    payload: Optional[Mapping[str, Any]],
    report_data: Optional[Mapping[str, Any]],
) -> bool:
    """检测范围 + 有效第五章点位：决定是否插入防护表/平面图及封面防护用语。"""
    scope = submit_inspection_scope_includes_radiation_protection(payload)
    has_points = report_data_has_radiation_points(report_data)
    if scope is False:
        return False
    if scope is True:
        return has_points
    return has_points


def try_build_report_data(
    payload: Optional[Mapping[str, Any]],
    *,
    site_template_parsed: Optional[Mapping[str, Any]] = None,
    site_pdf_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    优先从检测提交 + fieldBindings 构建；若无有效点位则尝试现场记录 PDF 提取。
    """
    if (
        isinstance(payload, dict)
        and submit_inspection_scope_includes_radiation_protection(payload) is False
    ):
        return None
    bindings: List[Dict[str, Any]] = []
    chapter = None
    pdf_fields: List[Any] = []
    if isinstance(site_template_parsed, dict):
        form_schema = site_template_parsed.get("formSchema") or site_template_parsed.get("form_schema")
        if isinstance(form_schema, dict):
            chapter = form_schema.get(SCHEMA_KEY) or form_schema.get("radiationProtectionChapter")
        chapter = chapter or site_template_parsed.get(SCHEMA_KEY) or site_template_parsed.get("radiationProtectionChapter")
        pdf_fields = _protection_pdf_fields_from_template(site_template_parsed)
    if isinstance(payload, dict):
        chapter = chapter or payload.get(SCHEMA_KEY)
        bindings = resolve_chapter_field_bindings(chapter, pdf_fields=pdf_fields or None, export_payload=payload)

    if isinstance(payload, dict) and bindings:
        bg = _background_from_payload(
            payload,
            bindings,
            site_template_parsed=site_template_parsed,
            chapter=chapter,
        )
        extracted_slots: Optional[Dict[str, str]] = None
        if site_pdf_path:
            try:
                import fitz

                from radiation_detection_report.extract_tabletest_chapter5 import (
                    extract_condition_slots_from_page,
                    find_chapter5_bounds,
                )

                start_page, _ = find_chapter5_bounds(site_pdf_path)
                doc = fitz.open(site_pdf_path)
                extracted_slots = _sanitize_extracted_condition_slots(
                    extract_condition_slots_from_page(doc[start_page - 1])
                )
                doc.close()
            except Exception:
                extracted_slots = None
        cond, cond2 = _conditions_from_payload(
            payload,
            site_template_parsed,
            extracted_slots=extracted_slots,
        )
        data = build_report_data_from_bindings(
            payload,
            bindings,
            condition=cond,
            condition_group2=cond2,
            background_value=bg,
            chapter=chapter,
            site_template_parsed=site_template_parsed,
        )
        if data and data.get("points"):
            return _ensure_annual_dose_note(
                apply_report_evaluations(
                    _enrich_report_data_from_site_extract(data, site_pdf_path=site_pdf_path)
                )
            )

    if site_pdf_path:
        try:
            from radiation_detection_report.extract_js009_chapter5 import extract_js009_chapter5

            extracted = extract_js009_chapter5(site_pdf_path)
            data = extracted.get("report_data") if isinstance(extracted, dict) else None
            if isinstance(data, dict) and data.get("points"):
                first_loc = ""
                pts0 = data.get("points") or []
                if pts0 and isinstance(pts0[0], dict):
                    first_loc = str(pts0[0].get("location") or "")
                if first_loc in ("检测项目", "检测点位置", "检测条件"):
                    data = None
            if isinstance(data, dict) and data.get("points"):
                return _ensure_annual_dose_note(apply_report_evaluations(data))
            built = build_report_data_from_extracted(extracted)
            if built and built.get("points"):
                return _ensure_annual_dose_note(built)
        except Exception:
            pass
    return None
