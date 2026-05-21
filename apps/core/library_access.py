"""角色权限与文件库访问策略（供 views、context_processors 共用）。"""
from typing import Any, Dict, FrozenSet, List, Tuple

from apps.core.models import LibraryFile

# 企业级权限分类（与《检测业务多角色与工作流说明》2.3 对齐）：基础操作 / 数据范围 / 流程与业务
ROLE_PERMISSION_CATEGORY_ORDER: Tuple[str, ...] = ("basic", "data", "workflow")
ROLE_PERMISSION_CATEGORY_META: Dict[str, Dict[str, str]] = {
    "basic": {
        "title": "基础操作权限",
        "subtitle": "后台管理入口、文件库访问与文件类操作（上传、下载、预览、删除等）",
    },
    "data": {
        "title": "数据与范围",
        "subtitle": "控制文件库可见数据范围，落实「项目隔离、岗位隔离」与本人数据限制",
    },
    "workflow": {
        "title": "流程与业务",
        "subtitle": "检测流程管线、模板编辑器、任务分配与业务登记等延伸能力",
    },
}

# 与 Role 模型字段一致；第四元组为分类 slug（见 ROLE_PERMISSION_CATEGORY_META）
ROLE_PERMISSION_MATRIX: List[Tuple[str, str, str, str]] = [
    ("perm_manage_users", "用户管理", "创建、编辑、删除系统用户", "basic"),
    ("perm_manage_roles", "角色管理", "管理角色及下方各项功能开关", "basic"),
    ("perm_manage_menus", "菜单管理", "管理侧边栏动态菜单与角色关联", "basic"),
    ("perm_file_library", "文件库访问", "进入文件库列表与筛选", "basic"),
    (
        "perm_file_upload",
        "文件上传",
        "向待识别文件、数据文件、模板、现场记录等分类上传",
        "basic",
    ),
    (
        "perm_file_upload_attachment",
        "附件上传",
        "仅「附件」分类；可与「文件上传」分开授权（便于向 App 用户单独开放附件）",
        "basic",
    ),
    ("perm_file_download", "文件下载", "下载文件库中的文件", "basic"),
    ("perm_file_preview", "文件预览", "在线预览 PDF、图片、数据与说明类文件等", "basic"),
    ("perm_file_delete", "文件删除", "单条删除与批量删除", "basic"),
    (
        "perm_file_scope_own_only",
        "文件库仅本人数据",
        "开启后仅能查看、预览、下载本人上传或产生的记录",
        "data",
    ),
    ("perm_process_pipeline", "文档识别", "使用独立页面的文档识别与结构化抽取", "workflow"),
    (
        "perm_htmlpdf",
        "模板编辑器",
        "使用模板编辑器进行 PDF 模板版式编辑与试填导出（不含独立文档识别页）",
        "workflow",
    ),
    (
        "perm_assign_tasks",
        "分配文件库任务",
        "新建任务模板与模板绑定、关联项目、向 App 用户下发任务（管理员或模板编辑等）",
        "workflow",
    ),
    (
        "perm_create_library_project",
        "创建检测项目",
        "在项目工作台新建检测项目；与「分配文件库任务」分离，不隐含向他人分配任务",
        "workflow",
    ),
    (
        "perm_biz_registry",
        "业务登记",
        "维护受检单位、设备、联系人及案件、原始记录、报告与文件库联动",
        "workflow",
    ),
]

# 仅通过 UserProfile.perm_overrides 生效、Role 模型无对应字段的补充键（须纳入 role_has 白名单）
USER_PROFILE_ONLY_PERM_OVERRIDE_KEYS: FrozenSet[str] = frozenset(
    {
        "perm_library_task_templates_write",
    }
)

# 可参与「项目任务分配 / 检测 App 侧」的角色（含旧版 app_user）
APP_SIDE_ROLE_CODES: FrozenSet[str] = frozenset(
    {
        "app_user",
        "field_inspector",
        "site_reviewer",
        "report_author",
        "report_auditor",
        "authorized_signatory",
    }
)


