"""
250075YP 主表格栏目结构（相对 gbzt 调整表头名称与顺序）。

差异要点：
- 建设性质 → 项目性质
- 辐射源项之后栏目顺序与子表头按 250075YP 样例
- 防护设施子表头合并为「安全防护措施」
- 放射防护管理增加「档案管理」
- 「评价结论与建议」→「结论与建议」
"""

from __future__ import annotations

from typing import Any, Dict, List

from f1_eval_report.base.checkboxes import CONSTRUCTION_NATURE_FORMAT
from f1_eval_report.base.columns import F1_VALUE_CENTER
from f1_eval_report.sections.gbzt import (
    HALF_LABEL_FMT,
    section_attachments,
    section_evaluation_basis,
    section_evaluation_objective,
    section_hazard_analysis,
    section_org_header,
    section_project_summary,
    section_radiation_source,
    section_workplace_layout,
)


def section_project_info() -> List[Dict[str, Any]]:
    """与 gbzt 相同，仅将「建设性质」改为「项目性质」。"""
    return [
        {
            "id": "project_name",
            "spacing": "normal",
            "type": "label_value",
            "label": "项目名称",
            "label_col": "label_main",
            "value_col": "value_full",
            "field_key": "project_name",
            "value_format": F1_VALUE_CENTER,
        },
        {
            "id": "project_purpose_workers",
            "spacing": "normal",
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
                        "wrap_chars": 8,
                    },
                    "value_format": F1_VALUE_CENTER,
                },
            ],
        },
        {
            "id": "construction_address",
            "spacing": "normal",
            "type": "label_value",
            "label": "建设地址",
            "label_col": "label_main",
            "value_col": "value_full",
            "field_key": "construction_address",
            "value_format": F1_VALUE_CENTER,
        },
        {
            "id": "construction_nature",
            "spacing": "normal",
            "type": "grid",
            "cells": [
                {"role": "label", "col": "label_main", "text": "项目性质"},
                {
                    "role": "value",
                    "col": "value_left",
                    "field_key": "construction_nature",
                    "value_format": CONSTRUCTION_NATURE_FORMAT,
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
                    "label_format": {"mode": "center", "lines": ["建设", "面积"], "wrap_chars": 2},
                },
                {
                    "role": "value",
                    "col": "value_area",
                    "field_key": "construction_area",
                    "value_format": F1_VALUE_CENTER,
                },
            ],
        },
    ]


def section_project_classification() -> List[Dict[str, Any]]:
    return [
        {
            "id": "project_classification",
            "type": "label_value",
            "label": "建设项目分类",
            "label_col": "label_main_half",
            "value_col": "value_full_half",
            "label_format": HALF_LABEL_FMT,
            "field_key": "project_classification",
        }
    ]


