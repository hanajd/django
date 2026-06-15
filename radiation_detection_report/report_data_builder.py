"""
从现场记录 PDF 或检测提交 JSON 构建报告表输入 report_data。
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from radiation_detection_report.chapter5_field_sync import (
    SCHEMA_KEY,
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


def _binding_is_slash_skipped(binding: Mapping[str, Any], dd: Mapping[str, Any]) -> bool:
    """测量列或报出值出现 / 表示该项未检测，整行不进入报告表。"""
    for key in ("reading_1", "reading_2", "reading_3", "mean_m", "report_d", "seq_no"):
        pid = str(binding.get(key) or "").strip()
        if not pid:
            continue
        raw = _raw_cell(dd, pid)
        if str(raw).strip() == "/":
            if key == "seq_no":
                continue
            return True
    return False


def _row_is_active(binding: Mapping[str, Any], dd: Mapping[str, Any]) -> bool:
    if _binding_is_slash_skipped(binding, dd):
        return False
    keys = ("reading_1", "reading_2", "reading_3", "mean_m", "report_d", "seq_no")
    for k in keys:
        v = _cell_value(dd, str(binding.get(k) or ""))
        if v:
            return True
    return False


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


def _format_seq_range(ids: Sequence[str]) -> str:
    clean = [x for x in (_norm(i) for i in ids) if x and x != "/"]
    if not clean:
        return ""
    if len(clean) == 1:
        return clean[0]
    return f"{clean[0]}~{clean[-1]}"


def _result_from_binding(binding: Mapping[str, Any], dd: Mapping[str, Any]) -> str:
    for key in ("report_d", "mean_m", "reading_1", "reading_2", "reading_3"):
        v = _cell_value(dd, str(binding.get(key) or ""))
        if v:
            return v
    return ""


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


def _condition_from_payload(
    payload: Mapping[str, Any],
    site_template_parsed: Optional[Mapping[str, Any]] = None,
) -> str:
    """
    从现场提交 JSON 拼装报告表首行「检测条件」。
    JS-009：f210 球管朝向、f216 kV、f213 mA、f212 s、f214 mAs 等。
    """
    del site_template_parsed  # 预留：后续可按模板 pdf.fields 泛化
    tube = _value_from_payload(payload, "f210")
    kv = _value_from_payload(payload, "f216")
    ma = _value_from_payload(payload, "f213")
    sec = _value_from_payload(payload, "f212")
    mas = _value_from_payload(payload, "f214")
    if not any([tube, kv, ma, sec, mas]):
        return ""
    chunks: List[str] = []
    if tube:
        chunks.append(f"球管朝{tube}照射")
    params: List[str] = []
    if kv:
        params.append(f"{kv} kV")
    if ma:
        params.append(f"{ma} mA")
    if sec:
        params.append(f"{sec} s")
    if params:
        chunks.append(" ".join(params))
    body = "，".join(chunks)
    if mas:
        body = f"{body}（{mas} mAs）" if body else f"（{mas} mAs）"
    if not body:
        return ""
    if not body.startswith("检测条件"):
        body = f"检测条件：{body}"
    if not body.endswith("。"):
        body += "。"
    return body


def build_report_data_from_bindings(
    payload: Mapping[str, Any],
    bindings: Sequence[Mapping[str, Any]],
    *,
    condition: str = "",
    background_value: str = "",
    notes: Optional[Sequence[str]] = None,
) -> Optional[Dict[str, Any]]:
    """按 fieldBindings + dynamicData 生成 report_data。"""
    if not isinstance(payload, dict) or not bindings:
        return None
    dd = _dynamic_data(payload)
    groups: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    for binding in bindings:
        if not isinstance(binding, dict) or _is_background_binding(binding):
            continue
        if not _row_is_active(binding, dd):
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
        result = _result_from_binding(binding, dd)
        entry["rows"].append(
            {
                "sub": sub,
                "seq": seq_val,
                "result": result,
                "row_idx": _row_idx or 0,
                "point_key": pt_key,
            }
        )

    if not order:
        return None

    points: List[Dict[str, Any]] = []
    sub_id_counter = 1
    for group_key in order:
        g = groups[group_key]
        rows = sorted(g["rows"], key=lambda r: (r.get("row_idx") or 0, r.get("point_key") or ""))
        if not g["has_sub"]:
            row = rows[0]
            if not _norm(row.get("result") or ""):
                continue
            points.append(
                {
                    "type": "simple",
                    "id": row.get("seq") or str(sub_id_counter),
                    "location": g["location"],
                    "result": row.get("result") or "",
                    "standard": DEFAULT_RADIATION_STANDARD,
                    "evaluation": "",
                }
            )
            sub_id_counter += 1
            continue

        sub_rows: List[Dict[str, Any]] = []
        seq_ids: List[str] = []
        for row in rows:
            result = _norm(row.get("result") or "")
            if not result or result == "/":
                continue
            sid = row.get("seq") or str(sub_id_counter)
            if str(sid).strip() == "/":
                sid = str(sub_id_counter)
            seq_ids.append(sid)
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
                "id": _format_seq_range(seq_ids),
                "location": g["location"],
                "sub_rows": sub_rows,
            }
        )

    if not points:
        return None

    out: Dict[str, Any] = {
        "condition": condition or "",
        "points": _renumber_report_point_ids(points),
        "background": {"label": "本底值（μSv/h）", "value": background_value or ""},
        "notes": list(notes) if notes else list(_DEFAULT_NOTES),
    }
    return apply_report_evaluations(out)


def _renumber_report_point_ids(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """报告表序号连续编号：simple 为 1、2…；complex 子行连续，组 id 为 首~尾。"""
    n = 1
    for pt in points:
        if pt.get("type") == "complex":
            subs = pt.get("sub_rows") or []
            seq_ids: List[str] = []
            for sub in subs:
                if not isinstance(sub, dict):
                    continue
                sub["id"] = str(n)
                seq_ids.append(str(n))
                n += 1
            if seq_ids:
                pt["id"] = f"{seq_ids[0]}~{seq_ids[-1]}" if len(seq_ids) > 1 else seq_ids[0]
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
                if isinstance(readings, list) and readings:
                    result = _norm(readings[-1])
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
                    "seq_ids": [],
                }
            if current is None:
                continue
            readings = row.get("readings") or []
            if isinstance(readings, list) and any(str(x).strip() == "/" for x in readings):
                continue
            if str(row.get("report_d") or "").strip() == "/" or str(row.get("mean_m") or "").strip() == "/":
                continue
            if not result and not sub:
                continue
            sid = _norm(row.get("point_id") or "") or str(sub_id)
            current["seq_ids"].append(sid)
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

    for g in groups:
        if g.get("type") == "complex":
            seq_ids = g.pop("seq_ids", [])
            g["id"] = _format_seq_range(seq_ids)

    points = [g for g in groups if g.get("type") in ("simple", "complex")]
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
    if not _norm(out.get("condition")) and _norm(built.get("condition")):
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


def try_build_report_data(
    payload: Optional[Mapping[str, Any]],
    *,
    site_template_parsed: Optional[Mapping[str, Any]] = None,
    site_pdf_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    优先从检测提交 + fieldBindings 构建；若无有效点位则尝试现场记录 PDF 提取。
    """
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
        cond = _condition_from_payload(payload, site_template_parsed)
        data = build_report_data_from_bindings(
            payload,
            bindings,
            condition=cond,
            background_value=bg,
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
