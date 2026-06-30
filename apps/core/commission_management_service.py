"""委托管理：按用户汇总项目委托情况，并支持在统一页面分配/撤回。"""
from __future__ import annotations

from collections import defaultdict

from django.contrib.auth.models import User
from django.db.models import Count, Q, QuerySet

from apps.core.library_access import (
    APP_SIDE_ROLE_CODES,
    _role_code,
    library_user_can_assign_tasks_to_participants,
    library_user_has_task_assignment_on_project,
    library_user_is_project_primary_responsible,
    library_user_may_assign_on_project,
    library_user_scoped_project_ids,
    role_has,
)
from apps.core.models import (
    InspectionCaseWorkflowState,
    InspectionSubmission,
    LibraryProject,
    LibraryProjectWorkflowMember,
    LibraryTaskAssignment,
)
from apps.core.project_numbering import PROJECT_CODE_RE, is_standard_commission_code
from apps.core.project_workflow_ui import (
    aggregate_issuance_progress_bars,
    build_issuance_progress_bar,
)
from apps.core.commission_workflow_progress import (
    effective_workflow_stage,
    manual_advance_actions_for_item,
    submission_has_checker_signature,
)
from apps.core.project_equipment_service import project_equipment_cards

# 绑定 ≥2 台受检设备视为大委托（不在列表展示逐条签发进度）
LARGE_COMMISSION_MIN_DEVICES = 2

# 角色可见性层级：数值越大权限越高，可查看层级更低用户的委托汇总
ROLE_VISIBILITY_RANK: dict[str, int] = {
    "super_admin": 100,
    "admin": 90,
    "template_editor": 55,
    "template_tester": 55,
    "authorized_signatory": 45,
    "report_auditor": 40,
    "report_author": 35,
    "site_reviewer": 30,
    "field_inspector": 25,
    "app_user": 20,
}

_SUBMISSION_DONE = frozenset(
    {
        InspectionSubmission.STATUS_SUBMITTED,
        InspectionSubmission.STATUS_APPROVED,
    }
)
_SUBMISSION_OPEN = frozenset(
    {
        InspectionSubmission.STATUS_PENDING,
        InspectionSubmission.STATUS_IN_PROGRESS,
        InspectionSubmission.STATUS_REJECTED,
    }
)


def is_large_commission(equipment_count: int) -> bool:
    """大委托：多台受检设备，列表不展示签发进度条。"""
    return int(equipment_count or 0) >= LARGE_COMMISSION_MIN_DEVICES


def library_user_may_access_commission_manage(user) -> bool:
    """是否可进入「委托管理」页面。"""
    if not getattr(user, "is_authenticated", False):
        return False
    if not role_has(user, "perm_file_library"):
        return False
    if library_user_can_assign_tasks_to_participants(user):
        return True
    if getattr(user, "is_superuser", False) or _role_code(user) in ("super_admin", "admin"):
        return True
    if LibraryProject.objects.filter(primary_responsible=user, is_active=True).exists():
        return True
    if LibraryTaskAssignment.objects.filter(assignee=user, project_id__isnull=False).exists():
        return True
    if LibraryProjectWorkflowMember.objects.filter(user=user, project__is_active=True).exists():
        return True
    return bool(library_user_scoped_project_ids(user))


def _viewer_rank(user) -> int:
    if getattr(user, "is_superuser", False):
        return 100
    return ROLE_VISIBILITY_RANK.get(_role_code(user), 0)


def commission_may_view_user_stats(viewer: User, subject: User) -> bool:
    if viewer.pk == subject.pk:
        return True
    if library_user_can_assign_tasks_to_participants(viewer):
        return True
    if getattr(viewer, "is_superuser", False) or _role_code(viewer) in ("super_admin", "admin"):
        return True
    sub_rank = ROLE_VISIBILITY_RANK.get(_role_code(subject), 0)
    if _viewer_rank(viewer) > sub_rank and _role_code(subject) in APP_SIDE_ROLE_CODES:
        return True
    assignee_on_my_projects = LibraryTaskAssignment.objects.filter(
        assignee=subject,
        project__primary_responsible=viewer,
        project__is_active=True,
    ).exists()
    if assignee_on_my_projects:
        return True
    return False


