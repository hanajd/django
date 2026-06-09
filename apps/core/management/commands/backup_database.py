"""备份 SQLite 数据库到 backups/db/，支持列出与恢复。"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.core.db_backup_service import (
    backup_sqlite_database,
    list_database_backups,
    restore_sqlite_database_from_backup,
)


class Command(BaseCommand):
    help = "备份 / 列出 / 恢复 SQLite 数据库（在线备份，不中断服务）。"

    def add_arguments(self, parser):
        parser.add_argument("--label", default="", help="备份文件名标签（可选）")
        parser.add_argument("--keep", type=int, default=None, help="保留最近 N 份备份（默认读 settings）")
        parser.add_argument("--list", action="store_true", help="列出已有备份")
        parser.add_argument("--restore", metavar="PATH", help="从指定备份文件恢复（恢复前会自动再备一份当前库）")

    def handle(self, *args, **options):
        if options.get("list"):
            rows = list_database_backups(limit=50)
            if not rows:
                self.stdout.write("尚无备份。")
                return
            for row in rows:
                size_mb = row["size"] / (1024 * 1024)
                self.stdout.write(
                    f"{row['path']}  {size_mb:.2f} MB  label={row.get('label') or '-'}  "
                    f"created={row.get('created_at') or '-'}"
                )
            return

        restore_path = (options.get("restore") or "").strip()
        if restore_path:
            result = restore_sqlite_database_from_backup(Path(restore_path))
            if not result.get("ok"):
                raise CommandError(result.get("error") or "恢复失败")
            self.stdout.write(self.style.SUCCESS(f"已从 {result['restored_from']} 恢复"))
            self.stdout.write(f"恢复前快照：{result.get('pre_restore_backup')}")
            return

        result = backup_sqlite_database(label=options.get("label") or "", keep=options.get("keep"))
        if not result.get("ok"):
            raise CommandError(result.get("error") or "备份失败")
        size_mb = result["size"] / (1024 * 1024)
        self.stdout.write(self.style.SUCCESS(f"备份完成：{result['path']} ({size_mb:.2f} MB)"))
        if result.get("pruned"):
            self.stdout.write(f"已清理旧备份 {result['pruned']} 份")
