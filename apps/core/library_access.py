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
    (
        "perm_f1_eval",
        "评价报告表",
        "使用侧栏「评价报告表」制作预评价报告（独立工作区，不写业务库）",
        "workflow",
    ),
]

# 仅通过 UserProfile.perm_overrides 生效、Role 模型无对应字段的补充键（须纳入 role_has 白名单）
USER_PROFILE_ONLY_PERM_OVERRIDE_KEYS: FrozenSet[str] = frozenset(
    {
        "perm_library_task_templates_write",
        "perm_mock_inspection_submit",
    }
)

# 用户编辑页「权限个性化」中展示、但 Role 表无对应列的补充项
PERM_OVERRIDE_EXTRA_META: Dict[str, Tuple[str, str]] = {
    "perm_mock_inspection_submit": (
        "现场记录模拟提交",
        "在项目工作台委托立项设备行使用「模拟提交」：生成模拟数据并按 App 流程写入 submit 库、自动回填现场记录 PDF",
    ),
}

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

COMMISSION_COORDINATOR_ROLE_CODE = "commission_coordinator"

# 委托统筹账号在文件库中可见的分类（不含数据文件、模板）
COMMISSION_COORDINATOR_FILE_LIBRARY_TAB_KEYS: FrozenSet[str] = frozenset(
    {"ocr", "site_record", "report", "attachment", "inspection_submit", "trash"}
)

FILE_LIBRARY_TAB_DEFS: Tuple[Dict[str, str], ...] = (
    {"key": "ocr", "label": "待识别文件"},
    {"key": "json", "label": "数据文件"},
    {"key": "template", "label": "模板"},
    {"key": "site_record", "label": "现场记录"},
    {"key": "report", "label": "报告"},
    {"key": "attachment", "label": "附件"},
    {"key": "inspection_submit", "label": "检测提交"},
    {"key": "trash", "label": "回收站"},
)

# 委托统筹可创建用户的检测流程岗位（与项目人员派工五级岗位一致）
COMMISSION_COORDINATOR_WORKFLOW_ROLE_CODES: FrozenSet[str] = frozenset(
    {
        "field_inspector",
        "site_reviewer",
        "report_author",
        "report_auditor",
        "authorized_signatory",
    }
)

COMMISSION_COORDINATOR_ASSIGNABLE_ROLE_CODES: FrozenSet[str] = COMMISSION_COORDINATOR_WORKFLOW_ROLE_CODES

