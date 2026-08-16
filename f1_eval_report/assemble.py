"""组装：基础表格格式 + 各表头内容 → 完整 layout。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from f1_eval_report.base.columns import scale_columns_to_margins
from f1_eval_report.sections.catalog import (
    DEFAULT_TABLE_TEMPLATE,
    all_row_definitions,
    resolve_table_template,
)

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BASE_FORMAT = os.path.join(PACKAGE_DIR, "base", "format.json")


def load_base_format(path: Optional[str] = None) -> Dict[str, Any]:
    path = path or DEFAULT_BASE_FORMAT
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def assemble_layout(
    base_format: Optional[Dict[str, Any]] = None,
    *,
    base_format_path: Optional[str] = None,
    rows: Optional[List[Dict[str, Any]]] = None,
    table_template: Optional[str] = None,
) -> Dict[str, Any]:
    """
    合并「整体格式」与「各表头内容」为生成器可用的 layout。

    - 改 base/format.json → 控制整表边距、字号、表题、分页、A3 页策略
    - 改 sections/gbzt.py 或 sections/yp250075.py → 控制各栏目结构与数据绑定
    - table_template: ``gbzt`` | ``250075YP``（默认 250075YP）
    """
    template_id = resolve_table_template(table_template or DEFAULT_TABLE_TEMPLATE)
    fmt = base_format or load_base_format(base_format_path)
    ps = fmt.get("page_size") or {"width": 595.3, "height": 841.9}
    pw, ph = float(ps["width"]), float(ps["height"])
    margins_mm = fmt.get("margins_mm") or {
        "top": 26.0,
        "bottom": 26.0,
        "left": 28.0,
        "right": 28.0,
    }
    title = fmt.get("title") or {}
    text = fmt.get("text") or {}
    table = fmt.get("table") or {}
    pagination = fmt.get("pagination") or {}
    landscape = fmt.get("landscape_figure_page") or {}

    table_left, table_right, columns, margins_pt = scale_columns_to_margins(
        pw,
        margins_mm,
        widths_mm=(table.get("column_widths_mm") if isinstance(table, dict) else None)
        or fmt.get("column_widths_mm"),
    )
    return {
        "schema": "f1_eval_layout/v1",
        "table_id": "F.1",
        "table_template": template_id,
        "table_title": title.get("table_name", ""),
        "table_label": title.get("table_label", ""),
        "report_no": title.get("report_no", ""),
        "page_size": {"width": pw, "height": ph},
        "orientation": fmt.get("orientation", "portrait"),
        "margins_mm": dict(margins_mm),
        "margins_pt": margins_pt,
        "text": {
            "font": text.get("font", "simsun"),
            "font_size_pt": float(text.get("font_size_pt", 12.0)),
            "line_spacing_pt": float(text.get("line_spacing_pt", 25.0)),
            "char_spacing": text.get("char_spacing", "standard"),
        },
        "title": {
            "table_label": title.get("table_label", ""),
            "table_name": title.get("table_name", ""),
            "title_line1": title.get("title_line1", ""),
            "title_line2": title.get("title_line2", ""),
            "report_no": title.get("report_no", ""),
            "align": title.get("align", "center"),
            "gap_below_pt": float(title.get("gap_below_pt", 6.0)),
            "draw_on_landscape": bool(title.get("draw_on_landscape", True)),
        },
        "table": {
            "x_left": round(table_left, 2),
            "x_right": round(table_right, 2),
            "columns": columns,
            "row_min_height": float(
                table.get("row_min_height_pt", text.get("line_spacing_pt", 25.0))
            ),
            "border_width_pt": float(table.get("border_width_pt", 0.6)),
            "column_widths_mm": dict(table.get("column_widths_mm") or {}),
        },
        "pagination": {
            "repeat_title_on_each_page": bool(
                pagination.get("repeat_title_on_each_page", False)
            ),
            "repeat_row_label_on_continue": bool(
                pagination.get("repeat_row_label_on_continue", True)
            ),
            "page_number": pagination.get("page_number", "outer_bottom"),
            "page_number_uses_actual_page_size": bool(
                pagination.get("page_number_uses_actual_page_size", True)
            ),
            "report_chrome": bool(pagination.get("report_chrome", True)),
        },
        "landscape_figure_page": dict(landscape),
        "rows": rows if rows is not None else all_row_definitions(template_id),
        "_base_format": fmt,
    }


def save_layout(path: str, layout: Optional[Dict[str, Any]] = None) -> None:
    doc = layout or assemble_layout()
    # 不写出内部引用
    out = {k: v for k, v in doc.items() if not k.startswith("_")}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
