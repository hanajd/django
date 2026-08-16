# -*- coding: utf-8 -*-
"""健康影响评价 · 正常情况下：影像诊断（表11–12）+ 介入放射学（表13–15）。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

TABLE_MARK = "<<<F1_TABLE>>>"

TABLE_TITLES = [
    "表11 本项目13台设备（X射线影像诊断）预期工作量",
    "表12 本项目X射线影像诊断工作人员及公众年受照剂量估算情况",
    "表13 本项目3台介入设备（介入放射学）预期工作量",
    "表14 本项目透视防护区工作人员年有效剂量估算结果一览表",
    "表15 本项目ERCP机房外和DSA机房外人员年有效剂量估算结果表",
]

TABLE11_TITLE_TMPL = "表11 本项目{n}台设备（X射线影像诊断）预期工作量"

# 设备操作方式、单次出束时间（固定）；年工作负荷 = 年诊疗人数 × 出束时间(h)
# (mode_label, time_label, time_seconds)
ImagingMode = Tuple[str, str, float]
IMAGING_BEAM_MODES: Dict[str, List[ImagingMode]] = {
    "CT": [("隔室", "20s", 20.0)],
    "DR": [("隔室", "0.2s", 0.2)],
    "BMD": [("隔室", "2min", 120.0)],
    "MAMMO": [("隔室", "1s", 1.0)],
    "GI": [
        ("隔室（透视）", "10min", 600.0),
        ("隔室（摄影）", "0.5min", 30.0),
    ],
    "C臂": [
        ("隔室（透视）", "10min", 600.0),
        ("隔室（摄影）", "0.5min", 30.0),
    ],
}

DEFAULT_PATIENTS: Dict[str, List[int]] = {
    "CT": [45000],
    "DR": [45000],
    "BMD": [6250],
    "MAMMO": [7500],
    "GI": [2000, 2000],
    "C臂": [1000, 1000],
}

TABLE11_NOTE = (
    "注：①机房名称、所在楼层由信息表装置/屏蔽位置写入；"
    "②设备操作方式、单次检查平均出束时间为固定默认（可改）；"
    "③年诊疗人数手工填写；"
    "④年工作负荷按公式自动计算：年工作负荷(h)=年诊疗人数×单次出束时间(换算为小时)。"
)

TABLE11_FORMULA = "年工作负荷(h)=年诊疗人数×单次出束时间(h)"

TABLE12_TITLE = "表12 本项目X射线影像诊断工作人员及公众年受照剂量估算情况"
TABLE12_FORMULA = (
    "年受照剂量(mSv)=机房外周围剂量当量率(μSv/h)×年工作负荷(h)×居留因子/1000"
)
TABLE12_NOTE = (
    "注①各机房外周围剂量当量率取标准限值，数字胃肠机和C形臂透视模式和摄影模式"
    "机房外周围剂量当量率均按2.5μSv/h估算。\n"
    "注②年有效剂量（mSv）=机房外周围剂量当量率（μSv/h）×年工作负荷（h）×居留因子/1000。\n"
    "注③工作人员居留因子保守取1，公众居留因子取1/4。"
)

# 人员行：(标签, 居留因子显示, 居留因子数值)
TABLE12_PERSON_ROWS: List[Tuple[str, str, float]] = [
    ("放射工作人员", "1", 1.0),
    ("公众", "1/4", 0.25),
]

# 默认机房外剂量率（μSv/h）；个别机房可覆盖，编辑后重建保留
DEFAULT_DOSE_RATE_USV_H = 2.5
DEFAULT_DOSE_RATE_BY_ROOM: Dict[str, float] = {
    "DR机房1": 25.0,
    "DR机房2": 25.0,
}

DEFAULT_IMAGING_INTRO = (
    "（1）X射线影像诊断放射工作人员及公众受照剂量估算\n\n"
    "根据建设单位提供的X射线影像诊断预期工作量（见表11）及机房屏蔽体外剂量水平，"
    "本项目X射线影像诊断放射人员剂量估算见表12。"
)

DEFAULT_IMAGING_AFTER = (
    "综上所述，本项目X射线影像诊断放射工作人员可能受到的年受照剂量和公众可能受到的最大年受照剂量"
    "均小于GB 18871-2002《电离辐射防护与辐射源安全基本标准》中规定的剂量限值，"
    "也小于建设单位设置的工作人员年管理目标值（5mSv/a）、公众年管理目标值（0.25mSv/a），"
    "符合GB 18871-2002《电离辐射防护与辐射源安全基本标准》相关要求。"
)

DEFAULT_INTERV_INTRO = (
    "（2）介入放射学放射人员受照剂量估算\n\n"
    "根据建设单位提供的介入放射学预期工作量（见表13）及机房屏蔽体外剂量水平，"
    "本项目介入放射学放射人员剂量估算见表14、表15。"
)

DEFAULT_INTERV_AFTER = (
    "从表14和表15可知，本项目ERCP项目放射工作人员可能受到的最大年有效剂量值为2.26mSv"
    "（机房内2.13mSv+机房外0.125mSv）、眼晶体可能受到的年当量剂量为2.13mSv、"
    "四肢和皮肤可能受到的年当量剂量为20.00mSv，DSA项目放射工作人员可能受到的最大年有效剂量值为4.50mSv"
    "（机房内4.25mSv+机房外0.250mSv）、眼晶体可能受到的年当量剂量为4.25mSv、"
    "四肢和皮肤可能受到的年当量剂量为40.00mSv，"
    "小于GB 18871-2002《电离辐射防护与辐射源安全基本标准》中规定的剂量限值"
    "（年有效剂量：20mSv/a、眼晶体年当量剂量：20mSv/a、四肢和皮肤年当量剂量：500mSv/a），"
    "也小于建设单位设置的工作人员年管理目标值"
    "（年有效剂量：5mSv/a、眼晶体年当量剂量：5mSv/a、四肢和皮肤年当量剂量：125mSv/a）。\n\n"
    "本项目ERCP机房外公众可能受到的年有效剂量值为0.034mSv、"
    "DSA机房外公众可能受到的年有效剂量值为0.066mSv，"
    "均小于GB 18871-2002《电离辐射防护与辐射源安全基本标准》中规定的剂量限值（1mSv/a），"
    "也小于建设单位设置的管理目标值（0.25mSv/a）。\n\n"
    "综上所述，本项目在正常运行情况下，放射工作人员和公众可能受到的年有效剂量"
    "均小于GB 18871-2002《电离辐射防护与辐射源安全基本标准》中规定的剂量限值和"
    "建设单位设置的年管理目标值。ERCP项目和DSA项目放射工作人员眼晶体、四肢和皮肤的年当量剂量"
    "小于GB 18871-2002《电离辐射防护与辐射源安全基本标准》中规定的剂量限值和"
    "建设单位设置的年管理目标值。"
)


def _load_fixed(rel: str, default: str = "") -> str:
    from f1_eval_report.templates.loader import load_common_text

    try:
        t = load_common_text(rel).strip()
        return t or default
    except Exception:
        return default


def _load_table(rel: str, title: str) -> Dict[str, Any]:
    from f1_eval_report.templates.loader import load_common_json

    try:
        raw = load_common_json(rel)
        if isinstance(raw, dict) and raw.get("rows"):
            raw.setdefault("title", title)
            return raw
    except Exception:
        pass
    return {
        "schema": "embedded_generic_table/v1",
        "title": title,
        "source": "health_impact",
        "from_info_sheet": False,
        "column_count": 1,
        "column_fractions": [0.0, 1.0],
        "band_count": 1,
        "rows": [
            {
                "band": 0,
                "cells": [
                    {
                        "c0": 0,
                        "c1": 1,
                        "row_span": 1,
                        "col_span": 1,
                        "text": "（待填写）",
                        "align": "center",
                    }
                ],
            }
        ],
    }


def _cell(
    c0: int,
    c1: int,
    text: str,
    *,
    row_span: int = 1,
    align: str = "center",
    **extra: Any,
) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "c0": c0,
        "c1": c1,
        "row_span": row_span,
        "col_span": max(1, c1 - c0),
        "text": text,
        "align": align,
    }
    d.update(extra)
    return d


def parse_beam_time_seconds(text: str) -> float:
    """解析「20s / 2min / 0.5min」为秒。"""
    s = str(text or "").strip().lower().replace(" ", "")
    m = re.match(r"^([\d.]+)\s*(min|分钟|分)", s)
    if m:
        return float(m.group(1)) * 60.0
    m = re.match(r"^([\d.]+)\s*(s|sec|秒)?$", s)
    if m:
        return float(m.group(1))
    m = re.search(r"([\d.]+)\s*(min|分钟)", s)
    if m:
        return float(m.group(1)) * 60.0
    m = re.search(r"([\d.]+)\s*(s|秒)", s)
    if m:
        return float(m.group(1))
    return 0.0


def parse_patients_count(text: str) -> float:
    """解析「45000人次/年」→ 45000。"""
    s = str(text or "").replace(",", "").replace("，", "")
    m = re.search(r"([\d.]+)", s)
    return float(m.group(1)) if m else 0.0


def format_patients(n: float) -> str:
    if n <= 0:
        return ""
    if abs(n - round(n)) < 1e-6:
        return f"{int(round(n))}人次/年"
    return f"{n:g}人次/年"


def format_workload_hours(hours: float) -> str:
    if hours <= 0:
        return ""
    return f"{hours:.1f}h"


def calc_workload_hours(patients: float, time_seconds: float) -> float:
    """年工作负荷(h) = 年诊疗人数 × 单次出束时间(s) / 3600。"""
    if patients <= 0 or time_seconds <= 0:
        return 0.0
    return patients * time_seconds / 3600.0


def _device_key(name: str, place: str = "") -> str:
    blob = f"{name}|{place}"
    if "DSA" in blob:
        return "DSA"
    if "ERCP" in blob or ("内镜" in place and "C形臂" in name):
        return "ERCP"
    if "骨密度" in blob:
        return "BMD"
    if "乳腺" in blob:
        return "MAMMO"
    if "胃肠" in blob:
        return "GI"
    if "C形臂" in name or "C臂" in name or "手术室" in place:
        return "C臂"
    if re.search(r"(^|[^A-Za-z])CT([^A-Za-z]|$)", name) or "CT机房" in place:
        return "CT"
    if name == "DR" or re.search(r"(^|[^A-Za-z])DR([^A-Za-z]|$)", name) or "DR机房" in place:
        return "DR"
    return "OTHER"


def _normalize_floor(place: str) -> str:
    s = re.sub(r"\s+", "", str(place or ""))
    s = s.replace("一层", "一楼").replace("二层", "二楼").replace("四层", "四楼")
    s = s.replace("门诊楼", "门急诊楼")
    s = s.replace("住院楼四楼手术中心", "医技楼四楼手术中心")
    s = s.replace("住院楼二楼介入中心", "医技楼二楼介入中心")
    s = s.replace("住院楼二楼内镜中心", "医技楼二楼内镜中心")
    s2 = re.sub(
        r"(?:CT|DR|DSA|ERCP|全身骨密度仪|乳腺DR|数字胃肠|C形臂)?机房\d*$",
        "",
        s,
    )
    s2 = re.sub(r"手术室\d+.*$", "", s2)
    return s2 or s


def _room_label_from_place(place: str, key: str, index_in_type: int, total_in_type: int) -> str:
    place_n = re.sub(r"\s+", "", str(place or ""))
    m = re.search(
        r"((?:CT|DR|DSA|ERCP|全身骨密度仪|乳腺DR|数字胃肠)机房\d*)",
        place_n,
    )
    if m:
        return m.group(1)
    type_names = {
        "CT": "CT机房",
        "DR": "DR机房",
        "BMD": "全身骨密度仪机房",
        "MAMMO": "乳腺DR机房",
        "GI": "数字胃肠机房",
        "C臂": "C形臂机房",
    }
    base = type_names.get(key, "机房")
    if key == "C臂":
        return f"C形臂机房{index_in_type}"
    if total_in_type > 1:
        return f"{base}{index_in_type}"
    return base


def imaging_devices_from_list(
    devices: Optional[Sequence[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """筛出表11用的影像诊断装置（不含 DSA/ERCP）。"""
    raw: List[Dict[str, Any]] = []
    for d in devices or []:
        name = str(d.get("name") or "").strip()
        place = str(d.get("place") or d.get("location") or "").strip()
        key = _device_key(name, place)
        if key in {"DSA", "ERCP", "OTHER"} or key not in IMAGING_BEAM_MODES:
            continue
        floor = _normalize_floor(place)
        raw.append({"key": key, "name": name, "place": place, "floor": floor})

    # 楼层内同类型计数
    floor_type_total: Dict[Tuple[str, str], int] = {}
    floor_type_idx: Dict[Tuple[str, str], int] = {}
    for r in raw:
        k = (r["floor"], r["key"])
        floor_type_total[k] = floor_type_total.get(k, 0) + 1
    c_global = 0
    out: List[Dict[str, Any]] = []
    for r in raw:
        k = (r["floor"], r["key"])
        floor_type_idx[k] = floor_type_idx.get(k, 0) + 1
        idx = floor_type_idx[k]
        total = floor_type_total[k]
        if r["key"] == "C臂":
            c_global += 1
            room = f"C形臂机房{c_global}"
        else:
            room = _room_label_from_place(r["place"], r["key"], idx, total)
        out.append({**r, "room": room})
    return out


def default_imaging_rooms() -> List[Dict[str, Any]]:
    """无信息表时的 13 台影像诊断机房骨架（与 NCU2 样例一致）。"""
    specs = [
        ("CT", "门急诊楼一楼", "CT机房", [45000]),
        ("DR", "门急诊楼一楼", "DR机房", [45000]),
        ("CT", "医技楼一楼影像中心", "CT机房1", [45000]),
        ("CT", "医技楼一楼影像中心", "CT机房2", [45000]),
        ("DR", "医技楼一楼影像中心", "DR机房1", [20000]),
        ("DR", "医技楼一楼影像中心", "DR机房2", [20000]),
        ("BMD", "医技楼一楼影像中心", "全身骨密度仪机房", [6250]),
        ("MAMMO", "医技楼一楼影像中心", "乳腺DR机房", [7500]),
        ("GI", "医技楼一楼影像中心", "数字胃肠机房", [2000, 2000]),
        ("C臂", "医技楼四楼手术中心", "C形臂机房1", [1000, 1000]),
        ("C臂", "医技楼四楼手术中心", "C形臂机房2", [1000, 1000]),
        ("CT", "发热门诊楼一楼", "CT机房", [25000]),
        ("DR", "发热门诊楼一楼", "DR机房", [20000]),
    ]
    return [
        {"key": k, "floor": fl, "room": rm, "patients": pts, "place": fl + rm}
        for k, fl, rm, pts in specs
    ]


def _patients_map_from_table(table: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """从已有表11读取 机房|方式 → 年诊疗人数。"""
    out: Dict[str, str] = {}
    if not isinstance(table, dict):
        return out
    cur_room = ""
    for band in table.get("rows") or []:
        if not isinstance(band, dict):
            continue
        cells = {
            int(c.get("c0") or 0): c
            for c in (band.get("cells") or [])
            if isinstance(c, dict)
        }
        if 0 in cells and str(cells[0].get("text") or "").strip() == "序号":
            continue
        room = str(cells.get(1, {}).get("text") or "").strip()
        if room:
            cur_room = room
        mode = str(cells.get(3, {}).get("text") or "").strip()
        patients = str(cells.get(5, {}).get("text") or "").strip()
        if cur_room and mode and patients:
            out[f"{cur_room}|{mode}"] = patients
    return out


def recalc_table11_workloads(table: Dict[str, Any]) -> Dict[str, Any]:
    """按公式重算表11「年工作负荷」列。"""
    if not isinstance(table, dict):
        return table
    for band in table.get("rows") or []:
        if not isinstance(band, dict):
            continue
        cells = list(band.get("cells") or [])
        by_c = {int(c.get("c0") or 0): c for c in cells if isinstance(c, dict)}
        if 0 in by_c and str(by_c[0].get("text") or "").strip() == "序号":
            continue
        time_cell = by_c.get(4)
        patients_cell = by_c.get(5)
        workload_cell = by_c.get(6)
        if not time_cell or not patients_cell or not workload_cell:
            continue
        sec = float(time_cell.get("time_seconds") or 0) or parse_beam_time_seconds(
            str(time_cell.get("text") or "")
        )
        patients = parse_patients_count(str(patients_cell.get("text") or ""))
        hours = calc_workload_hours(patients, sec)
        workload_cell["text"] = format_workload_hours(hours)
        workload_cell["formula"] = TABLE11_FORMULA
        workload_cell["time_seconds"] = sec
        workload_cell["cell_role"] = "workload"
        patients_cell["cell_role"] = "patients"
        time_cell["time_seconds"] = sec
        time_cell["cell_role"] = "beam_time"
    table["workload_formula"] = TABLE11_FORMULA
    return table


def build_table11(
    devices: Optional[Sequence[Dict[str, Any]]] = None,
    *,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    表11：影像诊断预期工作量。
    - 机房名称/所在楼层：信息表
    - 操作方式、出束时间：固定
    - 年诊疗人数：手工（重建保留）
    - 年工作负荷：patients × time_s / 3600
    """
    rooms = imaging_devices_from_list(devices)
    from_sheet = bool(rooms)
    if not rooms:
        rooms = default_imaging_rooms()
        from_sheet = False

    preserved = _patients_map_from_table(existing)
    last_floor = ""
    n_devices = len(rooms)
    title = TABLE11_TITLE_TMPL.format(n=n_devices)

    header = {
        "band": 0,
        "cells": [
            _cell(0, 1, "序号"),
            _cell(1, 2, "机房名称"),
            _cell(2, 3, "所在楼层"),
            _cell(3, 4, "设备操作方式"),
            _cell(4, 5, "单次检查平均出束时间"),
            _cell(5, 6, "年诊疗人数"),
            _cell(6, 7, "年工作负荷"),
        ],
    }
    bands: List[Dict[str, Any]] = [header]
    band_i = 1
    seq = 0
    for room in rooms:
        key = room["key"]
        modes = IMAGING_BEAM_MODES.get(key) or [("隔室", "20s", 20.0)]
        floor = str(room.get("floor") or "")
        room_name = str(room.get("room") or "")
        show_floor = floor if floor != last_floor else ""
        if floor:
            last_floor = floor
        n_modes = len(modes)
        seq += 1
        default_pts = list(room.get("patients") or DEFAULT_PATIENTS.get(key) or [0] * n_modes)
        while len(default_pts) < n_modes:
            default_pts.append(default_pts[-1] if default_pts else 0)

        for mi, (mode, time_label, time_s) in enumerate(modes):
            cells: List[Dict[str, Any]] = []
            if mi == 0:
                cells.append(_cell(0, 1, str(seq), row_span=n_modes))
                cells.append(_cell(1, 2, room_name, row_span=n_modes, align="left"))
                cells.append(_cell(2, 3, show_floor, row_span=n_modes, align="left"))
            cells.append(_cell(3, 4, mode, cell_role="op_mode"))
            cells.append(
                _cell(
                    4,
                    5,
                    time_label,
                    cell_role="beam_time",
                    time_seconds=time_s,
                )
            )
            key_pm = f"{room_name}|{mode}"
            if key_pm in preserved:
                patients_txt = preserved[key_pm]
            else:
                patients_txt = format_patients(float(default_pts[mi] or 0))
            patients_n = parse_patients_count(patients_txt)
            cells.append(_cell(5, 6, patients_txt, cell_role="patients"))
            hours = calc_workload_hours(patients_n, time_s)
            cells.append(
                _cell(
                    6,
                    7,
                    format_workload_hours(hours),
                    cell_role="workload",
                    formula=TABLE11_FORMULA,
                    time_seconds=time_s,
                )
            )
            bands.append({"band": band_i, "cells": cells})
            band_i += 1

    bands.append({"band": band_i, "cells": [_cell(0, 7, TABLE11_NOTE, align="left")]})
    return {
        "schema": "embedded_generic_table/v1",
        "title": title,
        "source": "health_impact",
        "from_info_sheet": from_sheet,
        "column_count": 7,
        "column_fractions": [0.0, 0.07, 0.22, 0.40, 0.54, 0.70, 0.86, 1.0],
        "band_count": len(bands),
        "row_min_height_pt": 18.0,
        "repeat_header_on_new_page": True,
        "header_band_count": 1,
        "workload_formula": TABLE11_FORMULA,
        "note": TABLE11_NOTE,
        "device_count": n_devices,
        "rows": bands,
    }


