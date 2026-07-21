"""医院信息管理：委托单位扩展字段、联系人、设备与文件库实际报告绑定。"""
from __future__ import annotations

from datetime import datetime

from django.contrib.auth.models import User
from django.db.models import Prefetch, QuerySet
from django.utils import timezone

from apps.core.commission_org_service import commission_org_subtree_ids
from apps.core.library_access import library_file_access_allowed
from apps.core.models import (
    CommissionOrgContact,
    CommissionOrgEquipment,
    CommissionOrgEquipmentHistory,
    CommissionOrganization,
    InspectionCase,
    LibraryFile,
    LibraryFileProject,
    LibraryProject,
    LibraryTask,
)


def _hospital_for_org(org: CommissionOrganization) -> CommissionOrganization:
    return org.hospital_root()


def _project_ids_under_org(org: CommissionOrganization) -> list[int]:
    hospital = _hospital_for_org(org)
    return list(
        LibraryProject.objects.filter(commission_org_id=hospital.pk, is_active=True).values_list(
            "pk", flat=True
        )
    )


def departments_under_org(org: CommissionOrganization) -> QuerySet[CommissionOrganization]:
    """当前机构下级全部科室（用于医院/院区新建设备时选择归属）。"""
    org_ids = commission_org_subtree_ids(org.pk)
    return (
        CommissionOrganization.objects.filter(
            pk__in=org_ids,
            level=CommissionOrganization.LEVEL_DEPARTMENT,
            is_active=True,
        )
        .order_by("name", "id")
    )


_DIRECT_CAMPUS_KEY = "__direct__"


def department_campus_picker_key(org: CommissionOrganization) -> str | None:
    """挂载节点在「院区 → 科室」下拉里的院区键；医院直挂为空，院区直挂为自身 id。"""
    if org is None:
        return None
    if org.level == CommissionOrganization.LEVEL_HOSPITAL:
        return ""
    if org.level == CommissionOrganization.LEVEL_CAMPUS:
        return str(org.pk)
    parent = org.parent
    if parent is None:
        return None
    if parent.level == CommissionOrganization.LEVEL_CAMPUS:
        return str(parent.pk)
    if parent.level == CommissionOrganization.LEVEL_HOSPITAL:
        return _DIRECT_CAMPUS_KEY
    return None


def build_equipment_department_picker(org: CommissionOrganization) -> dict:
    """
    添加设备时的挂载位置选择（科室可选）：
    - 科室：无需选择（挂在本级）
    - 院区：可选下级科室；不选则挂在本院区
    - 医院：可选院区、科室；都不选则挂在本院
    """
    if org.level == CommissionOrganization.LEVEL_DEPARTMENT:
        return {"mode": "none", "allow_empty": False}

    if org.level == CommissionOrganization.LEVEL_CAMPUS:
        depts = list(
            CommissionOrganization.objects.filter(
                parent_id=org.pk,
                level=CommissionOrganization.LEVEL_DEPARTMENT,
                is_active=True,
            ).order_by("name", "id")
        )
        return {
            "mode": "department_only",
            "allow_empty": True,
            "empty_label": "— 不选科室（挂在本院区）—",
            "departments": [{"id": d.pk, "label": d.name} for d in depts],
        }

    if org.level == CommissionOrganization.LEVEL_HOSPITAL:
        campuses = list(
            CommissionOrganization.objects.filter(
                parent_id=org.pk,
                level=CommissionOrganization.LEVEL_CAMPUS,
                is_active=True,
            ).order_by("name", "id")
        )
        direct_depts = list(
            CommissionOrganization.objects.filter(
                parent_id=org.pk,
                level=CommissionOrganization.LEVEL_DEPARTMENT,
                is_active=True,
            ).order_by("name", "id")
        )
        if not campuses:
            all_depts = list(departments_under_org(org))
            return {
                "mode": "department_only",
                "allow_empty": True,
                "empty_label": "— 不选科室（挂在本医院）—",
                "departments": [{"id": d.pk, "label": d.full_display_name} for d in all_depts],
            }

        campus_options: list[dict] = []
        departments_by_campus: dict[str, list[dict]] = {}
        for c in campuses:
            depts = list(
                CommissionOrganization.objects.filter(
                    parent_id=c.pk,
                    level=CommissionOrganization.LEVEL_DEPARTMENT,
                    is_active=True,
                ).order_by("name", "id")
            )
            key = str(c.pk)
            campus_options.append({"id": key, "label": c.name})
            departments_by_campus[key] = [{"id": d.pk, "label": d.name} for d in depts]
        if direct_depts:
            campus_options.append({"id": _DIRECT_CAMPUS_KEY, "label": "医院直属科室"})
            departments_by_campus[_DIRECT_CAMPUS_KEY] = [
                {"id": d.pk, "label": d.name} for d in direct_depts
            ]
        return {
            "mode": "campus_then_department",
            "allow_empty": True,
            "empty_campus_label": "— 不选院区（挂在本医院）—",
            "empty_department_label": "— 不选科室 —",
            "campuses": campus_options,
            "departments_by_campus": departments_by_campus,
        }

    return {"mode": "none", "allow_empty": False}


def equipments_for_org_view(org: CommissionOrganization) -> QuerySet[CommissionOrgEquipment]:
    """当前机构及其下级节点下的全部设备（含直接挂在医院/院区的设备）。"""
    org_ids = commission_org_subtree_ids(org.pk)
    return (
        CommissionOrgEquipment.objects.filter(
            is_active=True,
            department_id__in=org_ids,
            department__is_active=True,
        )
        .select_related("department", "report_file", "report_task")
        .order_by("department_id", "device_type", "instance_no", "sort_order", "id")
    )


def equipment_instance_label(instance_no: int) -> str:
    return f"设备{max(1, int(instance_no or 1))}"


def equipment_default_display_name(device_type: str, instance_no: int) -> str:
    dt = (device_type or "").strip()
    if not dt:
        return equipment_instance_label(instance_no)
    return f"{dt} · {equipment_instance_label(instance_no)}"


