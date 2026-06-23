"""写入 jwt_runtime.json，无需重启 Django 即可调整 JWT 有效期。"""

from django.core.management.base import BaseCommand

from apps.api.jwt_runtime_config import jwt_runtime_config_path, write_jwt_runtime_config


class Command(BaseCommand):
    help = "写入 jwt_runtime.json 并立即在当前进程生效（无需重启 Django）。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--access-hours",
            type=float,
            default=None,
            help="access token 有效期（小时）",
        )
        parser.add_argument(
            "--refresh-days",
            type=float,
            default=None,
            help="refresh token 有效期（天）",
        )
        parser.add_argument(
            "--leeway-seconds",
            type=int,
            default=None,
            help="校验过期容差（秒）",
        )
        parser.add_argument(
            "--show",
            action="store_true",
            help="仅显示当前运行时配置路径与数值",
        )

    def handle(self, *args, **options):
        path = jwt_runtime_config_path()
        if options.get("show"):
            from apps.api.jwt_runtime_config import get_jwt_runtime_config

            cfg = get_jwt_runtime_config(force_reload=True)
            self.stdout.write(f"配置文件: {path}")
            self.stdout.write(
                f"access={cfg.access_token_lifetime} "
                f"refresh={cfg.refresh_token_lifetime} "
                f"leeway={cfg.leeway_seconds}s"
            )
            return

        cfg = write_jwt_runtime_config(
            access_token_hours=options.get("access_hours"),
            refresh_token_days=options.get("refresh_days"),
            leeway_seconds=options.get("leeway_seconds"),
        )
        self.stdout.write(self.style.SUCCESS(f"已写入 {path}"))
        self.stdout.write(
            f"access={cfg.access_token_lifetime.total_seconds() / 3600:.1f}h "
            f"refresh={cfg.refresh_token_lifetime.days}d "
            f"leeway={cfg.leeway_seconds}s"
        )
        self.stdout.write("新登录/刷新的 token 将使用上述有效期（无需重启）。")
