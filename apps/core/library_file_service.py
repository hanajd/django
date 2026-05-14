"""文件库二进制上传与下载响应（Web 与 API 共用）。"""
import hashlib
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.http import FileResponse, Http404
from django.utils import timezone

from apps.core import pipeline_service
from apps.core.models import (
    LibraryFile,
    LibraryFileProject,
    LibraryFileTask,
    LibraryProject,
    LibraryTask,
)


def safe_library_basename(name: str) -> str:
    base = os.path.basename(name.replace("\\", "/"))
    return base[:240] if base else "unnamed"


def library_disk_dir_and_rel_prefix(category: str) -> Tuple[Path, str]:
    if category == LibraryFile.CATEGORY_UPLOAD:
        return Path(settings.FILE_LIBRARY_UPLOAD_DIR), "uploads"
    if category == LibraryFile.CATEGORY_JSON:
        return Path(settings.FILE_LIBRARY_JSON_DIR), "json"
    if category == LibraryFile.CATEGORY_TEMPLATE:
        return Path(settings.FILE_LIBRARY_TEMPLATE_DIR), "templates"
    if category == LibraryFile.CATEGORY_SITE_RECORD:
        return Path(settings.FILE_LIBRARY_SITE_RECORD_DIR), "site_records"
    if category == LibraryFile.CATEGORY_REPORT:
        return Path(settings.FILE_LIBRARY_REPORT_DIR), "reports"
    if category == LibraryFile.CATEGORY_ATTACHMENT:
        return Path(settings.FILE_LIBRARY_ATTACHMENT_DIR), "attachments"
    if category == LibraryFile.CATEGORY_INSPECTION_SUBMIT:
        return Path(settings.FILE_LIBRARY_INSPECTION_SUBMIT_DIR), "inspection_submits"
    raise ValueError(f"unsupported library category: {category}")


def soft_delete_library_file(lf: LibraryFile) -> None:
    """移入回收站（仅标记 deleted_at，不删磁盘）。"""
    if lf.deleted_at is not None:
        return
    lf.deleted_at = timezone.now()
    lf.save(update_fields=["deleted_at"])


def hard_delete_library_file_disk_and_row(lf: LibraryFile) -> None:
    """永久删除：删除磁盘文件并删除数据库行（可用 all_objects 取到的实例调用）。"""
    try:
        p = pipeline_service.library_absolute_path(lf.relative_path)
        if p.is_file():
            p.unlink()
    except (ValueError, OSError):
        pass
    lf.delete()


