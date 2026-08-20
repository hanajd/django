#!/usr/bin/env python3
"""Extract project variables from Markdown using project_keywords.csv and update main.tex."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from converter.project_vars import (
    extract_project_values,
    load_keywords,
    patch_main_tex,
    render_newcommand_block,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_KEYWORDS = ROOT / "data" / "project_keywords.csv"


def print_summary_table(keywords, values: dict[str, str]) -> None:
    print("\n| 命令 | 中文关键词 | 分类 | 提取值 |")
    print("|------|------------|------|--------|")
    for item in keywords:
        if item.command == "reporttype":
            val = values.get("radiationhazard", "") + values.get("reportkind", "")
        else:
            val = values.get(item.command, item.default_value)
        display = val if val else "（空）"
        print(f"| `{item.command}` | {item.label_cn} | {item.category} | {display} |")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="从 Markdown 检索关键词，更新 LaTeX main.tex 中的 \\newcommand 项目变量。"
    )
    parser.add_argument("markdown", help="输入 Markdown 报告文件")
    parser.add_argument(
        "--keywords",
        default=str(DEFAULT_KEYWORDS),
        help="关键词表 CSV（默认 project_keywords.csv）",
    )
    parser.add_argument(
        "--main-tex",
        help="要更新的 main.tex（默认 latex/<项目名>/main.tex 或同目录 main.tex）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印提取结果，不写文件",
    )
    args = parser.parse_args()

    md_path = Path(args.markdown).resolve()
    keywords_path = Path(args.keywords).resolve()
    if not md_path.is_file():
        parser.error(f"Markdown not found: {md_path}")
    if not keywords_path.is_file():
        parser.error(f"Keywords CSV not found: {keywords_path}")

    keywords = load_keywords(keywords_path)
    markdown_text = md_path.read_text(encoding="utf-8")
    values = extract_project_values(markdown_text, keywords)
    command_block = render_newcommand_block(values, keywords)

    print_summary_table(keywords, values)

    if args.dry_run:
        print("\n--- 生成的 \\newcommand 块 ---\n")
        print(command_block)
        return

    if args.main_tex:
        main_tex = Path(args.main_tex).resolve()
    else:
        main_tex = ROOT / "latex" / "yp250420" / "main.tex"
        if not main_tex.is_file():
            main_tex = md_path.with_name("main.tex")

    if not main_tex.is_file():
        parser.error(f"main.tex not found: {main_tex}")

    patch_main_tex(main_tex, command_block)
    print(f"\n已更新: {main_tex}")


if __name__ == "__main__":
    main()
