"""附件待上传暂存：选择文件后先入 staging，确认后再入库并同步 appendix。"""

from __future__ import annotations

import json
import secrets
import shutil
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile

from .models import EvaluationReport, EvaluationReportUpload, EvaluationReportUploadFile

MANIFEST_NAME = "manifest.json"
ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".pdf"}
VALID_PAGE_MODES = {
    EvaluationReportUploadFile.PageMode.NORMAL,
    EvaluationReportUploadFile.PageMode.A3_LANDSCAPE,
}
VALID_ROTATES = {0, 90, 180, 270}


def normalize_display_options(
    *,
    page_mode: str | None = None,
    rotate: int | None = None,
    width_percent: int | None = None,
) -> dict:
    mode = (page_mode or "").strip()
    if mode not in VALID_PAGE_MODES:
        mode = EvaluationReportUploadFile.PageMode.NORMAL
    try:
        angle = int(rotate if rotate is not None else 0) % 360
    except (TypeError, ValueError):
        angle = 0
    if angle not in VALID_ROTATES:
        angle = 0
    try:
        width = int(width_percent if width_percent is not None else 100)
    except (TypeError, ValueError):
        width = 100
    width = max(30, min(100, width))
    return {"page_mode": mode, "rotate": angle, "width_percent": width}


def staging_dir(report: EvaluationReport, slot_key: str) -> Path:
    path = report.ensure_work_dir() / "staging" / slot_key
    path.mkdir(parents=True, exist_ok=True)
    return path


def _manifest_path(report: EvaluationReport, slot_key: str) -> Path:
    return staging_dir(report, slot_key) / MANIFEST_NAME


def _load_manifest(report: EvaluationReport, slot_key: str) -> list[dict]:
    path = _manifest_path(report, slot_key)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    files = data.get("files")
    return files if isinstance(files, list) else []


