"""任务模板检测仪器：模板层绑定「种类」，项目派工时解析为具体编号。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

SCOPE_QC = "qualityControl"
SCOPE_RP = "radiationProtection"
BINDING_MODE_KINDS = "kinds"
BINDING_MODE_IDS = "ids"
KIND_SEP = "\x1e"  # 表单 kind 键 name+model 分隔符


@dataclass(frozen=True)
class InstrumentKindSpec:
    name: str
    model: str = ""

    def key(self) -> Tuple[str, str]:
        return ((self.name or "").strip(), (self.model or "").strip())


def _one_id(v: Any) -> Optional[int]:
    try:
        n = int(v)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _many_ids(v: Any) -> List[int]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        out: List[int] = []
        seen: set[int] = set()
        for item in v:
            if isinstance(item, dict):
                continue
            pk = _one_id(item)
            if pk is not None and pk not in seen:
                seen.add(pk)
                out.append(pk)
        return out
    pk = _one_id(v)
    return [pk] if pk is not None else []


def _kind_from_dict(d: dict) -> InstrumentKindSpec | None:
    if not isinstance(d, dict):
        return None
    name = str(d.get("name") or "").strip()
    if not name:
        return None
    return InstrumentKindSpec(name=name, model=str(d.get("model") or "").strip())


def _kinds_from_scope_value(v: Any) -> List[InstrumentKindSpec]:
    if v is None:
        return []
    if isinstance(v, dict) and "name" in v:
        one = _kind_from_dict(v)
        return [one] if one else []
    if not isinstance(v, (list, tuple)):
        return []
    out: List[InstrumentKindSpec] = []
    seen: set[Tuple[str, str]] = set()
    for item in v:
        if isinstance(item, dict):
            spec = _kind_from_dict(item)
        elif isinstance(item, str) and KIND_SEP in item:
            name, _, model = item.partition(KIND_SEP)
            spec = InstrumentKindSpec(name=name.strip(), model=model.strip())
        else:
            continue
        if spec is None:
            continue
        k = spec.key()
        if k in seen:
            continue
        seen.add(k)
        out.append(spec)
    return out


def binding_mode(raw: Any) -> str:
    if isinstance(raw, dict):
        mode = str(raw.get("bindingMode") or "").strip().lower()
        if mode == BINDING_MODE_KINDS:
            return BINDING_MODE_KINDS
        for scope in (SCOPE_QC, SCOPE_RP):
            val = raw.get(scope)
            if isinstance(val, list) and val and isinstance(val[0], dict) and "name" in val[0]:
                return BINDING_MODE_KINDS
        if any(_many_ids(raw.get(scope)) for scope in (SCOPE_QC, SCOPE_RP) if scope in raw):
            return BINDING_MODE_IDS
        if any(_kinds_from_scope_value(raw.get(scope)) for scope in (SCOPE_QC, SCOPE_RP) if scope in raw):
            return BINDING_MODE_KINDS
    if isinstance(raw, list) and raw and not isinstance(raw[0], dict):
        return BINDING_MODE_IDS
    return BINDING_MODE_KINDS


def normalize_task_bound_kinds(raw: Any) -> Dict[str, List[InstrumentKindSpec]]:
    out: Dict[str, List[InstrumentKindSpec]] = {SCOPE_QC: [], SCOPE_RP: []}
    if not isinstance(raw, dict):
        return out
    for key, scope in (
        ("qualityControl", SCOPE_QC),
        ("qc", SCOPE_QC),
        ("radiationProtection", SCOPE_RP),
        ("radiation", SCOPE_RP),
        ("rp", SCOPE_RP),
    ):
        if key in raw:
            merged = out[scope] + _kinds_from_scope_value(raw.get(key))
            seen: set[Tuple[str, str]] = set()
            deduped: List[InstrumentKindSpec] = []
            for spec in merged:
                k = spec.key()
                if k not in seen:
                    seen.add(k)
                    deduped.append(spec)
            out[scope] = deduped
    return out


def normalize_task_bound_instruments(raw: Any) -> Dict[str, List[int]]:
    """
    解析为具体仪器主键（仅 ``bindingMode=ids`` 或旧版列表；种类模式返回空列表）。
    项目已分配编号请用 ``normalize_project_assigned_instruments``。
    """
    if binding_mode(raw) == BINDING_MODE_KINDS:
        return {SCOPE_QC: [], SCOPE_RP: []}

    out: Dict[str, List[int]] = {SCOPE_QC: [], SCOPE_RP: []}
    if isinstance(raw, dict):
        for key, scope in (
            ("qualityControl", SCOPE_QC),
            ("qc", SCOPE_QC),
            ("radiationProtection", SCOPE_RP),
            ("radiation", SCOPE_RP),
            ("rp", SCOPE_RP),
        ):
            if key in raw:
                merged = out[scope] + _many_ids(raw.get(key))
                seen: set[int] = set()
                deduped: List[int] = []
                for pk in merged:
                    if pk not in seen:
                        seen.add(pk)
                        deduped.append(pk)
                out[scope] = deduped
        return out

    if isinstance(raw, list):
        parsed: List[int] = []
        seen_list: set[int] = set()
        for x in raw:
            pk = _one_id(x)
            if pk is not None and pk not in seen_list:
                seen_list.add(pk)
                parsed.append(pk)
        if len(parsed) == 1:
            out[SCOPE_QC] = list(parsed)
            out[SCOPE_RP] = list(parsed)
        elif len(parsed) == 2:
            out[SCOPE_QC] = [parsed[0]]
            out[SCOPE_RP] = [parsed[1]]
        elif len(parsed) > 2:
            out[SCOPE_QC] = parsed
    return out


def normalize_project_assigned_instruments(raw: Any) -> Dict[str, List[int]]:
    """项目派工后落库的具体编号（质控/防护 id 列表）。"""
    return normalize_task_bound_instruments(
        {**({"bindingMode": BINDING_MODE_IDS} if isinstance(raw, dict) else {}), **(raw if isinstance(raw, dict) else {})}
    )


def serialize_task_bound_kinds(
    *,
    quality_control_kinds: List[InstrumentKindSpec] | None = None,
    radiation_protection_kinds: List[InstrumentKindSpec] | None = None,
) -> dict:
    out: dict = {"bindingMode": BINDING_MODE_KINDS}
    if quality_control_kinds:
        out[SCOPE_QC] = [{"name": k.name, "model": k.model} for k in quality_control_kinds]
    if radiation_protection_kinds:
        out[SCOPE_RP] = [{"name": k.name, "model": k.model} for k in radiation_protection_kinds]
    return out


def serialize_task_bound_instruments(
    *,
    quality_control_ids: Any = None,
    radiation_protection_ids: Any = None,
    quality_control_id: Any = None,
    radiation_protection_id: Any = None,
) -> Dict[str, List[int]]:
    """旧版：模板直接绑编号（仍兼容）。"""
    qc = _many_ids(quality_control_ids)
    if not qc and quality_control_id is not None:
        qc = _many_ids(quality_control_id)
    rp = _many_ids(radiation_protection_ids)
    if not rp and radiation_protection_id is not None:
        rp = _many_ids(radiation_protection_id)

    out: Dict[str, Any] = {"bindingMode": BINDING_MODE_IDS}
    if qc:
        out[SCOPE_QC] = qc
    if rp:
        out[SCOPE_RP] = rp
    return out


def serialize_project_assigned_instruments(
    *,
    quality_control_ids: List[int] | None = None,
    radiation_protection_ids: List[int] | None = None,
) -> dict:
    out: dict = {}
    if quality_control_ids:
        out[SCOPE_QC] = list(quality_control_ids)
    if radiation_protection_ids:
        out[SCOPE_RP] = list(radiation_protection_ids)
    return out


def bound_instrument_ids_for_legacy_list(binding: Dict[str, List[int]]) -> List[int]:
    ordered: List[int] = []
    seen: set[int] = set()
    for scope in (SCOPE_QC, SCOPE_RP):
        for pk in binding.get(scope) or []:
            if pk in seen:
                continue
            seen.add(pk)
            ordered.append(pk)
    return ordered


def count_bound_kind_slots(binding_kinds: Dict[str, List[InstrumentKindSpec]]) -> tuple[int, int]:
    return len(binding_kinds.get(SCOPE_QC) or []), len(binding_kinds.get(SCOPE_RP) or [])


def count_bound_instruments(binding: Dict[str, List[int]]) -> tuple[int, int]:
    qc = len(binding.get(SCOPE_QC) or [])
    rp = len(binding.get(SCOPE_RP) or [])
    return qc, rp


def kind_spec_from_form_value(raw: str) -> InstrumentKindSpec | None:
    s = str(raw or "").strip()
    if not s:
        return None
    if KIND_SEP in s:
        name, _, model = s.partition(KIND_SEP)
        name = name.strip()
        if not name:
            return None
        return InstrumentKindSpec(name=name, model=model.strip())
    return None


def build_instrument_kind_groups(catalog_rows: list) -> List[dict]:
    """台账按「名称+型号」分组，供模板勾选种类（不选具体编号）。"""
    buckets: dict[Tuple[str, str], dict] = {}
    order: List[Tuple[str, str]] = []
    for row in catalog_rows or []:
        name = str(getattr(row, "name", None) or "").strip()
        if not name:
            continue
        model = str(getattr(row, "model", None) or "").strip()
        key = (name, model)
        if key not in buckets:
            order.append(key)
            buckets[key] = {
                "name": name,
                "model": model,
                "form_value": f"{name}{KIND_SEP}{model}",
                "codes": [],
                "in_stock": 0,
                "total": 0,
            }
        b = buckets[key]
        b["total"] += 1
        code = str(getattr(row, "code", None) or "").strip()
        if code:
            b["codes"].append(code)
        if getattr(row, "checkout_project_id", None) is None:
            b["in_stock"] += 1
    return [buckets[k] for k in order]