def next_equipment_instance_no(
    department_id: int,
    device_type: str,
    *,
    exclude_id: int | None = None,
) -> int:
    from django.db.models import Max

    dt = (device_type or "").strip()
    if not dt:
        return 1
    qs = CommissionOrgEquipment.objects.filter(
        department_id=department_id,
        device_type=dt,
        is_active=True,
    )
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
    current = qs.aggregate(m=Max("instance_no"))["m"] or 0
    return int(current) + 1


def renumber_equipment_instances_for_type(
    department_id: int,
    device_type: str,
) -> None:
    """删除或改类型后，将同科室同类型台次重排为 1..n。"""
    dt = (device_type or "").strip()
    if not dt:
        return
    rows = list(
        CommissionOrgEquipment.objects.filter(
            department_id=department_id,
            device_type=dt,
            is_active=True,
        ).order_by("instance_no", "sort_order", "id")
    )
    for idx, row in enumerate(rows, start=1):
        updates: list[str] = []
        if row.instance_no != idx:
            row.instance_no = idx
            updates.append("instance_no")
        default_name = equipment_default_display_name(dt, idx)
        if (row.name or "").strip() in ("", dt, default_name) or " · 设备" in (row.name or ""):
            if row.name != default_name:
                row.name = default_name
                updates.append("name")
        if updates:
            updates.append("updated_at")
            row.save(update_fields=updates)


def build_equipment_card_dict(eq: CommissionOrgEquipment) -> dict:
    return {
        "equipment": eq,
        "instance_label": eq.instance_label,
        "status": equipment_display_status(eq),
        "can_bind_project": equipment_can_bind_to_project(eq),
        "report_binding_labels": equipment_report_binding_labels(eq),
    }


def build_equipment_groups_for_org_view(
    equipments: list[CommissionOrgEquipment],
    explorer_org: CommissionOrganization | None,
) -> list[dict]:
    """
    同设备类型合并展示：科室视图按 (科室, 类型) 分组；医院/院区视图按设备类型跨科室合并。
    """
    merge_across_departments = bool(
        explorer_org
        and explorer_org.level != CommissionOrganization.LEVEL_DEPARTMENT
    )
    buckets: dict[tuple, list[CommissionOrgEquipment]] = {}
    for eq in equipments:
        dt = (eq.device_type or "").strip() or "未分类"
        key = (dt,) if merge_across_departments else (eq.department_id, dt)
        buckets.setdefault(key, []).append(eq)
    groups: list[dict] = []
    for key, rows in buckets.items():
        rows.sort(key=lambda e: (e.department_id, e.instance_no, e.id))
        first = rows[0]
        dt = (first.device_type or "").strip() or "未分类"
        dept_labels = sorted(
            {e.department.full_display_name for e in rows if e.department_id}
        )
        groups.append(
            {
                "device_type": dt,
                "count": len(rows),
                "department_labels": dept_labels,
                "department_label": dept_labels[0] if len(dept_labels) == 1 else "",
                "merge_across_departments": merge_across_departments,
                "report_binding_labels": equipment_report_binding_labels(first),
                "instances": [build_equipment_card_dict(e) for e in rows],
            }
        )
    groups.sort(key=lambda g: (g["device_type"], g.get("department_label") or ""))
    return groups


def library_tasks_for_equipment_binding() -> QuerySet[LibraryTask]:
    """医院设备可选绑定的任务模板：仅报告层级（现场记录由报告 report_source_tasks 带入项目）。"""
    return LibraryTask.objects.filter(output_target=LibraryTask.OUTPUT_REPORT).order_by("code")


def normalize_equipment_report_task(task: LibraryTask | None) -> LibraryTask | None:
    """设备/项目绑定只认报告任务；若误指现场记录则尝试解析其所属报告。"""
    if task is None:
        return None
    if task.output_target == LibraryTask.OUTPUT_REPORT:
        return task
    if task.output_target == LibraryTask.OUTPUT_SITE_RECORD:
        return (
            task.report_target_tasks.filter(output_target=LibraryTask.OUTPUT_REPORT)
            .order_by("code", "id")
            .first()
        )
    return None


def projects_for_org_binding(org: CommissionOrganization) -> QuerySet[LibraryProject]:
    hospital = _hospital_for_org(org)
    return LibraryProject.objects.filter(commission_org_id=hospital.pk, is_active=True).order_by(
        "-updated_at", "-id"
    )


def parse_report_task_id(raw: str) -> int | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        tid = int(raw)
    except ValueError:
        return None
    if library_tasks_for_equipment_binding().filter(pk=tid).exists():
        return tid
    return None


def normalize_equipment_report_task_bindings(raw) -> list[dict]:
    """校验并去重设备按检测类型的报告模板绑定。"""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen_types: set[str] = set()
    for row in raw:
        if not isinstance(row, dict):
            continue
        itype = str(row.get("inspection_type") or "").strip()
        if not itype or itype in seen_types:
            continue
        try:
            tid = int(row.get("report_task_id") or 0)
        except (TypeError, ValueError):
            continue
        if not tid or parse_report_task_id(str(tid)) is None:
            continue
        seen_types.add(itype)
        out.append({"inspection_type": itype, "report_task_id": tid})
    return out


def equipment_report_bindings_for_api(eq: CommissionOrgEquipment) -> list[dict]:
    """设备编辑/API：按检测类型返回绑定及模板摘要。"""
    bindings = normalize_equipment_report_task_bindings(
        eq.report_task_bindings if isinstance(eq.report_task_bindings, list) else []
    )
    if not bindings and eq.report_task_id:
        from apps.core.equipment_device_type_service import infer_inspection_type_for_report_task

        task = LibraryTask.objects.filter(
            pk=eq.report_task_id, output_target=LibraryTask.OUTPUT_REPORT
        ).first()
        itype = infer_inspection_type_for_report_task(task) or "默认"
        bindings = [{"inspection_type": itype, "report_task_id": eq.report_task_id}]
    task_ids = {b["report_task_id"] for b in bindings}
    tasks = {
        t.pk: t
        for t in LibraryTask.objects.filter(
            pk__in=task_ids, output_target=LibraryTask.OUTPUT_REPORT
        )
    }
    rows: list[dict] = []
    for b in bindings:
        t = tasks.get(b["report_task_id"])
        rows.append(
            {
                "inspection_type": b["inspection_type"],
                "report_task_id": b["report_task_id"],
                "task_code": (t.code if t else "") or "",
                "task_name": (t.name if t else "") or "",
                "task_label": f"{t.code} · {t.name}" if t else f"模板 #{b['report_task_id']}",
            }
        )
    return rows


