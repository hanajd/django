"""
新业务线组织角色（行政 / 检测部 / 评价部）。

与委托统筹（commission_coordinator）及全局五岗并行；不修改旧轨行为。
员工默认低权，仅在项目 WorkflowMember 挂岗后具备对应流程权限。
"""
from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any, Dict, FrozenSet, List, Optional

from django.contrib.auth.models import User
from django.db.models import QuerySet
from django.utils import timezone

# 结构化部门
ORG_UNIT_ADMIN = "admin_office"
ORG_UNIT_INSPECTION = "inspection"
ORG_UNIT_EVALUATION = "evaluation"

ORG_UNIT_CHOICES = (
    (ORG_UNIT_ADMIN, "行政"),
    (ORG_UNIT_INSPECTION, "检测部"),
    (ORG_UNIT_EVALUATION, "评价部"),
)

# 角色 code
ROLE_ADMIN_OFFICE = "admin_office"
ROLE_DEPT_DIRECTOR_INSPECTION = "dept_director_inspection"
ROLE_DEPT_STAFF_INSPECTION = "dept_staff_inspection"
ROLE_DEPT_DIRECTOR_EVALUATION = "dept_director_evaluation"
ROLE_DEPT_STAFF_EVALUATION = "dept_staff_evaluation"

ORG_ROLE_CODES: FrozenSet[str] = frozenset(
    {
        ROLE_ADMIN_OFFICE,
        ROLE_DEPT_DIRECTOR_INSPECTION,
        ROLE_DEPT_STAFF_INSPECTION,
        ROLE_DEPT_DIRECTOR_EVALUATION,
        ROLE_DEPT_STAFF_EVALUATION,
    }
)

# 旧轨角色：权限矩阵与派工逻辑保持改前行为，新轨规则不得套用
LEGACY_TRACK_ROLE_CODES: FrozenSet[str] = frozenset(
    {
        "super_admin",
        "admin",
        "app_user",
        "field_inspector",
        "site_reviewer",
        "report_author",
        "report_auditor",
        "authorized_signatory",
        "template_editor",
        "template_tester",
        "commission_coordinator",
    }
)

DEPT_DIRECTOR_ROLE_CODES: FrozenSet[str] = frozenset(
    {ROLE_DEPT_DIRECTOR_INSPECTION, ROLE_DEPT_DIRECTOR_EVALUATION}
)

DEPT_STAFF_ROLE_CODES: FrozenSet[str] = frozenset(
    {ROLE_DEPT_STAFF_INSPECTION, ROLE_DEPT_STAFF_EVALUATION}
)

ROLE_TO_ORG_UNIT: Dict[str, str] = {
    ROLE_ADMIN_OFFICE: ORG_UNIT_ADMIN,
    ROLE_DEPT_DIRECTOR_INSPECTION: ORG_UNIT_INSPECTION,
    ROLE_DEPT_STAFF_INSPECTION: ORG_UNIT_INSPECTION,
    ROLE_DEPT_DIRECTOR_EVALUATION: ORG_UNIT_EVALUATION,
    ROLE_DEPT_STAFF_EVALUATION: ORG_UNIT_EVALUATION,
}

DIRECTOR_TO_STAFF_ROLE: Dict[str, str] = {
    ROLE_DEPT_DIRECTOR_INSPECTION: ROLE_DEPT_STAFF_INSPECTION,
    ROLE_DEPT_DIRECTOR_EVALUATION: ROLE_DEPT_STAFF_EVALUATION,
}

# 旧轨全局五岗（派工隔离时排除）
LEGACY_WORKFLOW_GLOBAL_ROLE_CODES: FrozenSet[str] = frozenset(
    {
        "field_inspector",
        "site_reviewer",
        "report_author",
        "report_auditor",
        "authorized_signatory",
        "app_user",
        "commission_coordinator",
    }
)

INVITE_DEFAULT_TTL_DAYS = 7

# 角色默认权限（低权员工；主任带用户管理与派单）
_ORG_STAFF_PERMS: Dict[str, bool] = {
    "perm_manage_users": False,
    "perm_manage_roles": False,
    "perm_manage_menus": False,
    "perm_file_library": True,
    "perm_file_upload": False,
    "perm_file_upload_attachment": False,
    "perm_file_download": True,
    "perm_file_preview": True,
    "perm_file_delete": False,
    "perm_file_scope_own_only": True,
    "perm_process_pipeline": False,
    "perm_htmlpdf": False,
    "perm_assign_tasks": False,
    "perm_create_library_project": False,
    "perm_biz_registry": False,
    "perm_f1_eval": False,
    "perm_evaluation_report": False,
}

