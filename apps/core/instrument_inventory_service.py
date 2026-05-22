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
    BINDING_MODE_KINDS,
    InstrumentKindSpec,
    SCOPE_QC,
    SCOPE_RP,
    binding_mode,
    bound_instrument_ids_for_legacy_list,
    normalize_project_assigned_instruments,
    normalize_task_bound_instruments,
    normalize_task_bound_kinds,
    serialize_project_assigned_instruments,
)


@dataclass
class InstrumentDispatchCheckResult:
    ok: bool
    message: str
    checked_out_count: int = 0
    already_on_project_count: int = 0
    blocked: list[str] | None = None
    assigned_ids: dict[str, list[int]] | None = None


def instrument_kind_key(name: str, model: str = "") -> tuple[str, str]:
    return ((name or "").strip(), (model or "").strip())


def merged_kind_slots_for_project(project: LibraryProject) -> list[tuple[str, InstrumentKindSpec]]:
    """
    汇总本项目任务模板所需的仪器「种类」槽位（质控在前、防护在后）。
    多个模板需要同种仪器时，槽位会重复出现，派工时各占一台在库设备。
    """
    slots: list[tuple[str, InstrumentKindSpec]] = []
    for task in project_tasks_for_user_assignment(project):
        raw = getattr(task, "bound_instrument_ids", None) or []
        if binding_mode(raw) != BINDING_MODE_KINDS:
            continue
        kinds = normalize_task_bound_kinds(raw)
        for spec in kinds.get(SCOPE_QC) or []:
            slots.append((SCOPE_QC, spec))
        for spec in kinds.get(SCOPE_RP) or []:
            slots.append((SCOPE_RP, spec))
    return slots


def project_uses_kind_binding(project: LibraryProject) -> bool:
    for task in project_tasks_for_user_assignment(project):
        if binding_mode(getattr(task, "bound_instrument_ids", None) or []) == BINDING_MODE_KINDS:
            return True
    return False


def resolved_instrument_ids_for_project(project: LibraryProject) -> dict[str, list[int]]:
    """
    项目实际使用的仪器编号：优先 ``assigned_instrument_ids``（派工分配）；
    否则回退任务模板旧版直接绑 id。
    """
    assigned = normalize_project_assigned_instruments(
        getattr(project, "assigned_instrument_ids", None) or {}
    )
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
    exclude_ids: set[int],
) -> InstrumentCatalog | None:
    """为本项目槽位按台账编号顺序选取一台在库或已归属本项目的同种仪器。"""
    qs = _queryset_for_kind(spec.name, spec.model).select_related("checkout_project")
    on_project = sorted(
        (i for i in qs.filter(checkout_project=project) if i.pk not in exclude_ids),
        key=lambda row: (row.code or "", row.pk),
    )
    if on_project:
        return on_project[0]
    in_stock = list(
        qs.filter(checkout_project__isnull=True).order_by("code")
    )
    for inst in in_stock:
        if inst.pk not in exclude_ids:
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
    if pref.pk not in exclude_ids and _instrument_busy_elsewhere(pref, project) is None:
        if pref.checkout_project_id is None or pref.checkout_project_id == project.pk:
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


def availability_for_kind(name: str, model: str = "") -> dict:
    n, m = instrument_kind_key(name, model)
    qs = _queryset_for_kind(n, m)
    total = qs.count()
    in_stock = qs.filter(checkout_project__isnull=True).count()
    return {
        "name": n,
        "model": m,
        "total": total,
        "in_stock": in_stock,
        "checked_out": total - in_stock,
    }


def _instrument_busy_elsewhere(inst: InstrumentCatalog, project: LibraryProject) -> str | None:
    if not inst.checkout_project_id:
        return None
    if inst.checkout_project_id == project.pk:
        return None
    proj = inst.checkout_project
    label = f"{proj.code} · {proj.name}" if proj else f"项目#{inst.checkout_project_id}"
    return f"仪器 {inst.code}（{inst.name}）已出库至 {label}"


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
        busy = _instrument_busy_elsewhere(inst, project)
        if busy:
            errors.append(busy)
            continue
        if inst.checkout_project_id == project.pk:
            continue
        inst.checkout_project = project
        inst.checked_out_at = now
        inst.checked_out_by = user
        inst.save(
            update_fields=["checkout_project", "checked_out_at", "checked_out_by", "updated_at"]
        )
        InstrumentCheckoutLog.objects.create(
            instrument=inst,
            project=project,
            event_type=InstrumentCheckoutLog.EVENT_CHECKOUT,
            performed_by=user,
            note=note or "",
        )
        count += 1
    return count, errors


