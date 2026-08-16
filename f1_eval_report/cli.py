#!/usr/bin/env python
"""命令行：组装 layout / 生成 F.1 评价表 PDF。"""

from __future__ import annotations

import argparse
import json
import os
import sys

# 保证仓库根在 path 中
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f1_eval_report.assemble import assemble_layout, save_layout
from f1_eval_report.attachments import (
    default_attachment_titles,
    format_attachment_list_text,
    generate_attachments_pdf,
    load_attachment_format,
)
from f1_eval_report.generate import generate_f1_eval_pdf, prepare_250375_data

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    parser = argparse.ArgumentParser(description="F.1 预评价报告表（格式/内容分离版）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_layout = sub.add_parser("export-layout", help="导出合并后的 layout JSON")
    p_layout.add_argument(
        "--base",
        default=os.path.join(PACKAGE_DIR, "base", "format.json"),
        help="基础表格格式 JSON",
    )
    p_layout.add_argument(
        "--output",
        default=os.path.join(PACKAGE_DIR, "layout", "assembled_layout.json"),
    )
    p_layout.add_argument(
        "--template",
        default="250075YP",
        choices=["gbzt", "250075YP"],
        help="主表格模板：gbzt（标准）或 250075YP",
    )

    p_gen = sub.add_parser("generate", help="生成正文 PDF")
    p_gen.add_argument("--data", required=True, help="数据 JSON")
    p_gen.add_argument("--output", default="f1_eval_output.pdf")
    p_gen.add_argument(
        "--base",
        default=os.path.join(PACKAGE_DIR, "base", "format.json"),
        help="基础表格格式 JSON",
    )
    p_gen.add_argument(
        "--template",
        default="250075YP",
        choices=["gbzt", "250075YP"],
        help="主表格模板：gbzt（标准）或 250075YP",
    )
    p_gen.add_argument(
        "--adapt-250375",
        action="store_true",
        help="将旧版 gbzt_f1_250375.json 字段适配（附图归属放射防护分区）",
    )

    p_att = sub.add_parser("generate-attachments", help="生成附件图册 PDF")
    p_att.add_argument("--data", required=True, help="附件数据 JSON")
    p_att.add_argument("--output", default="attachments_output.pdf")
    p_att.add_argument(
        "--format",
        default=os.path.join(PACKAGE_DIR, "base", "attachment_format.json"),
        help="附件版式 JSON",
    )
    p_att.add_argument("--start-page", type=int, default=1, help="起始页码")

    args = parser.parse_args()
    if args.command == "export-layout":
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        save_layout(
            args.output,
            assemble_layout(base_format_path=args.base, table_template=args.template),
        )
        print(f"Layout written: {args.output} (template={args.template})")
    elif args.command == "generate":
        if args.adapt_250375:
            data = prepare_250375_data(args.data)
            tmp = os.path.join(PACKAGE_DIR, "examples", "_adapted_250375.json")
            os.makedirs(os.path.dirname(tmp), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            generate_f1_eval_pdf(
                args.output,
                tmp,
                base_format_path=args.base,
                table_template=args.template,
            )
            print(f"Adapted data: {tmp}")
        else:
            generate_f1_eval_pdf(
                args.output,
                args.data,
                base_format_path=args.base,
                table_template=args.template,
            )
        print(f"PDF written: {args.output} (template={args.template})")
    elif args.command == "generate-attachments":
        with open(args.data, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 若只有 titles 数组，补全 list 文本字段便于调试
        if not data.get("attachment_pages") and data.get("pages"):
            data["attachment_pages"] = data["pages"]
        generate_attachments_pdf(
            data,
            args.output,
            format_path=args.format,
            start_page_no=args.start_page,
        )
        print(f"Attachments PDF written: {args.output}")
        fmt = load_attachment_format(args.format)
        items = data.get("attachments_list_items") or default_attachment_titles(
            project_full_name=str(data.get("project_full_name") or ""),
            fmt=fmt,
        )
        print("--- F.1 附件栏文本预览 ---")
        print(
            format_attachment_list_text(
                items,
                fmt=fmt,
                project_full_name=str(data.get("project_full_name") or ""),
            )
        )


if __name__ == "__main__":
    main()
