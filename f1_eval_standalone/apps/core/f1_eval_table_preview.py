# -*- coding: utf-8 -*-
"""主表格空表骨架：合并单元格预览 + 可编辑表头 + 列宽。"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from f1_eval_report.assemble import assemble_layout, load_base_format
from f1_eval_report.base.columns import default_column_widths_mm
from f1_eval_report.sections.catalog import all_row_definitions, get_section_builders


def _flatten_label(text: Any) -> str:
    return "".join(str(text or "").split())


def _wrap_chars(label_format: Any) -> int:
    if isinstance(label_format, dict):
        try:
            return max(0, int(label_format.get("wrap_chars") or 0))
        except (TypeError, ValueError):
            return 0
    return 0


# 栏目导航标题 ← 主表主表头 label_key（修改后同步侧栏）
SECTION_PRIMARY_LABEL_KEY: Dict[str, str] = {
    "radiation_source": "radiation_source::label",
    "project_summary": "project_summary::label",
    "project_classification": "project_classification::label",
    "evaluation_basis": "evaluation_basis::label",
    "evaluation_objective": "evaluation_objective::label",
    "hazard_analysis": "hazard_analysis::label",
    "workplace_layout": "workplace_layout::label",
    "protection_measures": "protection_zoning::label",
    "health_impact": "health_impact::section_label",
    "radiation_management": "radiation_management::section_label",
    "conclusion": "conclusion::label",
    "attachments": "attachments::label",
}


def _label_display(text: str, label_format: Any = None) -> str:
    """预览用表头文案：支持 lines / wrap_chars（两字换行等），行间用换行符。"""
    raw = _flatten_label(text)
    if label_format and isinstance(label_format, dict):
        lines = label_format.get("lines")
        if lines:
            return "\n".join(_flatten_label(x) for x in lines if str(x).strip())
        wrap_n = _wrap_chars(label_format)
        if wrap_n > 0:
            if not raw:
                return ""
            return "\n".join(raw[i : i + wrap_n] for i in range(0, len(raw), wrap_n))
    return raw


def _ov(overrides: Dict[str, str], key: str, default: str) -> str:
    if key and key in overrides and str(overrides[key]).strip() != "":
        return _flatten_label(overrides[key])
    return _flatten_label(default)


def _fmt_label(
    overrides: Dict[str, str],
    key: str,
    raw: Any,
    label_format: Any = None,
) -> str:
    """规范文案（无空白）；换行仅用于预览展示时再由 wrap_chars 生成。"""
    if label_format and isinstance(label_format, dict) and label_format.get("lines"):
        if not (key and key in overrides and str(overrides[key]).strip()):
            return _flatten_label("".join(str(x) for x in label_format.get("lines") or []))
    return _ov(overrides, key, str(raw or ""))


def _geo(
    columns: Dict[str, Dict[str, float]],
    col: str,
    table_left: float,
    table_right: float,
) -> Tuple[float, float]:
    c = columns.get(col) or {"x0": table_left, "x1": table_right}
    tw = max(1.0, table_right - table_left)
    left = round((float(c["x0"]) - table_left) / tw * 100, 3)
    width = round((float(c["x1"]) - float(c["x0"])) / tw * 100, 3)
    return left, width


def _mk(
    columns: Dict[str, Dict[str, float]],
    col: str,
    text: str,
    *,
    table_left: float,
    table_right: float,
    is_label: bool = True,
    label_key: str = "",
    field_key: str = "",
    rowspan: int = 1,
    editable: bool = True,
    wrap_chars: int = 0,
    label_format: Any = None,
    value_format: Any = None,
) -> Dict[str, Any]:
    left, width = _geo(columns, col, table_left, table_right)
    wc = wrap_chars or _wrap_chars(label_format)
    cell = {
        "text": _flatten_label(text) if is_label else "",
        "is_label": is_label,
        "col": col,
        "label_key": label_key if (is_label and editable) else "",
        "field_key": str(field_key or "") if not is_label else "",
        "rowspan": max(1, int(rowspan)),
        "left_pct": left,
        "width_pct": width,
        "editable": bool(is_label and editable and label_key),
    }
    if wc > 0 and is_label:
        cell["wrap_chars"] = wc
    if not is_label and isinstance(value_format, dict):
        widget = value_format.get("widget")
        if widget:
            cell["widget"] = str(widget)
            if isinstance(value_format.get("options"), list):
                cell["widget_options"] = value_format.get("options")
            if isinstance(value_format.get("line_sep"), dict):
                cell["widget_line_sep"] = value_format.get("line_sep")
    return cell


def build_merged_skeleton(
    rows: List[Dict[str, Any]],
    columns: Dict[str, Dict[str, float]],
    table_left: float,
    table_right: float,
    overrides: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """
    生成带 rowspan 的空表行：
    - section / continue_section 共用左侧主表头
    - 嵌入表 + 后续 continue 行合并主表头
    """
    overrides = overrides or {}
    out: List[Dict[str, Any]] = []
    i = 0
    n = len(rows)

    def flush_section_block(start: int, end: int) -> None:
        """rows[start:end] 共用一个左侧主表头（含 embedded 首行 + continue section）。"""
        block = rows[start:end]
        # 统计逻辑行数
        logical = 0
        for r in block:
            if r.get("type") == "section":
                logical += max(1, len(r.get("lines") or []))
            else:
                logical += 1

        first = True
        for r in block:
            rid = str(r.get("id") or "")
            rtype = r.get("type")
            if rtype == "label_embedded_table":
                label = _fmt_label(
                    overrides, f"{rid}::label", r.get("label", ""), r.get("label_format")
                )
                cells: List[Dict[str, Any]] = []
                if first:
                    cells.append(
                        _mk(
                            columns,
                            r.get("label_col", "label_main_half"),
                            label,
                            table_left=table_left,
                            table_right=table_right,
                            label_key=f"{rid}::label",
                            rowspan=logical,
                            label_format=r.get("label_format"),
                        )
                    )
                    first = False
                sub = r.get("sub_label")
                if sub:
                    cells.append(
                        _mk(
                            columns,
                            r.get("sub_label_col", "sub_label_row_half"),
                            _fmt_label(
                                overrides,
                                f"{rid}::sub_label",
                                sub,
                                {"mode": "center", "wrap_chars": 2},
                            ),
                            table_left=table_left,
                            table_right=table_right,
                            label_key=f"{rid}::sub_label",
                            wrap_chars=2,
                        )
                    )
                    cells.append(
                        _mk(
                            columns,
                            r.get("sub_value_col", "value_wide_half"),
                            "",
                            is_label=False,
                            table_left=table_left,
                            table_right=table_right,
                            field_key=str((r.get("embed") or {}).get("text_key") or ""),
                        )
                    )
                else:
                    cells.append(
                        _mk(
                            columns,
                            r.get("value_col", "value_full_half"),
                            "",
                            is_label=False,
                            table_left=table_left,
                            table_right=table_right,
                            field_key=str((r.get("embed") or {}).get("text_key") or r.get("field_key") or ""),
                        )
                    )
                out.append(
                    {
                        "id": rid,
                        "type": rtype,
                        "tall": True,
                        "cells": cells,
                    }
                )
            elif rtype == "section":
                sec_key = f"{rid}::section_label"
                sec_text = _fmt_label(
                    overrides,
                    sec_key,
                    r.get("section_label") or "",
                    r.get("section_label_format"),
                )
                lines = r.get("lines") or []
                for li, line in enumerate(lines):
                    cells = []
                    if first:
                        cells.append(
                            _mk(
                                columns,
                                r.get("section_col", "label_main_half"),
                                sec_text,
                                table_left=table_left,
                                table_right=table_right,
                                label_key=sec_key,
                                rowspan=logical,
                                label_format=r.get("section_label_format"),
                            )
                        )
                        first = False
                    for cell in line.get("cells") or []:
                        if cell.get("role") == "label":
                            lk = f"{rid}::line:{li}:label"
                            cells.append(
                                _mk(
                                    columns,
                                    cell.get("col", "sub_label_row_half"),
                                    _fmt_label(
                                        overrides,
                                        lk,
                                        cell.get("text", ""),
                                        cell.get("label_format"),
                                    ),
                                    table_left=table_left,
                                    table_right=table_right,
                                    label_key=lk,
                                    label_format=cell.get("label_format"),
                                )
                            )
                        elif cell.get("role") == "value":
                            cells.append(
                                _mk(
                                    columns,
                                    cell.get("col", "value_wide_half"),
                                    "",
                                    is_label=False,
                                    table_left=table_left,
                                    table_right=table_right,
                                    field_key=str(cell.get("field_key") or ""),
                                )
                            )
                    out.append({"id": rid, "type": rtype, "line": li, "cells": cells})

    while i < n:
        row = rows[i]
        rid = str(row.get("id") or "")
        rtype = row.get("type")

        # 嵌入表头 + 后续 continue_section → 合并左侧主表头
        if rtype == "label_embedded_table":
            j = i + 1
            while j < n and rows[j].get("type") == "section" and rows[j].get("continue_section"):
                j += 1
            flush_section_block(i, j)
            i = j
            continue

        if rtype == "section" and not row.get("continue_section"):
            j = i + 1
            while j < n and rows[j].get("type") == "section" and rows[j].get("continue_section"):
                j += 1
            flush_section_block(i, j)
            i = j
            continue

        if rtype == "section" and row.get("continue_section"):
            flush_section_block(i, i + 1)
            i += 1
            continue

        if rtype == "label_value":
            lk = f"{rid}::label"
            out.append(
                {
                    "id": rid,
                    "type": rtype,
                    "cells": [
                        _mk(
                            columns,
                            row.get("label_col", "label_main"),
                            _fmt_label(
                                overrides, lk, row.get("label", ""), row.get("label_format")
                            ),
                            table_left=table_left,
                            table_right=table_right,
                            label_key=lk,
                            label_format=row.get("label_format"),
                        ),
                        _mk(
                            columns,
                            row.get("value_col", "value_full"),
                            "",
                            is_label=False,
                            table_left=table_left,
                            table_right=table_right,
                            field_key=str(row.get("field_key") or ""),
                        ),
                    ],
                }
            )
        elif rtype == "pairs":
            cells = []
            for pi, p in enumerate(row.get("pairs") or []):
                lk = f"{rid}::pair:{pi}:label"
                cells.append(
                    _mk(
                        columns,
                        p.get("label_col", "label_main"),
                        _fmt_label(
                            overrides, lk, p.get("label") or "", p.get("label_format")
                        ),
                        table_left=table_left,
                        table_right=table_right,
                        label_key=lk,
                        label_format=p.get("label_format"),
                    )
                )
                cells.append(
                    _mk(
                        columns,
                        p.get("value_col", "value_left"),
                        "",
                        is_label=False,
                        table_left=table_left,
                        table_right=table_right,
                        field_key=str(p.get("field_key") or ""),
                    )
                )
            out.append({"id": rid, "type": rtype, "cells": cells})
        elif rtype == "grid":
            cells = []
            li = 0
            for c in row.get("cells") or []:
                if c.get("role") == "label":
                    lk = f"{rid}::grid:{li}:label"
                    cells.append(
                        _mk(
                            columns,
                            c.get("col", "label_main"),
                            _fmt_label(
                                overrides, lk, c.get("text", ""), c.get("label_format")
                            ),
                            table_left=table_left,
                            table_right=table_right,
                            label_key=lk,
                            label_format=c.get("label_format"),
                        )
                    )
                    li += 1
                elif c.get("role") == "value":
                    cells.append(
                        _mk(
                            columns,
                            c.get("col", "value_left"),
                            "",
                            is_label=False,
                            table_left=table_left,
                            table_right=table_right,
                            field_key=str(c.get("field_key") or ""),
                            value_format=c.get("value_format"),
                        )
                    )
            out.append({"id": rid, "type": rtype, "cells": cells})
        else:
            out.append({"id": rid, "type": rtype or "", "cells": []})
        i += 1

    return out


def build_main_table_preview(
    *,
    base_format: Optional[Dict[str, Any]] = None,
    base_format_path: Optional[str] = None,
    table_template: str = "250075YP",
    label_overrides: Optional[Dict[str, str]] = None,
    grid: Optional[Dict[str, Any]] = None,
    workspace: Optional[Any] = None,
    persist_seeded_grid: bool = False,
    rematerialize_columns: bool = False,
) -> Dict[str, Any]:
    from pathlib import Path

    from apps.core.f1_eval_editor import apply_label_overrides_to_rows
    from apps.core.f1_eval_grid import (
        grid_to_html_rows,
        load_grid,
        normalize_grid,
        rematerialize_grid_from_skeleton,
        save_grid,
        seed_grid_from_skeleton,
    )

    overrides = dict(label_overrides or {})
    rows = all_row_definitions(table_template)
    rows = apply_label_overrides_to_rows(rows, overrides)

    fmt = base_format
    if fmt is None and base_format_path:
        fmt = load_base_format(base_format_path)
    if fmt is None:
        fmt = load_base_format()
    fmt = copy.deepcopy(fmt)
    table = fmt.setdefault("table", {})
    if not isinstance(table, dict):
        table = {}
        fmt["table"] = table
    widths = dict(default_column_widths_mm())
    widths.update(table.get("column_widths_mm") or {})
    table["column_widths_mm"] = widths

    layout = assemble_layout(base_format=fmt, rows=rows, table_template=table_template)
    table_layout = layout.get("table") or {}
    columns = table_layout.get("columns") or {}
    table_left = float(table_layout.get("x_left") or 0)
    table_right = float(table_layout.get("x_right") or 0)
    page = layout.get("page_size") or {}
    margins = layout.get("margins_mm") or {}
    text = layout.get("text") or {}
    tw = max(1.0, table_right - table_left)

    skeleton = build_merged_skeleton(rows, columns, table_left, table_right, overrides)
    row_min = float(table_layout.get("row_min_height") or table.get("row_min_height_pt") or 25.0)

    active_grid = None
    if isinstance(grid, dict):
        active_grid = normalize_grid(grid)
    elif workspace is not None:
        active_grid = load_grid(Path(workspace), table_template)
    if active_grid is None:
        active_grid = seed_grid_from_skeleton(skeleton, row_min_height_pt=max(18.0, row_min))
        if persist_seeded_grid and workspace is not None:
            save_grid(Path(workspace), active_grid, table_template)
    elif rematerialize_columns:
        # 列宽变更后按最新骨架重算列界，避免子表头仍沿用旧网格宽度
        active_grid = rematerialize_grid_from_skeleton(
            active_grid, skeleton, row_min_height_pt=max(18.0, row_min)
        )
        if persist_seeded_grid and workspace is not None:
            save_grid(Path(workspace), active_grid, table_template)

    # 用自由网格驱动预览行（支持 rowspan/colspan）
    html_rows = grid_to_html_rows(active_grid)
    preview_rows: List[Dict[str, Any]] = []
    for hr in html_rows:
        preview_rows.append(
            {
                "band": hr.get("band"),
                "tall": float(hr.get("height_pt") or row_min) > row_min * 1.3,
                "height_pt": hr.get("height_pt"),
                "cells": list(hr.get("cells") or []),
            }
        )

    def pct(col_name: str, edge: str = "x1") -> float:
        c = columns.get(col_name)
        if not c:
            return 0.0
        return round((float(c[edge]) - table_left) / tw * 100, 3)

    guides = [
        {"key": "label_main", "label": "主表头", "pct": pct("label_main"), "width_key": "label_main"},
        {
            "key": "label_main_half",
            "label": "半宽主表头",
            "pct": pct("label_main_half"),
            "width_key": "label_main_half",
        },
        {
            "key": "sub_label_row_half",
            "label": "半宽子表头",
            "pct": pct("sub_label_row_half"),
            "width_key": "sub_label_row_half",
        },
        {"key": "mid", "label": "中间分界", "pct": pct("label_mid", "x0"), "width_key": "mid_offset"},
        {"key": "pair", "label": "右侧分界", "pct": pct("label_pair", "x0"), "width_key": "pair_offset"},
    ]

    # 侧栏同步用的当前栏目名
    from apps.core.f1_eval_editor import SECTION_NAV_HIDDEN, SECTION_NAV_TITLES

    nav = []
    for sid, _b in get_section_builders(table_template):
        if sid in SECTION_NAV_HIDDEN:
            continue
        primary = SECTION_PRIMARY_LABEL_KEY.get(sid)
        title = SECTION_NAV_TITLES.get(sid, sid)
        if primary and primary in overrides and str(overrides[primary]).strip():
            title = _flatten_label(overrides[primary]) or title
        nav.append({"id": sid, "title": title, "label_key": primary or ""})

    return {
        "page_width_pt": float(page.get("width") or 595.3),
        "page_height_pt": float(page.get("height") or 841.9),
        "margins_mm": margins,
        "table_left_pt": table_left,
        "table_right_pt": table_right,
        "table_width_pt": tw,
        "table_width_mm": round(tw / (72.0 / 25.4), 2),
        "border_width_pt": float(table_layout.get("border_width_pt") or table.get("border_width_pt") or 0.6),
        "line_spacing_pt": float(text.get("line_spacing_pt") or 25.0),
        "row_min_height_pt": float(table_layout.get("row_min_height") or table.get("row_min_height_pt") or 25.0),
        "font_size_pt": float(text.get("font_size_pt") or 12),
        "title": {
            "table_label": layout.get("table_label") or "",
            "table_name": layout.get("table_title") or "",
            "gap_below_pt": float((layout.get("title") or {}).get("gap_below_pt") or 6.0),
        },
        "column_widths_mm": {
            k: widths[k]
            for k in (
                "label_main",
                "label_main_half",
                "sub_label_row_half",
                "mid_offset",
                "pair_offset",
            )
            if k in widths
        },
        "editable_columns": [
            {"key": "label_main", "label": "主表头宽", "min": 8, "max": 45},
            {"key": "label_main_half", "label": "半宽主表头", "min": 6, "max": 30},
            {"key": "sub_label_row_half", "label": "半宽子表头", "min": 6, "max": 35},
            {"key": "mid_offset", "label": "中间分界位置", "min": 40, "max": 120},
            {"key": "pair_offset", "label": "右侧分界位置", "min": 50, "max": 140},
        ],
        "guides": guides,
        "rows": preview_rows,
        "skeleton_rows": skeleton,
        "grid": active_grid,
        "grid_mode": True,
        "section_nav": nav,
    }
