#!/usr/bin/env python3
"""统一命令行入口。"""

import argparse
import json
import os
import sys

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.dirname(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, os.path.dirname(PACKAGE_DIR))

from radiation_detection_report.extract_layout import DEFAULT_LAYOUT_OUT, extract_layout
from radiation_detection_report.extract_tabletest_chapter5 import (
    DEFAULT_CHAPTER5_OUT,
    extract_chapter5_full,
)
from radiation_detection_report.extract_tabletest_layout import (
    DEFAULT_LAYOUT_OUT as TABLETEST_LAYOUT_OUT,
    extract_tabletest_layout,
)
from radiation_detection_report.field_record_generator import (
    DEFAULT_FIELD_LAYOUT_PATH,
    generate_field_record_pdf,
)
from radiation_detection_report.generator import DEFAULT_LAYOUT_PATH, generate_table_pdf
from radiation_detection_report.sample_data_generator import DEFAULT_SAMPLE_OUT, build_report


def main() -> None:
    parser = argparse.ArgumentParser(description="工作场所放射防护检测结果表格工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate", help="根据数据 JSON 生成报告表 PDF")
    p_gen.add_argument("--data", required=True, help="报告数据 JSON")
    p_gen.add_argument("--layout", default=DEFAULT_LAYOUT_PATH, help="版式 JSON")
    p_gen.add_argument("--output", default="output.pdf", help="输出 PDF")

    p_fr = sub.add_parser("generate-field-record", help="生成现场记录表 PDF（JXFS/JS-009 横版）")
    p_fr.add_argument("--data", required=True, help="现场记录数据 JSON")
    p_fr.add_argument("--layout", default=DEFAULT_FIELD_LAYOUT_PATH, help="版式 JSON")
    p_fr.add_argument("--output", default="field_record_output.pdf", help="输出 PDF")

    p_ext = sub.add_parser("extract-layout", help="从模板 PDF 提取版式 JSON")
    p_ext.add_argument("--input", required=True, help="模板 PDF")
    p_ext.add_argument("--page", type=int, default=6, help="参考页码（1-based）")
    p_ext.add_argument("--output", default=DEFAULT_LAYOUT_OUT, help="输出版式 JSON")

    p_ch5 = sub.add_parser(
        "extract-chapter5",
        help="提取第五章全部表格（至第六章前）：版式+检测点模板+本底+注释",
    )
    p_ch5.add_argument("--input", required=True, help="模板 PDF（如 tabletest.pdf）")
    p_ch5.add_argument("--output", default=DEFAULT_CHAPTER5_OUT)
    p_ch5.add_argument("--compact", action="store_true", help="不输出 raw_rows")

    p_tt = sub.add_parser("extract-tabletest-layout", help="从 JXFS/JS-009 横版 PDF 提取第五章首页表格版式")
    p_tt.add_argument("--input", required=True, help="模板 PDF（如 tabletest.pdf）")
    p_tt.add_argument("--page", type=int, default=None, help="参考页码，默认自动定位第五章")
    p_tt.add_argument("--output", default=TABLETEST_LAYOUT_OUT)

    p_smp = sub.add_parser("sample-data", help="生成示例/测试用数据 JSON")
    p_smp.add_argument("--output", default=DEFAULT_SAMPLE_OUT)
    p_smp.add_argument("--count", type=int, default=100)
    p_smp.add_argument("--seed", type=int, default=42)

    args = parser.parse_args()
    if args.command == "generate":
        generate_table_pdf(args.layout, args.data, args.output)
        print(f"PDF written: {args.output}")
    elif args.command == "generate-field-record":
        generate_field_record_pdf(args.layout, args.data, args.output)
        print(f"PDF written: {args.output}")
    elif args.command == "extract-layout":
        layout = extract_layout(args.input, args.page)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(layout, f, ensure_ascii=False, indent=2)
        print(f"Layout written: {args.output}")
    elif args.command == "extract-chapter5":
        data = extract_chapter5_full(args.input)
        if args.compact:
            data.pop("raw_rows", None)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        r = data["chapter_range"]
        print(f"Chapter 5 pages: {r['start_page']}-{r['end_page']}")
        print(f"Written: {args.output}")
    elif args.command == "extract-tabletest-layout":
        layout = extract_tabletest_layout(args.input, args.page)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(layout, f, ensure_ascii=False, indent=2)
        print(f"Layout written: {args.output}")
    elif args.command == "sample-data":
        data = build_report(total=args.count, seed=args.seed)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"JSON written: {args.output}")


if __name__ == "__main__":
    main()
