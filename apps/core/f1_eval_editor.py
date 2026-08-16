# -*- coding: utf-8 -*-
"""F.1 结构化栏目编辑：表头/单元格、嵌套表、附图。"""

from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SECTION_NAV_TITLES: Dict[str, str] = {
    "org_header": "建设单位信息",
    "project_info": "项目基础信息",
    "radiation_source": "辐射源项",
    "project_summary": "项目概述",
    "project_classification": "建设项目分类",
    "evaluation_basis": "主要评价依据",
    "evaluation_objective": "评价目标",
    "hazard_analysis": "危害因素分析",
    "workplace_layout": "工作场所布局",
    "protection_measures": "防护设施和措施",
    "health_impact": "健康影响评价",
    "radiation_management": "放射防护管理",
    "conclusion": "结论与建议",
    "attachments": "附件清单",
}

# 导航中隐藏，并入目标栏目一起编辑（PDF 行定义仍保持分栏）
SECTION_NAV_HIDDEN = {"org_header"}
SECTION_NAV_MERGE_INTO = {"org_header": "project_info"}


def flatten_label_text(text: Any) -> str:
    """栏目名称规范文案：去掉空白/换行（两字换行只在表头渲染时处理）。"""
    return "".join(str(text or "").split())


def load_label_overrides(ws: Path, table_template: Optional[str] = None) -> Dict[str, str]:
    from apps.core.f1_eval_grid import resolve_template_id, template_data_dir

    tid = resolve_template_id(table_template)
    candidates = [
        template_data_dir(ws, tid) / "label_overrides.json",
        Path(ws) / "data" / f"label_overrides_{tid}.json",
        Path(ws) / "data" / "label_overrides.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(raw, dict):
            return {str(k): flatten_label_text(v) for k, v in raw.items() if str(k)}
    return {}


def save_label_overrides(
    ws: Path,
    overrides: Dict[str, str],
    table_template: Optional[str] = None,
) -> None:
    from apps.core.f1_eval_grid import template_data_dir

    path = template_data_dir(ws, table_template) / "label_overrides.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {str(k): flatten_label_text(v) for k, v in (overrides or {}).items() if str(k)}
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")


def editor_nav(table_template: str = "250075YP", ws: Optional[Path] = None) -> List[Dict[str, str]]:
    from f1_eval_report.sections.catalog import get_section_builders
    from apps.core.f1_eval_table_preview import SECTION_PRIMARY_LABEL_KEY

    overrides = load_label_overrides(ws, table_template) if ws is not None else {}
    out: List[Dict[str, str]] = []
    for sid, _builder in get_section_builders(table_template):
        if sid in SECTION_NAV_HIDDEN:
            continue
        title = SECTION_NAV_TITLES.get(sid, sid)
        primary = SECTION_PRIMARY_LABEL_KEY.get(sid)
        if primary and primary in overrides and str(overrides[primary]).strip():
            title = flatten_label_text(overrides[primary]) or title
        out.append({"id": sid, "title": title})
    return out


def _field_get(data: Dict[str, Any], field_key: str) -> str:
    if not field_key:
        return ""
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    val = fields.get(field_key)
    if val is None:
        val = data.get(field_key)
    if val is None:
        return ""
    if isinstance(val, list):
        return "\n".join(str(x) for x in val)
    return str(val)


def _field_set(data: Dict[str, Any], field_key: str, value: str) -> None:
    if not field_key:
        return
    fields = data.setdefault("fields", {})
    if not isinstance(fields, dict):
        fields = {}
        data["fields"] = fields
    fields[field_key] = value


def _top_get(data: Dict[str, Any], key: str) -> str:
    if not key:
        return ""
    val = data.get(key)
    if val is None or isinstance(val, (dict, list)):
        # also try fields
        return _field_get(data, key)
    return str(val)


def _top_set_text(data: Dict[str, Any], key: str, value: str) -> None:
    """Write intro/text keys to both top-level and fields for generator compatibility."""
    if not key:
        return
    data[key] = value
    _field_set(data, key, value)


def _label_text(cell_or_fmt: Any) -> str:
    if isinstance(cell_or_fmt, dict):
        if cell_or_fmt.get("text"):
            return flatten_label_text(cell_or_fmt.get("text"))
        fmt = cell_or_fmt.get("label_format") or {}
        lines = fmt.get("lines") if isinstance(fmt, dict) else None
        if lines:
            return flatten_label_text("".join(str(x) for x in lines))
    return flatten_label_text(cell_or_fmt)


def _wrap_chars_of(fmt: Any) -> int:
    if isinstance(fmt, dict):
        try:
            return max(0, int(fmt.get("wrap_chars") or 0))
        except (TypeError, ValueError):
            return 0
    return 0


def apply_label_overrides_to_rows(
    rows: List[Dict[str, Any]], overrides: Dict[str, str]
) -> List[Dict[str, Any]]:
    if not overrides:
        return rows
    out = copy.deepcopy(rows)
    for row in out:
        rid = str(row.get("id") or "")
        if not rid:
            continue
        if f"{rid}::label" in overrides and "label" in row:
            row["label"] = flatten_label_text(overrides[f"{rid}::label"])
        if f"{rid}::sub_label" in overrides and "sub_label" in row:
            row["sub_label"] = flatten_label_text(overrides[f"{rid}::sub_label"])
        if f"{rid}::section_label" in overrides and "section_label" in row:
            row["section_label"] = flatten_label_text(overrides[f"{rid}::section_label"])
            fmt = row.get("section_label_format")
            if isinstance(fmt, dict) and "lines" in fmt:
                # 规范文案存连续字符串；换行仍由 wrap_chars 在绘制时处理
                fmt.pop("lines", None)

        rtype = row.get("type")
        if rtype == "pairs":
            for i, pair in enumerate(row.get("pairs") or []):
                k = f"{rid}::pair:{i}:label"
                if k in overrides:
                    pair["label"] = flatten_label_text(overrides[k])
        elif rtype == "grid":
            li = 0
            for cell in row.get("cells") or []:
                if cell.get("role") != "label":
                    continue
                k = f"{rid}::grid:{li}:label"
                if k in overrides:
                    cell["text"] = flatten_label_text(overrides[k])
                li += 1
        elif rtype == "section":
            for i, line in enumerate(row.get("lines") or []):
                for cell in line.get("cells") or []:
                    if cell.get("role") != "label":
                        continue
                    k = f"{rid}::line:{i}:label"
                    if k in overrides:
                        cell["text"] = flatten_label_text(overrides[k])
                    break
    return out


def table_to_grid(table: Dict[str, Any]) -> List[List[str]]:
    """兼容旧二维文本视图（会丢失合并）；优先用 embedded_to_edit_grid。"""
    rows = table.get("rows") or []
    ncol = int(table.get("column_count") or 0)
    if ncol <= 0 and rows:
        for band in rows:
            for cell in band.get("cells") or []:
                ncol = max(ncol, int(cell.get("c1") or 0), int(cell.get("c0") or 0) + 1)
    if ncol <= 0:
        ncol = 1
    grid: List[List[str]] = []
    for band in rows:
        row = [""] * ncol
        for cell in band.get("cells") or []:
            c0 = int(cell.get("c0") or 0)
            if 0 <= c0 < ncol:
                row[c0] = "" if cell.get("text") is None else str(cell.get("text"))
        grid.append(row)
    return grid


def grid_to_table(grid: List[List[Any]], base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """旧二维文本 → 嵌套表（无合并）。"""
    ncol = max((len(r) for r in grid), default=1) or 1
    rows: List[Dict[str, Any]] = []
    for band, raw in enumerate(grid):
        cells = []
        for c0 in range(ncol):
            text = ""
            if c0 < len(raw) and raw[c0] is not None:
                text = str(raw[c0])
            cells.append({"c0": c0, "c1": c0 + 1, "row_span": 1, "col_span": 1, "text": text})
        rows.append({"band": band, "cells": cells})
    out: Dict[str, Any] = {
        "schema": "embedded_generic_table/v1",
        "column_count": ncol,
        "band_count": len(rows),
        "column_fractions": [round(i / ncol, 6) for i in range(ncol + 1)],
        "row_min_height_pt": 17.5,
        "repeat_header_on_new_page": True,
        "cell_align": "center",
        "rows": rows,
    }
    if isinstance(base, dict):
        for keep in ("title", "header_bold", "cell_align", "row_min_height_pt", "repeat_header_on_new_page", "source"):
            if keep in base:
                out[keep] = base[keep]
        old_frac = base.get("column_fractions")
        if isinstance(old_frac, list) and len(old_frac) == ncol + 1:
            out["column_fractions"] = old_frac
    return out


def embedded_to_edit_grid(table: Dict[str, Any]) -> Dict[str, Any]:
    """嵌套表 → 可编辑自由网格（保留合并与列宽/行高）。"""
    from apps.core.f1_eval_grid import normalize_grid

    rows = table.get("rows") or []
    ncol = int(table.get("column_count") or 0)
    if ncol <= 0:
        for band in rows:
            for cell in band.get("cells") or []:
                ncol = max(ncol, int(cell.get("c1") or 0), int(cell.get("c0") or 0) + int(cell.get("col_span") or 1))
    if ncol <= 0:
        ncol = 1

    fr = table.get("column_fractions")
    if not isinstance(fr, list) or len(fr) != ncol + 1:
        fr = [round(i / ncol, 6) for i in range(ncol + 1)]
    fr = [float(x) for x in fr]
    fr[0], fr[-1] = 0.0, 1.0

    cells: List[Dict[str, Any]] = []
    max_r = max(1, len(rows))
    for band_i, band in enumerate(rows):
        if not isinstance(band, dict):
            continue
        for cell in band.get("cells") or []:
            if not isinstance(cell, dict):
                continue
            c0 = int(cell.get("c0") or 0)
            c1 = int(cell.get("c1") or (c0 + int(cell.get("col_span") or 1)))
            rs = max(1, int(cell.get("row_span") or 1))
            cs = max(1, int(cell.get("col_span") or (c1 - c0)))
            if c1 <= c0:
                c1 = c0 + cs
            max_r = max(max_r, band_i + rs)
            cell_out: Dict[str, Any] = {
                "r0": band_i,
                "r1": band_i + rs,
                "c0": c0,
                "c1": c1,
                "text": "" if cell.get("text") is None else str(cell.get("text")),
                "role": "value",
                "is_label": False,
                "editable": True,
                "label_key": "",
                "field_key": "",
                "align": (
                    str(cell.get("align")).strip().lower()
                    if str(cell.get("align") or "").strip().lower() in ("left", "center", "right")
                    else ("center" if band_i == 0 else "left")
                ),
            }
            try:
                if cell.get("font_size_pt") is not None:
                    cell_out["font_size_pt"] = float(cell.get("font_size_pt"))
            except (TypeError, ValueError):
                pass
            if cell.get("first_indent"):
                cell_out["first_indent"] = True
            if isinstance(cell.get("para_indents"), list) and cell.get("para_indents"):
                cell_out["para_indents"] = [bool(x) for x in cell["para_indents"]]
            if cell.get("cell_role"):
                cell_out["cell_role"] = str(cell.get("cell_role"))
            if cell.get("formula"):
                cell_out["formula"] = str(cell.get("formula"))
            try:
                if cell.get("time_seconds") is not None:
                    cell_out["time_seconds"] = float(cell.get("time_seconds"))
            except (TypeError, ValueError):
                pass
            try:
                if cell.get("rate_usv_h") is not None:
                    cell_out["rate_usv_h"] = float(cell.get("rate_usv_h"))
            except (TypeError, ValueError):
                pass
            try:
                if cell.get("workload_h") is not None:
                    cell_out["workload_h"] = float(cell.get("workload_h"))
            except (TypeError, ValueError):
                pass
            try:
                if cell.get("occupancy") is not None:
                    cell_out["occupancy"] = float(cell.get("occupancy"))
            except (TypeError, ValueError):
                pass
            try:
                if cell.get("time_hours") is not None:
                    cell_out["time_hours"] = float(cell.get("time_hours"))
            except (TypeError, ValueError):
                pass
            try:
                if cell.get("max_load_h") is not None:
                    cell_out["max_load_h"] = float(cell.get("max_load_h"))
            except (TypeError, ValueError):
                pass
            try:
                if cell.get("cases_per_year") is not None:
                    cell_out["cases_per_year"] = float(cell.get("cases_per_year"))
            except (TypeError, ValueError):
                pass
            try:
                if cell.get("batch_count") is not None:
                    cell_out["batch_count"] = float(cell.get("batch_count"))
            except (TypeError, ValueError):
                pass
            if cell.get("mode_role"):
                cell_out["mode_role"] = str(cell.get("mode_role"))
            cells.append(cell_out)

    default_h = float(table.get("row_min_height_pt") or 17.5)
    heights = table.get("row_heights_pt")
    if not isinstance(heights, list) or not heights:
        heights = [default_h] * max_r
    while len(heights) < max_r:
        heights.append(default_h)

    return normalize_grid(
        {
            "schema": "f1_main_table_grid/v1",
            "col_fractions": fr,
            "row_heights_pt": [max(14.0, float(h)) for h in heights[:max_r]],
            "cells": cells,
        }
    )


def edit_grid_to_embedded(
    grid: Dict[str, Any], base: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """自由网格 → 嵌套表持久化结构。"""
    from apps.core.f1_eval_grid import normalize_grid

    g = normalize_grid(grid)
    nrow, ncol = int(g["row_count"]), int(g["col_count"])
    rows: List[Dict[str, Any]] = []
    for r in range(nrow):
        band_cells = []
        for cell in g.get("cells") or []:
            if int(cell.get("r0") or 0) != r:
                continue
            c0, c1 = int(cell["c0"]), int(cell["c1"])
            r0, r1 = int(cell["r0"]), int(cell["r1"])
            band_cells.append(
                {
                    "c0": c0,
                    "c1": c1,
                    "row_span": max(1, r1 - r0),
                    "col_span": max(1, c1 - c0),
                    "text": str(cell.get("text") or ""),
                    **(
                        {"align": cell.get("align")}
                        if cell.get("align") in ("left", "center", "right")
                        else {}
                    ),
                    **(
                        {"font_size_pt": float(cell.get("font_size_pt"))}
                        if cell.get("font_size_pt") is not None
                        else {}
                    ),
                    **({"first_indent": True} if cell.get("first_indent") else {}),
                    **(
                        {"para_indents": [bool(x) for x in cell["para_indents"]]}
                        if isinstance(cell.get("para_indents"), list) and cell.get("para_indents")
                        else {}
                    ),
                    **({"cell_role": str(cell.get("cell_role"))} if cell.get("cell_role") else {}),
                    **({"formula": str(cell.get("formula"))} if cell.get("formula") else {}),
                    **(
                        {"time_seconds": float(cell.get("time_seconds"))}
                        if cell.get("time_seconds") is not None
                        else {}
                    ),
                    **(
                        {"rate_usv_h": float(cell.get("rate_usv_h"))}
                        if cell.get("rate_usv_h") is not None
                        else {}
                    ),
                    **(
                        {"workload_h": float(cell.get("workload_h"))}
                        if cell.get("workload_h") is not None
                        else {}
                    ),
                    **(
                        {"occupancy": float(cell.get("occupancy"))}
                        if cell.get("occupancy") is not None
                        else {}
                    ),
                    **(
                        {"time_hours": float(cell.get("time_hours"))}
                        if cell.get("time_hours") is not None
                        else {}
                    ),
                    **(
                        {"max_load_h": float(cell.get("max_load_h"))}
                        if cell.get("max_load_h") is not None
                        else {}
                    ),
                    **(
                        {"cases_per_year": float(cell.get("cases_per_year"))}
                        if cell.get("cases_per_year") is not None
                        else {}
                    ),
                    **(
                        {"batch_count": float(cell.get("batch_count"))}
                        if cell.get("batch_count") is not None
                        else {}
                    ),
                    **(
                        {"mode_role": str(cell.get("mode_role"))}
                        if cell.get("mode_role")
                        else {}
                    ),
                }
            )
        band_cells.sort(key=lambda c: c["c0"])
        rows.append({"band": r, "cells": band_cells})

    heights = [max(14.0, float(h)) for h in (g.get("row_heights_pt") or [])][:nrow]
    while len(heights) < nrow:
        heights.append(17.5)
    out: Dict[str, Any] = {
        "schema": "embedded_generic_table/v1",
        "column_count": ncol,
        "band_count": nrow,
        "column_fractions": list(g.get("col_fractions") or [0.0, 1.0]),
        "row_heights_pt": heights,
        "row_min_height_pt": min(heights) if heights else 17.5,
        "rows": rows,
        "repeat_header_on_new_page": True,
        "cell_align": "center",
    }
    if isinstance(base, dict):
        for keep in (
            "title",
            "id",
            "note",
            "header_bold",
            "cell_align",
            "repeat_header_on_new_page",
            "header_band_count",
            "source",
            "table_bbox",
            "from_info_sheet",
            "workload_formula",
            "dose_formula",
            "device_count",
        ):
            if keep in base:
                out[keep] = base[keep]
    return out


def import_excel_to_edit_grid(content: bytes) -> Dict[str, Any]:
    """解析 .xlsx/.xlsm 为可编辑网格（保留合并单元格）。"""
    from io import BytesIO

    from apps.core.f1_eval_grid import normalize_grid

    try:
        from openpyxl import load_workbook
    except ImportError as e:
        raise RuntimeError("服务器未安装 openpyxl，无法导入 Excel") from e

    wb = load_workbook(BytesIO(content), data_only=False, rich_text=True)
    ws = wb.active
    if ws is None:
        raise ValueError("Excel 文件没有可用工作表")

    # 实际使用范围
    if ws.max_row is None or ws.max_column is None or ws.max_row < 1 or ws.max_column < 1:
        raise ValueError("Excel 工作表为空")

    max_r = int(ws.max_row)
    max_c = int(ws.max_column)
    # 去掉尾部全空行列
    while max_r > 1:
        if all(ws.cell(max_r, c).value in (None, "") for c in range(1, max_c + 1)):
            max_r -= 1
        else:
            break
    while max_c > 1:
        if all(ws.cell(r, max_c).value in (None, "") for r in range(1, max_r + 1)):
            max_c -= 1
        else:
            break

    # 合并区：左上角保留文本，覆盖区跳过
    merge_map: Dict[Tuple[int, int], Tuple[int, int, int, int]] = {}
    covered: set = set()
    for mr in ws.merged_cells.ranges:
        r0, r1 = mr.min_row - 1, mr.max_row
        c0, c1 = mr.min_col - 1, mr.max_col
        merge_map[(r0, c0)] = (r0, r1, c0, c1)
        for r in range(r0, r1):
            for c in range(c0, c1):
                if (r, c) != (r0, c0):
                    covered.add((r, c))

    def _cell_text(r1: int, c1: int) -> str:
        from radiation_detection_report.md_rich_text import excel_value_to_marked

        return excel_value_to_marked(ws.cell(r1, c1).value)

    cells: List[Dict[str, Any]] = []
    for r in range(max_r):
        for c in range(max_c):
            if (r, c) in covered:
                continue
            if (r, c) in merge_map:
                r0, r1, c0, c1 = merge_map[(r, c)]
            else:
                r0, r1, c0, c1 = r, r + 1, c, c + 1
            # 检测 Excel 整格左缩进，映射为首行缩进
            excel_cell = ws.cell(r0 + 1, c0 + 1)
            first_indent = False
            try:
                al = excel_cell.alignment
                if al is not None and int(getattr(al, "indent", 0) or 0) >= 1:
                    first_indent = True
            except Exception:
                pass
            item = {
                "r0": r0,
                "r1": r1,
                "c0": c0,
                "c1": c1,
                "text": _cell_text(r0 + 1, c0 + 1),
                "role": "value",
                "is_label": False,
                "editable": True,
                "label_key": "",
                "field_key": "",
            }
            if first_indent:
                item["first_indent"] = True
            cells.append(item)

    fr = [round(i / max_c, 6) for i in range(max_c + 1)]
    fr[0], fr[-1] = 0.0, 1.0
    heights = [17.5] * max_r
    return normalize_grid(
        {
            "schema": "f1_main_table_grid/v1",
            "col_fractions": fr,
            "row_heights_pt": heights,
            "cells": cells,
        }
    )


def export_edit_grid_to_xlsx(
    edit_grid: Dict[str, Any],
    *,
    sheet_title: str = "Sheet1",
) -> bytes:
    """将可编辑网格导出为 .xlsx（保留合并单元格与文本）。"""
    from io import BytesIO

    from apps.core.f1_eval_grid import normalize_grid

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font
        from openpyxl.utils import get_column_letter
    except ImportError as e:
        raise RuntimeError("服务器未安装 openpyxl，无法导出 Excel") from e

    from radiation_detection_report.md_rich_text import marked_to_excel_rich

    g = normalize_grid(edit_grid if isinstance(edit_grid, dict) else {})
    nrow = int(g.get("row_count") or 0)
    ncol = int(g.get("col_count") or 0)
    if nrow <= 0 or ncol <= 0:
        raise ValueError("表格为空，无法导出")

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    title = str(sheet_title or "Sheet1").strip() or "Sheet1"
    # Excel 工作表名限制
    safe = "".join("_" if ch in '[]:*?/\\' else ch for ch in title)[:31]
    ws.title = safe or "Sheet1"

    # 先写左上角文本，再合并
    for cell in g.get("cells") or []:
        if not isinstance(cell, dict):
            continue
        r0 = int(cell.get("r0") or 0)
        r1 = max(r0 + 1, int(cell.get("r1") or (r0 + 1)))
        c0 = int(cell.get("c0") or 0)
        c1 = max(c0 + 1, int(cell.get("c1") or (c0 + 1)))
        text = "" if cell.get("text") is None else str(cell.get("text"))
        # 逐段首行缩进：Excel 用段首全角空格；整格 indent 仅作兼容
        para_indents = cell.get("para_indents") if isinstance(cell.get("para_indents"), list) else None
        first_indent = bool(cell.get("first_indent"))
        export_text = text
        if export_text.strip() and (para_indents is not None or first_indent):
            paras = export_text.split("\n")
            out_paras = []
            for i, p in enumerate(paras):
                want = first_indent
                if para_indents is not None:
                    want = bool(para_indents[i]) if i < len(para_indents) else False
                if want and p.strip() and not p.startswith("\u3000"):
                    out_paras.append("\u3000\u3000" + p)
                else:
                    out_paras.append(p)
            export_text = "\n".join(out_paras)
        rich_val = marked_to_excel_rich(export_text)
        excel_cell = ws.cell(row=r0 + 1, column=c0 + 1, value=rich_val)
        h_align = str(cell.get("align") or "").strip().lower()
        if h_align not in ("left", "center", "right"):
            h_align = "center" if r0 == 0 else "left"
        any_indent = bool(para_indents and any(para_indents)) or first_indent
        excel_cell.alignment = Alignment(
            wrap_text=True,
            vertical="center",
            horizontal=h_align,
            indent=2 if any_indent else 0,
        )
        if r0 == 0:
            excel_cell.font = Font(bold=True)
        if r1 - r0 > 1 or c1 - c0 > 1:
            ws.merge_cells(
                start_row=r0 + 1,
                start_column=c0 + 1,
                end_row=r1,
                end_column=c1,
            )

    fr = g.get("col_fractions") or [0.0, 1.0]
    # 列宽：按比例映射到约 12~48 字符宽
    for i in range(ncol):
        try:
            frac = float(fr[i + 1]) - float(fr[i])
        except (TypeError, ValueError, IndexError):
            frac = 1.0 / ncol
        width = max(6.0, min(48.0, frac * 60.0))
        ws.column_dimensions[get_column_letter(i + 1)].width = width

    heights = g.get("row_heights_pt") or []
    for r in range(nrow):
        try:
            hpt = float(heights[r]) if r < len(heights) else 17.5
        except (TypeError, ValueError):
            hpt = 17.5
        # Excel 行高单位约 point
        ws.row_dimensions[r + 1].height = max(14.0, min(120.0, hpt))

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_embedded_table_xlsx(
    ws: Path,
    *,
    tables_key: str,
    table_index: int = 0,
    edit_grid: Optional[Dict[str, Any]] = None,
    title: Optional[str] = None,
) -> Tuple[bytes, str]:
    """导出嵌套表为 xlsx；可传当前未保存的 edit_grid。返回 (bytes, filename)。"""
    data = _load_data(ws)
    tables = data.get(tables_key) if tables_key and isinstance(data.get(tables_key), list) else []
    table: Dict[str, Any] = {}
    if 0 <= table_index < len(tables) and isinstance(tables[table_index], dict):
        table = tables[table_index]
    if edit_grid and isinstance(edit_grid, dict):
        grid = edit_grid
    else:
        if not table:
            raise FileNotFoundError(f"表格不存在: {tables_key}[{table_index}]")
        grid = embedded_to_edit_grid(table)
    name = str(title or table.get("title") or f"table_{table_index + 1}").strip() or "table"
    # 文件名去掉非法字符
    safe_name = "".join(ch if ch not in r'<>:"/\\|?*' else "_" for ch in name).strip() or "table"
    payload = export_edit_grid_to_xlsx(grid, sheet_title=safe_name)
    return payload, f"{safe_name}.xlsx"


def import_excel_into_table(
    ws: Path,
    *,
    tables_key: str,
    content: bytes,
    filename: str = "",
    table_index: Optional[int] = None,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """导入 Excel：覆盖已有表或追加为新嵌套表。"""
    edit = import_excel_to_edit_grid(content)
    stem = Path(filename or "导入表").stem or "导入表"
    data = _load_data(ws)
    tables = data.get(tables_key)
    if not isinstance(tables, list):
        tables = []
        data[tables_key] = tables

    base_title = title if title is not None else stem
    table = edit_grid_to_embedded(edit, {"title": base_title})

    if table_index is None or table_index < 0 or table_index >= len(tables):
        tables.append(table)
        idx = len(tables) - 1
    else:
        old = tables[table_index] if isinstance(tables[table_index], dict) else {}
        if old.get("title") and title is None:
            table["title"] = old.get("title")
        tables[table_index] = table
        idx = table_index

    data[tables_key] = tables
    _save_data(ws, data)
    return load_embedded_table_editor(ws, tables_key, idx)


def apply_edit_grid_op(grid: Dict[str, Any], op: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """对嵌套表编辑网格执行插删/合并等操作。"""
    from apps.core.f1_eval_grid import (
        delete_col,
        delete_row,
        insert_col,
        insert_row,
        merge_cells,
        normalize_grid,
        set_col_fractions,
        unmerge_cell,
        update_cell_text,
    )

    payload = payload or {}
    g = normalize_grid(grid)
    op = str(op or "").strip()
    if op == "insert_row":
        return insert_row(g, int(payload.get("at") or 0), height_pt=float(payload.get("height_pt") or 17.5))
    if op == "delete_row":
        return delete_row(g, int(payload.get("at") or 0))
    if op == "insert_col":
        return insert_col(g, int(payload.get("at") or 0))
    if op == "delete_col":
        return delete_col(g, int(payload.get("at") or 0))
    if op == "merge":
        return merge_cells(
            g,
            int(payload.get("r0") or 0),
            int(payload.get("c0") or 0),
            int(payload.get("r1") or 0),
            int(payload.get("c1") or 0),
        )
    if op == "unmerge":
        return unmerge_cell(g, int(payload.get("r0") or 0), int(payload.get("c0") or 0))
    if op == "set_col_fractions":
        return set_col_fractions(g, list(payload.get("col_fractions") or g.get("col_fractions") or []))
    if op == "set_row_height":
        at = int(payload.get("at") or 0)
        h = max(14.0, float(payload.get("height_pt") or 17.5))
        heights = list(g.get("row_heights_pt") or [])
        if 0 <= at < len(heights):
            heights[at] = h
            g["row_heights_pt"] = heights
        return normalize_grid(g)
    if op == "set_cell_text":
        return update_cell_text(
            g,
            int(payload.get("r0") or 0),
            int(payload.get("c0") or 0),
            str(payload.get("text") or ""),
        )
    if op == "sync_texts":
        # payload.texts: [{r0,c0,text,first_indent?,para_indents?}, ...]
        for item in payload.get("texts") or []:
            if not isinstance(item, dict):
                continue
            fi = item.get("first_indent")
            first_indent = None
            if fi in (True, 1, "1", "true", "True"):
                first_indent = True
            elif fi in (False, 0, "0", "false", "False"):
                first_indent = False
            pi = item.get("para_indents")
            para_indents = None
            if isinstance(pi, list):
                para_indents = [bool(x) for x in pi]
            g = update_cell_text(
                g,
                int(item.get("r0") or 0),
                int(item.get("c0") or 0),
                str(item.get("text") or ""),
                first_indent=first_indent,
                para_indents=para_indents,
            )
        return g
    if op == "set_cell_style":
        align = str(payload.get("align") or "").strip().lower()
        if align and align not in ("left", "center", "right"):
            raise ValueError("align 必须是 left/center/right")
        fs_raw = payload.get("font_size_pt")
        fs = None
        if fs_raw is not None and fs_raw != "":
            try:
                fs = max(8.0, min(24.0, float(fs_raw)))
            except (TypeError, ValueError):
                raise ValueError("字号无效")
        fi_raw = payload.get("first_indent")
        fi_set = False
        fi_val = False
        if fi_raw is not None and fi_raw != "":
            fi_set = True
            fi_val = fi_raw in (True, 1, "1", "true", "True")
        targets = payload.get("cells") or []
        if not targets and payload.get("r0") is not None:
            targets = [{"r0": payload.get("r0"), "c0": payload.get("c0")}]
        for item in targets:
            if not isinstance(item, dict):
                continue
            r0 = int(item.get("r0") or 0)
            c0 = int(item.get("c0") or 0)
            for c in g.get("cells") or []:
                if int(c.get("r0") or 0) == r0 and int(c.get("c0") or 0) == c0:
                    if align:
                        c["align"] = align
                    if fs is not None:
                        c["font_size_pt"] = fs
                    if fi_set:
                        if fi_val:
                            c["first_indent"] = True
                        else:
                            c.pop("first_indent", None)
                    break
        return normalize_grid(g)
    raise ValueError(f"未知操作: {op}")


def _table_preview(table: Dict[str, Any], max_r: int = 6, max_c: int = 8) -> Dict[str, Any]:
    from apps.core.f1_eval_grid import grid_to_html_rows

    edit = embedded_to_edit_grid(table if isinstance(table, dict) else {})
    html_rows = grid_to_html_rows(edit)
    preview_rows = []
    for row in html_rows[:max_r]:
        cells = []
        for cell in row.get("cells") or []:
            if int(cell.get("c0") or 0) >= max_c:
                continue
            cells.append(
                {
                    "text": cell.get("text") or "",
                    "rowspan": cell.get("rowspan") or 1,
                    "colspan": cell.get("colspan") or 1,
                    "width_pct": cell.get("width_pct") or 0,
                }
            )
        preview_rows.append(cells)
    return {
        "row_count": int(edit.get("row_count") or 0),
        "col_count": int(edit.get("col_count") or 0),
        "preview": preview_rows,
        "preview_kind": "cells",
        "title": table.get("title") or "" if isinstance(table, dict) else "",
        # 编辑区所见即所得：完整行列与宽高
        "col_fractions": list(edit.get("col_fractions") or [0.0, 1.0]),
        "row_heights_pt": list(edit.get("row_heights_pt") or []),
        "html_rows": html_rows,
    }


def _table_asset_item(
    table: Dict[str, Any],
    *,
    tables_key: str,
    table_index: int,
    item_id: str,
    row_id: str = "",
) -> Dict[str, Any]:
    meta = _table_preview(table)
    return {
        "id": item_id,
        "kind": "table",
        "row_id": row_id,
        "tables_key": tables_key,
        "table_index": table_index,
        "label": meta.get("title") or f"嵌套表 {table_index + 1}",
        "title": meta.get("title") or "",
        "preview": meta.get("preview") or [],
        "preview_kind": meta.get("preview_kind") or "cells",
        "row_count": meta.get("row_count", 0),
        "col_count": meta.get("col_count", 0),
        "col_fractions": meta.get("col_fractions") or [0.0, 1.0],
        "row_heights_pt": meta.get("row_heights_pt") or [],
        "html_rows": meta.get("html_rows") or [],
    }


def _figure_has_content(fig: Any) -> bool:
    """是否有可展示的插页内容（有图或有图注）。"""
    if not isinstance(fig, dict):
        return False
    if str(fig.get("image") or "").strip():
        return True
    if str(fig.get("caption") or "").strip():
        return True
    return False


def _figure_asset_item(
    ws: Path,
    fig: Any,
    *,
    figure_key: str = "",
    figures_key: str = "",
    figure_index: Optional[int] = None,
    item_id: str = "",
    row_id: str = "",
    label: str = "独立插页",
    section_label: str = "",
    section_label_lines: Optional[List[Any]] = None,
    row_label: str = "",
) -> Dict[str, Any]:
    meta = _figure_meta(ws, fig if isinstance(fig, dict) else {})
    out = {
        "id": item_id or figure_key or f"{figures_key}::{figure_index}",
        "kind": "figure",
        "row_id": row_id,
        "figure_key": figure_key,
        "figures_key": figures_key,
        "figure_index": figure_index,
        "label": label,
        "figure": meta,
    }
    if section_label:
        out["section_label"] = section_label
    if isinstance(section_label_lines, list) and section_label_lines:
        out["section_label_lines"] = [str(x) for x in section_label_lines]
    if row_label:
        out["row_label"] = row_label
    return out


def _row_side_labels(row: Dict[str, Any]) -> Dict[str, Any]:
    embed = row.get("embed") if isinstance(row.get("embed"), dict) else {}
    section_label = str(
        embed.get("section_label") or row.get("label") or row.get("section_label") or ""
    ).strip()
    row_label = str(embed.get("row_label") or row.get("sub_label") or "").strip()
    lines = embed.get("section_label_lines")
    if not isinstance(lines, list):
        lines = None
    return {
        "section_label": section_label,
        "section_label_lines": lines,
        "row_label": row_label,
    }


def _nested_tables_index(
    section_rows: List[Dict[str, Any]], data: Dict[str, Any]
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, str]]:
    """row_id → 嵌套表列表；text_key → row_id。"""
    by_row: Dict[str, List[Dict[str, Any]]] = {}
    text_to_row: Dict[str, str] = {}
    for r in section_rows:
        if r.get("type") != "label_embedded_table":
            continue
        rid = str(r.get("id") or "")
        embed = r.get("embed") or {}
        if not isinstance(embed, dict):
            continue
        text_key = str(embed.get("text_key") or "")
        if text_key and rid:
            text_to_row[text_key] = rid
        tables_key = str(embed.get("tables_key") or "")
        if not tables_key or not rid:
            continue
        raw = data.get(tables_key)
        if not isinstance(raw, list):
            continue
        for ti, table in enumerate(raw):
            if not isinstance(table, dict):
                continue
            by_row.setdefault(rid, []).append(
                _table_asset_item(
                    table,
                    tables_key=tables_key,
                    table_index=ti,
                    item_id=f"{rid}::table:{ti}",
                    row_id=rid,
                )
            )
    return by_row, text_to_row


def _nested_figures_index(
    ws: Path,
    section_rows: List[Dict[str, Any]],
    data: Dict[str, Any],
    section_id: str = "",
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, str], List[Dict[str, Any]]]:
    """
    row_id → 插页列表；row_id → 行绑定的 figure_key；未挂行的栏目插页列表。
    空白占位（无图无注）不进入列表，避免编辑区出现无法删除的空插页。
    """
    by_row: Dict[str, List[Dict[str, Any]]] = {}
    row_figure_key: Dict[str, str] = {}
    for r in section_rows:
        if r.get("type") != "label_embedded_table":
            continue
        rid = str(r.get("id") or "")
        embed = r.get("embed") or {}
        if not isinstance(embed, dict) or not rid:
            continue
        figure_key = str(embed.get("figure_key") or "")
        side = _row_side_labels(r)
        if figure_key:
            row_figure_key[rid] = figure_key
            fig = data.get(figure_key)
            if _figure_has_content(fig):
                by_row.setdefault(rid, []).append(
                    _figure_asset_item(
                        ws,
                        fig,
                        figure_key=figure_key,
                        item_id=f"{rid}::figure",
                        row_id=rid,
                        label="独立插页",
                        **side,
                    )
                )

    orphans: List[Dict[str, Any]] = []
    figures_key = section_figures_key(section_id) if section_id else ""
    loaded_keys = set()
    if figures_key:
        loaded_keys.add(figures_key)
    raw_figs = data.get(figures_key) if figures_key and isinstance(data.get(figures_key), list) else []
    for fi, fig in enumerate(raw_figs):
        if not isinstance(fig, dict):
            continue
        if not _figure_has_content(fig):
            continue
        rid = str(fig.get("row_id") or "")
        item = _figure_asset_item(
            ws,
            fig,
            figures_key=figures_key,
            figure_index=fi,
            figure_key=f"{figures_key}::{fi}",
            item_id=f"{section_id}::extra_figure:{fi}",
            row_id=rid,
        )
        if rid:
            by_row.setdefault(rid, []).append(item)
        else:
            orphans.append(item)

    # 子行专属附图键（如 extra_figures::safety_protection），挂回对应 row_id
    for r in section_rows:
        rid = str(r.get("id") or "")
        if not rid:
            continue
        row_key = f"extra_figures::{rid}"
        if row_key in loaded_keys:
            continue
        raw_row = data.get(row_key)
        if not isinstance(raw_row, list):
            continue
        loaded_keys.add(row_key)
        side = _row_side_labels(r)
        for fi, fig in enumerate(raw_row):
            if not isinstance(fig, dict) or not _figure_has_content(fig):
                continue
            # 若图上已写 row_id 且指向其它行，尊重之；否则挂本行
            fig_rid = str(fig.get("row_id") or "").strip() or rid
            by_row.setdefault(fig_rid, []).append(
                _figure_asset_item(
                    ws,
                    fig,
                    figures_key=row_key,
                    figure_index=fi,
                    figure_key=f"{row_key}::{fi}",
                    item_id=f"{rid}::extra_figure:{fi}",
                    row_id=fig_rid,
                    label=str(fig.get("caption") or "独立插页"),
                    **side,
                )
            )
    return by_row, row_figure_key, orphans


def _ensure_safety_style_figures(ws: Path, data: Dict[str, Any]) -> bool:
    """保证安全防护措施行有图13～15样式图（缺则从常用模板补入）；返回是否写回。"""
    key = "extra_figures::safety_protection"
    figs = data.get(key)
    if not isinstance(figs, list):
        figs = []
    has_style = any(
        isinstance(f, dict) and str(f.get("style_figure_id") or "").strip() for f in figs
    )
    if has_style:
        # 仍尽量把图片落到 ws/assets，便于编辑页预览
        for f in figs:
            if not isinstance(f, dict):
                continue
            if not str(f.get("style_figure_id") or "").strip():
                continue
            _figure_meta(ws, f)
        return False
    try:
        from f1_eval_report.templates.loader import templates_root_context
        from f1_eval_report.templates.safety_protection import merge_safety_extra_figures

        vents = [
            f
            for f in figs
            if isinstance(f, dict) and not str(f.get("style_figure_id") or "").strip()
        ]
        assets = ws / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        with templates_root_context(str(ws / "templates")):
            merged = merge_safety_extra_figures(assets_dir=assets, ventilation_figs=vents)
        for f in merged:
            if isinstance(f, dict) and str(f.get("style_figure_id") or "").strip():
                f.setdefault("row_id", "safety_protection")
                _figure_meta(ws, f)
        data[key] = merged
        return True
    except Exception:
        return False


def _figure_meta(ws: Path, fig: Any) -> Dict[str, Any]:
    if not isinstance(fig, dict):
        fig = {}
    from apps.core.f1_eval_service import portable_basename, resolve_workspace_media

    path_str = str(fig.get("image") or "")
    caption = str(fig.get("caption") or "")
    exists = False
    url = ""
    name = ""
    if path_str:
        p = resolve_workspace_media(ws, path_str)
        if p is None:
            # 兼容旧逻辑：仅文件名落到 assets
            cand = ws / "assets" / portable_basename(path_str)
            if cand.is_file():
                p = cand
        exists = bool(p and p.is_file())
        if exists and p is not None:
            assets = ws / "assets"
            assets.mkdir(parents=True, exist_ok=True)
            dest = assets / p.name
            try:
                if p.resolve() != dest.resolve():
                    if (not dest.exists()) or dest.stat().st_mtime < p.stat().st_mtime:
                        shutil.copy2(p, dest)
                name = dest.name
                url = f"asset:{dest.name}"
            except Exception:
                name = p.name
                url = ""
        elif path_str:
            name = portable_basename(path_str)
    from f1_eval_report.base.page_modes import (
        DEFAULT_FIGURE_PAGE_MODE,
        PAGE_MODE_LABELS,
        apply_page_mode_to_figure,
        resolve_page_size_pt,
    )

    fig2 = apply_page_mode_to_figure(fig)
    mode = str(fig2.get("page_mode") or DEFAULT_FIGURE_PAGE_MODE)
    w, h = resolve_page_size_pt(mode, fig2)
    out = {
        "image": path_str,
        "caption": caption,
        "exists": exists,
        "name": name,
        "url_token": url,
        "page_mode": mode,
        "page_mode_label": PAGE_MODE_LABELS.get(mode, mode),
        "page_width_pt": w,
        "page_height_pt": h,
        "standalone_page": bool(fig2.get("standalone_page", True)),
        "full_page_from_info_sheet": bool(fig2.get("full_page_from_info_sheet")),
        "caption_outside_image": bool(fig2.get("caption_outside_image", True)),
    }
    sid = str(fig.get("style_figure_id") or "").strip()
    if sid:
        out["style_figure_id"] = sid
    return out


def _blocks_from_row(
    ws: Path,
    data: Dict[str, Any],
    row: Dict[str, Any],
    overrides: Dict[str, str],
) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    rid = str(row.get("id") or "")
    rtype = row.get("type")

    def ov(key: str, default: str) -> str:
        return flatten_label_text(overrides.get(key, default))

    if rtype == "label_value":
        default_label = flatten_label_text(row.get("label") or "")
        field_key = str(row.get("field_key") or "")
        blocks.append(
            {
                "id": f"{rid}::value",
                "kind": "text",
                "row_id": rid,
                "label_key": f"{rid}::label",
                "label": ov(f"{rid}::label", default_label),
                "default_label": default_label,
                "wrap_chars": _wrap_chars_of(row.get("label_format")),
                "label_col": str(row.get("label_col") or "label_main"),
                "value_col": str(row.get("value_col") or "value_full"),
                "field_key": field_key,
                "value": _field_get(data, field_key),
                "multiline": True,
            }
        )
    elif rtype == "pairs":
        for i, pair in enumerate(row.get("pairs") or []):
            default_label = flatten_label_text(pair.get("label") or "")
            field_key = str(pair.get("field_key") or "")
            lk = f"{rid}::pair:{i}:label"
            blocks.append(
                {
                    "id": f"{rid}::pair:{i}",
                    "kind": "text",
                    "row_id": rid,
                    "label_key": lk,
                    "label": ov(lk, default_label),
                    "default_label": default_label,
                    "wrap_chars": _wrap_chars_of(pair.get("label_format")),
                    "label_col": str(pair.get("label_col") or "label_main"),
                    "value_col": str(pair.get("value_col") or "value_left"),
                    "field_key": field_key,
                    "value": _field_get(data, field_key),
                    "multiline": False,
                }
            )
    elif rtype == "grid":
        labels = [c for c in (row.get("cells") or []) if c.get("role") == "label"]
        values = [c for c in (row.get("cells") or []) if c.get("role") == "value"]
        for i, (lab, val) in enumerate(zip(labels, values)):
            default_label = _label_text(lab)
            field_key = str(val.get("field_key") or "")
            lk = f"{rid}::grid:{i}:label"
            blocks.append(
                {
                    "id": f"{rid}::grid:{i}",
                    "kind": "text",
                    "row_id": rid,
                    "label_key": lk,
                    "label": ov(lk, default_label),
                    "default_label": default_label,
                    "wrap_chars": _wrap_chars_of((lab or {}).get("label_format")),
                    "label_col": str((lab or {}).get("col") or "label_main"),
                    "value_col": str((val or {}).get("col") or "value_left"),
                    "field_key": field_key,
                    "value": _field_get(data, field_key),
                    "multiline": False,
                }
            )
    elif rtype == "section":
        default_section = flatten_label_text(row.get("section_label") or "")
        if default_section and not row.get("continue_section"):
            blocks.append(
                {
                    "id": f"{rid}::section_label",
                    "kind": "label_only",
                    "layout": "side",
                    "row_id": rid,
                    "label_key": f"{rid}::section_label",
                    "label": ov(f"{rid}::section_label", default_section),
                    "default_label": default_section,
                    "wrap_chars": _wrap_chars_of(row.get("section_label_format")),
                }
            )
        for i, line in enumerate(row.get("lines") or []):
            lab_cell = None
            val_cell = None
            for cell in line.get("cells") or []:
                if cell.get("role") == "label" and lab_cell is None:
                    lab_cell = cell
                if cell.get("role") == "value" and val_cell is None:
                    val_cell = cell
            if not val_cell:
                continue
            default_label = _label_text(lab_cell) if lab_cell else f"行{i+1}"
            field_key = str(val_cell.get("field_key") or "")
            lk = f"{rid}::line:{i}:label"
            blocks.append(
                {
                    "id": f"{rid}::line:{i}",
                    "kind": "text",
                    "row_id": rid,
                    "label_key": lk,
                    "label": ov(lk, default_label),
                    "default_label": default_label,
                    "wrap_chars": _wrap_chars_of((lab_cell or {}).get("label_format")),
                    "label_col": str((lab_cell or {}).get("col") or "sub_label_row_half"),
                    "value_col": str((val_cell or {}).get("col") or "value_wide_half"),
                    "field_key": field_key,
                    "value": _field_get(data, field_key),
                    "multiline": True,
                }
            )
    elif rtype == "label_embedded_table":
        embed = row.get("embed") or {}
        default_label = flatten_label_text(row.get("label") or "")
        blocks.append(
            {
                "id": f"{rid}::main_label",
                "kind": "label_only",
                "layout": "side",
                "row_id": rid,
                "label_key": f"{rid}::label",
                "label": ov(f"{rid}::label", default_label),
                "default_label": default_label,
                "wrap_chars": _wrap_chars_of(row.get("label_format")),
            }
        )
        if row.get("sub_label"):
            blocks.append(
                {
                    "id": f"{rid}::sub_label",
                    "kind": "label_only",
                    "layout": "mid",
                    "row_id": rid,
                    "label_key": f"{rid}::sub_label",
                    "label": ov(f"{rid}::sub_label", str(row.get("sub_label"))),
                    "default_label": flatten_label_text(row.get("sub_label")),
                    "wrap_chars": 2,
                }
            )
        text_key = str(embed.get("text_key") or "")
        if text_key:
            blocks.append(
                {
                    "id": f"{rid}::intro",
                    "kind": "text",
                    "row_id": rid,
                    "label_key": "",
                    "label": "正文说明",
                    "default_label": "正文说明",
                    "field_key": text_key,
                    "value_scope": "top_and_fields",
                    "value": _top_get(data, text_key),
                    "multiline": True,
                    "hide_label": True,
                }
            )
        tables_key = str(embed.get("tables_key") or "")
        if tables_key:
            tables = data.get(tables_key) or []
            if not isinstance(tables, list):
                tables = []
            for ti, table in enumerate(tables):
                if not isinstance(table, dict):
                    continue
                meta = _table_preview(table)
                blocks.append(
                    {
                        "id": f"{rid}::table:{ti}",
                        "kind": "table",
                        "row_id": rid,
                        "tables_key": tables_key,
                        "table_index": ti,
                        "label": meta.get("title") or f"嵌套表 {ti + 1}",
                        "title": meta.get("title") or "",
                        "preview": meta.get("preview") or [],
                        "preview_kind": meta.get("preview_kind") or "cells",
                        "row_count": meta.get("row_count", 0),
                        "col_count": meta.get("col_count", 0),
                        "col_fractions": meta.get("col_fractions") or [0.0, 1.0],
                        "row_heights_pt": meta.get("row_heights_pt") or [],
                        "html_rows": meta.get("html_rows") or [],
                    }
                )
            # 不再在表内放「新增嵌套表」按钮；统一放到表格外工具栏
        figure_key = str(embed.get("figure_key") or "")
        if figure_key:
            fig = data.get(figure_key)
            # 仅在有图/图注时进入资源列表；空白占位不再生成无法删除的空插页
            if _figure_has_content(fig):
                blocks.append(
                    {
                        "id": f"{rid}::figure",
                        "kind": "figure",
                        "row_id": rid,
                        "figure_key": figure_key,
                        "label": "附图",
                        "figure": _figure_meta(ws, fig),
                    }
                )
    return blocks


def section_tables_key(section_id: str) -> str:
    return f"extra_tables::{section_id}"


def section_figures_key(section_id: str) -> str:
    return f"extra_figures::{section_id}"


def _build_section_extras(
    ws: Path,
    data: Dict[str, Any],
    section_id: str,
    blocks: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """把表格/图片从主表块中拆出，并提供栏目级插入键。"""
    main_blocks: List[Dict[str, Any]] = []
    asset_tables: List[Dict[str, Any]] = []
    asset_figures: List[Dict[str, Any]] = []
    preferred_tables_key = ""
    for b in blocks:
        kind = b.get("kind")
        if kind == "table_actions":
            if b.get("tables_key"):
                preferred_tables_key = str(b.get("tables_key"))
            continue
        if kind == "table":
            asset_tables.append(b)
            if b.get("tables_key"):
                preferred_tables_key = str(b.get("tables_key"))
            continue
        if kind == "figure":
            asset_figures.append(b)
            continue
        main_blocks.append(b)

    tables_key = preferred_tables_key or section_tables_key(section_id)
    figures_key = section_figures_key(section_id)

    # 栏目级额外表格（可能与 embed 共用同一 tables_key）
    seen = {(t.get("tables_key"), t.get("table_index")) for t in asset_tables}
    raw_tables = data.get(tables_key) if isinstance(data.get(tables_key), list) else []
    for ti, table in enumerate(raw_tables):
        if not isinstance(table, dict):
            continue
        if (tables_key, ti) in seen:
            continue
        meta = _table_preview(table)
        asset_tables.append(
            {
                "id": f"{section_id}::extra_table:{ti}",
                "kind": "table",
                "tables_key": tables_key,
                "table_index": ti,
                "label": meta.get("title") or f"嵌套表 {ti + 1}",
                "title": meta.get("title") or "",
                "preview": meta.get("preview") or [],
                "preview_kind": meta.get("preview_kind") or "cells",
                "row_count": meta.get("row_count", 0),
                "col_count": meta.get("col_count", 0),
                "col_fractions": meta.get("col_fractions") or [0.0, 1.0],
                "row_heights_pt": meta.get("row_heights_pt") or [],
                "html_rows": meta.get("html_rows") or [],
            }
        )

    raw_figs = data.get(figures_key) if isinstance(data.get(figures_key), list) else []
    for fi, fig in enumerate(raw_figs):
        if not isinstance(fig, dict) or not _figure_has_content(fig):
            continue
        asset_figures.append(
            {
                "id": f"{section_id}::extra_figure:{fi}",
                "kind": "figure",
                "row_id": str(fig.get("row_id") or ""),
                "figures_key": figures_key,
                "figure_index": fi,
                "figure_key": f"{figures_key}::{fi}",
                "label": str(fig.get("caption") or "附图"),
                "figure": _figure_meta(ws, fig),
            }
        )

    # 子行附图键（防护设施栏目 → 安全防护措施样式图/通风图）
    seen_extra = {(str(f.get("figures_key") or ""), f.get("figure_index")) for f in asset_figures}
    child_keys: List[str] = []
    if section_id == "protection_measures":
        child_keys.append("extra_figures::safety_protection")
    for ck in child_keys:
        if ck == figures_key:
            continue
        raw_child = data.get(ck) if isinstance(data.get(ck), list) else []
        for fi, fig in enumerate(raw_child):
            if not isinstance(fig, dict) or not _figure_has_content(fig):
                continue
            if (ck, fi) in seen_extra:
                continue
            asset_figures.append(
                {
                    "id": f"{ck}::{fi}",
                    "kind": "figure",
                    "row_id": str(fig.get("row_id") or "safety_protection"),
                    "figures_key": ck,
                    "figure_index": fi,
                    "figure_key": f"{ck}::{fi}",
                    "label": str(fig.get("caption") or "附图"),
                    "figure": _figure_meta(ws, fig),
                }
            )

    extras = {
        "tables_key": tables_key,
        "figures_key": figures_key,
        "tables": asset_tables,
        "figures": asset_figures,
    }
    return main_blocks, extras


def _load_data(ws: Path) -> Dict[str, Any]:
    from apps.core import f1_eval_service as svc

    return svc.load_report_data(ws)


def _save_data(ws: Path, data: Dict[str, Any]) -> None:
    path = ws / "data" / "report_data.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# 项目基础信息：按 gbzt_f1_ncu2_250075YP.pdf 表头区实测竖线（9 列）
# x: 79.37, 143.14, 206.91, 234.81, 318.87, 361.42, 380.07, 431.88, 459.31, 515.93
PROJECT_INFO_COL_FRACTIONS: List[float] = [
    0.0,
    0.146074,
    0.292148,
    0.356056,
    0.548607,
    0.646074,
    0.688794,
    0.807472,
    0.870304,
    1.0,
]

# row_id → [(label_c0, label_c1, value_c0, value_c1), ...] 按字段顺序
PROJECT_INFO_ROW_SPANS: Dict[str, List[Tuple[int, int, int, int]]] = {
    # 单位名称 | 值(至负责人) | 负责人 | 值
    "org_name_principal": [(0, 1, 1, 6), (6, 7, 7, 9)],
    # 地址 | 值 | 邮编 | 值
    "address_postal": [(0, 1, 1, 6), (6, 7, 7, 9)],
    # 联系人 | 值 | 电话 | 值 | 传真 | 值
    "contact_phone_fax": [(0, 1, 1, 2), (2, 3, 3, 6), (6, 7, 7, 9)],
    # 项目名称 | 通栏值
    "project_name": [(0, 1, 1, 9)],
    # 项目用途 | 值 | 人员数表头 | 值
    "project_purpose_workers": [(0, 1, 1, 4), (4, 7, 7, 9)],
    # 建设地址 | 通栏值
    "construction_address": [(0, 1, 1, 9)],
    # 项目性质 | 值 | 投资额 | 值 | 建设面积 | 值
    "construction_nature": [(0, 1, 1, 4), (4, 5, 5, 7), (7, 8, 8, 9)],
}


def _build_project_info_fixed_grid(blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把项目基础信息 blocks 映射到真实表固定列网格（跨列合并）。"""
    by_row: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for b in blocks:
        if b.get("kind") != "text":
            continue
        rid = str(b.get("row_id") or "")
        if not rid:
            continue
        if rid not in by_row:
            by_row[rid] = []
            order.append(rid)
        by_row[rid].append(b)

    rows_out: List[Dict[str, Any]] = []
    for rid in order:
        items = by_row[rid]
        spans = PROJECT_INFO_ROW_SPANS.get(rid)
        cells: List[Dict[str, Any]] = []
        if not spans:
            # 未知行：主表头 + 通栏值，逐项各占一行
            for b in items:
                cells = [
                    {
                        "role": "label",
                        "c0": 0,
                        "c1": 1,
                        "id": b.get("id"),
                        "label_key": b.get("label_key") or "",
                        "label": b.get("label") or "",
                        "default_label": b.get("default_label") or "",
                        "wrap_chars": b.get("wrap_chars") or 0,
                    },
                    {
                        "role": "value",
                        "c0": 1,
                        "c1": 9,
                        "id": b.get("id"),
                        "field_key": b.get("field_key") or "",
                        "value_scope": b.get("value_scope") or "",
                        "value": b.get("value") or "",
                        "multiline": bool(b.get("multiline")),
                    },
                ]
                rows_out.append({"row_id": rid, "cells": cells})
            continue

        for i, (lc0, lc1, vc0, vc1) in enumerate(spans):
            b = items[i] if i < len(items) else {}
            cells.append(
                {
                    "role": "label",
                    "c0": lc0,
                    "c1": lc1,
                    "id": b.get("id"),
                    "label_key": b.get("label_key") or "",
                    "label": b.get("label") or "",
                    "default_label": b.get("default_label") or "",
                    "wrap_chars": b.get("wrap_chars") or 0,
                }
            )
            cells.append(
                {
                    "role": "value",
                    "c0": vc0,
                    "c1": vc1,
                    "id": b.get("id"),
                    "field_key": b.get("field_key") or "",
                    "value_scope": b.get("value_scope") or "",
                    "value": b.get("value") or "",
                    "multiline": bool(b.get("multiline"))
                    or rid in ("project_name", "construction_address", "address_postal"),
                }
            )
        rows_out.append({"row_id": rid, "cells": cells})

    return {
        "mode": "project_info",
        "col_fractions": list(PROJECT_INFO_COL_FRACTIONS),
        "rows": rows_out,
    }


def _resolve_workspace_columns(
    ws: Path, table_template: str = "250075YP"
) -> Tuple[Dict[str, Dict[str, float]], float, float]:
    """与 PDF 导出相同的列坐标（含用户可调列宽）。"""
    import copy

    from f1_eval_report.assemble import assemble_layout, load_base_format
    from f1_eval_report.base.columns import default_column_widths_mm

    fmt_path = ws / "base" / "format.json"
    if fmt_path.is_file():
        try:
            fmt = json.loads(fmt_path.read_text(encoding="utf-8"))
        except Exception:
            fmt = load_base_format()
    else:
        fmt = load_base_format()
    fmt = copy.deepcopy(fmt or {})
    table = fmt.setdefault("table", {})
    if not isinstance(table, dict):
        table = {}
        fmt["table"] = table
    widths = dict(default_column_widths_mm())
    widths.update(table.get("column_widths_mm") or {})
    try:
        from apps.core.f1_eval_grid import load_template_table_format

        raw = load_template_table_format(ws, table_template)
        if isinstance(raw.get("column_widths_mm"), dict):
            widths.update(raw["column_widths_mm"])
        if isinstance(raw.get("margins_mm"), dict):
            fmt["margins_mm"] = dict(raw["margins_mm"])
    except Exception:
        pass
    table["column_widths_mm"] = widths
    layout = assemble_layout(base_format=fmt, rows=[], table_template=table_template)
    table_layout = layout.get("table") or {}
    columns = table_layout.get("columns") or {}
    table_left = float(table_layout.get("x_left") or 0)
    table_right = float(table_layout.get("x_right") or 0)
    return columns, table_left, table_right


def _build_section_fixed_grid(
    ws: Path,
    section_rows: List[Dict[str, Any]],
    overrides: Dict[str, str],
    data: Dict[str, Any],
    table_template: str = "250075YP",
    *,
    section_id: str = "",
) -> Dict[str, Any]:
    """
    用与主表/PDF 相同的 skeleton→grid 管线生成栏目编辑网格。
    保证行列比例、合并与导出一致。
    """
    from apps.core.f1_eval_grid import grid_to_html_rows, seed_grid_from_skeleton
    from apps.core.f1_eval_table_preview import build_merged_skeleton

    columns, table_left, table_right = _resolve_workspace_columns(ws, table_template)
    if not columns or table_right <= table_left:
        return {"mode": "section", "col_fractions": [0.0, 1.0], "rows": []}

    skeleton = build_merged_skeleton(
        section_rows, columns, table_left, table_right, overrides
    )
    grid = seed_grid_from_skeleton(skeleton, row_min_height_pt=22.0)
    html_rows = grid_to_html_rows(grid)

    embed_text_keys = set()
    for r in section_rows:
        if r.get("type") == "label_embedded_table":
            tk = str((r.get("embed") or {}).get("text_key") or "")
            if tk:
                embed_text_keys.add(tk)

    # 项目基础信息（含建设单位表头）：默认居中、无首行缩进
    is_project_info = section_id in ("project_info", "org_header")
    default_indent = not is_project_info
    default_align = "center" if is_project_info else "left"

    nested_by_row, text_to_row = _nested_tables_index(section_rows, data)
    nested_figs_by_row, row_figure_key, orphan_figures = _nested_figures_index(
        ws, section_rows, data, section_id=section_id
    )

    rows_out: List[Dict[str, Any]] = []
    for hr in html_rows:
        cells_out: List[Dict[str, Any]] = []
        for c in hr.get("cells") or []:
            is_lab = bool(c.get("is_label"))
            field_key = str(c.get("field_key") or "")
            row_id = str(c.get("row_id") or "")
            value = ""
            value_scope = ""
            if field_key and not is_lab:
                if field_key in embed_text_keys or (
                    field_key in data and not isinstance(data.get(field_key), (dict, list))
                ):
                    value = _top_get(data, field_key)
                    value_scope = "top_and_fields"
                else:
                    value = _field_get(data, field_key)
            widget = str(c.get("widget") or "")
            widget_options = c.get("widget_options") if isinstance(c.get("widget_options"), list) else None
            widget_line_sep = c.get("widget_line_sep") if isinstance(c.get("widget_line_sep"), dict) else None
            selected: List[str] = []
            if widget == "checkbox_group" and widget_options is not None:
                from f1_eval_report.base.checkboxes import parse_checkbox_group

                selected = parse_checkbox_group(value, widget_options)
                # 空值时用模板默认未勾选串，便于导出一致
                if not str(value or "").strip():
                    from f1_eval_report.base.checkboxes import format_checkbox_group

                    value = format_checkbox_group([], widget_options, widget_line_sep)
            styles = data.get("field_styles") if isinstance(data.get("field_styles"), dict) else {}
            st = styles.get(field_key) if field_key and isinstance(styles.get(field_key), dict) else {}
            # 正文默认首行缩进；项目基础信息默认无缩进
            if st and "first_indent" in st:
                first_indent = bool(st.get("first_indent"))
            else:
                first_indent = default_indent
            font_size_pt = None
            if st and st.get("font_size_pt") is not None:
                try:
                    font_size_pt = float(st.get("font_size_pt"))
                except (TypeError, ValueError):
                    font_size_pt = None
            text_align = default_align
            if st and st.get("text_align") in ("left", "center", "right"):
                text_align = str(st.get("text_align"))

            def _bool_list(raw: Any) -> Optional[List[bool]]:
                if not isinstance(raw, list):
                    return None
                return [bool(x) for x in raw]

            para_indents = _bool_list(st.get("para_indents")) if st else None
            para_indents_before = _bool_list(st.get("para_indents_before")) if st else None
            para_indents_after = _bool_list(st.get("para_indents_after")) if st else None

            nested_tables: List[Dict[str, Any]] = []
            nested_figures: List[Dict[str, Any]] = []
            if not is_lab:
                rid_for_tables = row_id or text_to_row.get(field_key) or ""
                if rid_for_tables and rid_for_tables in nested_by_row:
                    nested_tables = list(nested_by_row[rid_for_tables])
                if rid_for_tables and rid_for_tables in nested_figs_by_row:
                    nested_figures = list(nested_figs_by_row[rid_for_tables])

            cell = {
                "role": "label" if is_lab else "value",
                "c0": int(c.get("c0") or 0),
                "c1": int(c.get("c1") or 0),
                "r0": int(c.get("r0") or 0),
                "r1": int(c.get("r1") or 0),
                "rowspan": int(c.get("rowspan") or 1),
                "colspan": int(c.get("colspan") or 1),
                "id": (
                    str(c.get("label_key") or "")
                    if is_lab
                    else (
                        f"{c.get('row_id') or ''}::{field_key}"
                        if field_key
                        else str(c.get("row_id") or "")
                    )
                ),
                "label_key": str(c.get("label_key") or ""),
                "label": str(c.get("text") or "") if is_lab else "",
                "default_label": str(c.get("text") or "") if is_lab else "",
                "wrap_chars": int(c.get("wrap_chars") or 0),
                "field_key": field_key,
                "row_id": row_id,
                "figure_key": (
                    row_figure_key.get(row_id) or row_figure_key.get(text_to_row.get(field_key) or "") or ""
                    if not is_lab
                    else ""
                ),
                "value_scope": value_scope,
                "value": value,
                "multiline": (not is_lab) and widget != "checkbox_group",
                "widget": widget,
                "widget_options": widget_options or [],
                "widget_line_sep": widget_line_sep or {},
                "selected": selected,
                "first_indent": first_indent if not is_lab else False,
                "para_indents": para_indents or [],
                "para_indents_before": para_indents_before or [],
                "para_indents_after": para_indents_after or [],
                "font_size_pt": font_size_pt,
                "text_align": text_align if not is_lab else "center",
                "nested_tables": nested_tables,
                "nested_figures": nested_figures,
            }
            cells_out.append(cell)
        rows_out.append(
            {
                "row_id": next(
                    (
                        str(x.get("row_id") or "")
                        for x in (hr.get("cells") or [])
                        if x.get("row_id")
                    ),
                    "",
                ),
                "band": hr.get("band"),
                "height_pt": hr.get("height_pt"),
                "cells": cells_out,
            }
        )

    # 栏目级 extra_tables::{section} 中未编入 embed 行的表：挂到最后一个正文值格
    attached_keys = {
        (nt.get("tables_key"), nt.get("table_index"))
        for items in nested_by_row.values()
        for nt in items
    }
    orphan_tables: List[Dict[str, Any]] = []
    extra_key = section_tables_key(section_id) if section_id else ""
    if extra_key:
        raw_extra = data.get(extra_key)
        if isinstance(raw_extra, list):
            for ti, table in enumerate(raw_extra):
                if not isinstance(table, dict):
                    continue
                key = (extra_key, ti)
                if key in attached_keys:
                    continue
                orphan_tables.append(
                    _table_asset_item(
                        table,
                        tables_key=extra_key,
                        table_index=ti,
                        item_id=f"{section_id}::extra_table:{ti}",
                        row_id="",
                    )
                )
    if orphan_tables:
        placed = False
        for row in reversed(rows_out):
            for cell in reversed(row.get("cells") or []):
                if cell.get("role") == "value" and cell.get("widget") != "checkbox_group":
                    cell.setdefault("nested_tables", []).extend(orphan_tables)
                    placed = True
                    break
            if placed:
                break

    # 未指定 row_id 的栏目插页：优先挂到带 figure_key 的值格，否则挂最后一格正文
    if orphan_figures:
        placed = False
        for row in rows_out:
            for cell in row.get("cells") or []:
                if cell.get("role") == "value" and cell.get("figure_key"):
                    cell.setdefault("nested_figures", []).extend(orphan_figures)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            for row in reversed(rows_out):
                for cell in reversed(row.get("cells") or []):
                    if cell.get("role") == "value" and cell.get("widget") != "checkbox_group":
                        cell.setdefault("nested_figures", []).extend(orphan_figures)
                        placed = True
                        break
                if placed:
                    break

    return {
        "mode": "project_info" if is_project_info else "section",
        "col_fractions": list(grid.get("col_fractions") or [0.0, 1.0]),
        "rows": rows_out,
    }


def _section_layout_pct(ws: Path) -> Dict[str, float]:
    """编辑页列宽比例：与真实主表 F1_COLUMNS / 可调列宽一致。"""
    from f1_eval_report.base.columns import (
        F1_OLD_TABLE_LEFT,
        F1_OLD_TABLE_RIGHT,
        default_column_widths_mm,
        rebuild_columns,
    )

    widths = dict(default_column_widths_mm())
    table_left = float(F1_OLD_TABLE_LEFT)
    table_right = float(F1_OLD_TABLE_RIGHT)
    try:
        from apps.core.f1_eval_grid import load_template_table_format

        # 兼容旧调用：无 template 时仍可读共用 table_format
        raw = load_template_table_format(ws, None)
        if isinstance(raw, dict):
            if isinstance(raw.get("column_widths_mm"), dict):
                widths.update(raw["column_widths_mm"])
            # 若有页边距覆盖，按页宽重算表左右（A4≈595.3pt）
            margins = raw.get("margins_mm") if isinstance(raw.get("margins_mm"), dict) else None
            if margins:
                page_w = float(raw.get("page_width_pt") or 595.3)
                ml = float(margins.get("left") or 0) * 72.0 / 25.4
                mr = float(margins.get("right") or 0) * 72.0 / 25.4
                if ml > 0 and mr > 0 and page_w > ml + mr + 40:
                    table_left = ml
                    table_right = page_w - mr
    except Exception:
        pass

    cols = rebuild_columns(table_left, table_right, widths)
    tw = max(40.0, table_right - table_left)
    pct: Dict[str, float] = {}
    for name, box in cols.items():
        w = max(0.0, float(box["x1"]) - float(box["x0"]))
        pct[name] = round(w / tw * 100.0, 2)
    # 兼容旧前端别名
    pct["side"] = pct.get("label_main_half", 7.5)
    pct["mid"] = pct.get("sub_label_row_half", pct["side"])
    pct["main"] = pct.get("label_main", 14.0)
    pct["pair"] = pct.get("label_pair", 12.0)
    return pct


def _page_geometry(ws: Path, table_template: str = "250075YP") -> Dict[str, Any]:
    """编辑页与 PDF 一致的页面/表格几何与正文字号。"""
    import copy

    from f1_eval_report.assemble import assemble_layout, load_base_format

    fmt_path = ws / "base" / "format.json"
    if fmt_path.is_file():
        try:
            fmt = json.loads(fmt_path.read_text(encoding="utf-8"))
        except Exception:
            fmt = load_base_format()
    else:
        fmt = load_base_format()
    fmt = copy.deepcopy(fmt or {})
    layout = assemble_layout(base_format=fmt, rows=[], table_template=table_template)
    table = layout.get("table") or {}
    text = layout.get("text") or {}
    page = layout.get("page_size") or {}
    left = float(table.get("x_left") or 0)
    right = float(table.get("x_right") or 0)
    return {
        "page_width_pt": float(page.get("width") or 595.3),
        "page_height_pt": float(page.get("height") or 841.9),
        "table_left_pt": left,
        "table_right_pt": right,
        "table_width_pt": max(40.0, right - left),
        "font_size_pt": float(text.get("font_size_pt") or 12),
        # 正文默认 25 磅行距（与 GBZ/T / Word 固定值行距一致）
        "line_spacing_pt": float(text.get("line_spacing_pt") or 25.0),
        "row_min_height_pt": float(table.get("row_min_height") or 25.0),
        "border_width_pt": float(table.get("border_width_pt") or 0.6),
        "margins_mm": dict(layout.get("margins_mm") or {}),
    }


def build_section_editor(
    ws: Path,
    section_id: str,
    table_template: str = "250075YP",
) -> Dict[str, Any]:
    from f1_eval_report.sections.catalog import get_section_builders

    # 旧入口「建设单位信息」并入「项目基础信息」
    section_id = SECTION_NAV_MERGE_INTO.get(section_id, section_id)

    data = _load_data(ws)
    overrides = load_label_overrides(ws, table_template)
    builders = dict(get_section_builders(table_template))
    if section_id not in builders:
        raise FileNotFoundError(f"未知栏目: {section_id}")

    # 防护设施栏目：确保表8下有图13～15样式图；表7标准要求按模板回填
    if section_id == "protection_measures":
        if _ensure_safety_style_figures(ws, data):
            _save_data(ws, data)
        try:
            _ensure_shielding_table7(ws)
            data = _load_data(ws)  # 回填后重载
        except Exception:
            pass

    rows: List[Dict[str, Any]] = []
    # 项目基础信息：先展示建设单位表头行，再展示项目基础信息行
    if section_id == "project_info" and "org_header" in builders:
        rows.extend(builders["org_header"]())
    rows.extend(builders[section_id]())
    blocks: List[Dict[str, Any]] = []
    for row in rows:
        blocks.extend(_blocks_from_row(ws, data, row, overrides))

    blocks, extras = _build_section_extras(ws, data, section_id, blocks)

    # 标题用规范文案（无空格）
    title = SECTION_NAV_TITLES.get(section_id, section_id)
    from apps.core.f1_eval_table_preview import SECTION_PRIMARY_LABEL_KEY

    primary = SECTION_PRIMARY_LABEL_KEY.get(section_id)
    if primary and primary in overrides and str(overrides[primary]).strip():
        title = flatten_label_text(overrides[primary]) or title

    geo = _page_geometry(ws, table_template)
    # 项目基础信息为短表单项，用紧凑行距；其余栏目正文固定 25 磅
    if section_id in ("project_info", "org_header"):
        fs = float(geo.get("font_size_pt") or 12)
        geo["line_spacing_pt"] = round(fs * 1.35, 2)
        geo["row_min_height_pt"] = round(fs * 1.35, 2)
        geo["is_body_section"] = False
    else:
        geo["line_spacing_pt"] = 25.0
        geo["row_min_height_pt"] = max(float(geo.get("row_min_height_pt") or 25.0), 25.0)
        geo["is_body_section"] = True

    payload: Dict[str, Any] = {
        "section_id": section_id,
        "title": title,
        "blocks": blocks,
        "extras": extras,
        "layout_pct": _section_layout_pct(ws),
        "page_geometry": geo,
        "field_styles": dict(data.get("field_styles") or {})
        if isinstance(data.get("field_styles"), dict)
        else {},
        "section_nav": editor_nav(table_template, ws),
        "fixed_grid": _build_section_fixed_grid(
            ws, rows, overrides, data, table_template, section_id=section_id
        ),
    }
    try:
        from f1_eval_report.base.page_modes import page_mode_options

        payload["page_modes"] = page_mode_options()
    except Exception:
        payload["page_modes"] = []
    return payload


def save_section_edits(
    ws: Path,
    *,
    section_id: str,
    edits: List[Dict[str, Any]],
    table_template: str = "250075YP",
) -> Dict[str, Any]:
    section_id = SECTION_NAV_MERGE_INTO.get(section_id, section_id)
    data = _load_data(ws)
    overrides = load_label_overrides(ws, table_template)
    styles = data.get("field_styles")
    if not isinstance(styles, dict):
        styles = {}
    for ed in edits or []:
        label_key = ed.get("label_key") or ""
        if label_key and "label" in ed:
            overrides[label_key] = flatten_label_text(ed.get("label") or "")
        field_key = ed.get("field_key") or ""
        if field_key and "value" in ed:
            if ed.get("value_scope") == "top_and_fields":
                _top_set_text(data, field_key, str(ed.get("value") or ""))
            else:
                _field_set(data, field_key, str(ed.get("value") or ""))
        if field_key and isinstance(ed.get("style"), dict):
            st = dict(styles.get(field_key) or {})
            raw = ed.get("style") or {}
            if "font_size_pt" in raw:
                try:
                    st["font_size_pt"] = max(8.0, min(24.0, float(raw.get("font_size_pt"))))
                except (TypeError, ValueError):
                    pass
            if "first_indent" in raw:
                st["first_indent"] = bool(raw.get("first_indent"))

            def _save_bool_list(key: str) -> None:
                if key not in raw:
                    return
                val = raw.get(key)
                if isinstance(val, list):
                    st[key] = [bool(x) for x in val]
                elif val is None:
                    st.pop(key, None)

            _save_bool_list("para_indents")
            _save_bool_list("para_indents_before")
            _save_bool_list("para_indents_after")
            styles[field_key] = st
    data["field_styles"] = styles
    save_label_overrides(ws, overrides, table_template)
    _save_data(ws, data)
    return build_section_editor(ws, section_id, table_template)


def load_embedded_table_editor(ws: Path, tables_key: str, table_index: int = 0) -> Dict[str, Any]:
    from apps.core.f1_eval_grid import grid_to_html_rows

    data = _load_data(ws)
    tables = data.get(tables_key) or []
    if not isinstance(tables, list) or table_index < 0 or table_index >= len(tables):
        raise FileNotFoundError(f"表格不存在: {tables_key}[{table_index}]")
    table = tables[table_index]
    if not isinstance(table, dict):
        raise FileNotFoundError("表格数据损坏")
    edit_grid = embedded_to_edit_grid(table)
    return {
        "tables_key": tables_key,
        "table_index": table_index,
        "title": table.get("title") or "",
        "edit_grid": edit_grid,
        "html_rows": grid_to_html_rows(edit_grid),
        # 兼容旧前端
        "grid": table_to_grid(table),
        "row_count": int(edit_grid.get("row_count") or 0),
        "col_count": int(edit_grid.get("col_count") or 0),
    }


def _ensure_protection_zoning_table6(ws: Path) -> Dict[str, Any]:
    """确保报告数据中有表6；无则按信息表装置清单汇总组稿。"""
    from f1_eval_report.templates.protection_zoning import (
        TABLE6_TITLE,
        assemble_protection_zoning,
    )
    from f1_eval_report.templates.shielding_summary import devices_from_report_data

    data = _load_data(ws)
    tables = data.get("protection_zoning_tables")
    if isinstance(tables, list) and tables and isinstance(tables[0], dict):
        t0 = tables[0]
        if not str(t0.get("title") or "").strip():
            t0["title"] = TABLE6_TITLE
        return t0

    devices = devices_from_report_data(data)
    obj = assemble_protection_zoning(
        devices or None, include_table=True, report_data=data, table_mode="summary"
    )
    table = (obj.get("protection_zoning_tables") or [None])[0]
    if not isinstance(table, dict):
        from f1_eval_report.templates.protection_zoning import build_table6

        table = build_table6(devices or None, mode="summary", report_data=data)
    table = dict(table)
    table.setdefault("title", TABLE6_TITLE)
    data["protection_zoning_tables"] = [table]
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    if not str(fields.get("protection_zoning") or data.get("protection_zoning") or "").strip():
        fields["protection_zoning"] = obj["protection_zoning"]
        data["fields"] = fields
        data["protection_zoning"] = obj["protection_zoning"]
    _save_data(ws, data)
    return table


def _refresh_interv_conclusion(ws: Path, tables: List[Dict[str, Any]]) -> str:
    """由表14/15重写介入结论，并同步栏目正文与常用模板。"""
    from apps.core import f1_eval_service as svc
    from f1_eval_report.templates.health_impact import (
        _replace_interv_after_in_text,
        refresh_interventional_after_from_tables,
    )

    after = refresh_interventional_after_from_tables(tables)
    try:
        svc.write_file(
            ws,
            "templates/common/health_impact/interventional_after.md",
            after.strip() + "\n",
        )
    except Exception:
        pass
    data = _load_data(ws)
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    cur = str(fields.get("normal_condition") or data.get("normal_condition") or "")
    if cur.strip():
        new_text = _replace_interv_after_in_text(cur, after)
    else:
        # 无正文时仅保留结论段，完整组稿由 assemble 负责
        new_text = after.strip() + "\n"
    fields["normal_condition"] = new_text
    data["fields"] = fields
    data["normal_condition"] = new_text
    _save_data(ws, data)
    return after


def _ensure_normal_condition_tables(ws: Path) -> List[Dict[str, Any]]:
    """确保报告数据中有表11～15；无则按常用模板组稿。"""
    from f1_eval_report.templates.health_impact import (
        TABLE_TITLES,
        assemble_normal_condition,
        build_normal_condition_tables,
        build_table11,
        build_table12,
        build_table13,
        build_table14,
        build_table15,
    )
    from f1_eval_report.templates.shielding_summary import devices_from_report_data

    data = _load_data(ws)
    tables = data.get("normal_condition_tables")
    if (
        isinstance(tables, list)
        and len(tables) >= 5
        and all(isinstance(t, dict) and t.get("rows") for t in tables[:5])
    ):
        for i, title in enumerate(TABLE_TITLES):
            if not str(tables[i].get("title") or "").strip():
                tables[i]["title"] = title
        t11 = tables[0] if isinstance(tables[0], dict) else {}
        t12 = tables[1] if isinstance(tables[1], dict) else {}
        t13 = tables[2] if isinstance(tables[2], dict) else {}
        t14 = tables[3] if isinstance(tables[3], dict) else {}
        t15 = tables[4] if isinstance(tables[4], dict) else {}
        devices = None
        dirty = False
        # 旧骨架（非 7 列 / 无工作量公式）→ 按设备重建表11
        if int(t11.get("column_count") or 0) != 7 or not t11.get("workload_formula"):
            devices = devices_from_report_data(data)
            keep = t11 if int(t11.get("column_count") or 0) == 7 else None
            tables[0] = build_table11(devices or None, existing=keep)
            t11 = tables[0]
            dirty = True
        # 表12：非 8 列 / 无剂量公式 → 按表11重建
        if int(t12.get("column_count") or 0) != 8 or not t12.get("dose_formula"):
            if devices is None:
                devices = devices_from_report_data(data)
            keep12 = t12 if int(t12.get("column_count") or 0) == 8 else None
            tables[1] = build_table12(devices or None, table11=t11, existing=keep12)
            dirty = True
        # 表13～15
        if int(t13.get("column_count") or 0) != 6 or not t13.get("workload_formula"):
            if devices is None:
                devices = devices_from_report_data(data)
            keep13 = t13 if int(t13.get("column_count") or 0) == 6 else None
            tables[2] = build_table13(devices or None, existing=keep13)
            t13 = tables[2]
            dirty = True
        if int(t14.get("column_count") or 0) != 7 or not t14.get("dose_formula") or int(t14.get("header_band_count") or 0) < 2:
            tables[3] = build_table14(table13=t13)
            dirty = True
        if int(t15.get("column_count") or 0) != 7 or not t15.get("dose_formula"):
            tables[4] = build_table15(table13=t13)
            dirty = True
        if dirty:
            data["normal_condition_tables"] = list(tables[:5])
            _save_data(ws, data)
            try:
                from apps.core import f1_eval_service as svc

                for i, name in enumerate(
                    ["table11", "table12", "table13", "table14", "table15"]
                ):
                    svc.write_file(
                        ws,
                        f"templates/common/health_impact/{name}.json",
                        json.dumps(tables[i], ensure_ascii=False, indent=2) + "\n",
                    )
            except Exception:
                pass
        return list(tables[:5])

    devices = devices_from_report_data(data)
    obj = assemble_normal_condition(devices or None, include_table=True)
    built = obj.get("normal_condition_tables") or build_normal_condition_tables(
        devices or None
    )
    while len(built) < 5:
        built.append({"schema": "embedded_generic_table/v1", "title": TABLE_TITLES[len(built)], "rows": []})
    built = [dict(t) for t in built[:5]]
    for i, title in enumerate(TABLE_TITLES):
        built[i].setdefault("title", title)
    data["normal_condition_tables"] = built
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    cur = str(fields.get("normal_condition") or data.get("normal_condition") or "").strip()
    if (not cur) or ("<<<F1_TABLE>>>" not in cur):
        fields["normal_condition"] = obj["normal_condition"]
        data["fields"] = fields
        data["normal_condition"] = obj["normal_condition"]
    _save_data(ws, data)
    return built


def _ensure_ppe_tables(ws: Path) -> List[Dict[str, Any]]:
    """确保报告数据中有表9/表10；无则按骨架组稿，并补齐导语。表10优先信息表。"""
    from f1_eval_report.templates.personal_protective_equipment import (
        TABLE10_TITLE,
        TABLE9_TITLE,
        assemble_personal_protective_equipment,
        build_table10,
        build_table9,
    )
    from f1_eval_report.templates.shielding_summary import devices_from_report_data

    data = _load_data(ws)
    tables = data.get("personal_protective_equipment_tables")
    if isinstance(tables, list) and len(tables) >= 2 and all(
        isinstance(t, dict) for t in tables[:2]
    ):
        if not str(tables[0].get("title") or "").strip():
            tables[0]["title"] = TABLE9_TITLE
        if not str(tables[1].get("title") or "").strip():
            tables[1]["title"] = TABLE10_TITLE
        return [tables[0], tables[1]]

    devices = devices_from_report_data(data)
    pdf = ws / "uploads" / "info_sheet.pdf"
    obj = assemble_personal_protective_equipment(
        devices or None,
        include_table=True,
        pdf_path=str(pdf) if pdf.is_file() else None,
    )
    built = obj.get("personal_protective_equipment_tables") or []
    t9 = built[0] if built and isinstance(built[0], dict) else build_table9()
    t10 = (
        built[1]
        if len(built) > 1 and isinstance(built[1], dict)
        else build_table10(
            devices or None,
            pdf_path=str(pdf) if pdf.is_file() else None,
        )
    )
    t9 = dict(t9)
    t10 = dict(t10)
    t9.setdefault("title", TABLE9_TITLE)
    t10.setdefault("title", TABLE10_TITLE)
    data["personal_protective_equipment_tables"] = [t9, t10]
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    cur = str(
        fields.get("personal_protective_equipment")
        or data.get("personal_protective_equipment")
        or ""
    ).strip()
    if (not cur) or ("<<<F1_TABLE>>>" not in cur) or ("{room_count}" in cur):
        fields["personal_protective_equipment"] = obj["personal_protective_equipment"]
        data["fields"] = fields
        data["personal_protective_equipment"] = obj["personal_protective_equipment"]
    _save_data(ws, data)
    return [t9, t10]


def _ensure_safety_table8(ws: Path) -> Dict[str, Any]:
    """确保报告数据中有表8；无则按骨架组稿，并补齐导语。"""
    from f1_eval_report.templates.safety_protection import (
        TABLE8_TITLE,
        assemble_safety_protection,
        build_table8,
    )
    from f1_eval_report.templates.shielding_summary import devices_from_report_data

    data = _load_data(ws)
    tables = data.get("safety_protection_tables")
    if isinstance(tables, list) and tables and isinstance(tables[0], dict):
        t0 = tables[0]
        if not str(t0.get("title") or "").strip():
            t0["title"] = TABLE8_TITLE
        return t0

    devices = devices_from_report_data(data)
    obj = assemble_safety_protection(devices or None, include_table=True)
    table = (obj.get("safety_protection_tables") or [None])[0]
    if not isinstance(table, dict):
        table = build_table8(devices or None)
    table = dict(table)
    table.setdefault("title", TABLE8_TITLE)
    data["safety_protection_tables"] = [table]
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    if not str(fields.get("safety_protection") or data.get("safety_protection") or "").strip():
        fields["safety_protection"] = obj["safety_protection"]
        data["fields"] = fields
        data["safety_protection"] = obj["safety_protection"]
    _save_data(ws, data)
    return table


def _ensure_shielding_table7(ws: Path) -> Dict[str, Any]:
    """确保报告数据中有表7；并按 shielding_standards.json 回填「标准要求」列。"""
    from f1_eval_report.templates.loader import templates_root_context
    from f1_eval_report.templates.shielding_facilities import (
        TABLE7_TITLE,
        apply_standards_to_table7,
        assemble_shielding_facilities,
        build_table7,
    )
    from f1_eval_report.templates.shielding_summary import devices_from_report_data

    data = _load_data(ws)
    tables = data.get("shielding_tables")
    table: Optional[Dict[str, Any]] = None
    if isinstance(tables, list) and tables and isinstance(tables[0], dict):
        table = dict(tables[0])
        if not str(table.get("title") or "").strip():
            table["title"] = TABLE7_TITLE
    else:
        devices = devices_from_report_data(data)
        pdf = ws / "uploads" / "info_sheet.pdf"
        with templates_root_context(str(ws / "templates")):
            obj = assemble_shielding_facilities(
                devices or None,
                pdf_path=str(pdf) if pdf.is_file() else None,
            )
        table = (obj.get("shielding_tables") or [None])[0]
        if not isinstance(table, dict):
            with templates_root_context(str(ws / "templates")):
                table = build_table7(
                    devices or None,
                    pdf_path=str(pdf) if pdf.is_file() else None,
                )
        table = dict(table)
        table.setdefault("title", TABLE7_TITLE)
        fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
        cur = str(fields.get("shielding") or data.get("shielding") or "").strip()
        if (not cur) or ("<<<F1_TABLE>>>" not in cur) or ("{room_count}" in cur):
            fields["shielding"] = obj["shielding"]
            data["fields"] = fields
            data["shielding"] = obj["shielding"]

    # 无论新旧表，均按常用模板标准要求文案回填
    with templates_root_context(str(ws / "templates")):
        before = json.dumps(table.get("rows") or [], ensure_ascii=False)
        table = apply_standards_to_table7(table)
        after = json.dumps(table.get("rows") or [], ensure_ascii=False)
    data["shielding_tables"] = [table]
    if before != after:
        _save_data(ws, data)
        try:
            from apps.core import f1_eval_service as svc

            svc.write_file(
                ws,
                "templates/common/protection_measures/table7.json",
                json.dumps(table, ensure_ascii=False, indent=2) + "\n",
            )
        except Exception:
            pass
    else:
        _save_data(ws, data)
    return table



_RAD_MGMT_TABLE_MAP = {
    16: ("management_system_tables", "table16", "TABLE16_TITLE", "build_table16"),
    17: ("staff_management_tables", "table17", "TABLE17_TITLE", "build_table17"),
    18: ("personal_monitoring_tables", "table18", "TABLE18_TITLE", "build_table18"),
    19: ("health_surveillance_tables", "table19", "TABLE19_TITLE", "build_table19"),
    20: ("radiation_training_tables", "table20", "TABLE20_TITLE", "build_table20"),
    21: ("archive_management_tables", "table21", "TABLE21_TITLE", "build_table21"),
}

_RAD_MGMT_TEXT_KEY = {
    16: "management_system",
    17: "staff_management",
    18: "personal_monitoring",
    19: "health_surveillance",
    20: "radiation_training",
    21: "archive_management",
}


def _ensure_radiation_mgmt_table(ws: Path, table_no: int) -> Dict[str, Any]:
    """确保报告数据中有表16～21之一；无则按默认模板组稿。"""
    from f1_eval_report.templates import radiation_management as rm

    if table_no not in _RAD_MGMT_TABLE_MAP:
        raise ValueError(f"不支持的放射防护管理表: {table_no}")
    tables_key, file_stem, title_attr, build_attr = _RAD_MGMT_TABLE_MAP[table_no]
    data = _load_data(ws)
    tables = data.get(tables_key)
    if (
        isinstance(tables, list)
        and tables
        and isinstance(tables[0], dict)
        and tables[0].get("rows")
    ):
        t = tables[0]
        title = getattr(rm, title_attr)
        if not str(t.get("title") or "").strip():
            t["title"] = title
        return t
    builder = getattr(rm, build_attr)
    built = builder()
    data[tables_key] = [built]
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    text_key = _RAD_MGMT_TEXT_KEY[table_no]
    cur = str(fields.get(text_key) or data.get(text_key) or "")
    if "<<<F1_TABLE>>>" not in cur:
        if table_no in (16, 17):
            obj = rm.assemble_radiation_management(
                staff_total=int(built.get("staff_count") or 59) if table_no == 17 else None,
                include_tables=False,
            )
            fields[text_key] = obj[text_key]
            data[text_key] = obj[text_key]
        else:
            fields[text_key] = "<<<F1_TABLE>>>\n"
            data[text_key] = fields[text_key]
        data["fields"] = fields
    _save_data(ws, data)
    try:
        from apps.core import f1_eval_service as svc

        svc.write_file(
            ws,
            f"templates/common/radiation_management/{file_stem}.json",
            json.dumps(built, ensure_ascii=False, indent=2) + "\n",
        )
    except Exception:
        pass
    return built

def load_common_template_table_editor(ws: Path, rel: str) -> Dict[str, Any]:
    """常用文本模板中的嵌套表 / 机房-剂量表 JSON → 编辑网格。"""
    from apps.core import f1_eval_service as svc
    from apps.core.f1_eval_grid import grid_to_html_rows

    rel = str(rel or "").replace("\\", "/").lstrip("/")
    if not rel.startswith("templates/common/") or not rel.lower().endswith(".json"):
        raise ValueError("仅支持常用模板目录下的表格 JSON")

    # 表6：编辑报告数据 protection_zoning_tables，并与常用模板 JSON 同步
    if rel.endswith("protection_measures/table6.json"):
        table = _ensure_protection_zoning_table6(ws)
        edit_grid = embedded_to_edit_grid(table)
        return {
            "mode": "common_template",
            "map_kind": "protection_zoning_table6",
            "path": rel,
            "tables_key": "protection_zoning_tables",
            "table_index": 0,
            "title": str(table.get("title") or "表6 本项目放射防护分级设计情况及评价"),
            "edit_grid": edit_grid,
            "html_rows": grid_to_html_rows(edit_grid),
            "grid": table_to_grid(table),
            "row_count": int(edit_grid.get("row_count") or 0),
            "col_count": int(edit_grid.get("col_count") or 0),
            "hint": "编辑后写入本项目表6（防护设施和措施·放射防护分区）；可增删行、改控制区/监督区与评价。",
        }

    # 表7：编辑报告数据 shielding_tables，并与常用模板 JSON 同步
    if rel.endswith("protection_measures/table7.json"):
        table = _ensure_shielding_table7(ws)
        edit_grid = embedded_to_edit_grid(table)
        return {
            "mode": "common_template",
            "map_kind": "shielding_table7",
            "path": rel,
            "tables_key": "shielding_tables",
            "table_index": 0,
            "title": str(table.get("title") or "表7 本项目X射线设备机房屏蔽设计情况及评价"),
            "edit_grid": edit_grid,
            "html_rows": grid_to_html_rows(edit_grid),
            "grid": table_to_grid(table),
            "row_count": int(edit_grid.get("row_count") or 0),
            "col_count": int(edit_grid.get("col_count") or 0),
            "hint": "编辑后写入本项目表7（防护设施和措施·屏蔽设施）；请对照信息表屏蔽设计情况表填写材料厚度与铅当量。",
        }

    # 表8：编辑报告数据 safety_protection_tables
    if rel.endswith("protection_measures/table8.json"):
        table = _ensure_safety_table8(ws)
        edit_grid = embedded_to_edit_grid(table)
        return {
            "mode": "common_template",
            "map_kind": "safety_table8",
            "path": rel,
            "tables_key": "safety_protection_tables",
            "table_index": 0,
            "title": str(table.get("title") or "表8 本项目X射线设备机房安全防护措施设计情况评价"),
            "edit_grid": edit_grid,
            "html_rows": grid_to_html_rows(edit_grid),
            "grid": table_to_grid(table),
            "row_count": int(edit_grid.get("row_count") or 0),
            "col_count": int(edit_grid.get("col_count") or 0),
            "hint": "编辑后写入本项目表8（安全防护措施）；表下样式图与通风管道图（A3）挂在栏目附图中。",
        }

    # 表9：配置要求（固定）
    if rel.endswith("protection_measures/table9.json"):
        tables = _ensure_ppe_tables(ws)
        table = tables[0]
        edit_grid = embedded_to_edit_grid(table)
        return {
            "mode": "common_template",
            "map_kind": "ppe_table9",
            "path": rel,
            "tables_key": "personal_protective_equipment_tables",
            "table_index": 0,
            "title": str(table.get("title") or "表9 个人防护用品和辅助设施配置要求"),
            "edit_grid": edit_grid,
            "html_rows": grid_to_html_rows(edit_grid),
            "grid": table_to_grid(table),
            "row_count": int(edit_grid.get("row_count") or 0),
            "col_count": int(edit_grid.get("col_count") or 0),
            "hint": "表9为 GBZ 130-2020 配置要求（固定）；编辑后写入「个人防护用品」栏目第一张表。",
        }

    # 表10：本项目配置计划
    if rel.endswith("protection_measures/table10.json"):
        tables = _ensure_ppe_tables(ws)
        table = tables[1] if len(tables) > 1 else tables[0]
        edit_grid = embedded_to_edit_grid(table)
        return {
            "mode": "common_template",
            "map_kind": "ppe_table10",
            "path": rel,
            "tables_key": "personal_protective_equipment_tables",
            "table_index": 1,
            "title": str(table.get("title") or "表10 本项目个人防护用品和辅助设施配置计划"),
            "edit_grid": edit_grid,
            "html_rows": grid_to_html_rows(edit_grid),
            "grid": table_to_grid(table),
            "row_count": int(edit_grid.get("row_count") or 0),
            "col_count": int(edit_grid.get("col_count") or 0),
            "hint": "表10为本项目配备计划；对照信息表「个人防护用品和辅助设施配备计划」填写后保存。",
        }

    # 表11～15：健康影响评价 · 正常情况下
    for idx, tip in (
        (11, "表11：X射线影像诊断预期工作量"),
        (12, "表12：影像诊断工作人员及公众年受照剂量"),
        (13, "表13：介入设备预期工作量"),
        (14, "表14：透视防护区工作人员年有效剂量"),
        (15, "表15：ERCP/DSA机房外人员年有效剂量"),
    ):
        if rel.endswith(f"health_impact/table{idx}.json"):
            tables = _ensure_normal_condition_tables(ws)
            ti = idx - 11
            table = tables[ti] if ti < len(tables) else tables[0]
            edit_grid = embedded_to_edit_grid(table)
            return {
                "mode": "common_template",
                "map_kind": f"health_table{idx}",
                "path": rel,
                "tables_key": "normal_condition_tables",
                "table_index": ti,
                "title": str(table.get("title") or tip),
                "edit_grid": edit_grid,
                "html_rows": grid_to_html_rows(edit_grid),
                "grid": table_to_grid(table),
                "row_count": int(edit_grid.get("row_count") or 0),
                "col_count": int(edit_grid.get("col_count") or 0),
                "hint": f"{tip}；保存后写入「健康影响评价 · 正常情况下」。",
            }

    # 表16～21：放射防护管理
    for tno, tip in (
        (16, "表16：放射防护管理制度设置计划及评价"),
        (17, "表17：放射工作人员结构配备计划及评价"),
        (18, "表18：个人剂量管理计划情况及评价"),
        (19, "表19：职业健康管理计划情况及评价"),
        (20, "表20：放射防护培训计划情况及评价"),
        (21, "表21：档案管理计划情况及评价"),
    ):
        if rel.endswith(f"radiation_management/table{tno}.json"):
            table = _ensure_radiation_mgmt_table(ws, tno)
            tables_key = _RAD_MGMT_TABLE_MAP[tno][0]
            edit_grid = embedded_to_edit_grid(table)
            return {
                "mode": "common_template",
                "map_kind": f"rad_mgmt_table{tno}",
                "path": rel,
                "tables_key": tables_key,
                "table_index": 0,
                "title": str(table.get("title") or tip),
                "edit_grid": edit_grid,
                "html_rows": grid_to_html_rows(edit_grid),
                "grid": table_to_grid(table),
                "row_count": int(edit_grid.get("row_count") or 0),
                "col_count": int(edit_grid.get("col_count") or 0),
                "hint": f"{tip}；保存后写入对应放射防护管理栏目。",
            }

    raw = json.loads(svc.read_file(ws, rel))
    if not isinstance(raw, dict):
        raise ValueError("表格模板格式无效")

    schema = str(raw.get("schema") or "")
    if schema == "f1_room_dose_map/v1" or rel.endswith("room_dose_map.json"):
        from f1_eval_report.templates.shielding_summary import room_dose_map_to_embedded_table

        table = room_dose_map_to_embedded_table(raw)
        edit_grid = embedded_to_edit_grid(table)
        return {
            "mode": "common_template",
            "map_kind": "room_dose_map",
            "path": rel,
            "tables_key": "",
            "table_index": 0,
            "title": "机房-剂量对应表",
            "edit_grid": edit_grid,
            "html_rows": grid_to_html_rows(edit_grid),
            "grid": table_to_grid(table),
            "row_count": int(edit_grid.get("row_count") or 0),
            "col_count": int(edit_grid.get("col_count") or 0),
            "hint": "填「是/否」；排序数字越小越靠前；可增删行。保存后仍以 JSON 存盘。",
        }

    if schema == "f1_room_size_limits/v1" or rel.endswith("room_size_limits.json"):
        from f1_eval_report.templates.workplace_layout import room_size_limits_to_embedded_table

        table = room_size_limits_to_embedded_table(raw)
        edit_grid = embedded_to_edit_grid(table)
        return {
            "mode": "common_template",
            "map_kind": "room_size_limits",
            "path": rel,
            "tables_key": "",
            "table_index": 0,
            "title": "机房面积/单边限值（GBZ130）",
            "edit_grid": edit_grid,
            "html_rows": grid_to_html_rows(edit_grid),
            "grid": table_to_grid(table),
            "row_count": int(edit_grid.get("row_count") or 0),
            "col_count": int(edit_grid.get("col_count") or 0),
            "hint": "维护各设备标准限值；表4会引用此表预填标准列，设计列仍需手工填写。",
        }

    if schema == "f1_shielding_standards/v1" or rel.endswith("shielding_standards.json"):
        from f1_eval_report.templates.shielding_facilities import (
            shielding_standards_to_embedded_table,
        )

        table = shielding_standards_to_embedded_table(raw)
        edit_grid = embedded_to_edit_grid(table)
        return {
            "mode": "common_template",
            "map_kind": "shielding_standards",
            "path": rel,
            "tables_key": "",
            "table_index": 0,
            "title": "表7标准要求（GBZ130铅当量）",
            "edit_grid": edit_grid,
            "html_rows": grid_to_html_rows(edit_grid),
            "grid": table_to_grid(table),
            "row_count": int(edit_grid.get("row_count") or 0),
            "col_count": int(edit_grid.get("col_count") or 0),
            "hint": "按设备类型维护表7「标准要求」文案；类型键勿改错。保存后重新组稿/提取信息表写入表7。",
        }

    if schema not in ("", "embedded_generic_table/v1"):
        if "rows" not in raw:
            raise ValueError("不是可编辑的嵌套表模板")
    edit_grid = embedded_to_edit_grid(raw)
    return {
        "mode": "common_template",
        "path": rel,
        "tables_key": "",
        "table_index": 0,
        "title": str(raw.get("title") or Path(rel).stem),
        "edit_grid": edit_grid,
        "html_rows": grid_to_html_rows(edit_grid),
        "grid": table_to_grid(raw),
        "row_count": int(edit_grid.get("row_count") or 0),
        "col_count": int(edit_grid.get("col_count") or 0),
    }


def save_common_template_table_editor(
    ws: Path,
    *,
    rel: str,
    edit_grid: Optional[Dict[str, Any]] = None,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    from apps.core import f1_eval_service as svc

    rel = str(rel or "").replace("\\", "/").lstrip("/")
    if not rel.startswith("templates/common/") or not rel.lower().endswith(".json"):
        raise ValueError("仅支持常用模板目录下的表格 JSON")
    try:
        base = json.loads(svc.read_file(ws, rel))
        if not isinstance(base, dict):
            base = {}
    except Exception:
        base = {}

    # 表6：写回报告数据 + 常用模板 JSON
    if rel.endswith("protection_measures/table6.json"):
        from f1_eval_report.templates.protection_zoning import TABLE6_TITLE

        new_table = edit_grid_to_embedded(
            edit_grid or {},
            {"title": title or TABLE6_TITLE, "source": "protection_zoning"},
        )
        if title is not None:
            new_table["title"] = title
        else:
            new_table.setdefault("title", TABLE6_TITLE)
        data = _load_data(ws)
        data["protection_zoning_tables"] = [new_table]
        _save_data(ws, data)
        svc.write_file(ws, rel, json.dumps(new_table, ensure_ascii=False, indent=2) + "\n")
        return load_common_template_table_editor(ws, rel)

    # 表7：写回报告数据 + 常用模板 JSON
    if rel.endswith("protection_measures/table7.json"):
        from f1_eval_report.templates.shielding_facilities import TABLE7_TITLE

        new_table = edit_grid_to_embedded(
            edit_grid or {},
            {"title": title or TABLE7_TITLE, "source": "shielding_facilities"},
        )
        if title is not None:
            new_table["title"] = title
        else:
            new_table.setdefault("title", TABLE7_TITLE)
        data = _load_data(ws)
        data["shielding_tables"] = [new_table]
        _save_data(ws, data)
        svc.write_file(ws, rel, json.dumps(new_table, ensure_ascii=False, indent=2) + "\n")
        return load_common_template_table_editor(ws, rel)

    # 表8：写回报告数据 + 常用模板 JSON
    if rel.endswith("protection_measures/table8.json"):
        from f1_eval_report.templates.safety_protection import TABLE8_TITLE

        new_table = edit_grid_to_embedded(
            edit_grid or {},
            {"title": title or TABLE8_TITLE, "source": "safety_protection"},
        )
        if title is not None:
            new_table["title"] = title
        else:
            new_table.setdefault("title", TABLE8_TITLE)
        data = _load_data(ws)
        data["safety_protection_tables"] = [new_table]
        _save_data(ws, data)
        svc.write_file(ws, rel, json.dumps(new_table, ensure_ascii=False, indent=2) + "\n")
        return load_common_template_table_editor(ws, rel)

    # 表9：写回报告数据[0] + 常用模板 JSON
    if rel.endswith("protection_measures/table9.json"):
        from f1_eval_report.templates.personal_protective_equipment import TABLE9_TITLE

        new_table = edit_grid_to_embedded(
            edit_grid or {},
            {"title": title or TABLE9_TITLE, "source": "personal_protective_equipment"},
        )
        if title is not None:
            new_table["title"] = title
        else:
            new_table.setdefault("title", TABLE9_TITLE)
        data = _load_data(ws)
        tables = list(data.get("personal_protective_equipment_tables") or [])
        while len(tables) < 2:
            tables.append({})
        tables[0] = new_table
        if not isinstance(tables[1], dict) or not tables[1].get("rows"):
            from f1_eval_report.templates.personal_protective_equipment import build_table10

            tables[1] = build_table10()
        data["personal_protective_equipment_tables"] = tables
        _save_data(ws, data)
        svc.write_file(ws, rel, json.dumps(new_table, ensure_ascii=False, indent=2) + "\n")
        return load_common_template_table_editor(ws, rel)

    # 表10：写回报告数据[1] + 常用模板 JSON
    if rel.endswith("protection_measures/table10.json"):
        from f1_eval_report.templates.personal_protective_equipment import TABLE10_TITLE

        new_table = edit_grid_to_embedded(
            edit_grid or {},
            {"title": title or TABLE10_TITLE, "source": "personal_protective_equipment"},
        )
        if title is not None:
            new_table["title"] = title
        else:
            new_table.setdefault("title", TABLE10_TITLE)
        data = _load_data(ws)
        tables = list(data.get("personal_protective_equipment_tables") or [])
        while len(tables) < 2:
            tables.append({})
        if not isinstance(tables[0], dict) or not tables[0].get("rows"):
            from f1_eval_report.templates.personal_protective_equipment import build_table9

            tables[0] = build_table9()
        tables[1] = new_table
        data["personal_protective_equipment_tables"] = tables
        _save_data(ws, data)
        svc.write_file(ws, rel, json.dumps(new_table, ensure_ascii=False, indent=2) + "\n")
        return load_common_template_table_editor(ws, rel)

    # 表16～21：写回对应 tables_key[0] + 常用模板 JSON
    for tno in range(16, 22):
        if rel.endswith(f"radiation_management/table{tno}.json"):
            from f1_eval_report.templates import radiation_management as rm

            tables_key, file_stem, title_attr, _build = _RAD_MGMT_TABLE_MAP[tno]
            default_title = getattr(rm, title_attr)
            _ensure_radiation_mgmt_table(ws, tno)
            data = _load_data(ws)
            tables = list(data.get(tables_key) or [])
            while len(tables) < 1:
                tables.append({})
            prev = tables[0] if isinstance(tables[0], dict) else {}
            base = {
                **{
                    k: prev[k]
                    for k in (
                        "source",
                        "staff_count",
                        "header_band_count",
                        "from_info_sheet",
                    )
                    if k in prev
                },
                "title": title or prev.get("title") or default_title,
                "source": "radiation_management",
            }
            new_table = edit_grid_to_embedded(edit_grid or {}, base)
            if title is not None:
                new_table["title"] = title
            else:
                new_table.setdefault("title", default_title)
            if tno == 17:
                total = 0
                for band in new_table.get("rows") or []:
                    cells = band.get("cells") or []
                    texts = [str(c.get("text") or "") for c in cells]
                    if any("共计" in t for t in texts):
                        continue
                    for tx in texts:
                        m = re.search(r"^(\d+)\s*人$", tx.strip())
                        if m:
                            total += int(m.group(1))
                if total > 0:
                    new_table["staff_count"] = total
                    # 更新共计行人数
                    for band in new_table.get("rows") or []:
                        cells = band.get("cells") or []
                        texts = [str(c.get("text") or "") for c in cells]
                        if any("共计" in t for t in texts):
                            for c in cells:
                                tx = str(c.get("text") or "")
                                if re.search(r"\d+\s*人", tx):
                                    c["text"] = f"{total}人"
                    fields = (
                        data.get("fields")
                        if isinstance(data.get("fields"), dict)
                        else {}
                    )
                    intro = rm._load_fixed(
                        "radiation_management/staff_intro.md",
                        rm.DEFAULT_STAFF_INTRO,
                    ).replace("{staff_count}", str(total))
                    after = rm._load_fixed(
                        "radiation_management/staff_after.md",
                        rm.DEFAULT_STAFF_AFTER,
                    )
                    body = f"{intro}\n\n<<<F1_TABLE>>>\n\n{after}\n"
                    fields["staff_management"] = body
                    data["fields"] = fields
                    data["staff_management"] = body
            tables[0] = new_table
            data[tables_key] = tables
            _save_data(ws, data)
            svc.write_file(
                ws, rel, json.dumps(new_table, ensure_ascii=False, indent=2) + "\n"
            )
            return load_common_template_table_editor(ws, rel)

    # 表11～15：写回 normal_condition_tables[i]
    for idx in range(11, 16):
        if rel.endswith(f"health_impact/table{idx}.json"):
            from f1_eval_report.templates.health_impact import (
                TABLE_TITLES,
                apply_table11_workloads_to_table12,
                apply_table13_to_table14,
                apply_table13_to_table15,
                recalc_table11_workloads,
                recalc_table13_loads,
                recalc_table14_doses,
                recalc_table15_doses,
                sync_tables_from_table13,
            )

            ti = idx - 11
            default_title = TABLE_TITLES[ti]
            tables = list(_ensure_normal_condition_tables(ws))
            while len(tables) < 5:
                tables.append({})
            prev = tables[ti] if isinstance(tables[ti], dict) else {}
            base = {
                **{k: prev[k] for k in (
                    "note",
                    "from_info_sheet",
                    "workload_formula",
                    "dose_formula",
                    "device_count",
                    "header_band_count",
                    "source",
                ) if k in prev},
                "title": title or prev.get("title") or default_title,
                "source": "health_impact",
            }
            new_table = edit_grid_to_embedded(edit_grid or {}, base)
            if title is not None:
                new_table["title"] = title
            else:
                new_table.setdefault("title", default_title)
            if idx == 11:
                new_table = recalc_table11_workloads(new_table)
                tables[0] = new_table
                if isinstance(tables[1], dict) and tables[1].get("rows"):
                    tables[1] = apply_table11_workloads_to_table12(tables[1], new_table)
                    try:
                        svc.write_file(
                            ws,
                            "templates/common/health_impact/table12.json",
                            json.dumps(tables[1], ensure_ascii=False, indent=2) + "\n",
                        )
                    except Exception:
                        pass
            elif idx == 12:
                t11 = tables[0] if isinstance(tables[0], dict) else None
                new_table = apply_table11_workloads_to_table12(new_table, t11)
                tables[1] = new_table
            elif idx == 13:
                new_table = recalc_table13_loads(new_table)
                tables[2] = new_table
                tables = sync_tables_from_table13(tables)
                for j, name in ((3, "table14"), (4, "table15")):
                    try:
                        svc.write_file(
                            ws,
                            f"templates/common/health_impact/{name}.json",
                            json.dumps(tables[j], ensure_ascii=False, indent=2) + "\n",
                        )
                    except Exception:
                        pass
            elif idx == 14:
                t13 = tables[2] if isinstance(tables[2], dict) else None
                new_table = apply_table13_to_table14(new_table, t13)
                new_table = recalc_table14_doses(new_table)
                tables[3] = new_table
            elif idx == 15:
                t13 = tables[2] if isinstance(tables[2], dict) else None
                new_table = apply_table13_to_table15(new_table, t13)
                new_table = recalc_table15_doses(new_table)
                tables[4] = new_table
            else:
                tables[ti] = new_table
            data = _load_data(ws)
            data["normal_condition_tables"] = tables
            _save_data(ws, data)
            # 表13～15变更后刷新介入结论
            if idx in (13, 14, 15):
                try:
                    _refresh_interv_conclusion(ws, tables)
                except Exception:
                    pass
            svc.write_file(ws, rel, json.dumps(new_table, ensure_ascii=False, indent=2) + "\n")
            return load_common_template_table_editor(ws, rel)

    # 机房-剂量对应表：表格编辑 → 回写 JSON 规则文件
    if str(base.get("schema") or "") == "f1_room_dose_map/v1" or rel.endswith(
        "room_dose_map.json"
    ):
        from f1_eval_report.templates.shielding_summary import (
            embedded_table_to_room_dose_map,
        )

        embedded = edit_grid_to_embedded(edit_grid or {}, {"title": title or "机房-剂量对应表"})
        new_map = embedded_table_to_room_dose_map(embedded, base)
        svc.write_file(ws, rel, json.dumps(new_map, ensure_ascii=False, indent=2) + "\n")
        return load_common_template_table_editor(ws, rel)

    # 机房面积/单边限值
    if str(base.get("schema") or "") == "f1_room_size_limits/v1" or rel.endswith(
        "room_size_limits.json"
    ):
        from f1_eval_report.templates.workplace_layout import (
            embedded_table_to_room_size_limits,
        )

        embedded = edit_grid_to_embedded(
            edit_grid or {}, {"title": title or "机房面积/单边限值"}
        )
        new_map = embedded_table_to_room_size_limits(embedded, base)
        svc.write_file(ws, rel, json.dumps(new_map, ensure_ascii=False, indent=2) + "\n")
        return load_common_template_table_editor(ws, rel)

    # 表7标准要求（GBZ130铅当量）
    if str(base.get("schema") or "") == "f1_shielding_standards/v1" or rel.endswith(
        "shielding_standards.json"
    ):
        from f1_eval_report.templates.shielding_facilities import (
            embedded_table_to_shielding_standards,
        )

        embedded = edit_grid_to_embedded(
            edit_grid or {}, {"title": title or "表7标准要求（GBZ130）"}
        )
        new_map = embedded_table_to_shielding_standards(embedded, base)
        svc.write_file(ws, rel, json.dumps(new_map, ensure_ascii=False, indent=2) + "\n")
        return load_common_template_table_editor(ws, rel)

    new_table = edit_grid_to_embedded(edit_grid or {}, base)
    if title is not None:
        new_table["title"] = title
    elif base.get("title"):
        new_table["title"] = base["title"]
    if base.get("source"):
        new_table["source"] = base["source"]
    if base.get("note"):
        new_table["note"] = base["note"]
    svc.write_file(ws, rel, json.dumps(new_table, ensure_ascii=False, indent=2) + "\n")
    return load_common_template_table_editor(ws, rel)


def save_embedded_table_editor(
    ws: Path,
    *,
    tables_key: str,
    table_index: int,
    grid: Any = None,
    edit_grid: Optional[Dict[str, Any]] = None,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    data = _load_data(ws)
    tables = data.get(tables_key)
    if not isinstance(tables, list):
        tables = []
        data[tables_key] = tables
    while len(tables) <= table_index:
        tables.append({})
    base = tables[table_index] if isinstance(tables[table_index], dict) else {}
    if isinstance(edit_grid, dict):
        new_table = edit_grid_to_embedded(edit_grid, base)
    elif isinstance(grid, dict) and ("cells" in grid or "col_fractions" in grid):
        new_table = edit_grid_to_embedded(grid, base)
    else:
        new_table = grid_to_table(grid or [[""]], base)
    if title is not None:
        new_table["title"] = title
    elif base.get("title"):
        new_table["title"] = base["title"]
    if tables_key == "normal_condition_tables" and int(table_index) == 0:
        from f1_eval_report.templates.health_impact import (
            apply_table11_workloads_to_table12,
            recalc_table11_workloads,
        )

        new_table = recalc_table11_workloads(new_table)
        tables[0] = new_table
        if len(tables) > 1 and isinstance(tables[1], dict) and tables[1].get("rows"):
            tables[1] = apply_table11_workloads_to_table12(tables[1], new_table)
    elif tables_key == "normal_condition_tables" and int(table_index) == 1:
        from f1_eval_report.templates.health_impact import (
            apply_table11_workloads_to_table12,
        )

        t11 = tables[0] if isinstance(tables[0], dict) else None
        new_table = apply_table11_workloads_to_table12(new_table, t11)
        tables[1] = new_table
    elif tables_key == "normal_condition_tables" and int(table_index) == 2:
        from f1_eval_report.templates.health_impact import (
            recalc_table13_loads,
            sync_tables_from_table13,
        )

        new_table = recalc_table13_loads(new_table)
        tables[2] = new_table
        tables = sync_tables_from_table13(tables)
        data[tables_key] = tables
        _save_data(ws, data)
        try:
            _refresh_interv_conclusion(ws, tables)
        except Exception:
            pass
        return load_embedded_table_editor(ws, tables_key, table_index)
    elif tables_key == "normal_condition_tables" and int(table_index) == 3:
        from f1_eval_report.templates.health_impact import apply_table13_to_table14

        t13 = tables[2] if isinstance(tables[2], dict) else None
        new_table = apply_table13_to_table14(new_table, t13)
        tables[3] = new_table
        data[tables_key] = tables
        _save_data(ws, data)
        try:
            _refresh_interv_conclusion(ws, tables)
        except Exception:
            pass
        return load_embedded_table_editor(ws, tables_key, table_index)
    elif tables_key == "normal_condition_tables" and int(table_index) == 4:
        from f1_eval_report.templates.health_impact import apply_table13_to_table15

        t13 = tables[2] if isinstance(tables[2], dict) else None
        new_table = apply_table13_to_table15(new_table, t13)
        tables[4] = new_table
        data[tables_key] = tables
        _save_data(ws, data)
        try:
            _refresh_interv_conclusion(ws, tables)
        except Exception:
            pass
        return load_embedded_table_editor(ws, tables_key, table_index)
    else:
        tables[table_index] = new_table
    _save_data(ws, data)
    return load_embedded_table_editor(ws, tables_key, table_index)


def add_embedded_table(ws: Path, tables_key: str) -> Dict[str, Any]:
    data = _load_data(ws)
    tables = data.get(tables_key)
    if not isinstance(tables, list):
        tables = []
        data[tables_key] = tables
    tables.append(
        grid_to_table(
            [["列1", "列2", "列3"], ["", "", ""]],
            {"title": f"表{len(tables) + 1}"},
        )
    )
    _save_data(ws, data)
    return {"tables_key": tables_key, "table_index": len(tables) - 1, "table_count": len(tables)}


def delete_embedded_table(ws: Path, tables_key: str, table_index: int) -> Dict[str, Any]:
    """删除嵌套表（按 tables_key + 下标）。"""
    data = _load_data(ws)
    tables = data.get(tables_key)
    if not isinstance(tables, list):
        raise FileNotFoundError(f"表格列表不存在: {tables_key}")
    if table_index < 0 or table_index >= len(tables):
        raise FileNotFoundError(f"表格不存在: {tables_key}[{table_index}]")
    tables.pop(table_index)
    data[tables_key] = tables
    _save_data(ws, data)
    return {"tables_key": tables_key, "table_count": len(tables)}


def delete_figure(
    ws: Path,
    *,
    figures_key: str = "",
    figure_index: Optional[int] = None,
    figure_key: str = "",
) -> Dict[str, Any]:
    """删除独立插页/附图。列表项按 figures_key+index；单图按 figure_key。"""
    data = _load_data(ws)
    if figures_key:
        figs = data.get(figures_key)
        if not isinstance(figs, list):
            raise FileNotFoundError(f"插页列表不存在: {figures_key}")
        idx = int(figure_index if figure_index is not None else -1)
        if idx < 0 or idx >= len(figs):
            raise FileNotFoundError(f"插页不存在: {figures_key}[{idx}]")
        removed = figs.pop(idx)
        data[figures_key] = figs
        _save_data(ws, data)
        # 尽力清理资产文件（失败忽略）
        try:
            if isinstance(removed, dict) and removed.get("image"):
                from apps.core.f1_eval_service import resolve_workspace_media

                p = resolve_workspace_media(ws, removed.get("image"))
                if p is not None and p.is_file() and str(ws.resolve()) in str(p.resolve()):
                    p.unlink()
        except Exception:
            pass
        return {"figures_key": figures_key, "figure_count": len(figs)}

    key = str(figure_key or "").strip()
    if not key:
        raise ValueError("缺少 figures_key 或 figure_key")
    # 模板占位键可能尚未写入数据：视为已删除（幂等）
    if key not in data:
        return {"figure_key": key, "deleted": True, "missing": True}
    removed = data.pop(key, None)
    _save_data(ws, data)
    try:
        if isinstance(removed, dict) and removed.get("image"):
            from apps.core.f1_eval_service import resolve_workspace_media

            p = resolve_workspace_media(ws, removed.get("image"))
            if p is not None and p.is_file() and str(ws.resolve()) in str(p.resolve()):
                p.unlink()
    except Exception:
        pass
    return {"figure_key": key, "deleted": True}


def operate_embedded_table_editor(
    *,
    edit_grid: Dict[str, Any],
    op: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from apps.core.f1_eval_grid import grid_to_html_rows

    g = apply_edit_grid_op(edit_grid, op, payload)
    return {
        "edit_grid": g,
        "html_rows": grid_to_html_rows(g),
        "row_count": int(g.get("row_count") or 0),
        "col_count": int(g.get("col_count") or 0),
    }


def upload_figure(
    ws: Path,
    figure_key: str,
    uploaded_name: str,
    content: bytes,
    caption: Optional[str] = None,
    *,
    figures_key: str = "",
    figure_index: Optional[int] = None,
    page_mode: Optional[str] = None,
    row_id: str = "",
) -> Dict[str, Any]:
    assets = ws / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    suffix = Path(uploaded_name).suffix.lower() or ".png"
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}:
        suffix = ".png"
    data = _load_data(ws)
    from f1_eval_report.base.page_modes import apply_page_mode_to_figure

    single_key = str(figure_key or "").strip()
    # 真实单键（如 protection_zoning_figure）优先；列表键/空键走栏目多图
    use_list = bool(figures_key) and (
        not single_key
        or single_key == figures_key
        or single_key.startswith("extra_figures::")
    )

    if use_list:
        figs = data.get(figures_key)
        if not isinstance(figs, list):
            figs = []
        idx = figure_index if figure_index is not None else -1
        if idx < 0 or idx >= len(figs):
            idx = len(figs)
            figs.append({})
        safe = figures_key.replace(":", "_").replace("/", "_")
        dest = assets / f"{safe}_{idx}{suffix}"
        dest.write_bytes(content)
        fig = figs[idx] if isinstance(figs[idx], dict) else {}
        fig = dict(fig)
        fig["image"] = str(dest)
        fig["standalone_page"] = True
        if row_id:
            fig["row_id"] = str(row_id)
        fig = apply_page_mode_to_figure(fig, page_mode or fig.get("page_mode"))
        if caption is not None:
            fig["caption"] = caption
        elif not fig.get("caption"):
            fig["caption"] = ""
        figs[idx] = fig
        data[figures_key] = figs
        _save_data(ws, data)
        meta = _figure_meta(ws, fig)
        meta["figures_key"] = figures_key
        meta["figure_index"] = idx
        meta["row_id"] = str(fig.get("row_id") or "")
        return meta

    dest = assets / f"{single_key}{suffix}"
    dest.write_bytes(content)
    fig = data.get(single_key)
    if not isinstance(fig, dict):
        fig = {}
    fig = dict(fig)
    fig["image"] = str(dest)
    fig["standalone_page"] = True
    fig = apply_page_mode_to_figure(fig, page_mode or fig.get("page_mode"))
    if caption is not None:
        fig["caption"] = caption
    elif not fig.get("caption"):
        fig["caption"] = ""
    data[single_key] = fig
    _save_data(ws, data)
    return _figure_meta(ws, fig)


def save_figure_caption(
    ws: Path,
    figure_key: str,
    caption: str,
    *,
    figures_key: str = "",
    figure_index: Optional[int] = None,
    page_mode: Optional[str] = None,
) -> Dict[str, Any]:
    data = _load_data(ws)
    from f1_eval_report.base.page_modes import apply_page_mode_to_figure

    if figures_key and figure_index is not None:
        figs = data.get(figures_key)
        if not isinstance(figs, list):
            figs = []
        while len(figs) <= int(figure_index):
            figs.append({})
        fig = figs[int(figure_index)] if isinstance(figs[int(figure_index)], dict) else {}
        fig = dict(fig)
        fig["caption"] = caption
        if page_mode:
            fig = apply_page_mode_to_figure(fig, page_mode)
        figs[int(figure_index)] = fig
        data[figures_key] = figs
        _save_data(ws, data)
        meta = _figure_meta(ws, fig)
        meta["figures_key"] = figures_key
        meta["figure_index"] = int(figure_index)
        return meta

    fig = data.get(figure_key)
    if not isinstance(fig, dict):
        fig = {}
    fig = dict(fig)
    fig["caption"] = caption
    if page_mode:
        fig = apply_page_mode_to_figure(fig, page_mode)
    data[figure_key] = fig
    _save_data(ws, data)
    return _figure_meta(ws, fig)


def append_section_figure_slot(ws: Path, figures_key: str) -> Dict[str, Any]:
    """插入图片前先占一个空槽位，返回 figure_index。"""
    data = _load_data(ws)
    figs = data.get(figures_key)
    if not isinstance(figs, list):
        figs = []
    figs.append({"caption": ""})
    data[figures_key] = figs
    _save_data(ws, data)
    return {"figures_key": figures_key, "figure_index": len(figs) - 1}
