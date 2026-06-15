"""
工作场所放射防护检测结果表格 PDF 生成模块。
"""

from .generator import (
    DEFAULT_LAYOUT_PATH,
    FONT_SIZE_WU_HAO,
    FONT_SIZE_XIAO_SI,
    RadiationTableBuilder,
    TableLayout,
    default_layout_path,
    format_complex_location,
    generate_table_pdf,
    load_layout,
    load_report_data,
    resolve_complex_location,
)
from .report_data_builder import try_build_report_data
from .report_evaluation import apply_report_evaluations, evaluate_result
from .report_pdf_integrator import try_enrich_report_pdf_with_radiation_table

__all__ = [
    "DEFAULT_LAYOUT_PATH",
    "FONT_SIZE_WU_HAO",
    "FONT_SIZE_XIAO_SI",
    "TableLayout",
    "RadiationTableBuilder",
    "apply_report_evaluations",
    "default_layout_path",
    "evaluate_result",
    "format_complex_location",
    "generate_table_pdf",
    "load_layout",
    "load_report_data",
    "resolve_complex_location",
    "try_build_report_data",
    "try_enrich_report_pdf_with_radiation_table",
]
