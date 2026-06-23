"""
按报告任务模板 code 提供终稿叠印 / 回填几何与槽位语义（单份自动报告 PDF）。

新增模板：在此增加 Profile 并在 ``REPORT_TEMPLATE_PROFILES`` 注册；
未注册的任务使用 ``DEFAULT_REPORT_TEMPLATE_PROFILE``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from apps.core.models import LibraryTask

F1SlotKind = Literal["report_no", "commission_no"]


@dataclass(frozen=True)
class ReportTemplateProfile:
    """单份报告终稿后处理参数（PyMuPDF 坐标系：原点左上，单位 pt）。"""

    code: str = ""
    # 基本情况表
    basic_info_value_x0: float = 176.0
    basic_info_table_x1: float = 519.34
    basic_info_project_name_x0: float = 180.65
    # 页眉左缘 / 右缘与基本情况表外框对齐（None=沿用 DSA 定版坐标）
    page_header_left_x0: float | None = None
    page_header_right_x1: float | None = None
    summary_page_header_right_x1: float | None = None
    # 下列 pdfFieldId 由 HTMLPDF 映射回填（红字），终稿叠印不再覆写
    htmlpdf_basic_info_pdf_field_ids: tuple[str, ...] = ()
    # 封面「项目名称」：向上扩展擦除区，去掉模板预印「放射诊疗设备」等
    cover_project_name_erase_above_pt: float = 0.0
    cover_modality_residue_texts: tuple[str, ...] = ("放射诊疗设备",)
    # True：两行取值写在「项目名称」标签下方至下划线之间（勿用向上扩展的擦除带定位）
    cover_project_name_write_below_label: bool = False
    # 评价：自「二、评价」下缘至「编制人」上缘；write_x0 与模板预印「应」字左缘对齐（≈100pt）
    evaluation_clear_x0: float = 176.0
    evaluation_write_x0: float = 176.0
    evaluation_text_indent: bool = True
    evaluation_text_only_clear: bool = False
    # 评价正文写入区整体下移（pt），避免顶端压线
    evaluation_write_y_offset: float = 0.0
    # 封面第二行与下划线间距（pt）
    cover_project_name_line2_above_underline_pt: float = 5.5
    # 报告 JSON 模板 pdfFieldId f1：多数 DSA 为页眉报告编号；CBCT 状态模板 f1=委托编号
    f1_slot: F1SlotKind = "report_no"


DEFAULT_REPORT_TEMPLATE_PROFILE = ReportTemplateProfile()

# 002-状态检测 / 11-加速器中的CBCT / cbct-linac-status-report（空白 PDF 实测）
CBCT_LINAC_STATUS_REPORT_PROFILE = ReportTemplateProfile(
    code="cbct-linac-status-report",
    basic_info_value_x0=180.65,
    basic_info_table_x1=524.57,
    basic_info_project_name_x0=180.65,
    page_header_left_x0=70.65,
    page_header_right_x1=524.57,
    summary_page_header_right_x1=524.57,
    cover_project_name_erase_above_pt=24.0,
    cover_modality_residue_texts=("放射诊疗设备",),
    cover_project_name_write_below_label=True,
    cover_project_name_line2_above_underline_pt=9.0,
    evaluation_clear_x0=76.2,
    evaluation_write_x0=76.2,
    evaluation_text_indent=True,
    evaluation_text_only_clear=True,
    evaluation_write_y_offset=4.0,
    htmlpdf_basic_info_pdf_field_ids=("f1", "f3"),
    f1_slot="commission_no",
)

REPORT_TEMPLATE_PROFILES: dict[str, ReportTemplateProfile] = {
    CBCT_LINAC_STATUS_REPORT_PROFILE.code: CBCT_LINAC_STATUS_REPORT_PROFILE,
}


def get_report_template_profile(report_task: LibraryTask | None) -> ReportTemplateProfile:
    if report_task is None:
        return DEFAULT_REPORT_TEMPLATE_PROFILE
    code = (getattr(report_task, "code", None) or "").strip()
    if code and code in REPORT_TEMPLATE_PROFILES:
        return REPORT_TEMPLATE_PROFILES[code]
    return DEFAULT_REPORT_TEMPLATE_PROFILE
