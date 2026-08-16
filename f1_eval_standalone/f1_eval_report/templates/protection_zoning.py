# -*- coding: utf-8 -*-
"""防护设施和措施 · 放射防护分区：固定导语 + 表6（可读信息表机房数）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


DEFAULT_INTRO = (
    "本项目{room_count}间X射线设备机房工作场所放射防护分级设计情况及评价见表6，"
    "本项目控制区和监督区划分明确且合理，符合标准的相关要求。"
)

# 表6「标准要求」列预填文案（写在表内，不单独做常用模板）
DEFAULT_STANDARD = (
    "GB 18871-2002第6.4条：应把辐射工作场所分为控制区和监督区，"
    "以利于辐射防护管理和职业照射控制"
)

TABLE6_TITLE = "表6 本项目放射防护分级设计情况及评价"

# 表内控制区/监督区句式（{room_count}、{fig_refs} 由信息表填充）
CONTROL_TPL = "{room_count}间X射线设备机房内部区域{fig_refs}"
SUPERVISED_TPL = (
    "{room_count}间X射线设备机房控制室及其他与控制区相邻的人员可到达区域{fig_refs}"
)


def _load_fixed(rel: str, default: str = "") -> str:
    from f1_eval_report.templates.loader import load_common_text

    try:
        t = load_common_text(rel).strip()
        return t or default
    except Exception:
        return default


def _cell(c0: int, c1: int, text: str, *, row_span: int = 1) -> Dict[str, Any]:
    return {
        "c0": c0,
        "c1": c1,
        "row_span": row_span,
        "col_span": max(1, c1 - c0),
        "text": text,
    }


def count_xray_rooms(devices: Optional[Sequence[Dict[str, Any]]]) -> int:
    """
    从信息表装置清单统计 X 射线设备机房间数。
    优先按「位置/机房」去重；无位置时按装置台数计。
    """
    places: List[str] = []
    n_dev = 0
    for d in devices or []:
        if not isinstance(d, dict):
            continue
        n_dev += 1
        place = str(d.get("place") or d.get("room") or "").strip()
        if place and place not in places:
            places.append(place)
    return len(places) if places else n_dev


def _fig_refs_text(fig_nos: Optional[Sequence[int]] = None) -> str:
    """见图号列表 → 「（见图4、图5、…）」；无则空串（手工补）。"""
    nums = [int(x) for x in (fig_nos or []) if int(x) > 0]
    if not nums:
        return ""
    # 去重保序
    seen = set()
    ordered: List[int] = []
    for n in nums:
        if n not in seen:
            seen.add(n)
            ordered.append(n)
    inner = "、".join(f"图{n}" for n in ordered)
    return f"（见{inner}）"


# 表6 之前的栏目（不含防护设施和措施本身）
_SECTIONS_BEFORE_TABLE6 = (
    "org_header",
    "project_info",
    "radiation_source",
    "project_summary",
    "project_classification",
    "evaluation_basis",
    "evaluation_objective",
    "hazard_analysis",
    "workplace_layout",
)


def is_plane_layout_or_zoning_figure(caption: str) -> bool:
    """是否为「平面布局 / 放射分区」类附图（排除通道、通风、总平面等）。"""
    import re

    t = re.sub(r"\s+", "", str(caption or ""))
    if not t:
        return False
    if "通道走向" in t or "通风管道" in t or "地理位置" in t:
        return False
    if "地下室" in t or "地下层" in t or "地下一层" in t or "负一层" in t:
        return False
    if "院区总平面" in t:
        return False
    if "总平面图" in t and "机房" not in t:
        return False
    if "平面布局和放射分区" in t:
        return True
    if "放射分区" in t:
        return True
    if "平面布局" in t:
        return True
    return False


def _figure_has_image(fig: Any) -> bool:
    return isinstance(fig, dict) and bool(str(fig.get("image") or "").strip())


def _hazard_workflow_figure_count(data: Dict[str, Any]) -> int:
    """危害因素分析中流程图占用的图号数量（即使尚未上传插图）。"""
    import re

    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    text = str(fields.get("hazard_analysis") or data.get("hazard_analysis") or "")
    nos = [int(x) for x in re.findall(r"如图\s*(\d+)", text)]
    nos += [int(x) for x in re.findall(r"图\s+(\d+)\s+", text)]
    if nos:
        return max(nos)
    try:
        from f1_eval_report.templates.hazard_analysis import classify_devices
        from f1_eval_report.templates.shielding_summary import devices_from_report_data

        return len(classify_devices(devices_from_report_data(data) or []))
    except Exception:
        return 0


def iter_uploaded_figures_before_table6(
    data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    按报告栏目顺序，列出表6之前已上传的附图。
    （工作场所布局栏目末尾的平面布局图在防护设施/表6之前。）
    """
    out: List[Dict[str, Any]] = []
    for sid in _SECTIONS_BEFORE_TABLE6:
        key = f"extra_figures::{sid}"
        figs = data.get(key)
        if not isinstance(figs, list):
            continue
        for fig in figs:
            if _figure_has_image(fig):
                out.append(fig)
    # 旧版单图键（若仍挂在工作场所布局）
    legacy = data.get("workplace_layout_figure")
    if _figure_has_image(legacy):
        out.append(legacy)  # type: ignore[arg-type]
    return out