def parse_workload_hours(text: str) -> float:
    """解析「250.0h / 350h」→ 小时。"""
    s = str(text or "").replace(",", "").replace("，", "").strip().lower()
    m = re.search(r"([\d.]+)", s)
    return float(m.group(1)) if m else 0.0


def parse_dose_rate_usv_h(text: str) -> float:
    """解析「2.5μSv/h / 25uSv/h」→ μSv/h。"""
    s = str(text or "").replace("μ", "u").replace("µ", "u").lower()
    m = re.search(r"([\d.]+)", s)
    return float(m.group(1)) if m else 0.0


def parse_occupancy_factor(text: str) -> float:
    """解析「1 / 1/4 / 0.25」→ 居留因子。"""
    s = str(text or "").strip().replace(" ", "")
    if not s:
        return 0.0
    if "/" in s:
        parts = s.split("/", 1)
        try:
            a, b = float(parts[0]), float(parts[1])
            return a / b if b else 0.0
        except ValueError:
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def format_dose_rate(usv_h: float) -> str:
    if usv_h <= 0:
        return ""
    if abs(usv_h - round(usv_h)) < 1e-9:
        return f"{int(round(usv_h))}μSv/h"
    return f"{usv_h:g}μSv/h"


def format_dose_msv(dose: float) -> str:
    if dose <= 0:
        return ""
    s = f"{dose:.3f}".rstrip("0").rstrip(".")
    return f"{s}mSv"


