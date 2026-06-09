"""
任务模板文件分层磁盘存储。

目录约定（相对 FILE_LIBRARY_ROOT）::

    templates/{检测类型}/{设备类型}/report/{task_code}/current/{uuid}.ext
    templates/{检测类型}/{设备类型}/report/{task_code}/history/{uuid}.ext
    templates/{检测类型}/{设备类型}/site/{task_code}/current/{uuid}.ext
    templates/.../site/{task_code}/auxiliary/{uuid}_frontend.json
    templates/_unassigned/current/{uuid}.ext

每层任务目录含 ``_task.json`` 清单，数据库丢失时可据此还原任务模板库绑定关系。
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.core import pipeline_service
from apps.core.library_task_template_binding_service import infer_template_file_role
from apps.core.models import LibraryFile, LibraryTask

STORAGE_SCHEMA = "task_template_storage/v1"
MANIFEST_FILENAME = "_task.json"
UNASSIGNED_SEGMENT = "_unassigned"
VALID_SLOTS = frozenset({"current", "history", "auxiliary"})


def slugify_storage_segment(name: str) -> str:
    s = (name or "").strip()
    if not s:
        return "unnamed"
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", s)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return (s[:100] or "unnamed")


def resolve_primary_report_for_site(site: LibraryTask) -> LibraryTask | None:
    return (
        site.report_target_tasks.filter(output_target=LibraryTask.OUTPUT_REPORT)
        .order_by("id")
        .first()
    )


def infer_template_storage_slot(original_name: str, *, explicit: str | None = None) -> str:
    if explicit and explicit in VALID_SLOTS:
        return explicit
    low = (original_name or "").lower()
    if low.endswith("_frontend.json") or low.endswith("_matrix.json"):
        return "auxiliary"
    return "current"


def task_storage_base_segments(task: LibraryTask) -> list[str]:
    segments: list[str] = []
    if task.output_target == LibraryTask.OUTPUT_REPORT:
        folder = task.task_folder
        if folder:
            for anc in folder.ancestors_chain():
                segments.append(slugify_storage_segment(anc.name))
        else:
            segments.append(UNASSIGNED_SEGMENT)
        segments.append("report")
    else:
        report = resolve_primary_report_for_site(task)
        folder = report.task_folder if report else None
        if folder:
            for anc in folder.ancestors_chain():
                segments.append(slugify_storage_segment(anc.name))
        else:
            segments.append(UNASSIGNED_SEGMENT)
        segments.append("site")
    segments.append(slugify_storage_segment(task.code or f"task-{task.pk}"))
    return segments


def folder_chain_labels(task: LibraryTask) -> list[str]:
    if task.output_target == LibraryTask.OUTPUT_REPORT:
        folder = task.task_folder
    else:
        report = resolve_primary_report_for_site(task)
        folder = report.task_folder if report else None
    if not folder:
        return []
    return [anc.name for anc in folder.ancestors_chain()]


def build_template_relative_path(
    *,
    disk_name: str,
    task: LibraryTask | None = None,
    slot: str = "current",
) -> str:
    slot = infer_template_storage_slot("", explicit=slot)
    parts = ["templates"]
    if task is None:
        parts.extend([UNASSIGNED_SEGMENT, slot])
    else:
        parts.extend(task_storage_base_segments(task))
        parts.append(slot)
    parts.append(disk_name)
    return "/".join(parts)


def task_storage_manifest_path(task: LibraryTask, slot: str = "current") -> Path:
    rel = build_template_relative_path(disk_name=MANIFEST_FILENAME, task=task, slot=slot)
    return pipeline_service.library_absolute_path(rel)


def _disk_name_from_library_file(lf: LibraryFile) -> str:
    name = (lf.relative_path or "").rsplit("/", 1)[-1]
    if name:
        return name
    ext = Path(lf.original_name or "").suffix.lower()
    return f"{lf.pk}{ext}" if ext else str(lf.pk)


def _write_manifest_for_task(task: LibraryTask) -> None:
    """写入/更新任务目录下的 _task.json（放在 current 层）。"""
    try:
        manifest_abs = task_storage_manifest_path(task, slot="current")
    except ValueError:
        return
    manifest_abs.parent.mkdir(parents=True, exist_ok=True)

    current_files: list[dict[str, Any]] = []
    history_files: list[dict[str, Any]] = []
    aux_files: list[dict[str, Any]] = []

    task_dir_parts = task_storage_base_segments(task)
    templates_root = Path(settings.FILE_LIBRARY_TEMPLATE_DIR)

    for slot, bucket in (("current", current_files), ("history", history_files), ("auxiliary", aux_files)):
        slot_dir = templates_root.joinpath(*task_dir_parts, slot)
        if not slot_dir.is_dir():
            continue
        for fp in sorted(slot_dir.iterdir()):
            if not fp.is_file() or fp.name == MANIFEST_FILENAME:
                continue
            rel = str(Path("templates").joinpath(*task_dir_parts, slot, fp.name)).replace("\\", "/")
            row = LibraryFile.objects.filter(
                category=LibraryFile.CATEGORY_TEMPLATE,
                relative_path=rel,
                deleted_at__isnull=True,
            ).first()
            entry: dict[str, Any] = {
                "relative_path": rel,
                "disk_name": fp.name,
                "size": fp.stat().st_size,
            }
            if row:
                entry["library_file_id"] = row.pk
                entry["original_name"] = row.original_name
                role = infer_template_file_role(row)
                if role:
                    entry["role"] = role
            else:
                entry["original_name"] = fp.name
            bucket.append(entry)

    site_codes: list[str] = []
    if task.output_target == LibraryTask.OUTPUT_REPORT:
        site_codes = list(
            task.report_source_tasks.filter(output_target=LibraryTask.OUTPUT_SITE_RECORD)
            .order_by("code")
            .values_list("code", flat=True)
        )
        if not site_codes:
            site_codes = list(
                task.report_target_tasks.filter(output_target=LibraryTask.OUTPUT_SITE_RECORD)
                .order_by("code")
                .values_list("code", flat=True)
            )

    report_codes: list[str] = []
    if task.output_target == LibraryTask.OUTPUT_SITE_RECORD:
        report_codes = list(
            task.report_target_tasks.filter(output_target=LibraryTask.OUTPUT_REPORT)
            .order_by("code")
            .values_list("code", flat=True)
        )

    payload = {
        "schema": STORAGE_SCHEMA,
        "updated_at": timezone.now().isoformat(),
        "task_id": task.pk,
        "task_code": task.code,
        "task_name": task.name,
        "output_target": task.output_target,
        "task_folder_id": task.task_folder_id,
        "folder_chain": folder_chain_labels(task),
        "report_source_task_codes": site_codes,
        "report_target_task_codes": report_codes,
        "storage_segments": task_storage_base_segments(task),
        "files": {
            "current": current_files,
            "history": history_files,
            "auxiliary": aux_files,
        },
    }
    manifest_abs.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def relocate_library_template_file(
    lf: LibraryFile,
    *,
    task: LibraryTask | None,
    slot: str | None = None,
    write_manifest: bool = True,
) -> bool:
    """将模板文件移动到分层目录并更新 relative_path。"""
    if lf.category != LibraryFile.CATEGORY_TEMPLATE:
        return False
    slot_name = infer_template_storage_slot(lf.original_name or "", explicit=slot)
    disk_name = _disk_name_from_library_file(lf)
    new_rel = build_template_relative_path(disk_name=disk_name, task=task, slot=slot_name)
    try:
        old_abs = pipeline_service.library_absolute_path(lf.relative_path)
        new_abs = pipeline_service.library_absolute_path(new_rel)
    except ValueError:
        return False

    if old_abs.resolve() == new_abs.resolve():
        if write_manifest and task is not None:
            _write_manifest_for_task(task)
        return True

    new_abs.parent.mkdir(parents=True, exist_ok=True)
    if old_abs.is_file():
        if new_abs.exists():
            new_abs.unlink()
        shutil.move(str(old_abs), str(new_abs))
    elif not new_abs.is_file():
        return False

    lf.relative_path = new_rel
    lf.save(update_fields=["relative_path"])
    if write_manifest and task is not None:
        _write_manifest_for_task(task)
    return True


def archive_template_file_for_task(lf: LibraryFile, task: LibraryTask) -> bool:
    """绑定轮换时：将旧模板移入该任务目录下的 history/。"""
    return relocate_library_template_file(lf, task=task, slot="history", write_manifest=True)


def relocate_template_files_for_task(task: LibraryTask, *, slot: str = "current") -> int:
    """将任务已绑定的全部模板文件迁入对应分层目录。"""
    moved = 0
    for lf in task.library_files.filter(category=LibraryFile.CATEGORY_TEMPLATE, deleted_at__isnull=True):
        if relocate_library_template_file(lf, task=task, slot=slot):
            moved += 1
    return moved


def pick_primary_task_for_template_file(lf: LibraryFile) -> LibraryTask | None:
    tasks = list(
        lf.library_tasks.select_related("task_folder").order_by("output_target", "id")
    )
    if not tasks:
        return None
    reports = [t for t in tasks if t.output_target == LibraryTask.OUTPUT_REPORT]
    return reports[0] if reports else tasks[0]


def infer_storage_slot_for_file(lf: LibraryFile, task: LibraryTask | None) -> str:
    if infer_template_storage_slot(lf.original_name or "") == "auxiliary":
        return "auxiliary"
    if task is None:
        return "current"
    from apps.core.models import LibraryTaskTemplateBindingHistory

    if LibraryTaskTemplateBindingHistory.objects.filter(
        library_file=lf, library_task=task
    ).exists() and not lf.library_tasks.filter(pk=task.pk).exists():
        return "history"
    return "current"
