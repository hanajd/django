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
    assigned_instruments_for_detection_item,
    assigned_instruments_for_task,
    binding_mode,
    bound_instrument_ids_for_legacy_list,
    detection_item_instrument_key,
    kind_form_value,
    kind_spec_from_form_value,
    normalize_project_assigned_instruments,
    normalize_project_by_detection_item,
    normalize_project_by_kind,
    normalize_project_by_requirement,
    normalize_task_bound_instruments,
    normalize_task_bound_kinds,
    parse_detection_item_instrument_key,
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


def detection_items_for_project_instruments(project: LibraryProject) -> list[dict]:
    """
    按「委托设备 × 现场记录任务」展开检测项，供仪器管理按项分配。
    """
    from apps.core.project_equipment_service import (
        equipment_title_from_card,
        project_equipment_cards,
    )

    items: list[dict] = []
    task_cache: dict[int, LibraryTask | None] = {}
    for card in project_equipment_cards(project):
        link = card["link"]
        equip_title = equipment_title_from_card(card)
        report_label = (card.get("report_task_label") or "").strip()
        for row in card.get("site_submit_tasks") or []:
            lib_tid = int(row.get("libraryTaskId") or 0)
            if lib_tid <= 0:
                continue
            if lib_tid not in task_cache:
                task_cache[lib_tid] = LibraryTask.objects.filter(pk=lib_tid).first()
            site_task = task_cache[lib_tid]
            if site_task is None:
                continue
            raw_bind = getattr(site_task, "bound_instrument_ids", None) or []
            scoped_kinds: dict[str, list[dict]] = {SCOPE_QC: [], SCOPE_RP: []}
            has_template_kinds = False
            if binding_mode(raw_bind) == BINDING_MODE_KINDS:
                kinds = normalize_task_bound_kinds(raw_bind)
                for scope in (SCOPE_QC, SCOPE_RP):
                    for spec in kinds.get(scope) or []:
                        has_template_kinds = True
                        scoped_kinds[scope].append(
                            {
                                "key": kind_form_value(spec.name, spec.model),
                                "name": spec.name,
                                "model": spec.model,
                                "scope": scope,
                            }
                        )
            code = (row.get("code") or "").strip()
            name = (row.get("name") or "").strip()
            site_label = f"{code} · {name}" if code else (name or "—")
            items.append(
                {
                    "key": detection_item_instrument_key(link.pk, lib_tid),
                    "linkId": int(link.pk),
                    "libraryTaskId": lib_tid,
                    "equipmentTitle": equip_title,
                    "reportTaskLabel": report_label,
                    "siteTaskLabel": site_label,
                    "detectionLabel": f"{equip_title} · {site_label}",
                    "taskNo": str(row.get("taskNo") or "").strip(),
                    "taskCode": code,
                    "taskName": name,
                    "scopedKinds": scoped_kinds,
                    "hasTemplateKinds": has_template_kinds,
                }
            )
    return items


def _selected_ids_for_kind_in_binding(
    binding: dict[str, list[int]],
    scope: str,
    spec: InstrumentKindSpec,
    catalog_buckets: dict[tuple[str, str], list[InstrumentCatalog]],
) -> list[int]:
    scope_ids = {int(x) for x in (binding.get(scope) or []) if int(x) > 0}
    kind_ids = {int(inst.pk) for inst in catalog_buckets.get(spec.key(), [])}
    return [pk for pk in scope_ids if pk in kind_ids]


