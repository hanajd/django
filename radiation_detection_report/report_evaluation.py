"""
工作场所放射防护检测结果：报出值与标准要求比较，判定合格/不合格。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional

DEFAULT_RADIATION_STANDARD = "≤2.5"

_NUM_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _first_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s == "/":
        return None
    m = _NUM_RE.search(s.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _parse_standard_limit(standard: str) -> Optional[float]:
    s = (standard or "").strip() or DEFAULT_RADIATION_STANDARD
    m = re.search(r"≤\s*([-+]?\d*\.?\d+)", s)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def evaluate_result(result: Any, standard: str = DEFAULT_RADIATION_STANDARD) -> str:
    """解析 result 中第一个数字，与标准要求比较。"""
    val = _first_number(result)
    limit = _parse_standard_limit(standard)
    if val is None or limit is None:
        return ""
    return "合格" if val <= limit else "不合格"


def _apply_row_eval(row: Dict[str, Any], *, default_standard: str) -> None:
    std = str(row.get("standard") or default_standard).strip() or default_standard
    row["standard"] = std
    if not str(row.get("evaluation") or "").strip():
        row["evaluation"] = evaluate_result(row.get("result"), std)


def _evaluate_points_list(points: Any, *, default_std: str) -> list:
    new_points: list = []
    if not isinstance(points, list):
        return new_points
    for pt in points:
        if not isinstance(pt, dict):
            continue
        item = dict(pt)
        if item.get("type") == "complex":
            subs = []
            for sub in item.get("sub_rows") or []:
                if not isinstance(sub, dict):
                    continue
                row = dict(sub)
                _apply_row_eval(row, default_standard=default_std)
                subs.append(row)
            item["sub_rows"] = subs
        else:
            _apply_row_eval(item, default_standard=default_std)
        new_points.append(item)
    return new_points


def apply_report_evaluations(data: Mapping[str, Any]) -> Dict[str, Any]:
    """遍历 simple/complex 子行，补全 standard 与 evaluation。"""
    if not isinstance(data, dict):
        return {}
    out = dict(data)
    default_std = DEFAULT_RADIATION_STANDARD
    out["points"] = _evaluate_points_list(out.get("points"), default_std=default_std)
    if isinstance(out.get("points_group2"), list):
        out["points_group2"] = _evaluate_points_list(out.get("points_group2"), default_std=default_std)
    return out
