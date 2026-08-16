"""
表 F.1 逻辑行定义与固定列宽（无绝对 y 坐标）。
列宽来自 gbzt.pdf 模板实测；行高由生成器按内容自适应。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

MM_TO_PT = 72.0 / 25.4

# 页面与正文（GB/T 报告排版惯例）
F1_MARGINS_MM = {"top": 26.0, "bottom": 26.0, "left": 28.0, "right": 28.0}
F1_FONT_SIZE_PT = 12.0  # 小四
F1_LINE_SPACING_PT = 25.0
F1_TITLE_GAP_PT = 6.0

# 模板实测表宽（用于按比例缩放至页边距内）
F1_OLD_TABLE_LEFT = 85.68
F1_OLD_TABLE_RIGHT = 523.82

# 固定列宽（pt），整表 x: 85.68 – 523.82（缩放前）
F1_COLUMNS: Dict[str, Dict[str, float]] = {
    "label_main": {"x0": 85.68, "x1": 173.9},
    "value_left": {"x0": 173.9, "x1": 326.05},
    # 单位名称/地址填写格延伸至 label_pair 左缘
    "value_left_to_pair": {"x0": 173.9, "x1": 387.47},
    "value_contact": {"x0": 173.9, "x1": 225.9},
    "label_phone": {"x0": 225.9, "x1": 257.75},
    "value_phone": {"x0": 257.75, "x1": 326.05},
    # 电话填写格延伸至 label_pair 左缘（传真表头）
    "value_phone_to_pair": {"x0": 257.75, "x1": 387.47},
    # 负责人/邮编/传真：靠右窄列（填写格宽 = value_contact）
    "label_pair": {"x0": 387.47, "x1": 471.82},
    "value_pair": {"x0": 471.82, "x1": 523.82},
    # 项目用途行及以下：左侧分界 x=326.05（与上方 label_pair 不对齐）
    "label_mid": {"x0": 326.05, "x1": 426.05},
    "value_right": {"x0": 426.05, "x1": 523.82},
    "value_full": {"x0": 173.9, "x1": 523.82},
    "sub_label_left": {"x0": 173.9, "x1": 254.35},
    "sub_label": {"x0": 254.35, "x1": 326.05},
    "value_device": {"x0": 326.05, "x1": 523.82},
  # 放射性同位素行：装置名称列与数值列横向合并
    "sub_value_merge": {"x0": 254.35, "x1": 523.82},
    "sub_label_row": {"x0": 173.9, "x1": 274.65},
    "value_wide": {"x0": 274.65, "x1": 523.82},
    "label_invest": {"x0": 326.05, "x1": 368.75},
    "value_invest": {"x0": 368.75, "x1": 431.55},
    "label_area": {"x0": 431.55, "x1": 467.0},
    "value_area": {"x0": 467.0, "x1": 523.82},
}

F1_ROW_MIN_HEIGHT = F1_LINE_SPACING_PT

# 表头右侧填写格：水平+垂直居中
F1_VALUE_CENTER: Dict[str, Any] = {"mode": "center"}


def scale_f1_table_to_margins(
    page_width: float,
    page_height: float,
) -> Tuple[float, float, Dict[str, Dict[str, float]], Dict[str, float]]:
    """按页边距缩放列宽，返回表左右边界、列定义及边距 pt。"""
    ml = F1_MARGINS_MM["left"] * MM_TO_PT
    mr = F1_MARGINS_MM["right"] * MM_TO_PT
    mt = F1_MARGINS_MM["top"] * MM_TO_PT
    mb = F1_MARGINS_MM["bottom"] * MM_TO_PT
    table_left = ml
    table_right = page_width - mr
    old_w = F1_OLD_TABLE_RIGHT - F1_OLD_TABLE_LEFT
    new_w = table_right - table_left
    columns: Dict[str, Dict[str, float]] = {}
    for name, col in F1_COLUMNS.items():
        columns[name] = {
            "x0": round(table_left + (col["x0"] - F1_OLD_TABLE_LEFT) / old_w * new_w, 2),
            "x1": round(table_left + (col["x1"] - F1_OLD_TABLE_LEFT) / old_w * new_w, 2),
        }
    margins_pt = {
        "top": round(mt, 2),
        "bottom": round(mb, 2),
        "left": round(ml, 2),
        "right": round(mr, 2),
    }
    return table_left, table_right, columns, margins_pt

# 单元格：role=label|value|static；value 用 field_key，static 用 text
F1_ROW_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "pairs",
        "pairs": [
            {
                "label": "单位名称",
                "label_col": "label_main",
                "value_col": "value_left_to_pair",
                "field_key": "org_name",
                "label_format": {"mode": "center"},
                "value_format": F1_VALUE_CENTER,
            },
            {
                "label": "负责人",
                "label_col": "label_pair",
                "value_col": "value_pair",
                "field_key": "principal",
                "label_format": {"mode": "slots", "slots": 4},
                "value_format": F1_VALUE_CENTER,
            },
        ],
    },
    {
        "type": "pairs",
        "pairs": [
            {
                "label": "地址",
                "label_col": "label_main",
                "value_col": "value_left_to_pair",
                "field_key": "address",
                "label_format": {"mode": "slots", "slots": 4},
                "value_format": F1_VALUE_CENTER,
            },
            {
                "label": "邮编",
                "label_col": "label_pair",
                "value_col": "value_pair",
                "field_key": "postal_code",
                "label_format": {"mode": "slots", "slots": 4},
                "value_format": F1_VALUE_CENTER,
            },
        ],
    },
    {
        "type": "pairs",
        "pairs": [
            {
                "label": "联系人",
                "label_col": "label_main",
                "value_col": "value_contact",
                "field_key": "contact",
                "label_format": {"mode": "slots", "slots": 4},
                "value_format": F1_VALUE_CENTER,
            },
            {
                "label": "电话",
                "label_col": "label_phone",
                "value_col": "value_phone_to_pair",
                "field_key": "phone",
                "value_format": F1_VALUE_CENTER,
            },
            {
                "label": "传真",
                "label_col": "label_pair",
                "value_col": "value_pair",
                "field_key": "fax",
                "label_format": {"mode": "slots", "slots": 4},
                "value_format": F1_VALUE_CENTER,
            },
        ],
    },
    {
        "type": "label_value",
        "label": "项目名称",
        "label_col": "label_main",
        "value_col": "value_full",
        "field_key": "project_name",
        "value_format": F1_VALUE_CENTER,
    },
    {
        "type": "pairs",
        "pairs": [
            {
                "label": "项目用途",
                "label_col": "label_main",
                "value_col": "value_left",
                "field_key": "project_purpose",
                "value_format": F1_VALUE_CENTER,
            },
            {
                "label": "本项目计划配备的放射工作人员数",
                "label_col": "label_mid",
                "value_col": "value_right",
                "field_key": "planned_radiation_workers",
                "label_format": {
                    "mode": "left",
                    "lines": ["本项目计划配备的", "放射工作人员数"],
                },
                "value_format": F1_VALUE_CENTER,
            },
        ],
    },
    {
        "type": "label_value",
        "label": "建设地址",
        "label_col": "label_main",
        "value_col": "value_full",
        "field_key": "construction_address",
        "value_format": F1_VALUE_CENTER,
    },
    {
        "type": "grid",
        "cells": [
            {"role": "label", "col": "label_main", "text": "建设性质"},
            {
                "role": "static",
                "col": "value_left",
                "text": "新建□扩建□改建□\n技术引进□ 技术改造□",
                "value_format": F1_VALUE_CENTER,
            },
            {"role": "label", "col": "label_invest", "text": "投资额"},
            {
                "role": "value",
                "col": "value_invest",
                "field_key": "investment",
                "value_format": F1_VALUE_CENTER,
            },
            {
                "role": "label",
                "col": "label_area",
                "text": "建设面积",
                "label_format": {"mode": "center", "lines": ["建设", "面积"]},
            },
            {
                "role": "value",
                "col": "value_area",
                "field_key": "construction_area",
                "value_format": F1_VALUE_CENTER,
            },
        ],
    },
    {
        "type": "label_embedded_table",
        "label": "辐射源项",
        "label_col": "label_main",
        "value_col": "value_full",
        "embed": {
            "kind": "content_with_tables",
            "text_key": "radiation_source_intro",
            "tables_key": "radiation_source_tables",
        },
    },
    {"type": "label_value", "label": "主要评价依据", "label_col": "label_main", "value_col": "value_full", "field_key": "evaluation_basis"},
    {
        "type": "label_embedded_table",
        "label": "评价目标",
        "label_col": "label_main",
        "value_col": "value_full",
        "embed": {
            "kind": "content_with_tables",
            "text_key": "evaluation_objective_intro",
            "tables_key": "evaluation_objective_tables",
        },
    },
    {"type": "label_value", "label": "项目概述", "label_col": "label_main", "value_col": "value_full", "field_key": "project_summary"},
    {
        "type": "label_embedded_table",
        "label": "职业病危害因素分析",
        "label_col": "label_main",
        "value_col": "value_full",
        "label_format": {"mode": "center", "lines": ["职业病", "危害因素分析"]},
        "embed": {
            "kind": "field_record",
            "data_key": "field_record",
            "default_data": "examples/tabletest_field_record_example.json",
            "layout": "layout/tabletest_layout.json",
        },
    },
    {
        "type": "label_embedded_table",
        "label": "工作场所布局",
        "label_col": "label_main",
        "value_col": "value_full",
        "label_format": {"mode": "center", "lines": ["工作场所", "布局"]},
        "embed": {
            "kind": "workplace_layout",
            "text_key": "workplace_layout_intro",
            "tables_key": "workplace_layout_tables",
        },
    },
    {
        "type": "section",
        "section_label": "防护设施和措施",
        "section_col": "label_main",
        "lines": [
            {
                "cells": [
                    {"role": "label", "col": "sub_label_row", "text": "放射防护分区"},
                    {"role": "value", "col": "value_wide", "field_key": "protection_zoning"},
                ]
            },
            {
                "cells": [
                    {"role": "label", "col": "sub_label_row", "text": "屏蔽设施"},
                    {"role": "value", "col": "value_wide", "field_key": "shielding"},
                ]
            },
            {
                "cells": [
                    {"role": "label", "col": "sub_label_row", "text": "联锁保护措施"},
                    {"role": "value", "col": "value_wide", "field_key": "interlock_protection"},
                ]
            },
            {
                "cells": [
                    {"role": "label", "col": "sub_label_row", "text": "电离辐射警示标识"},
                    {"role": "value", "col": "value_wide", "field_key": "warning_signs"},
                ]
            },
            {
                "cells": [
                    {"role": "label", "col": "sub_label_row", "text": "个人防护用品"},
                    {"role": "value", "col": "value_wide", "field_key": "personal_protective_equipment"},
                ]
            },
            {
                "cells": [
                    {"role": "label", "col": "sub_label_row", "text": "放射性废物处置"},
                    {"role": "value", "col": "value_wide", "field_key": "waste_disposal"},
                ]
            },
            {
                "cells": [
                    {"role": "label", "col": "sub_label_row", "text": "其他"},
                    {"role": "value", "col": "value_wide", "field_key": "other_measures"},
                ]
            },
        ],
    },
    {
        "type": "section",
        "section_label": "健康影响评价",
        "section_col": "label_main",
        "lines": [
            {
                "cells": [
                    {"role": "label", "col": "sub_label_row", "text": "正常情况下"},
                    {"role": "value", "col": "value_wide", "field_key": "normal_condition"},
                ]
            },
            {
                "cells": [
                    {"role": "label", "col": "sub_label_row", "text": "异常情况下"},
                    {"role": "value", "col": "value_wide", "field_key": "abnormal_condition"},
                ]
            },
        ],
    },
    {
        "type": "section",
        "section_label": "放射防护管理",
        "section_col": "label_main",
        "lines": [
            {
                "cells": [
                    {"role": "label", "col": "sub_label_row", "text": "组织机构"},
                    {"role": "value", "col": "value_wide", "field_key": "organization"},
                ]
            },
            {
                "cells": [
                    {"role": "label", "col": "sub_label_row", "text": "管理制度及措施"},
                    {"role": "value", "col": "value_wide", "field_key": "management_system"},
                ]
            },
            {
                "cells": [
                    {"role": "label", "col": "sub_label_row", "text": "放射工作人员配置与 管理"},
                    {"role": "value", "col": "value_wide", "field_key": "staff_management"},
                ]
            },
            {
                "cells": [
                    {"role": "label", "col": "sub_label_row", "text": "个人监测"},
                    {"role": "value", "col": "value_wide", "field_key": "personal_monitoring"},
                ]
            },
            {
                "cells": [
                    {"role": "label", "col": "sub_label_row", "text": "职业健康监护"},
                    {"role": "value", "col": "value_wide", "field_key": "health_surveillance"},
                ]
            },
            {
                "cells": [
                    {"role": "label", "col": "sub_label_row", "text": "放射防护培训"},
                    {"role": "value", "col": "value_wide", "field_key": "radiation_training"},
                ]
            },
        ],
    },
    {
        "type": "label_value",
        "label": "结论与建议",
        "label_col": "label_main",
        "value_col": "value_full",
        "field_key": "conclusion",
    },
]


def collect_form_fields(rows: List[Dict[str, Any]] | None = None) -> List[Dict[str, str]]:
    rows = rows or F1_ROW_DEFINITIONS
    seen: set[str] = set()
    out: List[Dict[str, str]] = []

    def add(key: str, label: str) -> None:
        if key and key not in seen:
            seen.add(key)
            out.append({"field_key": key, "label": label})

    for row in rows:
        t = row.get("type")
        if t == "pairs":
            for p in row.get("pairs", []):
                add(p.get("field_key", ""), p.get("label", ""))
        elif t == "label_value":
            add(row.get("field_key", ""), row.get("label", ""))
        elif t == "grid":
            cells = row.get("cells", [])
            for i, c in enumerate(cells):
                if c.get("role") != "value":
                    continue
                label = ""
                for j in range(i - 1, -1, -1):
                    if cells[j].get("role") == "label":
                        label = str(cells[j].get("text", ""))
                        break
                add(c.get("field_key", ""), label)
        elif t == "section":
            section_lines: List[Dict[str, Any]] = list(row.get("lines", []))
            for block in row.get("blocks", []):
                section_lines.extend(block.get("lines", []))
            for line in section_lines:
                for c in line.get("cells", []):
                    if c.get("role") == "value":
                        add(c.get("field_key", ""), c.get("text", ""))
    return out


def build_v2_layout_document(
    page_size: Dict[str, float] | None = None,
) -> Dict[str, Any]:
    ps = page_size or {"width": 595.3, "height": 841.9}
    pw, ph = float(ps["width"]), float(ps["height"])
    table_left, table_right, columns, margins_pt = scale_f1_table_to_margins(pw, ph)
    return {
        "schema": "gbzt_pre_eval_report_f1_layout/v2",
        "source_pdf": "gbzt.pdf",
        "table_id": "F.1",
        "table_title": "建设项目放射性职业病危害预评价报告表",
        "table_label": "表F.1",
        "page_size": ps,
        "orientation": "portrait",
        "margins_mm": dict(F1_MARGINS_MM),
        "margins_pt": margins_pt,
        "text": {
            "font": "simsun",
            "font_size_pt": F1_FONT_SIZE_PT,
            "line_spacing_pt": F1_LINE_SPACING_PT,
            "char_spacing": "standard",
        },
        "title": {
            "table_label": "表F.1",
            "table_name": "建设项目放射性职业病危害预评价报告表",
            "align": "center",
            "gap_below_pt": F1_TITLE_GAP_PT,
        },
        "table": {
            "x_left": round(table_left, 2),
            "x_right": round(table_right, 2),
            "columns": columns,
            "row_min_height": F1_ROW_MIN_HEIGHT,
        },
        "pagination": {
            "repeat_title_on_each_page": False,
            "repeat_row_label_on_continue": True,
            "page_number": "outer_bottom",
        },
        "rows": F1_ROW_DEFINITIONS,
        "form_fields": collect_form_fields(),
        "notes": "v2：页边距内流式排版；宋体小四、行距25磅；跨页重复行标签；页码居翻页侧。",
    }
