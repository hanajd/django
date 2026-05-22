"""任务模板检测仪器绑定：质控与防护独立管理（非有序列表勾选）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

SCOPE_QC = "qualityControl"
SCOPE_RP = "radiationProtection"


def normalize_task_bound_instruments(raw: Any) -> Dict[str, Optional[int]]:
    """
    解析 ``LibraryTask.bound_instrument_ids``：
    - 新格式 dict：``{"qualityControl": 12, "radiationProtection": 34}``
    - 旧格式 list：``[qc_id, rp_id]``（仅兼容读取）
    """
    out: Dict[str, Optional[int]] = {SCOPE_QC: None, SCOPE_RP: None}

    def _one(v: Any) -> Optional[int]:
        try:
            n = int(v)
            return n if n > 0 else None
        except (TypeError, ValueError):
            return None

    if isinstance(raw, dict):
        for key, scope in (
            ("qualityControl", SCOPE_QC),
            ("qc", SCOPE_QC),
            ("质控", SCOPE_QC),
            ("radiationProtection", SCOPE_RP),
            ("radiation", SCOPE_RP),
            ("rp", SCOPE_RP),
            ("防护", SCOPE_RP),
        ):
            if key in raw and out[scope] is None:
                out[scope] = _one(raw.get(key))
        return out

    if isinstance(raw, list):
        if len(raw) > 0:
            out[SCOPE_QC] = _one(raw[0])
        if len(raw) > 1:
            out[SCOPE_RP] = _one(raw[1])
        elif out[SCOPE_QC] is not None:
            out[SCOPE_RP] = out[SCOPE_QC]
    return out


def serialize_task_bound_instruments(
    quality_control_id: Any = None,
    radiation_protection_id: Any = None,
) -> Dict[str, int]:
    """写入任务模板的绑定 JSON（仅包含有值的 scope）。"""
    binding = normalize_task_bound_instruments(
        {
            SCOPE_QC: quality_control_id,
            SCOPE_RP: radiation_protection_id,
        }
    )
    out: Dict[str, int] = {}
    if binding[SCOPE_QC] is not None:
        out[SCOPE_QC] = binding[SCOPE_QC]
    if binding[SCOPE_RP] is not None:
        out[SCOPE_RP] = binding[SCOPE_RP]
    return out


def bound_instrument_ids_for_legacy_list(binding: Dict[str, Optional[int]]) -> List[int]:
    """生成根级 ``instruments[]`` 预填顺序：质控在前、防护在后，去重。"""
    ordered: List[int] = []
    seen: set[int] = set()
    for scope in (SCOPE_QC, SCOPE_RP):
        pk = binding.get(scope)
        if pk is None or pk in seen:
            continue
        seen.add(pk)
        ordered.append(pk)
    return ordered
