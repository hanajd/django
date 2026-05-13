"""
Core 应用配置
"""
from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Core 应用配置类"""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.core'
    verbose_name = '核心模块'

    def ready(self):
        # 注册信号（延迟 import 避免 AppRegistry 未就绪）
        from apps.core import signals  # noqa: F401