_ROLE_KIND_BADGE_CLASSES = {
    "slate": "bg-slate-100 text-slate-800 ring-1 ring-slate-200/80",
    "blue": "bg-blue-100 text-blue-900 ring-1 ring-blue-200/70",
    "violet": "bg-violet-100 text-violet-900 ring-1 ring-violet-200/70",
    "amber": "bg-amber-100 text-amber-950 ring-1 ring-amber-200/70",
}


def role_enterprise_catalog(code: str) -> Dict[str, Any]:
    """列表/表单侧：企业域分类与是否可参与 App 检测任务（APP_SIDE_ROLE_CODES）。"""
    in_app = code in APP_SIDE_ROLE_CODES
    if code in ("super_admin", "admin"):
        kind, style = "系统管理", "slate"
    elif code in (
        "field_inspector",
        "site_reviewer",
        "report_author",
        "report_auditor",
        "authorized_signatory",
    ):
        kind, style = "检测流程岗位", "blue"
    elif code in ("template_editor", "template_tester"):
        kind, style = "模板与测试", "violet"
    elif code == "app_user":
        kind, style = "App 侧（兼容）", "amber"
    else:
        kind, style = "其他", "slate"
    return {
        "kind_label": kind,
        "badge_class": _ROLE_KIND_BADGE_CLASSES[style],
        "in_app_side": in_app,
    }


def role_permission_groups_for_edit(role) -> List[Dict[str, Any]]:
    """编辑角色页：按「基础操作 / 数据范围 / 流程与业务」分组展示权限矩阵。"""
    buckets: Dict[str, List[Dict[str, Any]]] = {c: [] for c in ROLE_PERMISSION_CATEGORY_ORDER}
    for key, title, help_text, cat in ROLE_PERMISSION_MATRIX:
        buckets.setdefault(cat, [])
        buckets[cat].append(
            {
                "field": key,
                "title": title,
                "help": help_text,
                "value": bool(getattr(role, key)),
            }
        )
    groups: List[Dict[str, Any]] = []
    for cat in ROLE_PERMISSION_CATEGORY_ORDER:
        rows = buckets.get(cat) or []
        if not rows:
            continue
        meta = ROLE_PERMISSION_CATEGORY_META[cat]
        groups.append(
            {
                "slug": cat,
                "title": meta["title"],
                "subtitle": meta["subtitle"],
                "rows": rows,
            }
        )
    return groups