def equipment_project_bind_type_options(eq: CommissionOrgEquipment) -> list[dict]:
    """工作台加入委托时可选的检测类型（来自设备主数据绑定）。"""
    rows = equipment_report_bindings_for_api(eq)
    if rows:
        return [
            {
                "inspection_type": r["inspection_type"],
                "report_task_id": r["report_task_id"],
                "task_label": r["task_label"],
            }
            for r in rows
        ]
    task = normalize_equipment_report_task(eq.report_task) if eq.report_task_id else None
    if task is not None:
        from apps.core.equipment_device_type_service import infer_inspection_type_for_report_task

        itype = infer_inspection_type_for_report_task(task) or "默认"
        return [
            {
                "inspection_type": itype,
                "report_task_id": task.pk,
                "task_label": f"{task.code} · {task.name}",
            }
        ]
    return []


def equipment_report_binding_labels(eq: CommissionOrgEquipment) -> list[str]:
    return [
        f"{r['inspection_type']}：{r['task_label']}"
        for r in equipment_report_bindings_for_api(eq)
    ]


def equipment_has_report_template_config(eq: CommissionOrgEquipment) -> bool:
    if normalize_equipment_report_task_bindings(
        eq.report_task_bindings if isinstance(eq.report_task_bindings, list) else []
    ):
        return True
    if eq.report_task_id:
        return True
    return resolve_equipment_report_task(eq) is not None


def all_tasks_for_equipment_project_bind(
    eq: CommissionOrgEquipment,
) -> list[LibraryTask]:
    """加入项目时合并各检测类型绑定的报告及现场记录任务。"""
    bindings = normalize_equipment_report_task_bindings(
        eq.report_task_bindings if isinstance(eq.report_task_bindings, list) else []
    )
    if bindings:
        tasks: list[LibraryTask] = []
        seen: set[int] = set()
        for row in bindings:
            for t in tasks_for_equipment_project_bind(
                eq, inspection_type=row["inspection_type"]
            ):
                if t.pk not in seen:
                    tasks.append(t)
                    seen.add(t.pk)
        if tasks:
            return tasks
    return tasks_for_equipment_project_bind(eq)


def inspection_type_suggestions_from_picker_catalog(catalog: dict) -> list[str]:
    names: list[str] = []
    for f in catalog.get("folders") or []:
        if isinstance(f, dict):
            name = str(f.get("name") or "").strip()
            if name:
                names.append(name)
    return names


def inspection_meta_from_report_file(lf: LibraryFile) -> tuple[str, datetime | None, dict | None]:
    """从报告成品解析委托编号、检测时间与提交 payload。"""
    payload = resolve_submit_payload_for_report_library_file(lf)
    inspected_at = lf.created_at
    commission_no = ""
    case: InspectionCase | None = None
    if lf.link_entity == LibraryFile.LINK_ENTITY_INSPECTION_CASE and lf.link_object_id:
        case = InspectionCase.objects.filter(pk=int(lf.link_object_id)).first()
        if case is not None:
            commission_no = (case.case_no or "").strip()
    if not commission_no and isinstance(payload, dict):
        commission_no = str(payload.get("taskNo") or "").strip()
        if not commission_no:
            ri = payload.get("reportInfo")
            if isinstance(ri, dict):
                commission_no = str(ri.get("reportNo") or "").strip()
    if isinstance(payload, dict):
        updated_raw = payload.get("updatedAt") or payload.get("updated_at")
        if updated_raw:
            try:
                inspected_at = datetime.fromisoformat(str(updated_raw).replace("Z", "+00:00"))
                if timezone.is_naive(inspected_at):
                    inspected_at = timezone.make_aware(inspected_at)
            except (TypeError, ValueError):
                pass
    return commission_no, inspected_at, payload


def record_equipment_inspection_from_report(
    equipment: CommissionOrgEquipment,
    lf: LibraryFile,
    *,
    user: User | None = None,
) -> CommissionOrgEquipmentHistory:
    """报告编制完成后：更新设备最近检测信息并写入历史。"""
    commission_no, inspected_at, payload = inspection_meta_from_report_file(lf)
    project = lf.projects.order_by("id").first()
    case: InspectionCase | None = None
    if lf.link_entity == LibraryFile.LINK_ENTITY_INSPECTION_CASE and lf.link_object_id:
        case = InspectionCase.objects.filter(pk=int(lf.link_object_id)).first()
        if case is not None and case.library_project_id:
            project = case.library_project

    equipment_info: dict = {}
    test_result: dict = {}
    if isinstance(payload, dict):
        ei = payload.get("equipmentInfo")
        if isinstance(ei, dict):
            equipment_info = ei
        tr = payload.get("testResult")
        if isinstance(tr, dict):
            test_result = tr
        fields = equipment_fields_from_submit_payload(payload)
        if fields.get("name"):
            equipment.name = fields["name"]
        if fields.get("model"):
            equipment.model = fields["model"]
        if fields.get("serial_no"):
            equipment.serial_no = fields["serial_no"]
        if fields.get("manufacturer"):
            equipment.manufacturer = fields["manufacturer"]
        if fields.get("location"):
            equipment.location = fields["location"]

    equipment.report_file = lf
    equipment.last_commission_no = commission_no
    equipment.last_inspected_at = inspected_at or timezone.now()
    update_fields = [
        "name",
        "model",
        "serial_no",
        "manufacturer",
        "location",
        "report_file",
        "last_commission_no",
        "last_inspected_at",
        "updated_at",
    ]
    if not equipment.report_task_id:
        inferred = resolve_equipment_report_task(equipment)
        if inferred is not None:
            equipment.report_task = inferred
            update_fields.append("report_task_id")
    equipment.save(update_fields=update_fields)

    hist, _created = CommissionOrgEquipmentHistory.objects.get_or_create(
        equipment=equipment,
        report_file=lf,
        defaults={
            "library_project": project,
            "inspection_case": case,
            "commission_no": commission_no,
            "inspected_at": equipment.last_inspected_at,
            "equipment_info": equipment_info,
            "test_result": test_result,
        },
    )
    if not _created:
        hist.library_project = project
        hist.inspection_case = case
        hist.commission_no = commission_no
        hist.inspected_at = equipment.last_inspected_at
        hist.equipment_info = equipment_info
        hist.test_result = test_result
        hist.save(
            update_fields=[
                "library_project",
                "inspection_case",
                "commission_no",
                "inspected_at",
                "equipment_info",
                "test_result",
            ]
        )
    return hist


