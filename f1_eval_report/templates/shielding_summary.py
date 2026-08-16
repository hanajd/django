# -*- coding: utf-8 -*-
"""工作场所辐射屏蔽「综上」段落：按机房/装置清单自动生成。"""

from __future__ import annotations

import json
import os
import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

PACKAGE_TEMPLATES = os.path.dirname(os.path.abspath(__file__))


def _map_path() -> str:
    from f1_eval_report.templates.loader import templates_dir

    p = os.path.join(templates_dir(), "common", "evaluation_objective", "room_dose_map.json")
    if os.path.isfile(p):
        return p
    return os.path.join(
        PACKAGE_TEMPLATES, "common", "evaluation_objective", "room_dose_map.json"
    )


def load_room_dose_map() -> Dict[str, Any]:
    path = _map_path()
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _yn(v: Any) -> str:
    return "是" if v else "否"


def _parse_yn(text: str) -> bool:
    t = str(text or "").strip().lower()
    return t in {"是", "y", "yes", "1", "true", "✓", "√", "是的"}


def room_dose_map_to_embedded_table(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """机房-剂量 JSON → 嵌套表（供常用模板表格编辑器）。"""
    cfg = cfg if isinstance(cfg, dict) else load_room_dose_map()
    labels = dict(cfg.get("labels") or {})
    dose_25 = set(cfg.get("dose_25_keys") or [])
    dual = set(cfg.get("dual_mode_fluoro") or []) | set(cfg.get("dual_mode_photo") or [])
    ws76 = set(cfg.get("ws76_keys") or [])
    order_2_5 = list(cfg.get("order_2_5") or [])
    order_25 = list(cfg.get("order_25") or [])

    keys: List[str] = []
    for k in order_2_5 + order_25 + list(labels.keys()):
        if k and k not in keys:
            keys.append(k)

    sort_no: Dict[str, int] = {}
    for i, k in enumerate(order_2_5, 1):
        sort_no[k] = i
    base = len(order_2_5)
    for i, k in enumerate(order_25, 1):
        sort_no.setdefault(k, base + i)

    header = [
        "类型键",
        "显示名称",
        "≤2.5μSv/h",
        "≤25μSv/h",
        "双模式(透视+摄影)",
        "计入WS76",
        "排序",
    ]
    rows: List[Dict[str, Any]] = [
        {
            "band": 0,
            "cells": [
                {"c0": i, "c1": i + 1, "row_span": 1, "col_span": 1, "text": h}
                for i, h in enumerate(header)
            ],
        }
    ]
    for bi, key in enumerate(keys, 1):
        in_dual = key in dual
        in_25 = key in dose_25 or in_dual
        in_2_5 = (key in order_2_5) or in_dual
        if key in dose_25 and not in_dual:
            in_2_5 = False
        if not in_2_5 and not in_25 and not in_dual:
            in_2_5 = True
        rows.append(
            {
                "band": bi,
                "cells": [
                    {"c0": 0, "c1": 1, "row_span": 1, "col_span": 1, "text": key},
                    {
                        "c0": 1,
                        "c1": 2,
                        "row_span": 1,
                        "col_span": 1,
                        "text": str(labels.get(key) or ""),
                    },
                    {"c0": 2, "c1": 3, "row_span": 1, "col_span": 1, "text": _yn(in_2_5)},
                    {"c0": 3, "c1": 4, "row_span": 1, "col_span": 1, "text": _yn(in_25)},
                    {"c0": 4, "c1": 5, "row_span": 1, "col_span": 1, "text": _yn(in_dual)},
                    {"c0": 5, "c1": 6, "row_span": 1, "col_span": 1, "text": _yn(key in ws76)},
                    {
                        "c0": 6,
                        "c1": 7,
                        "row_span": 1,
                        "col_span": 1,
                        "text": str(sort_no.get(key) or bi),
                    },
                ],
            }
        )

    ncol = 7
    return {
        "schema": "embedded_generic_table/v1",
        "title": "机房-剂量对应表",
        "source": "room_dose_map",
        "column_count": ncol,
        "column_fractions": [round(i / ncol, 6) for i in range(ncol + 1)],
        "band_count": len(rows),
        "row_min_height_pt": 18.0,
        "repeat_header_on_new_page": False,
        "rows": rows,
    }


def embedded_table_to_room_dose_map(
    table: Dict[str, Any],
    base: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """嵌套表 → 机房-剂量 JSON。"""
    base = dict(base or {})
    labels: Dict[str, str] = {}
    dose_25: List[str] = []
    dual: List[str] = []
    ws76: List[str] = []
    scored_2_5: List[Tuple[int, str]] = []
    scored_25: List[Tuple[int, str]] = []

    rows = table.get("rows") or []
    for band in rows[1:]:
        cells = (band or {}).get("cells") or []
        texts = [""] * 7
        for c in cells:
            if not isinstance(c, dict):
                continue
            c0 = int(c.get("c0") or 0)
            if 0 <= c0 < 7:
                texts[c0] = str(c.get("text") or "").strip()
        key = texts[0]
        if not key or key in {"类型键", "key"}:
            continue
        labels[key] = texts[1] or f"{key}机房"
        try:
            sort_v = int(float(texts[6] or "0"))
        except (TypeError, ValueError):
            sort_v = 999
        is_2_5 = _parse_yn(texts[2])
        is_25 = _parse_yn(texts[3])
        is_dual = _parse_yn(texts[4])
        is_ws = _parse_yn(texts[5])
        if is_dual:
            dual.append(key)
            scored_2_5.append((sort_v, key))
            scored_25.append((sort_v, key))
        else:
            if is_25:
                dose_25.append(key)
                scored_25.append((sort_v, key))
            if is_2_5:
                scored_2_5.append((sort_v, key))
            elif not is_25:
                scored_2_5.append((sort_v, key))
        if is_ws:
            ws76.append(key)

    scored_2_5.sort(key=lambda x: (x[0], x[1]))
    scored_25.sort(key=lambda x: (x[0], x[1]))

    def _uniq(seq: List[str]) -> List[str]:
        out: List[str] = []
        for x in seq:
            if x not in out:
                out.append(x)
        return out

    return {
        "schema": "f1_room_dose_map/v1",
        "description": base.get("description")
        or "机房类型与周围剂量当量率限值对应；用于自动生成「综上」段。",
        "labels": labels,
        "dose_25_keys": _uniq(dose_25),
        "dual_mode_fluoro": _uniq(dual),
        "dual_mode_photo": _uniq(list(dual)),
        "ws76_keys": _uniq(ws76),
        "order_2_5": _uniq([k for _, k in scored_2_5]),
        "order_25": _uniq([k for _, k in scored_25]),
        "notes": base.get("notes")
        or {
            "2.5μSv/h": "GBZ 130-2020 §6.3.1 a/b",
            "25μSv/h": "GBZ 130-2020 §6.3.1 c",
            "dual_mode": "透视+摄影双模式时两类限值各写一条",
            "ws76": "计入透视防护区 ≤400μSv/h",
            "编辑说明": "「是/否」填写是否；排序数字越小越靠前；可增删行维护机房类型",
        },
    }


def _cn_join(parts: List[str]) -> str:
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]}及{parts[1]}"
    return "、".join(parts[:-1]) + "及" + parts[-1]