def _full_admin_perms() -> Dict[str, bool]:
    p = {key: True for key, _, _, _ in ROLE_PERMISSION_MATRIX}
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
        "perm_htmlpdf": False,
        "perm_assign_tasks": False,
        "perm_create_library_project": True,
        "perm_biz_registry": True,
    },
    "template_tester": {
        "perm_manage_users": False,
        "perm_manage_roles": False,
        "perm_manage_menus": False,
        "perm_file_library": True,
        "perm_file_upload": True,
        "perm_file_upload_attachment": False,
        "perm_file_download": True,
        "perm_file_preview": True,
        "perm_file_delete": False,
        "perm_file_scope_own_only": True,
        "perm_process_pipeline": False,
        "perm_htmlpdf": True,
        "perm_assign_tasks": False,
        "perm_create_library_project": True,
        "perm_biz_registry": False,
    },
    "template_editor": {
        "perm_manage_users": False,
        "perm_manage_roles": False,
        "perm_manage_menus": False,
        "perm_file_library": True,
        "perm_file_upload": True,
        "perm_file_upload_attachment": False,
        "perm_file_download": True,
        "perm_file_preview": True,
        "perm_file_delete": False,
        "perm_file_scope_own_only": False,
        "perm_process_pipeline": False,
        "perm_htmlpdf": True,
        "perm_assign_tasks": True,
        "perm_create_library_project": True,
        "perm_biz_registry": False,
    },
}
# 与 app_user 同级的文件库/登记默认，后续可在「编辑角色」中细调
_app_side = dict(ROLE_DEFAULT_PERMS_BY_CODE["app_user"])
ROLE_DEFAULT_PERMS_BY_CODE["field_inspector"] = dict(_app_side)
ROLE_DEFAULT_PERMS_BY_CODE["site_reviewer"] = dict(_app_side)
ROLE_DEFAULT_PERMS_BY_CODE["report_author"] = dict(_app_side)
ROLE_DEFAULT_PERMS_BY_CODE["report_auditor"] = dict(_app_side)
ROLE_DEFAULT_PERMS_BY_CODE["report_auditor"]["perm_file_scope_own_only"] = False
_te = ROLE_DEFAULT_PERMS_BY_CODE["template_editor"]
_perm_keys = [k for k, _, _, _ in ROLE_PERMISSION_MATRIX]
ROLE_DEFAULT_PERMS_BY_CODE["authorized_signatory"] = {
    k: bool(_app_side.get(k)) or bool(_te.get(k)) for k in _perm_keys
}
ROLE_DEFAULT_PERMS_BY_CODE["authorized_signatory"]["perm_file_delete"] = True
ROLE_DEFAULT_PERMS_BY_CODE["authorized_signatory"]["perm_process_pipeline"] = True
ROLE_DEFAULT_PERMS_BY_CODE["authorized_signatory"]["perm_file_scope_own_only"] = False
ROLE_DEFAULT_PERMS_BY_CODE["authorized_signatory"]["perm_create_library_project"] = True


def _user_role(user):
    if not getattr(user, "is_authenticated", False):
        return None
    try:
        return user.profile.role
    except Exception:
        return None


def _user_perm_overrides(user) -> Dict[str, bool]:
    """用户级权限覆盖：ROLE_PERMISSION_MATRIX 中的键 + USER_PROFILE_ONLY_PERM_OVERRIDE_KEYS。"""
    if not getattr(user, "is_authenticated", False):
        return {}
    try:
        raw = user.profile.perm_overrides
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    valid = {key for key, _, _, _ in ROLE_PERMISSION_MATRIX} | USER_PROFILE_ONLY_PERM_OVERRIDE_KEYS
    out: Dict[str, bool] = {}
    for k, v in raw.items():
        if k not in valid:
            continue
        out[str(k)] = bool(v)
    return out


# 未配置 party_a_demo_restrictions 时，仍按登录名识别演示类账号（默认 seed 为 test；保留 party_a_demo 兼容旧库）
_PARTY_A_DEMO_LEGACY_USERNAMES = frozenset({"test", "party_a_demo"})


def library_user_has_party_a_demo_restrictions(user) -> bool:
    """
    甲方演示类账号：禁止在后台为其他用户分配角色、管理用户/角色模块。

    由 ``ensure_party_a_demo`` 在 ``perm_overrides`` 中写入 ``party_a_demo_restrictions: true``；
    未写入该键时，仍对演示用登录名（默认 ``test``，及旧名 ``party_a_demo``）生效以便兼容。
    """
    if not getattr(user, "is_authenticated", False):
        return False
    try:
        raw = user.profile.perm_overrides
    except Exception:
        raw = {}
    if isinstance(raw, dict) and raw.get("party_a_demo_restrictions") is True:
        return True
    un = (getattr(user, "username", "") or "").strip().lower()
    return un in _PARTY_A_DEMO_LEGACY_USERNAMES


def _role_code(user) -> str:
    role = _user_role(user)
    if role is None:
        return ""
    return getattr(role, "code", "") or ""