@transaction.atomic
def checkin_instruments_from_project(
    project: LibraryProject,
    instrument_ids: Iterable[int] | None = None,
    *,
    user: User | None = None,
    note: str = "",
) -> int:
    qs = InstrumentCatalog.objects.select_for_update().filter(checkout_project=project)
    if instrument_ids is not None:
        ids = [int(x) for x in instrument_ids if int(x) > 0]
        qs = qs.filter(pk__in=ids)
    n = 0
    for inst in qs:
        inst.checkout_project = None
        inst.checked_out_at = None
        inst.checked_out_by = None
        inst.save(
            update_fields=["checkout_project", "checked_out_at", "checked_out_by", "updated_at"]
        )
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

    assigned: dict[str, list[int]] = {SCOPE_QC: [], SCOPE_RP: []}
    used: set[int] = set()
    blocked: list[str] = []

    for scope, spec in slots:
        inst = pick_instrument_for_kind(spec, project, exclude_ids=used)
        if inst is None:
            avail = availability_for_kind(spec.name, spec.model)
            label = spec.name + (f" ({spec.model})" if spec.model else "")
            blocked.append(
                f"{label}：需要 1 台，同种在库 {avail['in_stock']}/{avail['total']} 台（均已被其他项目占用）"
            )
            continue
        used.add(inst.pk)
        assigned[scope].append(inst.pk)

    if blocked:
        return InstrumentDispatchCheckResult(
            ok=False,
            message="无法分配仪器编号：\n" + "\n".join(blocked),
            blocked=blocked,
        )

    project.assigned_instrument_ids = serialize_project_assigned_instruments(
        quality_control_ids=assigned[SCOPE_QC],
        radiation_protection_ids=assigned[SCOPE_RP],
    )
    project.save(update_fields=["assigned_instrument_ids", "updated_at"])

    all_ids = _sort_ids_by_code(bound_instrument_ids_for_legacy_list(assigned))
    n, errs = checkout_instruments_to_project(
        project,
        all_ids,
        user=user,
        note="人员派工：按种类与台账编号顺序分配并出库",
    )
    if errs:
        return InstrumentDispatchCheckResult(
            ok=False,
            message="编号已分配但出库失败：\n" + "\n".join(errs),
            blocked=errs,
            assigned_ids=assigned,
        )

    return InstrumentDispatchCheckResult(
        ok=True,
        message=f"已为本项目分配并出库 {len(all_ids)} 台仪器（质控 {len(assigned[SCOPE_QC])}、防护 {len(assigned[SCOPE_RP])}），按台账编号顺序选取。",
        checked_out_count=n,
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
        return InstrumentDispatchCheckResult(
            ok=False,
            message="无法按顺序分配仪器编号：\n" + "\n".join(blocked),
            blocked=blocked,
        )

    project.assigned_instrument_ids = serialize_project_assigned_instruments(
        quality_control_ids=assigned[SCOPE_QC],
        radiation_protection_ids=assigned[SCOPE_RP],
    )
    project.save(update_fields=["assigned_instrument_ids", "updated_at"])

    all_ids = _sort_ids_by_code(bound_instrument_ids_for_legacy_list(assigned))
    to_checkout = []
    for pk in all_ids:
        inst = InstrumentCatalog.objects.filter(pk=pk).first()
        if inst and inst.checkout_project_id is None:
            to_checkout.append(pk)

    if not to_checkout:
        on_proj = sum(
            1
            for pk in all_ids
            if InstrumentCatalog.objects.filter(
                pk=pk, checkout_project=project
            ).exists()
        )
        if on_proj == len(all_ids):
            return InstrumentDispatchCheckResult(
                ok=True,
                message=f"本项目 {len(all_ids)} 台仪器均已按分配编号出库。",
                already_on_project_count=on_proj,
                assigned_ids=assigned,
            )
        return InstrumentDispatchCheckResult(
            ok=True,
            message="编号已写入项目，无待出库设备。",
            assigned_ids=assigned,
        )

    n, errs = checkout_instruments_to_project(
        project,
        to_checkout,
        user=user,
        note="人员派工：旧版绑定，按台账编号顺序出库",
    )
    if errs:
        return InstrumentDispatchCheckResult(
            ok=False,
            message="编号已分配但出库失败：\n" + "\n".join(errs),
            blocked=errs,
            assigned_ids=assigned,
        )

    return InstrumentDispatchCheckResult(
        ok=True,
        message=f"已按顺序分配并出库 {len(to_checkout)} 台仪器（共需 {len(all_ids)} 台）。",
        checked_out_count=n,
        assigned_ids=assigned,
    )


def build_project_instrument_dispatch_panel(project: LibraryProject) -> dict:
    """人员派工页：种类需求 + 已分配编号 + 可否下发。"""
    kind_slots = merged_kind_slots_for_project(project)
    uses_kinds = bool(kind_slots) or project_uses_kind_binding(project)
    blocked: list[str] = []
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

    kind_rows: list[dict] = []
    for scope, spec in kind_slots:
        avail = availability_for_kind(spec.name, spec.model)
        kind_rows.append(
            {
                "scope": scope,
                "scope_label": "质控" if scope == SCOPE_QC else "防护",
                "name": spec.name,
                "model": spec.model,
                "kind_in_stock": avail["in_stock"],
                "kind_total": avail["total"],
            }
        )

    inst_rows: list[dict] = []

    if legacy_preview_blocked:
        blocked.extend(legacy_preview_blocked)
        can_dispatch = False

    if uses_kinds and not resolved_ids:
        can_dispatch = False
        blocked.append("尚未分配具体仪器编号，请执行「同步 App 任务」时自动分配（或先确认同种仪器有在库台数）。")

    for pk in resolved_ids:
        inst = (
            InstrumentCatalog.objects.filter(pk=pk)
            .select_related("checkout_project")
            .first()
        )
        if inst is None:
            inst_rows.append(
                {
                    "code": f"#{pk}",
                    "name": "（不存在）",
                    "status": "missing",
                    "status_label": "不存在",
                }
            )
            can_dispatch = False
            continue
        if inst.checkout_project_id is None:
            status, status_label = "in_stock", "在库（待出库）"
        elif inst.checkout_project_id == project.pk:
            status, status_label = "on_project", "已出库至本项目"
        else:
            if uses_kinds:
                status, status_label = "busy", "已出库至其他项目"
                cp = inst.checkout_project
                pl = f"{cp.code} · {cp.name}" if cp else ""
                blocked.append(f"{inst.code} 已被 {pl} 占用")
                can_dispatch = False
            else:
                status, status_label = "busy", "将顺延同种下一编号"
        inst_rows.append(
            {
                "id": inst.pk,
                "code": inst.code,
                "name": inst.name,
                "model": inst.model or "",
                "status": status,
                "status_label": status_label,
            }
        )

    if not uses_kinds and not resolved_ids:
        hint = "任务模板未配置仪器，派工不受仪器限制。"
        can_dispatch = True
    elif uses_kinds and not resolved_ids:
        hint = "模板仅指定仪器种类；同步 App 任务时按台账编号顺序从在库自动分配。"
    elif not uses_kinds and resolved_ids:
        hint = "旧版模板绑定；同步 App 任务时按台账编号顺序出库（占用时自动选用同种下一台）。"
    elif can_dispatch:
        hint = "种类与编号已就绪；同步任务时将按顺序确保仪器出库至本项目。"
    else:
        hint = "请处理占用或在库不足后，再同步 App 任务。"

    return {
        "uses_kind_binding": uses_kinds,
        "kind_rows": kind_rows,
        "kind_slot_count": len(kind_slots),
        "required_rows": inst_rows,
        "required_count": len(resolved_ids) if resolved_ids else len(kind_slots),
        "can_dispatch": can_dispatch,
        "blocked_messages": blocked,
        "hint": hint,
        "on_project_count": sum(1 for r in inst_rows if r.get("status") == "on_project"),
        "in_stock_count": sum(1 for r in inst_rows if r.get("status") == "in_stock"),
        "assigned_binding": resolved,
    }


def ensure_instruments_for_project_dispatch(
    project: LibraryProject,
    *,
    user: User | None = None,
) -> InstrumentDispatchCheckResult:
    """派工：种类模式或旧版 id 模式均先解析编号（按顺序），再出库。"""
    if project_uses_kind_binding(project):
        return allocate_instruments_for_project(project, user=user)
    return allocate_legacy_instruments_for_project(project, user=user)
