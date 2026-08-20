#!/usr/bin/env python3
"""Deprecated wrapper — use converter.gbz181_md2latex instead."""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

from converter.gbz181_md2latex import ConvertOptions, build_latex_project, default_output_dir


def convert_auto_folder(
    auto_dir: Path,
    template_dir: Path,
    *,
    md_name: str = "DSA.md",
    output_dir: Path | None = None,
) -> Path:
    warnings.warn(
        "gbz181_from_auto is deprecated; use python -m converter.gbz181_md2latex",
        DeprecationWarning,
        stacklevel=2,
    )
    md_path = auto_dir / md_name
    out = output_dir or default_output_dir(md_path, auto_dir.parent / "latex")
    return build_latex_project(
        md_path,
        out,
        template_dir,
        ConvertOptions(layout="document"),
    )


def main() -> None:
    from converter.gbz181_md2latex import DEFAULT_TEMPLATE_DIR, main as new_main

    warnings.warn(
        "Use: python -m converter.gbz181_md2latex <auto_dir>",
        DeprecationWarning,
        stacklevel=1,
    )
    sys.argv = ["gbz181_md2latex", *sys.argv[1:]]
    new_main()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