def classify_device_room(device: Dict[str, Any]) -> str:
    """将单台装置归入机房类型键。"""
    name = str(device.get("name") or device.get("type") or "").strip()
    place = str(device.get("place") or device.get("location") or device.get("room") or "").strip()
    note = str(device.get("note") or "").strip()
    blob = f"{name}|{place}|{note}"

    if "ERCP" in blob or "内镜" in place and "C形臂" in name:
        return "ercp"
    if "DSA" in name or "DSA" in place:
        return "dsa"
    if "骨密度" in name or "骨密度" in place:
        return "bmd"
    if "乳腺" in name or "乳腺" in place:
        return "mammo_dr"
    if "胃肠" in name or "胃肠" in place:
        return "gi"
    if "口腔CBCT" in blob or "四合一" in blob:
        return "oral_cbct"
    if "口内" in blob or "牙片" in blob:
        return "intraoral"
    if "全景" in blob:
        return "dental_panorama"
    if "车载CT" in blob:
        return "vehicle_ct"
    if "车载DR" in blob:
        return "vehicle_dr"
    if "移动DR" in blob:
        return "mobile_dr"
    if "动态DR" in blob:
        return "dynamic_dr"
    if "G形臂" in blob or "G臂" in blob:
        return "g_arm"
    if "C形臂" in name or "C臂" in name or "C形臂" in place:
        return "c_arm"
    if re.search(r"(^|[^A-Za-z])CT([^A-Za-z]|$)", name) or "CT机房" in place:
        if "CBCT" not in blob:
            return "ct"
    if name == "DR" or re.search(r"(^|[^A-Za-z])DR([^A-Za-z]|$)", name) or "DR机房" in place:
        if "乳腺" not in blob and "动态" not in blob and "移动" not in blob and "车载" not in blob:
            return "dr"
    return "other"


