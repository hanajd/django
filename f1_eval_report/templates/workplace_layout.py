# -*- coding: utf-8 -*-
"""工作场所布局：按机房区位组稿；表4/表5手工维护（可预填机房名与标准限值）。"""

from __future__ import annotations

import json
import os
import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

PACKAGE_TEMPLATES = os.path.dirname(os.path.abspath(__file__))


def _limits_path() -> str:
    from f1_eval_report.templates.loader import templates_dir

    p = os.path.join(templates_dir(), "common", "workplace_layout", "room_size_limits.json")
    if os.path.isfile(p):
        return p
    return os.path.join(
        PACKAGE_TEMPLATES, "common", "workplace_layout", "room_size_limits.json"
    )


def load_room_size_limits() -> Dict[str, Any]:
    path = _limits_path()
    if not os.path.isfile(path):
        return {"items": {}}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {"items": {}}


def _load_fixed(rel: str, default: str = "") -> str:
    from f1_eval_report.templates.loader import load_common_text

    try:
        t = load_common_text(rel).strip()
        return t or default
    except Exception:
        return default


def _cn_join(parts: List[str]) -> str:
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]}和{parts[1]}"
    return "、".join(parts[:-1]) + "和" + parts[-1]


def _device_key(device: Dict[str, Any]) -> str:
    from f1_eval_report.templates.shielding_summary import classify_device_room

    return classify_device_room(device)


def _device_label(key: str, device: Optional[Dict[str, Any]] = None) -> str:
    labels = {
        "ct": "CT",
        "dr": "DR",
        "cr": "CR",
        "bmd": "全身骨密度仪",
        "mammo_dr": "乳腺DR",
        "mammo_cr": "乳腺CR",
        "gi": "数字胃肠机",
        "ercp": "ERCP",
        "dsa": "DSA",
        "c_arm": "C形臂",
        "mini_c_arm": "小C臂",
        "g_arm": "G形臂",
        "oral_cbct": "口腔CBCT",
        "oral_cbct_combo": "多合一口腔CBCT",
        "intraoral": "口内牙片机",
        "dental_panorama": "口腔全景机",
        "vehicle_ct": "车载CT",
        "vehicle_dr": "车载DR",
        "mobile_ct": "移动CT",
        "mobile_dr": "移动DR",
        "mobile_xray": "移动X光机",
    }
    if key in labels:
        return labels[key]
    if device:
        return str(device.get("name") or key)
    return key


def location_zone(place: str) -> str:
    """从机房场所名提取区位（楼栋+楼层+中心），用于分组。"""
    p = str(place or "").strip()
    if not p:
        return "本项目放射机房"
    p = re.sub(r"手术室\d+$", "", p)
    # 注意：数字胃肠机房 = 数字胃肠 + 机房（不是「数字胃肠机」+「机房」）
    suffixes = [
        "全身骨密度仪机房",
        "数字胃肠机房",
        "乳腺DR机房",
        "乳腺CR机房",
        "口腔CBCT（四合一）机房",
        "口腔CBCT机房",
        "口内牙片机房",
        "牙科全景机房",
        "多合一口腔CBCT机房",
        "CT机房",
        "DR机房",
        "DSA机房",
        "ERCP机房",
        "CR机房",
        "C形臂机房",
        "G形臂机房",
        "机房",
    ]
    for suf in suffixes:
        np = re.sub(rf"{re.escape(suf)}\d*$", "", p)
        if np != p:
            p = np
            break
    p = p.rstrip("、，, ")
    return p or str(place).strip()


def room_short_name(place: str, key: str, device: Dict[str, Any]) -> str:
    """机房简称，如 CT机房、DR机房1。"""
    p = str(place or "")
    m = re.search(
        r"("
        r"全身骨密度仪机房\d*|数字胃肠机房\d*|乳腺DR机房\d*|口腔CBCT机房\d*|"
        r"口内牙片机房\d*|牙科全景机房\d*|"
        r"CT机房\d*|DR机房\d*|DSA机房\d*|ERCP机房\d*|CR机房\d*|"
        r"手术室\d+"
        r")$",
        p,
    )
    if m:
        return m.group(1)
    label = _device_label(key, device)
    return f"{label}机房"