def build_manual_instrument_picker_catalog(project: LibraryProject) -> dict:
    """
    手动分配悬浮窗：按检测项（委托设备×现场记录）列出所需仪器种类与台账选项。
    """
    detection_items = detection_items_for_project_instruments(project)
    if not detection_items:
        if merged_kind_slots_for_project(project):
            return _legacy_build_kind_picker_catalog(project)
        return {"items": [], "projectId": project.pk}

    raw = getattr(project, "assigned_instrument_ids", None) or {}
    by_item = normalize_project_by_detection_item(raw)
    legacy_by_kind = normalize_project_by_kind(raw)

    catalog = list(
        InstrumentCatalog.objects.filter(is_active=True)
        .select_related("checkout_project")
        .order_by("name", "model", "code", "id")
    )
    buckets: dict[tuple[str, str], list[InstrumentCatalog]] = {}
    for inst in catalog:
        k = instrument_kind_key(inst.name, inst.model or "")
        buckets.setdefault(k, []).append(inst)

    picker_items: list[dict] = []
    for item in detection_items:
        key = item["key"]
        binding = by_item.get(key) or {SCOPE_QC: [], SCOPE_RP: []}
        if not bound_instrument_ids_for_legacy_list(binding) and legacy_by_kind:
            binding = _binding_from_legacy_by_kind(item, legacy_by_kind, buckets)
        scopes_out: list[dict] = []
        for scope in (SCOPE_QC, SCOPE_RP):
            kinds_out: list[dict] = []
            for kind_row in item["scopedKinds"].get(scope) or []:
                spec = InstrumentKindSpec(
                    name=kind_row["name"],
                    model=kind_row.get("model") or "",
                )
                selected = _selected_ids_for_kind_in_binding(binding, scope, spec, buckets)
                avail = availability_for_kind(spec.name, spec.model)
                kinds_out.append(
                    {
                        "key": kind_row["key"],
                        "name": spec.name,
                        "model": spec.model,
                        "scope": scope,
                        "selectedIds": selected,
                        "kindTotal": avail["total"],
                        "kindInStock": avail["in_stock"],
                        "instruments": [
                            _instrument_picker_row(inst, project)
                            for inst in buckets.get(spec.key(), [])
                        ],
                    }
                )
            if kinds_out:
                scopes_out.append(
                    {
                        "scope": scope,
                        "scopeLabel": "质控（性能）" if scope == SCOPE_QC else "工作场所放射防护",
                        "kinds": kinds_out,
                    }
                )
            else:
                scope_selected = [
                    int(pk)
                    for pk in (binding.get(scope) or [])
                    if int(pk) > 0
                ]
                scopes_out.append(
                    {
                        "scope": scope,
                        "scopeLabel": "质控（性能）" if scope == SCOPE_QC else "工作场所放射防护",
                        "freeSelect": True,
                        "selectedIds": scope_selected,
                        "instruments": [
                            _instrument_picker_row(inst, project) for inst in catalog
                        ],
                    }
                )
        picker_items.append(
            {
                "key": key,
                "linkId": item["linkId"],
                "libraryTaskId": item["libraryTaskId"],
                "equipmentTitle": item["equipmentTitle"],
                "siteTaskLabel": item["siteTaskLabel"],
                "detectionLabel": item["detectionLabel"],
                "taskNo": item["taskNo"],
                "taskCode": item.get("taskCode") or "",
                "taskName": item.get("taskName") or "",
                "hasTemplateKinds": bool(item.get("hasTemplateKinds")),
                "selectedBinding": binding,
                "scopes": scopes_out,
            }
        )

    return {
        "projectId": project.pk,
        "assignmentMode": project_assignment_mode(raw),
        "items": picker_items,
    }


def _binding_from_legacy_by_kind(
    item: dict,
    by_kind: dict[str, list[int]],
    buckets: dict[tuple[str, str], list[InstrumentCatalog]],
) -> dict[str, list[int]]:
    """旧版按种类汇总分配 → 展开到单个检测项（便于迁移展示）。"""
    binding: dict[str, list[int]] = {SCOPE_QC: [], SCOPE_RP: []}
    for scope in (SCOPE_QC, SCOPE_RP):
        for kind_row in item["scopedKinds"].get(scope) or []:
            spec = InstrumentKindSpec(
                name=kind_row["name"],
                model=kind_row.get("model") or "",
            )
            for pk in by_kind.get(kind_row["key"]) or []:
                if pk in {int(x.pk) for x in buckets.get(spec.key(), [])}:
                    if pk not in binding[scope]:
                        binding[scope].append(int(pk))
    return binding


