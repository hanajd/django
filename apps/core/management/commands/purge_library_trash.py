"""永久删除回收站中超过保留期的文件库记录（默认 31 天）。"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.library_file_service import hard_delete_library_file_disk_and_row
from apps.core.models import LibraryFile


class Command(BaseCommand):
    help = "永久删除 deleted_at 早于「现在 - 保留天数」的文件库条目（磁盘 + 数据库）。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=31,
            help="回收站保留天数（默认 31）",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="仅打印将删除的条数，不执行删除",
        )

    def handle(self, *args, **options):
        days = max(1, int(options.get("days") or 31))
        dry = bool(options.get("dry_run"))
        cutoff = timezone.now() - timedelta(days=days)
        qs = LibraryFile.all_objects.filter(deleted_at__isnull=False, deleted_at__lte=cutoff).order_by("id")
        ids = list(qs.values_list("id", flat=True))
        self.stdout.write(
            self.style.WARNING(
                f"回收站保留 {days} 天：截止时间 {cutoff.isoformat(timespec='seconds')}，"
                f"待永久删除 {len(ids)} 条"
            )
        )
        if dry:
            return
        n = 0
        for lf in LibraryFile.all_objects.filter(pk__in=ids).order_by("id").iterator():
            hard_delete_library_file_disk_and_row(lf)
            n += 1
        self.stdout.write(self.style.SUCCESS(f"已永久删除 {n} 条回收站记录"))
