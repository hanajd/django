"""
报告表「结果评价」：根据报出值与标准要求（默认 ≤2.5 μSv/h）判定合格/不合格。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_RADIATION_STANDARD = "≤2.5"


def parse_numeric_value(text: Any) -> Optional[float]:
    if text is None:
        return None
    s = re.sub(r"\s+", "", str(text).strip())
    if not s or s in ("/", "-", "—"):
        return None
    m = re.search(r"[\d.]+", s)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def parse_standard_limit(standard: str) -> Tuple[str, float]:
    s = str(standard or DEFAULT_RADIATION_STANDARD).strip().replace(" ", "")
    s = s.replace("μSv/h", "").replace("μsv/h", "")
    limit_m = re.search(r"([\d.]+)", s)
    limit = float(limit_m.group(1)) if limit_m else 2.5
    if "≤" in s or "<=" in s.lower():
        return ("<=", limit)
    if "＜" in s or re.search(r"(?<![=<>])<(?!=)", s):
        return ("<", limit)
    if "≥" in s or ">=" in s.lower():
        return (">=", limit)
    if "＞" in s or re.search(r"(?<![=<>])>(?!=)", s):
        return (">", limit)
    return ("<=", limit)


def evaluate_result(result: Any, standard: str = DEFAULT_RADIATION_STANDARD) -> str:
    value = parse_numeric_value(result)
    if value is None:
        return ""
    op, limit = parse_standard_limit(standard)
    if op == "<=":
        ok = value <= limit
    elif op == "<":
        ok = value < limit
    elif op == ">=":
        ok = value >= limit
    else:
        ok = value > limit
    return "合格" if ok else "不合格"


def apply_report_evaluations(
    data: Dict[str, Any],
    *,
    default_standard: str = DEFAULT_RADIATION_STANDARD,
) -> Dict[str, Any]:
    for point in data.get("points", []):
        if point.get("type") == "complex":
            for sub in point.get("sub_rows", []):
                std = str(sub.get("standard") or default_standard)
                sub["standard"] = std
                sub["evaluation"] = evaluate_result(sub.get("result", ""), std)
        else:
            std = str(point.get("standard") or default_standard)
            point["standard"] = std
            point["evaluation"] = evaluate_result(point.get("result", ""), std)
    return data