def _legacy_build_kind_picker_catalog(project: LibraryProject) -> dict:
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
    by_kind: dict[str, list[int]] | None = None,
    *,
    by_detection_item: dict[str, dict[str, list[int]]] | None = None,
    user: User | None = None,
    checkout: bool = True,
) -> InstrumentDispatchCheckResult:
    """保存手动仪器分配并登记出库；支持按检测项或按种类（旧版）。"""
    if by_detection_item is not None:
        return _apply_detection_item_instrument_assignment(
            project,
            by_detection_item,
            user=user,
            checkout=checkout,
        )

    by_kind = by_kind or {}
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
    if errs:
        parts.append("；".join(errs))
    msg = "，".join(parts) + "。"
    if errs and not INSTRUMENT_DISPATCH_SOFT_MODE:
        return InstrumentDispatchCheckResult(
            ok=False,
            message=msg,
            blocked=errs,
            assigned_ids=assigned,
            checked_out_count=n_out,
            checked_in_count=n_in,
        )
    if errs:
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
        ok=True,
        message=msg,
        checked_out_count=n_out,
        checked_in_count=n_in,
        assigned_ids=assigned,
    )


@transaction.atomic
def _apply_detection_item_instrument_assignment(
    project: LibraryProject,
    by_detection_item: dict[str, dict[str, list[int]]],
    *,
    user: User | None = None,
    checkout: bool = True,
) -> InstrumentDispatchCheckResult:
    """按检测项（委托设备×现场记录）保存仪器分配。"""
    valid_items = {
        item["key"]: item for item in detection_items_for_project_instruments(project)
    }
    if not valid_items:
        return InstrumentDispatchCheckResult(ok=True, message="本项目无按检测项分配的仪器需求。")

    cleaned: dict[str, dict[str, list[int]]] = {}
    for key, scopes in (by_detection_item or {}).items():
        k = str(key or "").strip()
        if k not in valid_items or not isinstance(scopes, dict):
            continue
        binding = {
            SCOPE_QC: _dedupe_id_list(scopes.get(SCOPE_QC) or []),
            SCOPE_RP: _dedupe_id_list(scopes.get(SCOPE_RP) or []),
        }
        cleaned[k] = binding

    assigned: dict[str, list[int]] = {SCOPE_QC: [], SCOPE_RP: []}
    for binding in cleaned.values():
        for scope in (SCOPE_QC, SCOPE_RP):
            for pk in binding.get(scope) or []:
                if pk not in assigned[scope]:
                    assigned[scope].append(pk)

    share_mode = SHARE_MODE_PROJECT if getattr(project, "instrument_share_across_tasks", False) else SHARE_MODE_PER_TASK
    project.assigned_instrument_ids = serialize_project_assigned_instruments(
        quality_control_ids=assigned[SCOPE_QC],
        radiation_protection_ids=assigned[SCOPE_RP],
        share_mode=share_mode,
        by_detection_item=cleaned,
        assignment_mode=ASSIGNMENT_MODE_MANUAL,
    )
    project.save(update_fields=["assigned_instrument_ids", "updated_at"])

    all_ids = _sort_ids_by_code(bound_instrument_ids_for_legacy_list(assigned))
    n_in, n_out, errs = sync_project_instrument_checkout(
        project,
        all_ids,
        user=user,
        allow_checkout=checkout,
        checkout_note="仪器管理：按检测项指定仪器并出库",
        checkin_note="仪器管理：取消绑定，登记入库",
    )
    parts: list[str] = [
        f"已按 {len(cleaned)} 个检测项保存仪器分配（共 {len(all_ids)} 台编号）"
    ]
    if n_in:
        parts.append(f"已入库 {n_in} 台（已取消本项目绑定）")
    if checkout and n_out:
        parts.append(f"已出库 {n_out} 台至本项目")
    if errs:
        parts.append("；".join(errs))
    msg = "，".join(parts) + "。"
    if errs and not INSTRUMENT_DISPATCH_SOFT_MODE:
        return InstrumentDispatchCheckResult(
            ok=False,
            message=msg,
            blocked=errs,
            assigned_ids=assigned,
            checked_out_count=n_out,
            checked_in_count=n_in,
        )
    if errs:
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
    *,
    equipment_link_id: int | None = None,
) -> dict[str, list[int]]:
    """
    项目实际使用的仪器编号：优先 ``assigned_instrument_ids``（派工分配）；
    指定 task_obj 时优先读 byDetectionItem / byTask 中该模板的编号。
    """
    raw = getattr(project, "assigned_instrument_ids", None) or {}
    by_item = normalize_project_by_detection_item(raw)
    if by_item and task_obj is not None and equipment_link_id:
        per_item = assigned_instruments_for_detection_item(
            raw, int(equipment_link_id), int(task_obj.pk)
        )
        if bound_instrument_ids_for_legacy_list(per_item):
            return per_item

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


