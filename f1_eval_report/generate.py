"""
f1_eval_report 生成入口：基础格式 + 表头内容组装后生成 PDF。
复用 radiation_detection_report 的绘制引擎，扩展 section_with_landscape_figure。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from radiation_detection_report.gbzt_f1_generator import (
    GbztF1TableBuilder,
    load_f1_data,
)
from radiation_detection_report.gbzt_f1_content_embed import embed_content_with_tables
from radiation_detection_report.gbzt_f1_field_record_embed import embed_field_record_table
from radiation_detection_report.gbzt_f1_generic_table_embed import embed_generic_table

from f1_eval_report.assemble import assemble_layout, load_base_format
from f1_eval_report.embed.landscape import embed_section_with_landscape_figure

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def _draw_row_def_patched(self: GbztF1TableBuilder, row_def: Dict[str, Any]) -> None:
    prev_ls, prev_min = self._apply_row_spacing(row_def)
    try:
        t = row_def.get("type")
        if t == "pairs":
            self._draw_pairs(row_def)
        elif t == "label_value":
            self._draw_label_value(row_def)
        elif t == "label_embedded_table":
            embed = row_def.get("embed") or {}
            kind = embed.get("kind")
            if kind == "field_record":
                if embed.get("optional"):
                    key = embed.get("data_key", "field_record")
                    raw = self.raw_data.get(key)
                    has = isinstance(raw, dict) and raw.get("points")
                    default = embed.get("default_data")
                    if not has and not default:
                        # 无现场记录数据时退化为空行
                        empty = {
                            "type": "label_value",
                            "label": row_def.get("label", ""),
                            "label_col": row_def.get("label_col", "label_main"),
                            "value_col": row_def.get("value_col", "value_full"),
                            "field_key": "",
                            "label_format": row_def.get("label_format"),
                        }
                        self._draw_label_value(empty)
                        return
                embed_field_record_table(self, row_def, embed)
            elif kind == "generic_table":
                embed_generic_table(self, row_def, embed)
            elif kind == "content_with_tables":
                embed_content_with_tables(self, row_def, embed)
            elif kind == "section_with_landscape_figure":
                embed_section_with_landscape_figure(self, row_def, embed)
            elif kind == "workplace_layout":
                # 兼容旧 kind：转为 content + 可选 figure
                from f1_eval_report.embed.landscape import insert_landscape_figure_page

                merged = dict(embed)
                merged.setdefault("text_key", "workplace_layout_intro")
                merged.setdefault("tables_key", "workplace_layout_tables")
                embed_content_with_tables(self, row_def, merged)
                fig = self.raw_data.get(embed.get("figure_key") or "workplace_layout_figure")
                if isinstance(fig, dict) and fig.get("image"):
                    insert_landscape_figure_page(self, fig)
            else:
                raise ValueError(f"未知嵌套表格类型: {kind}")
        elif t == "standalone_figure_page":
            from f1_eval_report.base.page_modes import apply_page_mode_to_figure
            from f1_eval_report.embed.landscape import insert_landscape_figure_page

            fig = row_def.get("figure")
            if isinstance(fig, dict) and fig.get("image"):
                fig2 = apply_page_mode_to_figure(dict(fig))
                sec = str(row_def.get("section_label") or "").strip()
                row_lab = str(row_def.get("row_label") or "").strip()
                if sec or row_lab:
                    fig2["draw_side_labels"] = True
                insert_landscape_figure_page(
                    self,
                    fig2,
                    section_label=sec,
                    section_label_lines=row_def.get("section_label_lines"),
                    row_label=row_lab,
                    label_col=str(row_def.get("label_col") or "label_main_half"),
                    sub_label_col=str(row_def.get("sub_label_col") or "sub_label_row_half"),
                    sub_value_col=str(row_def.get("sub_value_col") or "value_wide_half"),
                    sub_label_format=row_def.get("sub_label_format"),
                )
        elif t == "standalone_multi_figure_page":
            from f1_eval_report.base.page_modes import apply_page_mode_to_figure
            from f1_eval_report.embed.landscape import insert_stacked_a4_figures_page

            figs_raw = row_def.get("figures") or []
            figs = []
            for f in figs_raw:
                if isinstance(f, dict) and f.get("image"):
                    figs.append(apply_page_mode_to_figure(dict(f)))
            if figs:
                insert_stacked_a4_figures_page(
                    self,
                    figs,
                    section_label=str(row_def.get("section_label") or "").strip(),
                    section_label_lines=row_def.get("section_label_lines"),
                    row_label=str(row_def.get("row_label") or "").strip(),
                    label_col=str(row_def.get("label_col") or "label_main_half"),
                    sub_label_col=str(row_def.get("sub_label_col") or "sub_label_row_half"),
                    sub_value_col=str(row_def.get("sub_value_col") or "value_wide_half"),
                    sub_label_format=row_def.get("sub_label_format"),
                )
        elif t == "grid":
            self._draw_grid(row_def)
        elif t == "section":
            self._draw_section(row_def)
    finally:
        self.line_spacing = prev_ls
        self.row_min_h = prev_min


def _normalize_fields_for_template(
    data: Dict[str, Any], table_template: str
) -> Dict[str, Any]:
    """按主表格模板补齐字段别名（不改动调用方传入的原 dict 语义外必要项）。"""
    out = dict(data)
    fields = dict(out.get("fields") or {})
    if table_template == "250075YP":
        if not str(fields.get("safety_protection") or "").strip():
            parts = [
                str(fields.get("interlock_protection") or "").strip(),
                str(fields.get("warning_signs") or "").strip(),
            ]
            merged = "\n".join(p for p in parts if p)
            if merged:
                fields["safety_protection"] = merged
        if not str(fields.get("archive_management") or "").strip():
            fields["archive_management"] = (
                "建立放射卫生管理、人员剂量、职业健康监护、培训及监测等档案，按要求归档保存。"
            )
        if not str(fields.get("project_classification") or "").strip():
            fields["project_classification"] = (
                "本项目涉及的射线装置主要为Ⅱ类；本项目属于危害一般类放射诊疗建设项目。"
            )
    out["fields"] = fields
    try:
        from f1_eval_report.templates.loader import (
            apply_evaluation_basis_default,
            assemble_evaluation_objective,
        )

        apply_evaluation_basis_default(out, force=False)
        if not str(out.get("evaluation_objective_intro") or "").strip():
            obj = assemble_evaluation_objective()
            out["evaluation_objective_intro"] = obj.get("evaluation_objective_intro") or ""
            if not out.get("evaluation_objective_tables"):
                out["evaluation_objective_tables"] = obj.get("evaluation_objective_tables") or []
        if not str(fields.get("hazard_analysis") or "").strip():
            from f1_eval_report.templates.hazard_analysis import assemble_hazard_analysis
            from f1_eval_report.templates.shielding_summary import devices_from_report_data

            devices = devices_from_report_data(out)
            fields["hazard_analysis"] = assemble_hazard_analysis(devices or None)
            out["fields"] = fields
    except Exception:
        pass
    return out


def _rows_with_section_figure_pages(
    template_id: str,
    values: Dict[str, Any],
    rows: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    在对应行之后插入独立附图页（A3/A4 横竖可配）。

    附图键 ``extra_figures::{row_id}`` 挂在同 id 行之后
   （如 workplace_layout、safety_protection）。
    传入的 rows（含标签覆盖）也会注入附图。
    """
    from f1_eval_report.base.page_modes import apply_page_mode_to_figure
    from f1_eval_report.sections.catalog import get_section_builders

    if rows is not None:
        base = list(rows)
    else:
        base = []
        for _sid, builder in get_section_builders(template_id):
            base.extend(builder())

    fig_map: Dict[str, List[Any]] = {}
    for key, figs in values.items():
        if not str(key).startswith("extra_figures::") or not isinstance(figs, list):
            continue
        anchor = str(key)[len("extra_figures::") :]
        if anchor:
            fig_map[anchor] = figs

    def _anchor_labels(row: Dict[str, Any]) -> Dict[str, Any]:
        embed = row.get("embed") if isinstance(row.get("embed"), dict) else {}
        section_label = str(
            embed.get("section_label") or row.get("label") or row.get("section_label") or ""
        ).strip()
        row_label = str(
            embed.get("row_label") or row.get("sub_label") or ""
        ).strip()
        lines = embed.get("section_label_lines")
        if not isinstance(lines, list) or not lines:
            lines = None
        return {
            "section_label": section_label,
            "section_label_lines": lines,
            "row_label": row_label,
            "label_col": str(
                embed.get("label_col") or row.get("label_col") or "label_main_half"
            ),
            "sub_label_col": str(
                embed.get("sub_label_col")
                or row.get("sub_label_col")
                or "sub_label_row_half"
            ),
            "sub_value_col": str(
                embed.get("sub_value_col")
                or row.get("sub_value_col")
                or "value_wide_half"
            ),
            "sub_label_format": embed.get("sub_label_format")
            or row.get("sub_label_format"),
        }

    out: List[Dict[str, Any]] = []
    for row in base:
        out.append(row)
        rid = str(row.get("id") or "")
        if not rid:
            continue
        labels = _anchor_labels(row)
        # 样式图（警告标志/灯箱/告知栏）：按 style_figure_id 识别并左右合页；
        # 切勿用「图13/14/15」判断——顺序编号后会误伤平面布局图。
        pending_style: List[Any] = []

        def _flush_style() -> None:
            nonlocal pending_style
            if not pending_style:
                return
            item = {
                "id": f"standalone_multi::{rid}::{len(out)}",
                "type": "standalone_multi_figure_page",
                "figures": pending_style,
            }
            item.update(labels)
            out.append(item)
            pending_style = []

        def _is_style_pack_figure(fig: Dict[str, Any]) -> bool:
            sid = str(fig.get("style_figure_id") or "").strip()
            if sid in ("warning_sign", "lightbox_text", "notice_board"):
                return True
            if fig.get("pack_group") == "style" and sid:
                return True
            return False

        for i, fig in enumerate(fig_map.get(rid) or []):
            if not isinstance(fig, dict) or not fig.get("image"):
                continue
            fig2 = apply_page_mode_to_figure(fig)
            if _is_style_pack_figure(fig2):
                pending_style.append(fig2)
                continue
            _flush_style()
            item = {
                "id": f"standalone_page::{rid}::{i}",
                "type": "standalone_figure_page",
                "figure": fig2,
            }
            item.update(labels)
            out.append(item)
        _flush_style()
    return out


