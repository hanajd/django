"""f1_eval_report：GBZ/T 表 F.1 预评价报告表（基础格式 / 表头内容分离）。"""

from f1_eval_report.assemble import assemble_layout, load_base_format, save_layout

__all__ = [
    "assemble_layout",
    "load_base_format",
    "save_layout",
    "generate_f1_eval_pdf",
    "prepare_250375_data",
    "generate_attachments_pdf",
    "append_attachment_pages",
    "format_attachment_list_text",
    "default_attachment_titles",
]


def __getattr__(name: str):
    if name in ("generate_f1_eval_pdf", "prepare_250375_data"):
        from f1_eval_report.generate import generate_f1_eval_pdf, prepare_250375_data

        return {
            "generate_f1_eval_pdf": generate_f1_eval_pdf,
            "prepare_250375_data": prepare_250375_data,
        }[name]
    if name in (
        "generate_attachments_pdf",
        "append_attachment_pages",
        "format_attachment_list_text",
        "default_attachment_titles",
    ):
        from f1_eval_report import attachments as _att

        return getattr(_att, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
