"""
从现场记录 PDF 或检测提交 JSON 构建报告表输入 report_data。
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from radiation_detection_report.chapter5_field_sync import (
    SCHEMA_KEY,
    _binding_has_dual_group,
    _report_rules_indicate_dual_group,
    resolve_chapter_field_bindings,
)
from radiation_detection_report.report_evaluation import (
    DEFAULT_RADIATION_STANDARD,
    apply_report_evaluations,
)

_POINT_KEY_RE = re.compile(r"^(?P<location>.+?)_r(?P<row_idx>\d+)(?:_(?P<sub>.+))?$", re.I)
_DEFAULT_NOTES = [
    "1. 上表中检测结果未扣除本底值。",
    "2. 检测结果已按响应时间修正系数修正。",
]


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip())


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

_REPORT_RESULT_KEYS = ("report_d", "report_d_2", "mean_m", "mean_m_2")
_GROUP1_RESULT_KEYS = ("report_d", "mean_m")
_GROUP2_RESULT_KEYS = ("report_d_2", "mean_m_2")
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
    "seq_no",
)
_CONDITION_FIELD_SKIP_RE = re.compile(r"仪器|校准因子|能量档|137Cs|Cs校准")
_CONDITION_FIELD_INCLUDE_RE = re.compile(
    r"检测条件|kV|mA|mAs|下划线|球管|曝光模式|^s2?$",
    re.I,
)
_GROUP2_CONDITION_MARKERS = (
    "检测条件_2",
    "检测条件_曝光模式2",
    "检测条件_球管朝向",
    "下划线2",
    "下划线3",
    "下划线4",
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
) -> bool:
    keys = _GROUP2_MEASUREMENT_KEYS if group == 2 else _GROUP1_MEASUREMENT_KEYS
    for key in keys:
        pid = str(binding.get(key) or "").strip()
        if not pid:
            continue
        raw = _raw_cell(dd, pid)
        if str(raw).strip() == "/":
            if key == "seq_no":
                continue
            return True
    return False


def _result_from_binding_group(
    binding: Mapping[str, Any],
    dd: Mapping[str, Any],
    *,
    group: int,
) -> str:
    keys = _GROUP2_RESULT_KEYS if group == 2 else _GROUP1_RESULT_KEYS
    for key in keys:
        v = _cell_value(dd, str(binding.get(key) or ""))
        if v:
            return v
    return ""


def _row_is_active_group(binding: Mapping[str, Any], dd: Mapping[str, Any], *, group: int) -> bool:
    if _binding_is_slash_skipped_group(binding, dd, group=group):
        return False
    return bool(_result_from_binding_group(binding, dd, group=group))


def _row_is_active(binding: Mapping[str, Any], dd: Mapping[str, Any]) -> bool:
    """报告表仅保留有报出值/均值的行，序号或读数 alone 不算有效行。"""
    if _binding_is_slash_skipped(binding, dd):
        return False
    return bool(_result_from_binding(binding, dd))


def _parse_radiation_point_key(key: str) -> Tuple[str, Optional[int], Optional[str]]:
    k = _norm(key)
    m = _POINT_KEY_RE.match(k)
    if not m:
        return k, None, None
    loc = _norm(m.group("location"))
    try:
        row_idx = int(m.group("row_idx"))
    except (TypeError, ValueError):
        row_idx = None
    sub = _norm(m.group("sub") or "") or None
    return loc, row_idx, sub



def _result_from_binding(binding: Mapping[str, Any], dd: Mapping[str, Any]) -> str:
    """报告表检测结果列：报出值优先，无报出值栏位时回退均值；不用原始读数。"""
    for key in _REPORT_RESULT_KEYS:
        v = _cell_value(dd, str(binding.get(key) or ""))
        if v:
            return v
    return ""


def _point_has_report_result(point: Mapping[str, Any]) -> bool:
    if point.get("type") == "complex":
        subs = point.get("sub_rows") or []
        return any(_norm(sub.get("result") or "") for sub in subs if isinstance(sub, dict))
    return bool(_norm(point.get("result") or ""))


def _filter_points_with_results(points: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """去掉无报出值/均值的检测点行。"""
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


def _parse_float_token(text: str) -> Optional[float]:
    m = re.search(r"[-+]?\d*\.?\d+", str(text or "").replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


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


def _is_radiation_condition_field(field_id: str) -> bool:
    fid = _norm(field_id)
    if not fid or _CONDITION_FIELD_SKIP_RE.search(fid):
        return False
    if any(x in fid for x in ("测量读数", "测量均值", "报出值", "序号_r", "检测点位置", "备注", "本底")):
        return False
    return bool(_CONDITION_FIELD_INCLUDE_RE.search(fid))


_GROUP2_CONDITION_FIELD_RE = re.compile(
    r"检测条件_2|检测条件_曝光模式2|检测条件_球管朝向|下划线[234]|^kV2$|^s2$|^mA4$",
    re.I,
)


def _condition_field_group(field_id: str) -> Optional[int]:
    fid = _norm(field_id)
    if not fid or _CONDITION_FIELD_SKIP_RE.search(fid):
        return None
    if not _is_radiation_condition_field(fid):
        return None
    if _GROUP2_CONDITION_FIELD_RE.search(fid):
        return 2
    if "检测条件" in fid and "2" in fid and "①" not in fid:
        return 2
    return 1


def _split_condition_fields_by_group(
    entries: Sequence[Tuple[float, float, str, str]],
) -> Tuple[List[Tuple[float, float, str, str]], List[Tuple[float, float, str, str]]]:
    g1: List[Tuple[float, float, str, str]] = []
    g2: List[Tuple[float, float, str, str]] = []
    for item in entries:
        grp = _condition_field_group(item[2])
        if grp == 2:
            g2.append(item)
        elif grp == 1:
            g1.append(item)
    return g1, g2


def _assemble_condition_from_fields(
    entries: Sequence[Tuple[float, float, str, str]],
    payload: Mapping[str, Any],
) -> str:
    parts: List[str] = []
    seen: set[str] = set()
    for _x, _y, _fid, pid in sorted(entries, key=lambda t: (t[1], t[0])):
        v = _value_from_payload(payload, pid)
        if not v or v in seen:
            continue
        seen.add(v)
        parts.append(v)
    return _norm(" ".join(parts))


def _conditions_from_template_fields(
    payload: Mapping[str, Any],
    site_template_parsed: Optional[Mapping[str, Any]],
) -> Tuple[str, str]:
    if not isinstance(site_template_parsed, dict):
        return "", ""
    pdf = site_template_parsed.get("pdf")
    if not isinstance(pdf, dict):
        return "", ""
    fields = pdf.get("fields")
    if not isinstance(fields, list):
        return "", ""
    candidates: List[Tuple[float, float, str, str]] = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        if field.get("templateSectionKey") != "site_radiation_protection":
            continue
        fid = _norm(field.get("id") or "")
        if not _is_radiation_condition_field(fid):
            continue
        rect = field.get("rect") or []
        if len(rect) < 3:
            continue
        try:
            x_pos = float(rect[1])
            y_pos = float(rect[2])
        except (TypeError, ValueError):
            continue
        pid = str(field.get("pdfFieldId") or field.get("id") or "").strip()
        if not pid:
            continue
        candidates.append((x_pos, y_pos, fid, pid))
    if not candidates:
        return "", ""
    g1_fields, g2_fields = _split_condition_fields_by_group(candidates)
    return (
        _assemble_condition_from_fields(g1_fields, payload),
        _assemble_condition_from_fields(g2_fields, payload),
    )


def _condition_from_payload_scan(payload: Mapping[str, Any], *, group: int) -> str:
    """从提交 JSON 扫描整段检测条件文案（模板栏位不可用时的兜底）。"""
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
            if "检测条件" in text or ("kV" in text and ("mA" in text or "mAs" in text)):
                matches.append(text)
    if not matches:
        return ""
    return _format_condition_text(matches[0] if group == 1 else matches[-1])


def _conditions_from_payload(
    payload: Mapping[str, Any],
    site_template_parsed: Optional[Mapping[str, Any]] = None,
) -> Tuple[str, str]:
    g1, g2 = _conditions_from_template_fields(payload, site_template_parsed)
    if not g1:
        g1 = _condition_from_payload_scan(payload, group=1)
    if not g2:
        g2 = _condition_from_payload_scan(payload, group=2)
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


def _build_points_from_bindings_group(
    bindings: Sequence[Mapping[str, Any]],
    dd: Mapping[str, Any],
    *,
    group: int,
) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    for binding in bindings:
        if not isinstance(binding, dict) or _is_background_binding(binding):
            continue
        if not _row_is_active_group(binding, dd, group=group):
            continue
        pt_key = _norm(binding.get("radiationPoint") or "")
        if not pt_key:
            continue
        loc, _row_idx, sub = _parse_radiation_point_key(pt_key)
        group_key = loc or pt_key
        if group_key not in groups:
            groups[group_key] = {
                "location": loc or pt_key,
                "has_sub": False,
                "rows": [],
            }
            order.append(group_key)
        entry = groups[group_key]
        if sub:
            entry["has_sub"] = True
        seq_val = _cell_value(dd, str(binding.get("seq_no") or ""))
        result = _result_from_binding_group(binding, dd, group=group)
        entry["rows"].append(
            {
                "sub": sub,
                "seq": seq_val,
                "result": result,
                "row_idx": _row_idx or 0,
                "point_key": pt_key,
            }
        )

    points: List[Dict[str, Any]] = []
    sub_id_counter = 1
    for group_key in order:
        g = groups[group_key]
        rows = sorted(g["rows"], key=lambda r: (r.get("row_idx") or 0, r.get("point_key") or ""))
        if not g["has_sub"]:
            chosen = next((row for row in rows if _norm(row.get("result") or "")), None)
            if not chosen:
                continue
            points.append(
                {
                    "type": "simple",
                    "id": chosen.get("seq") or str(sub_id_counter),
                    "location": g["location"],
                    "result": chosen.get("result") or "",
                    "standard": DEFAULT_RADIATION_STANDARD,
                    "evaluation": "",
                }
            )
            sub_id_counter += 1
            continue

        sub_rows: List[Dict[str, Any]] = []
        for row in rows:
            result = _norm(row.get("result") or "")
            if not result or result == "/":
                continue
            sid = row.get("seq") or str(sub_id_counter)
            if str(sid).strip() == "/":
                sid = str(sub_id_counter)
            sub_rows.append(
                {
                    "id": sid,
                    "location_sub": row.get("sub") or "",
                    "result": result,
                    "standard": DEFAULT_RADIATION_STANDARD,
                    "evaluation": "",
                }
            )
            sub_id_counter += 1
        if not sub_rows:
            continue
        points.append(
            {
                "type": "complex",
                "location": g["location"],
                "sub_rows": sub_rows,
            }
        )
    return _filter_points_with_results(points)


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
) -> Optional[Dict[str, Any]]:
    """按 fieldBindings + dynamicData 生成 report_data。"""
    if not isinstance(payload, dict) or not bindings:
        return None
    dd = _dynamic_data(payload)
    if dual_group is None:
        dual_group = _detect_dual_group_report(bindings, dd, chapter)

    points = _build_points_from_bindings_group(bindings, dd, group=1)
    points_group2: List[Dict[str, Any]] = []
    if dual_group:
        points_group2 = _build_points_from_bindings_group(bindings, dd, group=2)
        points = _preserve_report_point_ids(points)
        if points_group2:
            points_group2 = _preserve_report_point_ids(points_group2)
    else:
        points = _renumber_report_point_ids(points)

    if not points and not points_group2:
        return None
    if not points:
        points = points_group2
        points_group2 = []
        dual_group = False

    out: Dict[str, Any] = {
        "condition": condition or "",
        "points": points,
        "background": {"label": "本底值（μSv/h）", "value": background_value or ""},
        "notes": list(notes) if notes else list(_DEFAULT_NOTES),
    }
    if dual_group and points_group2:
        out["condition_group2"] = condition_group2 or ""
        out["points_group2"] = points_group2
    return apply_report_evaluations(out)


def _preserve_report_point_ids(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """双组表保留现场记录序号；缺失时按出现顺序补位。"""
    fallback = 1
    for pt in points:
        if pt.get("type") == "complex":
            for sub in pt.get("sub_rows") or []:
                if not isinstance(sub, dict):
                    continue
                sid = _norm(sub.get("id") or "")
                if not sid or sid == "/":
                    sub["id"] = str(fallback)
                fallback += 1
            pt.pop("id", None)
        else:
            sid = _norm(pt.get("id") or "")
            if not sid or sid == "/":
                pt["id"] = str(fallback)
            fallback += 1
    return points


def _renumber_report_point_ids(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """报告表序号连续编号：每行一个序号，从 1 起（复杂点位子行亦各占一行）。"""
    n = 1
    for pt in points:
        if pt.get("type") == "complex":
            for sub in pt.get("sub_rows") or []:
                if not isinstance(sub, dict):
                    continue
                sub["id"] = str(n)
                n += 1
            pt.pop("id", None)
        else:
            pt["id"] = str(n)
            n += 1
    return points


def _background_from_payload(payload: Mapping[str, Any], bindings: Sequence[Mapping[str, Any]]) -> str:
    """本底报出值：优先 f600（现场表公式结果），否则 f641–f650 读数 × f205。"""
    report_d = _value_from_payload(payload, "f600")
    if report_d:
        return report_d

    dd = _dynamic_data(payload)
    tr = _payload_bucket(payload, "testResult")
    slots: List[float] = []
    for i in range(641, 651):
        for src in (dd, tr):
            num = _parse_float_token(_cell_value(src, f"f{i}"))
            if num is not None:
                slots.append(num)
                break
    if not slots:
        for binding in bindings:
            if not isinstance(binding, dict) or not _is_background_binding(binding):
                continue
            for k in ("reading_1", "reading_2", "reading_3"):
                num = _parse_float_token(_cell_value(dd, str(binding.get(k) or "")))
                if num is not None:
                    slots.append(num)
    if not slots:
        return ""

    factor = _parse_float_token(_value_from_payload(payload, "f205")) or 1.0
    scaled = [n * factor for n in slots]
    return _format_background_range(min(scaled), max(scaled))


def _condition_from_extracted(extracted: Mapping[str, Any]) -> str:
    preface = extracted.get("preface")
    if isinstance(preface, dict):
        cond = preface.get("condition")
        if isinstance(cond, dict):
            text = _norm(cond.get("text") or "")
            if text:
                if not text.startswith("检测条件"):
                    text = f"检测条件：{text}"
                return text
    return ""


def build_report_data_from_extracted(extracted: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """将 extract_chapter5_full / extract_js009_chapter5 输出转为 report_data。"""
    if not isinstance(extracted, dict):
        return None
    raw_rows = extracted.get("raw_rows")
    if not isinstance(raw_rows, list):
        return None

    groups: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    sub_id = 1

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
            if str(row.get("report_d") or "").strip() == "/" or str(row.get("mean_m") or "").strip() == "/":
                continue
            result = _norm(row.get("report_d") or row.get("mean_m") or "")
            if not result:
                continue
            groups.append(
                {
                    "type": "simple",
                    "id": _norm(row.get("point_id") or "") or str(sub_id),
                    "location": _norm(row.get("location") or ""),
                    "result": result,
                    "standard": DEFAULT_RADIATION_STANDARD,
                    "evaluation": "",
                }
            )
            sub_id += 1
        elif kind == "complex_sub":
            main = _norm(row.get("location_main") or "")
            sub = _norm(row.get("location_sub") or "")
            result = _norm(row.get("report_d") or row.get("mean_m") or "")
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
            if str(row.get("report_d") or "").strip() == "/" or str(row.get("mean_m") or "").strip() == "/":
                continue
            if not result:
                continue
            sid = _norm(row.get("point_id") or "") or str(sub_id)
            current["sub_rows"].append(
                {
                    "id": sid,
                    "location_sub": sub,
                    "result": result,
                    "standard": DEFAULT_RADIATION_STANDARD,
                    "evaluation": "",
                }
            )
            sub_id += 1
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
        "points": _renumber_report_point_ids(points),
        "background": {"label": "本底值（μSv/h）", "value": bg_value},
        "notes": notes,
    }
    return apply_report_evaluations(out)


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
    built = build_report_data_from_extracted(extracted)
    if not isinstance(built, dict):
        return data
    out = dict(data)
    built_cond = _norm(built.get("condition"))
    payload_cond = _norm(out.get("condition"))
    if built_cond and (not payload_cond or "球管朝" in payload_cond and "照射" in payload_cond):
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
    pdf_fields = None
    if isinstance(site_template_parsed, dict):
        form_schema = site_template_parsed.get("formSchema") or site_template_parsed.get("form_schema")
        if isinstance(form_schema, dict):
            chapter = form_schema.get(SCHEMA_KEY) or form_schema.get("radiationProtectionChapter")
        chapter = chapter or site_template_parsed.get(SCHEMA_KEY) or site_template_parsed.get("radiationProtectionChapter")
        pdf = site_template_parsed.get("pdf")
        if isinstance(pdf, dict) and isinstance(pdf.get("fields"), list):
            pdf_fields = pdf.get("fields")
    if isinstance(payload, dict):
        chapter = chapter or payload.get(SCHEMA_KEY)
        bindings = resolve_chapter_field_bindings(chapter, pdf_fields=pdf_fields, export_payload=payload)

    if isinstance(payload, dict) and bindings:
        bg = _background_from_payload(payload, bindings)
        cond, cond2 = _conditions_from_payload(payload, site_template_parsed)
        data = build_report_data_from_bindings(
            payload,
            bindings,
            condition=cond,
            condition_group2=cond2,
            background_value=bg,
            chapter=chapter,
        )
        if data and data.get("points"):
            return apply_report_evaluations(
                _enrich_report_data_from_site_extract(data, site_pdf_path=site_pdf_path)
            )

    if site_pdf_path:
        try:
            from radiation_detection_report.extract_js009_chapter5 import extract_js009_chapter5

            extracted = extract_js009_chapter5(site_pdf_path)
            data = extracted.get("report_data") if isinstance(extracted, dict) else None
            if isinstance(data, dict) and data.get("points"):
                return apply_report_evaluations(data)
            built = build_report_data_from_extracted(extracted)
            if built and built.get("points"):
                return built
        except Exception:
            pass
    return None