def section_protection_measures() -> List[Dict[str, Any]]:
    """
    防护设施和措施。
    子表头：放射防护分区、屏蔽设施、安全防护措施、个人防护用品、放射性废物处置、其他。
    """
    return [
        {
            "id": "protection_zoning",
            "type": "label_embedded_table",
            "label": "防护设施和措施",
            "label_col": "label_main_half",
            "value_col": "value_full_half",
            "label_format": HALF_LABEL_FMT,
            "sub_label": "放射防护分区",
            "sub_label_col": "sub_label_row_half",
            "sub_label_format": HALF_LABEL_FMT,
            "sub_value_col": "value_wide_half",
            "embed": {
                "kind": "section_with_landscape_figure",
                "text_key": "protection_zoning",
                "tables_key": "protection_zoning_tables",
                "figure_key": "protection_zoning_figure",
                "section_label": "防护设施和措施",
                "section_label_lines": ["防护设施", "和措施"],
                "row_label": "放射防护分区",
                "label_col": "label_main_half",
                "sub_label_col": "sub_label_row_half",
                "sub_label_format": HALF_LABEL_FMT,
                "sub_value_col": "value_wide_half",
            },
        },
        {
            "id": "shielding",
            "type": "label_embedded_table",
            "label": "防护设施和措施",
            "label_col": "label_main_half",
            "value_col": "value_full_half",
            "label_format": HALF_LABEL_FMT,
            "sub_label": "屏蔽设施",
            "sub_label_col": "sub_label_row_half",
            "sub_label_format": HALF_LABEL_FMT,
            "sub_value_col": "value_wide_half",
            "embed": {
                "kind": "section_with_landscape_figure",
                "text_key": "shielding",
                "tables_key": "shielding_tables",
                "section_label": "防护设施和措施",
                "section_label_lines": ["防护设施", "和措施"],
                "row_label": "屏蔽设施",
                "label_col": "label_main_half",
                "sub_label_col": "sub_label_row_half",
                "sub_label_format": HALF_LABEL_FMT,
                "sub_value_col": "value_wide_half",
            },
        },
        {
            "id": "safety_protection",
            "type": "label_embedded_table",
            "label": "防护设施和措施",
            "label_col": "label_main_half",
            "value_col": "value_full_half",
            "label_format": HALF_LABEL_FMT,
            "sub_label": "安全防护措施",
            "sub_label_col": "sub_label_row_half",
            "sub_label_format": HALF_LABEL_FMT,
            "sub_value_col": "value_wide_half",
            "embed": {
                # 正文+表8；样式图与通风管道图经 extra_figures::safety_protection 插在本行之后
                "kind": "section_with_landscape_figure",
                "text_key": "safety_protection",
                "tables_key": "safety_protection_tables",
                "section_label": "防护设施和措施",
                "section_label_lines": ["防护设施", "和措施"],
                "row_label": "安全防护措施",
                "label_col": "label_main_half",
                "sub_label_col": "sub_label_row_half",
                "sub_label_format": HALF_LABEL_FMT,
                "sub_value_col": "value_wide_half",
            },
        },
        {
            "id": "personal_protective_equipment",
            "type": "label_embedded_table",
            "label": "防护设施和措施",
            "label_col": "label_main_half",
            "value_col": "value_full_half",
            "label_format": HALF_LABEL_FMT,
            "sub_label": "个人防护用品",
            "sub_label_col": "sub_label_row_half",
            "sub_label_format": HALF_LABEL_FMT,
            "sub_value_col": "value_wide_half",
            "embed": {
                # 导语 + 表9 + 表10 + 表后总结
                "kind": "section_with_landscape_figure",
                "text_key": "personal_protective_equipment",
                "tables_key": "personal_protective_equipment_tables",
                "section_label": "防护设施和措施",
                "section_label_lines": ["防护设施", "和措施"],
                "row_label": "个人防护用品",
                "label_col": "label_main_half",
                "sub_label_col": "sub_label_row_half",
                "sub_label_format": HALF_LABEL_FMT,
                "sub_value_col": "value_wide_half",
            },
        },
        {
            "id": "protection_trailing_lines",
            "type": "section",
            "section_label": "防护设施和措施",
            "section_col": "label_main_half",
            "section_label_format": HALF_LABEL_FMT,
            "continue_section": True,
            "lines": [
                {
                    "cells": [
                        {
                            "role": "label",
                            "col": "sub_label_row_half",
                            "text": "放射性废物处置",
                            "label_format": HALF_LABEL_FMT,
                        },
                        {
                            "role": "value",
                            "col": "value_wide_half",
                            "field_key": "waste_disposal",
                        },
                    ]
                },
                {
                    "cells": [
                        {
                            "role": "label",
                            "col": "sub_label_row_half",
                            "text": "其他",
                            "label_format": HALF_LABEL_FMT,
                        },
                        {
                            "role": "value",
                            "col": "value_wide_half",
                            "field_key": "other_measures",
                        },
                    ]
                },
            ],
        },
    ]


