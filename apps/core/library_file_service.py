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


def save_library_binary_uploads(
    user,
    uploaded_files,
    category: str,
    *,
    link_entity: str = "",
    link_object_id: Optional[int] = None,
    project_ids: Optional[List[int]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """
    将上传的文件写入磁盘并创建 LibraryFile。

    Returns:
        created: 每项含 id, original_name, relative_path, size, category, created_at (ISO)
        skipped: 每项含 filename, reason（如 empty）
    """
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
    # Keep explicit project bindings even if a project is currently inactive.
    # taskNo-based APIs resolve a concrete project from business data, and users
    # still need historical files to remain associated with that project.
    project_map = {p.pk: p for p in LibraryProject.objects.filter(pk__in=clean_project_ids)}
    dest_dir, rel_prefix = library_disk_dir_and_rel_prefix(category)
    dest_dir.mkdir(parents=True, exist_ok=True)
    created: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    for f in uploaded_files:
        raw = f.read()
        name = getattr(f, "name", "") or ""
        if not raw:
            skipped.append({"filename": name, "reason": "empty"})
            continue
        sha256 = hashlib.sha256(raw).hexdigest()
        safe = safe_library_basename(name)
        ext = Path(safe).suffix.lower()
        if category == LibraryFile.CATEGORY_UPLOAD:
            existing = LibraryFile.objects.filter(
                category=LibraryFile.CATEGORY_UPLOAD,
                content_sha256=sha256,
            ).first()
            if existing:
                # OCR 文件按 hash 去重；若命中历史文件，仍需补齐当前项目关联。
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
            disk_name = f"{sha256}{ext}" if ext else sha256
            # OCR 列表显示名统一为 hash 文件名，避免“同名不同文件”歧义。
            display_name = disk_name
        else:
            uid = uuid.uuid4().hex
            disk_name = f"{uid}_{safe}"
            display_name = safe
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
    """将文件关联到任务（与 attach_files_to_projects 相同模式，支持全部分类）。"""
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