def resolve_equipment_link_id_for_submit(
    project: LibraryProject,
    library_task: LibraryTask | None,
    equipment_info: dict | None,
) -> int | None:
    """从提交 payload 的设备信息反查委托设备 link，用于按检测项读取仪器。"""
    if project is None or library_task is None or not isinstance(equipment_info, dict):
        return None
    name = (
        str(
            equipment_info.get("name")
            or equipment_info.get("equipmentName")
            or equipment_info.get("deviceName")
            or ""
        )
        .strip()
    )
    serial = (
        str(
            equipment_info.get("serialNo")
            or equipment_info.get("serial")
            or equipment_info.get("serialNumber")
            or equipment_info.get("deviceSerial")
            or ""
        )
        .strip()
    )
    model = str(equipment_info.get("model") or equipment_info.get("deviceModel") or "").strip()
    if not name and not serial:
        return None

    from apps.core.project_equipment_service import (
        effective_report_task,
        normalize_equipment_report_task,
        project_equipment_queryset,
    )

    site_task_ids = {int(library_task.pk)}
    if library_task.output_target == LibraryTask.OUTPUT_SITE_RECORD:
        pass
    elif library_task.output_target == LibraryTask.OUTPUT_REPORT:
        site_task_ids = set()
        for st in library_task.report_source_tasks.filter(
            output_target=LibraryTask.OUTPUT_SITE_RECORD
        ):
            site_task_ids.add(int(st.pk))
    else:
        return None

    serial_matches: list[int] = []
    name_matches: list[int] = []
    for link in project_equipment_queryset(project):
        report = normalize_equipment_report_task(effective_report_task(link))
        if report is None:
            continue
        link_site_ids = {
            int(st.pk)
            for st in report.report_source_tasks.filter(
                output_target=LibraryTask.OUTPUT_SITE_RECORD
            )
        }
        if library_task.output_target == LibraryTask.OUTPUT_SITE_RECORD:
            if int(library_task.pk) not in link_site_ids:
                continue
        elif not link_site_ids.intersection(site_task_ids):
            continue
        eq = link.equipment
        eq_serial = (eq.serial_no or "").strip()
        eq_name = (eq.name or "").strip()
        eq_model = (eq.model or "").strip()
        if serial and eq_serial and serial == eq_serial:
            serial_matches.append(int(link.pk))
        if name and eq_name == name and (not model or eq_model == model):
            name_matches.append(int(link.pk))

    if len(serial_matches) == 1:
        return serial_matches[0]
    if len(name_matches) == 1:
        return name_matches[0]
    return None


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

    detection_items = detection_items_for_project_instruments(project)
    detection_item_rows = _build_detection_item_panel_rows(project, detection_items)
    uses_per_item_assignment = bool(detection_items)
    if uses_per_item_assignment:
        required_physical = sum(
            len(item["scopedKinds"].get(SCOPE_QC) or [])
            + len(item["scopedKinds"].get(SCOPE_RP) or [])
            for item in detection_items
        )
        hint = (
            "按「受检设备 × 现场记录」为每个检测项选择仪器；"
            "任务模板未预设种类时，可从台账自选质控/防护仪器。"
            "保存后 App 提交将按设备信息匹配对应仪器。"
        )

    manual_catalog = (
        build_manual_instrument_picker_catalog(project)
        if (uses_kinds or uses_per_item_assignment)
        else {"items": []}
    )

    return {
        "uses_kind_binding": uses_kinds,
        "uses_per_item_assignment": uses_per_item_assignment,
        "detection_item_rows": detection_item_rows,
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
        "manual_picker_catalog": manual_catalog,
        "assignment_mode": project_assignment_mode(
            getattr(project, "assigned_instrument_ids", None) or {}
        ),
    }


