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
    collect_library_tasks_for_project_equipments,
    equipment_display_status,
    equipment_has_report_template_config,
    equipment_project_bind_type_options,
    equipments_for_org_view,
    library_tasks_for_equipment_binding,
    normalize_equipment_report_task,
    resolve_equipment_report_task,
    sync_project_library_tasks_from_equipments,
)
from apps.core.library_task_folder_service import (
    UNCATEGORIZED_LABEL,
    index_task_folders,
    site_tasks_for_report,
    task_folders_active_queryset,
)
from apps.core.models import (
    CommissionOrgEquipment,
    CommissionOrganization,
    LibraryFile,
    LibraryProject,
    LibraryProjectEquipment,
    LibraryTask,
    LibraryTaskFolder,
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


def project_tasks_for_user_assignment(project: LibraryProject) -> list[LibraryTask]:
    """向参与人同步任务时：有委托设备则仅各设备任务链，否则沿用项目已挂载任务。"""
    if LibraryProjectEquipment.objects.filter(project=project).exists():
        tasks = collect_library_tasks_for_project_equipments(project)
        if tasks:
            return tasks
    return list(project.library_tasks.all().order_by("code"))


def project_equipment_queryset(project: LibraryProject) -> QuerySet[LibraryProjectEquipment]:
    return (
        LibraryProjectEquipment.objects.filter(project=project)
        .select_related(
            "equipment",
            "equipment__department",
            "equipment__department__parent",
            "equipment__report_task",
            "equipment__report_task__task_folder",
            "report_task",
            "report_task__task_folder",
        )
        .order_by("sort_order", "id")
    )


def effective_report_task(link: LibraryProjectEquipment) -> LibraryTask | None:
    if link.report_task_id:
        return link.report_task
    itype = (link.inspection_type or "").strip()
    return resolve_equipment_report_task(
        link.equipment, inspection_type=itype or None
    )


def report_task_folder_breadcrumb(task: LibraryTask | None) -> str:
    task = normalize_equipment_report_task(task)
    if task is None:
        return ""
    if not task.task_folder_id:
        return UNCATEGORIZED_LABEL
    folder = getattr(task, "task_folder", None)
    if folder is None:
        return UNCATEGORIZED_LABEL
    return " / ".join(f.name for f in folder.ancestors_chain())


def library_task_equipment_context_by_task_id(
    project: LibraryProject,
) -> dict[int, dict[str, str]]:
    """
    项目内库任务 id → 委托设备上下文（App 任务列表展示用）。

    委托单位名称取设备挂载科室（department）的 full_display_name；
    设备类型、检测类型来自委托设备链接。
    """
    ctx: dict[int, dict[str, str]] = {}
    for link in project_equipment_queryset(project):
        eq = link.equipment
        dept = eq.department
        if dept is not None:
            commission_name = dept.full_display_name
        else:
            commission_name = (project.commission_organization or "").strip()
            if not commission_name and project.commission_org_id:
                org = project.commission_org
                commission_name = org.full_display_name if org else ""
        device_type = (eq.device_type or "").strip()
        inspection_type = (link.inspection_type or "").strip()
        task = normalize_equipment_report_task(effective_report_task(link))
        if task is None:
            continue
        task_ids = {task.pk}
        for src in task.report_source_tasks.filter(
            output_target=LibraryTask.OUTPUT_SITE_RECORD
        ).order_by("code", "id"):
            task_ids.add(src.pk)
        payload = {
            "commissionOrganization": commission_name,
            "deviceType": device_type,
            "inspectionType": inspection_type,
        }
        for tid in task_ids:
            ctx[tid] = payload
    return ctx


def site_submit_tasks_for_report_task(
    project: LibraryProject,
    report_task: LibraryTask | None,
) -> list[dict]:
    """设备所挂载报告下的现场记录任务（含项目内 taskNo，供模拟提交）。"""
    from apps.core.project_numbering import project_task_no_for_library_task, project_tasks_ordered

    if project is None or report_task is None:
        return []
    ordered = project_tasks_ordered(project)
    rows: list[dict] = []
    for st in report_task.report_source_tasks.filter(
        output_target=LibraryTask.OUTPUT_SITE_RECORD
    ).order_by("code", "id"):
        task_no = project_task_no_for_library_task(st, project, ordered_tasks=ordered)
        if not task_no:
            continue
        rows.append(
            {
                "taskNo": task_no,
                "libraryTaskId": st.pk,
                "code": st.code or "",
                "name": st.name or "",
                "label": f"{task_no} · {st.code} · {st.name}",
            }
        )
    return rows


def project_equipment_cards(project: LibraryProject) -> list[dict]:
    cards: list[dict] = []
    for link in project_equipment_queryset(project):
        eq = link.equipment
        task = normalize_equipment_report_task(effective_report_task(link))
        site_labels: list[str] = []
        site_submit_tasks: list[dict] = []
        report_label = ""
        if task is not None:
            report_label = f"{task.code} · {task.name}"
            site_submit_tasks = site_submit_tasks_for_report_task(project, task)
            site_labels = [
                f"{row['code']} · {row['name']}" if row.get("code") else row.get("name") or "—"
                for row in site_submit_tasks
            ]
        itype = (link.inspection_type or "").strip()
        cards.append(
            {
                "link": link,
                "equipment": eq,
                "inspection_type": itype,
                "inspection_type_label": itype or "—",
                "status": equipment_display_status(eq),
                "report_task": task,
                "report_task_label": report_label or "—",
                "report_task_folder": report_task_folder_breadcrumb(task),
                "site_task_labels": site_labels,
                "site_submit_tasks": site_submit_tasks,
                "department_label": eq.department.full_display_name,
                "can_sync_tasks": task is not None,
                "can_mock_submit": bool(site_submit_tasks),
            }
        )
    return cards


def equipment_title_from_card(card: dict) -> str:
    """设备卡片 → 人员可读的受检设备标题。"""
    eq = card["equipment"]
    return " · ".join(
        x
        for x in (
            (eq.name or "").strip(),
            (eq.model or "").strip(),
            (card.get("inspection_type_label") or "").strip(),
        )
        if x
    ) or "—"


def task_no_display_index(project: LibraryProject) -> dict[str, dict]:
    """taskNo → 受检设备、检测项目等人员可读标签（供进度跟踪等界面）。"""
    index: dict[str, dict] = {}
    for card in project_equipment_cards(project):
        equip_title = equipment_title_from_card(card)
        report_label = (card.get("report_task_label") or "").strip() or "—"
        if " · " in report_label:
            _, report_human = report_label.split(" · ", 1)
        else:
            report_human = report_label
        department_label = (card.get("department_label") or "").strip()
        site_rows = card.get("site_submit_tasks") or []
        multi_site = len(site_rows) > 1
        for row in site_rows:
            task_no = str(row.get("taskNo") or "").strip()
            if not task_no:
                continue
            code = (row.get("code") or "").strip()
            if multi_site and code:
                detection_label = f"{report_human}（{code}）"
            else:
                detection_label = report_human
            index[task_no] = {
                "equipment_title": equip_title,
                "detection_label": detection_label,
                "report_task_label": report_label,
                "department_label": department_label,
            }
    return index


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
    return (
        equipments_for_org_view(scope)
        .select_related("department", "report_task")
        .order_by("department__name", "name", "id")
    )


def preview_equipment_folder_tree_for_org(org: CommissionOrganization) -> list[dict]:
    """新建项目向导：按科室文件夹分组设备（供前端文件夹卡片导航）。"""
    buckets: dict[str, list[dict]] = {}
    for row in preview_equipment_rows_for_org(org):
        folder_name = (row.get("department") or "").strip() or "未分配科室"
        buckets.setdefault(folder_name, []).append(row)
    folders: list[dict] = []
    for name in sorted(buckets.keys(), key=lambda s: (s == "未分配科室", s)):
        items = buckets[name]
        folders.append(
            {
                "folder_key": name,
                "name": name,
                "equipment_count": len(items),
                "equipments": items,
            }
        )
    return folders


def preview_equipment_rows_for_org(org: CommissionOrganization) -> list[dict]:
    """新建项目向导：按委托单位范围列出可绑定的设备（含检测类型选项）。"""
    rows: list[dict] = []
    for eq in equipments_for_org_view(org):
        if not equipment_has_report_template_config(eq):
            continue
        type_options = equipment_project_bind_type_options(eq)
        if not type_options:
            continue
        dept = eq.department
        rows.append(
            {
                "equipment_id": eq.pk,
                "name": eq.name,
                "model": (eq.model or "").strip(),
                "serial_no": (eq.serial_no or "").strip(),
                "department": dept.full_display_name if dept else "",
                "type_options": type_options,
                "default_inspection_type": type_options[0]["inspection_type"],
            }
        )
    return rows


def available_equipment_rows_for_project(
    project: LibraryProject,
    *,
    scope_org: CommissionOrganization | None = None,
    fl_path: str = "",
) -> list[dict]:
    """可加入本次委托的设备行（含尚未加入的检测类型选项）。"""
    bound_pairs = {
        (eid, (itype or "").strip())
        for eid, itype in LibraryProjectEquipment.objects.filter(project=project).values_list(
            "equipment_id", "inspection_type"
        )
    }
    rows: list[dict] = []
    for eq in available_equipments_for_project(
        project, scope_org=scope_org, fl_path=fl_path
    ):
        if not equipment_has_report_template_config(eq):
            continue
        type_options = [
            o
            for o in equipment_project_bind_type_options(eq)
            if (eq.pk, o["inspection_type"]) not in bound_pairs
        ]
        if not type_options:
            continue
        rows.append(
            {
                "equipment": eq,
                "type_options": type_options,
                "default_inspection_type": type_options[0]["inspection_type"],
            }
        )
    return rows


def _project_equipment_link_exists(
    project: LibraryProject, equipment_id: int, inspection_type: str
) -> bool:
    return LibraryProjectEquipment.objects.filter(
        project=project,
        equipment_id=equipment_id,
        inspection_type=inspection_type,
    ).exists()


def bind_equipments_to_project(
    project: LibraryProject,
    equipment_ids: list[int],
    user: User,
    *,
    equipment_inspection_types: dict[int, str] | None = None,
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
    allowed_rows = {
        row["equipment"].pk: row
        for row in available_equipment_rows_for_project(
            project, scope_org=scope, fl_path=fl_path
        )
    }
    allowed_ids = set(allowed_rows.keys())
    type_map = equipment_inspection_types or {}
    errors: list[str] = []
    added = 0
    for eid in equipment_ids:
        eq = CommissionOrgEquipment.objects.filter(pk=eid, is_active=True).select_related(
            "department"
        ).first()
        label = eq.name if eq else str(eid)
        if eid not in allowed_ids:
            itype_try = (type_map.get(eid) or "").strip()
            if eq and itype_try and _project_equipment_link_exists(project, eid, itype_try):
                errors.append(f"「{label}」（{itype_try}）已在本次委托中")
            else:
                errors.append(
                    f"设备「{label}」不可加入（不在{scope_hint}范围内，或该检测类型已加入）"
                )
            continue
        if eq is None:
            continue
        itype = (type_map.get(eid) or "").strip()
        type_options = equipment_project_bind_type_options(eq)
        valid_types = {o["inspection_type"] for o in type_options}
        if not itype and len(type_options) == 1:
            itype = type_options[0]["inspection_type"]
        if not itype:
            errors.append(f"「{eq.name}」请选择本次检测类型")
            continue
        if valid_types and itype not in valid_types:
            errors.append(f"「{eq.name}」未配置「{itype}」对应的报告模板")
            continue
        if _project_equipment_link_exists(project, eid, itype):
            errors.append(f"「{eq.name}」（{itype}）已在本次委托中")
            continue
        task = resolve_equipment_report_task(eq, inspection_type=itype)
        if task is None:
            errors.append(f"「{eq.name}」未绑定「{itype}」对应的报告任务模板")
            continue
        link = LibraryProjectEquipment.objects.create(
            project=project,
            equipment=eq,
            inspection_type=itype,
            report_task=task,
        )
        if not eq.report_task_id:
            eq.report_task = task
            eq.save(update_fields=["report_task_id", "updated_at"])
        _, err = bind_equipment_tasks_to_project(
            eq, project, user, inspection_type=itype
        )
        if err:
            link.delete()
            errors.append(f"「{eq.name}」（{itype}）：{err}")
        else:
            added += 1
    return added, errors


def sync_project_task_assignments_for_user(project, assignee, assigned_by) -> tuple[int, int]:
    """将项目下全部任务模板同步给指定用户（幂等），用于绑定设备后自动派发工单。"""
    from apps.core.hospital_info_service import sync_project_library_tasks_from_equipments
    from apps.core.library_file_service import attach_files_to_projects
    from apps.core.models import LibraryProjectWorkflowMember, LibraryTaskAssignment

    if project is None or assignee is None:
        return 0, 0
    sync_project_library_tasks_from_equipments(project, assigned_by)
    project_tasks = project_tasks_for_user_assignment(project)
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


def sync_project_tasks_to_all_workflow_members(project, assigned_by) -> int:
    """将项目任务同步给全部已登记岗位参与人与统筹人（幂等）。"""
    from apps.core.models import LibraryProjectWorkflowMember

    if project is None or assigned_by is None:
        return 0
    seen: set[int] = set()
    total = 0
    for m in LibraryProjectWorkflowMember.objects.filter(project=project).select_related("user"):
        if m.user_id in seen:
            continue
        seen.add(m.user_id)
        created, _ = sync_project_task_assignments_for_user(project, m.user, assigned_by)
        total += created
    if project.primary_responsible_id and project.primary_responsible_id not in seen:
        created, _ = sync_project_task_assignments_for_user(
            project, project.primary_responsible, assigned_by
        )
        total += created
    return total


def unbind_equipment_from_project(
    project: LibraryProject, link_id: int, user: User | None = None
) -> str | None:
    link = LibraryProjectEquipment.objects.filter(project=project, pk=link_id).first()
    if link is None:
        return "委托设备记录不存在"
    link.delete()
    sync_project_library_tasks_from_equipments(project, user)
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
    itype = (link.inspection_type or "").strip()
    _, err = bind_equipment_tasks_to_project(
        eq, project, user, inspection_type=itype or None
    )
    if err is None:
        sync_project_library_tasks_from_equipments(project, user)
    return err


def report_task_options_for_project() -> list[dict]:
    return [
        {"id": t.pk, "label": f"{t.code} · {t.name}"}
        for t in library_tasks_for_equipment_binding()[:300]
    ]


def _report_picker_item(
    report: LibraryTask,
    *,
    has_report_source_relation: bool,
    folder_breadcrumb: str = "",
) -> dict:
    sites: list[dict] = []
    if has_report_source_relation:
        for st in site_tasks_for_report(
            report, {report.pk: report}, has_report_source_relation=True
        ):
            sites.append({"code": st.code or "", "name": st.name or ""})
    return {
        "id": int(report.pk),
        "code": report.code or "",
        "name": report.name or "",
        "label": f"{report.code} · {report.name}" if report.code else (report.name or ""),
        "folderBreadcrumb": folder_breadcrumb,
        "siteTasks": sites,
    }


def _folder_picker_node(
    folder: LibraryTaskFolder,
    *,
    children_by_parent: dict,
    reports_by_folder: dict,
    has_report_source_relation: bool,
    breadcrumb_prefix: str = "",
) -> dict:
    from apps.core.library_task_folder_service import count_reports_in_folder_tree

    child_folders = children_by_parent.get(folder.pk, [])
    reports = reports_by_folder.get(folder.pk, [])
    n_reports = count_reports_in_folder_tree(
        folder.pk, reports_by_folder, children_by_parent
    )
    breadcrumb = folder.name if not breadcrumb_prefix else f"{breadcrumb_prefix} / {folder.name}"
    meta = f"{n_reports} 个报告"
    if child_folders:
        meta += f" · {len(child_folders)} 个子分类"
    if n_reports != len(reports) and not reports and child_folders:
        meta += "（在子分类下）"
    return {
        "folderId": folder.pk,
        "name": folder.name,
        "type": "folder",
        "meta": meta,
        "folders": [
            _folder_picker_node(
                sf,
                children_by_parent=children_by_parent,
                reports_by_folder=reports_by_folder,
                has_report_source_relation=has_report_source_relation,
                breadcrumb_prefix=breadcrumb,
            )
            for sf in child_folders
        ],
        "reports": [
            _report_picker_item(
                r,
                has_report_source_relation=has_report_source_relation,
                folder_breadcrumb=breadcrumb,
            )
            for r in reports
        ],
    }


def build_report_task_picker_catalog(*, has_report_source_relation: bool = True) -> dict:
    """
    委托立项选报告模板：检测类型 → 设备类型 → 报告（含现场记录摘要），供悬浮窗 JS 导航。
    """
    from django.db.models import Prefetch

    folder_rows = list(task_folders_active_queryset())
    _, children_by_parent = index_task_folders(folder_rows)
    site_prefetch = Prefetch(
        "report_source_tasks",
        queryset=LibraryTask.objects.filter(
            output_target=LibraryTask.OUTPUT_SITE_RECORD
        ).order_by("code", "id"),
    )
    reports = list(
        library_tasks_for_equipment_binding()
        .select_related("task_folder")
        .prefetch_related(site_prefetch)
        .order_by("code", "id")
    )
    reports_by_folder: dict[int | None, list[LibraryTask]] = {}
    for report in reports:
        reports_by_folder.setdefault(getattr(report, "task_folder_id", None), []).append(report)

    root_folders = [
        _folder_picker_node(
            f,
            children_by_parent=children_by_parent,
            reports_by_folder=reports_by_folder,
            has_report_source_relation=has_report_source_relation,
        )
        for f in children_by_parent.get(None, [])
    ]
    uncategorized = reports_by_folder.get(None, [])
    return {
        "rootLabel": "全部检测类型",
        "folders": root_folders,
        "uncategorized": {
            "name": UNCATEGORIZED_LABEL,
            "type": "folder",
            "meta": f"{len(uncategorized)} 个报告",
            "folders": [],
            "reports": [
                _report_picker_item(
                    r,
                    has_report_source_relation=has_report_source_relation,
                    folder_breadcrumb=UNCATEGORIZED_LABEL,
                )
                for r in uncategorized
            ],
        },
    }