def library_user_may_use_htmlpdf_matrix_beta_controls(user) -> bool:
    """
    内测能力：固定表格模板 JSON、前端规则/AI JSON、定位前缀与左右填等。
    仅 Django 超级用户或角色编码 super_admin 可使用（含编辑器工具栏与对应 API）。
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return _role_code(user) == "super_admin"


def role_has_htmlpdf(user) -> bool:
    """是否可使用模板编辑器：独立权限，或与 OCR 处理一并开启（兼容旧角色）。"""
    return role_has(user, "perm_htmlpdf") or role_has(user, "perm_process_pipeline")


def library_user_may_filled_pdf_toolchain(user) -> bool:
    """
    是否可使用「检测数据填 PDF / 报告合并」等与模板填充相关的文件库能力。

    与 ``perm_process_pipeline``（MinerU + Ollama 管线）分离，便于 ``template_tester``
    等仅有模板编辑器/模板侧权限的账号仍能导出现场记录、报告及任务模板试导。
    """
    return role_has(user, "perm_process_pipeline") or role_has(user, "perm_htmlpdf")


def library_user_may_access_task_template_library_nav(user) -> bool:
    """
    是否与 ``library_task_management`` 视图一致的准入条件（侧栏、仪表盘、项目工作台链等）。

    含：分配文件库任务、覆盖项 ``perm_library_task_templates_write``（如甲方演示自建模板）、
    或已被分配检测任务且具备模板填 PDF 链路的参与人。
    不包含 ``perm_file_library``；展示入口的模板仍需自备文件库可见性判断。
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if role_has(user, "perm_assign_tasks") or role_has(user, "perm_library_task_templates_write"):
        return True
    from apps.core.models import LibraryTaskAssignment

    return bool(
        library_user_may_filled_pdf_toolchain(user)
        and LibraryTaskAssignment.objects.filter(assignee=user).exists()
    )


def library_user_may_access_hospital_info_nav(user) -> bool:
    """医院信息管理：需文件库权限且具备委托单位维护能力。"""
    if not getattr(user, "is_authenticated", False):
        return False
    if not role_has(user, "perm_file_library"):
        return False
    return library_user_can_assign_tasks_to_participants(user) or role_has(
        user, "perm_create_library_project"
    )


