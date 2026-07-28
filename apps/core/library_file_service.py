"""文件库二进制上传与下载响应（Web 与 API 共用）。"""
import hashlib
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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


# Linux 单文件名通常上限 255 字节（UTF-8）；预留 uuid 前缀等余量
LIBRARY_DISK_FILENAME_MAX_BYTES = 200


def safe_library_basename(name: str, *, max_bytes: int = LIBRARY_DISK_FILENAME_MAX_BYTES) -> str:
    """路径安全的展示用文件名，按 UTF-8 字节截断以适配磁盘。"""
    base = os.path.basename(name.replace("\\", "/")) or "unnamed"
    base = re.sub(r'[/\\:*?"<>|\r\n\x00-\x1f]', "_", base)
    if len(base.encode("utf-8")) <= max_bytes:
        return base
    ext = Path(base).suffix
    ext_b = ext.encode("utf-8")
    stem = Path(base).stem
    budget = max(1, max_bytes - len(ext_b))
    while stem:
        candidate = stem + ext
        if len(candidate.encode("utf-8")) <= max_bytes:
            return candidate
        stem = stem[:-1]
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]
    fallback = f"{digest}{ext}"
    return fallback if len(fallback.encode("utf-8")) <= max_bytes else digest[: max_bytes // 2]


def sanitize_library_original_filename_fragment(s: str, max_len: int = 120) -> str:
    """用户可见文件名片段：去路径非法字符，压缩空白，限制长度（与导出 PDF 片段规则一致）。"""
    s = (s or "").strip()
    s = re.sub(r'[/\\:*?"<>|\r\n\x00-\x1f]', "_", s)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if len(s) > max_len:
        s = s[:max_len].rstrip("_")
    return s or "x"


@dataclass(frozen=True)
class InspectionSubmitBatchStorage:
    """检测提交单次落盘：委托编号 / 时间戳批次目录。"""

    commission_code: str
    batch_timestamp: str


def library_commission_code_for_project(project) -> str:
    code = sanitize_library_original_filename_fragment(
        str(getattr(project, "code", "") or "").strip() or f"project_{getattr(project, 'pk', 0)}",
        64,
    )
    return code or "unknown"


def library_batch_timestamp(dt=None) -> str:
    ts = dt or timezone.now()
    local = timezone.localtime(ts)
    return local.strftime("%Y%m%d%H%M%S")


def make_inspection_submit_batch_storage(project) -> InspectionSubmitBatchStorage:
    return InspectionSubmitBatchStorage(
        commission_code=library_commission_code_for_project(project),
        batch_timestamp=library_batch_timestamp(),
    )


def inspection_submit_relative_path(
    batch: InspectionSubmitBatchStorage,
    filename: str,
    *,
    subdir: str = "",
) -> str:
    """相对 FILE_LIBRARY_ROOT 的路径：inspection_submits/{委托编号}/{时间戳}/[{subdir}/]{文件名}"""
    return library_batch_relative_path(
        "inspection_submits", batch, filename, subdir=subdir
    )


def site_record_relative_path(
    batch: InspectionSubmitBatchStorage,
    filename: str,
    *,
    subdir: str = "",
) -> str:
    """
    现场记录 PDF 磁盘路径：site_records/{委托编号}/{时间戳}/[{subdir}/]{文件名}。
    层级与 inspection_submits 一致，便于按项目/批次对照提交 JSON 与导出 PDF。
    """
    return library_batch_relative_path(
        "site_records", batch, filename, subdir=subdir
    )


def library_batch_relative_path(
    root_prefix: str,
    batch: InspectionSubmitBatchStorage,
    filename: str,
    *,
    subdir: str = "",
) -> str:
    """{root_prefix}/{委托编号}/{时间戳}/[{subdir}/]{文件名}"""
    parts = [
        (root_prefix or "").strip().strip("/"),
        batch.commission_code,
        batch.batch_timestamp,
    ]
    sub = (subdir or "").strip().strip("/")
    if sub:
        parts.append(sub)
    parts.append(safe_library_basename(filename))
    return "/".join(p for p in parts if p)


_BATCH_FOLDER_PATH_RE = re.compile(
    r"^(?:inspection_submits|site_records)/([^/]+)/(\d{14})(?:/|$)",
    re.I,
)


def parse_batch_storage_from_relative_path(relative_path: str) -> InspectionSubmitBatchStorage | None:
    """从 inspection_submits / site_records 批次目录路径解析委托编号与时间戳。"""
    rel = (relative_path or "").replace("\\", "/").strip().lstrip("/")
    m = _BATCH_FOLDER_PATH_RE.match(rel)
    if not m:
        return None
    code = (m.group(1) or "").strip()
    ts = (m.group(2) or "").strip()
    if not code or not ts:
        return None
    return InspectionSubmitBatchStorage(commission_code=code, batch_timestamp=ts)


def resolve_site_record_batch_storage(
    *,
    project,
    case=None,
    task_no: str = "",
    source_payload: dict | None = None,
    source_submit_relative_path: str | None = None,
    created_at=None,
) -> InspectionSubmitBatchStorage:
    """
    解析现场记录落盘批次目录：优先与同源检测提交 JSON 同批（同委托编号/时间戳），
    否则按提交 updatedAt/createdAt 或当前时间新建批次。
    """
    for rel in (source_submit_relative_path,):
        parsed = parse_batch_storage_from_relative_path(rel or "")
        if parsed is not None:
            return parsed
    if case is not None and project is not None:
        for lf in (
            LibraryFile.objects.filter(
                category=LibraryFile.CATEGORY_INSPECTION_SUBMIT,
                link_entity=LibraryFile.LINK_ENTITY_INSPECTION_CASE,
                link_object_id=int(case.pk),
                projects=project,
                deleted_at__isnull=True,
            )
            .order_by("-created_at", "-id")
            .distinct()
        ):
            rel = (lf.relative_path or "").replace("\\", "/")
            if not rel.lower().endswith(".json"):
                continue
            parsed = parse_batch_storage_from_relative_path(rel)
            if parsed is not None:
                return parsed
    if isinstance(source_payload, dict):
        for key in ("updatedAt", "createdAt", "submittedAt"):
            raw = source_payload.get(key)
            if not raw:
                continue
            try:
                from django.utils.dateparse import parse_datetime

                dt = parse_datetime(str(raw).replace("Z", "+00:00"))
            except Exception:
                dt = None
            if dt is not None:
                if timezone.is_naive(dt):
                    dt = timezone.make_aware(dt, timezone.get_current_timezone())
                return InspectionSubmitBatchStorage(
                    commission_code=library_commission_code_for_project(project),
                    batch_timestamp=library_batch_timestamp(dt),
                )
    if created_at is not None:
        return InspectionSubmitBatchStorage(
            commission_code=library_commission_code_for_project(project),
            batch_timestamp=library_batch_timestamp(created_at),
        )
    return make_inspection_submit_batch_storage(project)


def report_relative_path(
    disk_name: str,
    *,
    project=None,
    library_task=None,
    created_at=None,
) -> str:
    """
    报告 PDF 磁盘路径：reports/{委托编号}/{任务代码}/{年}/{月}/{日}/{HHMMSS}_{磁盘名}。
    便于在本地 media 目录按项目与时间浏览，与 inspection_submits 分层风格一致。
    """
    code = library_commission_code_for_project(project) if project is not None else "unknown"
    task_seg = ""
    if library_task is not None:
        task_seg = sanitize_library_original_filename_fragment(
            (getattr(library_task, "code", None) or getattr(library_task, "name", None) or ""),
            48,
        )
    ts = created_at or timezone.now()
    local = timezone.localtime(ts)
    day = local.strftime("%Y/%m/%d")
    hm = local.strftime("%H%M%S")
    parts = ["reports", code]
    if task_seg:
        parts.append(task_seg)
    parts.extend(day.split("/"))
    fname = f"{hm}_{safe_library_basename(disk_name)}"
    parts.append(fname)
    return "/".join(parts)


def library_media_url(relative_path: str) -> str:
    rel = (relative_path or "").replace("\\", "/").lstrip("/")
    return f"{str(settings.MEDIA_URL).rstrip('/')}/file_library/{rel}"


def classify_inspection_submit_library_file(lf: LibraryFile) -> str:
    """
    检测提交类文件分组：data（JSON）| signature | photo | legacy。
    新路径含 /signatures/、/photos/；旧版平铺文件按扩展名与文件名推断。
    """
    rel = (lf.relative_path or "").replace("\\", "/").lower()
    name = (lf.original_name or "")
    if "/signatures/" in rel or "签名" in name:
        return "signature"
    if "/photos/" in rel:
        return "photo"
    if name.lower().endswith(".json") or "填写数据" in name:
        return "data"
    ext = Path(name).suffix.lower()
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif") and "签名" in name:
        return "signature"
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        return "photo"
    return "legacy"


def parse_site_record_submit_batch(relative_path: str) -> Optional[Tuple[str, str]]:
    """
    从现场记录路径解析同批次委托编号与时间戳。
    约定：site_records/{委托编号}/{时间戳}/xxx.pdf
    """
    parts = [p for p in (relative_path or "").replace("\\", "/").strip("/").split("/") if p]
    if len(parts) >= 3 and parts[0] == "site_records":
        code, batch = parts[1].strip(), parts[2].strip()
        if code and batch:
            return code, batch
    return None


def list_retained_photos_for_site_record(lf: LibraryFile) -> List[LibraryFile]:
    """
    列出与现场记录同提交批次、落库在 photos/ 下的留存照片。
    不含 signatures/、assets/（签名与布局图等）。
    """
    parsed = parse_site_record_submit_batch(getattr(lf, "relative_path", "") or "")
    if not parsed:
        return []
    code, batch = parsed
    needle = f"inspection_submits/{code}/{batch}/photos/"
    qs = (
        LibraryFile.objects.filter(
            category=LibraryFile.CATEGORY_INSPECTION_SUBMIT,
            deleted_at__isnull=True,
            relative_path__startswith=needle,
        )
        .order_by("original_name", "id")
    )
    return list(qs)


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


def library_file_exists_on_disk(lf: LibraryFile) -> bool:
    """仅依据 media 磁盘判断文件是否可读（与绑定历史 file_still_available 一致）。"""
    try:
        path = pipeline_service.library_absolute_path(lf.relative_path)
        return path.is_file()
    except (ValueError, OSError):
        return False


def soft_delete_library_file(lf: LibraryFile) -> None:
    """移入回收站（仅标记 deleted_at，不删磁盘）。"""
    if lf.deleted_at is not None:
        return
    lf.deleted_at = timezone.now()
    lf.save(update_fields=["deleted_at"])


def keep_library_files_on_disk(
    files: Iterable[LibraryFile],
    *,
    prune_missing: bool = False,
) -> Tuple[List[LibraryFile], int]:
    """
    仅保留 media 上真实存在的文件。
    prune_missing=True 时，将缺失磁盘的活跃记录软删进回收站。
    """
    kept: List[LibraryFile] = []
    pruned = 0
    for lf in files:
        if lf.deleted_at is not None:
            continue
        if library_file_exists_on_disk(lf):
            kept.append(lf)
        elif prune_missing:
            soft_delete_library_file(lf)
            pruned += 1
    return kept, pruned


def require_library_file_on_disk(lf: LibraryFile) -> None:
    """预览/下载前校验；磁盘不存在则 404。"""
    if not library_file_exists_on_disk(lf):
        raise Http404("媒体目录中不存在该文件，可能已被删除或未同步到本机")


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
    submit_batch: Optional[InspectionSubmitBatchStorage] = None,
    submit_subdir: str = "",
    site_record_batch: Optional[InspectionSubmitBatchStorage] = None,
    template_library_task=None,
    template_storage_slot: str = "current",
    report_project=None,
    report_library_task=None,
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
        elif (
            category == LibraryFile.CATEGORY_INSPECTION_SUBMIT
            and submit_batch is not None
        ):
            disk_name = safe
            display_name = safe
        else:
            uid = uuid.uuid4().hex
            display_name = safe_library_basename(name, max_bytes=220)
            ext = Path(display_name).suffix.lower()
            # 磁盘仅用短名，避免 uuid_长中文名 超过 255 字节；task 等元数据在 original_name
            disk_name = f"{uid}{ext}" if ext else uid

        if user is not None and enforce_storage_quota:
            cap = library_user_file_library_quota_bytes(user)
            if cap is not None:
                used_so_far = usage_base + added_in_batch
                if used_so_far + len(raw) > cap:
                    skipped.append({"filename": name, "reason": "quota_exceeded"})
                    continue

        if (
            category == LibraryFile.CATEGORY_INSPECTION_SUBMIT
            and submit_batch is not None
        ):
            rel = inspection_submit_relative_path(
                submit_batch, disk_name, subdir=submit_subdir
            )
            abs_p = Path(settings.FILE_LIBRARY_ROOT) / rel
            abs_p.parent.mkdir(parents=True, exist_ok=True)
        elif category == LibraryFile.CATEGORY_TEMPLATE:
            from apps.core.template_storage_service import build_template_relative_path

            rel = build_template_relative_path(
                disk_name=disk_name,
                task=template_library_task,
                slot=template_storage_slot,
            )
            abs_p = pipeline_service.library_absolute_path(rel)
            abs_p.parent.mkdir(parents=True, exist_ok=True)
        elif category == LibraryFile.CATEGORY_REPORT:
            rel = report_relative_path(
                disk_name,
                project=report_project,
                library_task=report_library_task,
            )
            abs_p = pipeline_service.library_absolute_path(rel)
            abs_p.parent.mkdir(parents=True, exist_ok=True)
        elif (
            category == LibraryFile.CATEGORY_SITE_RECORD
            and site_record_batch is not None
        ):
            rel = site_record_relative_path(site_record_batch, disk_name)
            abs_p = Path(settings.FILE_LIBRARY_ROOT) / rel
            abs_p.parent.mkdir(parents=True, exist_ok=True)
        else:
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
        if category == LibraryFile.CATEGORY_TEMPLATE and template_library_task is not None:
            from apps.core.template_storage_service import _write_manifest_for_task

            _write_manifest_for_task(template_library_task)
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


def rename_library_template_file(lf: LibraryFile, new_display_name: str) -> str | None:
    """重命名模板文件显示名（不移动磁盘路径，便于任务模板库维护）。"""
    if lf.category != LibraryFile.CATEGORY_TEMPLATE:
        return "仅支持「模板」分类文件"
    name = safe_library_basename((new_display_name or "").strip())
    if not name.lower().endswith((".pdf", ".json")):
        return "文件名须保留 .pdf 或 .json 扩展名"
    lf.original_name = name
    lf.save(update_fields=["original_name", "updated_at"])
    return None


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
    valid_tasks = list(
        LibraryTask.objects.filter(pk__in=task_ids).select_related("task_folder")
    )
    if not valid_tasks:
        return
    for fid in file_ids:
        for task in valid_tasks:
            LibraryFileTask.objects.get_or_create(
                library_file_id=fid,
                library_task_id=task.pk,
                defaults={"created_by": user},
            )
    if len(valid_tasks) == 1:
        task = valid_tasks[0]
        from apps.core.template_storage_service import relocate_library_template_file

        for fid in file_ids:
            lf = LibraryFile.objects.filter(
                pk=fid, category=LibraryFile.CATEGORY_TEMPLATE, deleted_at__isnull=True
            ).first()
            if lf is not None:
                relocate_library_template_file(lf, task=task, slot="current")


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


def reorganize_flat_reports_on_disk(*, dry_run: bool = False) -> dict:
    """
    将 ``reports/{uuid}.pdf`` 平铺文件迁入 ``report_relative_path`` 分层目录，并更新 LibraryFile.relative_path。
    仅处理 relative_path 形如 ``reports/<32hex>.pdf`` 且无二级目录的记录。
    """
    import re

    flat_re = re.compile(r"^reports/[0-9a-f]{32}\.pdf$", re.I)
    stats = {"scanned": 0, "moved": 0, "skipped": 0, "errors": []}
    qs = (
        LibraryFile.objects.filter(category=LibraryFile.CATEGORY_REPORT, deleted_at__isnull=True)
        .prefetch_related("projects")
        .order_by("id")
    )
    for lf in qs:
        rel = (lf.relative_path or "").replace("\\", "/").strip()
        if not flat_re.match(rel):
            stats["skipped"] += 1
            continue
        stats["scanned"] += 1
        src = pipeline_service.library_absolute_path(rel)
        if not src.is_file():
            stats["errors"].append(f"id={lf.pk}: missing disk file {rel}")
            continue
        project = lf.projects.order_by("id").first()
        task = lf.library_tasks.order_by("id").first()
        disk_name = src.name
        new_rel = report_relative_path(
            disk_name,
            project=project,
            library_task=task,
            created_at=lf.created_at,
        )
        if new_rel == rel:
            stats["skipped"] += 1
            continue
        dst = pipeline_service.library_absolute_path(new_rel)
        if dry_run:
            stats["moved"] += 1
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.is_file():
                stats["errors"].append(f"id={lf.pk}: target exists {new_rel}")
                continue
            shutil.move(str(src), str(dst))
            lf.relative_path = new_rel
            lf.save(update_fields=["relative_path"])
            stats["moved"] += 1
        except OSError as exc:
            stats["errors"].append(f"id={lf.pk}: {exc}")
    return stats


def reorganize_flat_site_records_on_disk(*, dry_run: bool = False) -> dict:
    """
    将 ``site_records/{uuid}.pdf`` 平铺文件迁入 ``site_record_relative_path`` 分层目录，
    并更新 LibraryFile.relative_path。批次时间戳优先取自同案件检测提交 JSON 目录。
    """
    flat_re = re.compile(r"^site_records/[0-9a-f]{32}\.pdf$", re.I)
    stats = {"scanned": 0, "moved": 0, "skipped": 0, "errors": []}
    qs = (
        LibraryFile.objects.filter(category=LibraryFile.CATEGORY_SITE_RECORD, deleted_at__isnull=True)
        .prefetch_related("projects")
        .order_by("id")
    )
    for lf in qs:
        rel = (lf.relative_path or "").replace("\\", "/").strip()
        if not flat_re.match(rel):
            stats["skipped"] += 1
            continue
        stats["scanned"] += 1
        src = pipeline_service.library_absolute_path(rel)
        if not src.is_file():
            stats["errors"].append(f"id={lf.pk}: missing disk file {rel}")
            continue
        project = lf.projects.order_by("id").first()
        case = None
        if lf.link_entity == LibraryFile.LINK_ENTITY_INSPECTION_CASE and lf.link_object_id:
            from apps.core.models import InspectionCase

            case = InspectionCase.objects.filter(pk=int(lf.link_object_id)).first()
        batch = resolve_site_record_batch_storage(
            project=project,
            case=case,
            created_at=lf.created_at,
        )
        new_rel = site_record_relative_path(batch, src.name)
        if new_rel == rel:
            stats["skipped"] += 1
            continue
        dst = pipeline_service.library_absolute_path(new_rel)
        if dry_run:
            stats["moved"] += 1
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.is_file():
                stats["errors"].append(f"id={lf.pk}: target exists {new_rel}")
                continue
            shutil.move(str(src), str(dst))
            lf.relative_path = new_rel
            lf.save(update_fields=["relative_path"])
            stats["moved"] += 1
        except OSError as exc:
            stats["errors"].append(f"id={lf.pk}: {exc}")
    return stats