def group_devices_by_zone(
    devices: List[Dict[str, Any]],
) -> "OrderedDict[str, List[Dict[str, Any]]]":
    groups: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for d in devices or []:
        if not isinstance(d, dict):
            continue
        zone = location_zone(str(d.get("place") or ""))
        groups.setdefault(zone, []).append(d)
    return groups


def limit_for_key(key: str, limits: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    limits = limits or load_room_size_limits()
    items = limits.get("items") if isinstance(limits.get("items"), dict) else {}
    hit = items.get(key)
    if isinstance(hit, dict):
        return hit
    # 别名
    aliases = {
        "mammo_cr": "mammo_dr",
        "mini_c_arm": "c_arm",
        "mobile_xray": "mobile_dr",
        "cr": "dr",
    }
    alt = aliases.get(key)
    if alt and isinstance(items.get(alt), dict):
        return items[alt]
    return {
        "label": _device_label(key),
        "min_area_m2": "",
        "min_side_m": "",
        "note": "请按GBZ 130-2020表2核对",
    }


def _simple_table(title: str, headers: List[str], data_rows: List[List[str]]) -> Dict[str, Any]:
    ncol = len(headers)
    rows: List[Dict[str, Any]] = [
        {
            "band": 0,
            "cells": [
                {
                    "c0": i,
                    "c1": i + 1,
                    "row_span": 1,
                    "col_span": 1,
                    "text": h,
                }
                for i, h in enumerate(headers)
            ],
        }
    ]
    for bi, cells in enumerate(data_rows, 1):
        padded = list(cells) + [""] * max(0, ncol - len(cells))
        rows.append(
            {
                "band": bi,
                "cells": [
                    {
                        "c0": i,
                        "c1": i + 1,
                        "row_span": 1,
                        "col_span": 1,
                        "text": str(padded[i] if i < len(padded) else ""),
                    }
                    for i in range(ncol)
                ],
            }
        )
    return {
        "schema": "embedded_generic_table/v1",
        "title": title,
        "source": "workplace_layout",
        "column_count": ncol,
        "column_fractions": [round(i / ncol, 6) for i in range(ncol + 1)],
        "band_count": len(rows),
        "row_min_height_pt": 18.0,
        "repeat_header_on_new_page": False,
        "note": "请手工核对并填写设计值/毗邻情况后保存",
        "rows": rows,
    }


def build_table4_area(devices: List[Dict[str, Any]]) -> Dict[str, Any]:
    """表4：机房有效使用面积与最小单边长度（预填机房与标准限值，设计列留空手工填）。"""
    limits = load_room_size_limits()
    headers = [
        "机房名称",
        "设备类型",
        "设计有效使用面积（m²）",
        "设计最小单边长度（m）",
        "标准最小有效使用面积（m²）",
        "标准最小单边长度（m）",
        "评价",
    ]
    data_rows: List[List[str]] = []
    for d in devices or []:
        key = _device_key(d)
        lim = limit_for_key(key, limits)
        place = str(d.get("place") or "")
        label = str(lim.get("label") or _device_label(key, d))
        area = lim.get("min_area_m2")
        side = lim.get("min_side_m")
        area_s = "" if area in (None, "") else str(area)
        side_s = "" if side in (None, "") else str(side)
        note = str(lim.get("note") or "")
        data_rows.append(
            [
                place,
                label,
                "",  # 设计面积 — 手工
                "",  # 设计单边 — 手工
                area_s,
                side_s,
                note if "不适用" in note or "除外" in note else "",
            ]
        )
    return _simple_table("表4  机房有效使用面积和最小单边长度", headers, data_rows)


def build_table5_adjacency(devices: List[Dict[str, Any]]) -> Dict[str, Any]:
    """表5：毗邻情况（预填机房名，毗邻列留空手工填）。"""
    headers = ["机房名称", "东侧", "西侧", "南侧", "北侧", "楼上", "楼下", "备注"]
    data_rows = [
        [str(d.get("place") or ""), "", "", "", "", "", "", ""]
        for d in (devices or [])
        if isinstance(d, dict)
    ]
    return _simple_table("表5  机房毗邻情况", headers, data_rows)


def _group_title(zone: str, devices: List[Dict[str, Any]]) -> str:
    names: List[str] = []
    for d in devices:
        key = _device_key(d)
        short = room_short_name(str(d.get("place") or ""), key, d)
        # 去重但保序（同类型多间保留编号）
        if short not in names:
            names.append(short)
    return f"{zone}{_cn_join(names)}"


def _count_rooms_phrase(devices: List[Dict[str, Any]]) -> str:
    """1间CT机房和1间DR机房"""
    counts: "OrderedDict[str, int]" = OrderedDict()
    labels: Dict[str, str] = {}
    for d in devices:
        key = _device_key(d)
        labels[key] = _device_label(key, d)
        counts[key] = counts.get(key, 0) + 1
    parts = [f"{n}间{labels[k]}机房" for k, n in counts.items()]
    return _cn_join(parts)


def build_group_paragraph(
    zone: str,
    devices: List[Dict[str, Any]],
    *,
    index: int,
    fig: int,
    table4_no: int = 4,
    table5_no: int = 5,
) -> str:
    tpl = _load_fixed(
        "workplace_layout/group_paragraph.md",
        "如图{fig}所示，建设单位拟在{zone}设置{room_counts}，拟各自设置单独的机房，"
        "并设置共用的控制室，且拟与机房分开设置。"
        "{room_names}拟设置机房防护门（为{door_room}）、控制室防护门（为{door_control}）"
        "和便于观察受检者状态及防护门开启状态的观察窗{extra_rooms}。"
        "{beam_text}"
        "{room_names}最小有效使用面积和最小单边长度见表{table4}、毗邻情况见表{table5}。"
        "{room_names}选址位置已充分考虑邻室（含楼上和楼下）及周围场所的人员防护与安全。",
    )
    door_room = _load_fixed(
        "workplace_layout/door_room_default.md", "单扇电动推拉门"
    )
    door_control = _load_fixed(
        "workplace_layout/door_control_default.md", "单扇手动平开门"
    )
    beam_tpl = _load_fixed(
        "workplace_layout/beam_default.md",
        "【请补充有用线束朝向及是否直接照射门、窗、管线口和工作人员操作位】",
    )

    room_names_list = []
    for d in devices:
        key = _device_key(d)
        short = room_short_name(str(d.get("place") or ""), key, d)
        if short not in room_names_list:
            room_names_list.append(short)
    room_names = _cn_join(room_names_list)
    room_counts = _count_rooms_phrase(devices)

    # 额外用房提示（CT常设设备间）
    extras = []
    if any(_device_key(d) == "ct" for d in devices):
        extras.append("CT机房拟设设备间")
    extra_rooms = ("，" + "，".join(extras)) if extras else ""

    beam_text = beam_tpl.strip()
    if beam_text and not beam_text.endswith("。"):
        beam_text += "。"

    body = (
        tpl.replace("{fig}", str(fig))
        .replace("{zone}", zone)
        .replace("{room_counts}", room_counts)
        .replace("{room_names}", room_names)
        .replace("{door_room}", door_room)
        .replace("{door_control}", door_control)
        .replace("{extra_rooms}", extra_rooms)
        .replace("{beam_text}", beam_text)
        .replace("{table4}", str(table4_no))
        .replace("{table5}", str(table5_no))
        .strip()
    )
    title = f"{index}.{_group_title(zone, devices)}"
    return f"{title}\n{body}"


def assemble_workplace_layout(
    devices: Optional[List[Dict[str, Any]]] = None,
    *,
    fig_start: int = 1,
    include_tables: bool = True,
) -> Dict[str, Any]:
    """
    返回:
      workplace_layout_intro: 分组正文 + 总结
      workplace_layout_tables: [表4, 表5]（设计值/毗邻手工填）
    """
    devices = list(devices or [])
    groups = group_devices_by_zone(devices)
    parts: List[str] = []
    fig = fig_start
    for i, (zone, devs) in enumerate(groups.items(), 1):
        parts.append(
            build_group_paragraph(
                zone, devs, index=i, fig=fig, table4_no=4, table5_no=5
            )
        )
        # 平面布局图统一挂在栏目末尾（extra_figures::workplace_layout），正文仅引用图号
        fig += 1

    summary = _load_fixed(
        "workplace_layout/summary.md",
        "综上所述，本项目涉及机房最小有效使用面积和最小单边长度均符合GBZ 130-2020"
        "《放射诊断放射防护要求》的相关要求，本项目整体平面布局合理，符合放射卫生学及相关标准要求。",
    )
    if summary:
        parts.append(summary)

    intro = "\n".join(p for p in parts if p)
    tables: List[Dict[str, Any]] = []
    if include_tables:
        tables.append(build_table4_area(devices))
        tables.append(build_table5_adjacency(devices))
    return {
        "workplace_layout_intro": intro,
        "workplace_layout_tables": tables,
        "fig_next": fig,
    }


# 附图提取：见 info_sheet_figures（机房平面 / 通风管道）
from f1_eval_report.templates.info_sheet_figures import (  # noqa: E402
    extract_workplace_layout_figures,
    is_room_layout_page,
)


def room_size_limits_to_embedded_table(
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """限值 JSON → 常用模板表格编辑。"""
    cfg = cfg if isinstance(cfg, dict) else load_room_size_limits()
    items = cfg.get("items") if isinstance(cfg.get("items"), dict) else {}
    order = list(cfg.get("order") or items.keys())
    headers = ["类型键", "显示名称", "最小有效使用面积(m²)", "最小单边长度(m)", "备注"]
    rows: List[List[str]] = []
    for key in order:
        it = items.get(key) or {}
        if not isinstance(it, dict):
            continue
        rows.append(
            [
                key,
                str(it.get("label") or key),
                "" if it.get("min_area_m2") in (None, "") else str(it.get("min_area_m2")),
                "" if it.get("min_side_m") in (None, "") else str(it.get("min_side_m")),
                str(it.get("note") or ""),
            ]
        )
    return _simple_table("GBZ 130 机房面积与单边长度限值", headers, rows)


def embedded_table_to_room_size_limits(
    table: Dict[str, Any], base: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    base = dict(base or {})
    items: Dict[str, Any] = {}
    order: List[str] = []
    for band in (table.get("rows") or [])[1:]:
        cells = (band or {}).get("cells") or []
        texts = [""] * 5
        for c in cells:
            if not isinstance(c, dict):
                continue
            c0 = int(c.get("c0") or 0)
            if 0 <= c0 < 5:
                texts[c0] = str(c.get("text") or "").strip()
        key = texts[0]
        if not key or key in {"类型键", "key"}:
            continue
        area = texts[2]
        side = texts[3]
        try:
            area_v: Any = float(area) if area else ""
            if isinstance(area_v, float) and area_v == int(area_v):
                area_v = int(area_v)
        except ValueError:
            area_v = area
        try:
            side_v: Any = float(side) if side else ""
            if isinstance(side_v, float) and side_v == int(side_v):
                side_v = int(side_v)
        except ValueError:
            side_v = side
        items[key] = {
            "label": texts[1] or key,
            "min_area_m2": area_v,
            "min_side_m": side_v,
            "note": texts[4],
        }
        order.append(key)
    return {
        "schema": "f1_room_size_limits/v1",
        "description": base.get("description")
        or "GBZ 130-2020 表2 机房最小有效使用面积与最小单边长度（供表4对照）",
        "order": order,
        "items": items,
        "notes": base.get("notes")
        or {
            "source": "GBZ 130-2020 §6.1.5 表2",
            "编辑说明": "可在常用模板中以表格维护；车载/部分移动设备标准注明除外",
        },
    }
