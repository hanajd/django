"""
根据 JXFS/JS-009 横版版式 JSON 生成「五、工作场所放射防护检测结果」现场记录表 PDF。
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import fitz

from radiation_detection_report.generator import (
    CELL_PAD_X,
    CELL_PAD_Y,
    FONT_SIZE_WU_HAO,
    FontResources,
    _autofit_row_height,
    _cell_lines,
    _draw_cell_border,
    _draw_cell_content,
    _load_font_resources,
    _register_fonts,
    _text_block_height,
    format_complex_location,
    resolve_complex_location,
)

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FIELD_LAYOUT_PATH = os.path.join(PACKAGE_DIR, "layout", "tabletest_layout.json")
DEFAULT_CHAPTER5_PATH = os.path.join(PACKAGE_DIR, "layout", "tabletest_chapter5_full.json")

# 本底行 10 格在模板 PDF 中的 x 分界（p7 实测）
BACKGROUND_SLOT_X = [232.0, 304.1, 378.5, 470.9, 552.4, 668.5]

# 现场记录表统一五号
FONT_SIZE = FONT_SIZE_WU_HAO


@dataclass
class FieldRecordLayout:
    page_width: float = 841.9
    page_height: float = 595.3
    section_title: str = "五、工作场所放射防护检测结果"
    title_x_left: float = 42.6
    title_x_right: float = 190.08
    title_y_top: float = 60.18
    title_height: float = 14.0
    table_x_left: float = 31.08
    table_x_right: float = 810.82
    table_y_top: float = 74.6
    continuation_y_top: float = 57.72
    content_bottom: float = 535.91
    col_point_id: Tuple[float, float] = (31.08, 65.0)
    col_location_main: Tuple[float, float] = (65.0, 147.8)
    col_location_sub: Tuple[float, float] = (147.75, 232.0)
    col_location_full: Tuple[float, float] = (65.0, 232.0)
    col_reading_1: Tuple[float, float] = (232.0, 328.35)
    col_reading_2: Tuple[float, float] = (328.35, 444.9)
    col_reading_3: Tuple[float, float] = (444.9, 552.4)
    col_mean_m: Tuple[float, float] = (552.4, 668.5)
    col_report_d: Tuple[float, float] = (668.5, 810.82)
    h_header_first: float = 20.69
    h_header_cont: float = 17.77
    h_simple: float = 18.9
    h_complex_sub: float = 19.1
    h_background: float = 32.2
    header_labels: Dict[str, str] = field(default_factory=dict)
    background_label: str = "本底水平（μSv/h）及范围"
    background_slot_labels: List[str] = field(
        default_factory=lambda: ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]
    )
    repeat_header_on_new_page: bool = True
    include_section_title: bool = True

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "FieldRecordLayout":
        if "layout" in data:
            data = data["layout"]
        st = data.get("section_title", {})
        t = data.get("table", {})
        cols = t.get("columns", {})
        pag = data.get("pagination", {})
        bg = t.get("background_row", {})

        def col(name: str, default: Tuple[float, float]) -> Tuple[float, float]:
            c = cols.get(name, {})
            return (float(c.get("x0", default[0])), float(c.get("x1", default[1])))

        location_main = (
            float(cols.get("location_main", {}).get("x0", 65.0)),
            float(t.get("complex_location_main_right", cols.get("location_main", {}).get("x1", 147.8))),
        )
        location_sub = col("location_sub", (147.75, 232.0))
        if location_sub[0] < location_main[1] - 0.5:
            location_sub = (location_main[1], location_sub[1])
        location_full = (location_main[0], location_sub[1])

        cont_y = float(pag.get("continuation_table_y_top") or 57.72)
        if "continuation_header_y" in pag and "continuation_table_y_top" not in pag:
            cont_y = float(pag["continuation_header_y"]) - 3.66

        page_h = float(data.get("page_size", {}).get("height", 595.3))
        content_bottom = float(pag.get("content_bottom") or (page_h - 59.39))
        title_y_top = float(st.get("y_top", 60.18))
        title_height = float(st.get("height", 14.0))
        table_y_top = float(
            pag.get("table_y_top_no_preface")
            or pag.get("table_y_top_first_page")
            or t.get("y_top", title_y_top + title_height + 4.0)
        )

        return cls(
            page_width=float(data.get("page_size", {}).get("width", 841.9)),
            page_height=page_h,
            section_title=str(st.get("text", "五、工作场所放射防护检测结果")),
            title_x_left=float(st.get("x_left", 42.6)),
            title_x_right=float(st.get("x_right", 190.08)),
            title_y_top=title_y_top,
            title_height=title_height,
            table_x_left=float(t.get("x_left", 31.08)),
            table_x_right=float(t.get("x_right", 810.82)),
            table_y_top=table_y_top,
            continuation_y_top=cont_y,
            content_bottom=content_bottom,
            col_point_id=col("point_id", (31.08, 65.0)),
            col_location_main=location_main,
            col_location_sub=location_sub,
            col_location_full=location_full,
            col_reading_1=col("reading_1", (232.0, 328.35)),
            col_reading_2=col("reading_2", (328.35, 444.9)),
            col_reading_3=col("reading_3", (444.9, 552.4)),
            col_mean_m=col("mean_m", (552.4, 668.5)),
            col_report_d=col("report_d", (668.5, 810.82)),
            h_header_first=float(t.get("row_heights", {}).get("header", 20.69)),
            h_simple=float(t.get("row_heights", {}).get("simple", 18.9)),
            h_complex_sub=float(t.get("row_heights", {}).get("complex_sub", 19.1)),
            header_labels=dict(t.get("header_labels", {})),
            background_label=str(bg.get("label", "本底水平（μSv/h）及范围")),
            background_slot_labels=list(
                bg.get("slot_labels") or ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]
            ),
            repeat_header_on_new_page=bool(pag.get("repeat_header_on_new_page", True)),
            include_section_title=bool(st.get("first_page_only", True)),
        )


def _reading_triplet(row: Dict[str, Any]) -> Tuple[str, str, str]:
    if "readings" in row and isinstance(row["readings"], (list, tuple)):
        vals = [str(v) for v in row["readings"]]
        while len(vals) < 3:
            vals.append("")
        return vals[0], vals[1], vals[2]
    return (
        str(row.get("reading_1", "") or ""),
        str(row.get("reading_2", "") or ""),
        str(row.get("reading_3", "") or ""),
    )


class FieldRecordTableBuilder:
    def __init__(self, layout: FieldRecordLayout, report_data: Dict[str, Any]) -> None:
        self.layout = layout
        self.data = report_data
        self.doc = fitz.open()
        self.page: Optional[fitz.Page] = None
        self.font_base = _load_font_resources()
        self.fonts: FontResources = self.font_base
        self.y: float = 0.0
        self.page_index = 0
        self.title_drawn = False
        self._id_counter = 0

    def _next_id(self, explicit: Any = None) -> str:
        if explicit is not None and str(explicit).strip():
            return str(explicit)
        self._id_counter += 1
        return str(self._id_counter)

    def _insert_page_watermark(self) -> None:
        if self.page is None:
            return
        try:
            from utils.pdf_merge import insert_report_page_watermark_bottom_layer

            insert_report_page_watermark_bottom_layer(
                self.page, self.layout.page_width, self.layout.page_height
            )
        except Exception:
            pass

    def _new_page(self, *, continuation: bool) -> None:
        self.page_index += 1
        self.page = self.doc.new_page(width=self.layout.page_width, height=self.layout.page_height)
        self._insert_page_watermark()
        self.fonts = _register_fonts(self.page, self.font_base)
        if continuation:
            self.y = self.layout.continuation_y_top
            if self.layout.repeat_header_on_new_page:
                self._draw_header_row(continuation=True)
        else:
            if not self.title_drawn and self.layout.include_section_title:
                self._draw_section_title()
                self.title_drawn = True
            self.y = self.layout.table_y_top

    def _ensure_space(self, height: float) -> None:
        if self.page is None:
            self._new_page(continuation=False)
            return
        if self.y + height > self.layout.content_bottom:
            self._new_page(continuation=True)

    def _draw_section_title(self) -> None:
        assert self.page is not None
        L = self.layout
        _draw_cell_content(
            self.page,
            fitz.Rect(L.title_x_left, L.title_y_top, L.table_x_right, L.title_y_top + L.title_height),
            L.section_title,
            self.fonts,
            FONT_SIZE,
            align="left",
        )

    def _col_rect(self, y0: float, y1: float, col: Tuple[float, float]) -> fitz.Rect:
        return fitz.Rect(col[0], y0, col[1], y1)

    def _draw_header_row(self, *, continuation: bool = False) -> None:
        h = self.layout.h_header_cont if continuation else self.layout.h_header_first
        self._ensure_space(h)
        y0, y1 = self.y, self.y + h
        L = self.layout
        labels = L.header_labels or {
            "point_id": "序号",
            "location": "检测点位置",
            "readings": "测量读数M，μSv/h",
            "mean_m": "测量均值M̄，μSv/h",
            "report_d": "报出值D，μSv/h",
        }
        cells = [
            (self._col_rect(y0, y1, L.col_point_id), labels.get("point_id", "序号"), "center"),
            (
                fitz.Rect(L.col_location_full[0], y0, L.col_location_full[1], y1),
                labels.get("location", "检测点位置"),
                "center",
            ),
            (
                fitz.Rect(L.col_reading_1[0], y0, L.col_reading_3[1], y1),
                labels.get("readings", "测量读数M，μSv/h"),
                "center",
            ),
            (self._col_rect(y0, y1, L.col_mean_m), labels.get("mean_m", "测量均值M̄，μSv/h"), "center"),
            (self._col_rect(y0, y1, L.col_report_d), labels.get("report_d", "报出值D，μSv/h"), "center"),
        ]
        for rect, text, align in cells:
            _draw_cell_border(self.page, rect)
            _draw_cell_content(self.page, rect, text, self.fonts, FONT_SIZE, align=align)
        self.y = y1

    def _draw_measurement_cells(
        self, y0: float, y1: float, row: Dict[str, Any]
    ) -> None:
        L = self.layout
        r1, r2, r3 = _reading_triplet(row)
        cells = [
            (self._col_rect(y0, y1, L.col_reading_1), r1, "center"),
            (self._col_rect(y0, y1, L.col_reading_2), r2, "center"),
            (self._col_rect(y0, y1, L.col_reading_3), r3, "center"),
            (self._col_rect(y0, y1, L.col_mean_m), str(row.get("mean_m", "")), "center"),
            (self._col_rect(y0, y1, L.col_report_d), str(row.get("report_d", "")), "center"),
        ]
        for rect, text, align in cells:
            _draw_cell_border(self.page, rect)
            _draw_cell_content(self.page, rect, text, self.fonts, FONT_SIZE, align=align)

    def _draw_simple_row(self, point: Dict[str, Any]) -> None:
        self._ensure_space(self.layout.h_simple)
        y0, y1 = self.y, self.y + self.layout.h_simple
        L = self.layout
        pid = self._next_id(point.get("id"))
        id_rect = self._col_rect(y0, y1, L.col_point_id)
        loc_rect = fitz.Rect(L.col_location_full[0], y0, L.col_location_full[1], y1)
        _draw_cell_border(self.page, id_rect)
        _draw_cell_border(self.page, loc_rect)
        _draw_cell_content(self.page, id_rect, pid, self.fonts, FONT_SIZE, align="center")
        _draw_cell_content(
            self.page, loc_rect, str(point.get("location", "")), self.fonts, FONT_SIZE, align="left"
        )
        self._draw_measurement_cells(y0, y1, point)
        self.y = y1

    def _complex_group_height(self, point: Dict[str, Any], sub_count: int) -> float:
        L = self.layout
        location = resolve_complex_location(point)
        main_w = max(8.0, L.col_location_main[1] - L.col_location_main[0] - 2 * CELL_PAD_X)
        lines = _cell_lines(location, main_w, self.fonts, FONT_SIZE)
        main_need = _text_block_height(len(lines), FONT_SIZE) + 2 * CELL_PAD_Y
        subs_need = sub_count * L.h_complex_sub
        return max(subs_need, main_need)

    def _draw_complex_point(self, point: Dict[str, Any]) -> None:
        subs = point.get("sub_rows") or []
        if not subs:
            self._draw_simple_row(point)
            return
        total_h = self._complex_group_height(point, len(subs))
        self._ensure_space(total_h)
        y0 = self.y
        y1 = y0 + total_h
        L = self.layout

        pid = self._next_id(point.get("id"))
        id_rect = fitz.Rect(L.col_point_id[0], y0, L.col_point_id[1], y1)
        main_rect = fitz.Rect(L.col_location_main[0], y0, L.col_location_main[1], y1)
        _draw_cell_border(self.page, id_rect)
        _draw_cell_border(self.page, main_rect)
        _draw_cell_content(self.page, id_rect, pid, self.fonts, FONT_SIZE, align="center")
        _draw_cell_content(
            self.page, main_rect, resolve_complex_location(point), self.fonts, FONT_SIZE, align="left"
        )

        sub_h = total_h / len(subs)
        for i, sub in enumerate(subs):
            sy0 = y0 + i * sub_h
            sy1 = sy0 + sub_h
            sub_rect = fitz.Rect(L.col_location_sub[0], sy0, L.col_location_sub[1], sy1)
            _draw_cell_border(self.page, sub_rect)
            _draw_cell_content(
                self.page, sub_rect, str(sub.get("location_sub", "")), self.fonts, FONT_SIZE, align="left"
            )
            self._draw_measurement_cells(sy0, sy1, sub)
        self.y = y1

    def _notes_row_height(self, notes: Sequence[str]) -> float:
        text = "\n".join(str(n) for n in notes)
        inner_w = max(8.0, self.layout.table_x_right - self.layout.table_x_left - 2 * CELL_PAD_X)
        min_notes_h = _text_block_height(1, FONT_SIZE) + 2 * CELL_PAD_Y
        return _autofit_row_height(text, inner_w, self.fonts, FONT_SIZE, min_height=min_notes_h)

    def _draw_background_row(self, bg: Dict[str, Any], *, reserve_space: bool = True) -> None:
        h = self.layout.h_background
        if reserve_space:
            self._ensure_space(h)
        y0 = self.y
        y1 = y0 + h
        L = self.layout

        label_rect = fitz.Rect(L.table_x_left, y0, L.col_location_full[1], y1)
        _draw_cell_border(self.page, label_rect)
        _draw_cell_content(
            self.page,
            label_rect,
            str(bg.get("label", L.background_label)),
            self.fonts,
            FONT_SIZE,
            align="center",
        )

        slots = bg.get("slots") or bg.get("values") or []
        slot_labels = L.background_slot_labels
        row_h = h / 2.0
        for i in range(10):
            row_idx = 0 if i < 5 else 1
            col_idx = i % 5
            sx0 = BACKGROUND_SLOT_X[col_idx]
            sx1 = BACKGROUND_SLOT_X[col_idx + 1]
            sy0 = y0 + row_idx * row_h
            sy1 = sy0 + row_h
            slot_rect = fitz.Rect(sx0, sy0, sx1, sy1)
            _draw_cell_border(self.page, slot_rect)
            label = slot_labels[i] if i < len(slot_labels) else str(i + 1)
            value = ""
            if isinstance(slots, dict):
                value = str(slots.get(label, slots.get(str(i + 1), "")) or "")
            elif i < len(slots):
                value = str(slots[i] or "")
            text = f"{label}{value}" if value else label
            _draw_cell_content(self.page, slot_rect, text, self.fonts, FONT_SIZE, align="center")

        range_rect = fitz.Rect(L.col_report_d[0], y0, L.col_report_d[1], y1)
        _draw_cell_border(self.page, range_rect)
        _draw_cell_content(
            self.page,
            range_rect,
            str(bg.get("range", bg.get("value", ""))),
            self.fonts,
            FONT_SIZE,
            align="center",
        )
        self.y = y1

    def _draw_notes_row(self, notes: Sequence[str], *, reserve_space: bool = True) -> None:
        text = "\n".join(str(n) for n in notes)
        row_h = self._notes_row_height(notes)
        if reserve_space:
            self._ensure_space(row_h)
        y0, y1 = self.y, self.y + row_h
        rect = fitz.Rect(self.layout.table_x_left, y0, self.layout.table_x_right, y1)
        _draw_cell_border(self.page, rect)
        _draw_cell_content(self.page, rect, text, self.fonts, FONT_SIZE, align="left")
        self.y = y1

    def build(self) -> fitz.Document:
        self._new_page(continuation=False)
        self._draw_header_row(continuation=False)
        for point in self.data.get("points", []):
            if point.get("type", "simple") == "complex":
                self._draw_complex_point(point)
            else:
                self._draw_simple_row(point)
        bg = self.data.get("background")
        notes = self.data.get("notes") or []
        tail_h = 0.0
        if bg:
            tail_h += self.layout.h_background
        if notes:
            tail_h += self._notes_row_height(notes)
        if tail_h > 0.0:
            self._ensure_space(tail_h)
        if bg:
            self._draw_background_row(bg, reserve_space=False)
        if notes:
            self._draw_notes_row(notes, reserve_space=False)
        return self.doc

    def save(self, output_path: str) -> None:
        doc = self.build()
        doc.save(output_path)
        doc.close()


def load_field_record_layout(path: str) -> FieldRecordLayout:
    with open(path, "r", encoding="utf-8") as f:
        return FieldRecordLayout.from_json(json.load(f))


def load_field_record_data(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def generate_field_record_pdf(layout_path: str, data_path: str, output_pdf: str) -> None:
    layout = load_field_record_layout(layout_path)
    data = load_field_record_data(data_path)
    FieldRecordTableBuilder(layout, data).save(output_pdf)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate JXFS/JS-009 field record table PDF.")
    parser.add_argument("--layout", default=DEFAULT_FIELD_LAYOUT_PATH)
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", default="field_record_output.pdf")
    args = parser.parse_args()
    generate_field_record_pdf(args.layout, args.data, args.output)
    print(f"PDF written: {args.output}")


if __name__ == "__main__":
    main()