def _count_rooms(devices: List[Dict[str, Any]]) -> "OrderedDict[str, int]":
    """按机房类型统计间数（以所在场所去重，无场所则按台计）。"""
    seen_place: Dict[str, str] = {}
    counts: "OrderedDict[str, int]" = OrderedDict()
    for d in devices:
        if not isinstance(d, dict):
            continue
        key = classify_device_room(d)
        place = str(d.get("place") or d.get("location") or d.get("room") or "").strip()
        if place:
            # 同一机房多台设备只计一间
            if place in seen_place:
                continue
            seen_place[place] = key
        counts[key] = counts.get(key, 0) + 1
    return counts


def _phrase(n: int, label: str, mode: str = "") -> str:
    if n <= 0:
        return ""
    if mode:
        return f"{n}间{label}（{mode}）"
    return f"{n}间{label}"


def build_shielding_project_summary(devices: Optional[List[Dict[str, Any]]] = None) -> str:
    """
    生成「综上」段正文（不含行首「综上，本项目涉及的」前缀以外的完整句，
    返回可直接接在「综上，本项目涉及的」之后的内容，或整段）。
    """
    devices = list(devices or [])
    cfg = load_room_dose_map()
    dose_25_keys = set(cfg.get("dose_25_keys") or ["dr", "mobile_dr", "vehicle_dr", "dynamic_dr"])
    dual_fluoro = set(cfg.get("dual_mode_fluoro") or ["gi", "ercp"])
    dual_photo = set(cfg.get("dual_mode_photo") or ["gi", "ercp"])
    labels = dict(cfg.get("labels") or {})
    default_labels = {
        "ct": "CT机房",
        "bmd": "全身骨密度仪机房",
        "mammo_dr": "乳腺DR机房",
        "gi": "数字胃肠机房",
        "ercp": "ERCP机房",
        "c_arm": "C形臂机房",
        "dsa": "DSA机房",
        "dr": "DR机房",
        "g_arm": "G形臂机房",
        "oral_cbct": "口腔CBCT机房",
        "intraoral": "口内牙片机房",
        "dental_panorama": "牙科全景机房",
        "vehicle_ct": "车载CT机房",
        "vehicle_dr": "车载DR机房",
        "mobile_dr": "移动DR机房",
        "dynamic_dr": "动态DR机房",
    }
    for k, v in default_labels.items():
        labels.setdefault(k, v)

    # 展示顺序
    order_2_5 = list(
        cfg.get("order_2_5")
        or ["ct", "bmd", "mammo_dr", "gi", "ercp", "c_arm", "dsa", "g_arm", "oral_cbct", "intraoral", "dental_panorama", "vehicle_ct"]
    )
    order_25 = list(
        cfg.get("order_25") or ["dr", "gi", "ercp", "mobile_dr", "vehicle_dr", "dynamic_dr"]
    )

    counts = _count_rooms(devices)
    parts_2_5: List[str] = []
    for key in order_2_5:
        n = int(counts.get(key) or 0)
        if n <= 0:
            continue
        label = labels.get(key) or f"{key}机房"
        if key in dual_fluoro:
            parts_2_5.append(_phrase(n, label, "透视模式下"))
        elif key not in dose_25_keys:
            parts_2_5.append(_phrase(n, label))

    # 纯 2.5 类里尚未列出的 other 跳过
    parts_25: List[str] = []
    for key in order_25:
        n = int(counts.get(key) or 0)
        if n <= 0:
            continue
        label = labels.get(key) or f"{key}机房"
        if key in dual_photo:
            parts_25.append(_phrase(n, label, "摄影模式下"))
        elif key in dose_25_keys:
            parts_25.append(_phrase(n, label))

    bit_25 = _cn_join(parts_2_5)
    bit_25_rate = _cn_join(parts_25)
    chunks: List[str] = []
    if bit_25:
        chunks.append(f"{bit_25}外周围剂量当量率应不大于2.5μSv/h")
    if bit_25_rate:
        chunks.append(
            f"{bit_25_rate}外周围剂量当量率应不大于25 μSv/h，"
            f"当超过时，机房外人员可能受到的年有效剂量不大于0.25mSv"
        )
    if not chunks:
        return ""
    return "；".join(chunks) + "。"