def commission_visible_subject_users(viewer: User) -> QuerySet[User]:
    """当前登录用户可查看委托汇总的用户列表。"""
    base = User.objects.filter(is_active=True).select_related("profile", "profile__role")
    if library_user_can_assign_tasks_to_participants(viewer):
        return base.filter(profile__role__code__in=APP_SIDE_ROLE_CODES).order_by("username")
    if getattr(viewer, "is_superuser", False) or _role_code(viewer) in ("super_admin", "admin"):
        return base.filter(profile__role__code__in=APP_SIDE_ROLE_CODES).order_by("username")

    visible_ids: set[int] = {viewer.pk}
    vr = _viewer_rank(viewer)
    lower_codes = [
        c for c, r in ROLE_VISIBILITY_RANK.items() if r < vr and c in APP_SIDE_ROLE_CODES
    ]
    if lower_codes:
        visible_ids.update(
            base.filter(profile__role__code__in=lower_codes).values_list("pk", flat=True)
        )
    my_pids = list(
        LibraryProject.objects.filter(primary_responsible=viewer, is_active=True).values_list(
            "pk", flat=True
        )
    )
    if my_pids:
        visible_ids.update(
            LibraryTaskAssignment.objects.filter(project_id__in=my_pids).values_list(
                "assignee_id", flat=True
            )
        )
    return base.filter(pk__in=visible_ids).order_by("username")


def commission_accessible_project_ids(viewer: User) -> set[int]:
    """
    当前用户可查看的委托项目：名下参与（分配/负责/创建）+ 流程岗位 + 可管理范围。
    全局任务分配者/管理员见全部活跃项目。
    """
    if library_user_can_assign_tasks_to_participants(viewer):
        return set(
            LibraryProject.objects.filter(is_active=True).values_list("pk", flat=True)
        )
    if getattr(viewer, "is_superuser", False) or _role_code(viewer) in ("super_admin", "admin"):
        return set(
            LibraryProject.objects.filter(is_active=True).values_list("pk", flat=True)
        )
    ids: set[int] = set(library_user_scoped_project_ids(viewer))
    ids.update(
        LibraryProjectWorkflowMember.objects.filter(
            user=viewer, project__is_active=True
        ).values_list("project_id", flat=True)
    )
    return ids


def commission_project_relation_labels(viewer: User, project: LibraryProject) -> list[str]:
    """当前用户与委托项目的参与关系（用于列表展示）。"""
    labels: list[str] = []
    if library_user_is_project_primary_responsible(viewer, project):
        labels.append("负责")
    if library_user_has_task_assignment_on_project(viewer, project):
        if "参与" not in labels:
            labels.append("参与")
    if (
        LibraryProjectWorkflowMember.objects.filter(user=viewer, project=project).exists()
        and "负责" not in labels
        and "参与" not in labels
    ):
        labels.append("流程")
    if getattr(project, "created_by_id", None) == viewer.pk and "负责" not in labels:
        labels.append("创建")
    if library_user_may_assign_on_project(viewer, project):
        if library_user_can_assign_tasks_to_participants(viewer) and "负责" not in labels:
            labels.append("可分配")
        elif library_user_is_project_primary_responsible(viewer, project):
            pass  # 已在「负责」
    return labels


def _project_on_viewer_name(viewer: User, project: LibraryProject) -> bool:
    if library_user_is_project_primary_responsible(viewer, project):
        return True
    if library_user_has_task_assignment_on_project(viewer, project):
        return True
    if getattr(project, "created_by_id", None) == viewer.pk:
        return True
    return False


def commission_projects_queryset(
    viewer: User,
    *,
    subject_user: User | None = None,
) -> QuerySet[LibraryProject]:
    qs = (
        LibraryProject.objects.filter(is_active=True)
        .select_related("commission_org", "primary_responsible", "created_by")
        .prefetch_related("project_equipments")
        .annotate(_equipment_count=Count("project_equipments", distinct=True))
    )
    if subject_user is not None and not commission_may_view_user_stats(viewer, subject_user):
        return qs.none()

    accessible = commission_accessible_project_ids(viewer)

    if library_user_can_assign_tasks_to_participants(viewer):
        if subject_user is None:
            return qs.order_by("-updated_at", "-id")
        return (
            qs.filter(
                Q(primary_responsible=subject_user)
                | Q(task_assignments__assignee=subject_user)
            )
            .distinct()
            .order_by("-updated_at", "-id")
        )

    if subject_user is not None and subject_user.pk != viewer.pk:
        return (
            qs.filter(
                Q(primary_responsible=subject_user)
                | Q(task_assignments__assignee=subject_user)
            )
            .filter(pk__in=accessible)
            .distinct()
            .order_by("-updated_at", "-id")
        )

    if not accessible:
        return qs.none()
    return qs.filter(pk__in=accessible).order_by("-updated_at", "-id")


