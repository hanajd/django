# -*- coding: utf-8 -*-
"""F.1 主表自由网格：插删行列、合并/取消合并。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

GRID_SCHEMA = "f1_main_table_grid/v1"


def resolve_template_id(table_template: Optional[str] = None) -> str:
    try:
        from f1_eval_report.sections.catalog import resolve_table_template

        return resolve_table_template(table_template)
    except Exception:
        return str(table_template or "250075YP").strip() or "250075YP"


def template_data_dir(ws: Path, table_template: Optional[str] = None) -> Path:
    """工作区内按模板隔离的数据目录：data/templates/{250075YP|gbzt}/"""
    tid = resolve_template_id(table_template)
    d = Path(ws) / "data" / "templates" / tid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _near(a: float, b: float, eps: float = 0.6) -> bool:
    return abs(a - b) <= eps


def _snap_bounds(vals: Sequence[float], eps: float = 0.6) -> List[float]:
    xs = sorted(set(float(v) for v in vals))
    if not xs:
        return [0.0, 100.0]
    out = [xs[0]]
    for x in xs[1:]:
        if _near(x, out[-1], eps):
            out[-1] = (out[-1] + x) / 2.0
        else:
            out.append(x)
    if out[0] > 0.05:
        out.insert(0, 0.0)
    if out[-1] < 99.95:
        out.append(100.0)
    # normalize endpoints
    out[0] = 0.0
    out[-1] = 100.0
    return out


def _idx_of(bounds: List[float], pct: float) -> int:
    best_i, best_d = 0, 1e9
    for i, b in enumerate(bounds):
        d = abs(b - pct)
        if d < best_d:
            best_i, best_d = i, d
    return best_i


def empty_cell(r0: int, r1: int, c0: int, c1: int, **extra: Any) -> Dict[str, Any]:
    cell = {
        "r0": int(r0),
        "r1": int(r1),
        "c0": int(c0),
        "c1": int(c1),
        "text": "",
        "role": "value",
        "is_label": False,
        "editable": False,
        "label_key": "",
        "field_key": "",
    }
    cell.update(extra)
    return cell


def normalize_grid(grid: Dict[str, Any]) -> Dict[str, Any]:
    g = copy.deepcopy(grid or {})
    g["schema"] = GRID_SCHEMA
    fr = list(g.get("col_fractions") or [0.0, 1.0])
    if len(fr) < 2:
        fr = [0.0, 1.0]
    fr[0], fr[-1] = 0.0, 1.0
    # ensure increasing
    for i in range(1, len(fr)):
        if fr[i] <= fr[i - 1]:
            fr[i] = min(1.0, fr[i - 1] + 0.02)
    fr[-1] = 1.0
    g["col_fractions"] = [round(float(x), 6) for x in fr]
    ncol = len(fr) - 1
    g["col_count"] = ncol

    heights = list(g.get("row_heights_pt") or [])
    cells = list(g.get("cells") or [])
    max_r = 0
    cleaned: List[Dict[str, Any]] = []
    for c in cells:
        if not isinstance(c, dict):
            continue
        r0, r1 = int(c.get("r0", 0)), int(c.get("r1", 1))
        c0, c1 = int(c.get("c0", 0)), int(c.get("c1", 1))
        if r1 <= r0:
            r1 = r0 + 1
        if c1 <= c0:
            c1 = c0 + 1
        r0 = max(0, r0)
        c0 = max(0, min(c0, ncol - 1))
        c1 = max(c0 + 1, min(c1, ncol))
        max_r = max(max_r, r1)
        raw_text = str(c.get("text") or "").replace("\r\n", "\n").replace("\r", "\n")
        is_label = bool(c.get("is_label") or c.get("role") == "label")
        label_key = str(c.get("label_key") or "")
        # 仅「表头 label_key」压扁空白；表格正文保留换行（段首缩进依赖 \n）
        if label_key:
            cell_text = "".join(raw_text.split())
        else:
            cell_text = raw_text
        item = {
            "r0": r0,
            "r1": r1,
            "c0": c0,
            "c1": c1,
            "text": cell_text,
            "role": c.get("role") or ("label" if is_label else "value"),
            "is_label": is_label,
            "editable": bool(c.get("editable", c.get("is_label", False))),
            "label_key": label_key,
            "field_key": str(c.get("field_key") or ""),
            "row_id": str(c.get("row_id") or ""),
        }
        try:
            wc = int(c.get("wrap_chars") or 0)
        except (TypeError, ValueError):
            wc = 0
        # 旧数据：带 label_key 的表头文案里已有换行但无 wrap_chars → 视为两字换行
        orig_text = str(c.get("text") or "")
        if label_key and wc <= 0 and ("\n" in orig_text or "\r" in orig_text):
            wc = 2
        if label_key and wc > 0:
            item["wrap_chars"] = wc
        if c.get("widget"):
            item["widget"] = str(c.get("widget"))
            if isinstance(c.get("widget_options"), list):
                item["widget_options"] = c.get("widget_options")
            if isinstance(c.get("widget_line_sep"), dict):
                item["widget_line_sep"] = c.get("widget_line_sep")
        align = str(c.get("align") or "").strip().lower()
        if align in ("left", "center", "right"):
            item["align"] = align
        if c.get("first_indent") in (True, 1, "1", "true", "True"):
            item["first_indent"] = True
        # 逐段首行缩进（与主页面 para_indents 一致）
        pi = c.get("para_indents")
        if isinstance(pi, list) and pi:
            item["para_indents"] = [bool(x) for x in pi]
            if any(item["para_indents"]):
                item["first_indent"] = True
            elif "first_indent" not in item:
                pass
        try:
            fs = float(c.get("font_size_pt")) if c.get("font_size_pt") is not None else None
        except (TypeError, ValueError):
            fs = None
        if fs is not None and fs > 0:
            item["font_size_pt"] = max(8.0, min(24.0, fs))
        if c.get("cell_role"):
            item["cell_role"] = str(c.get("cell_role"))
        if c.get("formula"):
            item["formula"] = str(c.get("formula"))
        try:
            if c.get("time_seconds") is not None:
                item["time_seconds"] = float(c.get("time_seconds"))
        except (TypeError, ValueError):
            pass
        try:
            if c.get("rate_usv_h") is not None:
                item["rate_usv_h"] = float(c.get("rate_usv_h"))
        except (TypeError, ValueError):
            pass
        try:
            if c.get("workload_h") is not None:
                item["workload_h"] = float(c.get("workload_h"))
        except (TypeError, ValueError):
            pass
        try:
            if c.get("occupancy") is not None:
                item["occupancy"] = float(c.get("occupancy"))
        except (TypeError, ValueError):
            pass
        try:
            if c.get("time_hours") is not None:
                item["time_hours"] = float(c.get("time_hours"))
        except (TypeError, ValueError):
            pass
        try:
            if c.get("max_load_h") is not None:
                item["max_load_h"] = float(c.get("max_load_h"))
        except (TypeError, ValueError):
            pass
        try:
            if c.get("cases_per_year") is not None:
                item["cases_per_year"] = float(c.get("cases_per_year"))
        except (TypeError, ValueError):
            pass
        try:
            if c.get("batch_count") is not None:
                item["batch_count"] = float(c.get("batch_count"))
        except (TypeError, ValueError):
            pass
        if c.get("mode_role"):
            item["mode_role"] = str(c.get("mode_role"))
        cleaned.append(item)
    nrow = max(max_r, len(heights), 1)
    while len(heights) < nrow:
        heights.append(22.0)
    heights = [max(14.0, float(h)) for h in heights[:nrow]]
    g["row_heights_pt"] = heights
    g["row_count"] = nrow
    g["cells"] = cleaned
    # fill missing 1x1 holes so table is complete
    g["cells"] = _fill_holes(g)
    return g


def _occupied(cells: List[Dict[str, Any]], nrow: int, ncol: int) -> List[List[bool]]:
    occ = [[False] * ncol for _ in range(nrow)]
    for c in cells:
        for r in range(c["r0"], min(c["r1"], nrow)):
            for col in range(c["c0"], min(c["c1"], ncol)):
                occ[r][col] = True
    return occ


def _fill_holes(grid: Dict[str, Any]) -> List[Dict[str, Any]]:
    cells = list(grid.get("cells") or [])
    nrow = int(grid.get("row_count") or 1)
    ncol = int(grid.get("col_count") or 1)
    occ = _occupied(cells, nrow, ncol)
    for r in range(nrow):
        for c in range(ncol):
            if not occ[r][c]:
                cells.append(empty_cell(r, r + 1, c, c + 1))
                occ[r][c] = True
    return cells


def rematerialize_grid_from_skeleton(
    old_grid: Optional[Dict[str, Any]],
    skeleton_rows: List[Dict[str, Any]],
    *,
    row_min_height_pt: float = 22.0,
) -> Dict[str, Any]:
    """按最新骨架重算列界/单元格几何，保留原网格中按 label_key（及坐标）的文字与行高。"""
    new_g = seed_grid_from_skeleton(skeleton_rows, row_min_height_pt=row_min_height_pt)
    if not isinstance(old_grid, dict):
        return new_g
    old = normalize_grid(old_grid)
    by_key: Dict[str, str] = {}
    by_pos: Dict[Tuple[int, int], str] = {}
    for c in old.get("cells") or []:
        text = str(c.get("text") or "")
        key = str(c.get("label_key") or "")
        if key and text.strip():
            by_key[key] = text
        by_pos[(int(c.get("r0") or 0), int(c.get("c0") or 0))] = text
    for c in new_g.get("cells") or []:
        key = str(c.get("label_key") or "")
        if key and key in by_key:
            old_text = by_key[key]
            skel_text = str(c.get("text") or "")
            # 用户改过文案才覆盖；否则保留骨架里的两字换行
            if "".join(old_text.split()) != "".join(skel_text.split()):
                c["text"] = "".join(old_text.split())
                c["is_label"] = True
                c["editable"] = True
                c["role"] = "label"
        else:
            pos = (int(c.get("r0") or 0), int(c.get("c0") or 0))
            if pos in by_pos and by_pos[pos].strip() and not str(c.get("text") or "").strip():
                c["text"] = by_pos[pos]
    if int(old.get("row_count") or 0) == int(new_g.get("row_count") or 0):
        heights = list(old.get("row_heights_pt") or [])
        if len(heights) == int(new_g.get("row_count") or 0):
            new_g["row_heights_pt"] = [max(14.0, float(h)) for h in heights]
    return normalize_grid(new_g)


def seed_grid_from_skeleton(
    skeleton_rows: List[Dict[str, Any]],
    *,
    row_min_height_pt: float = 22.0,
) -> Dict[str, Any]:
    """把带 left_pct/width_pct/rowspan 的骨架行转为自由网格。"""
    bounds: List[float] = [0.0, 100.0]
    for row in skeleton_rows:
        for cell in row.get("cells") or []:
            left = float(cell.get("left_pct") or 0)
            width = float(cell.get("width_pct") or 0)
            bounds.append(left)
            bounds.append(left + width)
    bounds = _snap_bounds(bounds)
    # convert % bounds → fractions 0..1
    fr = [round(b / 100.0, 6) for b in bounds]
    ncol = len(fr) - 1

    cells: List[Dict[str, Any]] = []
    heights: List[float] = []
    band = 0
    for row in skeleton_rows:
        tall = bool(row.get("tall"))
        h = row_min_height_pt * (1.55 if tall else 1.0)
        heights.append(round(h, 1))
        for cell in row.get("cells") or []:
            rs = max(1, int(cell.get("rowspan") or 1))
            left = float(cell.get("left_pct") or 0)
            width = float(cell.get("width_pct") or 0)
            c0 = _idx_of(bounds, left)
            c1 = _idx_of(bounds, left + width)
            if c1 <= c0:
                c1 = min(ncol, c0 + 1)
            is_label = bool(cell.get("is_label"))
            gcell = {
                "r0": band,
                "r1": band + rs,
                "c0": c0,
                "c1": c1,
                "text": "".join(str(cell.get("text") or "").split()) if is_label else "",
                "role": "label" if is_label else "value",
                "is_label": is_label,
                "editable": bool(cell.get("editable")),
                "label_key": str(cell.get("label_key") or ""),
                "field_key": str(cell.get("field_key") or ""),
                "row_id": str(row.get("id") or ""),
            }
            if cell.get("widget"):
                gcell["widget"] = cell.get("widget")
                if cell.get("widget_options") is not None:
                    gcell["widget_options"] = cell.get("widget_options")
                if cell.get("widget_line_sep") is not None:
                    gcell["widget_line_sep"] = cell.get("widget_line_sep")
            wc = int(cell.get("wrap_chars") or 0)
            if wc > 0 and is_label:
                gcell["wrap_chars"] = wc
            cells.append(gcell)
        band += 1

    # ensure rowspan target bands exist in heights
    max_r = max((c["r1"] for c in cells), default=band)
    while len(heights) < max_r:
        heights.append(row_min_height_pt)

    return normalize_grid(
        {
            "schema": GRID_SCHEMA,
            "col_fractions": fr,
            "row_heights_pt": heights,
            "cells": cells,
        }
    )


def grid_to_html_rows(grid: Dict[str, Any]) -> List[Dict[str, Any]]:
    """转为前端 HTML 表行（含 rowspan/colspan）。"""
    g = normalize_grid(grid)
    nrow, ncol = g["row_count"], g["col_count"]
    fr = g["col_fractions"]
    # map (r,c) -> owning cell
    owner: List[List[Optional[Dict[str, Any]]]] = [[None] * ncol for _ in range(nrow)]
    for cell in g["cells"]:
        for r in range(cell["r0"], cell["r1"]):
            for c in range(cell["c0"], cell["c1"]):
                if 0 <= r < nrow and 0 <= c < ncol:
                    owner[r][c] = cell

    rows_out: List[Dict[str, Any]] = []
    for r in range(nrow):
        row_cells = []
        c = 0
        while c < ncol:
            cell = owner[r][c]
            if cell is None:
                c += 1
                continue
            if cell["r0"] != r or cell["c0"] != c:
                c += 1
                continue
            width_pct = round((fr[cell["c1"]] - fr[cell["c0"]]) * 100, 3)
            item = {
                "text": cell.get("text") or "",
                "is_label": bool(cell.get("is_label")),
                "editable": bool(cell.get("editable") or cell.get("is_label")),
                "label_key": cell.get("label_key") or "",
                "field_key": cell.get("field_key") or "",
                "row_id": cell.get("row_id") or "",
                "role": cell.get("role") or "value",
                "rowspan": cell["r1"] - cell["r0"],
                "colspan": cell["c1"] - cell["c0"],
                "r0": cell["r0"],
                "r1": cell["r1"],
                "c0": cell["c0"],
                "c1": cell["c1"],
                "width_pct": width_pct,
            }
            if cell.get("widget"):
                item["widget"] = cell.get("widget")
                if cell.get("widget_options") is not None:
                    item["widget_options"] = cell.get("widget_options")
                if cell.get("widget_line_sep") is not None:
                    item["widget_line_sep"] = cell.get("widget_line_sep")
            wc = int(cell.get("wrap_chars") or 0)
            if wc > 0:
                item["wrap_chars"] = wc
            if cell.get("align") in ("left", "center", "right"):
                item["align"] = cell.get("align")
            if cell.get("first_indent"):
                item["first_indent"] = True
            if isinstance(cell.get("para_indents"), list) and cell.get("para_indents"):
                item["para_indents"] = [bool(x) for x in cell["para_indents"]]
            if cell.get("font_size_pt") is not None:
                try:
                    item["font_size_pt"] = float(cell.get("font_size_pt"))
                except (TypeError, ValueError):
                    pass
            row_cells.append(item)
            c = cell["c1"]
        rows_out.append(
            {
                "band": r,
                "height_pt": g["row_heights_pt"][r],
                "cells": row_cells,
            }
        )
    return rows_out


def insert_row(grid: Dict[str, Any], at: int, *, height_pt: float = 22.0) -> Dict[str, Any]:
    g = normalize_grid(grid)
    at = max(0, min(int(at), g["row_count"]))
    cells = []
    for c in g["cells"]:
        nc = dict(c)
        if nc["r0"] >= at:
            nc["r0"] += 1
            nc["r1"] += 1
        elif nc["r1"] > at:
            nc["r1"] += 1  # span grows
        cells.append(nc)
    heights = list(g["row_heights_pt"])
    heights.insert(at, float(height_pt))
    # add empty cells for columns not covered by expanded spans
    g["cells"] = cells
    g["row_heights_pt"] = heights
    g["row_count"] = len(heights)
    return normalize_grid(g)


def delete_row(grid: Dict[str, Any], at: int) -> Dict[str, Any]:
    g = normalize_grid(grid)
    if g["row_count"] <= 1:
        raise ValueError("至少保留一行")
    at = max(0, min(int(at), g["row_count"] - 1))
    cells = []
    for c in g["cells"]:
        if c["r0"] == at and c["r1"] == at + 1:
            continue  # drop single-row cell
        nc = dict(c)
        if nc["r0"] > at:
            nc["r0"] -= 1
            nc["r1"] -= 1
        elif nc["r0"] <= at < nc["r1"]:
            nc["r1"] -= 1
            if nc["r1"] <= nc["r0"]:
                continue
        cells.append(nc)
    heights = list(g["row_heights_pt"])
    del heights[at]
    g["cells"] = cells
    g["row_heights_pt"] = heights
    g["row_count"] = len(heights)
    return normalize_grid(g)


def insert_col(grid: Dict[str, Any], at: int) -> Dict[str, Any]:
    """在列索引 at 处插入全新空列（与 insert_row 对称）。

    - 左侧插列：at = 选区 c0 → 空列出现在选中列左侧，原选中列及其右侧整体右移
    - 右侧插列：at = 选区 c1 → 空列出现在选中列右侧
    跨插入点的合并单元格会向右扩展一列；空位由 normalize/_fill_holes 补齐。
    """
    g = normalize_grid(grid)
    fr = list(g["col_fractions"])
    ncol = g["col_count"]
    at = max(0, min(int(at), ncol))

    # 从将被挤开的列（或末列）借一半宽度给新空列
    if at >= ncol:
        mid = (fr[-2] + fr[-1]) / 2.0
        fr.insert(-1, round(mid, 6))
    else:
        mid = (fr[at] + fr[at + 1]) / 2.0
        fr.insert(at + 1, round(mid, 6))

    cells = []
    for c in g["cells"]:
        nc = dict(c)
        if nc["c0"] >= at:
            nc["c0"] += 1
            nc["c1"] += 1
        elif nc["c1"] > at:
            nc["c1"] += 1  # 跨插入点的合并格向右扩
        cells.append(nc)

    g["col_fractions"] = fr
    g["cells"] = cells
    return normalize_grid(g)


def delete_col(grid: Dict[str, Any], at: int) -> Dict[str, Any]:
    g = normalize_grid(grid)
    if g["col_count"] <= 1:
        raise ValueError("至少保留一列")
    at = max(0, min(int(at), g["col_count"] - 1))
    fr = list(g["col_fractions"])
    # remove boundary that collapses column at: remove fr[at+1] if at < n-1 else remove fr[at]
    if at < g["col_count"] - 1:
        del fr[at + 1]
    else:
        del fr[at]
    cells = []
    for c in g["cells"]:
        if c["c0"] == at and c["c1"] == at + 1:
            continue
        nc = dict(c)
        if nc["c0"] > at:
            nc["c0"] -= 1
            nc["c1"] -= 1
        elif nc["c0"] <= at < nc["c1"]:
            nc["c1"] -= 1
            if nc["c1"] <= nc["c0"]:
                continue
        cells.append(nc)
    g["col_fractions"] = fr
    g["cells"] = cells
    return normalize_grid(g)


def merge_cells(grid: Dict[str, Any], r0: int, c0: int, r1: int, c1: int) -> Dict[str, Any]:
    """合并闭开区间 [r0,r1) x [c0,c1)。"""
    g = normalize_grid(grid)
    r0, c0 = int(r0), int(c0)
    r1, c1 = int(r1), int(c1)
    if r1 <= r0 or c1 <= c0:
        raise ValueError("无效的合并区域")
    r0 = max(0, r0)
    c0 = max(0, c0)
    r1 = min(g["row_count"], r1)
    c1 = min(g["col_count"], c1)
    if r1 <= r0 or c1 <= c0:
        raise ValueError("无效的合并区域")

    # collect texts / meta from overlapping cells
    kept_text = ""
    meta = {"role": "label", "is_label": True, "editable": True, "label_key": "", "field_key": ""}
    new_cells = []
    for c in g["cells"]:
        overlap_r = not (c["r1"] <= r0 or c["r0"] >= r1)
        overlap_c = not (c["c1"] <= c0 or c["c0"] >= c1)
        if overlap_r and overlap_c:
            if (c.get("text") or "").strip() and not kept_text:
                kept_text = c.get("text") or ""
                meta = {
                    "role": c.get("role") or "label",
                    "is_label": bool(c.get("is_label", True)),
                    "editable": bool(c.get("editable", True)),
                    "label_key": c.get("label_key") or "",
                    "field_key": c.get("field_key") or "",
                }
            continue
        new_cells.append(c)
    new_cells.append(
        empty_cell(
            r0,
            r1,
            c0,
            c1,
            text=kept_text,
            **meta,
        )
    )
    g["cells"] = new_cells
    return normalize_grid(g)


def unmerge_cell(grid: Dict[str, Any], r0: int, c0: int) -> Dict[str, Any]:
    """取消包含 (r0,c0) 的合并单元格。"""
    g = normalize_grid(grid)
    r0, c0 = int(r0), int(c0)
    target = None
    for c in g["cells"]:
        if c["r0"] <= r0 < c["r1"] and c["c0"] <= c0 < c["c1"]:
            target = c
            break
    if not target:
        raise ValueError("未找到单元格")
    if target["r1"] - target["r0"] == 1 and target["c1"] - target["c0"] == 1:
        return g  # already 1x1
    cells = [
        c
        for c in g["cells"]
        if not (
            c["r0"] == target["r0"]
            and c["r1"] == target["r1"]
            and c["c0"] == target["c0"]
            and c["c1"] == target["c1"]
        )
    ]
    for r in range(target["r0"], target["r1"]):
        for col in range(target["c0"], target["c1"]):
            text = target.get("text") or "" if (r == target["r0"] and col == target["c0"]) else ""
            cells.append(
                empty_cell(
                    r,
                    r + 1,
                    col,
                    col + 1,
                    text=text,
                    role=target.get("role") or "value",
                    is_label=bool(target.get("is_label")) if text else False,
                    editable=bool(target.get("editable")) if text else False,
                    label_key=(target.get("label_key") or "") if text else "",
                    field_key=(target.get("field_key") or "") if text else "",
                )
            )
    g["cells"] = cells
    return normalize_grid(g)


def update_cell_text(
    grid: Dict[str, Any],
    r0: int,
    c0: int,
    text: str,
    *,
    first_indent: Optional[bool] = None,
    para_indents: Optional[List[bool]] = None,
) -> Dict[str, Any]:
    g = normalize_grid(grid)
    for c in g["cells"]:
        if c["r0"] <= r0 < c["r1"] and c["c0"] <= c0 < c["c1"]:
            raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
            # 有 label_key 的表头仍压成单行；其余单元格保留段落换行
            if c.get("label_key"):
                c["text"] = "".join(raw.split())
            else:
                c["text"] = raw
            if c["text"].strip():
                c["is_label"] = True
                c["editable"] = True
                c["role"] = c.get("role") or "label"
            if para_indents is not None:
                flags = [bool(x) for x in para_indents]
                if flags:
                    c["para_indents"] = flags
                    c["first_indent"] = any(flags)
                else:
                    c.pop("para_indents", None)
                    c.pop("first_indent", None)
            elif first_indent is True:
                c["first_indent"] = True
            elif first_indent is False:
                c.pop("first_indent", None)
                c.pop("para_indents", None)
            break
    return normalize_grid(g)


def set_col_fractions(grid: Dict[str, Any], fractions: List[float]) -> Dict[str, Any]:
    g = normalize_grid(grid)
    fr = [0.0] + [float(x) for x in fractions] + [1.0] if False else list(fractions)
    if len(fr) != g["col_count"] + 1:
        # allow replace
        if len(fr) < 2:
            raise ValueError("列分界无效")
    fr = sorted(float(x) for x in fr)
    fr[0], fr[-1] = 0.0, 1.0
    g["col_fractions"] = fr
    # if col count changed, normalize will fix holes badly — keep same count
    if len(fr) - 1 != g["col_count"]:
        # rebuild col_count; cells may be invalid — clamp in normalize
        pass
    return normalize_grid(g)


def load_grid(ws: Path, table_template: Optional[str] = None) -> Optional[Dict[str, Any]]:
    tid = resolve_template_id(table_template)
    candidates = [
        template_data_dir(ws, tid) / "main_table_grid.json",
        Path(ws) / "data" / f"main_table_grid_{tid}.json",
        Path(ws) / "data" / "main_table_grid.json",  # 旧版共用文件回退
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(raw, dict):
            return normalize_grid(raw)
    return None


def save_grid(
    ws: Path,
    grid: Dict[str, Any],
    table_template: Optional[str] = None,
) -> Dict[str, Any]:
    g = normalize_grid(grid)
    path = template_data_dir(ws, table_template) / "main_table_grid.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")
    return g


def load_template_table_format(
    ws: Path, table_template: Optional[str] = None
) -> Dict[str, Any]:
    tid = resolve_template_id(table_template)
    candidates = [
        template_data_dir(ws, tid) / "table_format.json",
        Path(ws) / "data" / f"table_format_{tid}.json",
        Path(ws) / "data" / "table_format.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(raw, dict):
            return raw
    return {}


def save_template_table_format(
    ws: Path,
    data: Dict[str, Any],
    table_template: Optional[str] = None,
) -> None:
    path = template_data_dir(ws, table_template) / "table_format.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data or {}, ensure_ascii=False, indent=2), encoding="utf-8")


def grid_label_overrides(grid: Dict[str, Any]) -> Dict[str, str]:
    """从网格可编辑表头提取 label_overrides（规范文案，无空白/换行）。"""
    out: Dict[str, str] = {}
    for c in normalize_grid(grid).get("cells") or []:
        key = c.get("label_key") or ""
        if key and c.get("text") is not None:
            out[str(key)] = "".join(str(c.get("text") or "").split())
    return out
