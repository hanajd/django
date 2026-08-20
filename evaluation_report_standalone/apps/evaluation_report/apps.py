from django.apps import AppConfig


class EvaluationReportConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.evaluation_report"
    verbose_name = "评价报告"

    def ready(self) -> None:
        # SQLite: WAL 允许读与写并发，避免生成 PDF 时编辑页报 database is locked
        from django.db.backends.signals import connection_created

        def _configure_sqlite(sender, connection, **kwargs):  # noqa: ARG001
            if connection.vendor != "sqlite":
                return
            cursor = connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA busy_timeout=60000;")
            cursor.execute("PRAGMA synchronous=NORMAL;")

        connection_created.connect(_configure_sqlite)