def backfill_equipment_histories_from_reports(org: CommissionOrganization) -> None:
    """
    为已绑定报告但尚无历史行的设备补建检测历史（一次性补数据）。
    已有 (equipment, report_file) 历史则跳过，避免每次浏览/跳转整页 GET 重复解析报告与写库。
    """
    qs = equipments_for_org_view(org).filter(report_file_id__isnull=False).select_related(
        "report_file"
    )
    rows = list(qs)
    if not rows:
        return
    eq_ids = [eq.pk for eq in rows]
    existing_pairs = set(
        CommissionOrgEquipmentHistory.objects.filter(equipment_id__in=eq_ids).values_list(
            "equipment_id", "report_file_id"
        )
    )
    for eq in rows:
        lf = eq.report_file
        if lf is None or (eq.pk, lf.pk) in existing_pairs:
            continue
        record_equipment_inspection_from_report(eq, lf)


def equipment_display_status(eq: CommissionOrgEquipment) -> dict:
    """设备检测状态展示信息。"""
    if eq.has_completed_inspection:
        dt = eq.last_inspected_at
        dt_label = timezone.localtime(dt).strftime("%Y-%m-%d %H:%M") if dt else "—"
        return {
            "key": "completed",
            "label": "已检测",
            "hint": f"上次检测 {dt_label} · 委托编号 {eq.last_commission_no or '—'}",
            "badge_class": "bg-emerald-50 text-emerald-800 ring-emerald-200/80",
        }
    labels = equipment_report_binding_labels(eq)
    if labels:
        hint = "已绑定报告模板：" + "；".join(labels)
    else:
        hint = (
            "请选择设备类型保存后将自动绑定验收/状态检测模板；"
            "也可在编辑中手动调整"
        )
    return {
        "key": "pending",
        "label": "未检测",
        "hint": hint,
        "badge_class": "bg-amber-50 text-amber-900 ring-amber-200/80",
    }


def equipment_history_rows(eq: CommissionOrgEquipment, user: User) -> list[dict]:
    rows: list[dict] = []
    for h in eq.inspection_histories.select_related("report_file", "library_project").order_by(
        "-inspected_at", "-id"
    )[:50]:
        lf = h.report_file
        preview_url = ""
        if lf is not None and library_file_access_allowed(user, lf):
            from django.urls import reverse

            preview_url = reverse("file_preview", args=[lf.pk])
        dt = h.inspected_at
        rows.append(
            {
                "id": h.pk,
                "commission_no": h.commission_no or "—",
                "inspected_at": timezone.localtime(dt).strftime("%Y-%m-%d %H:%M") if dt else "—",
                "project_label": (
                    f"{h.library_project.code} · {h.library_project.name}"
                    if h.library_project_id
                    else "—"
                ),
                "report_name": (lf.original_name if lf else "—") or "—",
                "preview_url": preview_url,
            }
        )
    return rows


def equipment_previous_submit_payload(eq: CommissionOrgEquipment) -> dict | None:
    """供新一次检测前端预填/对比用的最近一次提交快照。"""
    hist = (
        eq.inspection_histories.order_by("-inspected_at", "-id")
        .exclude(equipment_info={})
        .first()
    )
    if hist is not None and hist.equipment_info:
        return {
            "commissionNo": hist.commission_no,
            "inspectedAt": hist.inspected_at.isoformat() if hist.inspected_at else None,
            "equipmentInfo": hist.equipment_info,
            "testResult": hist.test_result or {},
        }
    if eq.report_file_id:
        payload = resolve_submit_payload_for_report_library_file(eq.report_file)
        if isinstance(payload, dict):
            return {
                "commissionNo": eq.last_commission_no,
                "inspectedAt": eq.last_inspected_at.isoformat() if eq.last_inspected_at else None,
                "equipmentInfo": payload.get("equipmentInfo") or {},
                "testResult": payload.get("testResult") or {},
            }
    return None


def resolve_equipment_report_task(
    eq: CommissionOrgEquipment,
    *,
    inspection_type: str | None = None,
) -> LibraryTask | None:
    """解析设备可用于「加入项目」的报告任务模板（可按检测类型选择）。"""
    key = (inspection_type or "").strip()
    bindings = eq.report_task_bindings if isinstance(eq.report_task_bindings, list) else []
    if key:
        for row in bindings:
            if not isinstance(row, dict):
                continue
            if str(row.get("inspection_type") or "").strip() != key:
                continue
            try:
                tid = int(row.get("report_task_id") or 0)
            except (TypeError, ValueError):
                tid = 0
            if tid:
                t = LibraryTask.objects.filter(
                    pk=tid, output_target=LibraryTask.OUTPUT_REPORT
                ).first()
                if t is not None:
                    return t
    if eq.report_task_id:
        return normalize_equipment_report_task(eq.report_task)
    lf = eq.report_file
    if lf is None:
        return None
    link = (
        lf.task_links.select_related("library_task")
        .filter(library_task__output_target=LibraryTask.OUTPUT_REPORT)
        .order_by("id")
        .first()
    )
    if link is not None and link.library_task_id:
        return link.library_task
    project = lf.projects.order_by("id").first()
    case: InspectionCase | None = None
    if lf.link_entity == LibraryFile.LINK_ENTITY_INSPECTION_CASE and lf.link_object_id:
        case = (
            InspectionCase.objects.filter(pk=int(lf.link_object_id))
            .select_related("library_project")
            .first()
        )
        if case is not None and case.library_project_id:
            project = case.library_project
    if project is not None:
        from apps.api.inspection_pdf_service import _resolve_library_task_for_task_no

        payload = resolve_submit_payload_for_report_library_file(lf)
        if isinstance(payload, dict):
            tn = str(payload.get("taskNo") or "").strip()
            if tn:
                t = normalize_equipment_report_task(
                    _resolve_library_task_for_task_no(tn, project)
                )
                if t is not None:
                    return t
        if case is not None:
            t = normalize_equipment_report_task(
                _resolve_library_task_for_task_no(case.case_no, project)
            )
            if t is not None:
                return t
    return None


