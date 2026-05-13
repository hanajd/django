"""
自定义权限类
实现基于角色的权限控制 (RBAC)
"""
from rest_framework import permissions
from django.contrib.auth.models import User

from apps.core.library_access import APP_SIDE_ROLE_CODES


class IsSuperAdmin(permissions.BasePermission):
    """超级管理员权限"""
    
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_superuser


class IsAdminOrSuperAdmin(permissions.BasePermission):
    """管理员或超级管理员权限"""
    
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        return request.user.is_superuser or (
            hasattr(request.user, 'profile') and
            request.user.profile.role.code in ['admin', 'super_admin']
        )


class IsAppUser(permissions.BasePermission):
    """检测业务侧参与人：旧版 app_user 或检测员/校核员/编制人/审核人/授权签字人。"""

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if not hasattr(request.user, "profile") or request.user.profile.role is None:
            return False
        code = getattr(request.user.profile.role, "code", "") or ""
        return code in APP_SIDE_ROLE_CODES


class IsOwnerOrReadOnly(permissions.BasePermission):
    """对象所有者或只读权限"""
    
    def has_object_permission(self, request, view, obj):
        # 读取权限允许任何请求
        if request.method in permissions.SAFE_METHODS:
            return True
        # 写入权限只允许对象所有者
        return obj.owner == request.user