def role_has(user, perm: str) -> bool:
    """
    是否拥有某权限：
    - Django 超级用户、角色代码 super_admin：固定全开（不受用户级覆盖影响）；
    - 否则若 UserProfile.perm_overrides 含该键，以覆盖值为准；
    - 否则取角色上对应布尔字段。
    - ``perm_library_task_templates_write`` 不在 Role 表字段中，仅见 ``perm_overrides``；
      对甲方演示类账号（见 ``library_user_has_party_a_demo_restrictions``）若未显式写入该键，
      则默认视为 True，与 ``ensure_party_a_demo`` 意图一致并兼容旧数据。
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    role = _user_role(user)
    if role is None:
        return False
    if _role_code(user) == "super_admin":
        return True
    overrides = _user_perm_overrides(user)
    if perm in overrides:
        return overrides[perm]
    if perm == "perm_library_task_templates_write" and library_user_has_party_a_demo_restrictions(user):
        return True
    return bool(getattr(role, perm, False))


def library_user_is_template_editor(user) -> bool:
    return _role_code(user) == "template_editor"


def library_user_can_assign_tasks_to_participants(user) -> bool:
    """
    是否可操作「向检测流程参与人分配项目任务、维护项目流程成员」等（与仅维护任务模板区分）。
    模板编辑角色不可调整他人或其它岗位的任务分配。
    """
    if not role_has(user, "perm_assign_tasks"):
        return False
    if library_user_is_template_editor(user):
        return False
    return True


def library_user_has_task_assignment_on_project(user, project) -> bool:
    """当前用户是否已被分配某项目下的文件库任务（LibraryTaskAssignment，project 非空）。"""
    if user is None or project is None:
        return False
    from apps.core.models import LibraryTaskAssignment

    return LibraryTaskAssignment.objects.filter(assignee=user, project=project).exists()


def library_user_is_project_primary_responsible(user, project) -> bool:
    """是否为指定项目的业务主要负责人（拥有该项目工作台内的完整配置与分配权限）。"""
    if user is None or project is None or not getattr(user, "is_authenticated", False):
        return False
    return getattr(project, "primary_responsible_id", None) == user.id


def library_user_may_assign_on_project(user, project) -> bool:
    """是否可在指定项目内向他人指定主要负责人、同步任务分配等。"""
    if project is None or not getattr(user, "is_authenticated", False):
        return False
    if library_user_can_assign_tasks_to_participants(user):
        return True
    return library_user_is_project_primary_responsible(user, project)


def library_user_may_mutate_project_workbench(user, project) -> bool:
    """
    是否可对「项目工作台」内指定项目进行委托、流程、分配等写操作（不含「文件」标签维护）。
    高权限分配者视为全项目；主要负责人限本项目；参与人须已有任务分配或为项目创建者。
    """
    if project is None or not getattr(user, "is_authenticated", False):
        return False
    if library_user_can_assign_tasks_to_participants(user):
        return True
    if library_user_is_project_primary_responsible(user, project):
        return True
    if library_user_has_task_assignment_on_project(user, project):
        return True
    if role_has(user, "perm_create_library_project") and getattr(project, "created_by_id", None) == user.id:
        return True
    return False


def library_user_may_edit_project_files(user, project) -> bool:
    """是否可在项目工作台「文件」标签中关联/移除项目文件。主要负责人不可编辑项目文件。"""
    if project is None or not getattr(user, "is_authenticated", False):
        return False
    if library_user_is_project_primary_responsible(user, project):
        return False
    if library_user_can_assign_tasks_to_participants(user):
        return True
    if library_user_has_task_assignment_on_project(user, project):
        return True
    if role_has(user, "perm_create_library_project") and getattr(project, "created_by_id", None) == user.id:
        return True
    return False


def library_user_may_browse_shared_library_templates(user) -> bool:
    """
    是否可在文件库「模板」分类中浏览任务级共享模板（含其他用户创建的模板文件）。
    条件：非「仅本人数据」、或系统管理类角色、或具备向参与人分配任务权限、或已在至少一个项目上有任务分配。
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    if _role_code(user) in ("super_admin", "admin"):
        return True
    if not library_scope_own_files_only(user):
        return True
    if library_user_can_assign_tasks_to_participants(user):
        return True
    return bool(library_user_scoped_project_ids(user))


def library_template_granted_via_assigned_projects_tasks(user, lf: LibraryFile) -> bool:
    """受限用户：模板文件已绑定到「本人可访问项目」所挂载的 LibraryTask 时允许访问。"""
    from apps.core.models import LibraryTask

    if lf.category != LibraryFile.CATEGORY_TEMPLATE:
        return False
    pids = library_user_scoped_project_ids(user)
    if not pids:
        return False
    task_ids = LibraryTask.objects.filter(projects__id__in=pids).values_list("id", flat=True).distinct()
    return lf.library_tasks.filter(pk__in=list(task_ids)).exists()


def library_template_granted_via_editable_task(user, lf: LibraryFile) -> bool:
    """受限用户：模板已绑定到本人可维护的 LibraryTask（含本人创建的任务模板）时允许访问。"""
    if lf.category != LibraryFile.CATEGORY_TEMPLATE:
        return False
    for task in lf.library_tasks.all()[:80]:
        if library_user_may_edit_library_task(user, task):
            return True
    return False


def library_user_may_edit_library_task(user, task) -> bool:
    """
    当前用户是否可编辑该 LibraryTask（输出目标、模板绑定、删除等）。
    模板编辑仅可编辑本人创建的任务模板。
    具备「分配文件库任务」时可按角色规则维护；仅有覆盖项 perm_library_task_templates_write
    （如甲方演示）且未开分配权时，仅可维护本人创建的任务模板。
    """
    if task is None:
        return False
    assign = role_has(user, "perm_assign_tasks")
    tmpl_write = role_has(user, "perm_library_task_templates_write")
    if not assign and not tmpl_write:
        return False
    if assign:
        if not library_user_is_template_editor(user):
            return True
        return getattr(task, "created_by_id", None) == user.id
    return getattr(task, "created_by_id", None) == user.id


