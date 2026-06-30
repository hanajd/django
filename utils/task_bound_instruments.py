"""任务模板检测仪器：模板层绑定「种类」，项目派工时解析为具体编号。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

SCOPE_QC = "qualityControl"
SCOPE_RP = "radiationProtection"
BINDING_MODE_KINDS = "kinds"
BINDING_MODE_IDS = "ids"
SHARE_MODE_PER_TASK = "per_task"
SHARE_MODE_PROJECT = "project"
ASSIGNMENT_MODE_AUTO = "auto"
ASSIGNMENT_MODE_MANUAL = "manual"
KIND_SEP = "\x1e"  # 历史 JSON / 内部键
KIND_SEP_FORM = "||"  # 表单 checkbox value（避免控制字符在 POST 中被剥离）
REQ_SEP = "\x1f"  # 项目手动分配：任务+范围+种类 键分隔符
DETECTION_ITEM_SEP = "|"  # 委托设备 link + 现场记录库任务 id


@dataclass(frozen=True)
class InstrumentKindSpec:
    name: str
    model: str = ""

    def key(self) -> Tuple[str, str]:
        return ((self.name or "").strip(), (self.model or "").strip())


def kind_form_value(name: str, model: str = "") -> str:
    """HTML 表单提交用种类键（name||model）。"""
    return f"{(name or '').strip()}{KIND_SEP_FORM}{(model or '').strip()}"


def kind_requirement_key(task_id: int, scope: str, name: str, model: str = "") -> str:
    """项目手动分配：唯一标识「某任务模板 + 范围 + 种类」。"""
    return REQ_SEP.join(
        [str(int(task_id)), str(scope or "").strip(), (name or "").strip(), (model or "").strip()]
    )


def parse_kind_requirement_key(raw: str) -> tuple[int, str, str, str] | None:
    s = str(raw or "").strip()
    if REQ_SEP not in s:
        return None
    parts = s.split(REQ_SEP, 3)
    if len(parts) < 3:
        return None
    try:
        task_id = int(parts[0])
    except (TypeError, ValueError):
        return None
    scope = parts[1].strip()
    name = parts[2].strip()
    model = parts[3].strip() if len(parts) > 3 else ""
    if task_id <= 0 or scope not in (SCOPE_QC, SCOPE_RP) or not name:
        return None
    return task_id, scope, name, model


def detection_item_instrument_key(link_id: int, library_task_id: int) -> str:
    """唯一标识「某委托设备 × 某现场记录任务模板」的仪器分配槽位。"""
    return f"{int(link_id)}{DETECTION_ITEM_SEP}{int(library_task_id)}"


def parse_detection_item_instrument_key(raw: str) -> tuple[int, int] | None:
    s = str(raw or "").strip()
    if DETECTION_ITEM_SEP not in s:
        return None
    left, _, right = s.partition(DETECTION_ITEM_SEP)
    try:
        link_id = int(left)
        library_task_id = int(right)
    except (TypeError, ValueError):
        return None
    if link_id <= 0 or library_task_id <= 0:
        return None
    return link_id, library_task_id


def normalize_project_by_detection_item(raw: Any) -> dict[str, dict[str, list[int]]]:
    """``byDetectionItem``：检测项键 → 质控/防护编号列表。"""
    out: dict[str, dict[str, list[int]]] = {}
    if not isinstance(raw, dict):
        return out
    src = raw.get("byDetectionItem")
    if not isinstance(src, dict):
        return out
    for key, val in src.items():
        k = str(key or "").strip()
        if not k or not isinstance(val, dict):
            continue
        binding = normalize_project_assigned_instruments(val)
        if bound_instrument_ids_for_legacy_list(binding):
            out[k] = binding
    return out


def assigned_instruments_for_detection_item(
    assigned_raw: Any,
    link_id: int,
    library_task_id: int,
) -> dict[str, list[int]]:
    """读取某检测项（委托设备×现场记录）已分配编号。"""
    if not isinstance(assigned_raw, dict):
        return {SCOPE_QC: [], SCOPE_RP: []}
    by_item = normalize_project_by_detection_item(assigned_raw)
    key = detection_item_instrument_key(link_id, library_task_id)
    entry = by_item.get(key)
    if entry:
        return entry
    return assigned_instruments_for_task(assigned_raw, int(library_task_id))


def project_assignment_mode(raw: Any) -> str:
    if isinstance(raw, dict):
        mode = str(raw.get("assignmentMode") or "").strip().lower()
        if mode == ASSIGNMENT_MODE_MANUAL:
            return ASSIGNMENT_MODE_MANUAL
    return ASSIGNMENT_MODE_AUTO


def _dedupe_int_ids(val: Any) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    if not isinstance(val, (list, tuple)):
        return ids
    for item in val:
        try:
            pk = int(item)
        except (TypeError, ValueError):
            continue
        if pk > 0 and pk not in seen:
            seen.add(pk)
            ids.append(pk)
    return ids


def normalize_project_by_requirement(raw: Any) -> dict[str, list[int]]:
    """``byRequirement``：旧版「任务+范围+种类」键 → 已选仪器主键列表。"""
    out: dict[str, list[int]] = {}
    if not isinstance(raw, dict):
        return out
    src = raw.get("byRequirement")
    if not isinstance(src, dict):
        return out
    for key, val in src.items():
        k = str(key or "").strip()
        if not k:
            continue
        out[k] = _dedupe_int_ids(val)
    return out


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
        elif isinstance(item, str):
            spec = kind_spec_from_form_value(item)
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


def _kinds_from_catalog_ids(ids: Iterable[int]) -> List[InstrumentKindSpec]:
    """旧版绑编号时，按台账反推种类供展示/编辑。"""
    from apps.core.models import InstrumentCatalog

    id_list = [int(x) for x in ids if int(x) > 0]
    if not id_list:
        return []
    rows = list(
        InstrumentCatalog.objects.filter(pk__in=id_list).order_by("code", "id")
    )
    by_pk = {r.pk: r for r in rows}
    out: List[InstrumentKindSpec] = []
    seen: set[Tuple[str, str]] = set()
    for pk in id_list:
        row = by_pk.get(pk)
        if row is None:
            continue
        spec = InstrumentKindSpec(name=row.name, model=str(row.model or "").strip())
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
        if mode == BINDING_MODE_IDS:
            return BINDING_MODE_IDS
        for scope in (SCOPE_QC, SCOPE_RP):
            val = raw.get(scope)
            if isinstance(val, list) and val and isinstance(val[0], dict) and "name" in val[0]:
                return BINDING_MODE_KINDS
        if any(_kinds_from_scope_value(raw.get(scope)) for scope in (SCOPE_QC, SCOPE_RP) if scope in raw):
            return BINDING_MODE_KINDS
        if any(_many_ids(raw.get(scope)) for scope in (SCOPE_QC, SCOPE_RP) if scope in raw):
            return BINDING_MODE_IDS
    if isinstance(raw, list) and raw and not isinstance(raw[0], dict):
        return BINDING_MODE_IDS
    return BINDING_MODE_KINDS


def normalize_task_bound_kinds(raw: Any) -> Dict[str, List[InstrumentKindSpec]]:
    out: Dict[str, List[InstrumentKindSpec]] = {SCOPE_QC: [], SCOPE_RP: []}
    if not isinstance(raw, dict):
        if isinstance(raw, list) and binding_mode(raw) == BINDING_MODE_IDS:
            parsed = _many_ids(raw)
            if len(parsed) == 2:
                out[SCOPE_QC] = _kinds_from_catalog_ids([parsed[0]])
                out[SCOPE_RP] = _kinds_from_catalog_ids([parsed[1]])
            elif parsed:
                out[SCOPE_QC] = _kinds_from_catalog_ids(parsed)
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

    if binding_mode(raw) == BINDING_MODE_KINDS:
        for scope in (SCOPE_QC, SCOPE_RP):
            if out[scope]:
                continue
            id_list = _many_ids(raw.get(scope))
            if id_list:
                out[scope] = _kinds_from_catalog_ids(id_list)

    if not (out[SCOPE_QC] or out[SCOPE_RP]) and binding_mode(raw) == BINDING_MODE_IDS:
        id_binding = normalize_task_bound_instruments(raw)
        out[SCOPE_QC] = _kinds_from_catalog_ids(id_binding.get(SCOPE_QC) or [])
        out[SCOPE_RP] = _kinds_from_catalog_ids(id_binding.get(SCOPE_RP) or [])

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
    """项目派工后落库的具体编号（质控/防护 id 列表，不含 byTask 元数据）。"""
    if isinstance(raw, dict):
        scope_only = {k: raw.get(k) for k in (SCOPE_QC, SCOPE_RP, "qc", "rp") if k in raw}
        if scope_only:
            return normalize_task_bound_instruments(
                {**{"bindingMode": BINDING_MODE_IDS}, **scope_only}
            )
    return normalize_task_bound_instruments(
        {**({"bindingMode": BINDING_MODE_IDS} if isinstance(raw, dict) else {}), **(raw if isinstance(raw, dict) else {})}
    )


def project_instrument_share_mode(project_or_raw: Any) -> str:
    if hasattr(project_or_raw, "instrument_share_across_tasks"):
        return SHARE_MODE_PROJECT if project_or_raw.instrument_share_across_tasks else SHARE_MODE_PER_TASK
    if isinstance(project_or_raw, dict):
        mode = str(project_or_raw.get("shareMode") or project_or_raw.get("share_mode") or "").strip()
        if mode == SHARE_MODE_PROJECT:
            return SHARE_MODE_PROJECT
    return SHARE_MODE_PER_TASK


def assigned_instruments_for_task(assigned_raw: Any, task_id: int) -> Dict[str, List[int]]:
    """读取某任务模板已分配编号；无 per-task 记录时回退项目级列表。"""
    if not isinstance(assigned_raw, dict):
        return {SCOPE_QC: [], SCOPE_RP: []}
    by_task = assigned_raw.get("byTask")
    if isinstance(by_task, dict):
        entry = by_task.get(str(task_id)) or by_task.get(task_id)
        if isinstance(entry, dict):
            return normalize_project_assigned_instruments(entry)
    return normalize_project_assigned_instruments(assigned_raw)


def serialize_task_bound_kinds(
    *,
    quality_control_kinds: List[InstrumentKindSpec] | None = None,
    radiation_protection_kinds: List[InstrumentKindSpec] | None = None,
) -> dict:
    qc = list(quality_control_kinds or [])
    rp = list(radiation_protection_kinds or [])
    out: dict = {"bindingMode": BINDING_MODE_KINDS, SCOPE_QC: [], SCOPE_RP: []}
    if qc:
        out[SCOPE_QC] = [{"name": k.name, "model": k.model} for k in qc]
    if rp:
        out[SCOPE_RP] = [{"name": k.name, "model": k.model} for k in rp]
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
    share_mode: str = SHARE_MODE_PER_TASK,
    by_task: Dict[str | int, dict] | None = None,
    by_requirement: Dict[str, List[int]] | None = None,
    by_kind: Dict[str, List[int]] | None = None,
    by_detection_item: Dict[str, dict] | None = None,
    assignment_mode: str = ASSIGNMENT_MODE_AUTO,
) -> dict:
    out: dict = {
        "shareMode": share_mode,
        "assignmentMode": assignment_mode,
    }
    if quality_control_ids:
        out[SCOPE_QC] = list(quality_control_ids)
    if radiation_protection_ids:
        out[SCOPE_RP] = list(radiation_protection_ids)
    if by_detection_item:
        out["byDetectionItem"] = {
            str(k): normalize_project_assigned_instruments(v) for k, v in by_detection_item.items()
        }
    if by_task:
        out["byTask"] = {str(k): v for k, v in by_task.items()}
    if by_kind:
        out["byKind"] = {str(k): list(v) for k, v in by_kind.items()}
    elif by_requirement:
        out["byRequirement"] = {str(k): list(v) for k, v in by_requirement.items()}
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
    for sep in (KIND_SEP_FORM, KIND_SEP):
        if sep in s:
            name, _, model = s.partition(sep)
            name = name.strip()
            if not name:
                return None
            return InstrumentKindSpec(name=name, model=model.strip())
    if s.isdigit():
        specs = _kinds_from_catalog_ids([int(s)])
        return specs[0] if specs else None
    return InstrumentKindSpec(name=s, model="")


def normalize_project_by_kind(raw: Any) -> dict[str, list[int]]:
    """``byKind``：仪器种类键（name||model）→ 已选仪器主键列表（可多台，按种类出库）。"""
    out: dict[str, list[int]] = {}
    if not isinstance(raw, dict):
        return out
    src = raw.get("byKind")
    if isinstance(src, dict):
        for key, val in src.items():
            k = str(key or "").strip()
            if not k:
                continue
            out[k] = _dedupe_int_ids(val)
        if out:
            return out
    by_req = normalize_project_by_requirement(raw)
    if not by_req:
        return out
    for key, ids in by_req.items():
        spec = kind_spec_from_form_value(key)
        if spec is not None:
            k = kind_form_value(spec.name, spec.model)
        else:
            parsed = parse_kind_requirement_key(key)
            if parsed is None:
                continue
            _task_id, _scope, name, model = parsed
            k = kind_form_value(name, model)
        out[k] = _dedupe_int_ids((out.get(k) or []) + ids)
    return out


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
                "form_value": kind_form_value(name, model),
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
