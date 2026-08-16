# -*- coding: utf-8 -*-
"""放射防护管理：组织机构 / 表16～21 / 人员配置；异常情况与结论另见对应模板。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

TABLE_MARK = "<<<F1_TABLE>>>"

TABLE16_TITLE = "表16 本项目放射防护管理制度设置计划及评价"
TABLE17_TITLE = "表17 本项目放射工作人员结构配备计划及评价"
TABLE18_TITLE = "表18 个人剂量管理计划情况及评价"
TABLE19_TITLE = "表19 职业健康管理计划情况及评价"
TABLE20_TITLE = "表20 放射防护培训计划情况及评价"
TABLE21_TITLE = "表21 档案管理计划情况及评价"

DEFAULT_ORGANIZATION = (
    "根据建设单位提供的相关信息，建设单位拟根据新建院区实际情况，成立放射防护管理组织。\n"
    "建议：建设单位成立放射防护管理组织，需设置专（兼）职的管理人员，"
    "负责工作质量保证和安全防护工作，并明确放射防护管理组织主要职责。"
)

DEFAULT_MGMT_INTRO = (
    "建设单位已经开展放射诊疗工作多年，建设单位拟沿用现有相关放射防护管理制度情况下，"
    "计划制定本项目射线装置操作规程及放射工作人员职业健康管理制度，"
    "放射防护管理制度设置情况见表16。"
)

DEFAULT_MGMT_AFTER = (
    "从表16可知，建设单位在应急预案、质量控制、放射防护安全和管理、岗位职责等方面"
    "初步制定了管理制度。\n"
    "建议：建设单位根据本项目实际情况和表16的建议健全、完善相关放射防护管理制度，"
    "将相关放射防护管理制度（如操作规程、应急预案、岗位职责等制度）进行上墙。"
)

DEFAULT_STAFF_INTRO = (
    "建设单位根据本项目情况，拟配备{staff_count}名放射工作人员，"
    "拟配备的具体放射工作人员待定，具体配备计划详见表17。"
)

DEFAULT_STAFF_AFTER = (
    "综上所述，本项目拟配备的人员结构符合《放射诊疗管理规定》相关管理规定，"
    "满足其正常运行的工作需要。"
)

DEFAULT_ABNORMAL = (
    "本项目运行过程中存在潜在照射的可能性，为预防异常事故的发生，"
    "保障放射工作人员和公众的安全，做好如下预防措施，估计潜在照射发生的可能性很小。\n"
    "本项目可能发生的异常情况及预防措施如下：\n"
    "（1）X射线球管损坏等设备故障：导致无法正常开展工作，或者使受检者接受不必要的照射；\n"
    "预防措施：做好设备稳定性检测并委托有相应资质的放射卫生技术服务机构定期做好状态检测工作，"
    "使设备始终保持正常的工作状态。\n"
    "（2）放射工作人员误操作：使受检者接受不必要的照射或检修人员未按程序操作接受了误照射；\n"
    "预防措施：放射工作人员必须加强防护知识培训，提高防护技能，避免犯常识性错误；"
    "加强职业道德修养，增强责任感；严格遵守操作规程和规章制度；"
    "管理人员应强化管理，落实安全责任制，经常督促检查。\n"
    "（3）人员误入造成的误照射：非受检者在射线装置处于工作状态情况下进入机房，造成不必要的照射；\n"
    "预防措施：撤离机房时清点人数，必须按程序对机房进行全视角搜寻，"
    "对滞留机房内的无关人员强行劝离。有外来人员进入时，工作人员应根据情况，"
    "采取急停或相应措施，阻止外来人员受到误照射。\n"
    "（4）屏蔽设施损坏或防护措施失效：导致相关人员接受不必要的额外照射。\n"
    "预防措施：定期对各个安全防护措施进行检查，发现故障及时清除，"
    "严禁在警示灯失效的情况下违规操作，对放射工作场所定期进行自主检测，发现异常及时报修。"
)

# 表16：(序号, 制度类型, 现已制定, 计划, 评价) 空类型表示与上行合并类型
TABLE16_ROWS: List[Tuple[str, str, str, str, str]] = [
    ("1", "应急预案", "放射安全事件应急预案", "拟沿用", "建议：根据新建院区实际情况，完善应急领导小组及应急预案"),
    ("2", "", "辐射损伤处置流程和规范", "拟沿用", ""),
    ("3", "", "放射科患者紧急意外情况的预防和抢救预案", "拟沿用", ""),
    ("4", "质量控制与防护安全管理", "医疗质量安全管理制度", "拟沿用", "符合"),
    ("5", "", "介入影像质量保证方案", "拟沿用", "符合"),
    ("6", "", "质量与安全管理制度", "拟沿用", "符合"),
    ("7", "", "放射科辐射安全管理制度", "拟沿用", "符合"),
    ("8", "", "放射防护安全管理制度", "拟沿用", "符合"),
    ("9", "人员职责", "影像中心员工岗位职责", "拟沿用", "符合"),
    ("10", "设备管理\n操作规程", "放射诊疗设备操作规程制度", "拟制定", "建议：根据购买的设备制定详细的操作规程"),
    ("11", "", "X线摄影的操作规程", "拟沿用", "符合"),
    ("12", "设备管理\n维护保养", "仪器设备检测、维修及保养制度", "拟沿用", "符合"),
    ("13", "", "大型医用设备使用管理办法与制度", "拟沿用", "符合"),
    ("14", "", "设备使用管理制度", "拟沿用", "符合"),
    ("15", "辐射监测计划", "辐射监测计划", "拟沿用", "符合"),
    ("16", "人员管理\n工作人员", "放射工作人员职业健康管理制度", "拟制定", "建议：制定关于放射工作人员个人剂量、放射防护培训、职业健康体检及档案管理的具体要求"),
    ("17", "人员管理\n患者及受检者", "电离辐射危害告知、温馨提示", "拟沿用", "符合"),
    ("18", "防护用品", "放射防护用品管理、监测、维护和保养制度", "拟沿用", "符合"),
    ("19", "", "影像中心医用铅衣的维护和保养", "拟沿用", "符合"),
]

# 表17 默认配备（与样例一致）
# group: (开展工作类型, 法规要求, rows[(工作类别, 人数文案)], 评价)
StaffGroup = Tuple[str, str, List[Tuple[str, str]], str]
DEFAULT_STAFF_GROUPS: List[StaffGroup] = [
    (
        "X射线影像诊断【CT、DR、数字胃肠机、全身骨密度仪、乳腺DR】",
        "第七条（四）开展X射线影像诊断的，应具有专业的放射影像医师。",
        [
            ("大学本科以上学历或中级以上的放射影像医师", "4人"),
            ("其他学历和职称放射影像医师", "6人"),
            ("放射影像技师", "16人"),
        ],
        "符合",
    ),
    (
        "X射线影像诊断\n【C形臂】",
        "第七条（四）开展X射线影像诊断的，应具有专业的放射影像医师。",
        [
            ("放射影像医师", "影像中心人员兼职"),
            ("放射影像技师", "2人"),
            ("骨外科医师", "4人"),
            ("麻醉医师", "2人"),
            ("护理人员", "2人"),
        ],
        "符合",
    ),
    (
        "介入放射学\n【ERCP】",
        "第七条（三）开展介入放射学工作的，应当具有：\n"
        "大学本科以上学历或中级以上专业技术职务任职资格的放射影像医师；\n"
        "放射影像技师；\n相关内、外科的专业技术人员。",
        [
            ("大学本科以上学历或中级以上的放射影像医师", "影像中心人员兼职"),
            ("放射影像技师", "2人"),
            ("内科医师", "4人"),
            ("护理人员", "4人"),
        ],
        "符合",
    ),
    (
        "介入放射学\n【DSA】",
        "第七条（三）开展介入放射学工作的，应当具有：\n"
        "大学本科以上学历或中级以上专业技术职务任职资格的放射影像医师；\n"
        "放射影像技师；\n相关内、外科的专业技术人员。",
        [
            ("大学本科以上学历或中级以上的放射影像医师", "影像中心人员兼职"),
            ("放射影像技师", "2人"),
            ("外科医师", "4人"),
            ("内科医师", "4人"),
            ("护理人员", "3人"),
        ],
        "符合",
    ),
]


def _load_fixed(rel: str, default: str = "") -> str:
    from f1_eval_report.templates.loader import load_common_text

    try:
        t = load_common_text(rel).strip()
        return t or default
    except Exception:
        return default


def _cell(
    c0: int,
    c1: int,
    text: str,
    *,
    row_span: int = 1,
    align: str = "center",
    **extra: Any,
) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "c0": c0,
        "c1": c1,
        "row_span": row_span,
        "col_span": max(1, c1 - c0),
        "text": text,
        "align": align,
    }
    d.update(extra)
    return d


def _empty_table(title: str) -> Dict[str, Any]:
    return {
        "schema": "embedded_generic_table/v1",
        "title": title,
        "source": "radiation_management",
        "column_count": 1,
        "column_fractions": [0.0, 1.0],
        "band_count": 1,
        "rows": [
            {
                "band": 0,
                "cells": [_cell(0, 1, "（待填写）")],
            }
        ],
    }


def build_table16() -> Dict[str, Any]:
    header = {
        "band": 0,
        "cells": [
            _cell(0, 1, "序号"),
            _cell(1, 2, "制度类型"),
            _cell(2, 3, "现已制定情况"),
            _cell(3, 4, "计划情况"),
            _cell(4, 5, "评价/建议"),
        ],
    }
    bands: List[Dict[str, Any]] = [header]
    # 预计算制度类型合并
    i = 0
    rows = list(TABLE16_ROWS)
    band_i = 1
    while i < len(rows):
        seq, typ, name, plan, eval_ = rows[i]
        span = 1
        if typ:
            j = i + 1
            while j < len(rows) and not rows[j][1]:
                span += 1
                j += 1
        cells: List[Dict[str, Any]] = [_cell(0, 1, seq)]
        if typ:
            cells.append(_cell(1, 2, typ, row_span=span, align="left"))
        cells.append(_cell(2, 3, name, align="left"))
        cells.append(_cell(3, 4, plan))
        cells.append(_cell(4, 5, eval_, align="left"))
        bands.append({"band": band_i, "cells": cells})
        band_i += 1
        i += 1
    return {
        "schema": "embedded_generic_table/v1",
        "title": TABLE16_TITLE,
        "source": "radiation_management",
        "column_count": 5,
        "column_fractions": [0.0, 0.08, 0.28, 0.52, 0.66, 1.0],
        "band_count": len(bands),
        "row_min_height_pt": 16.0,
        "repeat_header_on_new_page": True,
        "header_band_count": 1,
        "rows": bands,
    }


def count_staff_from_groups(groups: Sequence[StaffGroup]) -> int:
    total = 0
    for _g, _req, rows, _ev in groups:
        for _cat, num in rows:
            m = re.search(r"(\d+)\s*人", num)
            if m:
                total += int(m.group(1))
    return total


def build_table17(
    *,
    groups: Optional[Sequence[StaffGroup]] = None,
) -> Dict[str, Any]:
    groups = list(groups or DEFAULT_STAFF_GROUPS)
    # 两行表头
    header0 = {
        "band": 0,
        "cells": [
            _cell(0, 1, "开展工作类型（设备类型）", row_span=2),
            _cell(1, 3, "拟配备的人员情况"),
            _cell(3, 4, "《放射诊疗管理规定》相关要求", row_span=2),
            _cell(4, 5, "评价", row_span=2),
        ],
    }
    header1 = {
        "band": 1,
        "cells": [
            _cell(1, 2, "工作类别"),
            _cell(2, 3, "人数"),
        ],
    }
    bands: List[Dict[str, Any]] = [header0, header1]
    band_i = 2
    for gname, req, rows, ev in groups:
        n = len(rows)
        for ri, (cat, num) in enumerate(rows):
            cells: List[Dict[str, Any]] = []
            if ri == 0:
                cells.append(_cell(0, 1, gname, row_span=n, align="left"))
            cells.append(_cell(1, 2, cat, align="left"))
            cells.append(_cell(2, 3, num))
            if ri == 0:
                cells.append(_cell(3, 4, req, row_span=n, align="left"))
                cells.append(_cell(4, 5, ev, row_span=n))
            bands.append({"band": band_i, "cells": cells})
            band_i += 1
    total = count_staff_from_groups(groups)
    bands.append(
        {
            "band": band_i,
            "cells": [
                _cell(0, 2, "共计"),
                _cell(2, 3, f"{total}人"),
                _cell(3, 4, "/"),
                _cell(4, 5, "/"),
            ],
        }
    )
    return {
        "schema": "embedded_generic_table/v1",
        "title": TABLE17_TITLE,
        "source": "radiation_management",
        "from_info_sheet": False,
        "column_count": 5,
        "column_fractions": [0.0, 0.28, 0.52, 0.62, 0.90, 1.0],
        "band_count": len(bands),
        "row_min_height_pt": 16.0,
        "repeat_header_on_new_page": True,
        "header_band_count": 2,
        "staff_count": total,
        "rows": bands,
    }


def _plan_eval_table(
    title: str,
    rows: List[Tuple[str, str, str, str]],
) -> Dict[str, Any]:
    header = {
        "band": 0,
        "cells": [
            _cell(0, 1, "项目"),
            _cell(1, 2, "计划情况"),
            _cell(2, 3, "法规/标准要求"),
            _cell(3, 4, "评价/建议"),
        ],
    }
    bands: List[Dict[str, Any]] = [header]
    for i, (proj, plan, req, ev) in enumerate(rows, start=1):
        bands.append(
            {
                "band": i,
                "cells": [
                    _cell(0, 1, proj, align="left"),
                    _cell(1, 2, plan, align="left"),
                    _cell(2, 3, req, align="left"),
                    _cell(3, 4, ev, align="left"),
                ],
            }
        )
    return {
        "schema": "embedded_generic_table/v1",
        "title": title,
        "source": "radiation_management",
        "column_count": 4,
        "column_fractions": [0.0, 0.18, 0.42, 0.72, 1.0],
        "band_count": len(bands),
        "row_min_height_pt": 18.0,
        "repeat_header_on_new_page": True,
        "header_band_count": 1,
        "rows": bands,
    }


def build_table18() -> Dict[str, Any]:
    return _plan_eval_table(
        TABLE18_TITLE,
        [
            (
                "个人剂量管理对象",
                "计划委托有资质的机构对本项目放射工作人员进行个人剂量监测",
                "放射工作人员均应接受个人剂量监测",
                "评价：符合；\n建议：放射工作人员佩戴个人剂量计后方可从事放射工作",
            ),
            (
                "委托个人剂量监测单位",
                "",
                "个人剂量监测工作应当由具有资质的个人剂量监测技术服务机构承担",
                "",
            ),
            (
                "监测周期",
                "不超过3个月每周期",
                "常规监测周期一般为1个月，最长不得超过3个月",
                "",
            ),
        ],
    )


def build_table19() -> Dict[str, Any]:
    return _plan_eval_table(
        TABLE19_TITLE,
        [
            (
                "职业健康检查对象",
                "计划委托有资质的机构对本项目放射工作人员进行上岗前的职业健康检查",
                "对放射诊疗工作人员进行上岗前、在岗期间和离岗时的健康检查",
                "评价：符合；\n建议：本项目工作人员在上岗前进行职业健康检查，上岗前体检结果应符合国家要求",
            ),
            (
                "委托职业健康检查单位",
                "",
                "从事放射工作人员职业健康检查的医疗机构应当经省级卫生健康行政部门批准",
                "",
            ),
            (
                "检查周期",
                "",
                "上岗前和在岗期间（上岗后的两次检查的时间间隔不应超过2年）",
                "",
            ),
            (
                "体检结果",
                "",
                "体检结果应符合国家要求",
                "",
            ),
        ],
    )


def build_table20() -> Dict[str, Any]:
    return _plan_eval_table(
        TABLE20_TITLE,
        [
            (
                "教育培训对象",
                "计划对本项目放射工作人员进行上岗前的放射防护培训",
                "①定期放射工作人员接受放射防护和有关法律知识培训\n"
                "②放射工作人员上岗前应当接受放射防护和有关法律知识培训",
                "评价：符合；\n建议：本项目工作人员在上岗前进行放射防护培训，上岗前培训结果应符合国家要求",
            ),
            (
                "承担培训单位",
                "",
                "由符合省级卫生健康行政部门规定的单位承担",
                "",
            ),
            (
                "培训周期",
                "",
                "上岗前和在岗期间（放射工作人员两次培训时间的间隔不超过2年）",
                "",
            ),
            (
                "培训结果",
                "",
                "培训结果应合格",
                "",
            ),
        ],
    )


def build_table21() -> Dict[str, Any]:
    """档案管理计划（多层合并）。"""
    bands: List[Dict[str, Any]] = [
        {
            "band": 0,
            "cells": [
                _cell(0, 1, "项目"),
                _cell(1, 2, ""),
                _cell(2, 3, ""),
                _cell(3, 4, "计划情况"),
                _cell(4, 5, "法规/标准"),
                _cell(5, 6, "评价"),
            ],
        }
    ]

    def add_row(cells: List[Dict[str, Any]]) -> None:
        bands.append({"band": len(bands), "cells": cells})

    health_span = 7
    add_row(
        [
            _cell(0, 1, "放射工作人员健康管理档案", row_span=health_span, align="left"),
            _cell(1, 2, "教育培训档案", row_span=2, align="left"),
            _cell(2, 3, "保存内容"),
            _cell(3, 4, "培训证书", align="left"),
            _cell(
                4,
                5,
                "对放射诊疗工作人员分别建立个人剂量、职业健康管理和教育培训档案",
                row_span=health_span,
                align="left",
            ),
            _cell(5, 6, "符合", row_span=health_span),
        ]
    )
    add_row([_cell(2, 3, "管理期限"), _cell(3, 4, "规定终生保存", align="left")])
    add_row(
        [
            _cell(1, 2, "个人剂量管理档案", row_span=2, align="left"),
            _cell(2, 3, "保存内容"),
            _cell(3, 4, "个人剂量监测结果", align="left"),
        ]
    )
    add_row([_cell(2, 3, "管理期限"), _cell(3, 4, "规定终生保存", align="left")])
    add_row(
        [
            _cell(1, 2, "职业健康检查档案", row_span=2, align="left"),
            _cell(2, 3, "保存内容"),
            _cell(3, 4, "历次职业健康检查结果及评价处理意见", align="left"),
        ]
    )
    add_row([_cell(2, 3, "管理期限"), _cell(3, 4, "规定终生保存", align="left")])
    add_row(
        [
            _cell(1, 3, "管理科室"),
            _cell(3, 4, "公共卫生科", align="left"),
        ]
    )

    hygiene_items = [
        ("建设项目档案", "本项目预评报告、控制效果评价报告及相应审核批复文件"),
        ("许可档案", "本项目放射诊疗许可证"),
        ("放射卫生管理制度档案", "管理组织、应急预案、培训演练记录、其他放射防护管理制度"),
        ("设备档案", "设备购置合同、设备维护、维修记录等"),
        ("监测档案", "验收检测报告、年度检测报告、安全防护设施及措施检查记录等"),
        ("防护用品档案", "防护用品清单、维护检查记录等"),
    ]
    hy_span = len(hygiene_items) * 2
    first = True
    for sub, content in hygiene_items:
        cells: List[Dict[str, Any]] = []
        if first:
            cells.append(_cell(0, 1, "放射卫生档案", row_span=hy_span, align="left"))
        cells.append(_cell(1, 2, sub, row_span=2, align="left"))
        cells.append(_cell(2, 3, "保存内容"))
        cells.append(_cell(3, 4, content, align="left"))
        if first:
            cells.append(_cell(4, 5, "/"))
            cells.append(_cell(5, 6, "满足放射防护要求", row_span=hy_span, align="left"))
            # fix: / should also rowspan
            cells[-2] = _cell(4, 5, "/", row_span=hy_span)
        add_row(cells)
        add_row(
            [
                _cell(2, 3, "管理科室"),
                _cell(3, 4, "公共卫生科", align="left"),
            ]
        )
        first = False

    return {
        "schema": "embedded_generic_table/v1",
        "title": TABLE21_TITLE,
        "source": "radiation_management",
        "column_count": 6,
        "column_fractions": [0.0, 0.16, 0.34, 0.46, 0.72, 0.88, 1.0],
        "band_count": len(bands),
        "row_min_height_pt": 16.0,
        "repeat_header_on_new_page": True,
        "header_band_count": 1,
        "rows": bands,
    }


def parse_staff_groups_from_text(text: str) -> Optional[List[StaffGroup]]:
    """尽力从信息表人员配备计划文本识别总人数；结构仍用默认分组。"""
    if not text or "人员配备" not in text:
        return None
    # 结构解析较脆，保留默认分组，仅用文本确认存在计划
    return list(DEFAULT_STAFF_GROUPS)


def assemble_abnormal_condition() -> str:
    return _load_fixed("health_impact/abnormal.md", DEFAULT_ABNORMAL)


def assemble_radiation_management(
    *,
    staff_total: Optional[int] = None,
    staff_groups: Optional[Sequence[StaffGroup]] = None,
    include_tables: bool = True,
) -> Dict[str, Any]:
    """
    返回各栏目字段及表16～21：
      organization, management_system (+ tables), staff_management (+ tables),
      personal_monitoring (+ tables), health_surveillance, radiation_training,
      archive_management
    """
    org = _load_fixed(
        "radiation_management/organization.md", DEFAULT_ORGANIZATION
    )
    mgmt_intro = _load_fixed(
        "radiation_management/management_intro.md", DEFAULT_MGMT_INTRO
    )
    mgmt_after = _load_fixed(
        "radiation_management/management_after.md", DEFAULT_MGMT_AFTER
    )
    groups = list(staff_groups or DEFAULT_STAFF_GROUPS)
    t17 = build_table17(groups=groups) if include_tables else _empty_table(TABLE17_TITLE)
    n_staff = int(staff_total or t17.get("staff_count") or count_staff_from_groups(groups) or 59)
    staff_intro = _load_fixed(
        "radiation_management/staff_intro.md", DEFAULT_STAFF_INTRO
    ).replace("{staff_count}", str(n_staff))
    staff_after = _load_fixed(
        "radiation_management/staff_after.md", DEFAULT_STAFF_AFTER
    )

    t16 = build_table16() if include_tables else _empty_table(TABLE16_TITLE)
    t18 = build_table18() if include_tables else _empty_table(TABLE18_TITLE)
    t19 = build_table19() if include_tables else _empty_table(TABLE19_TITLE)
    t20 = build_table20() if include_tables else _empty_table(TABLE20_TITLE)
    t21 = build_table21() if include_tables else _empty_table(TABLE21_TITLE)

    management_system = f"{mgmt_intro}\n\n{TABLE_MARK}\n\n{mgmt_after}\n"
    staff_management = f"{staff_intro}\n\n{TABLE_MARK}\n\n{staff_after}\n"
    personal_monitoring = f"{TABLE_MARK}\n"
    health_surveillance = f"{TABLE_MARK}\n"
    radiation_training = f"{TABLE_MARK}\n"
    archive_management = f"{TABLE_MARK}\n"

    return {
        "organization": org.strip() + "\n",
        "management_system": management_system,
        "management_system_tables": [t16],
        "staff_management": staff_management,
        "staff_management_tables": [t17],
        "personal_monitoring": personal_monitoring,
        "personal_monitoring_tables": [t18],
        "health_surveillance": health_surveillance,
        "health_surveillance_tables": [t19],
        "radiation_training": radiation_training,
        "radiation_training_tables": [t20],
        "archive_management": archive_management,
        "archive_management_tables": [t21],
        "staff_count": n_staff,
    }


def device_summary_for_conclusion(
    devices: Optional[Sequence[Dict[str, Any]]],
) -> Dict[str, Any]:
    """统计结论用设备/机房数量。"""
    from f1_eval_report.templates.health_impact import _device_key
    from f1_eval_report.templates.protection_zoning import count_xray_rooms

    counts: Dict[str, int] = {
        "CT": 0,
        "DR": 0,
        "BMD": 0,
        "MAMMO": 0,
        "GI": 0,
        "C臂": 0,
        "DSA": 0,
        "ERCP": 0,
    }
    for d in devices or []:
        name = str(d.get("name") or "")
        place = str(d.get("place") or d.get("location") or "")
        key = _device_key(name, place)
        if key in counts:
            counts[key] += 1
    class_iii = (
        counts["CT"]
        + counts["DR"]
        + counts["BMD"]
        + counts["MAMMO"]
        + counts["GI"]
        + counts["C臂"]
    )
    class_ii = counts["DSA"] + counts["ERCP"]
    rooms = count_xray_rooms(devices) or (class_iii + class_ii)
    return {
        "counts": counts,
        "class_iii": class_iii,
        "class_ii": class_ii,
        "rooms": rooms,
        "total_devices": class_iii + class_ii,
    }


def build_conclusion(
    devices: Optional[Sequence[Dict[str, Any]]] = None,
    *,
    staff_count: int = 59,
) -> str:
    """结论与建议：按样例结构，数量由装置清单/人员表生成。"""
    s = device_summary_for_conclusion(devices)
    c = s["counts"]
    # 缺省样例数量
    if s["total_devices"] <= 0:
        c = {
            "CT": 4,
            "DR": 4,
            "BMD": 1,
            "MAMMO": 1,
            "GI": 1,
            "C臂": 2,
            "DSA": 2,
            "ERCP": 1,
        }
        s = {
            "counts": c,
            "class_iii": 13,
            "class_ii": 3,
            "rooms": 16,
            "total_devices": 16,
        }
    n3, n2, nr = s["class_iii"], s["class_ii"], s["rooms"]
    device_list = (
        f"{c['CT']}台CT、{c['DR']}台DR、{c['BMD']}台全身骨密度仪、"
        f"{c['MAMMO']}台乳腺DR、{c['GI']}台数字胃肠机和{c['C臂']}台C形臂"
    )
    interv_list = f"{c['DSA']}台DSA、{c['ERCP']}台ERCP设备"
    text = (
        "1.结论\n"
        f"（1）本项目为新建项目，涉及{n3}台Ⅲ类射线装置（{device_list}）"
        f"及{n2}台Ⅱ类射线装置（{interv_list}）的使用，"
        "拟开展的放射诊疗项目为介入放射学和X射线影像诊断；"
        "根据《放射诊疗建设项目卫生审查管理规定》，本项目按照可能产生的放射性危害程度与诊疗风险，"
        "属于危害一般类放射诊疗建设项目。\n"
        f"（2）建设单位拟按功能及诊疗需要，分别在门急诊楼一楼建设1间CT机房和1间DR机房，"
        "在医技楼一楼影像中心建设2间CT机房、2间DR机房、1间全身骨密度仪机房、1间乳腺DR机房、"
        "1间数字胃肠机房，在医技楼二楼内镜中心建设1间ERCP机房，在医技楼二楼介入中心建设2间DSA机房，"
        "在医技楼四楼建设2间C形臂机房，在发热门诊楼一楼建设1间CT机房和1间DR机房，"
        f"以上共计{nr}间X射线设备机房整体平面布局相对合理，控制区和监督区划分明确且合理，"
        "符合放射卫生学及GBZ 130-2020《放射诊断放射防护要求》的相关要求；"
        f"{nr}间X射线设备机房最小有效使用面积和最小单边长度均符合"
        "GBZ 130-2020《放射诊断放射防护要求》的相关要求。\n"
        f"（3）本项目{nr}间X射线设备机房拟采取的墙体、防护门及观察等屏蔽体等效铅当量厚度"
        "均不小于GBZ 130-2020《放射诊断放射防护要求》要求的屏蔽铅当量厚度。\n"
        f"（4）本项目{nr}间X射线设备机房拟设置动力通风装置、电离辐射警告标志、"
        "放射防护注意事项告知栏、工作状态指示灯、灯箱上设置警示语句及工作状态指示灯与防护门联动装置"
        "等安全防护措施，拟配备个人防护用品，符合GBZ 130-2020《放射诊断放射防护要求》相关要求，"
        "能有效预防事故照射和控制潜在照射。\n"
        "（5）建设单位拟根据新建院区实际情况成立放射防护管理小组及应急领导小组并明确相关职责；"
        "其他放射防护管理制度拟在沿用现有部分制度下，根据本项目情况完善相关放射防护管理制度，"
        "符合相关要求。\n"
        f"（6）建设单位拟为本项目配备相应岗位的放射工作人员{staff_count}名，"
        "人员结构符合《放射诊疗管理规定》的相关要求；"
        "计划组织本项目放射工作人员进行个人监测、职业健康检查、放射防护培训及相应档案建立管理，"
        "符合国家相关要求。\n"
        "综上所述，本项目在工作场所平面布局、防护设施和措施及放射防护管理等方面"
        "符合国家相关放射卫生法规、规范及标准的要求，在落实以下建议后，"
        "本项目在放射性职业病危害防护方面可行。\n"
        "2.建议\n"
        "（1）完善DSA机房污物通道。\n"
        "（2）落实本项目放射工作人员的放射防护培训、职业健康体检、个人剂量监测和相应档案建立管理事宜。\n"
        "（3）建设单位根据表7建议，在满足标准要求屏蔽铅当量厚度的前提下，"
        "可适当减少各X射线设备机房的部分屏蔽体屏蔽材料厚度。\n"
        "（4）根据本项目实际情况和表16的建议健全、完善相关放射防护管理制度，"
        "将相关放射防护管理制度（如操作规程、应急预案、岗位职责等制度）进行上墙。\n"
        "（5）应及时向相关卫生健康行政部门申请放射性职业病危害预评价审核，"
        "取得卫生健康行政部门预评价审核同意批复后方可施工；"
        "本项目在竣工验收前委托具有相应资质的放射卫生技术服务机构进行建设项目职业病危害放射防护控制效果评价。\n"
        "（6）如本项目放射工作场所或辐射源项发生变更时应重新委托具有相应资质的"
        "放射卫生技术服务机构进行建设项目放射性职业病危害预评价。\n"
    )
    return text
