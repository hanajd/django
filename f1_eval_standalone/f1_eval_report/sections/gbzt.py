"""GBZ/T 表 F.1 标准主表格栏目结构（当前格式快照，模板名：gbzt）。"""

from __future__ import annotations

from typing import Any, Dict, List

from f1_eval_report.base.checkboxes import CONSTRUCTION_NATURE_FORMAT
from f1_eval_report.base.columns import F1_VALUE_CENTER

# 自「辐射源项」起半宽表头：每两个字换行，便于窄列排版
HALF_LABEL_FMT: Dict[str, Any] = {"mode": "center", "wrap_chars": 2}


def section_org_header() -> List[Dict[str, Any]]:
    """单位名称 / 负责人 / 地址 / 邮编 / 联系人 / 电话 / 传真。"""
    return [
        {
            "id": "org_name_principal",
            "spacing": "normal",
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
            "id": "address_postal",
            "spacing": "normal",
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
            "id": "contact_phone_fax",
            "spacing": "normal",
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
    ]


def section_project_info() -> List[Dict[str, Any]]:
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
                {"role": "label", "col": "label_main", "text": "建设性质"},
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


def section_radiation_source() -> List[Dict[str, Any]]:
    return [
        {
            "id": "radiation_source",
            "type": "label_embedded_table",
            "label": "辐射源项",
            "label_col": "label_main_half",
            "value_col": "value_full_half",
            "label_format": HALF_LABEL_FMT,
            "embed": {
                "kind": "content_with_tables",
                "text_key": "radiation_source_intro",
                "tables_key": "radiation_source_tables",
            },
        }
    ]


def section_evaluation_basis() -> List[Dict[str, Any]]:
    return [
        {
            "id": "evaluation_basis",
            "type": "label_value",
            "label": "主要评价依据",
            "label_col": "label_main_half",
            "value_col": "value_full_half",
            "field_key": "evaluation_basis",
            "label_format": HALF_LABEL_FMT,
        }
    ]


def section_evaluation_objective() -> List[Dict[str, Any]]:
    return [
        {
            "id": "evaluation_objective",
            "type": "label_embedded_table",
            "label": "评价目标",
            "label_col": "label_main_half",
            "value_col": "value_full_half",
            "label_format": HALF_LABEL_FMT,
            "embed": {
                "kind": "content_with_tables",
                "text_key": "evaluation_objective_intro",
                "tables_key": "evaluation_objective_tables",
            },
        }
    ]


def section_project_summary() -> List[Dict[str, Any]]:
    return [
        {
            "id": "project_summary",
            "type": "label_value",
            "label": "项目概述",
            "label_col": "label_main_half",
            "value_col": "value_full_half",
            "label_format": HALF_LABEL_FMT,
            "field_key": "project_summary",
        }
    ]


def section_hazard_analysis() -> List[Dict[str, Any]]:
    return [
        {
            "id": "hazard_analysis",
            "type": "label_value",
            "label": "职业病危害因素分析",
            "label_col": "label_main_half",
            "value_col": "value_full_half",
            "field_key": "hazard_analysis",
            "label_format": HALF_LABEL_FMT,
        }
    ]


def section_workplace_layout() -> List[Dict[str, Any]]:
    """工作场所布局：正文 + 嵌套表（不含 A3 附图）。"""
    return [
        {
            "id": "workplace_layout",
            "type": "label_embedded_table",
            "label": "工作场所布局",
            "label_col": "label_main_half",
            "value_col": "value_full_half",
            "label_format": HALF_LABEL_FMT,
            "embed": {
                "kind": "content_with_tables",
                "text_key": "workplace_layout_intro",
                "tables_key": "workplace_layout_tables",
            },
        }
    ]


def section_protection_measures() -> List[Dict[str, Any]]:
    """
    防护设施和措施。
    「放射防护分区」绑定正文 + A3 横置附图（平面布局和放射分区图）。
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
            "id": "protection_other_lines",
            "type": "section",
            "section_label": "防护设施和措施",
            "section_col": "label_main_half",
            "section_label_format": HALF_LABEL_FMT,
            "continue_section": True,
            "lines": [
                {
                    "cells": [
                        {"role": "label", "col": "sub_label_row_half", "text": "屏蔽设施", "label_format": HALF_LABEL_FMT},
                        {
                            "role": "value",
                            "col": "value_wide_half",
                            "field_key": "shielding",
                        },
                    ]
                },
                {
                    "cells": [
                        {"role": "label", "col": "sub_label_row_half", "text": "联锁保护措施", "label_format": HALF_LABEL_FMT},
                        {
                            "role": "value",
                            "col": "value_wide_half",
                            "field_key": "interlock_protection",
                        },
                    ]
                },
                {
                    "cells": [
                        {
                            "role": "label",
                            "col": "sub_label_row_half",
                            "text": "电离辐射警示标识",
                            "label_format": HALF_LABEL_FMT,
                        },
                        {
                            "role": "value",
                            "col": "value_wide_half",
                            "field_key": "warning_signs",
                        },
                    ]
                },
                {
                    "cells": [
                        {"role": "label", "col": "sub_label_row_half", "text": "个人防护用品", "label_format": HALF_LABEL_FMT},
                        {
                            "role": "value",
                            "col": "value_wide_half",
                            "field_key": "personal_protective_equipment",
                        },
                    ]
                },
                {
                    "cells": [
                        {"role": "label", "col": "sub_label_row_half", "text": "放射性废物处置", "label_format": HALF_LABEL_FMT},
                        {
                            "role": "value",
                            "col": "value_wide_half",
                            "field_key": "waste_disposal",
                        },
                    ]
                },
                {
                    "cells": [
                        {"role": "label", "col": "sub_label_row_half", "text": "其他", "label_format": HALF_LABEL_FMT},
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


def section_health_impact() -> List[Dict[str, Any]]:
    return [
        {
            "id": "health_impact",
            "type": "section",
            "section_label": "健康影响评价",
            "section_col": "label_main_half",
            "section_label_format": HALF_LABEL_FMT,
            "lines": [
                {
                    "cells": [
                        {"role": "label", "col": "sub_label_row_half", "text": "正常情况下", "label_format": HALF_LABEL_FMT},
                        {
                            "role": "value",
                            "col": "value_wide_half",
                            "field_key": "normal_condition",
                        },
                    ]
                },
                {
                    "cells": [
                        {"role": "label", "col": "sub_label_row_half", "text": "异常情况下", "label_format": HALF_LABEL_FMT},
                        {
                            "role": "value",
                            "col": "value_wide_half",
                            "field_key": "abnormal_condition",
                        },
                    ]
                },
            ],
        }
    ]


def section_radiation_management() -> List[Dict[str, Any]]:
    return [
        {
            "id": "radiation_management",
            "type": "section",
            "section_label": "放射防护管理",
            "section_col": "label_main_half",
            "section_label_format": HALF_LABEL_FMT,
            "lines": [
                {
                    "cells": [
                        {"role": "label", "col": "sub_label_row_half", "text": "组织机构", "label_format": HALF_LABEL_FMT},
                        {
                            "role": "value",
                            "col": "value_wide_half",
                            "field_key": "organization",
                        },
                    ]
                },
                {
                    "cells": [
                        {
                            "role": "label",
                            "col": "sub_label_row_half",
                            "text": "管理制度及措施",
                            "label_format": HALF_LABEL_FMT,
                        },
                        {
                            "role": "value",
                            "col": "value_wide_half",
                            "field_key": "management_system",
                        },
                    ]
                },
                {
                    "cells": [
                        {
                            "role": "label",
                            "col": "sub_label_row_half",
                            "text": "放射工作人员配置与管理",
                            "label_format": HALF_LABEL_FMT,
                        },
                        {
                            "role": "value",
                            "col": "value_wide_half",
                            "field_key": "staff_management",
                        },
                    ]
                },
                {
                    "cells": [
                        {"role": "label", "col": "sub_label_row_half", "text": "个人监测", "label_format": HALF_LABEL_FMT},
                        {
                            "role": "value",
                            "col": "value_wide_half",
                            "field_key": "personal_monitoring",
                        },
                    ]
                },
                {
                    "cells": [
                        {"role": "label", "col": "sub_label_row_half", "text": "职业健康监护", "label_format": HALF_LABEL_FMT},
                        {
                            "role": "value",
                            "col": "value_wide_half",
                            "field_key": "health_surveillance",
                        },
                    ]
                },
                {
                    "cells": [
                        {"role": "label", "col": "sub_label_row_half", "text": "放射防护培训", "label_format": HALF_LABEL_FMT},
                        {
                            "role": "value",
                            "col": "value_wide_half",
                            "field_key": "radiation_training",
                        },
                    ]
                },
            ],
        }
    ]


def section_conclusion() -> List[Dict[str, Any]]:
    return [
        {
            "id": "conclusion",
            "type": "label_value",
            "label": "评价结论与建议",
            "label_col": "label_main_half",
            "value_col": "value_full_half",
            "field_key": "conclusion",
            "label_format": HALF_LABEL_FMT,
        }
    ]


def section_attachments() -> List[Dict[str, Any]]:
    """正文末「附件」栏：单元格内填写附件一～九名称清单（后面的附图页另生成）。"""
    return [
        {
            "id": "attachments",
            "type": "label_value",
            "label": "附件",
            "label_col": "label_main_half",
            "value_col": "value_full_half",
            "field_key": "attachments_list",
            "label_format": {"mode": "center"},
        }
    ]


# 表头顺序：改这里即可调整整表栏目顺序
SECTION_BUILDERS = [
    ("org_header", section_org_header),
    ("project_info", section_project_info),
    ("radiation_source", section_radiation_source),
    ("evaluation_basis", section_evaluation_basis),
    ("evaluation_objective", section_evaluation_objective),
    ("project_summary", section_project_summary),
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