def _completion_map(project_ids: list[int]) -> dict[int, str]:
    """返回 project_id -> completed | incomplete"""
    if not project_ids:
        return {}
    subs = InspectionSubmission.objects.filter(project_id__in=project_ids).values_list(
        "project_id", "status"
    )
    by_project: dict[int, list[str]] = defaultdict(list)
    for pid, st in subs:
        by_project[pid].append(st)
    out: dict[int, str] = {}
    for pid in project_ids:
        statuses = by_project.get(pid) or []
        if not statuses:
            out[pid] = "incomplete"
        elif any(s in _SUBMISSION_OPEN for s in statuses):
            out[pid] = "incomplete"
        elif all(s in _SUBMISSION_DONE for s in statuses):
            out[pid] = "completed"
        else:
            out[pid] = "incomplete"
    return out


def _bulk_submissions_by_project(project_ids: list[int]) -> dict[int, dict[str, InspectionSubmission]]:
    """project_id -> task_no -> submission（含 case / workflow_state）。"""
    if not project_ids:
        return {}
    out: dict[int, dict[str, InspectionSubmission]] = defaultdict(dict)
    qs = (
        InspectionSubmission.objects.filter(project_id__in=project_ids)
        .select_related("case", "case__workflow_state")
        .order_by("-updated_at")
    )
    for sub in qs:
        bucket = out[sub.project_id]
        if sub.task_no not in bucket:
            bucket[sub.task_no] = sub
    return out


def _submission_workflow_stage(sub: InspectionSubmission | None) -> str:
    if sub is None or sub.case_id is None:
        return ""
    return effective_workflow_stage(sub.case, sub)


def _submission_workflow_updated_at(sub: InspectionSubmission | None):
    if sub is None or sub.case_id is None:
        return None
    st = getattr(sub.case, "workflow_state", None)
    if st is not None and st.updated_at is not None:
        return st.updated_at
    return sub.updated_at


