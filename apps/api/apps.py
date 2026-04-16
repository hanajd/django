"""
API 应用配置
"""
from django.apps import AppConfig


class ApiConfig(AppConfig):
    """API 应用配置类"""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.api'
    verbose_name = 'API 模块'