def _save_manifest(report: EvaluationReport, slot_key: str, files: list[dict]) -> None:
    path = _manifest_path(report, slot_key)
    path.write_text(
        json.dumps({"files": files}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def staging_count(report: EvaluationReport, slot_key: str) -> int:
    return len(_load_manifest(report, slot_key))


def list_staging_files(report: EvaluationReport, slot_key: str) -> list[dict]:
    rows = _load_manifest(report, slot_key)
    rows.sort(key=lambda r: (r.get("sort_order", 0), r.get("id", "")))
    result: list[dict] = []
    base = staging_dir(report, slot_key)
    for row in rows:
        stored = row.get("stored_name") or ""
        path = base / stored
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        preview_kind = "pdf" if suffix == ".pdf" else "image" if suffix in {
            ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"
        } else "download"
        display = normalize_display_options(
            page_mode=row.get("page_mode"),
            rotate=row.get("rotate"),
            width_percent=row.get("width_percent"),
        )
        result.append(
            {
                "id": row["id"],
                "original_name": row.get("original_name") or stored,
                "stored_name": stored,
                "sort_order": row.get("sort_order", 0),
                "path": path,
                "preview_kind": preview_kind,
                **display,
            }
        )
    return result


def add_staging_files(
    report: EvaluationReport,
    slot_key: str,
    uploaded_files,
    *,
    displays: list[dict] | None = None,
) -> list[dict]:
    files = [f for f in uploaded_files if f]
    if not files:
        raise ValueError("请选择文件")

    base = staging_dir(report, slot_key)
    manifest = _load_manifest(report, slot_key)
    next_order = max((r.get("sort_order", 0) for r in manifest), default=-1) + 1
    added: list[dict] = []

    for idx, uploaded in enumerate(files):
        name = getattr(uploaded, "name", "") or "file"
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise ValueError(f"不支持的文件格式：{name}")

        file_id = secrets.token_hex(8)
        stored_name = f"{file_id}{suffix}"
        dest = base / stored_name
        raw = uploaded.read()
        if not raw:
            raise ValueError(f"文件为空：{name}")
        dest.write_bytes(raw)

        meta = displays[idx] if displays and idx < len(displays) else {}
        display = normalize_display_options(
            page_mode=meta.get("page_mode"),
            rotate=meta.get("rotate"),
            width_percent=meta.get("width_percent"),
        )
        row = {
            "id": file_id,
            "original_name": name,
            "stored_name": stored_name,
            "sort_order": next_order,
            **display,
        }
        manifest.append(row)
        added.append(row)
        next_order += 1

    _save_manifest(report, slot_key, manifest)
    return added


def update_staging_display(
    report: EvaluationReport,
    slot_key: str,
    file_id: str,
    *,
    page_mode: str | None = None,
    rotate: int | None = None,
    width_percent: int | None = None,
) -> dict:
    manifest = _load_manifest(report, slot_key)
    target = next((r for r in manifest if r.get("id") == file_id), None)
    if not target:
        raise ValueError("待上传文件不存在")
    display = normalize_display_options(
        page_mode=page_mode if page_mode is not None else target.get("page_mode"),
        rotate=rotate if rotate is not None else target.get("rotate"),
        width_percent=width_percent if width_percent is not None else target.get("width_percent"),
    )
    target.update(display)
    _save_manifest(report, slot_key, manifest)
    return display


def reorder_staging_files(report: EvaluationReport, slot_key: str, ordered_ids: list[str]) -> None:
    manifest = _load_manifest(report, slot_key)
    by_id = {r["id"]: r for r in manifest if r.get("id")}
    if len(ordered_ids) != len(by_id) or not all(i in by_id for i in ordered_ids):
        raise ValueError("文件列表不完整")
    for order, file_id in enumerate(ordered_ids):
        by_id[file_id]["sort_order"] = order
    _save_manifest(report, slot_key, list(by_id.values()))


def remove_staging_file(report: EvaluationReport, slot_key: str, file_id: str) -> None:
    manifest = _load_manifest(report, slot_key)
    base = staging_dir(report, slot_key)
    kept: list[dict] = []
    for row in manifest:
        if row.get("id") == file_id:
            path = base / (row.get("stored_name") or "")
            path.unlink(missing_ok=True)
        else:
            kept.append(row)
    for order, row in enumerate(sorted(kept, key=lambda r: r.get("sort_order", 0))):
        row["sort_order"] = order
    _save_manifest(report, slot_key, kept)


def clear_staging(report: EvaluationReport, slot_key: str) -> None:
    path = staging_dir(report, slot_key)
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def staging_files_as_uploads(report: EvaluationReport, slot_key: str) -> list[SimpleUploadedFile]:
    uploads: list[SimpleUploadedFile] = []
    for row in list_staging_files(report, slot_key):
        path: Path = row["path"]
        content = path.read_bytes()
        uploads.append(
            SimpleUploadedFile(
                name=row["original_name"],
                content=content,
                content_type="application/octet-stream",
            )
        )
    return uploads


def commit_staging_files(
    report: EvaluationReport,
    slot: EvaluationReportUpload,
    user,
) -> int:
    """将 staging 文件按顺序入库，并清空 staging。返回入库数量。"""
    from .library_integration import add_slot_files

    staging_rows = list_staging_files(report, slot.slot_key)
    if not staging_rows:
        raise ValueError("没有待上传的文件")
    uploads: list[SimpleUploadedFile] = []
    displays: list[dict] = []
    for row in staging_rows:
        path: Path = row["path"]
        uploads.append(
            SimpleUploadedFile(
                name=row["original_name"],
                content=path.read_bytes(),
                content_type="application/octet-stream",
            )
        )
        displays.append(
            normalize_display_options(
                page_mode=row.get("page_mode"),
                rotate=row.get("rotate"),
                width_percent=row.get("width_percent"),
            )
        )
    rows = add_slot_files(
        report, slot, uploads, user, replace=False, file_metas=displays
    )
    clear_staging(report, slot.slot_key)
    return len(rows)