def library_scope_own_files_only(user) -> bool:
    # 管理员与超级管理员固定可见全量（包含所有用户上传）。
    if _role_code(user) in ("super_admin", "admin"):
        return False
    return role_has(user, "perm_file_scope_own_only")


def library_inspection_act_on_all_projects(user) -> bool:
    """
    默认审核人、授权签字人可对任意活跃项目使用检测任务/提交等 API（不依赖任务分配）。
    若管理员将对应角色的「文件库仅本人数据」重新开启，则恢复为仅已分配项目可访问。
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if library_user_can_assign_tasks_to_participants(user):
        return True
    if _role_code(user) not in ("report_auditor", "authorized_signatory"):
        return False
    return not library_scope_own_files_only(user)


def library_assigned_project_ids(user):
    """当前用户作为接收人的任务分配所涉及的项目 ID（去重）。"""
    from apps.core.models import LibraryTaskAssignment

    return list(
        LibraryTaskAssignment.objects.filter(assignee=user, project_id__isnull=False)
        .values_list("project_id", flat=True)
        .distinct()
    )


def library_user_scoped_project_ids(user) -> list[int]:
    """
    「仅本人数据」下与项目维度相关的可见项目 ID：
    任务分配中的项目，加上（若有权限）本人创建的检测项目。
    """
    from apps.core.models import LibraryProject

    seen: set[int] = set()
    out: list[int] = []
    for pid in library_assigned_project_ids(user):
        if pid not in seen:
            seen.add(pid)
            out.append(pid)
    if role_has(user, "perm_create_library_project"):
        for pid in LibraryProject.objects.filter(created_by=user).values_list("pk", flat=True):
            if pid not in seen:
                seen.add(pid)
                out.append(pid)
    for pid in LibraryProject.objects.filter(primary_responsible=user).values_list("pk", flat=True):
        if pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def library_user_test_account_self_fill(user) -> bool:
    """
    测试沙箱账号：仅本人数据 + 可自建项目 + 不可向他人分配任务（与 ``party_a_demo_restrictions`` 等组合常见）。

    用于放宽部分文件库/导出入口等逻辑；**不再**在页面加载或创建项目时自动写入任务分配，
    以便在「分配」页由本人完成分配并走完整流程。
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False) or _role_code(user) in ("super_admin", "admin"):
        return False
    return (
        library_scope_own_files_only(user)
        and role_has(user, "perm_create_library_project")
        and not library_user_can_assign_tasks_to_participants(user)
    )