def layout_figure_numbers(data: Optional[Dict[str, Any]] = None) -> List[int]:
    """
    表6「见图…」：只收录表6之前已上传、且题注为平面布局/放射分区的图号。

    图号规则：危害因素分析流程图占 1..W；其后按栏目顺序给每张已上传附图顺延编号，
    再筛出平面布局/放射分区类。
    """
    if not isinstance(data, dict):
        return []
    try:
        n = _hazard_workflow_figure_count(data)
        nos: List[int] = []
        for fig in iter_uploaded_figures_before_table6(data):
            n += 1
            cap = str(fig.get("caption") or "")
            # 题注里若已写「图12 …」则优先用该显式编号
            import re

            m = re.match(r"^\s*图\s*(\d+)\s*", cap)
            if m:
                explicit = int(m.group(1))
                if is_plane_layout_or_zoning_figure(cap):
                    nos.append(explicit)
                continue
            if is_plane_layout_or_zoning_figure(cap):
                nos.append(n)
        # 去重保序
        seen = set()
        ordered: List[int] = []
        for x in nos:
            if x not in seen:
                seen.add(x)
                ordered.append(x)
        return ordered
    except Exception:
        return []


def _room_labels(devices: Optional[List[Dict[str, Any]]]) -> List[str]:
    """按装置清单去重机房简称（逐机房模式用）。"""
    try:
        from f1_eval_report.templates.workplace_layout import (
            _device_key,
            room_short_name,
        )
    except Exception:
        room_short_name = None  # type: ignore
        _device_key = None  # type: ignore

    labels: List[str] = []
    for d in devices or []:
        if not isinstance(d, dict):
            continue
        if room_short_name and _device_key:
            key = _device_key(d)
            short = room_short_name(str(d.get("place") or ""), key, d)
        else:
            short = str(d.get("place") or d.get("name") or d.get("type") or "").strip()
        if short and short not in labels:
            labels.append(short)
    return labels


def build_table6(
    devices: Optional[List[Dict[str, Any]]] = None,
    *,
    standard: Optional[str] = None,
    mode: str = "summary",
    fig_nos: Optional[Sequence[int]] = None,
    report_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    表6 结构:
      设计情况(控制区|监督区) | 标准要求 | 评价

    mode:
      - summary（默认）：一行汇总「N间X射线设备机房…」，N 来自信息表
      - per_room：每个机房一行（旧行为）
    """
    std = (standard or DEFAULT_STANDARD).strip() or DEFAULT_STANDARD
    room_count = count_xray_rooms(devices)
    if room_count <= 0:
        room_count = 1
    if fig_nos is None and report_data is not None:
        fig_nos = layout_figure_numbers(report_data)
    fig_refs = _fig_refs_text(fig_nos)

    header0 = {
        "band": 0,
        "cells": [
            _cell(0, 2, "设计情况"),
            _cell(2, 3, "标准要求", row_span=2),
            _cell(3, 4, "评价", row_span=2),
        ],
    }
    header1 = {
        "band": 1,
        "cells": [
            _cell(0, 1, "控制区"),
            _cell(1, 2, "监督区"),
        ],
    }

    data_rows: List[Dict[str, Any]] = []
    if mode == "per_room":
        rooms = _room_labels(devices)
        if not rooms:
            rooms = ["X射线设备机房"]
        for i, name in enumerate(rooms):
            data_rows.append(
                {
                    "band": 2 + i,
                    "cells": [
                        _cell(0, 1, f"{name}内部区域"),
                        _cell(
                            1,
                            2,
                            f"{name}控制室及其他与控制区相邻的人员可到达区域",
                        ),
                        _cell(2, 3, std),
                        _cell(3, 4, "符合"),
                    ],
                }
            )
    else:
        # 汇总模式：与样例一致，「16间X射线设备机房…」
        control = CONTROL_TPL.format(room_count=room_count, fig_refs=fig_refs)
        supervised = SUPERVISED_TPL.format(room_count=room_count, fig_refs=fig_refs)
        data_rows.append(
            {
                "band": 2,
                "cells": [
                    _cell(0, 1, control),
                    _cell(1, 2, supervised),
                    _cell(2, 3, std),
                    _cell(3, 4, "符合"),
                ],
            }
        )

    return {
        "schema": "embedded_generic_table/v1",
        "title": TABLE6_TITLE,
        "source": "protection_zoning",
        "from_info_sheet": True,
        "room_count": room_count,
        "column_count": 4,
        "column_fractions": [0.0, 0.22, 0.52, 0.82, 1.0],
        "band_count": 2 + len(data_rows),
        "row_min_height_pt": 18.0,
        "repeat_header_on_new_page": False,
        "note": "机房间数来自信息表；见图号=表6之前已上传的平面布局/放射分区图编号",
        "rows": [header0, header1, *data_rows],
    }


def fill_zoning_intro(
    tpl: str,
    *,
    room_count: int,
) -> str:
    text = (tpl or DEFAULT_INTRO).strip() or DEFAULT_INTRO
    return (
        text.replace("{room_count}", str(max(1, int(room_count))))
        .replace("{机房间数}", str(max(1, int(room_count))))
    )


def assemble_protection_zoning(
    devices: Optional[List[Dict[str, Any]]] = None,
    *,
    include_table: bool = True,
    report_data: Optional[Dict[str, Any]] = None,
    table_mode: str = "summary",
) -> Dict[str, Any]:
    """
    返回:
      protection_zoning: 导语（含信息表机房间数）
      protection_zoning_tables: [表6]
    """
    room_count = count_xray_rooms(devices)
    tpl = (
        _load_fixed("protection_measures/zoning_intro.md", "").strip()
        or _load_fixed("protection_zoning/intro.md", DEFAULT_INTRO).strip()
        or DEFAULT_INTRO
    )
    intro = fill_zoning_intro(tpl, room_count=room_count or 1)
    out: Dict[str, Any] = {"protection_zoning": intro, "room_count": room_count}
    if include_table:
        out["protection_zoning_tables"] = [
            build_table6(
                devices,
                mode=table_mode,
                report_data=report_data,
            )
        ]
    else:
        out["protection_zoning_tables"] = []
    return out