def save_library_binary_uploads(
    user,
    uploaded_files,
    category: str,
    *,
    link_entity: str = "",
    link_object_id: Optional[int] = None,
    project_ids: Optional[List[int]] = None,
    enforce_storage_quota: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """
    将上传的文件写入磁盘并创建 LibraryFile。

    ``enforce_storage_quota``：为 False 时不校验用户文件库配额（如系统自动生成的报告 PDF）。

    Returns:
        created: 每项含 id, original_name, relative_path, size, category, created_at (ISO)
        skipped: 每项含 filename, reason（如 empty / quota_exceeded）
    """
    from apps.core.library_access import (
        library_user_file_library_quota_bytes,
        library_user_file_library_usage_bytes,
    )

    pipeline_service.ensure_file_library_dirs()
    clean_project_ids: List[int] = []
    for x in (project_ids or []):
        try:
            i = int(x)
        except Exception:
            continue
        if i > 0:
            clean_project_ids.append(i)
    clean_project_ids = sorted(set(clean_project_ids))
    project_map = {p.pk: p for p in LibraryProject.objects.filter(pk__in=clean_project_ids)}
    dest_dir, rel_prefix = library_disk_dir_and_rel_prefix(category)
    dest_dir.mkdir(parents=True, exist_ok=True)
    created: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    staged: List[Tuple[str, bytes]] = []
    for f in uploaded_files:
        raw = f.read()
        name = getattr(f, "name", "") or ""
        if not raw:
            skipped.append({"filename": name, "reason": "empty"})
            continue
        staged.append((name, raw))

    usage_base = (
        library_user_file_library_usage_bytes(user)
        if (user is not None and enforce_storage_quota)
        else 0
    )
    added_in_batch = 0

    for name, raw in staged:
        sha256 = hashlib.sha256(raw).hexdigest()
        safe = safe_library_basename(name)
        ext = Path(safe).suffix.lower()
        if category == LibraryFile.CATEGORY_UPLOAD:
            existing = LibraryFile.objects.filter(
                category=LibraryFile.CATEGORY_UPLOAD,
                content_sha256=sha256,
            ).first()
            if existing:
                if project_map:
                    for pid in project_map.keys():
                        LibraryFileProject.objects.get_or_create(
                            library_file=existing,
                            project_id=pid,
                            defaults={"created_by": user},
                        )
                created.append(
                    {
                        "id": existing.pk,
                        "original_name": existing.original_name,
                        "relative_path": existing.relative_path,
                        "size": existing.size,
                        "category": existing.category,
                        "link_entity": existing.link_entity,
                        "link_object_id": existing.link_object_id,
                        "project_ids": list(project_map.keys()),
                        "created_at": existing.created_at.isoformat() if existing.created_at else "",
                        "reused_existing": True,
                    }
                )
                continue
            trashed_same = (
                LibraryFile.all_objects.filter(
                    category=LibraryFile.CATEGORY_UPLOAD,
                    content_sha256=sha256,
                    deleted_at__isnull=False,
                )
                .order_by("-deleted_at")
                .first()
            )
            if trashed_same is not None:
                trashed_same.deleted_at = None
                trashed_same.save(update_fields=["deleted_at"])
                if project_map:
                    for pid in project_map.keys():
                        LibraryFileProject.objects.get_or_create(
                            library_file=trashed_same,
                            project_id=pid,
                            defaults={"created_by": user},
                        )
                added_in_batch += int(trashed_same.size or 0)
                created.append(
                    {
                        "id": trashed_same.pk,
                        "original_name": trashed_same.original_name,
                        "relative_path": trashed_same.relative_path,
                        "size": trashed_same.size,
                        "category": trashed_same.category,
                        "link_entity": trashed_same.link_entity,
                        "link_object_id": trashed_same.link_object_id,
                        "project_ids": list(project_map.keys()),
                        "created_at": trashed_same.created_at.isoformat() if trashed_same.created_at else "",
                        "reused_existing": True,
                    }
                )
                continue
            disk_name = f"{sha256}{ext}" if ext else sha256
            display_name = disk_name
        else:
            uid = uuid.uuid4().hex
            disk_name = f"{uid}_{safe}"
            display_name = safe

        if user is not None and enforce_storage_quota:
            cap = library_user_file_library_quota_bytes(user)
            if cap is not None:
                used_so_far = usage_base + added_in_batch
                if used_so_far + len(raw) > cap:
                    skipped.append({"filename": name, "reason": "quota_exceeded"})
                    continue

        rel = f"{rel_prefix}/{disk_name}"
        abs_p = dest_dir / disk_name
        if not abs_p.exists():
            abs_p.write_bytes(raw)
        lf = LibraryFile.objects.create(
            original_name=display_name,
            relative_path=rel,
            category=category,
            content_sha256=sha256,
            size=len(raw),
            created_by=user,
            link_entity=link_entity or "",
            link_object_id=link_object_id,
        )
        added_in_batch += len(raw)
        if project_map:
            for pid in project_map.keys():
                LibraryFileProject.objects.get_or_create(
                    library_file=lf,
                    project_id=pid,
                    defaults={"created_by": user},
                )
        created.append(
            {
                "id": lf.pk,
                "original_name": lf.original_name,
                "relative_path": lf.relative_path,
                "size": lf.size,
                "category": lf.category,
                "link_entity": lf.link_entity,
                "link_object_id": lf.link_object_id,
                "project_ids": list(project_map.keys()),
                "created_at": lf.created_at.isoformat(),
            }
        )
    return created, skipped


def parse_project_ids(raw_values: List[str]) -> List[int]:
    out: List[int] = []
    for x in raw_values:
        try:
            i = int((x or "").strip())
        except Exception:
            continue
        if i > 0:
            out.append(i)
    # 保序去重
    uniq = []
    seen = set()
    for i in out:
        if i in seen:
            continue
        seen.add(i)
        uniq.append(i)
    return uniq


def attach_files_to_projects(file_ids: List[int], project_ids: List[int], user=None) -> None:
    if not file_ids or not project_ids:
        return
    valid_projects = list(LibraryProject.objects.filter(pk__in=project_ids).values_list("id", flat=True))
    if not valid_projects:
        return
    for fid in file_ids:
        for pid in valid_projects:
            LibraryFileProject.objects.get_or_create(
                library_file_id=fid,
                project_id=pid,
                defaults={"created_by": user},
            )


def detach_files_from_projects(file_ids: List[int], project_ids: List[int]) -> None:
    if not file_ids or not project_ids:
        return
    LibraryFileProject.objects.filter(library_file_id__in=file_ids, project_id__in=project_ids).delete()


def attach_files_to_tasks(file_ids: List[int], task_ids: List[int], user=None) -> None:
    """将文件关联到任务模板（业务上仅应关联「模板」分类；其它分类请使用 attach_files_to_projects）。"""
    if not file_ids or not task_ids:
        return
    valid_tasks = list(LibraryTask.objects.filter(pk__in=task_ids).values_list("id", flat=True))
    if not valid_tasks:
        return
    for fid in file_ids:
        for tid in valid_tasks:
            LibraryFileTask.objects.get_or_create(
                library_file_id=fid,
                library_task_id=tid,
                defaults={"created_by": user},
            )


def detach_files_from_tasks(file_ids: List[int], task_ids: List[int]) -> None:
    if not file_ids or not task_ids:
        return
    LibraryFileTask.objects.filter(library_file_id__in=file_ids, library_task_id__in=task_ids).delete()


def library_file_download_response(lf: LibraryFile) -> FileResponse:
    """构建文件库条目的下载响应（附件下载）。"""
    try:
        path = pipeline_service.library_absolute_path(lf.relative_path)
    except ValueError as e:
        raise Http404(str(e)) from e
    if not path.is_file():
        raise Http404("文件不存在")
    ext = path.suffix.lower()
    ctype = "application/octet-stream"
    if ext in (".png",):
        ctype = "image/png"
    elif ext in (".jpg", ".jpeg"):
        ctype = "image/jpeg"
    elif ext in (".gif",):
        ctype = "image/gif"
    elif ext in (".webp",):
        ctype = "image/webp"
    elif ext in (".pdf",):
        ctype = "application/pdf"
    elif ext in (".json",):
        ctype = "application/json"
    elif ext in (".md", ".markdown"):
        ctype = "text/markdown; charset=utf-8"
    return FileResponse(
        path.open("rb"),
        as_attachment=True,
        filename=safe_library_basename(lf.original_name),
        content_type=ctype,
    )


def convert_template_word_to_temp_pdf(lf: LibraryFile, user_id: int) -> Tuple[str, str]:
    """
    将模板中的 Word 文件转换为临时 PDF。
    返回 (relative_path, output_name)。
    """
    ext = Path(lf.original_name).suffix.lower()
    if ext not in (".doc", ".docx"):
        raise ValueError("only .doc/.docx can be converted to pdf")
    src = pipeline_service.library_absolute_path(lf.relative_path)
    if not src.is_file():
        raise FileNotFoundError("source word file not found")
    sha_short = (lf.content_sha256 or "nohash")[:12]
    out_name = f"tpl_{lf.pk}_u{user_id}_{sha_short}.pdf"
    rel = f"temp/template_pdf/{out_name}"
    out_path = pipeline_service.library_absolute_path(rel)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 如果已存在则复用，避免重复转换。
    if out_path.is_file():
        return rel, out_name

    soffice = shutil.which("soffice")
    if not soffice:
        raise RuntimeError("soffice command not found")

    cmd = [
        soffice,
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(out_path.parent),
        str(src),
    ]
    env = os.environ.copy()
    lo_lib = "/usr/lib/libreoffice/program"
    old_ld = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = (
        f"{lo_lib}:{old_ld}" if old_ld else lo_lib
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "soffice conversion failed").strip())

    generated = out_path.parent / f"{src.stem}.pdf"
    if generated.is_file() and generated != out_path:
        generated.replace(out_path)
    if not out_path.is_file():
        raise RuntimeError("converted pdf not found")
    return rel, out_name
