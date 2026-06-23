"""将平铺的 reports/*.pdf 迁入按委托编号/任务/日期分层的目录。"""
from django.core.management.base import BaseCommand

from apps.core.library_file_service import reorganize_flat_reports_on_disk


class Command(BaseCommand):
    help = "重组本地 reports 目录：reports/{委托编号}/{任务}/{年}/{月}/{日}/"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="仅统计将迁移的文件，不移动磁盘或更新数据库",
        )

    def handle(self, *args, **options):
        stats = reorganize_flat_reports_on_disk(dry_run=bool(options.get("dry_run")))
        self.stdout.write(
            self.style.SUCCESS(
                f"scanned={stats['scanned']} moved={stats['moved']} skipped={stats['skipped']}"
            )
        )
        for err in stats.get("errors") or []:
            self.stdout.write(self.style.WARNING(err))
