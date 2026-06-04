"""
根据 PDF 表格「判定标准」单元格常见写法，对「检测结果」数值推断「合格 / 不合格」。

支持：
- 比较：≥ / <= / > / < 及中文同义（大于等于、小于等于、不超过、不低于等）
- 区间：a～b、a~b、a-b（两侧均为数字时）
- 对称允差：n±m（含 ± 与 +/- 变体）

无法解析或缺少有效数值时返回 None，由调用方决定是否保留人工映射值。
"""

from __future__ import annotations

import re
import unicodedata
from typing import List, Optional

_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９．，％", "0123456789.,%")

_FLOAT_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def normalize_criterion_text(s: str) -> str:
    t = unicodedata.normalize("NFKC", str(s or "")).translate(_FULLWIDTH_DIGITS)
    t = t.replace("（", "(").replace("）", ")")
    t = t.replace("≦", "≤").replace("≧", "≥")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def parse_first_number(s: str) -> Optional[float]:
    if s is None:
        return None
    t = unicodedata.normalize("NFKC", str(s)).translate(_FULLWIDTH_DIGITS)
    m = _FLOAT_RE.search(t)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _cmp_le(v: float, limit: float) -> bool:
    return v <= limit + 1e-9


def _cmp_ge(v: float, limit: float) -> bool:
    return v >= limit - 1e-9


def _cmp_lt(v: float, limit: float) -> bool:
    return v < limit - 1e-9


def _cmp_gt(v: float, limit: float) -> bool:
    return v > limit + 1e-9


def _match_le(crit: str) -> Optional[float]:
    for pat in (
        r"(?:≤|<=|＜＝|不大于|不超过|小于等于)\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)",
        r"(?:小于|低于)\s*或\s*等于\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)",
    ):
        m = re.search(pat, crit)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
    return None


def _match_ge(crit: str) -> Optional[float]:
    for pat in (
        r"(?:≥|>=|＞＝|不小于|不低于|大于等于)\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)",
        r"(?:大于|高于)\s*或\s*等于\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)",
    ):
        m = re.search(pat, crit)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
    return None


def _match_strict_lt(crit: str) -> Optional[float]:
    m = re.search(r"(?:<|＜)(?!=)\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)", crit)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    if re.search(r"小于(?!等于)", crit) and "小于或等于" not in crit:
        return parse_first_number(crit)
    return None


def _match_strict_gt(crit: str) -> Optional[float]:
    m = re.search(r"(?:>|＞)(?!=)\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)", crit)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    if re.search(r"大于(?!等于)", crit) and "大于或等于" not in crit:
        return parse_first_number(crit)
    return None


def _eval_simple(v: float, crit: str) -> Optional[bool]:
    c = crit
    # 仅允差：±m（中心值按 0 处理，便于模板里写「±2」）
    m = re.search(
        r"^[±]\s*([-+]?(?:\d+\.?\d*|\.\d+))(?:\s|$)",
        c.strip(),
    )
    if m:
        try:
            tol = abs(float(m.group(1)))
            return abs(v) <= tol + 1e-9
        except ValueError:
            return None
    m = re.search(
        r"^\+/\-\s*([-+]?(?:\d+\.?\d*|\.\d+))(?:\s|$)",
        c.strip(),
        re.IGNORECASE,
    )
    if m:
        try:
            tol = abs(float(m.group(1)))
            return abs(v) <= tol + 1e-9
        except ValueError:
            return None
    # n±m / n ± m
    m = re.search(
        r"([-+]?(?:\d+\.?\d*|\.\d+))\s*[±]\s*([-+]?(?:\d+\.?\d*|\.\d+))",
        c,
    )
    if m:
        try:
            center = float(m.group(1))
            tol = abs(float(m.group(2)))
            return abs(v - center) <= tol + 1e-9
        except ValueError:
            return None
    m = re.search(
        r"([-+]?(?:\d+\.?\d*|\.\d+))\s*\+/\-\s*([-+]?(?:\d+\.?\d*|\.\d+))",
        c,
    )
    if m:
        try:
            center = float(m.group(1))
            tol = abs(float(m.group(2)))
            return abs(v - center) <= tol + 1e-9
        except ValueError:
            return None

    # 区间：～、~、至、到、连字符（不含 ± 场景）
    if "±" not in c and "+/-" not in c.lower():
        m = re.search(
            r"([-+]?(?:\d+\.?\d*|\.\d+))\s*(?:[~～]|至|到|[-−－])\s*([-+]?(?:\d+\.?\d*|\.\d+))",
            c,
        )
        if m:
            try:
                a = float(m.group(1))
                b = float(m.group(2))
            except ValueError:
                return None
            lo, hi = (a, b) if a <= b else (b, a)
            return lo - 1e-9 <= v <= hi + 1e-9

    lim = _match_le(c)
    if lim is not None:
        return _cmp_le(v, lim)
    lim = _match_ge(c)
    if lim is not None:
        return _cmp_ge(v, lim)
    lim = _match_strict_lt(c)
    if lim is not None:
        return _cmp_lt(v, lim)
    lim = _match_strict_gt(c)
    if lim is not None:
        return _cmp_gt(v, lim)

    return None


def _split_compound(crit: str) -> List[str]:
    parts = re.split(r"(?:且|并且|；|;)", crit)
    return [p.strip() for p in parts if p.strip()]


def verdict_from_measurement(measured: str, criterion: str) -> Optional[str]:
    """
    若可解析则返回「合格」或「不合格」，否则 None。
    """
    crit_raw = normalize_criterion_text(criterion)
    if not crit_raw:
        return None
    v = parse_first_number(measured)
    if v is None:
        return None

    chunks = _split_compound(crit_raw)
    if len(chunks) == 1:
        r = _eval_simple(v, chunks[0])
        if r is None:
            return None
        return "合格" if r else "不合格"

    results: List[Optional[bool]] = []
    for ch in chunks:
        results.append(_eval_simple(v, ch))
    if any(x is None for x in results):
        return None
    ok = all(bool(x) for x in results)
    return "合格" if ok else "不合格"
