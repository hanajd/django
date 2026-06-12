"""
提取 tabletest.pdf 第五章「五、工作场所放射防护检测结果」
至第六章「六、平面布局示意图」之前的全部表格内容与版式。

输出：layout + 完整表格模板（含所有检测点行、本底行、注释行）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import fitz

try:
    from .extract_tabletest_layout import (
        _find_block_y,
        _find_title_block,
        _table_rows,
        extract_tabletest_layout,
        find_chapter5_page,
    )
except ImportError:
    from extract_tabletest_layout import (
        _find_block_y,
        _find_title_block,
        _table_rows,
        extract_tabletest_layout,
        find_chapter5_page,
    )

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CHAPTER5_OUT = os.path.join(PACKAGE_DIR, "layout", "tabletest_chapter5_full.json")


def find_chapter6_page(pdf_path: str) -> int:
    doc = fitz.open(pdf_path)
    for i in range(len(doc)):
        text = doc[i].get_text()
        if "六、平面布局" in text or text.strip().startswith("六、"):
            doc.close()
            return i + 1
    doc.close()
    raise RuntimeError("未找到第六章「六、平面布局示意图」")


def find_chapter5_bounds(pdf_path: str) -> Tuple[int, int]:
    start = find_chapter5_page(pdf_path)
    end = find_chapter6_page(pdf_path) - 1
    if end < start:
        raise RuntimeError(f"章节范围无效: 第五章 p{start} 至 p{end}")
    return start, end


def _cell_text(page: fitz.Page, rect: Tuple[float, float, float, float]) -> str:
    return page.get_text("text", clip=fitz.Rect(rect)).strip()


def _normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _is_header_row(cells: Dict[int, str]) -> bool:
    joined = "".join(cells.values())
    return "序号" in joined and "检测点位置" in joined


def _is_notes_row(cells: Dict[int, str]) -> bool:
    t = _normalize_text("".join(cells.values()))
    return t.startswith("1.检测") or t.startswith("1．检测") or "响应时间修正系数" in t


def _is_background_row(cells: Dict[int, str]) -> bool:
    return "本底水平" in "".join(cells.values())


def _row_bbox(cells: List[Tuple[int, Tuple[float, float, float, float]]]) -> Dict[str, float]:
    xs0 = [r[0] for _, r in cells]
    ys0 = [r[1] for _, r in cells]
    xs1 = [r[2] for _, r in cells]
    ys1 = [r[3] for _, r in cells]
    return {
        "x0": round(min(xs0), 2),
        "y0": round(min(ys0), 2),
        "x1": round(max(xs1), 2),
        "y1": round(max(ys1), 2),
        "h": round(max(ys1) - min(ys0), 2),
    }


def _parse_table_rows_on_page(
    page: fitz.Page, page_no: int, skip_leading_header: bool
) -> Tuple[List[Dict[str, Any]], bool]:
    """解析单页表格行。返回 (rows, saw_header)。"""
    raw_rows = _table_rows(page)
    parsed: List[Dict[str, Any]] = []
    saw_header = False

    for ri, row_cells in enumerate(raw_rows):
        texts: Dict[int, str] = {}
        rects: Dict[int, Tuple[float, float, float, float]] = {}
        for ci, rect in row_cells:
            texts[ci] = _normalize_text(_cell_text(page, rect))
            rects[ci] = rect

        if _is_header_row(texts):
            saw_header = True
            if skip_leading_header:
                continue
            parsed.append(
                {
                    "kind": "header",
                    "page": page_no,
                    "row_index": ri,
                    "bbox": _row_bbox(row_cells),
                    "cells": {str(k): v for k, v in texts.items() if v},
                }
            )
            continue

        if _is_notes_row(texts):
            note_text = _normalize_text("".join(texts.values()))
            parsed.append(
                {
                    "kind": "notes",
                    "page": page_no,
                    "row_index": ri,
                    "bbox": _row_bbox(row_cells),
                    "text": note_text,
                }
            )
            continue

        if _is_background_row(texts):
            slots = []
            for k in sorted(texts.keys()):
                v = texts[k]
                if v and v not in ("本底水平（μSv /h）及范围", "本底水平（μSv/h）及范围"):
                    slots.append({"col": k, "label": v})
            parsed.append(
                {
                    "kind": "background",
                    "page": page_no,
                    "row_index": ri,
                    "bbox": _row_bbox(row_cells),
                    "label": "本底水平（μSv/h）及范围",
                    "slots": slots,
                    "cells": {str(k): v for k, v in texts.items()},
                }
            )
            continue

        loc_main = texts.get(1, "")
        loc_sub = texts.get(2, "")
        point_id = texts.get(0, "")

        if not loc_main and not loc_sub and not point_id:
            if any(texts.values()):
                parsed.append(
                    {
                        "kind": "other",
                        "page": page_no,
                        "row_index": ri,
                        "bbox": _row_bbox(row_cells),
                        "cells": {str(k): v for k, v in texts.items() if v},
                    }
                )
            else:
                parsed.append(
                    {
                        "kind": "empty",
                        "page": page_no,
                        "row_index": ri,
                        "bbox": _row_bbox(row_cells),
                    }
                )
            continue

        if loc_main and loc_sub:
            parsed.append(
                {
                    "kind": "complex_sub",
                    "page": page_no,
                    "row_index": ri,
                    "bbox": _row_bbox(row_cells),
                    "location_main": loc_main,
                    "location_sub": loc_sub,
                    "readings": [texts.get(i, "") for i in (3, 4, 5) if i in texts or i <= 5],
                }
            )
        elif loc_sub and not loc_main:
            parsed.append(
                {
                    "kind": "complex_sub",
                    "page": page_no,
                    "row_index": ri,
                    "bbox": _row_bbox(row_cells),
                    "location_sub": loc_sub,
                    "readings": [texts.get(i, "") for i in (3, 4, 5)],
                }
            )
        elif loc_main:
            parsed.append(
                {
                    "kind": "simple",
                    "page": page_no,
                    "row_index": ri,
                    "bbox": _row_bbox(row_cells),
                    "point_id": point_id,
                    "location": loc_main,
                    "readings": [texts.get(i, "") for i in (3, 4, 5)],
                }
            )

    return parsed, saw_header


def _group_complex_points(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """将 complex_sub 行合并为复杂点位组。"""
    groups: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for row in rows:
        kind = row.get("kind")
        if kind == "simple":
            if current:
                groups.append(current)
                current = None
            groups.append(
                {
                    "type": "simple",
                    "location": row.get("location", ""),
                    "point_id": row.get("point_id", ""),
                    "template_source": {"page": row["page"], "row_index": row["row_index"]},
                }
            )
        elif kind == "complex_sub":
            main = row.get("location_main", "")
            if main:
                if current:
                    groups.append(current)
                current = {
                    "type": "complex",
                    "location": main,
                    "sub_rows": [],
                    "template_source": {"page": row["page"], "row_index": row["row_index"]},
                }
                if row.get("location_sub"):
                    current["sub_rows"].append({"location_sub": row["location_sub"]})
            elif current:
                sub = row.get("location_sub", "")
                if sub:
                    current["sub_rows"].append({"location_sub": sub})
        elif kind in ("header", "empty", "background", "notes", "other"):
            if current:
                groups.append(current)
                current = None
            groups.append(row)
        else:
            if current:
                groups.append(current)
                current = None
            groups.append(row)

    if current:
        groups.append(current)
    return groups


def _extract_preface(page: fitz.Page) -> Dict[str, Any]:
    words = page.get_text("words")
    title = _find_title_block(words)
    blocks = page.get_text().split("\n")
    instrument_lines: List[str] = []
    condition_lines: List[str] = []
    mode = None
    for line in blocks:
        s = line.strip()
        if not s:
            continue
        if s.startswith("（1）"):
            mode = "instrument"
        elif s.startswith("（2）"):
            mode = "condition"
        elif s.startswith("序号") or s.startswith("五、"):
            mode = None
        elif mode == "instrument":
            instrument_lines.append(s)
        elif mode == "condition":
            condition_lines.append(s)

    return {
        "section_title": title,
        "instrument_info": {
            "label": "（1）、使用的检测仪器相关信息",
            "y_top": round(_find_block_y(words, "（1）、") or 74.0, 2),
            "text": _normalize_text(" ".join(instrument_lines)),
        },
        "condition": {
            "label": "（2）、检测条件",
            "y_top": round(_find_block_y(words, "（2）、") or 89.0, 2),
            "text": _normalize_text(" ".join(condition_lines)),
        },
    }


def extract_chapter5_full(pdf_path: str) -> Dict[str, Any]:
    start_page, end_page = find_chapter5_bounds(pdf_path)
    layout = extract_tabletest_layout(pdf_path, start_page)
    layout["chapter_range"] = {"start_page": start_page, "end_page": end_page}

    doc = fitz.open(pdf_path)
    all_raw_rows: List[Dict[str, Any]] = []
    pages_meta: List[Dict[str, Any]] = []
    preface: Optional[Dict[str, Any]] = None

    for pi in range(start_page - 1, end_page):
        page = doc[pi]
        page_no = pi + 1
        skip_header = page_no > start_page
        rows, _ = _parse_table_rows_on_page(page, page_no, skip_leading_header=skip_header)
        all_raw_rows.extend(rows)

        raw = _table_rows(page)
        table_bottom = max(rect[3] for row in raw for _, rect in row) if raw else 0
        pages_meta.append(
            {
                "page": page_no,
                "width": float(page.rect.width),
                "height": float(page.rect.height),
                "table_row_count": len(raw),
                "table_bottom_y": round(table_bottom, 2),
                "parsed_row_count": len(rows),
            }
        )

        if page_no == start_page and preface is None:
            preface = _extract_preface(page)

    doc.close()

    grouped = _group_complex_points(all_raw_rows)

    points_template = []
    background = None
    notes = None
    headers: List[Dict[str, Any]] = []

    for item in grouped:
        k = item.get("kind") or item.get("type")
        if k == "header":
            headers.append(item)
        elif k == "background":
            background = item
        elif k == "notes":
            notes = item
        elif k in ("simple", "complex"):
            entry: Dict[str, Any] = {"type": k}
            if k == "simple":
                entry["location"] = item.get("location", "")
            else:
                entry["location"] = item.get("location", "")
                entry["sub_rows"] = item.get("sub_rows", [])
            points_template.append(entry)

    # 去掉尾部空复杂子行
    for p in points_template:
        if p["type"] == "complex":
            p["sub_rows"] = [s for s in p["sub_rows"] if s.get("location_sub")]

    return {
        "schema": "workplace_radiation_js009_chapter5/v1",
        "template_id": "JXFS/JS-009",
        "source_pdf": os.path.basename(pdf_path),
        "chapter_range": {"start_page": start_page, "end_page": end_page, "before_section": "六、平面布局示意图"},
        "layout": layout,
        "preface": preface,
        "pages": pages_meta,
        "table_template": {
            "header_rows": headers,
            "points": points_template,
            "background": background,
            "notes": notes,
        },
        "raw_rows": all_raw_rows,
        "summary": {
            "point_groups": len(points_template),
            "simple_points": sum(1 for p in points_template if p["type"] == "simple"),
            "complex_points": sum(1 for p in points_template if p["type"] == "complex"),
            "total_sub_rows": sum(len(p.get("sub_rows", [])) for p in points_template if p["type"] == "complex"),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract full Chapter 5 workplace radiation tables until Chapter 6."
    )
    parser.add_argument("--input", default="tabletest.pdf")
    parser.add_argument("--output", default=DEFAULT_CHAPTER5_OUT)
    parser.add_argument("--compact", action="store_true", help="Omit raw_rows in output")
    args = parser.parse_args()

    input_path = args.input
    if not os.path.isabs(input_path) and not os.path.exists(input_path):
        for alt in (
            os.path.join(os.path.dirname(PACKAGE_DIR), input_path),
            os.path.join(PACKAGE_DIR, input_path),
        ):
            if os.path.exists(alt):
                input_path = alt
                break

    data = extract_chapter5_full(input_path)
    if args.compact:
        data.pop("raw_rows", None)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    r = data["chapter_range"]
    s = data["summary"]
    print(f"Chapter 5 pages: {r['start_page']}-{r['end_page']} (before {r['before_section']})")
    print(f"Points: {s['simple_points']} simple + {s['complex_points']} complex ({s['total_sub_rows']} sub-rows)")
    print(f"Written: {args.output}")


if __name__ == "__main__":
    main()