def equipment_can_bind_to_project(eq: CommissionOrgEquipment) -> bool:
    return len(all_tasks_for_equipment_project_bind(eq)) > 0


def tasks_for_equipment_project_bind(
    eq: CommissionOrgEquipment,
    *,
    inspection_type: str | None = None,
) -> list[LibraryTask]:
    """加入项目时需要关联的任务（含报告来源现场记录）。"""
    root = resolve_equipment_report_task(eq, inspection_type=inspection_type)
    if root is None or root.output_target != LibraryTask.OUTPUT_REPORT:
        return []
    tasks: list[LibraryTask] = [root]
    seen = {root.pk}
    for src in root.report_source_tasks.filter(
        output_target=LibraryTask.OUTPUT_SITE_RECORD
    ).order_by("code", "id"):
        if src.pk not in seen:
            tasks.append(src)
            seen.add(src.pk)
    return tasks


def collect_library_tasks_for_project_equipments(project: LibraryProject) -> list[LibraryTask]:
    """当前项目委托设备对应的报告 + 现场记录任务链（去重、按 code 排序）。"""
    from apps.core.models import LibraryProjectEquipment

    tasks: list[LibraryTask] = []
    seen: set[int] = set()
    for link in LibraryProjectEquipment.objects.filter(project=project).select_related(
        "equipment", "report_task"
    ):
        eq = link.equipment
        itype = (link.inspection_type or "").strip()
        if link.report_task_id:
            root = normalize_equipment_report_task(link.report_task)
        else:
            root = resolve_equipment_report_task(eq, inspection_type=itype or None)
        if root is None:
            continue
        chain = [root]
        chain_seen = {root.pk}
        for src in root.report_source_tasks.filter(
            output_target=LibraryTask.OUTPUT_SITE_RECORD
        ).order_by("code", "id"):
            if src.pk not in chain_seen:
                chain.append(src)
                chain_seen.add(src.pk)
        for t in chain:
            if t.pk not in seen:
                seen.add(t.pk)
                tasks.append(t)
    tasks.sort(key=lambda t: (t.code or "", t.pk))
    return tasks


def sync_project_library_tasks_from_equipments(
    project: LibraryProject,
    user: User | None = None,
) -> int:
    """
    项目已挂载委托设备时，将 library_tasks 设为各设备任务链的并集，去掉历史上
    绑定其它设备或手工勾选后残留的模板。
    """
    from apps.core.library_file_service import attach_files_to_projects
    from apps.core.models import LibraryProjectEquipment

    if not LibraryProjectEquipment.objects.filter(project=project).exists():
        return 0
    task_list = collect_library_tasks_for_project_equipments(project)
    if not task_list:
        return 0
    project.library_tasks.set(task_list)
    new_ids = {t.pk for t in task_list}
    from apps.core.models import LibraryTaskAssignment

    LibraryTaskAssignment.objects.filter(project=project).exclude(
        library_task_id__in=new_ids
    ).delete()
    if user is not None:
        all_file_ids: set[int] = set()
        for t in task_list:
            all_file_ids.update(
                t.library_files.filter(category=LibraryFile.CATEGORY_TEMPLATE).values_list(
                    "pk", flat=True
                )
            )
        if all_file_ids:
            attach_files_to_projects(sorted(all_file_ids), [project.pk], user)
    return len(task_list)


def bind_equipment_tasks_to_project(
    eq: CommissionOrgEquipment,
    project: LibraryProject,
    user: User,
    *,
    inspection_type: str | None = None,
) -> tuple[int, str | None]:
    from apps.core.library_file_service import attach_files_to_projects

    bindings = normalize_equipment_report_task_bindings(
        eq.report_task_bindings if isinstance(eq.report_task_bindings, list) else []
    )
    if bindings and not eq.report_task_id:
        eq.report_task_id = bindings[0]["report_task_id"]
        eq.save(update_fields=["report_task_id", "updated_at"])
    elif not bindings:
        root = resolve_equipment_report_task(eq)
        if root is not None and not eq.report_task_id:
            eq.report_task_id = root.pk
            eq.save(update_fields=["report_task_id", "updated_at"])
    key = (inspection_type or "").strip()
    if key:
        tasks = tasks_for_equipment_project_bind(eq, inspection_type=key)
    else:
        tasks = all_tasks_for_equipment_project_bind(eq)
    if not tasks:
        if key:
            return 0, f"该设备未配置「{key}」对应的报告任务模板，请先在医院信息中绑定"
        return 0, "该设备未绑定检测任务模板，请先在设备信息中选择，或确保已关联检测报告"
    eq_hospital = _hospital_for_org(eq.department)
    if not project.commission_org_id:
        return 0, "项目未绑定委托单位"
    proj_hospital = _hospital_for_org(project.commission_org)
    if eq_hospital.pk != proj_hospital.pk:
        return 0, "所选项目不属于该设备所在医院"
    added = 0
    for t in tasks:
        if not project.library_tasks.filter(pk=t.pk).exists():
            project.library_tasks.add(t)
            added += 1
            file_ids = list(
                t.library_files.filter(category=LibraryFile.CATEGORY_TEMPLATE).values_list(
                    "id", flat=True
                )
            )
            if file_ids:
                attach_files_to_projects(file_ids, [project.pk], user)
    return added, None