def _build_detection_item_panel_rows(
    project: LibraryProject,
    detection_items: list[dict],
) -> list[dict]:
    """仪器管理页：每个检测项的分配摘要。"""
    if not detection_items:
        return []
    raw = getattr(project, "assigned_instrument_ids", None) or {}
    by_item = normalize_project_by_detection_item(raw)
    legacy_by_kind = normalize_project_by_kind(raw)

    catalog = {
        int(r.pk): r
        for r in InstrumentCatalog.objects.filter(is_active=True).only(
            "pk", "code", "name", "model", "checkout_project_id", "checkout_project_ids"
        )
    }
    buckets: dict[tuple[str, str], set[int]] = {}
    for pk, inst in catalog.items():
        k = instrument_kind_key(inst.name, inst.model or "")
        buckets.setdefault(k, set()).add(pk)

    rows: list[dict] = []
    for item in detection_items:
        key = item["key"]
        binding = by_item.get(key) or {SCOPE_QC: [], SCOPE_RP: []}
        if not bound_instrument_ids_for_legacy_list(binding) and legacy_by_kind:
            binding = _binding_from_legacy_by_kind(item, legacy_by_kind, {
                k: [catalog[pk] for pk in pks if pk in catalog]
                for k, pks in buckets.items()
            })

        required_kind_count = sum(
            len(item["scopedKinds"].get(scope) or []) for scope in (SCOPE_QC, SCOPE_RP)
        )
        assigned_kind_count = 0
        inst_labels: list[str] = []
        bucket_lists = {
            k: [catalog[pk] for pk in ids if pk in catalog]
            for k, ids in buckets.items()
        }
        if required_kind_count > 0:
            for scope in (SCOPE_QC, SCOPE_RP):
                for kind_row in item["scopedKinds"].get(scope) or []:
                    spec = InstrumentKindSpec(
                        name=kind_row["name"],
                        model=kind_row.get("model") or "",
                    )
                    picked = _selected_ids_for_kind_in_binding(
                        binding, scope, spec, bucket_lists
                    )
                    if picked:
                        assigned_kind_count += 1
                        for pk in picked:
                            inst = catalog.get(int(pk))
                            if inst:
                                inst_labels.append(inst.code or f"#{pk}")
        else:
            for scope in (SCOPE_QC, SCOPE_RP):
                for pk in binding.get(scope) or []:
                    inst = catalog.get(int(pk))
                    if inst:
                        code = inst.code or f"#{pk}"
                        if code not in inst_labels:
                            inst_labels.append(code)

        has_template_kinds = bool(item.get("hasTemplateKinds"))
        if required_kind_count > 0:
            is_complete = assigned_kind_count >= required_kind_count
        else:
            is_complete = True

        rows.append(
            {
                "key": key,
                "equipment_title": item["equipmentTitle"],
                "site_task_label": item["siteTaskLabel"],
                "detection_label": item["detectionLabel"],
                "task_no": item["taskNo"],
                "required_kind_count": required_kind_count,
                "assigned_kind_count": assigned_kind_count,
                "has_template_kinds": has_template_kinds,
                "is_complete": is_complete,
                "instrument_codes": inst_labels,
            }
        )
    return rows