def build_commission_project_item_progress(
    project: LibraryProject,
    submissions_by_task: dict[str, InspectionSubmission],
    *,
    viewer: User,
) -> list[dict]:
    """委托内各检测项目（设备×任务）的签发流程进度。"""
    items: list[dict] = []
    seen_task_nos: set[str] = set()

    for card in project_equipment_cards(project):
        eq = card["equipment"]
        equip_title = " · ".join(
            x
            for x in (
                (eq.name or "").strip(),
                (eq.model or "").strip(),
                card.get("inspection_type_label") or "",
            )
            if x
        )
        site_rows = card.get("site_submit_tasks") or []
        if site_rows:
            for row in site_rows:
                task_no = str(row.get("taskNo") or "").strip()
                if not task_no or task_no in seen_task_nos:
                    continue
                seen_task_nos.add(task_no)
                sub = submissions_by_task.get(task_no)
                stage = _submission_workflow_stage(sub)
                bar = build_issuance_progress_bar(stage or None)
                case_id = sub.case_id if sub else None
                items.append(
                    {
                        "item_key": f"{project.pk}-{task_no}",
                        "equipment_title": equip_title or "—",
                        "task_no": task_no,
                        "task_label": row.get("label") or task_no,
                        "report_task_label": card.get("report_task_label") or "—",
                        "case_id": case_id,
                        "stage_code": bar["stage_code"],
                        "stage_label": bar["stage_label"],
                        "has_checker_signature": submission_has_checker_signature(sub),
                        "progress_updated_at": _submission_workflow_updated_at(sub),
                        "submission_status": sub.get_status_display() if sub else "—",
                        "progress_bar": bar,
                        "advance_actions": manual_advance_actions_for_item(
                            viewer,
                            project,
                            effective_stage=stage or "",
                            has_case=bool(case_id),
                        ),
                    }
                )
        else:
            task_label = card.get("report_task_label") or "—"
            items.append(
                {
                    "item_key": f"{project.pk}-eq-{card['link'].pk}",
                    "equipment_title": equip_title or "—",
                    "task_no": "",
                    "task_label": task_label,
                    "report_task_label": task_label,
                    "case_id": None,
                    "stage_code": "",
                    "stage_label": "未开始",
                    "has_checker_signature": False,
                    "progress_updated_at": None,
                    "submission_status": "—",
                    "progress_bar": build_issuance_progress_bar(None),
                    "advance_actions": [],
                }
            )

    for task_no, sub in sorted(submissions_by_task.items(), key=lambda x: x[0]):
        if task_no in seen_task_nos:
            continue
        stage = _submission_workflow_stage(sub)
        bar = build_issuance_progress_bar(stage or None)
        items.append(
            {
                "item_key": f"{project.pk}-{task_no}-orphan",
                "equipment_title": "其他提交",
                "task_no": task_no,
                "task_label": f"{task_no} · {sub.report_type}",
                "report_task_label": sub.report_type or "—",
                "case_id": sub.case_id,
                "stage_code": bar["stage_code"],
                "stage_label": bar["stage_label"],
                "has_checker_signature": submission_has_checker_signature(sub),
                "progress_updated_at": _submission_workflow_updated_at(sub),
                "submission_status": sub.get_status_display(),
                "progress_bar": bar,
                "advance_actions": manual_advance_actions_for_item(
                    viewer,
                    project,
                    effective_stage=stage or "",
                    has_case=bool(sub.case_id),
                ),
            }
        )

    if not items:
        items.append(
            {
                "item_key": f"{project.pk}-empty",
                "equipment_title": "—",
                "task_no": "",
                "task_label": "尚未绑定检测项目",
                "report_task_label": "—",
                "case_id": None,
                "stage_code": "",
                "stage_label": "未开始",
                "has_checker_signature": False,
                "progress_updated_at": None,
                "submission_status": "—",
                "progress_bar": build_issuance_progress_bar(None),
                "advance_actions": [],
            }
        )
    return items


def _bulk_project_item_progress(
    projects: list[LibraryProject],
    *,
    viewer: User,
    include_large: bool = True,
) -> dict[int, list[dict]]:
    pids = [p.pk for p in projects]
    subs_map = _bulk_submissions_by_project(pids)
    out: dict[int, list[dict]] = {}
    for p in projects:
        eq_count = int(getattr(p, "_equipment_count", 0) or 0)
        if not include_large and is_large_commission(eq_count):
            out[p.pk] = []
            continue
        out[p.pk] = build_commission_project_item_progress(
            p, subs_map.get(p.pk) or {}, viewer=viewer
        )
    return out


def commission_stats_for_projects(project_ids: list[int]) -> dict[str, int]:
    cmap = _completion_map(project_ids)
    total = len(project_ids)
    completed = sum(1 for v in cmap.values() if v == "completed")
    incomplete = total - completed
    return {"total": total, "completed": completed, "incomplete": incomplete}


def commission_user_summary_rows(viewer: User) -> list[dict]:
    """各用户委托数量汇总（仅 viewer 可见的用户）。"""
    rows: list[dict] = []
    for subject in commission_visible_subject_users(viewer):
        pids = list(
            commission_projects_queryset(viewer, subject_user=subject).values_list("pk", flat=True)
        )
        stats = commission_stats_for_projects(pids)
        role = getattr(getattr(subject, "profile", None), "role", None)
        rows.append(
            {
                "user": subject,
                "role_name": role.name if role else "—",
                "role_code": role.code if role else "",
                **stats,
            }
        )
    return rows