def resolve_department_for_equipment_save(
    explorer_org: CommissionOrganization,
    department_id_raw: str,
    campus_id_raw: str = "",
) -> tuple[CommissionOrganization | None, str | None]:
    """解析设备挂载节点。

    - 当前在科室：挂本级
    - 指定了科室/院区/医院 id：须在当前浏览节点子树内
    - 仅选了院区、未选科室：挂该院区
    - 都未选：挂当前浏览的医院/院区
    """
    if explorer_org.level == CommissionOrganization.LEVEL_DEPARTMENT:
        return explorer_org, None

    subtree_ids = set(commission_org_subtree_ids(explorer_org.pk))
    allowed_levels = {
        CommissionOrganization.LEVEL_HOSPITAL,
        CommissionOrganization.LEVEL_CAMPUS,
        CommissionOrganization.LEVEL_DEPARTMENT,
    }

    raw = (department_id_raw or "").strip()
    if raw:
        try:
            did = int(raw)
        except ValueError:
            return None, "挂载机构无效"
        if did not in subtree_ids:
            return None, "所选机构不在当前单位下级范围内"
        target = CommissionOrganization.objects.filter(
            pk=did, is_active=True, level__in=allowed_levels
        ).first()
        if target is None:
            return None, "挂载机构不存在"
        return target, None

    campus_raw = (campus_id_raw or "").strip()
    if campus_raw and campus_raw != _DIRECT_CAMPUS_KEY:
        try:
            cid = int(campus_raw)
        except ValueError:
            return None, "院区无效"
        if cid not in subtree_ids:
            return None, "所选院区不在当前单位下级范围内"
        campus = CommissionOrganization.objects.filter(
            pk=cid,
            is_active=True,
            level=CommissionOrganization.LEVEL_CAMPUS,
        ).first()
        if campus is None:
            return None, "院区不存在"
        return campus, None

    return explorer_org, None


def report_files_for_org_binding(org: CommissionOrganization) -> QuerySet[LibraryFile]:
    """该委托单位（含下级）关联项目下的文件库「报告」成品。"""
    project_ids = _project_ids_under_org(org)
    if not project_ids:
        return LibraryFile.objects.none()
    return (
        LibraryFile.objects.filter(
            category=LibraryFile.CATEGORY_REPORT,
            deleted_at__isnull=True,
            project_links__project_id__in=project_ids,
        )
        .distinct()
        .prefetch_related(
            Prefetch(
                "project_links",
                queryset=LibraryFileProject.objects.select_related("project"),
            )
        )
        .order_by("-created_at", "-id")
    )


def report_file_option_label(lf: LibraryFile) -> str:
    """下拉选项展示：项目 · 报告说明。"""
    proj = None
    for link in lf.project_links.all()[:1]:
        proj = link.project
        break
    on = (lf.original_name or "").strip()
    if proj:
        code = (proj.code or "").strip()
        pname = (proj.name or "").strip()
        head = " · ".join(x for x in (code, pname) if x)
        if head and on:
            return f"{head} — {on}"
        return head or on or f"报告 #{lf.pk}"
    return on or f"报告 #{lf.pk}"


def parse_report_file_id_for_org(
    org: CommissionOrganization,
    user: User,
    raw: str,
) -> int | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        fid = int(raw)
    except ValueError:
        return None
    lf = report_files_for_org_binding(org).filter(pk=fid).first()
    if lf is None:
        return None
    if not library_file_access_allowed(user, lf):
        return None
    return fid


def save_org_hospital_info(
    org: CommissionOrganization,
    *,
    name: str,
    notes: str = "",
    address: str = "",
    introduction: str = "",
    merged_report_file_id: int | None = None,
    update_merged_report: bool = True,
) -> str | None:
    name = (name or "").strip()
    if not name:
        return "名称不能为空"
    dup = False
    if org.level == CommissionOrganization.LEVEL_HOSPITAL:
        dup = (
            CommissionOrganization.objects.filter(
                parent__isnull=True, name=name, is_active=True
            )
            .exclude(pk=org.pk)
            .exists()
        )
    elif org.parent_id:
        dup = (
            CommissionOrganization.objects.filter(
                parent_id=org.parent_id, name=name, is_active=True
            )
            .exclude(pk=org.pk)
            .exists()
        )
    if dup:
        return "同级下已存在相同名称"
    org.name = name
    org.notes = (notes or "").strip()
    org.address = (address or "").strip()
    org.introduction = (introduction or "").strip()
    fields = ["name", "notes", "address", "introduction", "updated_at"]
    if update_merged_report:
        org.merged_report_file_id = merged_report_file_id
        fields.append("merged_report_file_id")
    org.save(update_fields=fields)
    from apps.core.commission_org_service import sync_commission_org_subtree_project_names

    sync_commission_org_subtree_project_names(org)
    return None


def upsert_org_contact(
    org: CommissionOrganization,
    *,
    contact_id: int | None,
    name: str,
    phone: str = "",
    title: str = "",
) -> tuple[CommissionOrgContact | None, str | None]:
    name = (name or "").strip()
    if not name:
        return None, "联系人姓名不能为空"
    if contact_id:
        row = CommissionOrgContact.objects.filter(pk=contact_id, organization_id=org.pk).first()
        if row is None:
            return None, "联系人不存在"
    else:
        row = CommissionOrgContact(organization=org)
    row.name = name
    row.phone = (phone or "").strip()
    row.title = (title or "").strip()
    row.is_active = True
    row.save()
    return row, None


