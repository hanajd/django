"""角色权限与文件库访问策略（供 views、context_processors 共用）。"""
from typing import Dict, List, Tuple

from apps.core.models import LibraryFile

# 与 Role 模型字段一致，顺序用于「编辑角色」表单展示
ROLE_PERMISSION_MATRIX: List[Tuple[str, str, str]] = [
    ("perm_manage_users", "用户管理", "创建、编辑、删除系统用户"),
    ("perm_manage_roles", "角色管理", "管理角色及下方各项功能开关"),
    ("perm_manage_menus", "菜单管理", "管理侧边栏动态菜单与角色关联"),
    ("perm_file_library", "文件库访问", "进入文件库列表与筛选"),
    (
        "perm_file_upload",
        "文件上传",
        "向 OCR / JSON / 模板 / 现场记录 分类上传",
    ),
    (
        "perm_file_upload_attachment",
        "附件上传",
        "仅「附件」分类；可与「文件上传」分开授权（便于向 App 用户单独开放附件）",
    ),
    ("perm_file_download", "文件下载", "下载文件库中的文件"),
    ("perm_file_preview", "文件预览", "在线预览 PDF / 图片 / JSON / Markdown 等"),
    ("perm_file_delete", "文件删除", "单条删除与批量删除"),
    (
        "perm_file_scope_own_only",
        "文件库仅本人数据",
        "开启后仅能查看、预览、下载本人上传或产生的记录",
    ),
    ("perm_process_pipeline", "流程处理", "使用 MinerU + Ollama 集成管线"),
    (
        "perm_assign_tasks",
        "分配文件库任务",
        "向 App 用户下发任务并指定模板文件（超级管理员 / 管理员使用）",
    ),
    (
        "perm_biz_registry",
        "业务登记",
        "维护受检单位、设备、联系人及案件、原始记录、报告与文件库联动",
    ),
]

def _full_admin_perms() -> Dict[str, bool]:
    p = {key: True for key, _, _ in ROLE_PERMISSION_MATRIX}
    p["perm_file_scope_own_only"] = False
    return p


ROLE_DEFAULT_PERMS_BY_CODE: Dict[str, Dict[str, bool]] = {
    "super_admin": _full_admin_perms(),
    "admin": _full_admin_perms(),
    "app_user": {
        "perm_manage_users": False,
        "perm_manage_roles": False,
        "perm_manage_menus": False,
        "perm_file_library": True,
        "perm_file_upload": True,
        "perm_file_upload_attachment": True,
        "perm_file_download": True,
        "perm_file_preview": True,
        "perm_file_delete": False,
        "perm_file_scope_own_only": True,
        "perm_process_pipeline": False,
        "perm_assign_tasks": False,
        "perm_biz_registry": True,
    },
}


def _user_role(user):
    if not getattr(user, "is_authenticated", False):
        return None
    try:
        return user.profile.role
    except Exception:
        return None


def _role_code(user) -> str:
    role = _user_role(user)
    if role is None:
        return ""
    return getattr(role, "code", "") or ""


def role_has(user, perm: str) -> bool:
    """是否拥有某权限；Django 超级用户与角色代码 super_admin 视为全开。"""
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    role = _user_role(user)
    if role is None:
        return False
    if _role_code(user) == "super_admin":
        return True
    return bool(getattr(role, perm, False))


def library_scope_own_files_only(user) -> bool:
    # 管理员与超级管理员固定可见全量（包含所有用户上传）。
    if _role_code(user) in ("super_admin", "admin"):
        return False
    return role_has(user, "perm_file_scope_own_only")


def library_assigned_project_ids(user):
    """当前用户作为接收人的任务分配所涉及的项目 ID（去重）。"""
    from apps.core.models import LibraryTaskAssignment

    return list(
        LibraryTaskAssignment.objects.filter(assignee=user, project_id__isnull=False)
        .values_list("project_id", flat=True)
        .distinct()
    )


def library_user_revoked_project_ids(user):
    """当前用户被管理员取消「项目编辑权」的项目 ID。"""
    from apps.core.models import LibraryProjectUserRevocation

    return list(
        LibraryProjectUserRevocation.objects.filter(user=user).values_list(
            "project_id", flat=True
        )
    )


def library_file_write_blocked_by_project_revocation(user, lf: LibraryFile) -> bool:
    """
    在「仅本人数据」模式下，若文件关联到用户已被取消编辑权的项目，则禁止删除/上传关联等改动（下载与预览仍走 library_file_access_allowed）。
    """
    if not library_scope_own_files_only(user):
        return False
    rev = set(library_user_revoked_project_ids(user))
    if not rev:
        return False
    return lf.projects.filter(pk__in=rev).exists()


def library_upload_blocked_revoked_projects(user, project_ids: list) -> str:
    """受限用户若向已取消编辑权的项目上传或关联文件，返回错误说明；否则返回空串。"""
    if not library_scope_own_files_only(user) or not project_ids:
        return ""
    rev = set(library_user_revoked_project_ids(user))
    if rev.intersection(set(project_ids)):
        return "您已被取消在部分所选项目上的编辑权限，无法向这些项目上传或关联新文件（仍可下载与预览）。"
    return ""


def library_project_file_granted_via_task_assignment(user, lf: LibraryFile) -> bool:
    """仅本人数据模式下：文件已关联到用户被分配过的任一项目时，可访问（前端按项目交互，不按任务）。"""
    pids = library_assigned_project_ids(user)
    if not pids:
        return False
    return lf.projects.filter(pk__in=pids).exists()


def library_file_access_allowed(user, lf: LibraryFile) -> bool:
    """在拥有文件库访问权前提下，校验是否可访问单条记录（含本人范围）。"""
    if not role_has(user, "perm_file_library"):
        return False
    if not library_scope_own_files_only(user):
        return True
    if library_project_file_granted_via_task_assignment(user, lf):
        return True
    if lf.created_by_id is None:
        return False
    return lf.created_by_id == user.id


def role_permission_map(user) -> Dict[str, bool]:
    """供模板使用的权限字典。"""
    return {key: role_has(user, key) for key, _, _ in ROLE_PERMISSION_MATRIX}


def role_can_upload_library_category(user, category: str) -> bool:
    """按文件库分类检查上传权限（附件与其它分类分离）。"""
    if category == LibraryFile.CATEGORY_ATTACHMENT:
        return role_has(user, "perm_file_upload_attachment")
    if category in (
        LibraryFile.CATEGORY_UPLOAD,
        LibraryFile.CATEGORY_JSON,
        LibraryFile.CATEGORY_TEMPLATE,
        LibraryFile.CATEGORY_SITE_RECORD,
        LibraryFile.CATEGORY_REPORT,
    ):
        return role_has(user, "perm_file_upload")
    return False
