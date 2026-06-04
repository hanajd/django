"""检测仪器台账：模板绑「种类」，项目派工时分配具体编号并出库。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import InstrumentCatalog, InstrumentCheckoutLog, LibraryProject, LibraryTask
from apps.core.project_equipment_service import project_tasks_for_user_assignment
from utils.task_bound_instruments import (
    ASSIGNMENT_MODE_MANUAL,
    BINDING_MODE_KINDS,
    InstrumentKindSpec,
    SCOPE_QC,
    SCOPE_RP,
    SHARE_MODE_PER_TASK,
    SHARE_MODE_PROJECT,
    assigned_instruments_for_task,
    binding_mode,
    bound_instrument_ids_for_legacy_list,
    kind_form_value,
    kind_spec_from_form_value,
    normalize_project_assigned_instruments,
    normalize_project_by_kind,
    normalize_project_by_requirement,
    normalize_task_bound_instruments,
    normalize_task_bound_kinds,
    parse_kind_requirement_key,
    project_assignment_mode,
    project_instrument_share_mode,
    serialize_project_assigned_instruments,
)


@dataclass(frozen=True)
class InstrumentKindSlot:
    task_id: int
    task_code: str
    task_name: str
    scope: str
    spec: InstrumentKindSpec


# 暂不因仪器未分配/出库失败阻断「同步 App 任务」；后续可按实际工作流收紧
INSTRUMENT_DISPATCH_SOFT_MODE = True


@dataclass
class InstrumentDispatchCheckResult:
    ok: bool
    message: str
    checked_out_count: int = 0
    checked_in_count: int = 0
    already_on_project_count: int = 0
    blocked: list[str] | None = None
    assigned_ids: dict[str, list[int]] | None = None
    warning_only: bool = False


def instrument_kind_key(name: str, model: str = "") -> tuple[str, str]:
    return ((name or "").strip(), (model or "").strip())


def _active_checkout_project_ids(inst: InstrumentCatalog) -> set[int]:
    raw = getattr(inst, "checkout_project_ids", None) or []
    ids: set[int] = set()
    for x in raw:
        try:
            pk = int(x)
        except (TypeError, ValueError):
            continue
        if pk > 0:
            ids.add(pk)
    if inst.checkout_project_id and inst.checkout_project_id not in ids:
        ids.add(int(inst.checkout_project_id))
    return ids


def _sync_checkout_projects(
    inst: InstrumentCatalog,
    project_ids: set[int],
    *,
    user: User | None,
    touch_time: bool = True,
) -> None:
    id_list = sorted(project_ids)
    inst.checkout_project_ids = id_list
    if id_list:
        inst.checkout_project_id = id_list[-1]
        if touch_time:
            inst.checked_out_at = timezone.now()
            inst.checked_out_by = user
    else:
        inst.checkout_project = None
        inst.checked_out_at = None
        inst.checked_out_by = None
    inst.save(
        update_fields=[
            "checkout_project_ids",
            "checkout_project",
            "checked_out_at",
            "checked_out_by",
            "updated_at",
        ]
    )


def merged_kind_slots_for_project(project: LibraryProject) -> list[InstrumentKindSlot]:
    """
    汇总本项目现场记录任务模板所需的仪器「种类」槽位。
    同一模板在任务列表中只出现一次（多台设备共用该模板时自动合并）。
    """
    slots: list[InstrumentKindSlot] = []
    for task in project_tasks_for_user_assignment(project):
        if task.output_target == LibraryTask.OUTPUT_REPORT:
            continue
        raw = getattr(task, "bound_instrument_ids", None) or []
        if binding_mode(raw) != BINDING_MODE_KINDS:
            continue
        kinds = normalize_task_bound_kinds(raw)
        for spec in kinds.get(SCOPE_QC) or []:
            slots.append(
                InstrumentKindSlot(
                    task_id=int(task.pk),
                    task_code=str(task.code or ""),
                    task_name=str(task.name or ""),
                    scope=SCOPE_QC,
                    spec=spec,
                )
            )
        for spec in kinds.get(SCOPE_RP) or []:
            slots.append(
                InstrumentKindSlot(
                    task_id=int(task.pk),
                    task_code=str(task.code or ""),
                    task_name=str(task.name or ""),
                    scope=SCOPE_RP,
                    spec=spec,
                )
            )
    return slots


def _dedupe_id_list(ids: Iterable[int]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for raw in ids:
        pk = int(raw)
        if pk > 0 and pk not in seen:
            seen.add(pk)
            out.append(pk)
    return out


def _allocate_from_kind_slots(
    project: LibraryProject,
    slots: list[InstrumentKindSlot],
    *,
    share_across_tasks: bool,
) -> tuple[dict[str, list[int]], dict[str, dict[str, list[int]]], list[str]]:
    """
    按槽位分配具体编号。
    - 同任务模板（task_id）内同种类（名称+型号）：共用一台（质控/防护槽位重复亦共用）；
    - share_across_tasks=False：不同模板默认可各用一台（exclude 已选编号）；
    - share_across_tasks=True：整项目同种类共用一台。
    """
    assigned: dict[str, list[int]] = {SCOPE_QC: [], SCOPE_RP: []}
    by_task: dict[str, dict[str, list[int]]] = {}
    blocked: list[str] = []
    # 同模板内按种类共用，不按 scope 区分
    picked_within_task: dict[tuple[int, tuple[str, str]], int] = {}
    picked_project_wide: dict[tuple[str, str], int] = {}
    used_ids: set[int] = set()

    for slot in slots:
        task_key = str(slot.task_id)
        if task_key not in by_task:
            by_task[task_key] = {SCOPE_QC: [], SCOPE_RP: []}

        within_key = (slot.task_id, slot.spec.key())
        if within_key in picked_within_task:
            pk = picked_within_task[within_key]
            if pk not in by_task[task_key][slot.scope]:
                by_task[task_key][slot.scope].append(pk)
            if pk not in assigned[slot.scope]:
                assigned[slot.scope].append(pk)
            continue

        if share_across_tasks:
            project_key = slot.spec.key()
            if project_key in picked_project_wide:
                pk = picked_project_wide[project_key]
                picked_within_task[within_key] = pk
                if pk not in by_task[task_key][slot.scope]:
                    by_task[task_key][slot.scope].append(pk)
                if pk not in assigned[slot.scope]:
                    assigned[slot.scope].append(pk)
                continue
            exclude = set()
        else:
            exclude = set(used_ids)

        inst = pick_instrument_for_kind(slot.spec, project, exclude_ids=exclude)
        if inst is None:
            avail = availability_for_kind(slot.spec.name, slot.spec.model)
            label = slot.spec.name + (f" ({slot.spec.model})" if slot.spec.model else "")
            task_label = slot.task_code or f"任务#{slot.task_id}"
            blocked.append(
                f"「{task_label}」{label}：需要 1 台，同种可用 {avail['total']} 台"
                + ("（已无可选编号）" if exclude else "")
            )
            continue

        pk = inst.pk
        picked_within_task[within_key] = pk
        if share_across_tasks:
            picked_project_wide[slot.spec.key()] = pk
        used_ids.add(pk)
        if pk not in assigned[slot.scope]:
            assigned[slot.scope].append(pk)
        if pk not in by_task[task_key][slot.scope]:
            by_task[task_key][slot.scope].append(pk)

    assigned[SCOPE_QC] = _dedupe_id_list(assigned[SCOPE_QC])
    assigned[SCOPE_RP] = _dedupe_id_list(assigned[SCOPE_RP])
    for task_key, scopes in by_task.items():
        by_task[task_key] = {
            SCOPE_QC: _dedupe_id_list(scopes.get(SCOPE_QC) or []),
            SCOPE_RP: _dedupe_id_list(scopes.get(SCOPE_RP) or []),
        }
    return assigned, by_task, blocked


def _instrument_picker_row(inst: InstrumentCatalog, project: LibraryProject) -> dict:
    active = _active_checkout_project_ids(inst)
    if project.pk in active:
        status, status_label = "on_project", "已关联本项目"
    elif active:
        status, status_label = "shared", "已关联其他项目（可共用）"
    else:
        status, status_label = "in_stock", "在库"
    return {
        "id": int(inst.pk),
        "code": inst.code or "",
        "name": inst.name or "",
        "model": inst.model or "",
        "status": status,
        "statusLabel": status_label,
    }


def _kind_rows_from_slots(kind_slots: list[InstrumentKindSlot]) -> list[dict]:
    """按仪器种类汇总一行（不区分质控/防护）；附带引用该种类的任务模板列表。"""
    from collections import Counter

    slot_counts = Counter(slot.spec.key() for slot in kind_slots)
    rows_by_kind: dict[tuple[str, str], dict] = {}
    for slot in kind_slots:
        k = slot.spec.key()
        if k not in rows_by_kind:
            rows_by_kind[k] = {
                "key": kind_form_value(slot.spec.name, slot.spec.model),
                "name": slot.spec.name,
                "model": slot.spec.model,
                "taskCodes": [],
                "taskNames": [],
            }
        row = rows_by_kind[k]
        if slot.task_code and slot.task_code not in row["taskCodes"]:
            row["taskCodes"].append(slot.task_code)
        if slot.task_name and slot.task_name not in row["taskNames"]:
            row["taskNames"].append(slot.task_name)
    rows: list[dict] = []
    for k, row in rows_by_kind.items():
        row["slotRepeatCount"] = slot_counts.get(k, 0)
        row["referencedByMultipleTasks"] = len(row["taskCodes"]) > 1
        rows.append(row)
    rows.sort(key=lambda r: (r["name"], r["model"], r["key"]))
    return rows


def _coalesce_incoming_to_by_kind(
    incoming: dict[str, list[int]],
    kind_slots: list[InstrumentKindSlot],
) -> dict[str, list[int]]:
    """接受种类键或旧版任务+范围键，统一为 byKind。"""
    valid = {kind_form_value(s.spec.name, s.spec.model) for s in kind_slots}
    out: dict[str, list[int]] = {}
    for key, ids in (incoming or {}).items():
        spec = kind_spec_from_form_value(key)
        if spec is not None:
            k = kind_form_value(spec.name, spec.model)
        else:
            parsed = parse_kind_requirement_key(key)
            if parsed is None:
                continue
            _task_id, _scope, name, model = parsed
            k = kind_form_value(name, model)
        if k not in valid:
            continue
        out[k] = _dedupe_id_list((out.get(k) or []) + list(ids))
    return out


def _by_task_from_by_kind(
    by_kind: dict[str, list[int]],
    kind_slots: list[InstrumentKindSlot],
) -> dict[str, dict[str, list[int]]]:
    """将按种类选择的多台设备展开到各任务模板的质控/防护槽位（供 App 读取）。"""
    by_task: dict[str, dict[str, list[int]]] = {}
    for slot in kind_slots:
        k = kind_form_value(slot.spec.name, slot.spec.model)
        ids = by_kind.get(k) or []
        if not ids:
            continue
        task_key = str(slot.task_id)
        if task_key not in by_task:
            by_task[task_key] = {SCOPE_QC: [], SCOPE_RP: []}
        for pk in ids:
            if pk not in by_task[task_key][slot.scope]:
                by_task[task_key][slot.scope].append(pk)
    for task_key, scopes in by_task.items():
        by_task[task_key] = {
            SCOPE_QC: _dedupe_id_list(scopes.get(SCOPE_QC) or []),
            SCOPE_RP: _dedupe_id_list(scopes.get(SCOPE_RP) or []),
        }
    return by_task


def _by_task_from_by_requirement(by_requirement: dict[str, list[int]]) -> dict[str, dict[str, list[int]]]:
    """兼容旧版 byRequirement 键（任务+范围+种类）。"""
    by_task: dict[str, dict[str, list[int]]] = {}
    for req_key, ids in (by_requirement or {}).items():
        parsed = parse_kind_requirement_key(req_key)
        if parsed is None:
            continue
        task_id, scope, _name, _model = parsed
        task_key = str(task_id)
        if task_key not in by_task:
            by_task[task_key] = {SCOPE_QC: [], SCOPE_RP: []}
        for pk in ids:
            if pk not in by_task[task_key][scope]:
                by_task[task_key][scope].append(pk)
    for task_key, scopes in by_task.items():
        by_task[task_key] = {
            SCOPE_QC: _dedupe_id_list(scopes.get(SCOPE_QC) or []),
            SCOPE_RP: _dedupe_id_list(scopes.get(SCOPE_RP) or []),
        }
    return by_task


def _flat_assigned_from_by_task(by_task: dict[str, dict[str, list[int]]]) -> dict[str, list[int]]:
    assigned: dict[str, list[int]] = {SCOPE_QC: [], SCOPE_RP: []}
    for scopes in by_task.values():
        for scope in (SCOPE_QC, SCOPE_RP):
            for pk in scopes.get(scope) or []:
                if pk not in assigned[scope]:
                    assigned[scope].append(pk)
    assigned[SCOPE_QC] = _dedupe_id_list(assigned[SCOPE_QC])
    assigned[SCOPE_RP] = _dedupe_id_list(assigned[SCOPE_RP])
    return assigned


def build_manual_instrument_picker_catalog(project: LibraryProject) -> dict:
    """
    手动分配悬浮窗数据：按仪器种类汇总（不区分质控/防护），
    每种可多选台账设备作为本项目出库清单。
    """
    kind_slots = merged_kind_slots_for_project(project)
    kinds = _kind_rows_from_slots(kind_slots)
    if not kinds:
        return {"kinds": [], "projectId": project.pk}

    raw = getattr(project, "assigned_instrument_ids", None) or {}
    by_kind = normalize_project_by_kind(raw)

    catalog = list(
        InstrumentCatalog.objects.filter(is_active=True)
        .select_related("checkout_project")
        .order_by("name", "model", "code", "id")
    )
    buckets: dict[tuple[str, str], list[InstrumentCatalog]] = {}
    for inst in catalog:
        k = instrument_kind_key(inst.name, inst.model or "")
        buckets.setdefault(k, []).append(inst)

    for row in kinds:
        k = instrument_kind_key(row["name"], row.get("model") or "")
        row["selectedIds"] = list(by_kind.get(row["key"]) or [])
        row["instruments"] = [
            _instrument_picker_row(inst, project) for inst in buckets.get(k, [])
        ]
        avail = availability_for_kind(row["name"], row.get("model") or "")
        row["kindTotal"] = avail["total"]
        row["kindInStock"] = avail["in_stock"]

    return {
        "projectId": project.pk,
        "assignmentMode": project_assignment_mode(raw),
        "kinds": kinds,
    }


@transaction.atomic
def apply_manual_instrument_assignment(
    project: LibraryProject,
    by_kind: dict[str, list[int]],
    *,
    user: User | None = None,
    checkout: bool = True,
) -> InstrumentDispatchCheckResult:
    """按仪器种类保存手动选择（每种可多选）并登记出库至本项目。"""
    kind_slots = merged_kind_slots_for_project(project)
    if not kind_slots:
        return InstrumentDispatchCheckResult(ok=True, message="本项目无仪器种类需求。")

    cleaned = _coalesce_incoming_to_by_kind(by_kind, kind_slots)
    by_task = _by_task_from_by_kind(cleaned, kind_slots)
    assigned = _flat_assigned_from_by_task(by_task)
    share_mode = SHARE_MODE_PROJECT if getattr(project, "instrument_share_across_tasks", False) else SHARE_MODE_PER_TASK

    project.assigned_instrument_ids = serialize_project_assigned_instruments(
        quality_control_ids=assigned[SCOPE_QC],
        radiation_protection_ids=assigned[SCOPE_RP],
        share_mode=share_mode,
        by_task=by_task,
        by_kind=cleaned,
        assignment_mode=ASSIGNMENT_MODE_MANUAL,
    )
    project.save(update_fields=["assigned_instrument_ids", "updated_at"])

    all_ids = _sort_ids_by_code(bound_instrument_ids_for_legacy_list(assigned))
    n_in, n_out, errs = sync_project_instrument_checkout(
        project,
        all_ids,
        user=user,
        allow_checkout=checkout,
        checkout_note="人员派工：手动指定仪器并出库",
        checkin_note="人员派工：取消绑定，登记入库",
    )
    parts: list[str] = [f"已保存手动分配（共 {len(all_ids)} 台仪器编号）"]
    if n_in:
        parts.append(f"已入库 {n_in} 台（已取消本项目绑定）")
    if checkout and n_out:
        parts.append(f"已出库 {n_out} 台至本项目")
    elif not checkout and all_ids:
        linked = _instrument_ids_linked_to_project(project)
        pending = len(set(all_ids) - linked)
        if pending:
            parts.append(f"未勾选出库登记，另有 {pending} 台仍待在库")
    msg = "；".join(parts) + "。"
    if errs and INSTRUMENT_DISPATCH_SOFT_MODE:
        return InstrumentDispatchCheckResult(
            ok=True,
            message=msg + " 部分出库登记未完成：\n" + "\n".join(errs),
            checked_out_count=n_out,
            checked_in_count=n_in,
            assigned_ids=assigned,
            warning_only=True,
        )
    if errs:
        return InstrumentDispatchCheckResult(
            ok=False,
            message=msg + " 出库失败：\n" + "\n".join(errs),
            blocked=errs,
            assigned_ids=assigned,
            checked_in_count=n_in,
        )
    return InstrumentDispatchCheckResult(
        ok=True,
        message=msg,
        checked_out_count=n_out,
        checked_in_count=n_in,
        assigned_ids=assigned,
    )


def project_uses_kind_binding(project: LibraryProject) -> bool:
    for task in project_tasks_for_user_assignment(project):
        if binding_mode(getattr(task, "bound_instrument_ids", None) or []) == BINDING_MODE_KINDS:
            return True
    return False


def resolved_instrument_ids_for_project(
    project: LibraryProject,
    task_obj: LibraryTask | None = None,
) -> dict[str, list[int]]:
    """
    项目实际使用的仪器编号：优先 ``assigned_instrument_ids``（派工分配）；
    指定 task_obj 时优先读 byTask 中该模板的编号。
    """
    raw = getattr(project, "assigned_instrument_ids", None) or {}
    by_kind = normalize_project_by_kind(raw)
    if by_kind:
        slots = merged_kind_slots_for_project(project)
        by_task = _by_task_from_by_kind(by_kind, slots)
        if task_obj is not None:
            per_task = assigned_instruments_for_task(
                {**raw, "byTask": by_task, SCOPE_QC: [], SCOPE_RP: []},
                int(task_obj.pk),
            )
            if bound_instrument_ids_for_legacy_list(per_task):
                return per_task
        return _flat_assigned_from_by_task(by_task)

    if task_obj is not None and isinstance(raw, dict) and raw.get("byTask"):
        per_task = assigned_instruments_for_task(raw, int(task_obj.pk))
        if bound_instrument_ids_for_legacy_list(per_task):
            return per_task

    assigned = normalize_project_assigned_instruments(raw)
    if bound_instrument_ids_for_legacy_list(assigned):
        return assigned

    seen: set[int] = set()
    merged: dict[str, list[int]] = {SCOPE_QC: [], SCOPE_RP: []}
    for task in project_tasks_for_user_assignment(project):
        binding = normalize_task_bound_instruments(
            getattr(task, "bound_instrument_ids", None) or []
        )
        for scope in (SCOPE_QC, SCOPE_RP):
            for pk in binding.get(scope) or []:
                if pk in seen:
                    continue
                seen.add(pk)
                merged[scope].append(pk)
    return merged


def required_instrument_ids_for_project(project: LibraryProject) -> list[int]:
    """出库/展示用：已分配编号列表（种类模式须先派工分配）。"""
    return bound_instrument_ids_for_legacy_list(resolved_instrument_ids_for_project(project))


def _queryset_for_kind(name: str, model: str = ""):
    n, m = instrument_kind_key(name, model)
    qs = InstrumentCatalog.objects.filter(is_active=True, name=n)
    if m:
        return qs.filter(model=m)
    return qs.filter(Q(model="") | Q(model__isnull=True))


def _sort_ids_by_code(ids: Iterable[int]) -> list[int]:
    """出库/展示顺序：按台账编号 ``code`` 升序。"""
    id_list = [int(x) for x in ids if int(x) > 0]
    if not id_list:
        return []
    rows = list(
        InstrumentCatalog.objects.filter(pk__in=id_list, is_active=True)
        .only("pk", "code")
        .order_by("code")
    )
    ordered = [r.pk for r in rows]
    seen = set(ordered)
    for pk in id_list:
        if pk not in seen:
            ordered.append(pk)
    return ordered


def pick_instrument_for_kind(
    spec: InstrumentKindSpec,
    project: LibraryProject,
    *,
    exclude_ids: set[int] | None = None,
) -> InstrumentCatalog | None:
    """
    为本项目按种类选一台仪器（台账编号升序）。
    允许多委托/项目共用：已出库至其他项目的同种仪器也可选用。
    """
    exclude = exclude_ids or set()
    qs = _queryset_for_kind(spec.name, spec.model).select_related("checkout_project")
    on_project = sorted(
        (
            i
            for i in qs
            if project.pk in _active_checkout_project_ids(i) and i.pk not in exclude
        ),
        key=lambda row: (row.code or "", row.pk),
    )
    if on_project:
        return on_project[0]
    for inst in qs.order_by("code"):
        if inst.pk not in exclude:
            return inst
    return None


def pick_instrument_for_preferred_id(
    preferred_id: int,
    project: LibraryProject,
    *,
    exclude_ids: set[int],
) -> InstrumentCatalog | None:
    """
    旧版模板槽位：优先使用模板绑定的 id；若已被其他项目占用或不可用，
    则按同种仪器在库编号顺序自动选用下一台（兼容老台账多台同型号）。
    """
    pref = (
        InstrumentCatalog.objects.filter(pk=preferred_id, is_active=True)
        .select_related("checkout_project")
        .first()
    )
    if pref is None:
        return None
    if pref.pk not in exclude_ids:
        return pref
    spec = InstrumentKindSpec(name=pref.name, model=str(pref.model or "").strip())
    return pick_instrument_for_kind(spec, project, exclude_ids=exclude_ids)


def merged_legacy_id_slots_for_project(
    project: LibraryProject,
) -> list[tuple[str, int]]:
    """旧版 ``bindingMode=ids`` 或列表格式：质控槽位在前、防护在后，保留模板顺序。"""
    slots: list[tuple[str, int]] = []
    for task in project_tasks_for_user_assignment(project):
        raw = getattr(task, "bound_instrument_ids", None) or []
        if binding_mode(raw) == BINDING_MODE_KINDS:
            continue
        binding = normalize_task_bound_instruments(raw)
        for scope in (SCOPE_QC, SCOPE_RP):
            for pk in binding.get(scope) or []:
                slots.append((scope, int(pk)))
    return slots


def _resolve_legacy_slots_to_assigned(
    project: LibraryProject,
) -> tuple[dict[str, list[int]], list[str]]:
    assigned: dict[str, list[int]] = {SCOPE_QC: [], SCOPE_RP: []}
    used: set[int] = set()
    blocked: list[str] = []
    for scope, preferred_id in merged_legacy_id_slots_for_project(project):
        inst = pick_instrument_for_preferred_id(
            preferred_id, project, exclude_ids=used
        )
        if inst is None:
            pref = InstrumentCatalog.objects.filter(pk=preferred_id).first()
            if pref:
                avail = availability_for_kind(pref.name, pref.model or "")
                label = pref.name + (f" ({pref.model})" if pref.model else "")
                blocked.append(
                    f"{label}：模板指定 #{preferred_id} 不可用，同种在库 "
                    f"{avail['in_stock']}/{avail['total']} 台"
                )
            else:
                blocked.append(f"模板仪器 #{preferred_id} 不存在或已停用")
            continue
        used.add(inst.pk)
        assigned[scope].append(inst.pk)
    return assigned, blocked


def is_instrument_in_stock(inst: InstrumentCatalog) -> bool:
    """无任一项目出库关联时视为在库（支持多项目共用字段 checkout_project_ids）。"""
    return not _active_checkout_project_ids(inst)


def availability_for_kind(name: str, model: str = "") -> dict:
    n, m = instrument_kind_key(name, model)
    qs = _queryset_for_kind(n, m).only("pk", "checkout_project_id", "checkout_project_ids")
    total = 0
    in_stock = 0
    for inst in qs:
        total += 1
        if is_instrument_in_stock(inst):
            in_stock += 1
    return {
        "name": n,
        "model": m,
        "total": total,
        "in_stock": in_stock,
        "checked_out": total - in_stock,
    }


def _instrument_busy_elsewhere(inst: InstrumentCatalog, project: LibraryProject) -> str | None:
    """多项目可共用仪器，不再因「已出库至其他项目」而阻断。"""
    return None


@transaction.atomic
def checkout_instruments_to_project(
    project: LibraryProject,
    instrument_ids: Iterable[int],
    *,
    user: User | None = None,
    note: str = "",
) -> tuple[int, list[str]]:
    errors: list[str] = []
    count = 0
    now = timezone.now()
    for raw in _sort_ids_by_code(instrument_ids):
        try:
            pk = int(raw)
        except (TypeError, ValueError):
            continue
        if pk <= 0:
            continue
        inst = (
            InstrumentCatalog.objects.select_for_update()
            .filter(pk=pk, is_active=True)
            .first()
        )
        if inst is None:
            errors.append(f"仪器 #{pk} 不存在或已停用")
            continue
        active = _active_checkout_project_ids(inst)
        if project.pk in active:
            continue
        active.add(project.pk)
        _sync_checkout_projects(inst, active, user=user, touch_time=True)
        InstrumentCheckoutLog.objects.create(
            instrument=inst,
            project=project,
            event_type=InstrumentCheckoutLog.EVENT_CHECKOUT,
            performed_by=user,
            note=note or "",
        )
        count += 1
    return count, errors


def _instrument_ids_linked_to_project(project: LibraryProject) -> set[int]:
    """当前台账上仍关联到本项目的仪器主键（含 checkout_project_ids）。"""
    linked: set[int] = set()
    # SQLite 不支持 JSONField ``contains``，在应用层根据 checkout_project_ids 判断
    for inst in InstrumentCatalog.objects.filter(is_active=True).only(
        "pk", "checkout_project_id", "checkout_project_ids"
    ):
        if project.pk in _active_checkout_project_ids(inst):
            linked.add(int(inst.pk))
    return linked


@transaction.atomic
def sync_project_instrument_checkout(
    project: LibraryProject,
    desired_ids: Iterable[int] | None = None,
    *,
    user: User | None = None,
    allow_checkout: bool = True,
    checkout_note: str = "人员派工：同步出库至本项目",
    checkin_note: str = "人员派工：取消本项目绑定，登记入库",
) -> tuple[int, int, list[str]]:
    """
    使仪器台账出库状态与项目当前分配列表一致：
    已从分配中移除的仪器解除本项目关联（无其他项目时恢复在库）；
    新加入分配的仪器登记出库至本项目。
    """
    desired_list = _sort_ids_by_code(desired_ids if desired_ids is not None else required_instrument_ids_for_project(project))
    desired = set(desired_list)
    current = _instrument_ids_linked_to_project(project)
    to_checkin = sorted(current - desired)
    to_checkout = sorted(desired - current) if allow_checkout else []

    n_in = 0
    if to_checkin:
        n_in = checkin_instruments_from_project(
            project,
            to_checkin,
            user=user,
            note=checkin_note,
        )
    n_out = 0
    errs: list[str] = []
    if to_checkout:
        n_out, errs = checkout_instruments_to_project(
            project,
            to_checkout,
            user=user,
            note=checkout_note,
        )
    return n_in, n_out, errs


@transaction.atomic
def checkin_instruments_from_project(
    project: LibraryProject,
    instrument_ids: Iterable[int] | None = None,
    *,
    user: User | None = None,
    note: str = "",
) -> int:
    if instrument_ids is not None:
        ids = [int(x) for x in instrument_ids if int(x) > 0]
        if not ids:
            return 0
        inst_qs = InstrumentCatalog.objects.select_for_update().filter(
            pk__in=ids, is_active=True
        )
    else:
        linked_ids = list(_instrument_ids_linked_to_project(project))
        if not linked_ids:
            return 0
        inst_qs = InstrumentCatalog.objects.select_for_update().filter(
            pk__in=linked_ids, is_active=True
        )
    n = 0
    for inst in inst_qs:
        active = _active_checkout_project_ids(inst)
        if project.pk not in active:
            continue
        active.discard(project.pk)
        _sync_checkout_projects(inst, active, user=user, touch_time=False)
        InstrumentCheckoutLog.objects.create(
            instrument=inst,
            project=project,
            event_type=InstrumentCheckoutLog.EVENT_CHECKIN,
            performed_by=user,
            note=note or "",
        )
        n += 1
    if instrument_ids is None and n:
        project.assigned_instrument_ids = {}
        project.save(update_fields=["assigned_instrument_ids", "updated_at"])
    return n


@transaction.atomic
def allocate_instruments_for_project(
    project: LibraryProject,
    *,
    user: User | None = None,
) -> InstrumentDispatchCheckResult:
    """
    按任务模板绑定的「种类」为本项目分配具体编号，写入 assigned_instrument_ids 并出库。
    """
    slots = merged_kind_slots_for_project(project)
    if not slots:
        if project_uses_kind_binding(project):
            return InstrumentDispatchCheckResult(
                ok=True,
                message="任务模板未配置仪器种类，已跳过分配。",
            )
        return InstrumentDispatchCheckResult(
            ok=True,
            message="本项目无仪器种类要求，已跳过分配。",
        )

    share_across = bool(getattr(project, "instrument_share_across_tasks", False))
    assigned, by_task, blocked = _allocate_from_kind_slots(
        project, slots, share_across_tasks=share_across
    )

    if blocked:
        msg = "无法分配仪器编号：\n" + "\n".join(blocked)
        if INSTRUMENT_DISPATCH_SOFT_MODE:
            return InstrumentDispatchCheckResult(
                ok=True,
                message=msg,
                blocked=blocked,
                warning_only=True,
            )
        return InstrumentDispatchCheckResult(ok=False, message=msg, blocked=blocked)

    share_mode = SHARE_MODE_PROJECT if share_across else SHARE_MODE_PER_TASK
    project.assigned_instrument_ids = serialize_project_assigned_instruments(
        quality_control_ids=assigned[SCOPE_QC],
        radiation_protection_ids=assigned[SCOPE_RP],
        share_mode=share_mode,
        by_task=by_task,
    )
    project.save(update_fields=["assigned_instrument_ids", "updated_at"])

    all_ids = _sort_ids_by_code(bound_instrument_ids_for_legacy_list(assigned))
    n_in, n_out, errs = sync_project_instrument_checkout(
        project,
        all_ids,
        user=user,
        checkout_note="人员派工：按种类与台账编号顺序分配并出库",
        checkin_note="人员派工：自动重分配，取消旧绑定并入库",
    )
    if errs:
        msg = "编号已分配但出库登记未完成：\n" + "\n".join(errs)
        if INSTRUMENT_DISPATCH_SOFT_MODE:
            return InstrumentDispatchCheckResult(
                ok=True,
                message=msg,
                blocked=errs,
                assigned_ids=assigned,
                checked_out_count=n_out,
                checked_in_count=n_in,
                warning_only=True,
            )
        return InstrumentDispatchCheckResult(
            ok=False,
            message=msg,
            blocked=errs,
            assigned_ids=assigned,
            checked_in_count=n_in,
        )

    mode_hint = "跨模板共用" if share_across else "按任务模板分别"
    extra = f"（入库 {n_in} 台）" if n_in else ""
    return InstrumentDispatchCheckResult(
        ok=True,
        message=(
            f"已为本项目分配并同步出库 {len(all_ids)} 台仪器{extra}（{mode_hint}；"
            f"质控 {len(assigned[SCOPE_QC])}、防护 {len(assigned[SCOPE_RP])}）。"
        ),
        checked_out_count=n_out,
        checked_in_count=n_in,
        assigned_ids=assigned,
    )


@transaction.atomic
def allocate_legacy_instruments_for_project(
    project: LibraryProject,
    *,
    user: User | None = None,
) -> InstrumentDispatchCheckResult:
    """
    旧版模板绑具体 id / 列表 ``[qc, rp]``：派工时按编号顺序出库；
    模板指定编号已被占用时，同种在库自动顺延下一台。
    """
    slots = merged_legacy_id_slots_for_project(project)
    if not slots:
        return InstrumentDispatchCheckResult(
            ok=True,
            message="任务模板未配置仪器（旧版），已跳过。",
        )

    assigned, blocked = _resolve_legacy_slots_to_assigned(project)
    if blocked:
        msg = "无法按顺序分配仪器编号：\n" + "\n".join(blocked)
        if INSTRUMENT_DISPATCH_SOFT_MODE:
            return InstrumentDispatchCheckResult(
                ok=True,
                message=msg,
                blocked=blocked,
                warning_only=True,
            )
        return InstrumentDispatchCheckResult(ok=False, message=msg, blocked=blocked)

    project.assigned_instrument_ids = serialize_project_assigned_instruments(
        quality_control_ids=assigned[SCOPE_QC],
        radiation_protection_ids=assigned[SCOPE_RP],
    )
    project.save(update_fields=["assigned_instrument_ids", "updated_at"])

    all_ids = _sort_ids_by_code(bound_instrument_ids_for_legacy_list(assigned))
    n_in, n_out, errs = sync_project_instrument_checkout(
        project,
        all_ids,
        user=user,
        checkout_note="人员派工：旧版绑定，按台账编号顺序出库",
        checkin_note="人员派工：旧版重分配，取消旧绑定并入库",
    )
    if errs:
        msg = "编号已分配但出库登记未完成：\n" + "\n".join(errs)
        if INSTRUMENT_DISPATCH_SOFT_MODE:
            return InstrumentDispatchCheckResult(
                ok=True,
                message=msg,
                blocked=errs,
                assigned_ids=assigned,
                checked_out_count=n_out,
                checked_in_count=n_in,
                warning_only=True,
            )
        return InstrumentDispatchCheckResult(
            ok=False,
            message=msg,
            blocked=errs,
            assigned_ids=assigned,
            checked_in_count=n_in,
        )

    extra = f"（入库 {n_in} 台）" if n_in else ""
    return InstrumentDispatchCheckResult(
        ok=True,
        message=f"已按顺序分配并同步出库 {len(all_ids)} 台仪器{extra}（可与其他委托共用）。",
        checked_out_count=n_out,
        checked_in_count=n_in,
        assigned_ids=assigned,
    )


def build_project_instrument_dispatch_panel(project: LibraryProject) -> dict:
    """人员派工页：种类需求 + 已分配编号 + 可否下发。"""
    kind_slots = merged_kind_slots_for_project(project)
    uses_kinds = bool(kind_slots) or project_uses_kind_binding(project)
    blocked: list[str] = []
    info_messages: list[str] = []
    can_dispatch = True
    legacy_preview_blocked: list[str] = []

    assigned_only = normalize_project_assigned_instruments(
        getattr(project, "assigned_instrument_ids", None) or {}
    )
    if bound_instrument_ids_for_legacy_list(assigned_only):
        resolved = assigned_only
    elif uses_kinds:
        resolved = resolved_instrument_ids_for_project(project)
    else:
        resolved, legacy_preview_blocked = _resolve_legacy_slots_to_assigned(project)
    resolved_ids = bound_instrument_ids_for_legacy_list(resolved)

    from collections import Counter

    share_across = bool(getattr(project, "instrument_share_across_tasks", False))
    slot_counts = Counter(
        (slot.task_id, slot.spec.key()) if not share_across else slot.spec.key()
        for slot in kind_slots
    )
    kind_slot_counts = Counter(slot.spec.key() for slot in kind_slots)
    required_physical = len(slot_counts)

    kind_rows: list[dict] = []
    scoped_kind_rows: dict[str, list[dict]] = {SCOPE_QC: [], SCOPE_RP: []}
    seen_kind: set[tuple[str, str]] = set()
    seen_kind_by_scope: dict[str, set[tuple[str, str]]] = {SCOPE_QC: set(), SCOPE_RP: set()}
    task_codes_by_kind: dict[tuple[str, str], list[str]] = {}
    task_codes_by_kind_scope: dict[tuple[str, str, str], list[str]] = {}
    for slot in kind_slots:
        k = slot.spec.key()
        if slot.task_code and slot.task_code not in task_codes_by_kind.setdefault(k, []):
            task_codes_by_kind[k].append(slot.task_code)
        sk = (slot.scope, k[0], k[1])
        if slot.task_code and slot.task_code not in task_codes_by_kind_scope.setdefault(sk, []):
            task_codes_by_kind_scope[sk].append(slot.task_code)
    for slot in kind_slots:
        k = slot.spec.key()
        if k in seen_kind:
            pass
        else:
            seen_kind.add(k)
            avail = availability_for_kind(slot.spec.name, slot.spec.model)
            kind_rows.append(
                {
                    "name": slot.spec.name,
                    "model": slot.spec.model,
                    "task_codes": list(task_codes_by_kind.get(k) or []),
                    "kind_in_stock": avail["in_stock"],
                    "kind_total": avail["total"],
                    "slot_repeat_count": kind_slot_counts.get(k, 0),
                    "referenced_by_multiple_tasks": len(task_codes_by_kind.get(k) or []) > 1,
                }
            )
        scope = slot.scope if slot.scope in (SCOPE_QC, SCOPE_RP) else SCOPE_QC
        if k in seen_kind_by_scope[scope]:
            continue
        seen_kind_by_scope[scope].add(k)
        avail = availability_for_kind(slot.spec.name, slot.spec.model)
        scoped_kind_rows[scope].append(
            {
                "name": slot.spec.name,
                "model": slot.spec.model,
                "scope": scope,
                "scope_label": "质控（性能）" if scope == SCOPE_QC else "防护",
                "task_codes": list(
                    task_codes_by_kind_scope.get((scope, k[0], k[1])) or []
                ),
                "kind_in_stock": avail["in_stock"],
                "kind_total": avail["total"],
                "slot_repeat_count": kind_slot_counts.get(k, 0),
            }
        )
    assigned_raw = getattr(project, "assigned_instrument_ids", None) or {}
    by_task_map = assigned_raw.get("byTask") if isinstance(assigned_raw, dict) else {}
    task_code_by_id = {slot.task_id: slot.task_code for slot in kind_slots}

    inst_rows: list[dict] = []

    if legacy_preview_blocked:
        if INSTRUMENT_DISPATCH_SOFT_MODE:
            info_messages.extend(legacy_preview_blocked)
        else:
            blocked.extend(legacy_preview_blocked)
            can_dispatch = False

    if uses_kinds and not resolved_ids:
        info_messages.append(
            "尚未分配具体仪器编号；确认同步 App 任务时将尝试自动分配（失败也不阻断派工）。"
        )

    scoped_required_rows: dict[str, list[dict]] = {SCOPE_QC: [], SCOPE_RP: []}

    def _append_assigned_row(pk: int, scope: str) -> None:
        nonlocal can_dispatch
        inst = (
            InstrumentCatalog.objects.filter(pk=pk)
            .select_related("checkout_project")
            .first()
        )
        if inst is None:
            row = {
                "code": f"#{pk}",
                "name": "（不存在）",
                "status": "missing",
                "status_label": "不存在",
                "shared_in_task": False,
                "task_codes": [],
                "scope": scope,
                "scope_label": "质控（性能）" if scope == SCOPE_QC else "防护",
            }
            inst_rows.append(row)
            scoped_required_rows[scope].append(row)
            if not INSTRUMENT_DISPATCH_SOFT_MODE:
                can_dispatch = False
            else:
                info_messages.append(f"仪器 #{pk} 在台账中不存在，请核对登记。")
            return
        active = _active_checkout_project_ids(inst)
        if project.pk in active:
            status, status_label = "on_project", "已关联本项目（可共用）"
        elif active:
            status, status_label = "shared", "已出库至其他项目（可共用）"
        else:
            status, status_label = "in_stock", "在库（待出库）"

        kind_key = instrument_kind_key(inst.name, inst.model or "")
        task_codes: list[str] = []
        shared_in_task = False
        for task_key, scopes in (by_task_map or {}).items():
            try:
                tid = int(task_key)
            except (TypeError, ValueError):
                continue
            uses_pk = pk in (scopes.get(SCOPE_QC) or []) or pk in (scopes.get(SCOPE_RP) or [])
            if not uses_pk:
                continue
            code = task_code_by_id.get(tid) or f"#{tid}"
            if code not in task_codes:
                task_codes.append(code)
            cnt_key = (tid, kind_key) if not share_across else kind_key
            if slot_counts.get(cnt_key, 0) > 1:
                shared_in_task = True
        if not shared_in_task and len(task_codes) == 1:
            cnt_key = None
            for slot in kind_slots:
                if slot.task_code == task_codes[0] and slot.spec.key() == kind_key:
                    cnt_key = (slot.task_id, kind_key) if not share_across else kind_key
                    break
            if cnt_key and slot_counts.get(cnt_key, 0) > 1:
                shared_in_task = True

        row = {
            "id": inst.pk,
            "code": inst.code,
            "name": inst.name,
            "model": inst.model or "",
            "status": status,
            "status_label": status_label,
            "shared_in_task": shared_in_task,
            "task_codes": task_codes,
            "scope": scope,
            "scope_label": "质控（性能）" if scope == SCOPE_QC else "防护",
        }
        inst_rows.append(row)
        scoped_required_rows[scope].append(row)

    for scope in (SCOPE_QC, SCOPE_RP):
        for pk in list(resolved.get(scope) or []):
            _append_assigned_row(int(pk), scope)

    if not uses_kinds and not resolved_ids:
        hint = "任务模板未配置仪器，派工不受仪器限制。"
        can_dispatch = True
    elif uses_kinds and not resolved_ids:
        if share_across:
            hint = (
                "仪器台账为辅助信息，不阻断向检测人员同步 App 任务。"
                "同步时将尝试按种类自动分配编号；跨模板共用策略见下方勾选。"
            )
        else:
            hint = (
                "仪器台账为辅助信息，不阻断派工。"
                "各模板将分别分配编号（同模板多台设备共用）；同步 App 任务时会尝试自动分配。"
            )
    elif not uses_kinds and resolved_ids:
        hint = "旧版模板绑定；同步 App 任务时按台账编号顺序出库（占用时自动选用同种下一台）。"
    elif can_dispatch:
        hint = "种类与编号已就绪；同步任务时将登记出库至本项目。"
    else:
        hint = "请确认台账中有对应种类的启用仪器后，再同步 App 任务。"

    return {
        "uses_kind_binding": uses_kinds,
        "kind_rows": kind_rows,
        "scoped_kind_rows": scoped_kind_rows,
        "scoped_required_rows": scoped_required_rows,
        "kind_slot_count": len(kind_slots),
        "required_physical_count": required_physical,
        "instrument_share_across_tasks": share_across,
        "required_rows": inst_rows,
        "required_count": len(resolved_ids) if resolved_ids else required_physical,
        "can_dispatch": can_dispatch,
        "blocked_messages": blocked,
        "info_messages": info_messages,
        "instrument_dispatch_soft_mode": INSTRUMENT_DISPATCH_SOFT_MODE,
        "hint": hint,
        "on_project_count": sum(1 for r in inst_rows if r.get("status") == "on_project"),
        "in_stock_count": sum(1 for r in inst_rows if r.get("status") == "in_stock"),
        "assigned_binding": resolved,
        "share_mode": project_instrument_share_mode(project),
        "manual_picker_catalog": build_manual_instrument_picker_catalog(project)
        if uses_kinds
        else {"kinds": []},
        "assignment_mode": project_assignment_mode(
            getattr(project, "assigned_instrument_ids", None) or {}
        ),
    }


def ensure_instruments_for_project_dispatch(
    project: LibraryProject,
    *,
    user: User | None = None,
) -> InstrumentDispatchCheckResult:
    """派工：种类模式或旧版 id 模式均先解析编号（按顺序），再出库。"""
    raw = getattr(project, "assigned_instrument_ids", None) or {}
    if project_assignment_mode(raw) == ASSIGNMENT_MODE_MANUAL and normalize_project_by_kind(raw):
        all_ids = required_instrument_ids_for_project(project)
        n_in, n_out, errs = sync_project_instrument_checkout(
            project,
            all_ids,
            user=user,
            checkout_note="人员派工：按手动分配结果出库",
            checkin_note="人员派工：手动分配变更，取消绑定并入库",
        )
        if not all_ids and not n_in:
            return InstrumentDispatchCheckResult(ok=True, message="手动分配为空，无仪器需同步。")
        parts = []
        if n_in:
            parts.append(f"入库 {n_in} 台")
        if n_out:
            parts.append(f"出库 {n_out} 台")
        summary = "、".join(parts) if parts else "出库状态已与分配一致"
        if errs and INSTRUMENT_DISPATCH_SOFT_MODE:
            return InstrumentDispatchCheckResult(
                ok=True,
                message=f"已同步手动分配（{summary}）；部分未完成：\n" + "\n".join(errs),
                checked_out_count=n_out,
                checked_in_count=n_in,
                warning_only=True,
            )
        if errs:
            return InstrumentDispatchCheckResult(
                ok=False,
                message=f"同步出库失败：\n" + "\n".join(errs),
                blocked=errs,
                checked_in_count=n_in,
            )
        return InstrumentDispatchCheckResult(
            ok=True,
            message=f"已按手动分配同步台账（{summary}）。",
            checked_out_count=n_out,
            checked_in_count=n_in,
        )
    if project_uses_kind_binding(project):
        return allocate_instruments_for_project(project, user=user)
    return allocate_legacy_instruments_for_project(project, user=user)


def auto_checkout_on_detection_phase(
    project: LibraryProject,
    *,
    user: User | None = None,
) -> InstrumentDispatchCheckResult:
    """
    项目进入检测环节时自动分配仪器编号并出库（人员派工同步、委托设备就绪等触发）。
    无仪器种类要求时静默跳过。
    """
    if not project or not getattr(project, "is_active", True):
        return InstrumentDispatchCheckResult(ok=True, message="项目未启用，已跳过仪器出库。")
    return ensure_instruments_for_project_dispatch(project, user=user)


def auto_checkin_on_commission_end(
    project: LibraryProject,
    *,
    user: User | None = None,
) -> int:
    """委托结束（项目停用等）时自动将本项目已出库仪器全部入库。"""
    if not project:
        return 0
    return checkin_instruments_from_project(
        project,
        None,
        user=user,
        note="委托结束自动入库",
    )