# 委托统筹创建的现场两岗：可查看已分配项目、仅导出现场记录，不可编辑工作台
COORDINATOR_WORKFLOW_SITE_ROLE_CODES: FrozenSet[str] = frozenset(
    {"field_inspector", "site_reviewer"}
)
# 委托统筹创建的编制/审核/签发岗：可编辑已分配项目、可导出报告与合并报告
COORDINATOR_WORKFLOW_REPORT_EDIT_ROLE_CODES: FrozenSet[str] = frozenset(
    {"report_author", "report_auditor", "authorized_signatory"}
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
    elif code == COMMISSION_COORDINATOR_ROLE_CODE:
        kind, style = "委托统筹", "amber"
    elif code in (
        "admin_office",
        "dept_director_inspection",
        "dept_staff_inspection",
        "dept_director_evaluation",
        "dept_staff_evaluation",
    ):
        kind, style = "业务线组织", "violet"
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
        "perm_f1_eval": False,
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
        "perm_f1_eval": True,
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
        "perm_f1_eval": False,
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

ROLE_DEFAULT_PERMS_BY_CODE[COMMISSION_COORDINATOR_ROLE_CODE] = {
    "perm_manage_users": True,
    "perm_manage_roles": False,
    "perm_manage_menus": False,
    "perm_file_library": True,
    "perm_file_upload": True,
    "perm_file_upload_attachment": True,
    "perm_file_download": True,
    "perm_file_preview": True,
    "perm_file_delete": True,
    "perm_file_scope_own_only": False,
    "perm_process_pipeline": False,
    "perm_htmlpdf": False,
    "perm_assign_tasks": True,
    "perm_create_library_project": True,
    "perm_biz_registry": True,
    "perm_f1_eval": False,
}

# 新业务线组织角色默认权限（与 org_roles.ORG_ROLE_SEED 对齐；迁移/ensure 时写入 Role 表）
try:
    from apps.core.org_roles import ORG_ROLE_SEED as _ORG_ROLE_SEED

    for _code, _meta in _ORG_ROLE_SEED.items():
        ROLE_DEFAULT_PERMS_BY_CODE[_code] = dict(_meta["perms"])
except Exception:
    pass


def library_user_is_commission_coordinator(user) -> bool:
    return _role_code(user) == COMMISSION_COORDINATOR_ROLE_CODE


def library_user_is_coordinator_managed_workflow_user(user) -> bool:
    """委托统筹创建的检测流程岗位账号（检测员、校核员、编制人、审核人、授权签字人）。"""
    if not getattr(user, "is_authenticated", False):
        return False
    if library_user_is_commission_coordinator(user):
        return False
    try:
        profile = user.profile
        if not profile.created_by_id:
            return False
        creator = profile.created_by
        if not library_user_is_commission_coordinator(creator):
            return False
        code = profile.role.code if profile.role_id else ""
        return code in COMMISSION_COORDINATOR_ASSIGNABLE_ROLE_CODES
    except Exception:
        return False


def library_user_coordinator_workflow_role_code(user) -> str:
    """若为由委托统筹创建的流程岗位，返回其角色 code，否则返回空字符串。"""
    if not library_user_is_coordinator_managed_workflow_user(user):
        return ""
    try:
        return (user.profile.role.code if user.profile.role_id else "") or ""
    except Exception:
        return ""


def library_user_is_coordinator_workflow_site_only_role(user) -> bool:
    """委托统筹创建的检测员 / 校核员（只读工作台 + 仅导出现场记录）。"""
    return library_user_coordinator_workflow_role_code(user) in COORDINATOR_WORKFLOW_SITE_ROLE_CODES


def library_user_is_coordinator_workflow_report_edit_role(user) -> bool:
    """委托统筹创建的编制人 / 审核人 / 授权签字人（可编辑工作台 + 导出/合并报告）。"""
    return (
        library_user_coordinator_workflow_role_code(user)
        in COORDINATOR_WORKFLOW_REPORT_EDIT_ROLE_CODES
    )


def library_user_may_view_coordinator_usage_guide(user) -> bool:
    """是否可查看「委托业务使用说明」（委托统筹本人或其创建的流程岗位账号）。"""
    return library_user_is_commission_coordinator(
        user
    ) or library_user_is_coordinator_managed_workflow_user(user)


def file_library_tabs_for_user(user) -> list[dict]:
    """按角色返回文件库侧栏可见分类。"""
    if library_user_is_commission_coordinator(user):
        keys = COMMISSION_COORDINATOR_FILE_LIBRARY_TAB_KEYS
        return [dict(t) for t in FILE_LIBRARY_TAB_DEFS if t["key"] in keys]
    return [dict(t) for t in FILE_LIBRARY_TAB_DEFS]


def file_library_tab_allowed_for_user(user, tab: str) -> bool:
    normalized = (tab or "ocr").strip()
    if normalized == "upload":
        normalized = "ocr"
    allowed = {t["key"] for t in file_library_tabs_for_user(user)}
    return normalized in allowed


def roles_assignable_by_user(actor) -> list:
    """用户管理页：当前操作者可分配的角色主键列表（空表示不限制）。"""
    from apps.core.models import Role

    if not getattr(actor, "is_authenticated", False):
        return []
    if getattr(actor, "is_superuser", False) or _role_code(actor) in ("super_admin", "admin"):
        return list(Role.objects.order_by("name", "id"))
    # 旧轨委托统筹：行为不变
    if library_user_is_commission_coordinator(actor):
        from apps.core.project_workflow_ui import ROLE_CODE_ORDER

        roles_by_code = {
            r.code: r
            for r in Role.objects.filter(code__in=COMMISSION_COORDINATOR_WORKFLOW_ROLE_CODES)
        }
        return [roles_by_code[c] for c in ROLE_CODE_ORDER if c in roles_by_code]
    # 仅新轨部门主任收窄可分配角色
    try:
        from apps.core.org_roles import (
            staff_role_code_for_director,
            user_is_dept_director,
            user_is_legacy_permission_track,
        )

        if not user_is_legacy_permission_track(actor) and user_is_dept_director(actor):
            staff_code = staff_role_code_for_director(actor)
            if staff_code:
                return list(Role.objects.filter(code=staff_code).order_by("name", "id"))
            return []
    except Exception:
        pass
    if role_has(actor, "perm_manage_users"):
        return list(Role.objects.order_by("name", "id"))
    return []


def library_user_may_manage_target_user(actor, target) -> bool:
    """委托统筹仅可维护本人创建的检测流程岗位账号；新轨主任可管本部门员工。"""
    if actor is None or target is None:
        return False
    if getattr(actor, "is_superuser", False) or _role_code(actor) in ("super_admin", "admin"):
        return True
    if not role_has(actor, "perm_manage_users"):
        return False
    # 新轨主任专用规则；旧轨不进入此分支
    try:
        from apps.core.org_roles import (
            DEPT_STAFF_ROLE_CODES,
            staff_role_code_for_director,
            user_is_dept_director,
            user_is_legacy_permission_track,
            user_org_unit,
        )

        if not user_is_legacy_permission_track(actor) and user_is_dept_director(actor):
            if getattr(target, "is_superuser", False):
                return False
            try:
                profile = target.profile
                code = profile.role.code if profile.role_id else ""
            except Exception:
                return False
            if code not in DEPT_STAFF_ROLE_CODES:
                return False
            if code != staff_role_code_for_director(actor):
                return False
            return user_org_unit(actor) == user_org_unit(target)
    except Exception:
        pass
    # —— 以下与改前委托统筹 / 普通管理员逻辑一致 ——
    if not library_user_is_commission_coordinator(actor):
        return True
    if getattr(target, "is_superuser", False):
        return False
    try:
        profile = target.profile
        code = profile.role.code if profile.role_id else ""
        if profile.created_by_id != actor.pk:
            return False
    except Exception:
        return False
    if code in ("super_admin", "admin", "template_editor", "template_tester", COMMISSION_COORDINATOR_ROLE_CODE):
        return False
    if code and code not in COMMISSION_COORDINATOR_ASSIGNABLE_ROLE_CODES:
        return False
    return True


def library_coordinator_managed_users_queryset(actor):
    """用户列表：委托统筹仅本人创建的流程岗；新轨主任为本部门员工；其余旧轨不变。"""
    from django.contrib.auth.models import User

    qs = User.objects.select_related("profile", "profile__role").order_by("username", "id")
    try:
        from apps.core.org_roles import (
            org_staff_queryset_for_director,
            user_is_dept_director,
            user_is_legacy_permission_track,
        )

        if not user_is_legacy_permission_track(actor) and user_is_dept_director(actor):
            return org_staff_queryset_for_director(actor)
    except Exception:
        pass
    if not library_user_is_commission_coordinator(actor):
        return qs
    return qs.filter(
        profile__created_by=actor,
        profile__role__code__in=COMMISSION_COORDINATOR_ASSIGNABLE_ROLE_CODES,
    )


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

def library_user_is_test_peer_username(username: str) -> bool:
    """演示沙箱同组账号：test、party_a_demo、test-1 … test-99。"""
    un = (username or "").strip().lower()
    if un in _PARTY_A_DEMO_LEGACY_USERNAMES:
        return True
    if not un.startswith("test-"):
        return False
    suffix = un[5:]
    return suffix.isdigit() and 1 <= int(suffix) <= 99


def library_user_is_test_peer(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return library_user_is_test_peer_username(getattr(user, "username", "") or "")


def library_test_peer_user_ids() -> frozenset[int]:
    """同组 test 账号主键（用于互相维护任务模板与模板文件）。"""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    ids: list[int] = []
    for row in User.objects.filter(is_active=True).only("id", "username"):
        if library_user_is_test_peer_username(row.username):
            ids.append(int(row.pk))
    return frozenset(ids)


def library_user_may_access_instrument_database(user) -> bool:
    """
    检测仪器台账（Web 页查看）：与 may_edit 相同（本模块无只读访客档）。
    注：REST ``GET /registry/instruments/`` 仍为只读，增删改请走 Web 台账页。
    """
    return library_user_may_edit_instrument_database(user)


def library_user_may_edit_instrument_database(user) -> bool:
    """
    检测仪器台账资料维护（新增/改编号·名称·证书/有效期/备注等；与出库入库无关）。
    已出库仪器也可改资料，不改变出库状态。
    """
    if not getattr(user, "is_authenticated", False):
        return False
    try:
        from apps.core.org_roles import user_is_evaluation_org

        if user_is_evaluation_org(user):
            return False
    except Exception:
        pass
    if role_has(user, "perm_manage_users"):
        return True
    if library_user_is_test_peer(user):
        return True
    if role_has(user, "perm_biz_registry"):
        return True
    # 项目负责人/派工岗需更正证书、补录登记错误
    if role_has(user, "perm_assign_tasks"):
        return True
    if library_user_is_template_editor(user) and role_has(
        user, "perm_library_task_templates_write"
    ):
        return True
    return False


def library_user_may_access_project_workbench(user) -> bool:
    """项目工作台入口（检测部为检测委托；评价部为评价报告表/书看板）。"""
    if not getattr(user, "is_authenticated", False):
        return False
    return role_has(user, "perm_file_library")


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
    return library_user_is_test_peer_username(un)


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


def library_user_may_mock_inspection_submit(user) -> bool:
    """
    项目工作台「现场记录模拟提交 JSON」：默认仅 Django 超级用户或 super_admin 角色；
    其他账号须由超管在用户编辑页通过 perm_overrides 显式授权。
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    if _role_code(user) == "super_admin":
        return True
    return role_has(user, "perm_mock_inspection_submit")


def role_has_htmlpdf(user) -> bool:
    """是否可使用模板编辑器：独立权限，或与 OCR 处理一并开启（兼容旧角色）。"""
    return role_has(user, "perm_htmlpdf") or role_has(user, "perm_process_pipeline")


def library_user_may_access_f1_eval(user) -> bool:
    """
    评价报告表（F.1）：不写业务库表，仅文件工作区。

    - 角色权限 ``perm_f1_eval``（评价部主任/员工默认开启）；
    - test / party_a_demo / test-N 同组演示账号可用；
    - template_tester 可用；
    - 系统超管 / super_admin / admin 可用（运维）。
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    code = _role_code(user)
    if code in ("super_admin", "admin"):
        return True
    if role_has(user, "perm_f1_eval"):
        return True
    if library_user_is_test_peer(user):
        return True
    if code == "template_tester":
        return True
    # 兼容未跑种子前的评价部角色
    if code in ("dept_director_evaluation", "dept_staff_evaluation"):
        return True
    return False


def library_user_may_manage_f1_eval_projects(user) -> bool:
    """新建 / 删除评价报告表项目：test 组、评价部主任、超管；评价部员工不可。"""
    if not library_user_may_access_f1_eval(user):
        return False
    if getattr(user, "is_superuser", False) or _role_code(user) in ("super_admin", "admin"):
        return True
    if library_user_is_test_peer(user):
        return True
    if _role_code(user) == "dept_director_evaluation":
        return True
    if _role_code(user) == "template_tester":
        return True
    return False


def library_user_may_assign_f1_eval_projects(user) -> bool:
    """项目指派：test 组与评价部主任（及超管）。"""
    if not library_user_may_access_f1_eval(user):
        return False
    if getattr(user, "is_superuser", False) or _role_code(user) in ("super_admin", "admin"):
        return True
    if library_user_is_test_peer(user):
        return True
    if _role_code(user) == "dept_director_evaluation":
        return True
    return False


def library_user_is_f1_eval_dept_staff(user) -> bool:
    return _role_code(user) == "dept_staff_evaluation"


def f1_eval_assignable_users_for(actor):
    """
    可被指派的用户：仅评价部**员工**。
    评价部主任 / test / 超管本身已有项目管理权，无需也不应出现在指派列表。
    """
    from django.contrib.auth.models import User

    if not library_user_may_assign_f1_eval_projects(actor):
        return User.objects.none()
    return (
        User.objects.filter(
            is_active=True,
            profile__role__code="dept_staff_evaluation",
        )
        .select_related("profile", "profile__role")
        .order_by("username", "id")
        .distinct()
    )


def library_user_may_filled_pdf_toolchain(user) -> bool:
    """
    是否可使用「检测数据填 PDF / 报告合并」等与模板填充相关的文件库能力。

    与 ``perm_process_pipeline``（MinerU + Ollama 管线）分离，便于 ``template_tester``
    等仅有模板编辑器/模板侧权限的账号仍能导出现场记录、报告及任务模板试导。
    """
    return role_has(user, "perm_process_pipeline") or role_has(user, "perm_htmlpdf")


def library_user_may_export_site_record_library_pdfs(user) -> bool:
    """
    文件库：手动导出现场记录 PDF（检测提交 → 现场记录）。

    委托统筹创建的检测员 / 校核员仅具备此项导出能力。
    """
    if library_user_may_filled_pdf_toolchain(user):
        return True
    if library_user_is_commission_coordinator(user) and role_has(user, "perm_file_library"):
        return True
    if library_user_is_coordinator_managed_workflow_user(user) and role_has(
        user, "perm_file_library"
    ):
        return True
    return False


def library_user_may_export_report_library_pdfs(user) -> bool:
    """
    文件库：由现场记录/提交导出报告、文件夹导出报告、合并报告 PDF 等。

    委托统筹创建的编制人 / 审核人 / 授权签字人，以及委托统筹本人可用。
    """
    if library_user_may_filled_pdf_toolchain(user):
        return True
    if library_user_is_commission_coordinator(user) and role_has(user, "perm_file_library"):
        return True
    if library_user_is_coordinator_workflow_report_edit_role(user) and role_has(
        user, "perm_file_library"
    ):
        return True
    return False


def library_user_may_export_inspection_library_pdfs(user) -> bool:
    """文件库：任一类检测相关 PDF 导出（现场记录或报告）。"""
    return library_user_may_export_site_record_library_pdfs(
        user
    ) or library_user_may_export_report_library_pdfs(user)


_LIBRARY_EXPORT_SELECTABLE_CATEGORIES = frozenset(
    {
        LibraryFile.CATEGORY_INSPECTION_SUBMIT,
        LibraryFile.CATEGORY_SITE_RECORD,
        LibraryFile.CATEGORY_REPORT,
    }
)


def library_user_may_select_library_file_for_batch(user, lf) -> bool:
    """
    文件库表格复选框：导出类操作可选中可见的检测提交/现场记录/报告；
    删除类操作仍仅本人上传（或管理员删任意）。
    """
    if lf is None:
        return False
    if library_user_may_delete_library_file(user, lf):
        return True
    if not library_file_access_allowed(user, lf):
        return False
    cat = (getattr(lf, "category", None) or "").strip()
    if cat == LibraryFile.CATEGORY_INSPECTION_SUBMIT:
        return library_user_may_export_site_record_library_pdfs(user)
    if cat == LibraryFile.CATEGORY_SITE_RECORD:
        return (
            library_user_may_export_site_record_library_pdfs(user)
            or library_user_may_export_report_library_pdfs(user)
        )
    if cat == LibraryFile.CATEGORY_REPORT:
        return library_user_may_export_report_library_pdfs(user)
    return False


def library_filter_tasks_for_template_management(queryset, user):
    """
    任务模板库左侧树 / 列表可见的 LibraryTask 范围。

    test / test-1 … test-5：同组创建 + 可见项目挂载 +（若有）分配给自己的任务；
    仅有分配权无自建权：仅分配任务；模板编辑岗：仅本人创建。
    """
    from apps.core.models import LibraryTaskAssignment

    assign = role_has(user, "perm_assign_tasks")
    tmpl_write = role_has(user, "perm_library_task_templates_write")
    participant = bool(
        library_user_may_filled_pdf_toolchain(user)
        and LibraryTaskAssignment.objects.filter(assignee=user).exists()
    )
    if assign and library_user_is_template_editor(user):
        return queryset.filter(created_by=user)
    if assign:
        return queryset
    assign_ids = list(
        LibraryTaskAssignment.objects.filter(assignee=user).values_list(
            "library_task_id", flat=True
        )
    )
    if tmpl_write and library_user_is_test_peer(user):
        from django.db.models import Q

        peer_ids = list(library_test_peer_user_ids())
        scoped = library_user_scoped_project_ids(user)
        tq = Q(created_by_id__in=peer_ids) | Q(created_by_id__isnull=True)
        if scoped:
            tq |= Q(projects__id__in=scoped)
        if participant and assign_ids:
            tq |= Q(pk__in=assign_ids)
        return queryset.filter(tq).distinct()
    if participant and tmpl_write:
        from django.db.models import Q

        return queryset.filter(Q(pk__in=assign_ids) | Q(created_by=user))
    if participant:
        return queryset.filter(pk__in=assign_ids)
    if tmpl_write:
        return queryset.filter(created_by=user)
    return queryset.none()


def library_user_may_access_task_template_library_nav(user) -> bool:
    """
    是否与 ``library_task_management`` 视图一致的准入条件（侧栏、仪表盘、项目工作台链等）。

    含：分配文件库任务、覆盖项 ``perm_library_task_templates_write``（如甲方演示自建模板）、
    或已被分配检测任务且具备模板填 PDF 链路的参与人。
    不包含 ``perm_file_library``；展示入口的模板仍需自备文件库可见性判断。
    委托统筹与新轨组织角色（行政 / 检测·评价主任与员工）可在工作台选用标准任务，
    但不展示「任务模板库」入口（与 coordinator 一致）。
    """
    if library_user_is_commission_coordinator(user):
        return False
    try:
        from apps.core.org_roles import user_is_org_role

        if user_is_org_role(user):
            return False
    except Exception:
        pass
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
    """医院信息管理入口：可写账号，或检测/评价部只读浏览。"""
    if not getattr(user, "is_authenticated", False):
        return False
    if not role_has(user, "perm_file_library"):
        return False
    if library_user_may_edit_hospital_info(user):
        return True
    try:
        from apps.core.org_roles import user_is_evaluation_org, user_is_inspection_org

        if user_is_inspection_org(user) or user_is_evaluation_org(user):
            return True
    except Exception:
        pass
    return False


def library_user_may_edit_hospital_info(user) -> bool:
    """医院信息管理：维护医院/院区/科室与设备（本模块无只读访客档）。"""
    if not getattr(user, "is_authenticated", False):
        return False
    # 仅新轨组织角色走新规则；旧轨（统筹/五岗/管理员等）完全沿用原逻辑
    try:
        from apps.core.org_roles import (
            user_is_admin_office,
            user_is_dept_director,
            user_is_dept_staff,
            user_is_legacy_permission_track,
        )

        if not user_is_legacy_permission_track(user):
            if user_is_admin_office(user):
                return True
            if user_is_dept_director(user) or user_is_dept_staff(user):
                return False
    except Exception:
        pass
    if library_user_is_commission_coordinator(user) and role_has(user, "perm_file_library"):
        return True
    if role_has(user, "perm_manage_users"):
        return True
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
    if perm == "perm_library_task_templates_write" and (
        library_user_has_party_a_demo_restrictions(user) or library_user_is_test_peer(user)
    ):
        return True
    if perm == "perm_biz_registry" and library_user_has_party_a_demo_restrictions(user):
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


def library_user_can_delete_library_project(user, project) -> bool:
    """是否可在项目工作台删除或停用指定项目。"""
    if project is None or not getattr(user, "is_authenticated", False):
        return False
    if library_user_can_assign_tasks_to_participants(user):
        return True
    if library_user_is_project_primary_responsible(user, project):
        return True
    if role_has(user, "perm_create_library_project") and getattr(project, "created_by_id", None) == user.id:
        return True
    return False


def library_user_may_create_library_project(user) -> bool:
    """是否可在项目工作台新建委托项目。"""
    if not getattr(user, "is_authenticated", False):
        return False
    if library_user_is_coordinator_managed_workflow_user(user):
        # 授权签字人：即使由委托统筹创建，也可在工作台新建委托项目
        if _role_code(user) == "authorized_signatory" and role_has(
            user, "perm_create_library_project"
        ):
            return True
        return False
    if library_user_can_assign_tasks_to_participants(user):
        return True
    return role_has(user, "perm_create_library_project")


def library_user_may_mutate_project_workbench(user, project) -> bool:
    """
    是否可对「项目工作台」内指定项目进行委托、流程、分配等写操作（不含「文件」标签维护）。
    高权限分配者视为全项目；主要负责人限本项目；参与人须已有任务分配或为项目创建者。
    委托统筹创建的检测员 / 校核员仅可查看，不可编辑。
    """
    if project is None or not getattr(user, "is_authenticated", False):
        return False
    if library_user_is_coordinator_workflow_site_only_role(user):
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
    if library_user_is_coordinator_workflow_site_only_role(user):
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


def library_template_file_accessible_for_task(user, task, lf: LibraryFile) -> bool:
    """
    在指定任务上下文中是否可读该模板文件。

    轮换进历史的文件会从任务 M2M 解绑，仅 ``library_file_access_allowed`` 会误判为无权；
    任务模板库历史与编辑器下拉已放宽为「可维护/可打开该任务」即可读。
    """
    if library_file_access_allowed(user, lf):
        return True
    if task is None or lf.category != LibraryFile.CATEGORY_TEMPLATE:
        return False
    from apps.core.library_file_service import library_file_exists_on_disk

    if not library_file_exists_on_disk(lf):
        return False
    return library_user_may_edit_library_task(user, task) or library_user_may_access_assigned_library_task(
        user, task
    )


def _library_task_linked_to_user_scoped_projects(user, task) -> bool:
    """任务模板已挂载到当前用户可见的检测项目（分配/自建/主责）。"""
    pids = library_user_scoped_project_ids(user)
    if not pids:
        return False
    return task.projects.filter(pk__in=pids).exists()


def library_user_may_edit_library_task(user, task) -> bool:
    """
    当前用户是否可编辑该 LibraryTask（输出目标、模板绑定、删除等）。

    - test / test-1 … test-5：可维护同组账号创建的无主/同组任务，以及已挂到本人可见项目上的任务模板；
    - 具备「分配文件库任务」：按角色规则（模板编辑岗仅本人创建）；
    - 仅有 ``perm_library_task_templates_write``：仅本人创建。
    """
    if task is None:
        return False
    assign = role_has(user, "perm_assign_tasks")
    tmpl_write = role_has(user, "perm_library_task_templates_write")
    if not assign and not tmpl_write:
        return False
    creator_id = getattr(task, "created_by_id", None)
    if library_user_is_test_peer(user) and tmpl_write:
        if creator_id is None or creator_id in library_test_peer_user_ids():
            return True
        if _library_task_linked_to_user_scoped_projects(user, task):
            return True
    if assign:
        if not library_user_is_template_editor(user):
            return True
        return creator_id == user.id
    return creator_id == user.id


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
    委托统筹创建的流程岗位在已有分配/可见项目时允许导出（现场岗仅现场记录类按钮另判）。
    """
    if not library_scope_own_files_only(user):
        return True
    if library_signatory_assigned_project_selected(user, project_selected_id):
        return True
    if library_user_test_account_self_fill(user) and library_user_scoped_project_ids(user):
        return True
    if library_user_is_coordinator_managed_workflow_user(user) and library_user_scoped_project_ids(
        user
    ):
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
    if library_user_is_test_peer(user) and lf.created_by_id in library_test_peer_user_ids():
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
    - test 同组账号可删除同组上传的模板文件。
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
    if library_user_is_test_peer(user) and lf.created_by_id in library_test_peer_user_ids():
        return True
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
        "ui_sidebar_show_user_management": False,
        "ui_sidebar_show_f1_eval": False,
        "ui_sidebar_show_detection_workflow_hubs": False,
        "ui_dashboard_hide_json_tile": False,
        "ui_dashboard_site_record_focus": False,
        "ui_dashboard_hide_file_stats_row": False,
        "ui_dashboard_show_django_admin_tile": False,
        "ui_show_system_debug_settings": False,
        "ui_show_app_ota_settings": False,
    }
    if not getattr(user, "is_authenticated", False):
        return empty
    code = _role_code(user)
    is_super = bool(getattr(user, "is_superuser", False)) or code == "super_admin"
    is_admin = code == "admin"
    is_coordinator = code == COMMISSION_COORDINATOR_ROLE_CODE
    is_dept_director = False
    is_evaluation_org = False
    try:
        from apps.core.org_roles import (
            user_is_dept_director as _is_dept_director,
            user_is_evaluation_org as _is_eval_org,
        )

        is_dept_director = bool(_is_dept_director(user))
        is_evaluation_org = bool(_is_eval_org(user))
    except Exception:
        is_dept_director = False
        is_evaluation_org = False
    has_assign = library_user_can_assign_tasks_to_participants(user)

    # 侧栏「项目工作台 / 委托管理」：评价部与检测部均可进（内容按部门分支）
    if is_evaluation_org:
        show_pm = role_has(user, "perm_file_library") and library_user_may_access_f1_eval(user)
    elif is_super or is_admin or has_assign or is_coordinator:
        show_pm = role_has(user, "perm_file_library")
    elif library_user_is_coordinator_managed_workflow_user(user):
        show_pm = role_has(user, "perm_file_library")
    elif code in ("field_inspector", "site_reviewer"):
        show_pm = False
    else:
        show_pm = role_has(user, "perm_file_library")

    # 仪表盘首行文件统计：现场两岗只强调现场记录；委托统筹隐藏数据文件入口
    site_focus = code in ("field_inspector", "site_reviewer")
    hide_json = site_focus or is_coordinator
    hide_file_row = not role_has(user, "perm_file_library")

    # Django Admin 入口：仅系统管理类角色；委托统筹 / 部门主任走业务「用户管理」页
    show_admin_tile = is_super or is_admin or (
        role_has(user, "perm_manage_users")
        and not is_coordinator
        and not is_dept_director
    )

    from apps.core.commission_management_service import library_user_may_access_commission_manage

    # 侧栏用户管理：超管/管理员 + 委托统筹 + 检测/评价部主任（本部门员工）
    show_user_mgmt = role_has(user, "perm_manage_users") and (
        is_super or is_admin or is_coordinator or is_dept_director
    )

    # 检测报告链路入口：原始记录 / 报告生成 / 审核签发 / 报告下载
    show_detection_hubs = bool(
        role_has(user, "perm_file_library") and not is_evaluation_org
    )

    return {
        "ui_sidebar_show_project_management": bool(show_pm),
        "ui_sidebar_show_commission_manage": bool(
            show_pm and library_user_may_access_commission_manage(user)
        ),
        "ui_sidebar_show_user_management": bool(show_user_mgmt),
        "ui_sidebar_show_f1_eval": bool(library_user_may_access_f1_eval(user)),
        "ui_sidebar_show_detection_workflow_hubs": show_detection_hubs,
        "ui_dashboard_hide_json_tile": bool(hide_json),
        "ui_dashboard_site_record_focus": bool(site_focus),
        "ui_dashboard_hide_file_stats_row": bool(hide_file_row),
        "ui_dashboard_show_django_admin_tile": bool(show_admin_tile),
        "ui_show_system_debug_settings": bool(is_super),
        "ui_show_app_ota_settings": bool(is_super),
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
