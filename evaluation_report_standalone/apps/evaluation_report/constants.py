"""评价报告模板与上传项配置。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UploadSlotDef:
    key: str
    label: str
    library_category: str  # evaluation_form | attachment
    required: bool = False
    appendix_section: str = ""
    appendix_anchor: str = "section"


@dataclass(frozen=True)
class ReportTemplateDef:
    key: str
    name: str
    latex_dir: str
    report_subtype: str
    description: str = ""
    # 兼容旧字段：已不再参与匹配/新建流程
    device_category: str = ""


SUBTYPE_BRACHY_PRE = "brachy_pre"
SUBTYPE_BRACHY_CONTROL = "brachy_control"
SUBTYPE_LINAC_PRE = "linac_pre"
SUBTYPE_LINAC_CONTROL = "linac_control"

REPORT_SUBTYPE_BLANK = ""

# 四种评价报告类型（新建报告直接选择，不再区分设备大类）
REPORT_TYPE_CHOICES = (
    (SUBTYPE_BRACHY_PRE, "后装预评"),
    (SUBTYPE_BRACHY_CONTROL, "后装控评"),
    (SUBTYPE_LINAC_PRE, "加速器预评"),
    (SUBTYPE_LINAC_CONTROL, "加速器控评"),
)

# 历史别名
ACCELERATOR_SUBTYPE_CHOICES = REPORT_TYPE_CHOICES

# 遗留常量（仅兼容旧数据/迁移，业务逻辑不再使用）
DEVICE_ACCELERATOR = "accelerator"
DEVICE_GENERAL = "general_radiology"
DEVICE_CATEGORY_CHOICES = (
    (DEVICE_ACCELERATOR, "加速器"),
    (DEVICE_GENERAL, "普放设备"),
)


REPORT_TEMPLATES: dict[str, ReportTemplateDef] = {
    "yp250420": ReportTemplateDef(
        key="yp250420",
        name="YP250420 后装预评价报告书",
        latex_dir="yp250420",
        report_subtype=SUBTYPE_BRACHY_PRE,
        description="萍乡市人民医院后装治疗机机房改建项目预评价（GBZ/T 181 结构）",
    ),
    "kp250420": ReportTemplateDef(
        key="kp250420",
        name="KP250420 后装控制效果评价报告书",
        latex_dir="kp250420",
        report_subtype=SUBTYPE_BRACHY_CONTROL,
        description="后装治疗机机房改建项目控制效果评价（对照 KP 报批稿结构，基于系统模板改造）",
    ),
    "yp_linac250221": ReportTemplateDef(
        key="yp_linac250221",
        name="YP250221 加速器预评价报告书",
        latex_dir="yp_linac250221",
        report_subtype=SUBTYPE_LINAC_PRE,
        description="医用电子直线加速器+CT模拟定位机预评价（对照大余县人民医院 YP250221-02 报批稿结构）",
    ),
    "kp_linac250454": ReportTemplateDef(
        key="kp_linac250454",
        name="KP250454 加速器控制效果评价报告书",
        latex_dir="kp_linac250454",
        report_subtype=SUBTYPE_LINAC_CONTROL,
        description="医用电子直线加速器+CT模拟定位机控制效果评价（对照上饶市立医院 KP250454 报批稿结构）",
    ),
}


def template_choices_for_ui() -> list[dict[str, Any]]:
    from .latex_template_service import template_choices_for_ui as _db_choices

    rows = _db_choices()
    if rows:
        return rows
    return [
        {
            "key": t.key,
            "name": t.name,
            "device_category": "",
            "device_label": "",
            "report_subtype": t.report_subtype,
            "subtype_label": dict(REPORT_TYPE_CHOICES).get(t.report_subtype, ""),
            "description": t.description,
            "is_editable": False,
            "has_main_tex": True,
        }
        for t in REPORT_TEMPLATES.values()
    ]


def template_for_type(report_subtype: str) -> ReportTemplateDef | None:
    """按评价报告类型匹配模板（不再使用设备大类）。"""
    from .latex_template_service import template_for_type as _db_match

    subtype = (report_subtype or "").strip()
    if not subtype:
        return None
    row = _db_match(subtype)
    if row:
        return ReportTemplateDef(
            key=row.key,
            name=row.name,
            latex_dir=row.bundled_dir or row.key,
            report_subtype=row.report_subtype or subtype,
            description=row.description,
        )
    for tpl in REPORT_TEMPLATES.values():
        if tpl.report_subtype == subtype:
            return tpl
    return None


def reference_pdf_template_map() -> dict[str, str]:
    """三份报批稿 PDF 与模板键的对应关系。"""
    from .template_push import REFERENCE_PDF_TO_TEMPLATE

    return dict(REFERENCE_PDF_TO_TEMPLATE)
