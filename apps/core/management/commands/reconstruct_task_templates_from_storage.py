"""从 templates/ 分层目录与 _task.json 清单还原任务模板库（数据库丢失后的应急恢复）。"""

from __future__ import annotations

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core import pipeline_service
from apps.core.library_file_service import attach_files_to_tasks
from apps.core.library_task_folder_service import create_task_folder
from apps.core.models import LibraryFile, LibraryTask
from apps.core.template_storage_service import MANIFEST_FILENAME, STORAGE_SCHEMA


class Command(BaseCommand):
    help = "扫描 templates/**/_task.json，还原 LibraryTaskFolder / LibraryTask / LibraryFile 绑定。"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--owner", default="test", help="新建任务时的 created_by 用户名")

    def handle(self, *args, **options):
        dry = bool(options.get("dry_run"))
        owner = get_user_model().objects.filter(username=options.get("owner"), is_active=True).first()
        if owner is None:
            raise CommandError(f"用户 {options.get('owner')} 不存在")

        from django.conf import settings

        templates_root = Path(settings.FILE_LIBRARY_TEMPLATE_DIR)
        manifests = sorted(templates_root.rglob(MANIFEST_FILENAME))
        if not manifests:
            raise CommandError(f"未找到任何 {MANIFEST_FILENAME}，请先运行 migrate_template_storage_layout")

        stats = {"tasks": 0, "files": 0, "binds": 0, "folders": 0}

        with transaction.atomic():
            for mf in manifests:
                try:
                    data = json.loads(mf.read_text(encoding="utf-8"))
                except Exception as exc:
                    self.stdout.write(self.style.WARNING(f"跳过无效清单 {mf}: {exc}"))
                    continue
                if data.get("schema") != STORAGE_SCHEMA:
                    self.stdout.write(self.style.WARNING(f"跳过非本 schema 清单: {mf}"))
                    continue

                task = self._ensure_task(data, owner, dry=dry, stats=stats)
                if task is None:
                    continue

                for slot in ("current", "history", "auxiliary"):
                    for ent in (data.get("files") or {}).get(slot) or []:
                        rel = str(ent.get("relative_path") or "").strip()
                        if not rel:
                            continue
                        lf = self._ensure_library_file(ent, rel, owner, dry=dry)
                        if lf is None:
                            continue
                        stats["files"] += 1
                        if slot == "current" and not dry:
                            attach_files_to_tasks([lf.pk], [task.pk], owner)
                            stats["binds"] += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"完成：folders={stats['folders']} tasks={stats['tasks']} "
                f"files={stats['files']} binds={stats['binds']}"
            )
        )

    def _ensure_task(self, data: dict, owner, *, dry: bool, stats: dict):
        code = str(data.get("task_code") or "").strip()
        if not code:
            return None
        task = LibraryTask.objects.filter(code=code).first()
        if task:
            return task
        if dry:
            stats["tasks"] += 1
            return None
        folder = None
        chain = data.get("folder_chain") or []
        parent = None
        for name in chain:
            from apps.core.models import LibraryTaskFolder

            row = LibraryTaskFolder.objects.filter(parent=parent, name=name, is_active=True).first()
            if row is None:
                row, err = create_task_folder(owner, name=str(name), parent=parent)
                if err or row is None:
                    raise CommandError(f"无法创建文件夹「{name}」：{err}")
                stats["folders"] += 1
            parent = row
        folder = parent
        task = LibraryTask.objects.create(
            code=code,
            name=str(data.get("task_name") or code)[:128],
            output_target=str(data.get("output_target") or LibraryTask.OUTPUT_SITE_RECORD),
            task_folder=folder if data.get("output_target") == LibraryTask.OUTPUT_REPORT else None,
            created_by=owner,
        )
        stats["tasks"] += 1
        return task

    def _ensure_library_file(self, ent: dict, rel: str, owner, *, dry: bool):
        lf = LibraryFile.objects.filter(relative_path=rel, category=LibraryFile.CATEGORY_TEMPLATE).first()
        if lf:
            return lf
        try:
            abs_p = pipeline_service.library_absolute_path(rel)
        except ValueError:
            return None
        if not abs_p.is_file():
            return None
        if dry:
            return None
        lf = LibraryFile.objects.create(
            original_name=str(ent.get("original_name") or abs_p.name),
            relative_path=rel.replace("\\", "/"),
            category=LibraryFile.CATEGORY_TEMPLATE,
            size=int(ent.get("size") or abs_p.stat().st_size),
            created_by=owner,
        )
        return lf
