"""
上下文处理器
用于在模板中提供动态菜单等全局数据
"""
from apps.core.library_access import (
    library_user_has_party_a_demo_restrictions,
    library_user_may_access_task_template_library_nav,
    role_has,
    role_permission_map,
    role_ui_context,
)
from apps.core.models import Menu


def menu_context(request):
    """动态菜单上下文处理器"""
    user = request.user

    if not user.is_authenticated:
        out = {
            "menus": [],
            "hide_process_pipeline": True,
            "role_perm": {},
            "show_library_task_nav": False,
            "show_backend_usage_guide": False,
        }
        out.update(role_ui_context(user))
        return out

    role_perm = role_permission_map(user)

    # 超级管理员可以访问所有菜单
    if user.is_superuser:
        menus = Menu.objects.filter(
            parent=None,
            is_visible=True,
        ).exclude(name="文件与提取").prefetch_related("children")
    else:
        try:
            role = user.profile.role
            menus = Menu.objects.filter(
                parent=None,
                is_visible=True,
                roles=role,
            ).exclude(name="文件与提取").prefetch_related("children")
        except Exception:
            menus = []

    # 文件库 / 流程处理由 base 模板固定展示，此处排除同名动态菜单以免重复
    out = {
        "menus": menus,
        "hide_process_pipeline": not role_has(user, "perm_process_pipeline"),
        "role_perm": role_perm,
        "show_library_task_nav": library_user_may_access_task_template_library_nav(user),
        "show_backend_usage_guide": library_user_has_party_a_demo_restrictions(user),
    }
    out.update(role_ui_context(user))
    return out