def equipment_fields_from_submit_payload(payload: dict) -> dict[str, str]:
    """从检测提交 JSON 的 equipmentInfo 提取设备主数据（与报告 PDF 回填字段一致）。"""
    ei = payload.get("equipmentInfo") if isinstance(payload, dict) else None
    if not isinstance(ei, dict):
        return {}
    serial = ei.get("serialNo")
    if serial in (None, ""):
        serial = ei.get("serialNumber")
    if serial in (None, ""):
        serial = ei.get("noDevice")
    mfr = ei.get("manufacturer")
    if mfr in (None, ""):
        mfr = ei.get("manufacturerProduction")
    loc = ei.get("location")
    if loc in (None, ""):
        loc = ei.get("device4")
    return {
        "name": str(ei.get("deviceName") or ei.get("equipmentName") or "").strip(),
        "model": str(ei.get("deviceModel") or ei.get("model") or "").strip(),
        "serial_no": str(serial or "").strip() if serial not in (None, "") else "",
        "manufacturer": str(mfr or "").strip() if mfr not in (None, "") else "",
        "location": str(loc or "").strip() if loc not in (None, "") else "",
    }


def resolve_submit_payload_for_report_library_file(lf: LibraryFile) -> dict | None:
    """
    从文件库「报告」成品回溯检测提交 JSON（非解析 PDF 正文）。
    优先 inspection_case 关联；其次从导出文件名中的 taskNo 匹配提交。
    """
    from apps.api.inspection_pdf_service import (
        SUBMIT_PAYLOAD_REQUIRED_KEYS,
        _resolve_submit_payload_for_report_merge,
        resolve_submit_payload_for_report,
    )

    if lf.category != LibraryFile.CATEGORY_REPORT:
        return None
    if lf.link_entity == LibraryFile.LINK_ENTITY_INSPECTION_CASE and lf.link_object_id:
        case = (
            InspectionCase.objects.select_related("library_project")
            .filter(pk=int(lf.link_object_id))
            .first()
        )
        if case is not None and case.library_project_id:
            payload = _resolve_submit_payload_for_report_merge(case, case.library_project)
            if isinstance(payload, dict) and SUBMIT_PAYLOAD_REQUIRED_KEYS.issubset(payload.keys()):
                return payload
    project = lf.projects.order_by("id").first()
    if project is None:
        return None
    case = None
    if lf.link_entity == LibraryFile.LINK_ENTITY_INSPECTION_CASE and lf.link_object_id:
        case = InspectionCase.objects.filter(pk=int(lf.link_object_id)).first()
    if case is None or not case.library_project_id:
        return None
    project = case.library_project
    name = (lf.original_name or "").strip()
    lower = name.lower()
    marker = "-报告.pdf"
    idx = lower.rfind(marker)
    if idx < 0:
        return None
    base = name[:idx].strip()
    if "__task__" in base:
        tail = base.rsplit("__task__", 1)[-1].strip()
        if tail:
            base = tail
    candidates: list[str] = []
    if base:
        candidates.append(base)
        if "_" in base:
            candidates.append(base.replace("_", "/"))
    for tn in dict.fromkeys(candidates):
        payload = resolve_submit_payload_for_report(tn, case, project)
        if isinstance(payload, dict) and SUBMIT_PAYLOAD_REQUIRED_KEYS.issubset(payload.keys()):
            return payload
    return _resolve_submit_payload_for_report_merge(case, project)


def equipment_fields_from_report_file(lf: LibraryFile) -> tuple[dict[str, str], str | None]:
    """
    从报告文件关联的检测提交解析设备信息。
    返回 (字段字典, 错误说明)；成功时错误为 None。
    """
    payload = resolve_submit_payload_for_report_library_file(lf)
    if not payload:
        return {}, "未能从该报告关联到检测提交数据（需为系统导出且关联检测案件的报告 PDF）"
    fields = equipment_fields_from_submit_payload(payload)
    if not any(fields.values()):
        return {}, "检测提交中未填写受检设备信息（equipmentInfo）"
    return fields, None


def merge_equipment_post_with_report_fields(
    *,
    name: str,
    model: str = "",
    serial_no: str = "",
    manufacturer: str = "",
    location: str = "",
    report_fields: dict[str, str],
    fill_blanks_only: bool = True,
) -> dict[str, str]:
    """将报告解析出的设备字段并入表单值；默认仅填补空项。"""
    out = {
        "name": (name or "").strip(),
        "model": (model or "").strip(),
        "serial_no": (serial_no or "").strip(),
        "manufacturer": (manufacturer or "").strip(),
        "location": (location or "").strip(),
    }
    for key in out:
        src = (report_fields.get(key) or "").strip()
        if not src:
            continue
        if fill_blanks_only and out[key]:
            continue
        out[key] = src
    return out


def sync_commission_equipment_from_client_payload(payload: dict) -> None:
    """App 现场检测草稿/提交时，将 equipmentInfo 回写委托单位设备主数据。"""
    if not isinstance(payload, dict):
        return
    raw = (
        str(payload.get("commissionOrgEquipmentId") or payload.get("equipmentId") or "")
        .strip()
    )
    if not raw:
        return
    try:
        eq_id = int(raw)
    except (TypeError, ValueError):
        return
    eq = CommissionOrgEquipment.objects.filter(pk=eq_id, is_active=True).first()
    if eq is None:
        return
    ei = payload.get("equipmentInfo")
    if not isinstance(ei, dict) or not ei:
        return
    apply_equipment_fields_from_submit_payload(eq, {"equipmentInfo": ei}, fill_blanks_only=False)


def apply_equipment_fields_from_submit_payload(
    equipment: CommissionOrgEquipment,
    payload: dict,
    *,
    fill_blanks_only: bool = False,
) -> list[str]:
    """现场记录/报告提交中的 equipmentInfo 回写设备主数据（仅更新有值的字段）。"""
    if not isinstance(payload, dict):
        return []
    fields = equipment_fields_from_submit_payload(payload)
    update_fields: list[str] = []
    for attr, key in (
        ("name", "name"),
        ("model", "model"),
        ("serial_no", "serial_no"),
        ("manufacturer", "manufacturer"),
        ("location", "location"),
    ):
        val = (fields.get(key) or "").strip()
        if not val:
            continue
        cur = (getattr(equipment, attr) or "").strip()
        if fill_blanks_only and cur:
            continue
        setattr(equipment, attr, val)
        update_fields.append(attr)
    if update_fields:
        equipment.save(update_fields=update_fields + ["updated_at"])
    return update_fields