def generate_f1_eval_pdf(
    output_pdf: str,
    data_path: Optional[str] = None,
    *,
    base_format_path: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
    table_template: Optional[str] = None,
    rows: Optional[List[Dict[str, Any]]] = None,
) -> None:
    values = data if data is not None else load_f1_data(data_path)
    # 先解析模板再注入插页行
    from f1_eval_report.sections.catalog import resolve_table_template

    template_id = resolve_table_template(table_template)
    values = _normalize_fields_for_template(values, template_id)
    row_defs = _rows_with_section_figure_pages(template_id, values, rows)
    layout = assemble_layout(
        base_format_path=base_format_path,
        table_template=template_id,
        rows=row_defs,
    )
    builder = GbztF1TableBuilder(layout, values)
    builder._draw_row_def = _draw_row_def_patched.__get__(builder, GbztF1TableBuilder)  # type: ignore[method-assign]
    _apply_field_styles(builder, values.get("field_styles") if isinstance(values.get("field_styles"), dict) else {})
    builder.save(output_pdf)


def _apply_field_styles(builder: GbztF1TableBuilder, styles: Dict[str, Any]) -> None:
    """按 field_styles 覆盖字号 / 首行缩进（与编辑页一致，支持逐段缩进）。"""
    if not styles:
        return
    from radiation_detection_report.f1_paragraph_layout import f1_paragraph_line_entries

    orig_lv = builder._draw_label_value
    orig_entries = builder._line_entries_for_text
    orig_entries_value = builder._line_entries_for_value

    def _draw_label_value(row_def: Dict[str, Any]) -> None:
        fk = str(row_def.get("field_key") or "")
        st = styles.get(fk) if fk and isinstance(styles.get(fk), dict) else None
        if not st:
            orig_lv(row_def)
            return
        # 必须基于「当前行」已应用的行距（如 spacing=normal），不要用全文 25 磅回推
        prev_fs, prev_ls = builder.font_size, builder.line_spacing
        try:
            if st.get("font_size_pt") is not None:
                try:
                    fs = max(8.0, min(24.0, float(st.get("font_size_pt"))))
                    builder.font_size = fs
                    builder.line_spacing = (
                        fs * (prev_ls / prev_fs) if prev_fs else fs * 1.35
                    )
                except (TypeError, ValueError):
                    pass
            want_indent = bool(st.get("first_indent", True))
            para_indents = st.get("para_indents") if isinstance(st.get("para_indents"), list) else None
            # 居中短栏（项目名称/地址等）不做正文缩进折行
            value_format = row_def.get("value_format") or {}
            center_plain = value_format.get("mode") == "center"

            def _entries(
                text: str,
                col: str,
                value_format: Optional[Dict[str, Any]] = None,
                **_kwargs: Any,
            ):
                max_w = max(8.0, builder._col_inner_width(col))
                fmt = value_format or {}
                if center_plain or fmt.get("mode") == "center":
                    from radiation_detection_report.f1_paragraph_layout import wrap_plain_lines

                    return wrap_plain_lines(str(text), max_w, builder.fonts, builder.font_size)
                return f1_paragraph_line_entries(
                    str(text),
                    max_w,
                    builder.fonts,
                    builder.font_size,
                    first_indent=want_indent,
                    para_indents=para_indents,
                )

            # _draw_label_value 实际调用的是 _line_entries_for_value（不是 _line_entries_for_text）
            builder._line_entries_for_value = _entries  # type: ignore[method-assign]
            builder._line_entries_for_text = lambda text, col: _entries(text, col)  # type: ignore[method-assign]
            orig_lv(row_def)
        finally:
            builder.font_size = prev_fs
            builder.line_spacing = prev_ls
            builder._line_entries_for_value = orig_entries_value  # type: ignore[method-assign]
            builder._line_entries_for_text = orig_entries  # type: ignore[method-assign]

    builder._draw_label_value = _draw_label_value  # type: ignore[method-assign]