def library_user_hide_project_workbench_files_tab(user, project=None) -> bool:
    """
    是否在项目工作台隐藏「项目文件」标签。

    演示/沙箱端不适合在项目维度自由勾选文件库记录；主要负责人亦不在此维护项目文件。
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if library_user_has_party_a_demo_restrictions(user):
        return True
    if library_user_test_account_self_fill(user):
        return True
    if project is not None and library_user_is_project_primary_responsible(user, project):
        if not library_user_can_assign_tasks_to_participants(user):
            return True
    return False


def library_signatory_assigned_project_selected(user, project_selected_id: int | None) -> bool:
    """
    授权签字人当前是否筛选在「本人被分配到的项目」下。
    用于在「仅本人数据」模式下，于该项目内开放与管理员相当的文件操作入口（批量删除、合并报告等）。
    """
    if project_selected_id is None:
        return False
    if _role_code(user) != "authorized_signatory":
        return False
    return project_selected_id in set(library_assigned_project_ids(user))


def library_export_merge_allowed_under_own_files_scope(user, project_selected_id: int | None) -> bool:
    """
    「仅本人数据」下是否允许文件库中的合并报告 / 手动导出 PDF 等入口。

    除「授权签字人已选中被分配项目」外，对甲方演示等沙箱账号（仅本人 + 自建项目 + 不可分配他人任务）
    在存在可见项目时亦允许，避免必须先在文件库顶栏选项目才能导出。
    """
    if not library_scope_own_files_only(user):
        return True
    if library_signatory_assigned_project_selected(user, project_selected_id):
        return True
    if library_user_test_account_self_fill(user) and library_user_scoped_project_ids(user):
        return True
    return False


def library_user_may_access_assigned_library_task(user, task) -> bool:
    """
    无「分配文件库任务」权限时，是否仍可打开某任务模板页（查看绑定、试导 PDF）。

    已被分配到该任务（任意项目）的参与人允许，避免 test / party_a_demo 等演示账号被完全挡在任务模板库外。
    """
    if task is None:
        return False
    if library_user_may_edit_library_task(user, task):
        return True
    from apps.core.models import LibraryTaskAssignment

    return LibraryTaskAssignment.objects.filter(assignee=user, library_task=task).exists()


def library_user_may_export_task_template_pdf_for_project(user, task, project) -> bool:
    """从任务模板试导现场记录/报告 PDF：全量维护权限，或在本项目有该任务分配且可写工作台。"""
    if task is None or project is None:
        return False
    if library_user_may_edit_library_task(user, task):
        return True
    if not library_user_may_mutate_project_workbench(user, project):
        return False
    from apps.core.models import LibraryTaskAssignment

    return LibraryTaskAssignment.objects.filter(assignee=user, library_task=task, project=project).exists()


def library_signatory_unrestricted_template_picker(user) -> bool:
    """授权签字人在任务模板中绑定模板文件时，可浏览全库模板（不限本人上传）。"""
    return _role_code(user) == "authorized_signatory"


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
    pids = library_user_scoped_project_ids(user)
    if not pids:
        return False
    return lf.projects.filter(pk__in=pids).exists()


def library_file_access_allowed(user, lf: LibraryFile) -> bool:
    """在拥有文件库访问权前提下，校验是否可访问单条记录（含本人范围）。"""
    if not role_has(user, "perm_file_library"):
        return False
    if not library_scope_own_files_only(user):
        return True
    if lf.category == LibraryFile.CATEGORY_TEMPLATE:
        if library_template_granted_via_editable_task(user, lf):
            return True
        if library_user_may_browse_shared_library_templates(user):
            if library_user_can_assign_tasks_to_participants(user):
                return True
            if library_template_granted_via_assigned_projects_tasks(user, lf):
                return True
    if library_project_file_granted_via_task_assignment(user, lf):
        return True
    if lf.created_by_id is None:
        return False
    return lf.created_by_id == user.id


def library_user_can_delete_any_library_file(user) -> bool:
    """
    是否可按「已可见」范围删除任意上传者创建的文件（仍须单独通过 library_file_access_allowed）。

    用于系统管理员类角色。部门 / 上下级删除他人文件等扩展策略可在此函数或
    library_user_may_delete_library_file 中追加分支（当前未建模组织层级）。
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return _role_code(user) in ("super_admin", "admin")


def library_user_may_delete_library_file(user, lf: LibraryFile) -> bool:
    """
    移入回收站、回收站内恢复或彻底删除等写操作；规则严于「仅可见」。

    - 须先满足 library_file_access_allowed（与列表/预览一致）。
    - 默认仅允许删除本人上传的记录（created_by 为当前用户）。
    - Django 超级用户或角色 super_admin / admin：可删除在可见范围内的任意文件。
    - 无上传者（created_by 为空）的记录：仅上述管理员类账号可删，避免普通用户误删系统数据。
    """
    if lf is None:
        return False
    if not library_file_access_allowed(user, lf):
        return False
    if library_user_can_delete_any_library_file(user):
        return True
    if lf.created_by_id is None:
        return False
    return lf.created_by_id == user.id


def role_permission_map(user) -> Dict[str, bool]:
    """供模板使用的权限字典。"""
    return {key: role_has(user, key) for key, _, _, _ in ROLE_PERMISSION_MATRIX}