def instrument_pipeline_step_status(panel: dict | None) -> tuple[bool, str]:
    """
    概览「仪器管理」步骤是否已完成。

    已完成：无需配置仪器，或已满足种类/编号分配且无在库未出库、无效登记。
    待办：任务需要仪器但尚未分配完整，或已选编号仍有在库/缺失。
    """
    if not panel:
        return False, "请在仪器管理页分配检测仪器"

    uses_kinds = bool(panel.get("uses_kind_binding"))
    kind_slot_count = int(panel.get("kind_slot_count") or 0)
    required_physical = int(panel.get("required_physical_count") or 0)
    item_rows: list[dict] = list(panel.get("detection_item_rows") or [])
    rows: list[dict] = list(panel.get("required_rows") or [])

    if not uses_kinds and kind_slot_count == 0:
        return True, "本项目任务未配置仪器种类，无需分配"

    if item_rows:
        required_items = [r for r in item_rows if int(r.get("required_kind_count") or 0) > 0]
        incomplete = sum(1 for r in required_items if not r.get("is_complete"))
        if incomplete:
            return False, f"有 {incomplete} 个检测项尚未完成仪器分配"
        if rows:
            missing = sum(1 for r in rows if r.get("status") == "missing")
            if missing:
                return False, f"有 {missing} 条仪器登记无效，请在仪器管理页核对"
            in_stock = int(panel.get("in_stock_count") or 0)
            if in_stock > 0:
                return False, f"已选仪器中 {in_stock} 台仍在库，待出库至本项目"
            return True, f"已为 {len(item_rows)} 个检测项完成仪器分配"

    if not rows:
        if required_physical > 0:
            return False, f"需为 {required_physical} 个仪器位分配具体编号"
        return False, "请在仪器管理页分配检测仪器"

    missing = sum(1 for r in rows if r.get("status") == "missing")
    if missing:
        return False, f"有 {missing} 条仪器登记无效，请在仪器管理页核对"

    in_stock = int(panel.get("in_stock_count") or 0)
    if in_stock > 0:
        return False, f"已选 {len(rows)} 台，其中 {in_stock} 台仍在库，待出库至本项目"

    if panel.get("blocked_messages"):
        return False, str(panel["blocked_messages"][0])

    on_project = int(panel.get("on_project_count") or 0)
    shared = sum(1 for r in rows if r.get("status") == "shared")
    ready = on_project + shared
    if ready >= len(rows) and len(rows) >= max(required_physical, 1):
        return True, f"已分配 {len(rows)} 台仪器（{on_project} 台已关联本项目）"

    if panel.get("assignment_mode") == ASSIGNMENT_MODE_MANUAL and rows and in_stock == 0:
        return True, f"已手动分配 {len(rows)} 台仪器"

    return False, (panel.get("hint") or "请在仪器管理页完成仪器分配").strip()


def ensure_instruments_for_project_dispatch(
    project: LibraryProject,
    *,
    user: User | None = None,
) -> InstrumentDispatchCheckResult:
    """派工：种类模式或旧版 id 模式均先解析编号（按顺序），再出库。"""
    raw = getattr(project, "assigned_instrument_ids", None) or {}
    if project_assignment_mode(raw) == ASSIGNMENT_MODE_MANUAL and (
        normalize_project_by_kind(raw) or normalize_project_by_detection_item(raw)
    ):
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


def _remove_id_from_int_list(values: Iterable, instrument_id: int) -> list[int]:
    pk = int(instrument_id)
    out: list[int] = []
    for x in values or []:
        try:
            if int(x) == pk:
                continue
        except (TypeError, ValueError):
            continue
        out.append(int(x))
    return out


def _strip_instrument_from_assigned_raw(raw: dict, instrument_id: int) -> dict:
    """从项目 assigned_instrument_ids 各层列表中移除指定仪器编号。"""
    if not isinstance(raw, dict):
        return {}
    out = dict(raw)
    for scope in (SCOPE_QC, SCOPE_RP, "qc", "rp"):
        if scope in out and isinstance(out[scope], list):
            out[scope] = _remove_id_from_int_list(out[scope], instrument_id)
    for key in ("byKind", "byRequirement"):
        bucket = out.get(key)
        if isinstance(bucket, dict):
            out[key] = {
                str(k): _remove_id_from_int_list(v, instrument_id)
                for k, v in bucket.items()
                if isinstance(v, list)
            }
    by_task = out.get("byTask")
    if isinstance(by_task, dict):
        out["byTask"] = {
            str(k): _strip_instrument_from_assigned_raw(v, instrument_id)
            if isinstance(v, dict)
            else v
            for k, v in by_task.items()
        }
    by_item = out.get("byDetectionItem")
    if isinstance(by_item, dict):
        out["byDetectionItem"] = {
            str(k): _strip_instrument_from_assigned_raw(v, instrument_id)
            if isinstance(v, dict)
            else v
            for k, v in by_item.items()
        }
    return out


