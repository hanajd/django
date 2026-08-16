# -*- coding: utf-8 -*-
"""独立插页页面模式（A3/A4 × 横/竖）与尺寸。"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

# PDF 点（pt）
PAGE_MODE_SIZES_PT: Dict[str, Tuple[float, float]] = {
    "a4_portrait": (595.3, 841.9),
    "a4_landscape": (841.9, 595.3),
    "a3_portrait": (841.9, 1190.55),
    "a3_landscape": (1190.55, 841.9),
}

PAGE_MODE_LABELS: Dict[str, str] = {
    "a4_portrait": "A4 纵向",
    "a4_landscape": "A4 横向",
    "a3_portrait": "A3 纵向",
    "a3_landscape": "A3 横向",
}

DEFAULT_FIGURE_PAGE_MODE = "a3_landscape"


def page_mode_options() -> List[Dict[str, str]]:
    return [{"key": k, "label": PAGE_MODE_LABELS[k]} for k in PAGE_MODE_SIZES_PT]


def resolve_page_size_pt(page_mode: str | None = None, figure: Dict[str, Any] | None = None) -> Tuple[float, float]:
    fig = figure or {}
    if fig.get("page_width_pt") and fig.get("page_height_pt"):
        try:
            return float(fig["page_width_pt"]), float(fig["page_height_pt"])
        except (TypeError, ValueError):
            pass
    mode = str(page_mode or fig.get("page_mode") or DEFAULT_FIGURE_PAGE_MODE).strip()
    if mode not in PAGE_MODE_SIZES_PT:
        mode = DEFAULT_FIGURE_PAGE_MODE
    return PAGE_MODE_SIZES_PT[mode]


def apply_page_mode_to_figure(fig: Dict[str, Any], page_mode: str | None = None) -> Dict[str, Any]:
    out = dict(fig or {})
    mode = str(page_mode or out.get("page_mode") or DEFAULT_FIGURE_PAGE_MODE).strip()
    if mode not in PAGE_MODE_SIZES_PT:
        mode = DEFAULT_FIGURE_PAGE_MODE
    w, h = PAGE_MODE_SIZES_PT[mode]
    out["page_mode"] = mode
    out["page_width_pt"] = w
    out["page_height_pt"] = h
    return out