def upsert_department_equipment(
    dept: CommissionOrganization,
    *,
    equipment_id: int | None,
    name: str,
    device_type: str = "",
    model: str = "",
    serial_no: str = "",
    manufacturer: str = "",
    location: str = "",
    notes: str = "",
    report_file_id: int | None = None,
    report_task_id: int | None = None,
    report_task_bindings: list | None = None,
    auto_bind_templates: bool = False,
    user: User | None = None,
    autofill_from_report: bool = True,
    bind_org_for_report: CommissionOrganization | None = None,
) -> tuple[CommissionOrgEquipment | None, str | None]:
    if dept.level not in (
        CommissionOrganization.LEVEL_HOSPITAL,
        CommissionOrganization.LEVEL_CAMPUS,
        CommissionOrganization.LEVEL_DEPARTMENT,
    ):
        return None, "设备只能挂在医院、院区或科室下"
    if not dept.is_active:
        return None, "挂载机构已停用"
    name = (name or "").strip()
    org_for_report = bind_org_for_report or dept
    if report_file_id and user is not None:
        valid = parse_report_file_id_for_org(org_for_report, user, str(report_file_id))
        if valid is None:
            return None, "所选报告无效或无权访问，请从本单位关联项目的已完成报告中选择"
        report_file_id = valid
        if autofill_from_report and report_file_id:
            lf = LibraryFile.objects.filter(pk=report_file_id).first()
            if lf is not None:
                rep_fields, _err = equipment_fields_from_report_file(lf)
                merged = merge_equipment_post_with_report_fields(
                    name=name,
                    model=model,
                    serial_no=serial_no,
                    manufacturer=manufacturer,
                    location=location,
                    report_fields=rep_fields,
                    fill_blanks_only=True,
                )
                name = merged["name"]
                model = merged["model"]
                serial_no = merged["serial_no"]
                manufacturer = merged["manufacturer"]
                location = merged["location"]
    from apps.core.equipment_device_type_service import (
        auto_report_task_bindings_for_device_type,
        normalize_device_type,
    )

    device_type_norm = normalize_device_type(device_type) or ""
    is_new = equipment_id is None

    normalized_bindings: list[dict] = []
    if report_task_bindings is not None:
        normalized_bindings = normalize_equipment_report_task_bindings(report_task_bindings)
    elif auto_bind_templates and device_type_norm:
        normalized_bindings, bind_err = auto_report_task_bindings_for_device_type(device_type_norm)
        if bind_err:
            return None, bind_err
    if report_task_id is not None:
        if report_task_id and parse_report_task_id(str(report_task_id)) is None:
            return None, "所选任务模板无效，请选择报告层级的模板"
        if report_task_id == 0:
            report_task_id = None
    if normalized_bindings:
        report_task_id = normalized_bindings[0]["report_task_id"]
    if is_new and not device_type_norm:
        return None, "请选择设备类型"
    has_template = bool(normalized_bindings or report_task_id or report_file_id)
    if is_new and not has_template:
        return None, "未找到可绑定的报告模板，请检查任务模板库或在编辑时手动绑定"
    if equipment_id:
        row = CommissionOrgEquipment.objects.filter(pk=equipment_id, department_id=dept.pk).first()
        if row is None and bind_org_for_report is not None:
            org_ids = commission_org_subtree_ids(bind_org_for_report.pk)
            row = CommissionOrgEquipment.objects.filter(
                pk=equipment_id, department_id__in=org_ids
            ).first()
        if row is None:
            return None, "设备不存在"
    else:
        row = CommissionOrgEquipment(department=dept)
    prev_type = (row.device_type or "").strip()
    prev_dept_id = row.department_id
    row.department = dept
    if is_new and device_type_norm:
        row.instance_no = next_equipment_instance_no(dept.pk, device_type_norm)
    elif device_type_norm and (
        device_type_norm != prev_type or prev_dept_id != dept.pk
    ):
        row.instance_no = next_equipment_instance_no(
            dept.pk, device_type_norm, exclude_id=row.pk
        )
    elif not row.instance_no:
        row.instance_no = next_equipment_instance_no(
            dept.pk, device_type_norm or prev_type, exclude_id=row.pk
        )
    if device_type_norm:
        default_name = equipment_default_display_name(device_type_norm, row.instance_no)
        stripped = (name or "").strip()
        if is_new and (not stripped or stripped == device_type_norm):
            name = default_name
        elif not is_new and stripped in ("", device_type_norm, prev_type):
            name = default_name
    if not (name or "").strip():
        name = device_type_norm or "设备"
    row.name = name
    row.device_type = device_type_norm
    row.model = (model or "").strip()
    row.serial_no = (serial_no or "").strip()
    row.manufacturer = (manufacturer or "").strip()
    row.location = (location or "").strip()
    row.notes = (notes or "").strip()
    if report_task_id:
        rt = LibraryTask.objects.filter(
            pk=report_task_id, output_target=LibraryTask.OUTPUT_REPORT
        ).first()
        if rt is None:
            return None, "设备只能绑定报告层级的任务模板，现场记录请通过报告模板关联"
        report_task_id = rt.pk
    row.report_task_id = report_task_id if report_task_id else None
    if report_task_bindings is not None:
        row.report_task_bindings = normalized_bindings
    elif normalized_bindings:
        # 添加设备时 auto_bind_templates：report_task_bindings 参数为 None，也需落库
        row.report_task_bindings = normalized_bindings
    row.is_active = True
    if report_file_id:
        lf = LibraryFile.objects.filter(pk=report_file_id).first()
        if lf is not None and user is not None:
            row.save()
            record_equipment_inspection_from_report(row, lf, user=user)
            return row, None
    row.report_file_id = report_file_id
    row.save()
    if device_type_norm:
        renumber_equipment_instances_for_type(dept.pk, device_type_norm)
    if prev_type and prev_type != device_type_norm and prev_dept_id:
        renumber_equipment_instances_for_type(prev_dept_id, prev_type)
    return row, None