def build_ws76_rooms_summary(devices: Optional[List[Dict[str, Any]]] = None) -> str:
    """生成 WS76 句中的机房表述，如「2间DSA机房和1间ERCP机房」。"""
    devices = list(devices or [])
    cfg = load_room_dose_map()
    ws_keys = list(cfg.get("ws76_keys") or ["dsa", "ercp"])
    labels = dict(cfg.get("labels") or {})
    labels.setdefault("dsa", "DSA机房")
    labels.setdefault("ercp", "ERCP机房")
    counts = _count_rooms(devices)
    parts: List[str] = []
    for key in ws_keys:
        n = int(counts.get(key) or 0)
        if n > 0:
            parts.append(_phrase(n, labels.get(key) or f"{key}机房"))
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]}和{parts[1]}"
    return "、".join(parts[:-1]) + "和" + parts[-1]


def build_shielding_project_paragraph(devices: Optional[List[Dict[str, Any]]] = None) -> str:
    """完整「综上」段（含前缀）。"""
    body = build_shielding_project_summary(devices)
    if not body:
        return ""
    if body.startswith("综上"):
        return body
    return f"综上，本项目涉及的{body}"


def devices_from_radiation_table(table: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从辐射源项嵌套表粗提取 name/place。"""
    out: List[Dict[str, Any]] = []
    rows = table.get("rows") or []
    # 找列：设备类型、所在场所
    header_cells = []
    if rows:
        header_cells = (rows[0] or {}).get("cells") or []
    headers = [str(c.get("text") or "") for c in header_cells]
    idx_type = next((i for i, h in enumerate(headers) if "设备类型" in h or "类型" in h), 1)
    idx_place = next((i for i, h in enumerate(headers) if "场所" in h or "位置" in h), 7)
    for band in rows[1:]:
        cells = (band or {}).get("cells") or []
        texts = [str(c.get("text") or "").strip() for c in cells]
        if len(texts) <= max(idx_type, idx_place):
            continue
        name = texts[idx_type]
        place = texts[idx_place]
        if not name and not place:
            continue
        # 去掉备注括号外的主名
        main = re.split(r"[（(]", name, 1)[0].strip()
        out.append({"name": main or name, "place": place, "note": name})
    return out


def devices_from_report_data(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    tables = data.get("radiation_source_tables")
    if isinstance(tables, list) and tables and isinstance(tables[0], dict):
        return devices_from_radiation_table(tables[0])
    return []