# 检测部主任：派工 + 业务登记 + 用户管理
_ORG_DIRECTOR_PERMS: Dict[str, bool] = {
    **_ORG_STAFF_PERMS,
    "perm_manage_users": True,
    "perm_file_upload": True,
    "perm_file_upload_attachment": True,
    "perm_file_delete": True,
    "perm_file_scope_own_only": False,
    "perm_assign_tasks": True,
    "perm_create_library_project": False,
    "perm_biz_registry": True,
    "perm_f1_eval": False,
    "perm_evaluation_report": False,
}

# 评价部主任：本部门用户 + 评价报告表/书；医院信息只读；不开检测派工/仪器台账/建项
_ORG_EVAL_DIRECTOR_PERMS: Dict[str, bool] = {
    **_ORG_STAFF_PERMS,
    "perm_manage_users": True,
    "perm_file_upload": True,
    "perm_file_upload_attachment": True,
    "perm_file_delete": True,
    "perm_file_scope_own_only": False,
    "perm_assign_tasks": False,
    "perm_create_library_project": False,
    "perm_biz_registry": False,
    "perm_f1_eval": True,
    "perm_evaluation_report": True,
}

# 评价部员工：低权 + 评价报告表/书
_ORG_EVAL_STAFF_PERMS: Dict[str, bool] = {
    **_ORG_STAFF_PERMS,
    "perm_f1_eval": True,
    "perm_evaluation_report": True,
}

_ORG_ADMIN_PERMS: Dict[str, bool] = {
    **_ORG_STAFF_PERMS,
    "perm_manage_users": False,
    "perm_file_upload": True,
    "perm_file_upload_attachment": True,
    "perm_file_delete": True,
    "perm_file_scope_own_only": False,
    "perm_assign_tasks": False,
    "perm_create_library_project": True,
    "perm_biz_registry": True,
    "perm_f1_eval": False,
}

ORG_ROLE_SEED: Dict[str, Dict[str, Any]] = {
    ROLE_ADMIN_OFFICE: {
        "name": "行政",
        "description": "医院信息与委托立项；不管项目流程派工与检测环节。",
        "org_unit": ORG_UNIT_ADMIN,
        "perms": _ORG_ADMIN_PERMS,
    },
    ROLE_DEPT_DIRECTOR_INSPECTION: {
        "name": "检测部主任",
        "description": "检测部接单、管本部门员工、人员派工；医院信息只读。",
        "org_unit": ORG_UNIT_INSPECTION,
        "perms": _ORG_DIRECTOR_PERMS,
    },
    ROLE_DEPT_STAFF_INSPECTION: {
        "name": "检测部员工",
        "description": "默认低权；医院信息只读；仅在项目挂流程岗后具备对应权限。",
        "org_unit": ORG_UNIT_INSPECTION,
        "perms": _ORG_STAFF_PERMS,
    },
    ROLE_DEPT_DIRECTOR_EVALUATION: {
        "name": "评价部主任",
        "description": "评价报告表/书看板（委托管理与项目工作台）；管本部门员工；医院信息只读；不进入检测派工与仪器台账。",
        "org_unit": ORG_UNIT_EVALUATION,
        "perms": _ORG_EVAL_DIRECTOR_PERMS,
    },
    ROLE_DEPT_STAFF_EVALUATION: {
        "name": "评价部员工",
        "description": "可使用评价报告表；委托管理与项目工作台展示评价项目；医院信息只读。",
        "org_unit": ORG_UNIT_EVALUATION,
        "perms": _ORG_EVAL_STAFF_PERMS,
    },
}


def _role_code(user) -> str:
    try:
        role = user.profile.role
        return (role.code if role else "") or ""
    except Exception:
        return ""


def _profile_org_unit(user) -> str:
    try:
        return (getattr(user.profile, "org_unit", None) or "").strip()
    except Exception:
        return ""


def user_is_org_role(user) -> bool:
    return _role_code(user) in ORG_ROLE_CODES


def user_is_legacy_permission_track(user) -> bool:
    """
    旧轨账号（超管/统筹/全局五岗/模板岗等）：权限与派工逻辑保持改前不变。
    无角色或未知角色也按旧轨处理，避免误伤存量用户。
    """
    code = _role_code(user)
    if not code:
        return True
    if code in ORG_ROLE_CODES:
        return False
    return True