def prepare_250375_data(src_path: str) -> Dict[str, Any]:
    """将旧 gbzt_f1_250375.json 适配为本包数据约定。"""
    with open(src_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    data = dict(raw)
    fields = dict(data.get("fields") or {})

    # 放射防护分区正文：若 fields 无，用默认说明
    if not fields.get("protection_zoning"):
        fields["protection_zoning"] = (
            "本项目X射线设备机房工作场所放射防护分级设计情况及评价见表6，"
            "本项目控制区和监督区划分明确且合理，符合标准的相关要求。"
        )
    data["fields"] = fields
    data["protection_zoning"] = fields["protection_zoning"]

    # 附图从工作场所布局挪到放射防护分区
    fig = data.pop("workplace_layout_figure", None) or data.get("protection_zoning_figure")
    if isinstance(fig, dict):
        # 解析为相对本包 examples 或绝对路径
        img = str(fig.get("image", ""))
        if img and not os.path.isabs(img):
            cand = [
                os.path.join(PACKAGE_DIR, "examples", img),
                os.path.join(
                    os.path.dirname(PACKAGE_DIR),
                    "radiation_detection",
                    "examples",
                    img,
                ),
            ]
            for p in cand:
                if os.path.isfile(p):
                    fig = dict(fig)
                    fig["image"] = p
                    break
        if not fig.get("caption"):
            fig = dict(fig)
            fig["caption"] = "综合办公大楼地下一层1间DSA机房平面布局和放射分区图"
        data["protection_zoning_figure"] = fig

    return data