def section_radiation_management() -> List[Dict[str, Any]]:
    """放射防护管理：组织机构 + 管理制度(表16) + 人员(表17) + 表18～21。"""

    def _embed_row(
        rid: str,
        sub_label: str,
        text_key: str,
        tables_key: str,
    ) -> Dict[str, Any]:
        return {
            "id": rid,
            "type": "label_embedded_table",
            "label": "放射防护管理",
            "label_col": "label_main_half",
            "value_col": "value_full_half",
            "label_format": HALF_LABEL_FMT,
            "sub_label": sub_label,
            "sub_label_col": "sub_label_row_half",
            "sub_label_format": HALF_LABEL_FMT,
            "sub_value_col": "value_wide_half",
            "embed": {
                "kind": "section_with_landscape_figure",
                "text_key": text_key,
                "tables_key": tables_key,
                "section_label": "放射防护管理",
                "section_label_lines": ["放射防护", "管理"],
                "row_label": sub_label,
                "label_col": "label_main_half",
                "sub_label_col": "sub_label_row_half",
                "sub_label_format": HALF_LABEL_FMT,
                "sub_value_col": "value_wide_half",
            },
        }

    return [
        {
            "id": "organization",
            "type": "section",
            "section_label": "放射防护管理",
            "section_col": "label_main_half",
            "section_label_format": HALF_LABEL_FMT,
            "lines": [
                {
                    "cells": [
                        {
                            "role": "label",
                            "col": "sub_label_row_half",
                            "text": "组织机构",
                            "label_format": HALF_LABEL_FMT,
                        },
                        {
                            "role": "value",
                            "col": "value_wide_half",
                            "field_key": "organization",
                        },
                    ]
                },
            ],
        },
        _embed_row(
            "management_system",
            "管理制度及措施",
            "management_system",
            "management_system_tables",
        ),
        _embed_row(
            "staff_management",
            "放射工作人员配置与管理",
            "staff_management",
            "staff_management_tables",
        ),
        _embed_row(
            "personal_monitoring",
            "个人监测",
            "personal_monitoring",
            "personal_monitoring_tables",
        ),
        _embed_row(
            "health_surveillance",
            "职业健康监护",
            "health_surveillance",
            "health_surveillance_tables",
        ),
        _embed_row(
            "radiation_training",
            "放射防护培训",
            "radiation_training",
            "radiation_training_tables",
        ),
        _embed_row(
            "archive_management",
            "档案管理",
            "archive_management",
            "archive_management_tables",
        ),
    ]


def section_health_impact() -> List[Dict[str, Any]]:
    """健康影响评价：正常情况下（正文+表11～15）；异常情况下（纯文本）。"""
    return [
        {
            "id": "normal_condition",
            "type": "label_embedded_table",
            "label": "健康影响评价",
            "label_col": "label_main_half",
            "value_col": "value_full_half",
            "label_format": HALF_LABEL_FMT,
            "sub_label": "正常情况下",
            "sub_label_col": "sub_label_row_half",
            "sub_label_format": HALF_LABEL_FMT,
            "sub_value_col": "value_wide_half",
            "embed": {
                "kind": "section_with_landscape_figure",
                "text_key": "normal_condition",
                "tables_key": "normal_condition_tables",
                "section_label": "健康影响评价",
                "section_label_lines": ["健康影响", "评价"],
                "row_label": "正常情况下",
                "label_col": "label_main_half",
                "sub_label_col": "sub_label_row_half",
                "sub_label_format": HALF_LABEL_FMT,
                "sub_value_col": "value_wide_half",
            },
        },
        {
            "id": "health_impact_trailing",
            "type": "section",
            "section_label": "健康影响评价",
            "section_col": "label_main_half",
            "section_label_format": HALF_LABEL_FMT,
            "continue_section": True,
            "lines": [
                {
                    "cells": [
                        {
                            "role": "label",
                            "col": "sub_label_row_half",
                            "text": "异常情况下",
                            "label_format": HALF_LABEL_FMT,
                        },
                        {
                            "role": "value",
                            "col": "value_wide_half",
                            "field_key": "abnormal_condition",
                        },
                    ]
                },
            ],
        },
    ]


def section_conclusion() -> List[Dict[str, Any]]:
    return [
        {
            "id": "conclusion",
            "type": "label_value",
            "label": "结论与建议",
            "label_col": "label_main_half",
            "value_col": "value_full_half",
            "field_key": "conclusion",
            "label_format": HALF_LABEL_FMT,
        }
    ]


# 辐射源项之后：项目概述 → … → 附件
SECTION_BUILDERS = [
    ("org_header", section_org_header),
    ("project_info", section_project_info),
    ("radiation_source", section_radiation_source),
    ("project_summary", section_project_summary),
    ("project_classification", section_project_classification),
    ("evaluation_basis", section_evaluation_basis),
    ("evaluation_objective", section_evaluation_objective),
    ("hazard_analysis", section_hazard_analysis),
    ("workplace_layout", section_workplace_layout),
    ("protection_measures", section_protection_measures),
    ("health_impact", section_health_impact),
    ("radiation_management", section_radiation_management),
    ("conclusion", section_conclusion),
    ("attachments", section_attachments),
]


def all_row_definitions() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for _name, builder in SECTION_BUILDERS:
        rows.extend(builder())
    return rows
