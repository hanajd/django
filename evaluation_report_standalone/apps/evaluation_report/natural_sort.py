"""文件名自然排序（02_3 排在 02_10 前）。"""

from __future__ import annotations

import re

_PART = re.compile(r"(\d+|\D+)")


def natural_sort_key(text: str) -> tuple:
    parts: list[tuple[int, int | str]] = []
    for part in _PART.findall(text.lower()):
        if part.isdigit():
            parts.append((0, int(part)))
        elif part:
            parts.append((1, part))
    return tuple(parts)