def commission_project_rows(
    viewer: User,
    *,
    subject_user: User | None,
    status_filter: str = "",
    scope_filter: str = "",
    search: str = "",
) -> list[dict]:
    qs = commission_projects_queryset(viewer, subject_user=subject_user)
    q = (search or "").strip()
    if q:
        compact = q.replace(" ", "")
        if is_standard_commission_code(compact) or PROJECT_CODE_RE.fullmatch(compact):
            qs = qs.filter(code__iexact=compact)
        elif compact.isdigit() and len(compact) == 6:
            qs = qs.filter(code__iexact=compact)
        else:
            qs = qs.filter(
                Q(code__icontains=q)
                | Q(name__icontains=q)
                | Q(commission_organization__icontains=q)
            )
    projects = list(qs[:500])
    pids = [p.pk for p in projects]
    cmap = _completion_map(pids)
    items_by_project = _bulk_project_item_progress(projects, viewer=viewer)

    assignees_by_project: dict[int, list[dict]] = defaultdict(list)
    for row in (
        LibraryTaskAssignment.objects.filter(project_id__in=pids)
        .select_related("assignee", "library_task")
        .order_by("assignee__username", "library_task__code")
    ):
        assignees_by_project[row.project_id].append(
            {
                "assignee_id": row.assignee_id,
                "username": row.assignee.username,
                "task_code": row.library_task.code,
                "assignment_id": row.pk,
            }
        )

    out: list[dict] = []
    for p in projects:
        completion = cmap.get(p.pk, "incomplete")
        if status_filter == "completed" and completion != "completed":
            continue
        if status_filter == "incomplete" and completion != "incomplete":
            continue
        relations = commission_project_relation_labels(viewer, p)
        on_name = _project_on_viewer_name(viewer, p)
        can_manage = library_user_may_assign_on_project(viewer, p) or on_name
        if scope_filter == "mine" and not on_name:
            continue
        if scope_filter == "manage" and not library_user_may_assign_on_project(viewer, p):
            continue
        equipment_count = int(getattr(p, "_equipment_count", 0) or 0)
        large_commission = is_large_commission(equipment_count)
        show_issuance_progress = not large_commission
        item_rows = items_by_project.get(p.pk) or []
        item_bars = [row["progress_bar"] for row in item_rows] if show_issuance_progress else []
        agg_bar = (
            aggregate_issuance_progress_bars(item_bars)
            if show_issuance_progress
            else build_issuance_progress_bar(None)
        )
        latest_ts = None
        for row in item_rows:
            ts = row.get("progress_updated_at")
            if ts is not None and (latest_ts is None or ts > latest_ts):
                latest_ts = ts
        assignees = assignees_by_project.get(p.pk, [])
        unique_users = {a["assignee_id"]: a["username"] for a in assignees}
        sort_ts = latest_ts or p.updated_at
        issued = agg_bar.get("issued_count", 0) if show_issuance_progress else 0
        total_items = agg_bar.get("total_items", len(item_rows)) if show_issuance_progress else 0
        if large_commission:
            progress_summary = f"大委托 · {equipment_count} 台设备 · {len(item_rows)} 项检测"
            progress_label = "—"
        elif total_items and issued:
            progress_summary = f"{issued}/{total_items} 已签发"
            progress_label = agg_bar["stage_label"]
        else:
            progress_summary = f"{len(item_rows)} 项检测" if item_rows else "—"
            progress_label = agg_bar["stage_label"]
        out.append(
            {
                "project": p,
                "completion": completion,
                "completion_label": "已完成" if completion == "completed" else "未完成",
                "progress_label": progress_label,
                "progress_summary": progress_summary,
                "progress_updated_at": latest_ts,
                "progress_bar": agg_bar if show_issuance_progress else None,
                "item_progress_rows": item_rows,
                "is_large_commission": large_commission,
                "show_issuance_progress": show_issuance_progress,
                "show_item_expand": bool(item_rows),
                "relation_labels": relations,
                "on_viewer_name": on_name,
                "equipment_count": equipment_count,
                "task_count": p.library_tasks.count(),
                "assignee_labels": ", ".join(sorted(set(unique_users.values()))) or "—",
                "assignees": assignees,
                "can_manage": can_manage,
                "workbench_url": f"?project_id={p.pk}&tab=submissions",
                "_sort_ts": sort_ts,
            }
        )
    out.sort(
        key=lambda row: (
            row["_sort_ts"] is not None,
            row["_sort_ts"] or row["project"].updated_at,
        ),
        reverse=True,
    )
    for row in out:
        row.pop("_sort_ts", None)
    return out


