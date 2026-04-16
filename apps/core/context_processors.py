"""
上下文处理器
用于在模板中提供动态菜单等全局数据
"""
from apps.core.library_access import role_has, role_permission_map
from apps.core.models import Menu


def menu_context(request):
    """动态菜单上下文处理器"""
    user = request.user

    if not user.is_authenticated:
        return {
            "menus": [],
            "hide_process_pipeline": True,
            "role_perm": {},
        }

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
    return {
        "menus": menus,
        "hide_process_pipeline": not role_has(user, "perm_process_pipeline"),
        "role_perm": role_perm,
    }
