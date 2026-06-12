"""
从 PDF 指定页提取「1.1 工作场所放射防护检测结果」表格版式坐标。
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import fitz

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LAYOUT_OUT = os.path.join(PACKAGE_DIR, "layout", "default_layout.json")


def _rect_tuple(cell) -> Optional[Tuple[float, float, float, float]]:
    if cell is None:
        return None
    if isinstance(cell, (list, tuple)) and len(cell) >= 4:
        return tuple(float(v) for v in cell[:4])
    if hasattr(cell, "bbox"):
        b = cell.bbox
        return float(b.x0), float(b.y0), float(b.x1), float(b.y1)
    return None


def _find_section_title(words: Sequence) -> Dict[str, float]:
    title_parts: List[Tuple[float, float, str]] = []
    for w in words:
        text = str(w[4])
        if text.startswith("1.1") or "工作场所" in text or "放射防护" in text:
            title_parts.append((float(w[0]), float(w[2]), text))
    if not title_parts:
        return {"x_left": 79.0, "x_right": 245.0, "y_top": 74.0}
    x_left = min(p[0] for p in title_parts)
    x_right = max(p[1] for p in title_parts)
    y_top = min(float(w[1]) for w in words if str(w[4]).startswith("1.1")) if title_parts else 74.0
    return {"x_left": x_left, "x_right": x_right, "y_top": y_top}


def _table_rows(page: fitz.Page) -> List[List[Tuple[int, Tuple[float, float, float, float]]]]:
    if not hasattr(page, "find_tables"):
        return []
    try:
        tables = page.find_tables().tables
    except Exception:
        return []
    if not tables:
        return []
    table = tables[0]
    rows_out: List[List[Tuple[int, Tuple[float, float, float, float]]]] = []
    for row in getattr(table, "rows", None) or []:
        cells = getattr(row, "cells", None) or []
        row_cells: List[Tuple[int, Tuple[float, float, float, float]]] = []
        for ci, cell in enumerate(cells):
            rect = _rect_tuple(cell)
            if rect:
                row_cells.append((ci, rect))
        if row_cells:
            rows_out.append(row_cells)
    return rows_out


def _column_bounds(rows: List[List[Tuple[int, Tuple[float, float, float, float]]]]) -> Dict[str, Dict[str, float]]:
    xs: Dict[int, List[float]] = {}
    table_right = max(rect[2] for row in rows for _, rect in row)
    for row in rows:
        # 跳过检测条件等整行合并行，避免列宽被拉满
        spans = [(ci, rect) for ci, rect in row]
        if len(spans) <= 1:
            continue
        if len(spans) == 1 and spans[0][1][2] - spans[0][1][0] > table_right * 0.85:
            continue
        for ci, rect in spans:
            xs.setdefault(ci, []).extend([rect[0], rect[2]])
    col_ids = sorted(xs.keys())
    bounds = {}
    for ci in col_ids:
        vals = xs[ci]
        bounds[f"col_{ci}"] = {"x0": min(vals), "x1": max(vals)}
    # 映射到语义列名（与 fhtest 6 列表头一致）
    mapping = {
        0: "point_id",
        1: "location_main",
        2: "location_sub",
        3: "result",
        4: "standard",
        5: "evaluation",
    }
    named: Dict[str, Dict[str, float]] = {}
    for ci, name in mapping.items():
        key = f"col_{ci}"
        if key in bounds:
            named[name] = bounds[key]
    return named


def extract_layout(pdf_path: str, page_no: int) -> Dict[str, Any]:
    doc = fitz.open(pdf_path)
    page = doc[page_no - 1]
    words = page.get_text("words")
    title = _find_section_title(words)
    rows = _table_rows(page)
    if not rows:
        raise RuntimeError(f"No table found on page {page_no}")

    table_left = min(rect[0] for row in rows for _, rect in row)
    table_right = max(rect[2] for row in rows for _, rect in row)
    table_top = min(rect[1] for row in rows for _, rect in row)

    condition_rect = rows[0][0][1] if rows else None
    header_rect = rows[1][0][1] if len(rows) > 1 else None
    simple_rect = rows[2][0][1] if len(rows) > 2 else None

    # 复杂点位：找纵向合并的 location_main（col1 高度 > 2 倍简单行）
    complex_main_h = None
    complex_sub_h = None
    for row in rows[3:12]:
        by_col = {ci: rect for ci, rect in row}
        if 1 in by_col and 2 in by_col:
            main_h = by_col[1][3] - by_col[1][1]
            sub_h = by_col[2][3] - by_col[2][1]
            if main_h > (simple_rect[3] - simple_rect[1]) * 1.8 if simple_rect else 30:
                complex_main_h = main_h
                complex_sub_h = sub_h
                break

    table_bottom = max(rect[3] for row in rows for _, rect in row)
    page_h = float(page.rect.height)
    footer_margin = round(max(0.0, page_h - table_bottom), 2)

    columns = _column_bounds(rows)
    main_right = 198.45
    if "location_main" in columns:
        xr = columns["location_main"]["x1"]
        if xr < 210.0:
            main_right = xr

    layout = {
        "schema": "radiation_detection_table_layout/v1",
        "source_pdf": os.path.basename(pdf_path),
        "reference_page": page_no,
        "page_size": {"width": float(page.rect.width), "height": float(page.rect.height)},
        "section_title": {
            "text": "1.1工作场所放射防护检测结果",
            "x_left": round(title["x_left"], 2),
            "x_right": round(title["x_right"], 2),
            "y_top": round(title["y_top"], 2),
        },
        "table": {
            "x_left": round(table_left, 2),
            "x_right": round(table_right, 2),
            "y_top": round(table_top, 2),
            "columns": {k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in columns.items()},
            "complex_location_main_right": round(main_right, 2),
            "row_heights": {
                "condition": round(condition_rect[3] - condition_rect[1], 2) if condition_rect else 33.6,
                "header": round(header_rect[3] - header_rect[1], 2) if header_rect else 40.5,
                "simple": round(simple_rect[3] - simple_rect[1], 2) if simple_rect else 21.8,
                "complex_sub": round(complex_sub_h, 2) if complex_sub_h else 25.0,
            },
            "complex_location_main_width": round(columns.get("location_main", {}).get("x1", 198.45) - columns.get("location_main", {}).get("x0", 120.6), 2),
            "complex_location_sub_width": round(columns.get("location_sub", {}).get("x1", 326.0) - columns.get("location_sub", {}).get("x0", 198.45), 2),
            "header_labels": {
                "point_id": "检测点\n编号",
                "location": "检测点位置",
                "result": "检测结果\n(μSv/h)",
                "standard": "标准要求\n(μSv/h)",
                "evaluation": "结果\n评价",
            },
            "background_row": {
                "label": "本底值（μSv/h）",
                "merge_columns": ["point_id", "location_main", "location_sub"],
                "value_columns": ["result", "standard", "evaluation"],
            },
            "notes_row": {
                "full_width": True,
                "prefix": "注：",
            },
        },
        "pagination": {
            "content_top": round(title["y_top"] + 16, 2),
            "continuation_y_top": 72.0,
            "table_max_bottom": round(table_bottom, 2),
            "footer_margin_below_table": footer_margin,
            "content_bottom": round(table_bottom, 2),
            "repeat_header_on_new_page": True,
            "include_condition_on_first_page_only": True,
            "repeat_section_title_on_new_page": False,
        },
    }
    doc.close()
    return layout


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract radiation detection table layout from PDF page.")
    parser.add_argument("--input", default="fhtest.pdf", help="Input PDF")
    parser.add_argument("--page", type=int, default=6, help="PDF page number (1-based)")
    parser.add_argument("--output", default=DEFAULT_LAYOUT_OUT, help="Output layout JSON")
    args = parser.parse_args()
    layout = extract_layout(args.input, args.page)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(layout, f, ensure_ascii=False, indent=2)
    print(f"Layout written: {args.output}")


if __name__ == "__main__":
    main()
