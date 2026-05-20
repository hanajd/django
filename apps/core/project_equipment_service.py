"""项目工作台：以设备为中心的委托（绑定设备 → 任务模板 → 报告/现场记录）。"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.db.models import QuerySet

from apps.core.commission_org_service import (
    commission_orgs_active_queryset,
    index_commission_orgs,
    parse_commission_path_segments,
    resolve_org_id_from_segments,
)
from apps.core.hospital_info_service import (
    bind_equipment_tasks_to_project,
    equipment_display_status,
    equipments_for_org_view,
    library_tasks_for_equipment_binding,
    resolve_equipment_report_task,
)
from apps.core.models import (
    CommissionOrgEquipment,
    CommissionOrganization,
    LibraryFile,
    LibraryProject,
    LibraryProjectEquipment,
    LibraryTask,
)


def hospital_for_project(project: LibraryProject) -> CommissionOrganization | None:
    if not project.commission_org_id:
        return None
    return project.commission_org.hospital_root()


def equipment_scope_label(org: CommissionOrganization) -> str:
    if org.level == CommissionOrganization.LEVEL_DEPARTMENT:
        return f"科室「{org.name}」"
    if org.level == CommissionOrganization.LEVEL_CAMPUS:
        return f"院区「{org.name}」"
    return f"医院「{org.name}」"


def resolve_workbench_equipment_scope_org(
    project: LibraryProject,
    *,
    fl_path: str = "",
    scope_org: CommissionOrganization | None = None,
) -> CommissionOrganization | None:
    """
    项目工作台绑定设备时的组织范围：当前浏览的科室/院区/医院及其下级设备；
    不可跨同级院区或科室。须与项目所属医院一致。
    """
    hospital = hospital_for_project(project)
    if hospital is None:
        return None
    org = scope_org
    if org is None and (fl_path or "").strip():
        rows = list(commission_orgs_active_queryset())
        org_by_id, _, _ = index_commission_orgs(rows)
        oid = resolve_org_id_from_segments(parse_commission_path_segments(fl_path), org_by_id)
        if oid is not None:
            org = org_by_id.get(oid)
    if org is None and project.commission_org_id:
        org = project.commission_org
    if org is None:
        org = hospital
    if org.hospital_root().pk != hospital.pk:
        return None
    return org


def project_equipment_queryset(project: LibraryProject) -> QuerySet[LibraryProjectEquipment]:
    return (
        LibraryProjectEquipment.objects.filter(project=project)
        .select_related(
            "equipment",
            "equipment__department",
            "equipment__department__parent",
            "equipment__report_task",
            "report_task",
        )
        .order_by("sort_order", "id")
    )


def effective_report_task(link: LibraryProjectEquipment) -> LibraryTask | None:
    if link.report_task_id:
        return link.report_task
    return resolve_equipment_report_task(link.equipment)


def project_equipment_cards(project: LibraryProject) -> list[dict]:
    cards: list[dict] = []
    for link in project_equipment_queryset(project):
        eq = link.equipment
        task = effective_report_task(link)
        site_labels: list[str] = []
        report_label = ""
        if task is not None:
            if task.output_target == LibraryTask.OUTPUT_REPORT:
                report_label = f"{task.code} · {task.name}"
                site_labels = [
                    f"{t.code} · {t.name}" for t in task.report_source_tasks.all().order_by("code")
                ]
            else:
                report_label = "—"
                site_labels = [f"{task.code} · {task.name}"]
                for rt in task.report_target_tasks.all().order_by("code"):
                    report_label = f"{rt.code} · {rt.name}"
        cards.append(
            {
                "link": link,
                "equipment": eq,
                "status": equipment_display_status(eq),
                "report_task": task,
                "report_task_label": report_label or "—",
                "site_task_labels": site_labels,
                "department_label": eq.department.full_display_name,
                "can_sync_tasks": task is not None,
            }
        )
    return cards


def available_equipments_for_project(
    project: LibraryProject,
    *,
    scope_org: CommissionOrganization | None = None,
    fl_path: str = "",
) -> QuerySet[CommissionOrgEquipment]:
    """当前工作台组织范围内、尚未加入本项目的设备（上级含下级科室/院区）。"""
    scope = resolve_workbench_equipment_scope_org(
        project, scope_org=scope_org, fl_path=fl_path
    )
    if scope is None:
        return CommissionOrgEquipment.objects.none()
    bound_ids = LibraryProjectEquipment.objects.filter(project=project).values_list(
        "equipment_id", flat=True
    )
    return (
        equipments_for_org_view(scope)
        .exclude(pk__in=bound_ids)
        .select_related("department", "report_task")
        .order_by("department__name", "name", "id")
    )


def bind_equipments_to_project(
    project: LibraryProject,
    equipment_ids: list[int],
    user: User,
    *,
    scope_org: CommissionOrganization | None = None,
    fl_path: str = "",
) -> tuple[int, list[str]]:
    """将设备加入项目委托，并同步关联任务模板链到项目。"""
    if not equipment_ids:
        return 0, ["请至少选择一台设备"]
    hospital = hospital_for_project(project)
    if hospital is None:
        return 0, ["请先将项目绑定到委托单位（医院）"]
    scope = resolve_workbench_equipment_scope_org(
        project, scope_org=scope_org, fl_path=fl_path
    )
    if scope is None:
        return 0, ["当前浏览范围与项目所属医院不一致，无法绑定设备"]

    scope_hint = equipment_scope_label(scope)
    allowed_ids = set(
        available_equipments_for_project(project, scope_org=scope, fl_path=fl_path).values_list(
            "pk", flat=True
        )
    )
    errors: list[str] = []
    added = 0
    for eid in equipment_ids:
        if eid not in allowed_ids:
            eq = CommissionOrgEquipment.objects.filter(pk=eid, is_active=True).first()
            label = eq.name if eq else str(eid)
            if LibraryProjectEquipment.objects.filter(project=project, equipment_id=eid).exists():
                errors.append(f"「{label}」已在本次委托中")
            else:
                errors.append(
                    f"设备「{label}」不可加入（不在{scope_hint}范围内，或已加入本次委托）"
                )
            continue
        eq = CommissionOrgEquipment.objects.filter(pk=eid, is_active=True).select_related(
            "department"
        ).first()
        if eq is None:
            continue
        task = resolve_equipment_report_task(eq)
        if task is None:
            errors.append(f"「{eq.name}」未绑定检测任务模板，请先在设备主数据或下方选择模板")
            continue
        link, created = LibraryProjectEquipment.objects.get_or_create(
            project=project,
            equipment=eq,
            defaults={"report_task": task},
        )
        if not created and link.report_task_id != task.pk:
            link.report_task = task
            link.save(update_fields=["report_task"])
        if not eq.report_task_id:
            eq.report_task = task
            eq.save(update_fields=["report_task_id", "updated_at"])
        _, err = bind_equipment_tasks_to_project(eq, project, user)
        if err:
            errors.append(f"「{eq.name}」：{err}")
        else:
            added += 1
    return added, errors


def sync_project_task_assignments_for_user(project, assignee, assigned_by) -> tuple[int, int]:
    """将项目下全部任务模板同步给指定用户（幂等），用于绑定设备后自动派发工单。"""
    from apps.core.library_file_service import attach_files_to_projects
    from apps.core.models import LibraryProjectWorkflowMember, LibraryTaskAssignment

    if project is None or assignee is None:
        return 0, 0
    project_tasks = list(project.library_tasks.all().order_by("code"))
    if not project_tasks:
        return 0, 0
    created_count = 0
    all_file_ids: set[int] = set()
    for library_task in project_tasks:
        exists = LibraryTaskAssignment.objects.filter(
            library_task=library_task,
            project=project,
            assignee=assignee,
        ).exists()
        if not exists:
            LibraryTaskAssignment.objects.create(
                library_task=library_task,
                project=project,
                assignee=assignee,
                assigned_by=assigned_by,
            )
            created_count += 1
        for fid in library_task.library_files.filter(
            category=LibraryFile.CATEGORY_TEMPLATE
        ).values_list("pk", flat=True):
            all_file_ids.add(fid)
    LibraryProjectWorkflowMember.ensure_for_project_assignment(project, assignee)
    if all_file_ids:
        attach_files_to_projects(sorted(all_file_ids), [project.pk], assigned_by)
    return created_count, len(all_file_ids)


def unbind_equipment_from_project(project: LibraryProject, link_id: int) -> str | None:
    link = LibraryProjectEquipment.objects.filter(project=project, pk=link_id).first()
    if link is None:
        return "委托设备记录不存在"
    link.delete()
    return None


def update_project_equipment_report_task(
    project: LibraryProject,
    link_id: int,
    report_task_id: int | None,
    user: User,
) -> str | None:
    link = (
        LibraryProjectEquipment.objects.filter(project=project, pk=link_id)
        .select_related("equipment")
        .first()
    )
    if link is None:
        return "委托设备记录不存在"
    if not report_task_id:
        return "请选择报告层级的任务模板"
    task = (
        library_tasks_for_equipment_binding()
        .filter(pk=report_task_id, output_target=LibraryTask.OUTPUT_REPORT)
        .first()
    )
    if task is None:
        return "报告模板无效，请选择报告层级的任务"
    link.report_task = task
    link.save(update_fields=["report_task"])
    eq = link.equipment
    eq.report_task = task
    eq.save(update_fields=["report_task_id", "updated_at"])
    _, err = bind_equipment_tasks_to_project(eq, project, user)
    return err


def report_task_options_for_project() -> list[dict]:
    return [
        {"id": t.pk, "label": f"{t.code} · {t.name}"}
        for t in library_tasks_for_equipment_binding()[:300]
    ]
