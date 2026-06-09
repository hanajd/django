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
        from apps.core.library_media_integrity import run_library_media_integrity_on_startup

        run_library_media_integrity_on_startup()
        try:
            from apps.core.db_backup_service import maybe_auto_backup_on_startup

            result = maybe_auto_backup_on_startup()
            if result and result.get("ok"):
                import logging

                logging.getLogger(__name__).info("SQLite auto backup: %s", result.get("path"))
        except Exception:
            import logging

            logging.getLogger(__name__).exception("SQLite auto backup on startup failed")
