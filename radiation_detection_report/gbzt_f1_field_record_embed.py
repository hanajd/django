"""
在 GBZ/T 表 F.1 的 value 区域内嵌套渲染「工作场所放射防护检测结果」现场记录表。
五号字原生绘制，列宽按 F.1 嵌套区域等比压缩，不缩放 PDF。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import fitz

from radiation_detection_report.field_record_generator import (
    FieldRecordLayout,
    _reading_triplet,
    load_field_record_layout,
)
from radiation_detection_report.generator import (
    CELL_PAD_X,
    CELL_PAD_Y,
    FONT_SIZE_WU_HAO,
    _autofit_row_height,
    _draw_cell_border,
    _draw_cell_content,
    _text_block_height,
    resolve_complex_location,
)

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))

# 嵌套表统一五号（与独立现场记录表一致）
EMBED_FONT_SIZE = FONT_SIZE_WU_HAO
# 嵌套表外框与 F.1 单元格边框间距（pt）
EMBED_CELL_PAD = CELL_PAD_X

# 嵌套区列宽比例（五号字下优化，避免表头/序号列过窄）
_EMBED_COL_FRACS = [0.0, 0.08, 0.36, 0.47, 0.58, 0.69, 0.86, 1.0]

# 嵌套表表头（换行缩短，适配窄列）
EMBED_HEADER_LABELS: Dict[str, str] = {
    "point_id": "序号",
    "location": "检测点位置",
    "readings": "读数M\nμSv/h",
    "mean_m": "均值M̄\nμSv/h",
    "report_d": "报出值D\nμSv/h",
}


def _resolve_embed_data(host: Any, embed: Dict[str, Any]) -> Dict[str, Any]:
    key = embed.get("data_key", "field_record")
    raw = host.raw_data.get(key)
    if isinstance(raw, dict) and raw.get("points"):
        return raw
    default_rel = embed.get("default_data")
    if default_rel:
        path = default_rel if os.path.isabs(default_rel) else os.path.join(PACKAGE_DIR, default_rel)
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("points"):
                return data
    raise ValueError(
        f"嵌套现场记录表缺少数据：请在 JSON 中提供 '{key}' 或配置 embed.default_data"
    )


def build_embed_field_record_layout(
    table_left: float,
    table_right: float,
    template: Optional[FieldRecordLayout] = None,
) -> Tuple[FieldRecordLayout, List[float]]:
    """按 F.1 嵌套区域宽度生成列宽（五号字专用比例，非横版等比压缩）。"""
    tpl = template or FieldRecordLayout()
    w = table_right - table_left
    bounds = [table_left + f * w for f in _EMBED_COL_FRACS]

    loc_main = (bounds[1], bounds[1] + (bounds[2] - bounds[1]) * 0.42)
    loc_sub = (loc_main[1], bounds[2])
    if loc_sub[0] < loc_main[1] - 0.5:
        loc_sub = (loc_main[1], loc_sub[1])

    header_labels = dict(tpl.header_labels or {})
    header_labels.update(EMBED_HEADER_LABELS)

    layout = FieldRecordLayout(
        table_x_left=table_left,
        table_x_right=table_right,
        col_point_id=(bounds[0], bounds[1]),
        col_location_main=loc_main,
        col_location_sub=loc_sub,
        col_location_full=(bounds[1], bounds[2]),
        col_reading_1=(bounds[2], bounds[3]),
        col_reading_2=(bounds[3], bounds[4]),
        col_reading_3=(bounds[4], bounds[5]),
        col_mean_m=(bounds[5], bounds[6]),
        col_report_d=(bounds[6], bounds[7]),
        h_header_first=tpl.h_header_first,
        h_header_cont=tpl.h_header_cont,
        h_simple=tpl.h_simple,
        h_complex_sub=tpl.h_complex_sub,
        h_background=tpl.h_background,
        header_labels=header_labels,
        background_label=tpl.background_label,
        background_slot_labels=list(tpl.background_slot_labels),
        repeat_header_on_new_page=tpl.repeat_header_on_new_page,
        include_section_title=False,
    )
    slot_x0 = layout.col_reading_1[0]
    slot_x1 = layout.col_mean_m[0]
    bg_slot_x = [slot_x0 + (slot_x1 - slot_x0) * i / 5.0 for i in range(6)]
    return layout, bg_slot_x


class F1FieldRecordEmbedBuilder:
    """在 GbztF1TableBuilder 流式排版中绘制五号字嵌套现场记录表。"""

    def __init__(
        self,
        host: Any,
        row_def: Dict[str, Any],
        layout: FieldRecordLayout,
        data: Dict[str, Any],
        bg_slot_x: List[float],
    ) -> None:
        self.host = host
        self.row_def = row_def
        self.layout = layout
        self.data = data
        self.bg_slot_x = bg_slot_x
        self.page = host.page
        self.fonts = host.fonts
        self.y = host.y
        self.band_top = host.y
        self._id_counter = 0

    def _next_id(self, explicit: Any = None) -> str:
        if explicit is not None and str(explicit).strip():
            return str(explicit)
        self._id_counter += 1
        return str(self._id_counter)

    def _usable_bottom(self) -> float:
        return float(self.host.content_bottom) - EMBED_CELL_PAD

    def _segment_bottom(self) -> float:
        return self.y + EMBED_CELL_PAD

    def _sync_f1_table_frame(
        self,
        *,
        draw_top: bool = False,
        draw_bottom: bool = False,
    ) -> None:
        """续写 F.1 表格外框（左右边线由 page_band 在换页时绘制）。"""
        bottom = self._segment_bottom()
        if bottom <= self.band_top:
            return
        self.host._mark_band(self.band_top, bottom)
        page = self.page
        if page is None:
            return
        x_left = self.host.table_x_left
        x_right = self.host.table_x_right
        label_col = self.row_def.get("label_col", "label_main")
        lx1 = self.host.columns[label_col]["x1"]
        line_w = 0.6
        color = (0, 0, 0)
        if draw_top:
            page.draw_line(
                (x_left, self.band_top), (x_right, self.band_top), color=color, width=line_w
            )
        page.draw_line(
            (lx1, self.band_top), (lx1, bottom), color=color, width=line_w
        )
        if draw_bottom:
            page.draw_line(
                (x_left, bottom), (x_right, bottom), color=color, width=line_w
            )

    def _finish_band(self) -> None:
        bottom = self._segment_bottom()
        if self.band_top >= bottom:
            return
        self._sync_f1_table_frame(draw_bottom=True)
        self.host._draw_cell(
            self.row_def.get("label_col", "label_main"),
            self.band_top,
            bottom,
            str(self.row_def.get("label", "")),
            is_label=True,
            label_format=self.row_def.get("label_format"),
        )
        self.host._mark_band(self.band_top, bottom)

    def _page_break(self) -> None:
        self._finish_band()
        self.host._new_page(continuation=True)
        self.page = self.host.page
        self.band_top = self.host.y
        self.y = self.host.y + EMBED_CELL_PAD
        self._sync_f1_table_frame(draw_top=True)
        if self.layout.repeat_header_on_new_page:
            self._draw_header_row(continuation=True)

    def _ensure_space(self, height: float) -> None:
        if self.y + height <= self._usable_bottom():
            return
        self._page_break()

    def _col_rect(self, y0: float, y1: float, col: Tuple[float, float]) -> fitz.Rect:
        return fitz.Rect(col[0], y0, col[1], y1)

    def _col_inner_width(self, x0: float, x1: float) -> float:
        return max(8.0, x1 - x0 - 2 * CELL_PAD_X)

    def _text_row_height(
        self, text: str, x0: float, x1: float, min_height: float
    ) -> float:
        return _autofit_row_height(
            text,
            self._col_inner_width(x0, x1),
            self.fonts,
            EMBED_FONT_SIZE,
            min_height=min_height,
        )

    def _header_row_height(self, *, continuation: bool) -> float:
        L = self.layout
        h_min = L.h_header_cont if continuation else L.h_header_first
        labels = L.header_labels or EMBED_HEADER_LABELS
        cells = [
            (L.col_point_id, labels.get("point_id", "序号")),
            (L.col_location_full, labels.get("location", "检测点位置")),
            ((L.col_reading_1[0], L.col_reading_3[1]), labels.get("readings", "")),
            (L.col_mean_m, labels.get("mean_m", "")),
            (L.col_report_d, labels.get("report_d", "")),
        ]
        max_h = h_min
        for col, text in cells:
            x0, x1 = col[0], col[1]
            max_h = max(max_h, self._text_row_height(str(text), x0, x1, h_min))
        return max_h

    def _draw_header_row(self, *, continuation: bool = False) -> None:
        h = self._header_row_height(continuation=continuation)
        self._ensure_space(h)
        y0, y1 = self.y, self.y + h
        L = self.layout
        labels = L.header_labels or EMBED_HEADER_LABELS
        cells = [
            (self._col_rect(y0, y1, L.col_point_id), labels.get("point_id", "序号"), "center"),
            (
                fitz.Rect(L.col_location_full[0], y0, L.col_location_full[1], y1),
                labels.get("location", "检测点位置"),
                "center",
            ),
            (
                fitz.Rect(L.col_reading_1[0], y0, L.col_reading_3[1], y1),
                labels.get("readings", "读数M\nμSv/h"),
                "center",
            ),
            (self._col_rect(y0, y1, L.col_mean_m), labels.get("mean_m", "均值M̄\nμSv/h"), "center"),
            (self._col_rect(y0, y1, L.col_report_d), labels.get("report_d", "报出值D\nμSv/h"), "center"),
        ]
        for rect, text, align in cells:
            _draw_cell_border(self.page, rect)
            _draw_cell_content(
                self.page, rect, text, self.fonts, EMBED_FONT_SIZE, align=align
            )
        self.y = y1
        self.host.y = self.y
        self._sync_f1_table_frame()

    def _draw_measurement_cells(self, y0: float, y1: float, row: Dict[str, Any]) -> None:
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
            _draw_cell_content(
                self.page, rect, text, self.fonts, EMBED_FONT_SIZE, align=align
            )

    def _simple_row_height(self, point: Dict[str, Any]) -> float:
        L = self.layout
        loc_h = self._text_row_height(
            str(point.get("location", "")),
            L.col_location_full[0],
            L.col_location_full[1],
            L.h_simple,
        )
        return max(L.h_simple, loc_h)

    def _draw_simple_row(self, point: Dict[str, Any]) -> None:
        row_h = self._simple_row_height(point)
        self._ensure_space(row_h)
        y0, y1 = self.y, self.y + row_h
        L = self.layout
        pid = self._next_id(point.get("id"))
        id_rect = self._col_rect(y0, y1, L.col_point_id)
        loc_rect = fitz.Rect(L.col_location_full[0], y0, L.col_location_full[1], y1)
        _draw_cell_border(self.page, id_rect)
        _draw_cell_border(self.page, loc_rect)
        _draw_cell_content(self.page, id_rect, pid, self.fonts, EMBED_FONT_SIZE, align="center")
        _draw_cell_content(
            self.page,
            loc_rect,
            str(point.get("location", "")),
            self.fonts,
            EMBED_FONT_SIZE,
            align="left",
        )
        self._draw_measurement_cells(y0, y1, point)
        self.y = y1
        self.host.y = self.y
        self._sync_f1_table_frame()

    def _sub_row_height(self, sub: Dict[str, Any]) -> float:
        L = self.layout
        return self._text_row_height(
            str(sub.get("location_sub", "")),
            L.col_location_sub[0],
            L.col_location_sub[1],
            L.h_complex_sub,
        )

    def _complex_group_height(self, point: Dict[str, Any], subs: List[Dict[str, Any]]) -> float:
        L = self.layout
        main_need = self._text_row_height(
            resolve_complex_location(point),
            L.col_location_main[0],
            L.col_location_main[1],
            L.h_complex_sub,
        )
        subs_need = sum(self._sub_row_height(s) for s in subs)
        return max(subs_need, main_need)

    def _draw_complex_point(self, point: Dict[str, Any]) -> None:
        subs = point.get("sub_rows") or []
        if not subs:
            self._draw_simple_row(point)
            return
        sub_heights = [self._sub_row_height(s) for s in subs]
        total_h = self._complex_group_height(point, subs)
        self._ensure_space(total_h)
        y0 = self.y
        y1 = y0 + total_h
        L = self.layout

        pid = self._next_id(point.get("id"))
        id_rect = fitz.Rect(L.col_point_id[0], y0, L.col_point_id[1], y1)
        main_rect = fitz.Rect(L.col_location_main[0], y0, L.col_location_main[1], y1)
        _draw_cell_border(self.page, id_rect)
        _draw_cell_border(self.page, main_rect)
        _draw_cell_content(self.page, id_rect, pid, self.fonts, EMBED_FONT_SIZE, align="center")
        _draw_cell_content(
            self.page,
            main_rect,
            resolve_complex_location(point),
            self.fonts,
            EMBED_FONT_SIZE,
            align="left",
        )

        sy0 = y0
        for sub, sub_h in zip(subs, sub_heights):
            sy1 = sy0 + sub_h
            sub_rect = fitz.Rect(L.col_location_sub[0], sy0, L.col_location_sub[1], sy1)
            _draw_cell_border(self.page, sub_rect)
            _draw_cell_content(
                self.page,
                sub_rect,
                str(sub.get("location_sub", "")),
                self.fonts,
                EMBED_FONT_SIZE,
                align="left",
            )
            self._draw_measurement_cells(sy0, sy1, sub)
            sy0 = sy1
        self.y = y1
        self.host.y = self.y
        self._sync_f1_table_frame()

    def _notes_row_height(self, notes: Sequence[str]) -> float:
        text = "\n".join(str(n) for n in notes)
        inner_w = max(
            8.0, self.layout.table_x_right - self.layout.table_x_left - 2 * CELL_PAD_X
        )
        min_notes_h = _text_block_height(1, EMBED_FONT_SIZE) + 2 * CELL_PAD_Y
        return _autofit_row_height(
            text, inner_w, self.fonts, EMBED_FONT_SIZE, min_height=min_notes_h
        )

    def _background_row_height(self, bg: Dict[str, Any]) -> float:
        L = self.layout
        label = str(bg.get("label", L.background_label))
        label_h = self._text_row_height(
            label, L.table_x_left, L.col_location_full[1], L.h_background / 2.0
        )
        return max(L.h_background, label_h * 2.0)

    def _draw_background_row(self, bg: Dict[str, Any]) -> None:
        h = self._background_row_height(bg)
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
            EMBED_FONT_SIZE,
            align="center",
        )

        slots = bg.get("slots") or bg.get("values") or []
        slot_labels = L.background_slot_labels
        row_h = h / 2.0
        for i in range(10):
            row_idx = 0 if i < 5 else 1
            col_idx = i % 5
            sx0 = self.bg_slot_x[col_idx]
            sx1 = self.bg_slot_x[col_idx + 1]
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
            _draw_cell_content(
                self.page, slot_rect, text, self.fonts, EMBED_FONT_SIZE, align="center"
            )

        range_rect = fitz.Rect(L.col_report_d[0], y0, L.col_report_d[1], y1)
        _draw_cell_border(self.page, range_rect)
        _draw_cell_content(
            self.page,
            range_rect,
            str(bg.get("range", bg.get("value", ""))),
            self.fonts,
            EMBED_FONT_SIZE,
            align="center",
        )
        self.y = y1
        self.host.y = self.y
        self._sync_f1_table_frame()

    def _draw_notes_row(self, notes: Sequence[str]) -> None:
        row_h = self._notes_row_height(notes)
        self._ensure_space(row_h)
        y0, y1 = self.y, self.y + row_h
        rect = fitz.Rect(self.layout.table_x_left, y0, self.layout.table_x_right, y1)
        _draw_cell_border(self.page, rect)
        _draw_cell_content(
            self.page, rect, "\n".join(str(n) for n in notes), self.fonts, EMBED_FONT_SIZE, align="left"
        )
        self.y = y1
        self.host.y = self.y
        self._sync_f1_table_frame()

    def run(self) -> None:
        self.page = self.host.page
        self.band_top = self.host.y
        self.y = self.host.y + EMBED_CELL_PAD
        self.host.y = self.y

        self._draw_header_row(continuation=False)
        for point in self.data.get("points", []):
            if point.get("type", "simple") == "complex":
                self._draw_complex_point(point)
            else:
                self._draw_simple_row(point)

        bg = self.data.get("background")
        notes = self.data.get("notes") or []
        if bg:
            self._draw_background_row(bg)
        if notes:
            self._draw_notes_row(notes)

        self._finish_band()
        self.host.y = self._segment_bottom()


def embed_field_record_table(host: Any, row_def: Dict[str, Any], embed: Dict[str, Any]) -> None:
    """将五号字现场记录表嵌入 F.1 当前行的 value 列。"""
    value_col = row_def.get("value_col", "value_full")
    value_x0 = host.columns[value_col]["x0"] + EMBED_CELL_PAD
    value_x1 = host.columns[value_col]["x1"] - EMBED_CELL_PAD

    layout_rel = embed.get("layout", "layout/tabletest_layout.json")
    path = layout_rel if os.path.isabs(layout_rel) else os.path.join(PACKAGE_DIR, layout_rel)
    template = load_field_record_layout(path)

    fr_data = _resolve_embed_data(host, embed)
    embed_layout, bg_slot_x = build_embed_field_record_layout(value_x0, value_x1, template)

    F1FieldRecordEmbedBuilder(host, row_def, embed_layout, fr_data, bg_slot_x).run()