def build_commission_message_center(
    viewer: User,
    *,
    max_items: int = 36,
    recent_days: int = 14,
) -> dict:
    """
    委托管理页消息中心：优先展示待当前用户处理的环节，其次为近期进度更新。
    大委托按汇总提醒，不展开逐条签发进度。
    """
    from datetime import timedelta

    from django.utils import timezone

    projects = list(commission_projects_queryset(viewer).order_by("-updated_at")[:80])
    if not projects:
        return {"items": [], "my_workflow_count": 0, "recent_count": 0}

    items_by_project = _bulk_project_item_progress(
        projects, viewer=viewer, include_large=True
    )
    recent_cutoff = timezone.now() - timedelta(days=max(1, recent_days))
    notices: list[dict] = []

    for p in projects:
        eq_count = int(getattr(p, "_equipment_count", 0) or 0)
        large = is_large_commission(eq_count)
        item_rows = items_by_project.get(p.pk) or []
        my_pending = [
            i
            for i in item_rows
            if i.get("task_no") and i.get("advance_actions")
        ]

        if large and my_pending:
            sample = "、".join(
                (i.get("equipment_title") or i.get("task_no") or "—")[:24]
                for i in my_pending[:3]
            )
            if len(my_pending) > 3:
                sample += "…"
            ts_list = [
                i["progress_updated_at"]
                for i in my_pending
                if i.get("progress_updated_at")
            ]
            notices.append(
                {
                    "kind": "my_workflow",
                    "priority": "high",
                    "is_large_commission": True,
                    "project_id": p.pk,
                    "project_code": p.code,
                    "project_name": p.name,
                    "equipment_title": "",
                    "task_no": "",
                    "stage_label": my_pending[0].get("stage_label") or "—",
                    "message": f"大委托 {p.code}：{len(my_pending)} 项待您处理",
                    "detail": sample or f"共 {eq_count} 台设备",
                    "updated_at": max(ts_list, default=p.updated_at),
                    "workbench_url": f"?project_id={p.pk}&tab=submissions",
                }
            )
            continue

        if large:
            continue

        for item in item_rows:
            task_no = str(item.get("task_no") or "").strip()
            if not task_no:
                continue
            actions = item.get("advance_actions") or []
            if actions:
                act = actions[0]
                notices.append(
                    {
                        "kind": "my_workflow",
                        "priority": "high",
                        "is_large_commission": False,
                        "project_id": p.pk,
                        "project_code": p.code,
                        "project_name": p.name,
                        "equipment_title": item.get("equipment_title") or "—",
                        "task_no": task_no,
                        "stage_label": item.get("stage_label") or "—",
                        "message": f"待您{act['label']}：{item.get('equipment_title') or task_no}",
                        "detail": f"{p.code} · task {task_no} · {item.get('stage_label') or '—'}",
                        "updated_at": item.get("progress_updated_at") or p.updated_at,
                        "workbench_url": f"?project_id={p.pk}&tab=submissions",
                        "advance_action": act,
                    }
                )
                continue
            ts = item.get("progress_updated_at")
            stage_code = str(item.get("stage_code") or "")
            if (
                ts
                and ts >= recent_cutoff
                and stage_code != InspectionCaseWorkflowState.STAGE_ISSUED
            ):
                notices.append(
                    {
                        "kind": "recent",
                        "priority": "normal",
                        "is_large_commission": False,
                        "project_id": p.pk,
                        "project_code": p.code,
                        "project_name": p.name,
                        "equipment_title": item.get("equipment_title") or "—",
                        "task_no": task_no,
                        "stage_label": item.get("stage_label") or "—",
                        "message": f"进度更新：{item.get('equipment_title') or task_no}",
                        "detail": f"{p.code} · {item.get('stage_label') or '—'}",
                        "updated_at": ts,
                        "workbench_url": f"?project_id={p.pk}&tab=submissions",
                    }
                )

    def _sort_key(n: dict) -> tuple:
        kind_rank = 0 if n.get("kind") == "my_workflow" else 1
        ts = n.get("updated_at")
        ts_val = ts.timestamp() if ts is not None else 0.0
        return (kind_rank, -ts_val)

    notices.sort(key=_sort_key)
    items = notices[:max_items]
    return {
        "items": items,
        "my_workflow_count": sum(1 for n in notices if n.get("kind") == "my_workflow"),
        "recent_count": sum(1 for n in notices if n.get("kind") == "recent"),
    }


def commission_overview_for_viewer(viewer: User, *, subject_user: User | None) -> dict[str, int]:
    pids = list(
        commission_projects_queryset(viewer, subject_user=subject_user).values_list("pk", flat=True)
    )
    return commission_stats_for_projects(pids)
