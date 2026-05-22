"""
启动时 / 管理命令：校验文件库记录在 media 中是否有对应磁盘文件。

media 目录未必与代码一同部署；DB 中仍可能存在「有记录无文件」的条目，会导致预览/下载/OCR 等操作失败。
缺失磁盘文件时：将 LibraryFile 移入回收站（软删）；磁盘存在则保持原状。
卡住的 OCR 任务（源文件全部缺失）标记为失败。
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.core import pipeline_service
from apps.core.library_file_service import soft_delete_library_file
from apps.core.models import LibraryFile, LibraryOCRProcessTask, LibraryProject

logger = logging.getLogger(__name__)


def library_file_exists_on_disk(lf: LibraryFile) -> bool:
    """与 LibraryTaskTemplateBindingHistory.file_still_available 一致：仅检查磁盘。"""
    try:
        path = pipeline_service.library_absolute_path(lf.relative_path)
        return path.is_file()
    except (ValueError, OSError):
        return False


def should_run_startup_media_integrity() -> bool:
    """是否在本次进程启动时执行 media 完整性校验。"""
    if not getattr(settings, "LIBRARY_MEDIA_INTEGRITY_ON_STARTUP", True):
        return False
    if os.environ.get("LIBRARY_MEDIA_INTEGRITY_SKIP", "").lower() in ("1", "true", "yes"):
        return False
    argv = sys.argv
    skip_commands = (
        "migrate",
        "makemigrations",
        "test",
        "shell",
        "collectstatic",
        "check_library_media",
        "flush",
        "loaddata",
        "dumpdata",
    )
    if any(cmd in argv for cmd in skip_commands):
        return False
    # runserver 热重载：仅在子进程执行一次，避免父进程重复扫描
    if "runserver" in argv:
        return os.environ.get("RUN_MAIN") == "true"
    return True


def reconcile_library_files_missing_on_disk(*, dry_run: bool = False) -> dict[str, int]:
    """
    扫描所有未在回收站中的 LibraryFile；磁盘不存在则软删。
    磁盘存在的不改动。
    """
    missing = 0
    ok = 0
    for lf in (
        LibraryFile.objects.only("id", "relative_path", "deleted_at", "original_name")
        .order_by("id")
        .iterator(chunk_size=500)
    ):
        if library_file_exists_on_disk(lf):
            ok += 1
            continue
        missing += 1
        if dry_run:
            continue
        soft_delete_library_file(lf)
    return {"files_checked": missing + ok, "files_ok": ok, "files_soft_deleted": missing}


def reconcile_ocr_tasks_with_missing_sources(*, dry_run: bool = False) -> dict[str, int]:
    """待处理/处理中的 OCR 任务若源文件均不可读，标记为失败。"""
    failed = 0
    checked = 0
    statuses = (
        LibraryOCRProcessTask.STATUS_PENDING,
        LibraryOCRProcessTask.STATUS_RUNNING,
    )
    for task in LibraryOCRProcessTask.objects.filter(status__in=statuses).order_by("id").iterator():
        checked += 1
        source_ids = [int(x) for x in (task.source_file_ids or []) if str(x).isdigit()]
        if not source_ids:
            continue
        rows = list(
            LibraryFile.objects.filter(pk__in=source_ids).only("id", "relative_path", "deleted_at")
        )
        any_readable = False
        for lf in rows:
            if lf.deleted_at:
                continue
            if library_file_exists_on_disk(lf):
                any_readable = True
                break
        if any_readable:
            continue
        failed += 1
        if dry_run:
            continue
        task.status = LibraryOCRProcessTask.STATUS_FAILED
        task.error_message = "启动校验：OCR 源文件在媒体目录中不存在或已移入回收站"
        task.save(update_fields=["status", "error_message", "updated_at"])
    return {"ocr_tasks_checked": checked, "ocr_tasks_failed": failed}


def summarize_projects_after_reconcile() -> dict[str, int]:
    """统计各启用项目是否仍有关联且磁盘存在的文件（仅日志，不改项目 is_active）。"""
    active_projects = 0
    projects_with_usable_files = 0
    for project in LibraryProject.objects.filter(is_active=True).only("id").iterator():
        active_projects += 1
        has_usable = False
        for lf in project.library_files.only("id", "relative_path", "deleted_at").iterator():
            if lf.deleted_at:
                continue
            if library_file_exists_on_disk(lf):
                has_usable = True
                break
        if has_usable:
            projects_with_usable_files += 1
    return {
        "active_projects": active_projects,
        "projects_with_usable_files": projects_with_usable_files,
        "projects_without_usable_files": active_projects - projects_with_usable_files,
    }


def run_library_media_integrity(*, dry_run: bool = False) -> dict[str, Any]:
    """
    执行完整 media 校验。返回统计字典，供启动日志与管理命令使用。
    """
    pipeline_service.ensure_file_library_dirs()
    file_stats = reconcile_library_files_missing_on_disk(dry_run=dry_run)
    ocr_stats = reconcile_ocr_tasks_with_missing_sources(dry_run=dry_run)
    project_stats = {} if dry_run else summarize_projects_after_reconcile()
    return {
        "dry_run": dry_run,
        "ran_at": timezone.now().isoformat(timespec="seconds"),
        **file_stats,
        **ocr_stats,
        **project_stats,
    }


def run_library_media_integrity_on_startup() -> None:
    """在 AppConfig.ready 中调用；失败仅记日志，不阻断启动。"""
    if not should_run_startup_media_integrity():
        return
    try:
        stats = run_library_media_integrity(dry_run=False)
        logger.info("文件库 media 完整性校验完成: %s", stats)
    except Exception:
        logger.exception("文件库 media 完整性校验失败")
