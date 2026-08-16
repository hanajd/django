"""
按报告任务模板 code 提供终稿叠印 / 回填几何与槽位语义（单份自动报告 PDF）。

新增模板：在此增加 Profile 并在 ``REPORT_TEMPLATE_PROFILES`` 注册；
未注册的任务使用 ``DEFAULT_REPORT_TEMPLATE_PROFILE``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from apps.core.models import LibraryTask

F1SlotKind = Literal["report_no", "commission_no"]


@dataclass(frozen=True)
class ReportTemplateProfile:
    """单份报告终稿后处理参数（PyMuPDF 坐标系：原点左上，单位 pt）。"""

    code: str = ""
    # 基本情况表
    basic_info_value_x0: float = 175.34
    basic_info_table_x1: float = 519.34
    basic_info_project_name_x0: float = 180.65
    # 项目名称叠印区整体上移（pt，y 向下为正故负值上移）
    basic_info_project_name_write_y_offset: float = 0.0
    # 长名称是否在「及」处强制换行（单份 DSA 报告宜关闭以免过早断行）
    basic_info_project_name_split_at_and: bool = True
    # 页眉左缘 / 右缘与基本情况表外框对齐（与 DSA/CT 定版一致；未注册 Profile 的报告亦生效）
    page_header_left_x0: float | None = 70.65
    page_header_right_x1: float | None = 519.34
    summary_page_header_right_x1: float | None = 519.34
    # 下列 pdfFieldId 由 HTMLPDF 映射回填（红字），终稿叠印不再覆写
    htmlpdf_basic_info_pdf_field_ids: tuple[str, ...] = ()
    # 封面「项目名称」：向上扩展擦除区，去掉模板预印「放射诊疗设备」等
    cover_project_name_erase_above_pt: float = 0.0
    cover_modality_residue_texts: tuple[str, ...] = ("放射诊疗设备",)
    # True：两行取值写在「项目名称」标签下方至下划线之间（勿用向上扩展的擦除带定位）
    cover_project_name_write_below_label: bool = False
    # 评价：自「二、评价」下缘至「编制人」上缘；write_x0 与模板预印「应」字左缘对齐（≈78.5pt）
    evaluation_clear_x0: float = 78.5
    evaluation_write_x0: float = 78.5
    evaluation_text_indent: bool = True
    evaluation_text_only_clear: bool = True
    # 评价正文写入区整体下移（pt），避免顶端压线
    evaluation_write_y_offset: float = 3.0
    # 封面第二行与下划线间距（pt）
    cover_project_name_line2_above_underline_pt: float = 5.5
    # 封面「项目名称」两行书写区整体上移（pt，y 向下为正故负值上移）
    cover_project_name_write_y_offset: float = 0.0
    # 封面「项目名称」双下划线模板：上行写在第一根线与标签之间、下行写在两根线之间
    cover_project_name_dual_underline_cells: bool = False
    # 单下划线模板：首行与「项目名称」标签同一行，次行至下划线前（勿上移标签行）
    cover_project_name_first_row_align_label: bool = False
    # 基本情况「受检设备台数」写入区整体上移（pt，y 向下为正故为负值）
    basic_info_device_count_write_y_offset: float = 0.0
    # 基本情况「受检设备台数」写入区右移（pt），避免「1台」压单元格左线
    basic_info_device_count_write_x_offset: float = 0.0
    # 报告 JSON 模板 pdfFieldId f1：多数 DSA 为页眉报告编号；CBCT 状态模板 f1=委托编号
    f1_slot: F1SlotKind = "report_no"
    # 封面由叠印（四号）写入，HTMLPDF 回填阶段须留空
    cover_overlay_pdf_field_ids: tuple[str, ...] = ()
    # 基本情况页由终稿叠印写入、禁止 HTMLPDF 红字回填的 pdfFieldId（如栏位56–59）
    summary_overlay_pdf_field_ids: tuple[str, ...] = ()
    # 基本情况表由 HTMLPDF 红字回填的 pdfFieldId（终稿叠印不覆盖）
    contact_pdf_field_ids: tuple[str, ...] = ("f4", "f5", "f23", "f24")
    # 受检设备台数叠印是否带「台」字（模板格旁常另有「台」单位）
    device_count_include_unit_suffix: bool = True


DEFAULT_REPORT_TEMPLATE_PROFILE = ReportTemplateProfile()

# 002-状态检测 / 03DSA / dsa-2（空白 PDF 实测，与 ctc-1 同套叠印策略）
DSA_STATUS_REPORT_PROFILE = ReportTemplateProfile(
    code="dsa-2",
    basic_info_value_x0=175.3,
    basic_info_table_x1=519.34,
    basic_info_project_name_x0=175.3,
    basic_info_project_name_write_y_offset=-8.0,
    basic_info_project_name_split_at_and=False,
    page_header_left_x0=70.65,
    page_header_right_x1=519.34,
    summary_page_header_right_x1=519.34,
    cover_project_name_erase_above_pt=20.0,
    cover_modality_residue_texts=("放射诊疗设备",),
    cover_project_name_write_below_label=True,
    cover_project_name_line2_above_underline_pt=11.0,
    basic_info_device_count_write_y_offset=-2.5,
    basic_info_device_count_write_x_offset=20.0,
    evaluation_clear_x0=78.5,
    evaluation_write_x0=78.5,
    evaluation_text_indent=True,
    evaluation_text_only_clear=True,
    evaluation_write_y_offset=4.0,
    cover_overlay_pdf_field_ids=("f2", "f3", "f4", "f5"),
    htmlpdf_basic_info_pdf_field_ids=("f6", "f7", "f8", "f9", "f10", "f11"),
    contact_pdf_field_ids=("f8", "f9"),
    device_count_include_unit_suffix=True,
    f1_slot="report_no",
)

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

# 002-状态检测 / 01-CT / ct-1（空白 PDF 实测：f2/f3 红字，f26 台数叠印，质控小节 2.1）
CT1_STATUS_REPORT_PROFILE = ReportTemplateProfile(
    code="ct-1",
    basic_info_value_x0=175.34,
    basic_info_table_x1=519.34,
    basic_info_project_name_x0=180.65,
    page_header_left_x0=70.65,
    page_header_right_x1=519.34,
    summary_page_header_right_x1=519.34,
    cover_project_name_erase_above_pt=18.0,
    cover_modality_residue_texts=("放射诊疗设备",),
    cover_project_name_write_below_label=True,
    cover_project_name_line2_above_underline_pt=16.0,
    basic_info_device_count_write_y_offset=0.0,
    basic_info_device_count_write_x_offset=0.0,
    evaluation_clear_x0=78.5,
    evaluation_write_x0=78.5,
    evaluation_text_indent=True,
    evaluation_text_only_clear=True,
    evaluation_write_y_offset=3.0,
    htmlpdf_basic_info_pdf_field_ids=("f2", "f3", "f4", "f5"),
    summary_overlay_pdf_field_ids=("f26",),
    contact_pdf_field_ids=("f4", "f5"),
    f1_slot="commission_no",
)

# 001-验收检测 / 01-CT / ctc-1（CT、C臂机验收报告，空白 PDF 实测）
CTC_ACCEPT_REPORT_PROFILE = ReportTemplateProfile(
    code="ctc-1",
    basic_info_value_x0=175.34,
    basic_info_table_x1=519.34,
    basic_info_project_name_x0=180.65,
    page_header_left_x0=70.65,
    page_header_right_x1=519.34,
    summary_page_header_right_x1=519.34,
    cover_project_name_erase_above_pt=18.0,
    cover_modality_residue_texts=("放射诊疗设备",),
    cover_project_name_write_below_label=True,
    cover_project_name_line2_above_underline_pt=16.0,
    basic_info_device_count_write_y_offset=-1.0,
    basic_info_device_count_write_x_offset=14.0,
    evaluation_clear_x0=78.5,
    evaluation_write_x0=78.5,
    evaluation_text_indent=True,
    evaluation_text_only_clear=True,
    evaluation_write_y_offset=3.0,
    htmlpdf_basic_info_pdf_field_ids=("f3", "f4", "f5", "f6", "f7", "f8", "f9"),
    contact_pdf_field_ids=("f6", "f7"),
    f1_slot="report_no",
)

# 001-验收检测 / 08-口腔CBCT / cbct、cbct-1（空白 PDF 实测，与 ctc-1 同套封面/基本情况叠印）
CBCT_ACCEPT_REPORT_PROFILE = ReportTemplateProfile(
    code="cbct",
    basic_info_value_x0=175.34,
    basic_info_table_x1=519.34,
    basic_info_project_name_x0=180.65,
    basic_info_project_name_split_at_and=False,
    page_header_left_x0=70.65,
    page_header_right_x1=519.34,
    summary_page_header_right_x1=519.34,
    cover_project_name_erase_above_pt=4.0,
    cover_modality_residue_texts=("放射诊疗设备",),
    cover_project_name_write_below_label=True,
    cover_project_name_line2_above_underline_pt=14.0,
    cover_project_name_write_y_offset=-8.0,
    cover_project_name_dual_underline_cells=True,
    basic_info_device_count_write_y_offset=-1.0,
    basic_info_device_count_write_x_offset=14.0,
    evaluation_clear_x0=78.5,
    evaluation_write_x0=78.5,
    evaluation_text_indent=True,
    evaluation_text_only_clear=True,
    evaluation_write_y_offset=3.0,
    cover_overlay_pdf_field_ids=("f47", "f48", "f49", "f50"),
    htmlpdf_basic_info_pdf_field_ids=("f1", "f2", "f3", "f4", "f5", "f7"),
    contact_pdf_field_ids=("f4", "f5"),
    f1_slot="commission_no",
)

# 001-验收检测 / 05-胃肠机 / task-1（空白 PDF 实测，与 cbct/ctc-1 同套封面/基本情况叠印）
TASK1_ACCEPT_REPORT_PROFILE = ReportTemplateProfile(
    code="task-1",
    basic_info_value_x0=175.34,
    basic_info_table_x1=519.34,
    basic_info_project_name_x0=180.65,
    basic_info_project_name_write_y_offset=-8.0,
    basic_info_project_name_split_at_and=False,
    page_header_left_x0=70.65,
    page_header_right_x1=519.34,
    summary_page_header_right_x1=519.34,
    cover_project_name_erase_above_pt=4.0,
    cover_modality_residue_texts=("放射诊疗设备",),
    cover_project_name_write_below_label=True,
    cover_project_name_line2_above_underline_pt=14.0,
    cover_project_name_write_y_offset=-8.0,
    basic_info_device_count_write_y_offset=-1.0,
    basic_info_device_count_write_x_offset=14.0,
    evaluation_clear_x0=78.5,
    evaluation_write_x0=78.5,
    evaluation_text_indent=True,
    evaluation_text_only_clear=True,
    evaluation_write_y_offset=3.0,
    cover_overlay_pdf_field_ids=("f46", "f62", "f63", "f64"),
    summary_overlay_pdf_field_ids=("f47", "f48", "f49", "f50"),
    htmlpdf_basic_info_pdf_field_ids=("f1", "f2", "f3", "f4", "f5", "f7"),
    contact_pdf_field_ids=("f4", "f5"),
    f1_slot="commission_no",
)

# 001-验收检测 / 02-DR / dr-1（空白 PDF 实测：封面 f2–f5 叠印，基本情况 f6–f12 红字映射）
DR1_ACCEPT_REPORT_PROFILE = ReportTemplateProfile(
    code="dr-1",
    basic_info_value_x0=175.34,
    basic_info_table_x1=519.34,
    basic_info_project_name_x0=180.65,
    page_header_left_x0=70.65,
    page_header_right_x1=519.34,
    summary_page_header_right_x1=519.34,
    cover_project_name_erase_above_pt=18.0,
    cover_modality_residue_texts=("放射诊疗设备",),
    cover_project_name_write_below_label=True,
    cover_project_name_line2_above_underline_pt=16.0,
    basic_info_device_count_write_y_offset=-1.0,
    basic_info_device_count_write_x_offset=14.0,
    evaluation_clear_x0=78.5,
    evaluation_write_x0=78.5,
    evaluation_text_indent=True,
    evaluation_text_only_clear=True,
    evaluation_write_y_offset=3.0,
    cover_overlay_pdf_field_ids=("f2", "f3", "f4", "f5"),
    htmlpdf_basic_info_pdf_field_ids=("f6", "f7", "f8", "f9", "f10", "f11", "f12"),
    contact_pdf_field_ids=("f9", "f10"),
    f1_slot="report_no",
)

# 001-验收检测 / 07-乳腺DR / dr-6（空白 PDF 实测：封面 f46–f49 叠印，基本情况 f1–f7 红字）
DR6_ACCEPT_REPORT_PROFILE = ReportTemplateProfile(
    code="dr-6",
    basic_info_value_x0=175.34,
    basic_info_table_x1=519.34,
    basic_info_project_name_x0=180.65,
    page_header_left_x0=70.65,
    page_header_right_x1=519.34,
    summary_page_header_right_x1=519.34,
    cover_project_name_erase_above_pt=18.0,
    cover_modality_residue_texts=("放射诊疗设备",),
    cover_project_name_write_below_label=True,
    cover_project_name_line2_above_underline_pt=16.0,
    basic_info_device_count_write_y_offset=-1.0,
    basic_info_device_count_write_x_offset=14.0,
    evaluation_clear_x0=78.5,
    evaluation_write_x0=78.5,
    evaluation_text_indent=True,
    evaluation_text_only_clear=True,
    evaluation_write_y_offset=3.0,
    cover_overlay_pdf_field_ids=("f46", "f47", "f48", "f49"),
    summary_overlay_pdf_field_ids=("f50", "f51", "f52", "f53"),
    htmlpdf_basic_info_pdf_field_ids=("f1", "f2", "f3", "f4", "f5", "f6", "f7"),
    contact_pdf_field_ids=("f4", "f5"),
    f1_slot="commission_no",
)

# 001-验收检测 / 06-动态DR / dr-2（空白 PDF 实测，与 ctc-1 同套基本情况列宽）
DR2_ACCEPT_REPORT_PROFILE = ReportTemplateProfile(
    code="dr-2",
    basic_info_value_x0=175.3,
    basic_info_table_x1=519.22,
    basic_info_project_name_x0=175.3,
    basic_info_project_name_split_at_and=False,
    page_header_left_x0=70.65,
    page_header_right_x1=519.34,
    summary_page_header_right_x1=519.34,
    cover_project_name_erase_above_pt=18.0,
    cover_modality_residue_texts=("放射诊疗设备",),
    cover_project_name_write_below_label=True,
    cover_project_name_line2_above_underline_pt=11.0,
    evaluation_clear_x0=78.5,
    evaluation_write_x0=78.5,
    evaluation_text_indent=True,
    evaluation_text_only_clear=True,
    evaluation_write_y_offset=3.0,
    htmlpdf_basic_info_pdf_field_ids=("f1", "f2", "f3"),
    f1_slot="commission_no",
)

# 002-状态检测 / 06动态DR / dr-4（当前空白 PDF 取值列竖线 x≈180.95；旧版字段框 175.3 会压线）
DR4_STATUS_REPORT_PROFILE = ReportTemplateProfile(
    code="dr-4",
    basic_info_value_x0=180.95,
    basic_info_table_x1=519.34,
    basic_info_project_name_x0=180.95,
    basic_info_project_name_write_y_offset=-8.0,
    basic_info_project_name_split_at_and=False,
    page_header_left_x0=70.65,
    page_header_right_x1=519.34,
    summary_page_header_right_x1=519.34,
    cover_project_name_erase_above_pt=18.0,
    cover_modality_residue_texts=("放射诊疗设备",),
    cover_project_name_write_below_label=True,
    cover_project_name_line2_above_underline_pt=11.0,
    evaluation_clear_x0=78.5,
    evaluation_write_x0=78.5,
    evaluation_text_indent=True,
    evaluation_text_only_clear=True,
    evaluation_write_y_offset=3.0,
    htmlpdf_basic_info_pdf_field_ids=("f1", "f2", "f3", "f4", "f5", "f6", "f7"),
    contact_pdf_field_ids=("f4", "f5"),
    f1_slot="commission_no",
)

# 002-状态检测 / 08-口腔CBCT / oral-cbct-status-report（封面 f58–f61 叠印，基本情况 f1–f7 红字）
ORAL_CBCT_STATUS_REPORT_PROFILE = ReportTemplateProfile(
    code="oral-cbct-status-report",
    basic_info_value_x0=175.3,
    basic_info_table_x1=519.34,
    basic_info_project_name_x0=175.3,
    basic_info_project_name_write_y_offset=-8.0,
    basic_info_project_name_split_at_and=False,
    page_header_left_x0=70.65,
    page_header_right_x1=519.34,
    summary_page_header_right_x1=519.34,
    cover_project_name_erase_above_pt=0.0,
    cover_modality_residue_texts=("放射诊疗设备",),
    cover_project_name_write_below_label=False,
    cover_project_name_first_row_align_label=True,
    cover_project_name_line2_above_underline_pt=14.0,
    cover_project_name_write_y_offset=0.0,
    cover_project_name_dual_underline_cells=False,
    basic_info_device_count_write_y_offset=-1.0,
    basic_info_device_count_write_x_offset=0.0,
    evaluation_clear_x0=78.5,
    evaluation_write_x0=78.5,
    evaluation_text_indent=True,
    evaluation_text_only_clear=True,
    evaluation_write_y_offset=3.0,
    cover_overlay_pdf_field_ids=("f58", "f59", "f60", "f61"),
    htmlpdf_basic_info_pdf_field_ids=("f1", "f2", "f3", "f4", "f5", "f6", "f7"),
    contact_pdf_field_ids=("f4", "f5"),
    device_count_include_unit_suffix=False,
    f1_slot="commission_no",
)

# 002-状态检测 / 10口内牙片机 / drct9（封面 f41/f42 叠印，基本情况 f1–f7 红字）
DRCT9_STATUS_REPORT_PROFILE = ReportTemplateProfile(
    code="drct9",
    basic_info_value_x0=175.3,
    basic_info_table_x1=519.34,
    basic_info_project_name_x0=175.3,
    basic_info_project_name_write_y_offset=-8.0,
    basic_info_project_name_split_at_and=False,
    page_header_left_x0=70.65,
    page_header_right_x1=519.34,
    summary_page_header_right_x1=519.34,
    cover_project_name_erase_above_pt=0.0,
    cover_modality_residue_texts=("放射诊疗设备",),
    cover_project_name_write_below_label=False,
    cover_project_name_first_row_align_label=True,
    cover_project_name_line2_above_underline_pt=14.0,
    cover_project_name_write_y_offset=0.0,
    cover_project_name_dual_underline_cells=False,
    basic_info_device_count_write_y_offset=-1.0,
    basic_info_device_count_write_x_offset=0.0,
    evaluation_clear_x0=78.5,
    evaluation_write_x0=78.5,
    evaluation_text_indent=True,
    evaluation_text_only_clear=True,
    evaluation_write_y_offset=3.0,
    cover_overlay_pdf_field_ids=("f41", "f42"),
    htmlpdf_basic_info_pdf_field_ids=("f1", "f2", "f3", "f4", "f5", "f6", "f7"),
    contact_pdf_field_ids=("f4", "f5"),
    device_count_include_unit_suffix=False,
    f1_slot="commission_no",
)

# 002-状态检测 / 09-口腔全景 / panorama-status-report（封面 f27–f30 叠印，基本情况 f1–f7 红字）
PANORAMA_STATUS_REPORT_PROFILE = ReportTemplateProfile(
    code="panorama-status-report",
    basic_info_value_x0=175.3,
    basic_info_table_x1=519.34,
    basic_info_project_name_x0=175.3,
    basic_info_project_name_write_y_offset=-8.0,
    basic_info_project_name_split_at_and=False,
    page_header_left_x0=70.65,
    page_header_right_x1=519.34,
    summary_page_header_right_x1=519.34,
    cover_project_name_erase_above_pt=20.0,
    cover_modality_residue_texts=("放射诊疗设备",),
    cover_project_name_write_below_label=True,
    cover_project_name_line2_above_underline_pt=14.0,
    cover_project_name_write_y_offset=-8.0,
    basic_info_device_count_write_y_offset=-1.0,
    basic_info_device_count_write_x_offset=0.0,
    evaluation_clear_x0=78.5,
    evaluation_write_x0=78.5,
    evaluation_text_indent=True,
    evaluation_text_only_clear=True,
    evaluation_write_y_offset=3.0,
    cover_overlay_pdf_field_ids=("f27", "f28", "f29", "f30"),
    htmlpdf_basic_info_pdf_field_ids=("f1", "f2", "f3", "f4", "f5", "f6", "f7"),
    contact_pdf_field_ids=("f4", "f5"),
    device_count_include_unit_suffix=False,
    f1_slot="commission_no",
)

# 001-验收检测 / 09-口腔全景 / panorama-accept-report（与状态报告同版式）
PANORAMA_ACCEPT_REPORT_PROFILE = ReportTemplateProfile(
    code="panorama-accept-report",
    basic_info_value_x0=175.3,
    basic_info_table_x1=519.34,
    basic_info_project_name_x0=175.3,
    basic_info_project_name_write_y_offset=-8.0,
    basic_info_project_name_split_at_and=False,
    page_header_left_x0=70.65,
    page_header_right_x1=519.34,
    summary_page_header_right_x1=519.34,
    cover_project_name_erase_above_pt=20.0,
    cover_modality_residue_texts=("放射诊疗设备",),
    cover_project_name_write_below_label=True,
    cover_project_name_line2_above_underline_pt=14.0,
    cover_project_name_write_y_offset=-8.0,
    basic_info_device_count_write_y_offset=-1.0,
    basic_info_device_count_write_x_offset=0.0,
    evaluation_clear_x0=78.5,
    evaluation_write_x0=78.5,
    evaluation_text_indent=True,
    evaluation_text_only_clear=True,
    evaluation_write_y_offset=3.0,
    cover_overlay_pdf_field_ids=("f27", "f28", "f29", "f30"),
    htmlpdf_basic_info_pdf_field_ids=("f1", "f2", "f3", "f4", "f5", "f6", "f7"),
    contact_pdf_field_ids=("f4", "f5"),
    device_count_include_unit_suffix=False,
    f1_slot="commission_no",
)

# 002-状态检测 / 05-胃肠机 / task（空白 PDF 实测：基本情况 f1–f7 红字，封面按标签叠印）
TASK_STATUS_REPORT_PROFILE = ReportTemplateProfile(
    code="task",
    basic_info_value_x0=180.95,
    basic_info_table_x1=519.34,
    basic_info_project_name_x0=180.65,
    basic_info_project_name_write_y_offset=-8.0,
    basic_info_project_name_split_at_and=False,
    page_header_left_x0=70.65,
    page_header_right_x1=519.34,
    summary_page_header_right_x1=519.34,
    cover_project_name_erase_above_pt=4.0,
    cover_modality_residue_texts=("放射诊疗设备",),
    cover_project_name_write_below_label=True,
    cover_project_name_line2_above_underline_pt=14.0,
    cover_project_name_write_y_offset=-8.0,
    basic_info_device_count_write_y_offset=-1.0,
    basic_info_device_count_write_x_offset=14.0,
    evaluation_clear_x0=78.5,
    evaluation_write_x0=78.5,
    evaluation_text_indent=True,
    evaluation_text_only_clear=True,
    evaluation_write_y_offset=3.0,
    htmlpdf_basic_info_pdf_field_ids=("f1", "f2", "f3", "f4", "f5", "f6", "f7"),
    contact_pdf_field_ids=("f4", "f5"),
    f1_slot="commission_no",
)

_BASIC_INFO_HTMlPDF_FIELD_LABELS = frozenset(
    {
        "委托编号",
        "受检单位名称",
        "受检单位地址",
        "联系人",
        "联系电话",
        "主要检测人员",
        "委托单位",
        "委托单位名称",
    }
)

REPORT_TEMPLATE_PROFILES: dict[str, ReportTemplateProfile] = {
    CT1_STATUS_REPORT_PROFILE.code: CT1_STATUS_REPORT_PROFILE,
    CBCT_LINAC_STATUS_REPORT_PROFILE.code: CBCT_LINAC_STATUS_REPORT_PROFILE,
    CBCT_ACCEPT_REPORT_PROFILE.code: CBCT_ACCEPT_REPORT_PROFILE,
    "cbct-1": CBCT_ACCEPT_REPORT_PROFILE,
    CTC_ACCEPT_REPORT_PROFILE.code: CTC_ACCEPT_REPORT_PROFILE,
    DR1_ACCEPT_REPORT_PROFILE.code: DR1_ACCEPT_REPORT_PROFILE,
    DR6_ACCEPT_REPORT_PROFILE.code: DR6_ACCEPT_REPORT_PROFILE,
    DSA_STATUS_REPORT_PROFILE.code: DSA_STATUS_REPORT_PROFILE,
    DR2_ACCEPT_REPORT_PROFILE.code: DR2_ACCEPT_REPORT_PROFILE,
    DR4_STATUS_REPORT_PROFILE.code: DR4_STATUS_REPORT_PROFILE,
    TASK1_ACCEPT_REPORT_PROFILE.code: TASK1_ACCEPT_REPORT_PROFILE,
    TASK_STATUS_REPORT_PROFILE.code: TASK_STATUS_REPORT_PROFILE,
    PANORAMA_STATUS_REPORT_PROFILE.code: PANORAMA_STATUS_REPORT_PROFILE,
    PANORAMA_ACCEPT_REPORT_PROFILE.code: PANORAMA_ACCEPT_REPORT_PROFILE,
    ORAL_CBCT_STATUS_REPORT_PROFILE.code: ORAL_CBCT_STATUS_REPORT_PROFILE,
    DRCT9_STATUS_REPORT_PROFILE.code: DRCT9_STATUS_REPORT_PROFILE,
    "dsa": DSA_STATUS_REPORT_PROFILE,
    "dsa-1": DSA_STATUS_REPORT_PROFILE,
}


def _pdf_field_id_sort_key(pid: str) -> int:
    m = re.fullmatch(r"f(\d+)", str(pid or "").strip(), flags=re.IGNORECASE)
    return int(m.group(1)) if m else 10**9


def infer_htmlpdf_basic_info_pdf_field_ids(template_fields: list | None) -> tuple[str, ...]:
    """从模板 pdf.fields 的 id/label 推断基本情况红字映射槽位（未注册 Profile 时兜底）。"""
    seen: set[str] = set()
    out: list[str] = []
    for row in template_fields or []:
        if not isinstance(row, dict):
            continue
        fid = str(row.get("id") or "").strip()
        flabel = str(row.get("label") or row.get("title") or "").strip()
        if fid not in _BASIC_INFO_HTMlPDF_FIELD_LABELS and flabel not in _BASIC_INFO_HTMlPDF_FIELD_LABELS:
            continue
        pdf_fid = str(row.get("pdfFieldId") or "").strip()
        if not pdf_fid:
            continue
        key = pdf_fid.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(pdf_fid)
    return tuple(sorted(out, key=_pdf_field_id_sort_key))


def infer_f1_slot_from_template_fields(template_fields: list | None) -> F1SlotKind | None:
    for row in template_fields or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("pdfFieldId") or "").strip().lower() != "f1":
            continue
        fid = str(row.get("id") or "").strip()
        if fid == "委托编号":
            return "commission_no"
        if fid in ("报告编号",):
            return "report_no"
    return None


def get_report_template_profile(report_task: LibraryTask | None) -> ReportTemplateProfile:
    if report_task is None:
        return DEFAULT_REPORT_TEMPLATE_PROFILE
    code = (getattr(report_task, "code", None) or "").strip()
    if code and code in REPORT_TEMPLATE_PROFILES:
        return REPORT_TEMPLATE_PROFILES[code]
    return DEFAULT_REPORT_TEMPLATE_PROFILE


def resolve_report_template_profile(
    report_task: LibraryTask | None,
    template_fields: list | None = None,
) -> ReportTemplateProfile:
    """
    在注册 Profile 基础上，对未声明 htmlpdf 槽位的模板按字段标签自动推断红字映射，
    避免每上新报告模板都要手调 Profile。
    """
    prof = get_report_template_profile(report_task)
    if not template_fields:
        return prof
    updates: dict[str, object] = {}
    if not prof.htmlpdf_basic_info_pdf_field_ids:
        inferred = infer_htmlpdf_basic_info_pdf_field_ids(template_fields)
        if inferred:
            updates["htmlpdf_basic_info_pdf_field_ids"] = inferred
    if prof is DEFAULT_REPORT_TEMPLATE_PROFILE or not prof.code:
        f1_slot = infer_f1_slot_from_template_fields(template_fields)
        if f1_slot and f1_slot != prof.f1_slot:
            updates["f1_slot"] = f1_slot
    if not updates:
        return prof
    return replace(prof, **updates)