def calc_annual_dose_msv(rate_usv_h: float, workload_h: float, occupancy: float) -> float:
    """年受照剂量(mSv)=率(μSv/h)×负荷(h)×居留因子/1000。"""
    if rate_usv_h <= 0 or workload_h <= 0 or occupancy <= 0:
        return 0.0
    return rate_usv_h * workload_h * occupancy / 1000.0


def rooms_and_workloads_from_table11(
    table11: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """从表11按机房汇总年工作负荷（多模式机房求和）。"""
    if not isinstance(table11, dict):
        return []
    out: List[Dict[str, Any]] = []
    cur_room = ""
    cur_floor = ""
    cur_hours = 0.0
    cur_seq = ""
    last_floor_shown = ""

    def flush() -> None:
        nonlocal cur_room, cur_floor, cur_hours, cur_seq
        if not cur_room:
            return
        out.append(
            {
                "room": cur_room,
                "floor": cur_floor,
                "workload_h": cur_hours,
                "seq": cur_seq,
            }
        )
        cur_room, cur_floor, cur_hours, cur_seq = "", "", 0.0, ""

    for band in table11.get("rows") or []:
        if not isinstance(band, dict):
            continue
        cells = {
            int(c.get("c0") or 0): c
            for c in (band.get("cells") or [])
            if isinstance(c, dict)
        }
        if not cells:
            continue
        t0 = str(cells.get(0, {}).get("text") or "").strip()
        if t0 == "序号" or t0.startswith("注"):
            continue
        # 注脚整行合并
        if len(cells) == 1 and int(cells[list(cells.keys())[0]].get("c1") or 0) >= 6:
            continue
        room = str(cells.get(1, {}).get("text") or "").strip()
        floor = str(cells.get(2, {}).get("text") or "").strip()
        hours = parse_workload_hours(str(cells.get(6, {}).get("text") or ""))
        if room or (t0 and t0.isdigit()):
            flush()
            cur_room = room or cur_room
            cur_seq = t0 if t0.isdigit() else cur_seq
            if floor:
                cur_floor = floor
                last_floor_shown = floor
            elif last_floor_shown:
                cur_floor = last_floor_shown
            cur_hours = hours
        else:
            cur_hours += hours
    flush()
    return out


def _dose_rate_map_from_table12(table: Optional[Dict[str, Any]]) -> Dict[str, float]:
    """已有表12：机房 → 剂量率（取该机房第一行）。"""
    out: Dict[str, float] = {}
    if not isinstance(table, dict):
        return out
    cur_room = ""
    for band in table.get("rows") or []:
        if not isinstance(band, dict):
            continue
        cells = {
            int(c.get("c0") or 0): c
            for c in (band.get("cells") or [])
            if isinstance(c, dict)
        }
        t0 = str(cells.get(0, {}).get("text") or "").strip()
        if t0 == "序号" or t0.startswith("注"):
            continue
        room = str(cells.get(1, {}).get("text") or "").strip()
        if room:
            cur_room = room
        rate_cell = cells.get(4)
        if cur_room and rate_cell and cur_room not in out:
            rate = float(rate_cell.get("rate_usv_h") or 0) or parse_dose_rate_usv_h(
                str(rate_cell.get("text") or "")
            )
            if rate > 0:
                out[cur_room] = rate
    return out


def recalc_table12_doses(table: Dict[str, Any]) -> Dict[str, Any]:
    """按公式重算表12「年受照剂量」列。"""
    if not isinstance(table, dict):
        return table
    for band in table.get("rows") or []:
        if not isinstance(band, dict):
            continue
        cells = list(band.get("cells") or [])
        by_c = {int(c.get("c0") or 0): c for c in cells if isinstance(c, dict)}
        if 0 in by_c and str(by_c[0].get("text") or "").strip() == "序号":
            continue
        if 0 in by_c and str(by_c[0].get("text") or "").strip().startswith("注"):
            continue
        rate_cell = by_c.get(4)
        wl_cell = by_c.get(5)
        occ_cell = by_c.get(6)
        dose_cell = by_c.get(7)
        if not rate_cell or not wl_cell or not occ_cell or not dose_cell:
            continue
        rate = float(rate_cell.get("rate_usv_h") or 0) or parse_dose_rate_usv_h(
            str(rate_cell.get("text") or "")
        )
        hours = float(wl_cell.get("workload_h") or 0) or parse_workload_hours(
            str(wl_cell.get("text") or "")
        )
        occ = float(occ_cell.get("occupancy") or 0) or parse_occupancy_factor(
            str(occ_cell.get("text") or "")
        )
        dose = calc_annual_dose_msv(rate, hours, occ)
        dose_cell["text"] = format_dose_msv(dose)
        dose_cell["formula"] = TABLE12_FORMULA
        dose_cell["cell_role"] = "annual_dose"
        rate_cell["rate_usv_h"] = rate
        rate_cell["cell_role"] = "dose_rate"
        wl_cell["workload_h"] = hours
        wl_cell["cell_role"] = "workload"
        occ_cell["occupancy"] = occ
        occ_cell["cell_role"] = "occupancy"
    table["dose_formula"] = TABLE12_FORMULA
    return table


def apply_table11_workloads_to_table12(
    table12: Dict[str, Any],
    table11: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """把表11汇总负荷写回表12年工作负荷列，并重算剂量。"""
    if not isinstance(table12, dict):
        return table12
    by_room = {
        r["room"]: float(r.get("workload_h") or 0)
        for r in rooms_and_workloads_from_table11(table11)
        if r.get("room")
    }
    cur_room = ""
    for band in table12.get("rows") or []:
        if not isinstance(band, dict):
            continue
        cells = {
            int(c.get("c0") or 0): c
            for c in (band.get("cells") or [])
            if isinstance(c, dict)
        }
        t0 = str(cells.get(0, {}).get("text") or "").strip()
        if t0 == "序号" or t0.startswith("注"):
            continue
        room = str(cells.get(1, {}).get("text") or "").strip()
        if room:
            cur_room = room
        wl = cells.get(5)
        if cur_room and wl and cur_room in by_room:
            h = by_room[cur_room]
            wl["text"] = format_workload_hours(h)
            wl["workload_h"] = h
            wl["cell_role"] = "workload"
    return recalc_table12_doses(table12)


def build_table12(
    devices: Optional[Sequence[Dict[str, Any]]] = None,
    *,
    table11: Optional[Dict[str, Any]] = None,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    表12：影像诊断人员/公众年受照剂量。
    - 机房/楼层：同表11（信息表）
    - 年工作负荷：由表11按机房汇总
    - 剂量率默认标准限值（可改，重建保留）
    - 居留因子：工作人员1、公众1/4
    - 年受照剂量：率×负荷×居留因子/1000
    """
    t11 = table11 if isinstance(table11, dict) and table11.get("rows") else None
    if t11 is None:
        t11 = build_table11(devices, existing=None)
    rooms = rooms_and_workloads_from_table11(t11)
    from_sheet = bool(imaging_devices_from_list(devices))
    if not rooms:
        # 无表11时按默认机房+默认负荷
        t11 = build_table11(devices, existing=None)
        rooms = rooms_and_workloads_from_table11(t11)
        from_sheet = bool(imaging_devices_from_list(devices))

    preserved_rates = _dose_rate_map_from_table12(existing)
    header = {
        "band": 0,
        "cells": [
            _cell(0, 1, "序号"),
            _cell(1, 2, "机房名称"),
            _cell(2, 3, "所在楼层"),
            _cell(3, 4, "人员"),
            _cell(4, 5, "机房外周围剂量当量率"),
            _cell(5, 6, "年工作负荷"),
            _cell(6, 7, "居留因子"),
            _cell(7, 8, "年受照剂量"),
        ],
    }
    bands: List[Dict[str, Any]] = [header]
    band_i = 1
    last_floor = ""
    n_person = len(TABLE12_PERSON_ROWS)
    for seq, room in enumerate(rooms, start=1):
        room_name = str(room.get("room") or "")
        floor = str(room.get("floor") or "")
        show_floor = floor if floor != last_floor else ""
        if floor:
            last_floor = floor
        hours = float(room.get("workload_h") or 0)
        rate = preserved_rates.get(room_name)
        if rate is None:
            rate = float(
                DEFAULT_DOSE_RATE_BY_ROOM.get(room_name, DEFAULT_DOSE_RATE_USV_H)
            )
        for pi, (person, occ_txt, occ) in enumerate(TABLE12_PERSON_ROWS):
            cells: List[Dict[str, Any]] = []
            if pi == 0:
                cells.append(_cell(0, 1, str(seq), row_span=n_person))
                cells.append(_cell(1, 2, room_name, row_span=n_person, align="left"))
                cells.append(_cell(2, 3, show_floor, row_span=n_person, align="left"))
            cells.append(_cell(3, 4, person, cell_role="person"))
            cells.append(
                _cell(
                    4,
                    5,
                    format_dose_rate(rate),
                    cell_role="dose_rate",
                    rate_usv_h=rate,
                )
            )
            cells.append(
                _cell(
                    5,
                    6,
                    format_workload_hours(hours),
                    cell_role="workload",
                    workload_h=hours,
                )
            )
            cells.append(
                _cell(
                    6,
                    7,
                    occ_txt,
                    cell_role="occupancy",
                    occupancy=occ,
                )
            )
            dose = calc_annual_dose_msv(rate, hours, occ)
            cells.append(
                _cell(
                    7,
                    8,
                    format_dose_msv(dose),
                    cell_role="annual_dose",
                    formula=TABLE12_FORMULA,
                )
            )
            bands.append({"band": band_i, "cells": cells})
            band_i += 1

    bands.append({"band": band_i, "cells": [_cell(0, 8, TABLE12_NOTE, align="left")]})
    return {
        "schema": "embedded_generic_table/v1",
        "title": TABLE12_TITLE,
        "source": "health_impact",
        "from_info_sheet": from_sheet,
        "column_count": 8,
        "column_fractions": [0.0, 0.06, 0.18, 0.34, 0.46, 0.62, 0.74, 0.84, 1.0],
        "band_count": len(bands),
        "row_min_height_pt": 18.0,
        "repeat_header_on_new_page": True,
        "header_band_count": 1,
        "dose_formula": TABLE12_FORMULA,
        "note": TABLE12_NOTE,
        "device_count": len(rooms),
        "rows": bands,
    }


# ─── 表13～15：介入放射学 ───────────────────────────────────────────

TABLE13_TITLE_TMPL = "表13 本项目{n}台介入设备（介入放射学）预期工作量"
TABLE13_FORMULA = (
    "单人年最大工作负荷=单例手术平均曝光时间×年工作负荷×设备台数÷工作人员批次数"
)
TABLE13_NOTE = (
    "注：①根据建设单位提供的信息，ERCP工作人员可分为2批，DSA工作人员可分为2批/台。\n"
    "②单人年最大工作负荷为单例手术平均曝光时间×年工作负荷×设备台数÷工作人员批次数。"
)

TABLE14_TITLE = "表14 本项目透视防护区工作人员年有效剂量估算结果一览表"
TABLE14_FORMULA_UNPROT = (
    "工作人员年有效剂量(mSv)=未穿戴防护用品的剂量率(μSv/h)×年出束时间(h)/1000"
)
TABLE14_FORMULA_PROT = (
    "工作人员年有效剂量(mSv)=(0.79×穿戴防护用品后的剂量率(μSv/h)"
    "+0.051×未穿戴防护用品的剂量率(μSv/h))×年出束时间(h)/1000"
)
TABLE14_NOTE = (
    "注：1.未穿戴防护用品时的剂量率：在确保铅屏风和床侧铅挂帘等防护设施正常使用的情况下，"
    "透视防护区工作人员位置周围剂量当量率取标准限值400μSv/h；\n"
    "2.穿戴防护用品后的剂量率：指工作人员正常使用铅悬挂防护屏和铅防护吊帘下，"
    "工作人员穿戴0.5mmPb防护用品，参考《放射诊断放射防护要求》（GBZ 130-2020）附录C.1.2"
    "的参数和计算公式，在125kV（散射）条件下，0.5mmPb的个人防护用品屏蔽效率约为93%，"
    "则穿防护用品后的剂量率为400×（1-93%）=28μSv/h；\n"
    "3.（1）未穿戴防护用品：工作人员年有效剂量（mSv）=未穿戴防护用品的剂量率（μSv/h）"
    "×年出束时间（h）/1000。\n"
    "（2）穿戴防护用品后：工作人员年有效剂量（mSv）=（0.79×穿戴防护用品后的剂量率（μSv/h）"
    "+0.051×未穿戴防护用品的剂量率（μSv/h））×年出束时间（h）/1000；"
    "依据GBZ 128-2019《职业性外照射个人监测规范》6.2.4。"
)

TABLE15_TITLE = "表15 本项目ERCP机房外和DSA机房外人员年有效剂量估算结果表"
TABLE15_FORMULA = (
    "年有效剂量(mSv)=机房外周围剂量当量率(μSv/h)×年工作负荷(h)×居留因子/1000"
)
TABLE15_NOTE = (
    "注：年有效剂量（mSv）=机房外周围剂量当量率（取机房外周围剂量当量率限值2.5μSv/h）"
    "×年工作负荷（摄影模式+透视模式）（h）×居留因子/1000。"
    "出于保守估算考虑，工作人员居留因子保守取1，公众居留因子取1/4。"
)

# key -> modes: (time_label, time_hours, mode_role)
IntervMode = Tuple[str, float, str]
INTERV_DEVICE_SPECS: Dict[str, Dict[str, Any]] = {
    "ERCP": {
        "label": "ERCP设备",
        "default_count": 1,
        "batches_fixed": 2,  # 总共 2 批
        "batches_per_device": None,
        "cases_per_year": 600,
        "modes": [
            ("10分钟（同室透视）", 10.0 / 60.0, "fluoro"),
            ("1分钟（隔室摄影）", 1.0 / 60.0, "radio"),
        ],
    },
    "DSA": {
        "label": "DSA",
        "default_count": 2,
        "batches_fixed": None,
        "batches_per_device": 2,  # 2批/台
        "cases_per_year": 600,
        "modes": [
            ("20分钟（同室透视）", 20.0 / 60.0, "fluoro"),
            ("1分钟（隔室摄影+类CT模式）", 1.0 / 60.0, "radio"),
        ],
    },
}

TABLE14_POSITIONS: List[Tuple[str, bool]] = [
    ("全身", True),
    ("眼晶体", True),
    ("四肢和皮肤", False),  # 无穿戴后剂量
]

TABLE14_RATE_UNPROT = 400.0
TABLE14_RATE_PROT = 28.0
TABLE14_PROT_COEF_PROT = 0.79
TABLE14_PROT_COEF_UNPROT = 0.051

TABLE15_OUTSIDE_RATE = 2.5
# (人员, 工作负荷来源: fluoro | fluoro+radio, 居留因子显示, 居留因子)
TABLE15_PERSON_ROWS: List[Tuple[str, str, str, float]] = [
    ("介入手术医师", "fluoro", "1", 1.0),
    ("影像医师、技师、护士", "fluoro+radio", "1", 1.0),
    ("公众", "fluoro+radio", "1/4", 0.25),
]


def interventional_devices_from_list(
    devices: Optional[Sequence[Dict[str, Any]]],
) -> Dict[str, int]:
    """统计信息表中 ERCP / DSA 台数。"""
    counts = {"ERCP": 0, "DSA": 0}
    for d in devices or []:
        name = str(d.get("name") or "").strip()
        place = str(d.get("place") or d.get("location") or "").strip()
        key = _device_key(name, place)
        if key in counts:
            counts[key] += 1
    return counts


def parse_cases_per_year(text: str) -> float:
    """解析「600例手术/年/每台设备」→ 600。"""
    s = str(text or "").replace(",", "").replace("，", "")
    m = re.search(r"([\d.]+)", s)
    return float(m.group(1)) if m else 0.0


def parse_device_count(text: str) -> float:
    s = str(text or "").strip()
    m = re.search(r"([\d.]+)", s)
    return float(m.group(1)) if m else 0.0


def parse_batch_count(text: str) -> float:
    s = str(text or "").replace("批", "").strip()
    m = re.search(r"([\d.]+)", s)
    return float(m.group(1)) if m else 0.0


def parse_exposure_hours(text: str) -> float:
    """解析「10分钟… / 20min」→ 小时。"""
    s = str(text or "").strip().lower().replace(" ", "")
    m = re.search(r"([\d.]+)\s*(分钟|min|分)", s)
    if m:
        return float(m.group(1)) / 60.0
    m = re.search(r"([\d.]+)\s*(小时|h|hr)", s)
    if m:
        return float(m.group(1))
    sec = parse_beam_time_seconds(s)
    return sec / 3600.0 if sec > 0 else 0.0


def format_cases_per_year(n: float) -> str:
    if n <= 0:
        return ""
    if abs(n - round(n)) < 1e-6:
        return f"{int(round(n))}例手术/年/每台设备"
    return f"{n:g}例手术/年/每台设备"


def format_batches(n: float) -> str:
    if n <= 0:
        return ""
    if abs(n - round(n)) < 1e-6:
        return f"{int(round(n))}批"
    return f"{n:g}批"


def format_max_load_hours(hours: float) -> str:
    if hours <= 0:
        return ""
    if abs(hours - 50.0) < 0.05:
        return "50.0小时"
    if abs(hours - round(hours)) < 1e-6:
        return f"{int(round(hours))}小时"
    return f"{hours:.1f}小时"


def calc_interv_max_load_h(
    time_h: float, cases: float, device_count: float, batches: float
) -> float:
    if time_h <= 0 or cases <= 0 or device_count <= 0 or batches <= 0:
        return 0.0
    return time_h * cases * device_count / batches


def format_table14_dose(dose: float) -> str:
    if dose < 0:
        return "/"
    return f"{dose:.2f}"


def calc_table14_dose_unprot(rate_unprot: float, hours: float) -> float:
    if rate_unprot <= 0 or hours <= 0:
        return 0.0
    return rate_unprot * hours / 1000.0


def calc_table14_dose_prot(
    rate_prot: float, rate_unprot: float, hours: float
) -> float:
    if hours <= 0:
        return 0.0
    return (
        (TABLE14_PROT_COEF_PROT * rate_prot + TABLE14_PROT_COEF_UNPROT * rate_unprot)
        * hours
        / 1000.0
    )


def _interv_inputs_from_table13(table: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """从表13读取各设备台数/年例数/批次数（重建保留）。"""
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(table, dict):
        return out
    cur_key = ""
    for band in table.get("rows") or []:
        if not isinstance(band, dict):
            continue
        cells = {
            int(c.get("c0") or 0): c
            for c in (band.get("cells") or [])
            if isinstance(c, dict)
        }
        t0 = str(cells.get(0, {}).get("text") or "").strip()
        if t0.startswith("设备") or t0.startswith("注"):
            continue
        if t0:
            if "ERCP" in t0:
                cur_key = "ERCP"
            elif "DSA" in t0:
                cur_key = "DSA"
            else:
                cur_key = ""
            if cur_key:
                out.setdefault(cur_key, {})
                if 1 in cells:
                    out[cur_key]["device_count"] = parse_device_count(
                        str(cells[1].get("text") or "")
                    )
                if 3 in cells:
                    out[cur_key]["cases"] = parse_cases_per_year(
                        str(cells[3].get("text") or "")
                    )
                if 4 in cells:
                    out[cur_key]["batches"] = parse_batch_count(
                        str(cells[4].get("text") or "")
                    )
    return out


def loads_from_table13(table13: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """
    从表13提取各设备 fluor/radio 单人年最大工作负荷（小时）。
    返回 {ERCP: {fluoro, radio, total}, DSA: {...}}
    """
    out: Dict[str, Dict[str, float]] = {
        "ERCP": {"fluoro": 0.0, "radio": 0.0, "total": 0.0},
        "DSA": {"fluoro": 0.0, "radio": 0.0, "total": 0.0},
    }
    if not isinstance(table13, dict):
        return out
    cur_key = ""
    for band in table13.get("rows") or []:
        if not isinstance(band, dict):
            continue
        cells = {
            int(c.get("c0") or 0): c
            for c in (band.get("cells") or [])
            if isinstance(c, dict)
        }
        t0 = str(cells.get(0, {}).get("text") or "").strip()
        if t0.startswith("设备") or t0.startswith("注"):
            continue
        if t0:
            if "ERCP" in t0:
                cur_key = "ERCP"
            elif "DSA" in t0:
                cur_key = "DSA"
        if not cur_key:
            continue
        mode_cell = cells.get(2) or {}
        load_cell = cells.get(5) or {}
        role = str(mode_cell.get("mode_role") or "")
        if not role:
            mt = str(mode_cell.get("text") or "")
            role = "fluoro" if "透视" in mt else ("radio" if "摄影" in mt else "")
        hours = float(load_cell.get("max_load_h") or 0) or parse_workload_hours(
            str(load_cell.get("text") or "").replace("小时", "h")
        )
        if role == "fluoro":
            out[cur_key]["fluoro"] = hours
        elif role == "radio":
            out[cur_key]["radio"] = hours
        out[cur_key]["total"] = out[cur_key]["fluoro"] + out[cur_key]["radio"]
    return out


def recalc_table13_loads(table: Dict[str, Any]) -> Dict[str, Any]:
    """重算表13「单人年最大工作负荷」。"""
    if not isinstance(table, dict):
        return table
    cur_count = 0.0
    cur_cases = 0.0
    cur_batches = 0.0
    for band in table.get("rows") or []:
        if not isinstance(band, dict):
            continue
        cells = list(band.get("cells") or [])
        by_c = {int(c.get("c0") or 0): c for c in cells if isinstance(c, dict)}
        t0 = str(by_c.get(0, {}).get("text") or "").strip()
        if t0.startswith("设备") or t0.startswith("注"):
            continue
        if 1 in by_c:
            cur_count = parse_device_count(str(by_c[1].get("text") or ""))
            by_c[1]["cell_role"] = "device_count"
        if 3 in by_c:
            cur_cases = parse_cases_per_year(str(by_c[3].get("text") or ""))
            by_c[3]["cell_role"] = "cases"
            by_c[3]["cases_per_year"] = cur_cases
        if 4 in by_c:
            cur_batches = parse_batch_count(str(by_c[4].get("text") or ""))
            by_c[4]["cell_role"] = "batches"
            by_c[4]["batch_count"] = cur_batches
        time_cell = by_c.get(2)
        load_cell = by_c.get(5)
        if not time_cell or not load_cell:
            continue
        time_h = float(time_cell.get("time_hours") or 0) or parse_exposure_hours(
            str(time_cell.get("text") or "")
        )
        hours = calc_interv_max_load_h(time_h, cur_cases, cur_count, cur_batches)
        load_cell["text"] = format_max_load_hours(hours)
        load_cell["max_load_h"] = hours
        load_cell["formula"] = TABLE13_FORMULA
        load_cell["cell_role"] = "max_load"
        time_cell["time_hours"] = time_h
        time_cell["cell_role"] = "exposure_time"
    table["workload_formula"] = TABLE13_FORMULA
    return table


def build_table13(
    devices: Optional[Sequence[Dict[str, Any]]] = None,
    *,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    表13：介入设备预期工作量。
    - 设备台数：信息表 ERCP/DSA 计数（可改）
    - 曝光时间：固定
    - 年工作负荷（例数）、批次数：默认可改，重建保留
    - 单人年最大工作负荷：公式自动算
    """
    counts = interventional_devices_from_list(devices)
    from_sheet = any(counts.values())
    preserved = _interv_inputs_from_table13(existing)
    total_devices = 0
    order = ["ERCP", "DSA"]
    header = {
        "band": 0,
        "cells": [
            _cell(0, 1, "设备类型（备注）"),
            _cell(1, 2, "设备台数"),
            _cell(2, 3, "单例手术平均曝光时间"),
            _cell(3, 4, "年工作负荷"),
            _cell(4, 5, "工作人员批次数"),
            _cell(5, 6, "单人年最大工作负荷"),
        ],
    }
    bands: List[Dict[str, Any]] = [header]
    band_i = 1
    for key in order:
        spec = INTERV_DEVICE_SPECS[key]
        # 信息表未检出时用默认台数
        n = int(counts.get(key) or 0)
        if n <= 0:
            n = int(spec["default_count"])
        prev = preserved.get(key) or {}
        if prev.get("device_count"):
            n = int(prev["device_count"])
        cases = float(prev.get("cases") or spec["cases_per_year"])
        if prev.get("batches"):
            batches = float(prev["batches"])
        elif spec.get("batches_fixed") is not None:
            batches = float(spec["batches_fixed"])
        else:
            batches = float(spec["batches_per_device"] or 1) * n
        total_devices += n
        modes: List[IntervMode] = list(spec["modes"])
        n_modes = len(modes)
        for mi, (time_label, time_h, mode_role) in enumerate(modes):
            cells: List[Dict[str, Any]] = []
            if mi == 0:
                cells.append(_cell(0, 1, str(spec["label"]), row_span=n_modes, align="left"))
                cells.append(
                    _cell(
                        1,
                        2,
                        str(n),
                        row_span=n_modes,
                        cell_role="device_count",
                    )
                )
            cells.append(
                _cell(
                    2,
                    3,
                    time_label,
                    cell_role="exposure_time",
                    time_hours=time_h,
                    mode_role=mode_role,
                )
            )
            if mi == 0:
                cells.append(
                    _cell(
                        3,
                        4,
                        format_cases_per_year(cases),
                        row_span=n_modes,
                        cell_role="cases",
                        cases_per_year=cases,
                    )
                )
                cells.append(
                    _cell(
                        4,
                        5,
                        format_batches(batches),
                        row_span=n_modes,
                        cell_role="batches",
                        batch_count=batches,
                    )
                )
            hours = calc_interv_max_load_h(time_h, cases, float(n), batches)
            cells.append(
                _cell(
                    5,
                    6,
                    format_max_load_hours(hours),
                    cell_role="max_load",
                    max_load_h=hours,
                    formula=TABLE13_FORMULA,
                    mode_role=mode_role,
                )
            )
            bands.append({"band": band_i, "cells": cells})
            band_i += 1

    bands.append({"band": band_i, "cells": [_cell(0, 6, TABLE13_NOTE, align="left")]})
    title = TABLE13_TITLE_TMPL.format(n=total_devices or 3)
    return {
        "schema": "embedded_generic_table/v1",
        "title": title,
        "source": "health_impact",
        "from_info_sheet": from_sheet,
        "column_count": 6,
        "column_fractions": [0.0, 0.14, 0.24, 0.48, 0.66, 0.80, 1.0],
        "band_count": len(bands),
        "row_min_height_pt": 18.0,
        "repeat_header_on_new_page": True,
        "header_band_count": 1,
        "workload_formula": TABLE13_FORMULA,
        "note": TABLE13_NOTE,
        "device_count": total_devices,
        "rows": bands,
    }


def recalc_table14_doses(table: Dict[str, Any]) -> Dict[str, Any]:
    """重算表14年有效剂量两列。"""
    if not isinstance(table, dict):
        return table
    cur_hours = 0.0
    for band in table.get("rows") or []:
        if not isinstance(band, dict):
            continue
        cells = list(band.get("cells") or [])
        by_c = {int(c.get("c0") or 0): c for c in cells if isinstance(c, dict)}
        t0 = str(by_c.get(0, {}).get("text") or "").strip()
        t1 = str(by_c.get(1, {}).get("text") or "").strip()
        if t0.startswith("射线") or t0.startswith("注") or t1.startswith("未穿戴"):
            continue
        if 4 in by_c:
            cur_hours = float(by_c[4].get("workload_h") or 0) or parse_workload_hours(
                str(by_c[4].get("text") or "")
            )
            by_c[4]["workload_h"] = cur_hours
            by_c[4]["cell_role"] = "workload"
        rate_u_cell = by_c.get(2)
        rate_p_cell = by_c.get(3)
        dose_u_cell = by_c.get(5)
        dose_p_cell = by_c.get(6)
        if not rate_u_cell or not dose_u_cell or not dose_p_cell:
            continue
        rate_u = float(rate_u_cell.get("rate_usv_h") or 0) or parse_dose_rate_usv_h(
            str(rate_u_cell.get("text") or "")
        )
        rate_p_txt = str(rate_p_cell.get("text") or "").strip() if rate_p_cell else "/"
        has_prot = rate_p_txt not in ("/", "—", "-", "")
        rate_p = (
            float(rate_p_cell.get("rate_usv_h") or 0)
            or parse_dose_rate_usv_h(rate_p_txt)
            if has_prot and rate_p_cell
            else 0.0
        )
        dose_u = calc_table14_dose_unprot(rate_u, cur_hours)
        dose_u_cell["text"] = format_table14_dose(dose_u)
        dose_u_cell["formula"] = TABLE14_FORMULA_UNPROT
        dose_u_cell["cell_role"] = "dose_unprot"
        if has_prot:
            dose_p = calc_table14_dose_prot(rate_p, rate_u, cur_hours)
            dose_p_cell["text"] = format_table14_dose(dose_p)
            dose_p_cell["formula"] = TABLE14_FORMULA_PROT
        else:
            dose_p_cell["text"] = "/"
            dose_p_cell["formula"] = TABLE14_FORMULA_PROT
        dose_p_cell["cell_role"] = "dose_prot"
        rate_u_cell["rate_usv_h"] = rate_u
        rate_u_cell["cell_role"] = "rate_unprot"
        if rate_p_cell:
            if has_prot:
                rate_p_cell["rate_usv_h"] = rate_p
            rate_p_cell["cell_role"] = "rate_prot"
    table["dose_formula"] = TABLE14_FORMULA_PROT
    return table


def apply_table13_to_table14(
    table14: Dict[str, Any],
    table13: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """用表13透视模式单人最大负荷刷新表14工作负荷列。"""
    if not isinstance(table14, dict):
        return table14
    loads = loads_from_table13(table13)
    cur_key = ""
    for band in table14.get("rows") or []:
        if not isinstance(band, dict):
            continue
        cells = {
            int(c.get("c0") or 0): c
            for c in (band.get("cells") or [])
            if isinstance(c, dict)
        }
        t0 = str(cells.get(0, {}).get("text") or "").strip()
        if t0.startswith("射线") or t0.startswith("注"):
            continue
        if t0:
            if "ERCP" in t0:
                cur_key = "ERCP"
            elif "DSA" in t0:
                cur_key = "DSA"
        wl = cells.get(4)
        if cur_key and wl:
            h = float((loads.get(cur_key) or {}).get("fluoro") or 0)
            if h > 0:
                wl["text"] = f"{h:.1f}" if abs(h - round(h)) > 1e-6 else f"{h:g}"
                # 与样例一致：50.0 / 100
                if abs(h - 50.0) < 0.05:
                    wl["text"] = "50.0"
                elif abs(h - 100.0) < 0.05:
                    wl["text"] = "100"
                wl["workload_h"] = h
                wl["cell_role"] = "workload"
    return recalc_table14_doses(table14)


def build_table14(
    *,
    table13: Optional[Dict[str, Any]] = None,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """表14：透视防护区工作人员年有效剂量（负荷来自表13透视模式）。"""
    t13 = table13 if isinstance(table13, dict) and table13.get("rows") else build_table13()
    loads = loads_from_table13(t13)
    # 两行表头
    header0 = {
        "band": 0,
        "cells": [
            _cell(0, 1, "射线装置\n（备注）", row_span=2),
            _cell(1, 2, "受照位置", row_span=2),
            _cell(2, 3, "未穿戴防护用品时的剂量率（μSv/h）", row_span=2),
            _cell(3, 4, "穿戴防护用品后的剂量率（μSv/h）", row_span=2),
            _cell(4, 5, "机房内单人年最大工作负荷（h）", row_span=2),
            _cell(5, 7, "年有效剂量（mSv）"),
        ],
    }
    header1 = {
        "band": 1,
        "cells": [
            _cell(5, 6, "未穿戴防护用品"),
            _cell(6, 7, "穿戴防护用品后"),
        ],
    }
    bands: List[Dict[str, Any]] = [header0, header1]
    band_i = 2
    for key in ("ERCP", "DSA"):
        label = INTERV_DEVICE_SPECS[key]["label"]
        hours = float((loads.get(key) or {}).get("fluoro") or 0)
        n_pos = len(TABLE14_POSITIONS)
        for pi, (pos, has_prot) in enumerate(TABLE14_POSITIONS):
            cells: List[Dict[str, Any]] = []
            if pi == 0:
                cells.append(_cell(0, 1, label, row_span=n_pos, align="left"))
            cells.append(_cell(1, 2, pos))
            cells.append(
                _cell(
                    2,
                    3,
                    f"{int(TABLE14_RATE_UNPROT)}"
                    if abs(TABLE14_RATE_UNPROT - round(TABLE14_RATE_UNPROT)) < 1e-9
                    else f"{TABLE14_RATE_UNPROT:g}",
                    cell_role="rate_unprot",
                    rate_usv_h=TABLE14_RATE_UNPROT,
                )
            )
            if has_prot:
                cells.append(
                    _cell(
                        3,
                        4,
                        f"{int(TABLE14_RATE_PROT)}"
                        if abs(TABLE14_RATE_PROT - round(TABLE14_RATE_PROT)) < 1e-9
                        else f"{TABLE14_RATE_PROT:g}",
                        cell_role="rate_prot",
                        rate_usv_h=TABLE14_RATE_PROT,
                    )
                )
            else:
                cells.append(_cell(3, 4, "/", cell_role="rate_prot"))
            if pi == 0:
                wl_txt = "50.0" if abs(hours - 50) < 0.05 else (
                    "100" if abs(hours - 100) < 0.05 else format_workload_hours(hours).replace("h", "")
                )
                cells.append(
                    _cell(
                        4,
                        5,
                        wl_txt if hours > 0 else "",
                        row_span=n_pos,
                        cell_role="workload",
                        workload_h=hours,
                    )
                )
            dose_u = calc_table14_dose_unprot(TABLE14_RATE_UNPROT, hours)
            cells.append(
                _cell(
                    5,
                    6,
                    format_table14_dose(dose_u),
                    cell_role="dose_unprot",
                    formula=TABLE14_FORMULA_UNPROT,
                )
            )
            if has_prot:
                dose_p = calc_table14_dose_prot(
                    TABLE14_RATE_PROT, TABLE14_RATE_UNPROT, hours
                )
                cells.append(
                    _cell(
                        6,
                        7,
                        format_table14_dose(dose_p),
                        cell_role="dose_prot",
                        formula=TABLE14_FORMULA_PROT,
                    )
                )
            else:
                cells.append(_cell(6, 7, "/", cell_role="dose_prot"))
            bands.append({"band": band_i, "cells": cells})
            band_i += 1

    bands.append({"band": band_i, "cells": [_cell(0, 7, TABLE14_NOTE, align="left")]})
    _ = existing  # 剂量率固定默认；负荷始终跟表13
    return {
        "schema": "embedded_generic_table/v1",
        "title": TABLE14_TITLE,
        "source": "health_impact",
        "from_info_sheet": bool(t13.get("from_info_sheet")),
        "column_count": 7,
        "column_fractions": [0.0, 0.12, 0.24, 0.40, 0.56, 0.70, 0.85, 1.0],
        "band_count": len(bands),
        "row_min_height_pt": 18.0,
        "repeat_header_on_new_page": True,
        "header_band_count": 2,
        "dose_formula": TABLE14_FORMULA_PROT,
        "note": TABLE14_NOTE,
        "rows": bands,
    }


def recalc_table15_doses(table: Dict[str, Any]) -> Dict[str, Any]:
    """重算表15年有效剂量。"""
    if not isinstance(table, dict):
        return table
    for band in table.get("rows") or []:
        if not isinstance(band, dict):
            continue
        cells = list(band.get("cells") or [])
        by_c = {int(c.get("c0") or 0): c for c in cells if isinstance(c, dict)}
        t0 = str(by_c.get(0, {}).get("text") or "").strip()
        if t0.startswith("射线") or t0.startswith("注"):
            continue
        rate_cell = by_c.get(3)
        wl_cell = by_c.get(4)
        occ_cell = by_c.get(5)
        dose_cell = by_c.get(6)
        if not rate_cell or not wl_cell or not occ_cell or not dose_cell:
            continue
        rate = float(rate_cell.get("rate_usv_h") or 0) or parse_dose_rate_usv_h(
            str(rate_cell.get("text") or "")
        )
        hours = float(wl_cell.get("workload_h") or 0) or parse_workload_hours(
            str(wl_cell.get("text") or "")
        )
        occ = float(occ_cell.get("occupancy") or 0) or parse_occupancy_factor(
            str(occ_cell.get("text") or "")
        )
        dose = calc_annual_dose_msv(rate, hours, occ)
        dose_cell["text"] = format_dose_msv(dose)
        dose_cell["formula"] = TABLE15_FORMULA
        dose_cell["cell_role"] = "annual_dose"
        rate_cell["rate_usv_h"] = rate
        rate_cell["cell_role"] = "dose_rate"
        wl_cell["workload_h"] = hours
        wl_cell["cell_role"] = "workload"
        occ_cell["occupancy"] = occ
        occ_cell["cell_role"] = "occupancy"
    table["dose_formula"] = TABLE15_FORMULA
    return table


def apply_table13_to_table15(
    table15: Dict[str, Any],
    table13: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """用表13 fluor/radio 负荷刷新表15年工作负荷。"""
    if not isinstance(table15, dict):
        return table15
    loads = loads_from_table13(table13)
    cur_key = ""
    for band in table15.get("rows") or []:
        if not isinstance(band, dict):
            continue
        cells = {
            int(c.get("c0") or 0): c
            for c in (band.get("cells") or [])
            if isinstance(c, dict)
        }
        t0 = str(cells.get(0, {}).get("text") or "").strip()
        if t0.startswith("射线") or t0.startswith("注"):
            continue
        if t0:
            if "ERCP" in t0:
                cur_key = "ERCP"
            elif "DSA" in t0:
                cur_key = "DSA"
        person = str(cells.get(2, {}).get("text") or "")
        wl = cells.get(4)
        if not cur_key or not wl:
            continue
        ld = loads.get(cur_key) or {}
        if "介入手术医师" in person:
            h = float(ld.get("fluoro") or 0)
        else:
            h = float(ld.get("total") or 0)
        if h > 0:
            wl["text"] = f"{int(round(h))}h" if abs(h - round(h)) < 0.05 else f"{h:.1f}h"
            # 样例：50h / 55h / 100h / 105h
            if abs(h - round(h)) < 0.05:
                wl["text"] = f"{int(round(h))}h"
            wl["workload_h"] = h
            wl["cell_role"] = "workload"
    return recalc_table15_doses(table15)


def build_table15(
    *,
    table13: Optional[Dict[str, Any]] = None,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """表15：机房外人员年有效剂量（负荷来自表13）。"""
    t13 = table13 if isinstance(table13, dict) and table13.get("rows") else build_table13()
    loads = loads_from_table13(t13)
    header = {
        "band": 0,
        "cells": [
            _cell(0, 1, "射线装置\n（备注）"),
            _cell(1, 2, "受照位置"),
            _cell(2, 3, "放射工作人员"),
            _cell(3, 4, "机房外周围剂量当量率"),
            _cell(4, 5, "机房外年工作负荷"),
            _cell(5, 6, "居留因子"),
            _cell(6, 7, "年有效剂量"),
        ],
    }
    bands: List[Dict[str, Any]] = [header]
    band_i = 1
    n_person = len(TABLE15_PERSON_ROWS)
    for key in ("ERCP", "DSA"):
        label = INTERV_DEVICE_SPECS[key]["label"]
        ld = loads.get(key) or {}
        for pi, (person, src, occ_txt, occ) in enumerate(TABLE15_PERSON_ROWS):
            cells: List[Dict[str, Any]] = []
            if pi == 0:
                cells.append(_cell(0, 1, label, row_span=n_person, align="left"))
                cells.append(_cell(1, 2, "全身", row_span=n_person))
            cells.append(_cell(2, 3, person, align="left", cell_role="person"))
            cells.append(
                _cell(
                    3,
                    4,
                    format_dose_rate(TABLE15_OUTSIDE_RATE),
                    cell_role="dose_rate",
                    rate_usv_h=TABLE15_OUTSIDE_RATE,
                )
            )
            if src == "fluoro":
                hours = float(ld.get("fluoro") or 0)
            else:
                hours = float(ld.get("total") or 0)
            wl_txt = f"{int(round(hours))}h" if abs(hours - round(hours)) < 0.05 else f"{hours:.1f}h"
            cells.append(
                _cell(
                    4,
                    5,
                    wl_txt if hours > 0 else "",
                    cell_role="workload",
                    workload_h=hours,
                )
            )
            cells.append(
                _cell(5, 6, occ_txt, cell_role="occupancy", occupancy=occ)
            )
            dose = calc_annual_dose_msv(TABLE15_OUTSIDE_RATE, hours, occ)
            cells.append(
                _cell(
                    6,
                    7,
                    format_dose_msv(dose),
                    cell_role="annual_dose",
                    formula=TABLE15_FORMULA,
                )
            )
            bands.append({"band": band_i, "cells": cells})
            band_i += 1

    bands.append({"band": band_i, "cells": [_cell(0, 7, TABLE15_NOTE, align="left")]})
    _ = existing
    return {
        "schema": "embedded_generic_table/v1",
        "title": TABLE15_TITLE,
        "source": "health_impact",
        "from_info_sheet": bool(t13.get("from_info_sheet")),
        "column_count": 7,
        "column_fractions": [0.0, 0.12, 0.22, 0.42, 0.58, 0.72, 0.84, 1.0],
        "band_count": len(bands),
        "row_min_height_pt": 18.0,
        "repeat_header_on_new_page": True,
        "header_band_count": 1,
        "dose_formula": TABLE15_FORMULA,
        "note": TABLE15_NOTE,
        "rows": bands,
    }


def sync_tables_from_table13(
    tables: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """表13变更后同步表14、表15。"""
    if not tables:
        return tables
    t13 = tables[2] if len(tables) > 2 else None
    if len(tables) > 3 and isinstance(tables[3], dict):
        tables[3] = apply_table13_to_table14(tables[3], t13)
    if len(tables) > 4 and isinstance(tables[4], dict):
        tables[4] = apply_table13_to_table15(tables[4], t13)
    return tables


def build_normal_condition_tables(
    devices: Optional[Sequence[Dict[str, Any]]] = None,
    *,
    existing_tables: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    existing = list(existing_tables or [])
    t11_exist = existing[0] if existing and isinstance(existing[0], dict) else None
    t12_exist = existing[1] if len(existing) > 1 and isinstance(existing[1], dict) else None
    t13_exist = existing[2] if len(existing) > 2 and isinstance(existing[2], dict) else None
    t11 = build_table11(devices, existing=t11_exist)
    t12 = build_table12(devices, table11=t11, existing=t12_exist)
    t13 = build_table13(devices, existing=t13_exist)
    t14 = build_table14(table13=t13)
    t15 = build_table15(table13=t13)
    return [t11, t12, t13, t14, t15]


def assemble_normal_condition(
    devices: Optional[List[Dict[str, Any]]] = None,
    *,
    include_table: bool = True,
    existing_tables: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    返回:
      normal_condition: （1）导语+表11+表12+小结 + （2）导语+表13+表14+表15+结论
      normal_condition_tables: [表11..表15]
      interventional_after: 由表14/15生成的介入结论（可选）
    """
    imaging_intro = _load_fixed(
        "health_impact/imaging_intro.md", DEFAULT_IMAGING_INTRO
    )
    imaging_after = _load_fixed(
        "health_impact/imaging_after.md", DEFAULT_IMAGING_AFTER
    )
    interv_intro = _load_fixed(
        "health_impact/interventional_intro.md", DEFAULT_INTERV_INTRO
    )

    tables: List[Dict[str, Any]] = []
    if include_table:
        tables = build_normal_condition_tables(
            devices, existing_tables=existing_tables
        )
        interv_after = build_interventional_after(
            tables[3] if len(tables) > 3 else None,
            tables[4] if len(tables) > 4 else None,
        )
    else:
        interv_after = _load_fixed(
            "health_impact/interventional_after.md", DEFAULT_INTERV_AFTER
        )

    parts = [
        imaging_intro,
        TABLE_MARK,
        TABLE_MARK,
        imaging_after,
        "",
        interv_intro,
        TABLE_MARK,
        TABLE_MARK,
        TABLE_MARK,
        interv_after,
    ]
    text = "\n\n".join(p for p in parts if p is not None).strip() + "\n"
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")

    out: Dict[str, Any] = {
        "normal_condition": text,
        "interventional_after": interv_after,
    }
    if include_table:
        out["normal_condition_tables"] = tables
        n = int((tables[0] or {}).get("device_count") or 13)
        out["normal_condition"] = text.replace("13台设备", f"{n}台设备")
        n_interv = int((tables[2] or {}).get("device_count") or 3)
        out["normal_condition"] = out["normal_condition"].replace(
            "3台介入设备", f"{n_interv}台介入设备"
        )
    else:
        out["normal_condition_tables"] = []
    return out


def parse_dose_number(text: str) -> Optional[float]:
    """解析「2.13 / 2.13mSv / /」→ float 或 None。"""
    s = str(text or "").strip()
    if not s or s in ("/", "—", "-", "／"):
        return None
    m = re.search(r"([\d.]+)", s.replace(",", ""))
    return float(m.group(1)) if m else None


def _round_half_up(value: float, places: int) -> float:
    from decimal import ROUND_HALF_UP, Decimal

    q = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))


def _fmt_msv2(v: float) -> str:
    return f"{_round_half_up(v, 2):.2f}mSv"


def _fmt_msv3(v: float) -> str:
    return f"{_round_half_up(v, 3):.3f}mSv"


def summarize_interv_doses_from_tables(
    table14: Optional[Dict[str, Any]],
    table15: Optional[Dict[str, Any]],
) -> Dict[str, Dict[str, float]]:
    """
    从表14/表15提取结论用剂量。
    返回 {ERCP|DSA: {inside_prot, eye_prot, skin_unprot, outside_physician, public}}
    """
    out: Dict[str, Dict[str, float]] = {
        "ERCP": {},
        "DSA": {},
    }
    # 表14：全身穿戴后 / 眼晶体穿戴后 / 四肢皮肤未穿戴
    if isinstance(table14, dict):
        cur = ""
        for band in table14.get("rows") or []:
            if not isinstance(band, dict):
                continue
            cells = {
                int(c.get("c0") or 0): c
                for c in (band.get("cells") or [])
                if isinstance(c, dict)
            }
            t0 = str(cells.get(0, {}).get("text") or "").strip()
            if t0.startswith("射线") or t0.startswith("注"):
                continue
            if t0:
                if "ERCP" in t0:
                    cur = "ERCP"
                elif "DSA" in t0:
                    cur = "DSA"
            if not cur:
                continue
            pos = str(cells.get(1, {}).get("text") or "").strip()
            dose_u = parse_dose_number(str(cells.get(5, {}).get("text") or ""))
            dose_p = parse_dose_number(str(cells.get(6, {}).get("text") or ""))
            if pos == "全身" and dose_p is not None:
                out[cur]["inside_prot"] = dose_p
            elif "眼晶体" in pos and dose_p is not None:
                out[cur]["eye_prot"] = dose_p
            elif ("四肢" in pos or "皮肤" in pos) and dose_u is not None:
                out[cur]["skin_unprot"] = dose_u

    # 表15：介入手术医师 / 公众
    if isinstance(table15, dict):
        cur = ""
        for band in table15.get("rows") or []:
            if not isinstance(band, dict):
                continue
            cells = {
                int(c.get("c0") or 0): c
                for c in (band.get("cells") or [])
                if isinstance(c, dict)
            }
            t0 = str(cells.get(0, {}).get("text") or "").strip()
            if t0.startswith("射线") or t0.startswith("注"):
                continue
            if t0:
                if "ERCP" in t0:
                    cur = "ERCP"
                elif "DSA" in t0:
                    cur = "DSA"
            if not cur:
                continue
            person = str(cells.get(2, {}).get("text") or "")
            dose = parse_dose_number(str(cells.get(6, {}).get("text") or ""))
            if dose is None:
                continue
            if "介入手术医师" in person:
                out[cur]["outside_physician"] = dose
            elif "公众" in person:
                out[cur]["public"] = dose
    return out


def build_interventional_after(
    table14: Optional[Dict[str, Any]] = None,
    table15: Optional[Dict[str, Any]] = None,
) -> str:
    """
    介入结论：由表14（机房内穿戴后/未穿戴）与表15（机房外医师/公众）数值生成。
    最大年有效剂量 = 机房内全身穿戴后 + 机房外介入手术医师。
    """
    doses = summarize_interv_doses_from_tables(table14, table15)
    defaults = {
        "ERCP": {
            "inside_prot": 2.13,
            "eye_prot": 2.13,
            "skin_unprot": 20.0,
            "outside_physician": 0.125,
            "public": 0.034,
        },
        "DSA": {
            "inside_prot": 4.25,
            "eye_prot": 4.25,
            "skin_unprot": 40.0,
            "outside_physician": 0.250,
            "public": 0.066,
        },
    }
    for k in ("ERCP", "DSA"):
        for fk, fv in defaults[k].items():
            doses[k].setdefault(fk, fv)

    parts_proj = []
    for key, name in (("ERCP", "ERCP"), ("DSA", "DSA")):
        d = doses[key]
        inside = float(d["inside_prot"])
        outside = float(d["outside_physician"])
        total = inside + outside
        eye = float(d["eye_prot"])
        skin = float(d["skin_unprot"])
        parts_proj.append(
            f"本项目{name}项目放射工作人员可能受到的最大年有效剂量值为{_fmt_msv2(total)}"
            f"（机房内{_fmt_msv2(inside)}+机房外{_fmt_msv3(outside)}）、"
            f"眼晶体可能受到的年当量剂量为{_fmt_msv2(eye)}、"
            f"四肢和皮肤可能受到的年当量剂量为{_fmt_msv2(skin)}"
        )
    p1 = (
        "从表14和表15可知，"
        + "，".join(parts_proj)
        + "，小于GB 18871-2002《电离辐射防护与辐射源安全基本标准》中规定的剂量限值"
        "（年有效剂量：20mSv/a、眼晶体年当量剂量：20mSv/a、四肢和皮肤年当量剂量：500mSv/a），"
        "也小于建设单位设置的工作人员年管理目标值"
        "（年有效剂量：5mSv/a、眼晶体年当量剂量：5mSv/a、四肢和皮肤年当量剂量：125mSv/a）。"
    )
    p2 = (
        f"本项目ERCP机房外公众可能受到的年有效剂量值为{_fmt_msv3(float(doses['ERCP']['public']))}、"
        f"DSA机房外公众可能受到的年有效剂量值为{_fmt_msv3(float(doses['DSA']['public']))}，"
        "均小于GB 18871-2002《电离辐射防护与辐射源安全基本标准》中规定的剂量限值（1mSv/a），"
        "也小于建设单位设置的管理目标值（0.25mSv/a）。"
    )
    p3 = (
        "综上所述，本项目在正常运行情况下，放射工作人员和公众可能受到的年有效剂量"
        "均小于GB 18871-2002《电离辐射防护与辐射源安全基本标准》中规定的剂量限值和"
        "建设单位设置的年管理目标值。ERCP项目和DSA项目放射工作人员眼晶体、四肢和皮肤的年当量剂量"
        "小于GB 18871-2002《电离辐射防护与辐射源安全基本标准》中规定的剂量限值和"
        "建设单位设置的年管理目标值。"
    )
    return f"{p1}\n\n{p2}\n\n{p3}"


def _replace_interv_after_in_text(text: str, interv_after: str) -> str:
    """用新结论替换 normal_condition 中表15之后的介入结论文段。"""
    raw = str(text or "")
    parts = raw.split(TABLE_MARK)
    if len(parts) >= 6:
        parts[-1] = "\n\n" + interv_after.strip() + "\n"
        return TABLE_MARK.join(parts)
    marker = "从表14和表15可知"
    idx = raw.find(marker)
    if idx >= 0:
        return raw[:idx] + interv_after.strip() + "\n"
    return raw.rstrip() + "\n\n" + interv_after.strip() + "\n"


def refresh_interventional_after_from_tables(
    tables: Sequence[Dict[str, Any]],
) -> str:
    """根据当前表14/15生成介入结论正文。"""
    t14 = tables[3] if len(tables) > 3 else None
    t15 = tables[4] if len(tables) > 4 else None
    return build_interventional_after(
        t14 if isinstance(t14, dict) else None,
        t15 if isinstance(t15, dict) else None,
    )
