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

__all__ = [
    "DEFAULT_LAYOUT_PATH",
    "FONT_SIZE_WU_HAO",
    "FONT_SIZE_XIAO_SI",
    "TableLayout",
    "RadiationTableBuilder",
    "default_layout_path",
    "format_complex_location",
    "generate_table_pdf",
    "load_layout",
    "load_report_data",
    "resolve_complex_location",
]
