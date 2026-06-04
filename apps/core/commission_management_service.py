"""委托管理：按用户汇总项目委托情况，并支持在统一页面分配/撤回。"""
from __future__ import annotations

from collections import defaultdict

from django.contrib.auth.models import User
from django.db.models import Q, QuerySet

from apps.core.library_access import (
    APP_SIDE_ROLE_CODES,
    _role_code,
    library_user_can_assign_tasks_to_participants,
    library_user_is_project_primary_responsible,
    library_user_may_assign_on_project,
    library_user_scoped_project_ids,
    role_has,
)
from apps.core.models import InspectionSubmission, LibraryProject, LibraryTaskAssignment
from apps.core.project_numbering import PROJECT_CODE_RE, is_standard_commission_code

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


def commission_projects_queryset(
    viewer: User,
    *,
    subject_user: User | None = None,
) -> QuerySet[LibraryProject]:
    qs = (
        LibraryProject.objects.filter(is_active=True)
        .select_related("commission_org", "primary_responsible", "created_by")
        .prefetch_related("project_equipments")
    )
    if subject_user is not None and not commission_may_view_user_stats(viewer, subject_user):
        return qs.none()

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
            .distinct()
            .order_by("-updated_at", "-id")
        )

    scoped: set[int] = set(library_user_scoped_project_ids(viewer))
    scoped.update(
        LibraryProject.objects.filter(primary_responsible=viewer, is_active=True).values_list(
            "pk", flat=True
        )
    )
    if not scoped:
        return qs.none()
    return qs.filter(pk__in=scoped).order_by("-updated_at", "-id")


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
        assignees = assignees_by_project.get(p.pk, [])
        unique_users = {a["assignee_id"]: a["username"] for a in assignees}
        out.append(
            {
                "project": p,
                "completion": completion,
                "completion_label": "已完成" if completion == "completed" else "未完成",
                "equipment_count": p.project_equipments.count(),
                "task_count": p.library_tasks.count(),
                "assignee_labels": ", ".join(sorted(set(unique_users.values()))) or "—",
                "assignees": assignees,
                "can_manage": library_user_may_assign_on_project(viewer, p)
                or library_user_is_project_primary_responsible(viewer, p),
                "workbench_url": f"?project_id={p.pk}&tab=dispatch",
            }
        )
    return out


def commission_overview_for_viewer(viewer: User, *, subject_user: User | None) -> dict[str, int]:
    pids = list(
        commission_projects_queryset(viewer, subject_user=subject_user).values_list("pk", flat=True)
    )
    return commission_stats_for_projects(pids)
