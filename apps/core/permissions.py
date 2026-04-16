"""
自定义权限类
实现基于角色的权限控制 (RBAC)
"""
from rest_framework import permissions
from django.contrib.auth.models import User


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
    """App 用户权限"""
    
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        return (
            hasattr(request.user, 'profile') and
            request.user.profile.role.code == 'app_user'
        )


class IsOwnerOrReadOnly(permissions.BasePermission):
    """对象所有者或只读权限"""
    
    def has_object_permission(self, request, view, obj):
        # 读取权限允许任何请求
        if request.method in permissions.SAFE_METHODS:
            return True
        # 写入权限只允许对象所有者
        return obj.owner == request.user
