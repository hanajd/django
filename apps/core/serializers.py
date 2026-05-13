"""
DRF 序列化器
用于 API 数据的序列化和反序列化
"""
from django.contrib.auth.models import User
from django.db import transaction
from rest_framework import serializers

from apps.core.models import Menu, Role, UserProfile


class UserSerializer(serializers.ModelSerializer):
    """用户序列化器"""
    phone = serializers.CharField(source='profile.phone', required=False, allow_blank=True)
    department = serializers.CharField(source='profile.department', required=False, allow_blank=True)
    position = serializers.CharField(source='profile.position', required=False, allow_blank=True)
    role_code = serializers.CharField(source='profile.role.code', required=False, allow_blank=True)
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 
                  'phone', 'department', 'position', 'role_code', 'is_staff', 'is_active']
        read_only_fields = ['id']


class UserCreateSerializer(serializers.ModelSerializer):
    """用户创建序列化器"""
    password = serializers.CharField(write_only=True, required=True, min_length=1)
    phone = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    department = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    position = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    role_code = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "password",
            "first_name",
            "last_name",
            "phone",
            "department",
            "position",
            "role_code",
        ]

    def validate_role_code(self, value):
        if not (value or "").strip():
            return ""
        code = value.strip()
        if not Role.objects.filter(code=code).exists():
            raise serializers.ValidationError(f"未知角色代码: {code}")
        return code

    def create(self, validated_data):
        """创建用户与 UserProfile（同事务）"""
        phone = (validated_data.pop("phone", None) or "") or ""
        department = (validated_data.pop("department", None) or "") or ""
        position = (validated_data.pop("position", None) or "") or ""
        role_code = (validated_data.pop("role_code", None) or "") or ""

        password = validated_data.pop("password")
        email = validated_data.get("email") or ""
        validated_data["email"] = email

        role = None
        if role_code.strip():
            role = Role.objects.get(code=role_code.strip())

        with transaction.atomic():
            user = User.objects.create_user(password=password, **validated_data)
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.phone = phone or None
            profile.department = department or None
            profile.position = position or None
            profile.role = role
            profile.save()

        return user


class RoleSerializer(serializers.ModelSerializer):
    """角色序列化器"""
    
    class Meta:
        model = Role
        fields = ['id', 'name', 'code', 'description', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class MenuSerializer(serializers.ModelSerializer):
    """菜单序列化器"""
    children = serializers.SerializerMethodField()
    
    class Meta:
        model = Menu
        fields = ['id', 'name', 'path', 'icon', 'parent', 'sort_order', 
                  'is_visible', 'children', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_children(self, obj):
        """获取子菜单"""
        children = obj.get_children()
        return MenuSerializer(children, many=True).data