@transaction.atomic
def manual_checkin_instrument(
    instrument_id: int,
    *,
    user: User | None = None,
    note: str = "",
) -> tuple[bool, str]:
    """
    台账页手动落库：解除该仪器与全部项目的出库关联，并同步清理各项目分配列表。
    用于项目侧已还但台账仍显示「已出库」、影响后续出库时的人工纠正。
    """
    try:
        pk = int(instrument_id)
    except (TypeError, ValueError):
        return False, "仪器不存在"
    if pk <= 0:
        return False, "仪器不存在"

    inst = (
        InstrumentCatalog.objects.select_for_update()
        .filter(pk=pk, is_active=True)
        .first()
    )
    if inst is None:
        return False, "仪器不存在或已停用"

    active_project_ids = sorted(_active_checkout_project_ids(inst))
    if not active_project_ids:
        return False, f"仪器 {inst.code} 当前已在库，无需落库"

    checkin_note = (note or "").strip() or "台账手动落库"
    for project in LibraryProject.objects.filter(pk__in=active_project_ids):
        InstrumentCheckoutLog.objects.create(
            instrument=inst,
            project=project,
            event_type=InstrumentCheckoutLog.EVENT_CHECKIN,
            performed_by=user,
            note=checkin_note,
        )
        raw = getattr(project, "assigned_instrument_ids", None) or {}
        stripped = _strip_instrument_from_assigned_raw(raw, pk)
        if stripped != raw:
            project.assigned_instrument_ids = stripped
            project.save(update_fields=["assigned_instrument_ids", "updated_at"])

    _sync_checkout_projects(inst, set(), user=user, touch_time=False)
    n = len(active_project_ids)
    if n == 1:
        return True, f"仪器 {inst.code} 已落库（已解除 1 个项目关联）"
    return True, f"仪器 {inst.code} 已落库（已解除 {n} 个项目关联）"


def eligible_scopes_for_instrument_on_detection_item(
    instrument: InstrumentCatalog,
    item: dict,
) -> list[str]:
    """判断仪器可用于该检测项的质控/防护范围（与任务模板种类一致）。"""
    if not item.get("hasTemplateKinds"):
        return [SCOPE_QC, SCOPE_RP]
    spec_key = instrument_kind_key(instrument.name, instrument.model or "")
    scopes: list[str] = []
    for scope in (SCOPE_QC, SCOPE_RP):
        for kind_row in item.get("scopedKinds", {}).get(scope) or []:
            k = instrument_kind_key(kind_row["name"], kind_row.get("model") or "")
            if k == spec_key:
                scopes.append(scope)
                break
    return scopes


def _current_by_detection_item_for_project(
    project: LibraryProject,
) -> dict[str, dict[str, list[int]]]:
    """读取项目当前按检测项的仪器分配（含从旧版按种类展开的结果）。"""
    catalog = build_manual_instrument_picker_catalog(project)
    items = catalog.get("items") or []
    by_item: dict[str, dict[str, list[int]]] = {}
    for item in items:
        binding = item.get("selectedBinding") or {SCOPE_QC: [], SCOPE_RP: []}
        by_item[item["key"]] = {
            SCOPE_QC: _dedupe_id_list(binding.get(SCOPE_QC) or []),
            SCOPE_RP: _dedupe_id_list(binding.get(SCOPE_RP) or []),
        }
    if by_item:
        return by_item
    raw = getattr(project, "assigned_instrument_ids", None) or {}
    return normalize_project_by_detection_item(raw)