def user_is_admin_office(user) -> bool:
    return _role_code(user) == ROLE_ADMIN_OFFICE


def user_is_dept_director(user) -> bool:
    return _role_code(user) in DEPT_DIRECTOR_ROLE_CODES


def user_is_dept_staff(user) -> bool:
    return _role_code(user) in DEPT_STAFF_ROLE_CODES


def user_is_evaluation_org(user) -> bool:
    """评价部主任或员工（含 org_unit=evaluation 的新轨账号）。"""
    code = _role_code(user)
    if code in (ROLE_DEPT_DIRECTOR_EVALUATION, ROLE_DEPT_STAFF_EVALUATION):
        return True
    return user_org_unit(user) == ORG_UNIT_EVALUATION and code in ORG_ROLE_CODES


def user_is_inspection_org(user) -> bool:
    """检测部主任或员工。"""
    code = _role_code(user)
    if code in (ROLE_DEPT_DIRECTOR_INSPECTION, ROLE_DEPT_STAFF_INSPECTION):
        return True
    return user_org_unit(user) == ORG_UNIT_INSPECTION and code in ORG_ROLE_CODES


def user_is_inspection_director(user) -> bool:
    return _role_code(user) == ROLE_DEPT_DIRECTOR_INSPECTION


def user_org_unit(user) -> str:
    """优先资料字段；否则从角色推断。"""
    ou = _profile_org_unit(user)
    if ou:
        return ou
    return ROLE_TO_ORG_UNIT.get(_role_code(user), "")


def staff_role_code_for_director(user) -> str:
    return DIRECTOR_TO_STAFF_ROLE.get(_role_code(user), "")


def ensure_org_roles_seeded() -> List[str]:
    """幂等写入五类组织角色。返回新建的 code 列表。"""
    from apps.core.models import Role

    created: List[str] = []
    for code, meta in ORG_ROLE_SEED.items():
        perms = dict(meta["perms"])
        role, was_created = Role.objects.update_or_create(
            code=code,
            defaults={
                "name": meta["name"],
                "description": meta["description"],
                **perms,
            },
        )
        if was_created:
            created.append(code)
        else:
            dirty = False
            for k, v in perms.items():
                if getattr(role, k, None) != v:
                    setattr(role, k, v)
                    dirty = True
            if role.name != meta["name"]:
                role.name = meta["name"]
                dirty = True
            if dirty:
                role.save()
    return created


def library_user_may_manage_org_staff(actor) -> bool:
    """部门主任可管理本部门员工；超管/普通管理员亦可。员工不可。"""
    if not getattr(actor, "is_authenticated", False):
        return False
    if user_is_dept_staff(actor):
        return False
    if getattr(actor, "is_superuser", False) or _role_code(actor) in ("super_admin", "admin"):
        return True
    return user_is_dept_director(actor)


def library_user_may_create_invite(actor) -> bool:
    """可生成注册邀请链接：超管/管理员、部门主任；员工不可。"""
    return library_user_may_manage_org_staff(actor)


def user_is_system_admin(actor) -> bool:
    return bool(getattr(actor, "is_superuser", False)) or _role_code(actor) in (
        "super_admin",
        "admin",
    )


# 超管邀请可选角色（不含超级管理员 / 普通管理员，避免通过链接抬权）
INVITE_EXCLUDED_ROLE_CODES: FrozenSet[str] = frozenset({"super_admin", "admin"})


def invite_roles_for_user(actor) -> List[Any]:
    """
    生成邀请链接时可选择的角色列表。
    - 超管/管理员：除超管与普通管理员外的全部角色
    - 部门主任：仅本部门员工角色（固定一项）
    - 其他：空
    """
    from apps.core.models import Role

    if not library_user_may_create_invite(actor):
        return []
    if user_is_system_admin(actor):
        return list(
            Role.objects.exclude(code__in=INVITE_EXCLUDED_ROLE_CODES).order_by("name", "id")
        )
    if user_is_dept_director(actor):
        staff_code = staff_role_code_for_director(actor)
        if not staff_code:
            return []
        return list(Role.objects.filter(code=staff_code).order_by("name", "id"))
    return []


