"""
工作场所布局：正文 + 嵌套表格 + 横向 A3 附图页。
"""

from __future__ import annotations

import os
from typing import Any, Dict

import fitz

from radiation_detection_report.generator import (
    _paragraph_line_entries,
    _register_fonts,
    _text_ascent,
)
from radiation_detection_report.gbzt_f1_content_embed import F1ContentEmbedBuilder

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_A3_LANDSCAPE_W = 1190.55
DEFAULT_A3_LANDSCAPE_H = 841.9


def _f1_text_descent(font_size: float) -> float:
    return font_size * 0.22


def _f1_text_block_height(n_lines: int, font_size: float, line_spacing: float) -> float:
    if n_lines <= 0:
        return 0.0
    from radiation_detection_report.generator import _text_ascent as ascent_fn

    return (n_lines - 1) * line_spacing + ascent_fn(font_size) + _f1_text_descent(font_size)


def _f1_register_fonts(page: fitz.Page, base: Any) -> Any:
    res = _register_fonts(page, base)
    res.times = res.song
    res.symbol = res.song
    if res.song_metric:
        res.times_metric = res.song_metric
        res.symbol_metric = res.song_metric
    return res


def _resolve_image_path(image_rel: str) -> str:
    if os.path.isabs(image_rel) and os.path.isfile(image_rel):
        return image_rel
    candidates = [
        os.path.join(PACKAGE_DIR, "examples", image_rel),
        image_rel,
        os.path.join(os.getcwd(), image_rel),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return os.path.join(PACKAGE_DIR, "examples", image_rel)


def insert_landscape_figure_page(host: Any, figure: Dict[str, Any]) -> None:
    """委托统一 A3 附图实现（铺满值区、无多余空白页）。"""
    from f1_eval_report.embed.landscape import insert_landscape_figure_page as _insert

    _insert(host, figure)


def embed_workplace_layout(host: Any, row_def: Dict[str, Any], embed: Dict[str, Any]) -> None:
    merged = dict(embed)
    merged.setdefault("text_key", "workplace_layout_intro")
    merged.setdefault("tables_key", "workplace_layout_tables")
    merged["figure_key"] = "workplace_layout_figure"
    F1ContentEmbedBuilder(host, row_def, merged).run()
