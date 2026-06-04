"""校验文件库 DB 记录与 media 磁盘是否一致（可定时或部署后手动执行）。"""

from django.core.management.base import BaseCommand

from apps.core.library_media_integrity import run_library_media_integrity


class Command(BaseCommand):
    help = (
        "检查文件库记录在 media 中是否有对应文件；"
        "缺失则移入回收站，OCR 源文件全缺失的待处理任务标记失败。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="仅统计，不写数据库",
        )

    def handle(self, *args, **options):
        dry = bool(options.get("dry_run"))
        stats = run_library_media_integrity(dry_run=dry)
        prefix = "[dry-run] " if dry else ""
        self.stdout.write(f"{prefix}文件库 media 校验结果:")
        for key, val in sorted(stats.items()):
            self.stdout.write(f"  {key}: {val}")
        if not dry and stats.get("files_soft_deleted"):
            self.stdout.write(
                self.style.WARNING(
                    f"已将 {stats['files_soft_deleted']} 条无磁盘文件的记录移入回收站"
                )
            )
        if not dry and stats.get("ocr_tasks_failed"):
            self.stdout.write(
                self.style.WARNING(
                    f"已将 {stats['ocr_tasks_failed']} 个 OCR 任务标记为失败"
                )
            )
        if stats.get("projects_without_usable_files"):
            self.stdout.write(
                self.style.NOTICE(
                    f"仍有 {stats['projects_without_usable_files']} 个启用项目"
                    f"暂无任何可用磁盘文件（可能为新空项目，未自动停用）"
                )
            )
