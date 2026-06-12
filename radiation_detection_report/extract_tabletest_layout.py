"""
从 tabletest 类 PDF（JXFS/JS-009 横版原始记录）提取
「五、工作场所放射防护检测结果」表格版式。

与 fhtest（竖版 1.1 报告）为不同模板，schema 为 workplace_radiation_js009/v1。
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import fitz

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LAYOUT_OUT = os.path.join(PACKAGE_DIR, "layout", "tabletest_layout.json")


def _rect_tuple(cell) -> Optional[Tuple[float, float, float, float]]:
    if cell is None:
        return None
    if isinstance(cell, (list, tuple)) and len(cell) >= 4:
        return tuple(float(v) for v in cell[:4])
    if hasattr(cell, "bbox"):
        b = cell.bbox
        return float(b.x0), float(b.y0), float(b.x1), float(b.y1)
    return None


def find_chapter5_page(pdf_path: str) -> int:
    """定位「五、工作场所放射防护检测结果」起始页（1-based）。"""
    doc = fitz.open(pdf_path)
    for i in range(len(doc)):
        text = doc[i].get_text()
        if "五、工作场所放射防护" in text or (
            "工作场所放射防护检测结果" in text and "五、" in text
        ):
            doc.close()
            return i + 1
    doc.close()
    raise RuntimeError("未找到第五章「工作场所放射防护检测结果」页面")


def _table_rows(page: fitz.Page) -> List[List[Tuple[int, Tuple[float, float, float, float]]]]:
    if not hasattr(page, "find_tables"):
        return []
    try:
        tables = page.find_tables().tables
    except Exception:
        return []
    if not tables:
        return []
    rows_out: List[List[Tuple[int, Tuple[float, float, float, float]]]] = []
    for row in getattr(tables[0], "rows", None) or []:
        cells = getattr(row, "cells", None) or []
        row_cells: List[Tuple[int, Tuple[float, float, float, float]]] = []
        for ci, cell in enumerate(cells):
            rect = _rect_tuple(cell)
            if rect:
                row_cells.append((ci, rect))
        if row_cells:
            rows_out.append(row_cells)
    return rows_out


def _col_bounds_from_data_rows(
    rows: List[List[Tuple[int, Tuple[float, float, float, float]]]],
    skip_header_rows: int = 2,
) -> Dict[int, Dict[str, float]]:
    """从数据行统计各物理列 x 边界（跳过表头、跳过大跨度合并行）。"""
    xs: Dict[int, List[float]] = {}
    table_right = max(rect[2] for row in rows for _, rect in row) if rows else 810.0
    for row in rows[skip_header_rows:]:
        spans = list(row)
        if len(spans) <= 1:
            continue
        # 跳过整行合并（如注释行）
        if len(spans) == 1 and spans[0][1][2] - spans[0][1][0] > table_right * 0.7:
            continue
        for ci, rect in spans:
            xs.setdefault(ci, []).extend([rect[0], rect[2]])
    out: Dict[int, Dict[str, float]] = {}
    for ci, vals in xs.items():
        out[ci] = {"x0": min(vals), "x1": max(vals)}
    return out


def _find_title_block(words: Sequence) -> Dict[str, Any]:
    for w in words:
        t = str(w[4])
        if "五、工作场所放射防护" in t or t.startswith("五、"):
            return {
                "text": "五、工作场所放射防护检测结果",
                "x_left": float(w[0]),
                "x_right": float(w[2]),
                "y_top": float(w[1]),
            }
    return {"text": "五、工作场所放射防护检测结果", "x_left": 42.6, "x_right": 190.1, "y_top": 60.2}


def _find_block_y(words: Sequence, prefix: str) -> Optional[float]:
    for w in words:
        if str(w[4]).startswith(prefix):
            return float(w[1])
    return None


def extract_tabletest_layout(pdf_path: str, page_no: Optional[int] = None) -> Dict[str, Any]:
    if page_no is None:
        page_no = find_chapter5_page(pdf_path)

    doc = fitz.open(pdf_path)
    page = doc[page_no - 1]
    words = page.get_text("words")
    rows = _table_rows(page)
    if not rows:
        doc.close()
        raise RuntimeError(f"第 {page_no} 页未检测到表格")

    table_left = min(rect[0] for row in rows for _, rect in row)
    table_right = max(rect[2] for row in rows for _, rect in row)
    table_top = min(rect[1] for row in rows for _, rect in row)
    table_bottom = max(rect[3] for row in rows for _, rect in row)

    col_raw = _col_bounds_from_data_rows(rows, skip_header_rows=2)

    # 语义列映射（tabletest 8 列结构）
    def pick(ci: int, default: Tuple[float, float]) -> Dict[str, float]:
        if ci in col_raw:
            return {k: round(v, 2) for k, v in col_raw[ci].items()}
        return {"x0": default[0], "x1": default[1]}

    columns = {
        "point_id": pick(0, (31.1, 65.0)),
        "location_main": pick(1, (65.0, 147.8)),
        "location_sub": pick(2, (147.8, 232.0)),
        "location_full": {"x0": 65.0, "x1": 232.0},
        "reading_1": pick(3, (232.0, 328.4)),
        "reading_2": pick(4, (328.4, 444.9)),
        "reading_3": pick(5, (444.9, 552.4)),
        "readings_merged": {"x0": 232.0, "x1": 552.4},
        "mean_m": pick(6, (552.4, 668.5)),
        "report_d": pick(7, (668.5, 810.8)),
    }
    if columns["location_main"]["x1"] > 200:
        columns["location_main"] = {"x0": 65.0, "x1": 147.8}

    # 行高：从典型行推断
    def row_h(ri: int) -> float:
        if ri >= len(rows):
            return 20.0
        ys = [rect[1] for _, rect in rows[ri]]
        ye = [rect[3] for _, rect in rows[ri]]
        return max(ye) - min(ys)

    header_h = row_h(0) + row_h(1) if len(rows) > 1 else 20.0
    simple_h = row_h(2) if len(rows) > 2 else 20.0
    complex_sub_h = row_h(7) if len(rows) > 7 else 18.5

    title = _find_title_block(words)
    instrument_y = _find_block_y(words, "（1）、")
    condition_y = _find_block_y(words, "（2）、")

    page_h = float(page.rect.height)
    page_w = float(page.rect.width)

    # 续页表头（第 6 页）
    cont_header_y = None
    if page_no + 1 <= len(doc):
        p2 = doc[page_no]
        w2 = p2.get_text("words")
        for w in w2:
            if str(w[4]) == "序号":
                cont_header_y = float(w[1])
                break

    layout = {
        "schema": "workplace_radiation_js009/v1",
        "template_id": "JXFS/JS-009",
        "source_pdf": os.path.basename(pdf_path),
        "reference_page": page_no,
        "orientation": "landscape",
        "page_size": {"width": page_w, "height": page_h},
        "section_title": {
            "text": title["text"],
            "x_left": round(title["x_left"], 2),
            "x_right": round(title["x_right"], 2),
            "y_top": round(title["y_top"], 2),
            "first_page_only": True,
        },
        "preface_blocks": {
            "instrument_info": {
                "label": "（1）、使用的检测仪器相关信息",
                "y_top": round(instrument_y, 2) if instrument_y else 73.0,
                "x_left": round(title["x_left"], 2),
                "x_right": round(table_right, 2),
                "first_page_only": True,
            },
            "condition": {
                "label": "（2）、检测条件",
                "y_top": round(condition_y, 2) if condition_y else 89.3,
                "x_left": round(title["x_left"], 2),
                "x_right": round(table_right, 2),
                "first_page_only": True,
            },
        },
        "table": {
            "x_left": round(table_left, 2),
            "x_right": round(table_right, 2),
            "y_top": round(table_top, 2),
            "columns": columns,
            "header_labels": {
                "point_id": "序号",
                "location": "检测点位置",
                "readings": "测量读数M，μSv/h",
                "mean_m": "测量均值M̄，μSv/h",
                "report_d": "报出值D，μSv/h",
            },
            "header_row_count": 2,
            "row_heights": {
                "header": round(header_h, 2),
                "simple": round(simple_h, 2),
                "complex_sub": round(complex_sub_h, 2),
            },
            "complex_location_main_right": columns["location_main"]["x1"],
            "background_row": {
                "label": "本底水平（μSv/h）及范围",
                "reading_slots": 10,
                "slot_labels": ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"],
            },
            "notes": {
                "below_table": True,
                "full_width": True,
            },
        },
        "pagination": {
            "table_y_top_first_page": round(table_top, 2),
            "continuation_header_y": round(cont_header_y, 2) if cont_header_y else 57.7,
            "content_bottom": round(table_bottom, 2),
            "footer_margin_below_table": round(max(0.0, page_h - table_bottom), 2),
            "repeat_header_on_new_page": True,
            "repeat_section_title_on_new_page": False,
            "include_preface_on_first_page_only": True,
        },
        "point_patterns": {
            "simple": "单行：序号 + 检测点位置（跨 location_main~location_sub）+ 3次读数 + 均值 + 报出值",
            "complex": "复杂：序号与 location_main 纵向合并；location_sub 为中部/上端等；右侧同简单行",
            "location_format": "名称与距离连写，如「观察窗C外表面30cm」「防护门M（）外表面30cm」",
        },
    }
    doc.close()
    return layout


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract JXFS/JS-009 workplace radiation table layout from tabletest PDF."
    )
    parser.add_argument("--input", default="tabletest.pdf", help="Input PDF (e.g. tabletest.pdf)")
    parser.add_argument(
        "--page",
        type=int,
        default=None,
        help="Reference page (1-based); auto-detect chapter 5 if omitted",
    )
    parser.add_argument("--output", default=DEFAULT_LAYOUT_OUT, help="Output layout JSON")
    args = parser.parse_args()

    input_path = args.input
    if not os.path.isabs(input_path) and not os.path.exists(input_path):
        parent = os.path.dirname(os.path.dirname(PACKAGE_DIR))
        alt = os.path.join(os.path.dirname(PACKAGE_DIR), input_path)
        if os.path.exists(alt):
            input_path = alt

    layout = extract_tabletest_layout(input_path, args.page)
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(layout, f, ensure_ascii=False, indent=2)
    print(f"Chapter 5 starts at page: {layout['reference_page']}")
    print(f"Layout written: {args.output}")


if __name__ == "__main__":
    main()