def build_ledger_instrument_checkout_catalog(
    project: LibraryProject,
    instrument: InstrumentCatalog,
) -> dict:
    """
    台账出库弹窗：按委托项目列出可绑定的「受检设备 × 现场记录」检测项。
    选项与医院信息挂载的任务模板、项目工作台仪器管理一致。
    """
    detection_items = detection_items_for_project_instruments(project)
    equipments_map: dict[int, dict] = {}
    for item in detection_items:
        eligible = eligible_scopes_for_instrument_on_detection_item(instrument, item)
        if item.get("hasTemplateKinds") and not eligible:
            continue
        link_id = int(item["linkId"])
        if link_id not in equipments_map:
            equipments_map[link_id] = {
                "linkId": link_id,
                "equipmentTitle": item["equipmentTitle"],
                "reportTaskLabel": item.get("reportTaskLabel") or "",
                "detections": [],
            }
        equipments_map[link_id]["detections"].append(
            {
                "key": item["key"],
                "siteTaskLabel": item["siteTaskLabel"],
                "taskNo": item.get("taskNo") or "",
                "taskCode": item.get("taskCode") or "",
                "taskName": item.get("taskName") or "",
                "detectionLabel": item["detectionLabel"],
                "eligibleScopes": eligible,
                "scopeOptions": [
                    {
                        "value": scope,
                        "label": "质控（性能）"
                        if scope == SCOPE_QC
                        else "工作场所放射防护",
                    }
                    for scope in eligible
                ],
            }
        )
    equipments = list(equipments_map.values())
    for eq in equipments:
        eq["detections"].sort(
            key=lambda d: (d.get("taskNo") or "", d.get("siteTaskLabel") or "")
        )
    equipments.sort(key=lambda e: e.get("equipmentTitle") or "")
    empty_hint = ""
    if not detection_items:
        empty_hint = (
            "该项目尚未挂载委托设备与现场记录任务，请先在项目工作台完成委托立项。"
        )
    elif not equipments:
        empty_hint = (
            "该仪器与当前项目各检测项要求的种类均不匹配，请核对任务模板绑定或更换仪器。"
        )
    return {
        "projectId": project.pk,
        "projectCode": project.code or "",
        "projectName": project.name or "",
        "instrument": {
            "id": int(instrument.pk),
            "code": instrument.code or "",
            "name": instrument.name or "",
            "model": instrument.model or "",
        },
        "equipments": equipments,
        "hasOptions": bool(equipments),
        "emptyHint": empty_hint,
    }


@transaction.atomic
def manual_checkout_instrument_to_detection_item(
    instrument_id: int,
    project_id: int,
    detection_item_key: str,
    scope: str,
    *,
    user: User | None = None,
) -> tuple[bool, str]:
    """
    台账手动出库：将仪器登记至指定项目的某一检测项（委托设备×现场记录），
    并合并写入 assigned_instrument_ids 后同步出库状态。
    """
    scope = str(scope or "").strip()
    if scope not in (SCOPE_QC, SCOPE_RP):
        return False, "请选择质控或防护范围"

    try:
        inst_pk = int(instrument_id)
        proj_pk = int(project_id)
    except (TypeError, ValueError):
        return False, "参数无效"

    inst = (
        InstrumentCatalog.objects.select_for_update()
        .filter(pk=inst_pk, is_active=True)
        .first()
    )
    if inst is None:
        return False, "仪器不存在或已停用"

    project = (
        LibraryProject.objects.select_for_update()
        .filter(pk=proj_pk, is_active=True)
        .first()
    )
    if project is None:
        return False, "委托项目不存在或已停用"

    key = str(detection_item_key or "").strip()
    valid_items = {
        item["key"]: item for item in detection_items_for_project_instruments(project)
    }
    if key not in valid_items:
        return False, "所选检测项不属于该项目或已失效，请重新选择"

    item_meta = valid_items[key]
    eligible = eligible_scopes_for_instrument_on_detection_item(inst, item_meta)
    if scope not in eligible:
        return False, "该仪器不能用于所选检测项的该范围"

    by_item = _current_by_detection_item_for_project(project)
    binding = by_item.get(key) or {SCOPE_QC: [], SCOPE_RP: []}
    scope_ids = list(binding.get(scope) or [])
    if inst_pk not in scope_ids:
        scope_ids.append(inst_pk)
    binding = {
        SCOPE_QC: _dedupe_id_list(binding.get(SCOPE_QC) or []),
        SCOPE_RP: _dedupe_id_list(binding.get(SCOPE_RP) or []),
    }
    binding[scope] = _dedupe_id_list(scope_ids)
    by_item[key] = binding

    result = apply_manual_instrument_assignment(
        project,
        {},
        by_detection_item=by_item,
        user=user,
        checkout=True,
    )
    if not result.ok:
        return False, result.message or "出库失败"
    return True, result.message or f"仪器 {inst.code} 已出库至项目 {project.code}"