def role_ui_context(user) -> Dict[str, Any]:
    """
    按角色精简后台侧栏/仪表盘，减少与岗位职责无关的入口。
    模板中使用 ui_* 键（由 context_processors 扁平注入）。
    """
    empty: Dict[str, Any] = {
        "ui_sidebar_show_project_management": False,
        "ui_sidebar_show_commission_manage": False,
        "ui_dashboard_hide_json_tile": False,
        "ui_dashboard_site_record_focus": False,
        "ui_dashboard_hide_file_stats_row": False,
        "ui_dashboard_show_django_admin_tile": False,
    }
    if not getattr(user, "is_authenticated", False):
        return empty
    code = _role_code(user)
    is_super = bool(getattr(user, "is_superuser", False)) or code == "super_admin"
    is_admin = code == "admin"
    has_assign = library_user_can_assign_tasks_to_participants(user)

    # 侧栏「项目管理」：现场检测两岗只做上传/校核，不进入项目配置页
    if is_super or is_admin or has_assign:
        show_pm = role_has(user, "perm_file_library")
    elif code in ("field_inspector", "site_reviewer"):
        show_pm = False
    else:
        show_pm = role_has(user, "perm_file_library")

    # 仪表盘首行文件统计：现场两岗只强调现场记录，隐藏 JSON 统计块
    site_focus = code in ("field_inspector", "site_reviewer")
    hide_json = site_focus
    hide_file_row = not role_has(user, "perm_file_library")

    # Django Admin 入口：仅保留给系统管理类角色，避免检测岗误点
    show_admin_tile = is_super or is_admin or role_has(user, "perm_manage_users")

    from apps.core.commission_management_service import library_user_may_access_commission_manage

    return {
        "ui_sidebar_show_project_management": bool(show_pm),
        "ui_sidebar_show_commission_manage": bool(
            show_pm and library_user_may_access_commission_manage(user)
        ),
        "ui_dashboard_hide_json_tile": bool(hide_json),
        "ui_dashboard_site_record_focus": bool(site_focus),
        "ui_dashboard_hide_file_stats_row": bool(hide_file_row),
        "ui_dashboard_show_django_admin_tile": bool(show_admin_tile),
    }


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


# 文件库默认配额：5 GiB（与 UserProfile.file_library_quota_bytes 默认值一致）
FILE_LIBRARY_DEFAULT_QUOTA_BYTES = 5368709120


def library_user_file_library_quota_bytes(user) -> int | None:
    """
    当前用户文件库配额（字节）。返回 None 表示不限制（超级用户或超级管理员/普通管理员角色）。
    """
    if not getattr(user, "is_authenticated", False):
        return None
    if getattr(user, "is_superuser", False):
        return None
    code = _role_code(user)
    if code in ("super_admin", "admin"):
        return None
    try:
        v = int(user.profile.file_library_quota_bytes)
        return max(v, 0)
    except Exception:
        return FILE_LIBRARY_DEFAULT_QUOTA_BYTES


def library_user_file_library_usage_bytes(user) -> int:
    """当前用户已占用的文件库字节数（不含回收站中已软删记录）。"""
    from django.db.models import Sum

    if not getattr(user, "is_authenticated", False):
        return 0
    v = LibraryFile.objects.filter(created_by=user).aggregate(s=Sum("size"))["s"]
    return int(v or 0)


def library_user_file_library_upload_exceeds_quota(user, additional_bytes: int) -> Tuple[bool, int, int | None]:
    """
    若再增加 additional_bytes 是否超过配额。
    返回 (是否超限, 当前已用字节, 配额上限或 None 表示不限)。
    """
    cap = library_user_file_library_quota_bytes(user)
    used = library_user_file_library_usage_bytes(user)
    if cap is None:
        return False, used, None
    add = max(int(additional_bytes), 0)
    return (used + add > cap), used, cap
