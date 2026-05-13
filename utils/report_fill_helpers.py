"""
报告 PDF 自动回填辅助：联系人/电话拆分、模板数字与现场记录数字对齐等。
"""

from __future__ import annotations

import re
from typing import Tuple

# 常见大陆手机/固话（宽松）
_PHONE_RE = re.compile(
    r"(?:\+?86[\s\-]?)?(?:1[3-9]\d{9}|0\d{2,3}[\s\-]?\d{7,8}|[\d\-\s（）\(\)]{8,24})"
)


def split_contact_name_phone(contact_person: str, contact_phone: str) -> Tuple[str, str]:
    """
    现场记录常为「委托单位联系人/电话」一格：中文姓名 + 号码。
    若 contactPhone 已有值则直接使用；否则从 contactPerson 中拆出号码与姓名。
    """
    cp = (contact_person or "").strip()
    ph = (contact_phone or "").strip()
    if ph:
        return cp, ph
    if not cp:
        return "", ""
    m = _PHONE_RE.search(cp)
    if m:
        phone = re.sub(r"\s+", " ", m.group(0)).strip()
        name = (cp[: m.start()] + cp[m.end() :]).strip()
        name = re.sub(r"[/／|，,\s]+$", "", name).strip()
        return name, phone
    digits_only = re.sub(r"\D", "", cp)
    if len(digits_only) >= 7 and not re.search(r"[\u4e00-\u9fff]", cp):
        return "", cp.strip()
    return cp, ""


def merge_number_tokens_from_source(template: str, source: str) -> str:
    """
    将 template 中从左到右的数字串，按顺序替换为 source 中提取的数字串（现场记录）。
    模板中多出的数字位保留原样；source 中数字不足时保留 template 剩余数字。
    """
    tmpl = str(template or "")
    src = str(source or "")
    if not tmpl or not src:
        return tmpl
    nums = re.findall(r"-?\d+\.?\d*", src)
    if not nums:
        return tmpl
    idx = 0

    def repl(_m: re.Match) -> str:
        nonlocal idx
        if idx >= len(nums):
            return _m.group(0)
        out = nums[idx]
        idx += 1
        return out

    return re.sub(r"-?\d+\.?\d*", repl, tmpl)
