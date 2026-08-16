# -*- coding: utf-8 -*-
"""表内勾选项：模板定义 + 与 PDF 文本（☑/□）互转。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence, Set

CHECKED = "☑"
UNCHECKED = "□"

# 项目性质 / 建设性质（GBZ/T 表 F.1）：两行五选项
CONSTRUCTION_NATURE_OPTIONS: List[Dict[str, Any]] = [
    {"label": "新建", "line": 0},
    {"label": "扩建", "line": 0},
    {"label": "改建", "line": 0},
    {"label": "技术引进", "line": 1},
    {"label": "技术改造", "line": 1},
]

CONSTRUCTION_NATURE_FORMAT: Dict[str, Any] = {
    "mode": "center",
    "widget": "checkbox_group",
    "options": CONSTRUCTION_NATURE_OPTIONS,
    # 同行内连接：第 0 行无空格，第 1 行选项之间一个空格（与样例 PDF 一致）
    "line_sep": {0: "", 1: " "},
}


def parse_checkbox_group(
    text: str,
    options: Sequence[Dict[str, Any]] | None = None,
) -> List[str]:
    """从「标签☑/□」文本解析已勾选项标签列表。"""
    opts = list(options or CONSTRUCTION_NATURE_OPTIONS)
    raw = str(text or "")
    selected: List[str] = []
    for opt in opts:
        lab = str(opt.get("label") or "")
        if not lab:
            continue
        m = re.search(re.escape(lab) + r"\s*([☑■✓√]|□|▢|☐)", raw)
        if m and m.group(1) in ("☑", "■", "✓", "√"):
            selected.append(lab)
    return selected


def format_checkbox_group(
    selected: Sequence[str] | Set[str] | None,
    options: Sequence[Dict[str, Any]] | None = None,
    line_sep: Dict[int, str] | None = None,
) -> str:
    """序列化为 PDF 用两行勾选文本。"""
    opts = list(options or CONSTRUCTION_NATURE_OPTIONS)
    sep_map = {int(k): str(v) for k, v in (line_sep or {0: "", 1: " "}).items()}
    chosen = set(selected or [])
    by_line: Dict[int, List[str]] = {}
    for opt in opts:
        lab = str(opt.get("label") or "")
        if not lab:
            continue
        line = int(opt.get("line") or 0)
        mark = CHECKED if lab in chosen else UNCHECKED
        by_line.setdefault(line, []).append(lab + mark)
    lines: List[str] = []
    for line_no in sorted(by_line.keys()):
        joiner = sep_map.get(line_no, "")
        lines.append(joiner.join(by_line[line_no]))
    return "\n".join(lines)


def default_construction_nature_text() -> str:
    return format_checkbox_group([])
