"""将平铺的 site_records/*.pdf 迁入与 inspection_submits 一致的批次目录。"""
from django.core.management.base import BaseCommand

from apps.core.library_file_service import reorganize_flat_site_records_on_disk


class Command(BaseCommand):
    help = "重组本地 site_records 目录：site_records/{委托编号}/{时间戳}/"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="仅统计将迁移的文件，不移动磁盘或更新数据库",
        )

    def handle(self, *args, **options):
        stats = reorganize_flat_site_records_on_disk(dry_run=bool(options.get("dry_run")))
        self.stdout.write(
            self.style.SUCCESS(
                f"scanned={stats['scanned']} moved={stats['moved']} skipped={stats['skipped']}"
            )
        )
        for err in stats.get("errors") or []:
            self.stdout.write(self.style.WARNING(err))
