"""可选工具：将某项目上的任务分配与流程岗位同步为指定用户（管理命令或运维脚本使用）。

项目工作台与演示账号**不再**在页面加载时自动调用，以便 test 等账号在界面内自行完成「分配」体验全流程。
"""
from __future__ import annotations

from django.db import transaction

from apps.core.library_access import library_user_scoped_project_ids
from apps.core.library_file_service import attach_files_to_projects
from apps.core.models import (
    LibraryFile,
    LibraryProject,
    LibraryProjectWorkflowMember,
    LibraryTaskAssignment,
)


@transaction.atomic
def sync_project_for_solo_tester(project: LibraryProject, user) -> None:
    """
    对单项目：为每个已关联的 LibraryTask 写入本人分配；五个流程岗位全部指向本人；
    并同步模板文件到项目（与手动分配逻辑一致）。
    """
    if project is None or user is None:
        return
    all_file_ids: set[int] = set()
    for lt in project.library_tasks.all():
        _, _ = LibraryTaskAssignment.objects.get_or_create(
            library_task=lt,
            project=project,
            assignee=user,
            defaults={"assigned_by": user},
        )
        all_file_ids.update(
            lt.library_files.filter(category=LibraryFile.CATEGORY_TEMPLATE).values_list(
                "id", flat=True
            )
        )
    if all_file_ids:
        attach_files_to_projects(sorted(all_file_ids), [project.pk], user)

    if project.primary_responsible_id != user.id:
        project.primary_responsible = user
        project.save(update_fields=["primary_responsible", "updated_at"])

    for role_code, _label in LibraryProjectWorkflowMember.WORKFLOW_ROLE_CHOICES:
        LibraryProjectWorkflowMember.objects.get_or_create(
            project=project,
            user=user,
            workflow_role=role_code,
        )


def sync_all_scoped_projects_for_user(user) -> None:
    """对当前用户可见的全部项目执行 sync_project_for_solo_tester（幂等）。"""
    if user is None or not getattr(user, "is_authenticated", False):
        return
    for pid in library_user_scoped_project_ids(user):
        p = LibraryProject.objects.filter(pk=pid).first()
        if p is not None:
            sync_project_for_solo_tester(p, user)
