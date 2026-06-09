"""将扁平 templates/{uuid}.ext 迁移为按任务模板库层级存储，并写入 _task.json 清单。"""

from __future__ import annotations

import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.db_backup_service import backup_sqlite_database
from apps.core.library_file_service import library_file_exists_on_disk
from apps.core.models import LibraryFile, LibraryTask, LibraryTaskTemplateBindingHistory
from apps.core.template_storage_service import (
    _write_manifest_for_task,
    pick_primary_task_for_template_file,
    relocate_library_template_file,
)


class Command(BaseCommand):
    help = "迁移模板文件到分层目录（检测类型/设备/报告|现场/任务/current|history）。"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--skip-backup", action="store_true")
        parser.add_argument(
            "--purge-orphan-flat",
            action="store_true",
            help="删除 templates/ 根目录下已无数据库记录的旧扁平文件",
        )

    def handle(self, *args, **options):
        dry = bool(options.get("dry_run"))
        if not dry and not options.get("skip_backup"):
            bak = backup_sqlite_database(label="pre_migrate_template_storage")
            if not bak.get("ok"):
                raise CommandError(bak.get("error"))
            self.stdout.write(self.style.SUCCESS(f"已备份：{bak['path']}"))

        stats = {
            "current": 0,
            "history": 0,
            "unassigned": 0,
            "skipped": 0,
            "manifests": 0,
            "orphans_cleaned": 0,
            "orphans_purged": 0,
        }
        notes: list[str] = []
        flat_re = re.compile(r"^templates/[^/]+\.[A-Za-z0-9]+$", re.I)

        bound_ids: set[int] = set()
        for lf in LibraryFile.objects.filter(category=LibraryFile.CATEGORY_TEMPLATE, deleted_at__isnull=True):
            if lf.library_tasks.exists():
                bound_ids.add(lf.pk)

        def _relocate(lf: LibraryFile, task, slot: str) -> None:
            if dry:
                notes.append(f"file#{lf.pk} -> task#{getattr(task, 'pk', None)} slot={slot}")
                stats[slot if slot in stats else "skipped"] += 1
                return
            ok = relocate_library_template_file(lf, task=task, slot=slot, write_manifest=False)
            if ok:
                stats[slot if slot in ("current", "history") else "unassigned"] += 1
            else:
                stats["skipped"] += 1

        with transaction.atomic():
            # 1) 当前仍绑定在任务上的文件 -> current
            for lf in LibraryFile.objects.filter(
                category=LibraryFile.CATEGORY_TEMPLATE, deleted_at__isnull=True
            ).order_by("id"):
                if not library_file_exists_on_disk(lf):
                    stats["skipped"] += 1
                    continue
                if lf.pk not in bound_ids:
                    continue
                task = pick_primary_task_for_template_file(lf)
                _relocate(lf, task, "current")

            # 2) 仅在绑定历史中、当前未挂任务的文件 -> 对应任务的 history
            for hist in LibraryTaskTemplateBindingHistory.objects.select_related(
                "library_task", "library_file"
            ).order_by("id"):
                lf = hist.library_file
                if lf is None or lf.deleted_at is not None:
                    continue
                if lf.pk in bound_ids:
                    continue
                if not library_file_exists_on_disk(lf):
                    stats["skipped"] += 1
                    continue
                _relocate(lf, hist.library_task, "history")

            # 3) 仍未迁移的扁平文件 -> _unassigned/current
            for lf in LibraryFile.objects.filter(
                category=LibraryFile.CATEGORY_TEMPLATE, deleted_at__isnull=True
            ).order_by("id"):
                if not library_file_exists_on_disk(lf):
                    continue
                rel = (lf.relative_path or "").replace("\\", "/")
                if not flat_re.match(rel):
                    continue
                _relocate(lf, None, "current")

            if not dry:
                task_ids = set(
                    LibraryTask.objects.filter(
                        library_files__category=LibraryFile.CATEGORY_TEMPLATE,
                        library_files__deleted_at__isnull=True,
                    )
                    .distinct()
                    .values_list("id", flat=True)
                )
                for tid in sorted(task_ids):
                    task = LibraryTask.objects.filter(pk=tid).first()
                    if task:
                        _write_manifest_for_task(task)
                        stats["manifests"] += 1

                from pathlib import Path
                from django.conf import settings

                templates_root = Path(settings.FILE_LIBRARY_TEMPLATE_DIR)
                cleaned = 0
                for fp in templates_root.iterdir():
                    if not fp.is_file():
                        continue
                    rel_flat = f"templates/{fp.name}"
                    if LibraryFile.objects.filter(relative_path=rel_flat).exists():
                        continue
                    if LibraryFile.objects.filter(
                        category=LibraryFile.CATEGORY_TEMPLATE,
                        relative_path__endswith=f"/{fp.name}",
                        deleted_at__isnull=True,
                    ).exists():
                        fp.unlink(missing_ok=True)
                        cleaned += 1
                stats["orphans_cleaned"] = cleaned

            if options.get("purge_orphan_flat") and not dry:
                from pathlib import Path
                from django.conf import settings

                templates_root = Path(settings.FILE_LIBRARY_TEMPLATE_DIR)
                purged = 0
                for fp in templates_root.iterdir():
                    if not fp.is_file():
                        continue
                    if LibraryFile.objects.filter(
                        category=LibraryFile.CATEGORY_TEMPLATE,
                        relative_path__endswith=f"/{fp.name}",
                        deleted_at__isnull=True,
                    ).exists():
                        continue
                    fp.unlink(missing_ok=True)
                    purged += 1
                stats["orphans_purged"] = purged

        for line in notes[:50]:
            self.stdout.write(line)
        if len(notes) > 50:
            self.stdout.write(f"... 另有 {len(notes) - 50} 条")
        self.stdout.write(
            self.style.SUCCESS(
                f"完成：current={stats['current']} history={stats['history']} "
                f"unassigned={stats['unassigned']} skipped={stats['skipped']} "
                f"manifests={stats['manifests']} orphans_cleaned={stats['orphans_cleaned']} "
                f"orphans_purged={stats['orphans_purged']}"
            )
        )
