"""Simplified file-library upload used by evaluation_report.library_integration."""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings

from apps.core import pipeline_service
from apps.core.models import LibraryFile


def safe_library_basename(name: str) -> str:
    base = Path(name or "file").name
    base = re.sub(r"[^\w.\u4e00-\u9fff\-]+", "_", base, flags=re.UNICODE)
    return base[:180] or "file"


def library_disk_dir_and_rel_prefix(category: str) -> Tuple[Path, str]:
    if category == LibraryFile.CATEGORY_EVALUATION_FORM:
        return Path(settings.FILE_LIBRARY_EVALUATION_FORM_DIR), "evaluation_forms"
    if category == LibraryFile.CATEGORY_ATTACHMENT:
        return Path(settings.FILE_LIBRARY_ATTACHMENT_DIR), "attachments"
    root = Path(settings.FILE_LIBRARY_ROOT) / "misc"
    return root, "misc"


def save_library_binary_uploads(
    user,
    uploaded_files,
    category: str,
    *,
    link_entity: str = "",
    link_object_id: Optional[int] = None,
    project_ids: Optional[List[int]] = None,
    enforce_storage_quota: bool = True,
    submit_batch=None,
    submit_subdir: str = "",
    template_library_task=None,
    template_storage_slot: str = "current",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Write uploads to disk and create LibraryFile rows (no quota / project links)."""
    _ = (
        project_ids,
        enforce_storage_quota,
        submit_batch,
        submit_subdir,
        template_library_task,
        template_storage_slot,
    )
    pipeline_service.ensure_file_library_dirs()
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
        stem = Path(safe).stem
        ext = Path(safe).suffix
        disk_name = f"{stem}_{uuid.uuid4().hex[:10]}{ext}"
        abs_path = dest_dir / disk_name
        abs_path.write_bytes(raw)
        rel_path = f"{rel_prefix}/{disk_name}".replace("\\", "/")
        obj = LibraryFile.objects.create(
            original_name=name or disk_name,
            relative_path=rel_path,
            category=category,
            content_sha256=sha256,
            size=len(raw),
            link_entity=link_entity or "",
            link_object_id=link_object_id,
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )
        created.append(
            {
                "id": obj.pk,
                "original_name": obj.original_name,
                "relative_path": obj.relative_path,
                "size": obj.size,
                "category": obj.category,
                "link_entity": obj.link_entity,
                "link_object_id": obj.link_object_id,
                "created_at": obj.created_at.isoformat() if obj.created_at else "",
            }
        )
    return created, skipped