def resolve_invite_role_and_org_unit(
    *,
    created_by,
    role_code: str | None = None,
) -> tuple[str, str]:
    """
    解析邀请目标角色与 org_unit。
    主任忽略传入 role_code，强制本部门员工；超管必须指定合法角色。
    """
    if user_is_dept_staff(created_by):
        raise PermissionError("员工无权生成邀请链接")
    if not library_user_may_create_invite(created_by):
        raise PermissionError("无权创建邀请链接")

    if user_is_dept_director(created_by) and not user_is_system_admin(created_by):
        staff_code = staff_role_code_for_director(created_by)
        ou = user_org_unit(created_by)
        if not staff_code or not ou:
            raise PermissionError("当前账号无法确定部门员工角色")
        return staff_code, ou

    if not user_is_system_admin(created_by):
        raise PermissionError("无权创建邀请链接")

    code = (role_code or "").strip()
    if not code:
        raise PermissionError("请选择注册角色")
    if code in INVITE_EXCLUDED_ROLE_CODES:
        raise PermissionError("不可通过邀请链接注册该角色")
    from apps.core.models import Role

    if not Role.objects.filter(code=code).exists():
        raise PermissionError("所选角色无效")
    return code, ROLE_TO_ORG_UNIT.get(code, "")


def org_staff_queryset_for_director(actor) -> QuerySet:
    """主任可见/可管的本部门员工列表。"""
    from apps.core.models import User as _  # noqa: F401 — keep import path clear

    qs = User.objects.select_related("profile", "profile__role").filter(is_active=True)
    if getattr(actor, "is_superuser", False) or _role_code(actor) in ("super_admin", "admin"):
        return qs.filter(profile__role__code__in=DEPT_STAFF_ROLE_CODES).order_by("username", "id")
    if not user_is_dept_director(actor):
        return qs.none()
    ou = user_org_unit(actor)
    staff_code = staff_role_code_for_director(actor)
    return qs.filter(
        profile__org_unit=ou,
        profile__role__code=staff_code,
    ).order_by("username", "id")


def org_dispatch_candidate_queryset(actor) -> QuerySet:
    """
    新轨派工候选人：仅检测部主任可派本部门员工。
    评价部不参与检测流程派工。
    排除旧轨全局五岗 / 统筹 / 行政。
    """
    if user_is_evaluation_org(actor) and not user_is_system_admin(actor):
        return User.objects.none()
    return org_staff_queryset_for_director(actor)


def user_may_be_dispatched_by_org_director(actor, target) -> bool:
    if target is None or not getattr(target, "is_active", False):
        return False
    if user_is_evaluation_org(actor) and not user_is_system_admin(actor):
        return False
    code = _role_code(target)
    if code in LEGACY_WORKFLOW_GLOBAL_ROLE_CODES or code == ROLE_ADMIN_OFFICE:
        return False
    if code not in DEPT_STAFF_ROLE_CODES:
        return False
    if getattr(actor, "is_superuser", False) or _role_code(actor) in ("super_admin", "admin"):
        return True
    if not user_is_dept_director(actor):
        return False
    return user_org_unit(actor) == user_org_unit(target) and code == staff_role_code_for_director(
        actor
    )


def library_user_may_edit_hospital_info_org_track(user) -> bool:
    """新轨：仅行政（及超管）可写医院信息。委托统筹仍走旧逻辑。"""
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False) or _role_code(user) in ("super_admin", "admin"):
        return True
    return user_is_admin_office(user)


def create_invite_token(
    *,
    created_by,
    ttl_days: int = INVITE_DEFAULT_TTL_DAYS,
    role_code: str | None = None,
) -> Any:
    """创建一次性注册邀请令牌。"""
    from apps.core.models import UserInviteToken

    staff_code, ou = resolve_invite_role_and_org_unit(
        created_by=created_by, role_code=role_code
    )
    token = secrets.token_urlsafe(32)
    return UserInviteToken.objects.create(
        token=token,
        created_by=created_by,
        org_unit=ou or "",
        staff_role_code=staff_code,
        expires_at=timezone.now() + timedelta(days=max(1, int(ttl_days))),
        is_active=True,
        use_count=0,
    )


def pending_invite_tokens_for_user(actor) -> QuerySet:
    """当前用户发出的、尚未使用且未过期的邀请。"""
    from apps.core.models import UserInviteToken

    return UserInviteToken.objects.filter(
        created_by=actor,
        is_active=True,
        use_count=0,
        expires_at__gt=timezone.now(),
    ).order_by("-created_at")


def resolve_invite_token(raw_token: str) -> Any:
    from apps.core.models import UserInviteToken

    token = (raw_token or "").strip()
    if not token:
        return None
    row = UserInviteToken.objects.filter(token=token).select_related("created_by").first()
    if row is None:
        return None
    if not row.is_usable():
        return None
    return row
