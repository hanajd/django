"""
在 F.1 单元格内流式排版正文段落，并嵌入一个或多个 generic 表格。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from radiation_detection_report.f1_paragraph_layout import f1_paragraph_line_entries
from radiation_detection_report.generator import (
    CELL_PAD_X,
    CELL_PAD_Y,
    _text_ascent,
)
from radiation_detection_report.gbzt_f1_generic_table_embed import F1GenericTableEmbedBuilder

EMBED_CELL_PAD = CELL_PAD_X
# 正文段落距上表格线（pt）；左右仍用 EMBED_CELL_PAD
CONTENT_TOP_PAD_PT = 10.0
F1_TABLE_MARK = "<<<F1_TABLE>>>"
F1_AFTER_TABLES_MARK = "<<<F1_AFTER_TABLES>>>"


def _strip_table_marks(text: str) -> str:
    """正文绘制前去掉插表标记，防止标记泄漏到 PDF。"""
    s = str(text or "")
    s = s.replace(F1_TABLE_MARK, "")
    s = s.replace(F1_AFTER_TABLES_MARK, "")
    return s


def _f1_text_descent(font_size: float) -> float:
    return font_size * 0.22


def _f1_text_block_height(n_lines: int, font_size: float, line_spacing: float) -> float:
    if n_lines <= 0:
        return 0.0
    return (n_lines - 1) * line_spacing + _text_ascent(font_size) + _f1_text_descent(font_size)


def _resolve_tables(host: Any, embed: Dict[str, Any]) -> List[Dict[str, Any]]:
    key = embed.get("tables_key")
    if key:
        raw = host.raw_data.get(key)
        if isinstance(raw, list):
            return [t for t in raw if isinstance(t, dict) and t.get("rows")]
    single = embed.get("table_key")
    if single:
        one = host.raw_data.get(single)
        if isinstance(one, dict) and one.get("rows"):
            return [one]
    return []


def _content_flow_plan(raw: str) -> List[Dict[str, Any]]:
    """
    将正文字符串拆成流式片段：
    - {"kind": "text", "text": "..."}
    - {"kind": "table"}  插入下一张表
    - {"kind": "tables"} 插入剩余全部表（兼容旧标记 <<<F1_AFTER_TABLES>>>）
    """
    text = str(raw or "")
    if F1_TABLE_MARK in text:
        parts = text.split(F1_TABLE_MARK)
        plan: List[Dict[str, Any]] = []
        for i, part in enumerate(parts):
            chunk = part.strip("\n")
            # 兼容同一段里仍带 AFTER 标记
            if F1_AFTER_TABLES_MARK in chunk:
                before, after = chunk.split(F1_AFTER_TABLES_MARK, 1)
                before = before.strip("\n")
                after = after.lstrip("\n")
                if before.strip():
                    plan.append({"kind": "text", "text": before})
                plan.append({"kind": "tables"})
                if after.strip():
                    plan.append({"kind": "text", "text": after})
            elif chunk.strip():
                plan.append({"kind": "text", "text": chunk})
            if i < len(parts) - 1:
                plan.append({"kind": "table"})
        return plan
    if F1_AFTER_TABLES_MARK in text:
        before, after = text.split(F1_AFTER_TABLES_MARK, 1)
        plan = []
        if before.strip("\n").strip():
            plan.append({"kind": "text", "text": before.strip("\n")})
        plan.append({"kind": "tables"})
        if after.lstrip("\n").strip():
            plan.append({"kind": "text", "text": after.lstrip("\n")})
        return plan
    return [{"kind": "text", "text": text}] if text else []


class F1ContentEmbedBuilder:
    def __init__(
        self,
        host: Any,
        row_def: Dict[str, Any],
        embed: Dict[str, Any],
    ) -> None:
        self.host = host
        self.row_def = row_def
        self.embed = embed
        self.page = host.page
        self.fonts = host.fonts
        self.y = host.y
        self.band_top = host.y
        text_key = embed.get("text_key", "")
        if text_key:
            raw = str(host.values.get(text_key, "") or "")
            if not raw.strip():
                raw = str(host.raw_data.get(text_key, "") or "")
            self._flow_plan = _content_flow_plan(raw)
            # 兼容旧属性：无交错标记时 before=全文、after 空；有 AFTER 时拆开
            if F1_TABLE_MARK not in raw and F1_AFTER_TABLES_MARK in raw:
                before, after = raw.split(F1_AFTER_TABLES_MARK, 1)
                self.text = before.strip("\n")
                self.text_after = after.lstrip("\n")
            else:
                self.text = raw
                self.text_after = ""
        else:
            self._flow_plan = []
            self.text = ""
            self.text_after = ""
        self._indent_part = "before"

    def _field_style(self) -> Dict[str, Any]:
        fk = str(self.embed.get("text_key") or "")
        raw = getattr(self.host, "raw_data", None) or {}
        styles = raw.get("field_styles") if isinstance(raw.get("field_styles"), dict) else {}
        st = styles.get(fk) if fk and isinstance(styles.get(fk), dict) else {}
        return st if isinstance(st, dict) else {}

    def _usable_bottom(self) -> float:
        return float(self.host.content_bottom) - EMBED_CELL_PAD

    def _segment_bottom(self) -> float:
        return self.y + EMBED_CELL_PAD

    def _value_col(self) -> str:
        return self.row_def.get("value_col", "value_full")

    def _sync_f1_table_frame(
        self,
        *,
        draw_top: bool = False,
        draw_bottom: bool = False,
    ) -> None:
        bottom = self._segment_bottom()
        if bottom <= self.band_top:
            return
        self.host._mark_band(self.band_top, bottom)
        self.page = self.host.page
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
        page.draw_line((lx1, self.band_top), (lx1, bottom), color=color, width=line_w)
        if draw_bottom:
            page.draw_line((x_left, bottom), (x_right, bottom), color=color, width=line_w)

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
        self.y = self.host.y + CONTENT_TOP_PAD_PT
        self._sync_f1_table_frame(draw_top=True)

    def _line_entries(self):
        value_col = self._value_col()
        inner_w = self.host._col_inner_width(value_col)
        st = self._field_style()
        want_indent = bool(st.get("first_indent", True))
        part = getattr(self, "_indent_part", "before")
        if part == "after":
            para_indents = st.get("para_indents_after")
        else:
            para_indents = st.get("para_indents_before")
            if not isinstance(para_indents, list):
                para_indents = st.get("para_indents")
        if not isinstance(para_indents, list):
            para_indents = None
        return f1_paragraph_line_entries(
            self.text,
            inner_w,
            self.fonts,
            self.host.font_size,
            first_indent=want_indent,
            para_indents=para_indents,
        )

    def _height_for_lines(self, n: int) -> float:
        if n <= 0:
            return self.host.row_min_h
        return max(
            self.host.row_min_h,
            _f1_text_block_height(n, self.host.font_size, self.host.line_spacing) + 2 * CELL_PAD_Y,
        )

    def _max_lines_fit(self, available: float) -> int:
        for n in range(10000, 0, -1):
            if self._height_for_lines(n) <= available:
                return n
        return 0

    def _draw_text_chunk(self, entries, chunk_h: float) -> None:
        value_col = self._value_col()
        y0, y1 = self.y, self.y + chunk_h
        value_rect = self.host._col_rect(value_col, y0, y1)
        self.host._draw_cell_content_entries(
            value_rect, entries, align="left", vertical_center=False
        )
        self.y = y1
        self.host.y = self.y
        self._sync_f1_table_frame()

    def _flow_text(self) -> None:
        self.text = _strip_table_marks(self.text)
        if not self.text.strip():
            return
        entries = self._line_entries()
        idx = 0
        while idx < len(entries):
            available = self._usable_bottom() - self.y
            if available < self.host.row_min_h:
                self._page_break()
                continue
            n = min(len(entries) - idx, self._max_lines_fit(available))
            if n <= 0:
                self._page_break()
                continue
            chunk = entries[idx:idx + n]
            idx += n
            chunk_h = self._height_for_lines(len(chunk))
            self.host._ensure_space(chunk_h)
            self._draw_text_chunk(chunk, chunk_h)

    def _draw_one_table(self, table_data: Dict[str, Any]) -> None:
        sub_embed = dict(self.embed)
        sub_embed["defer_finish"] = True
        self.host.y = self.y
        builder = F1GenericTableEmbedBuilder(
            self.host, self.row_def, table_data, sub_embed
        )
        builder.run()
        self.y = self.host.y
        self.page = self.host.page

    def _draw_tables(self, tables: Optional[List[Dict[str, Any]]] = None) -> None:
        if tables is None:
            tables = _resolve_tables(self.host, self.embed)
        self.host._content_embed_parent = self
        try:
            for table_data in tables:
                self._draw_one_table(table_data)
        finally:
            self.host._content_embed_parent = None

    def _draw_empty_placeholder(self) -> None:
        """无正文、无嵌套表时按普通 label_value 空行绘制，避免画出多余嵌套框线。"""
        label = str(self.row_def.get("label", ""))
        label_col = self.row_def.get("label_col", "label_main")
        value_col = self._value_col()
        label_format = self.row_def.get("label_format")
        h = max(
            self.host._label_text_height(label, label_col, label_format),
            self.host.row_min_h,
        )
        self.host._ensure_space(h)
        y0 = self.host.y
        self.host._draw_cell(
            label_col, y0, y0 + h, label, is_label=True, label_format=label_format
        )
        self.host._draw_cell(value_col, y0, y0 + h, "", is_label=False)
        self.host._advance(h)

    def _run_flow_plan(self, tables: List[Dict[str, Any]]) -> None:
        """按片段顺序排版正文与表；未消费完的表追加在末尾。"""
        ti = 0
        text_i = 0
        for step in getattr(self, "_flow_plan", None) or []:
            kind = step.get("kind")
            if kind == "text":
                chunk = _strip_table_marks(str(step.get("text") or ""))
                if not chunk.strip():
                    continue
                self._indent_part = "before" if text_i == 0 else "after"
                text_i += 1
                self.text = chunk
                self._flow_text()
            elif kind == "table":
                if ti < len(tables):
                    self.host._content_embed_parent = self
                    try:
                        self._draw_one_table(tables[ti])
                    finally:
                        self.host._content_embed_parent = None
                    ti += 1
            elif kind == "tables":
                if ti < len(tables):
                    self._draw_tables(tables[ti:])
                    ti = len(tables)
        if ti < len(tables):
            self._draw_tables(tables[ti:])

    def run(self) -> None:
        tables = _resolve_tables(self.host, self.embed)
        figure_key = self.embed.get("figure_key")
        has_figure = bool(
            figure_key and isinstance(self.host.raw_data.get(figure_key), dict)
        )
        plan = getattr(self, "_flow_plan", None) or []
        has_plan_text = any(
            s.get("kind") == "text" and str(s.get("text") or "").strip() for s in plan
        )
        if (
            not has_plan_text
            and not self.text.strip()
            and not str(getattr(self, "text_after", "") or "").strip()
            and not tables
            and not has_figure
        ):
            self._draw_empty_placeholder()
            return

        self.page = self.host.page
        self.band_top = self.host.y
        self.y = self.host.y + CONTENT_TOP_PAD_PT
        self.host.y = self.y
        # 段带起始顶线（A3 附图后接正文时尤其需要，否则缺上表格线）
        self._sync_f1_table_frame(draw_top=True)

        # 有单表/多表插入点，或旧 AFTER 标记：走统一流式计划
        if any(s.get("kind") in ("table", "tables") for s in plan):
            self._run_flow_plan(tables)
        else:
            self._indent_part = "before"
            self._flow_text()
            self._draw_tables(tables)
            after = getattr(self, "text_after", "") or ""
            if str(after).strip():
                self._indent_part = "after"
                self.text = str(after)
                self._flow_text()
        if has_figure:
            from radiation_detection_report.gbzt_f1_workplace_layout_embed import (
                insert_landscape_figure_page,
            )

            self._finish_band()
            insert_landscape_figure_page(self.host, self.host.raw_data[figure_key])
            self.page = self.host.page
            self.band_top = self.host.y
            self.y = self.host.y + CONTENT_TOP_PAD_PT
            self.host.y = self.y
            self._sync_f1_table_frame(draw_top=True)
        else:
            self._finish_band()
        self.host.y = self._segment_bottom()


def embed_content_with_tables(
    host: Any, row_def: Dict[str, Any], embed: Dict[str, Any]
) -> None:
    F1ContentEmbedBuilder(host, row_def, embed).run()
