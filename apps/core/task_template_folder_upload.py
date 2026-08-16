"""任务模板库：按目录层级批量导入 PDF/JSON 模板。

目录约定（相对上传根目录）::

    {检测类型}/{设备类型}/报告.pdf[+同名.json]
    {检测类型}/{设备类型}/空白原始记录/现场记录.pdf[+同名.json]

同一设备类型下所有报告模板绑定该目录下全部现场记录模板。
"""
from __future__ import annotations

import io
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable

from django.db import transaction

from apps.core.library_file_service import attach_files_to_tasks, save_library_binary_uploads
from apps.core.library_task_folder_service import create_task_folder
from apps.core.models import LibraryFile, LibraryTask, LibraryTaskFolder

BLANK_RAW_FOLDER_NAME = "空白原始记录"
ALLOWED_TEMPLATE_SUFFIXES = {".pdf", ".json"}


@dataclass
class _UploadLeaf:
    rel_path: str
    name: str
    read: callable


@dataclass
class IngestStats:
    folders_created: int = 0
    reports_created: int = 0
    site_records_created: int = 0
    files_uploaded: int = 0
    ignored_files: int = 0
    messages: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _normalize_rel_path(raw: str) -> str:
    p = PurePosixPath((raw or "").replace("\\", "/").strip().lstrip("/"))
    return str(p) if str(p) not in (".", "") else ""


def _allowed_template_name(name: str) -> bool:
    return Path(name or "").suffix.lower() in ALLOWED_TEMPLATE_SUFFIXES


def _pair_pdf_json(leaves: list[_UploadLeaf]) -> dict[str, dict[str, _UploadLeaf | None]]:
    """按主文件名（不含扩展名）配对 pdf/json。"""
    buckets: dict[str, dict[str, _UploadLeaf | None]] = defaultdict(lambda: {"pdf": None, "json": None})
    for leaf in leaves:
        if not _allowed_template_name(leaf.name):
            continue
        stem = Path(leaf.name).stem
        ext = Path(leaf.name).suffix.lower()
        buckets[stem][ext.lstrip(".")] = leaf
    return dict(buckets)


def _get_or_create_folder_chain(
    user,
    names: list[str],
    stats: IngestStats,
) -> LibraryTaskFolder | None:
    parent: LibraryTaskFolder | None = None
    for nm in names:
        nm = (nm or "").strip()
        if not nm:
            continue
        existing = LibraryTaskFolder.objects.filter(parent=parent, name=nm, is_active=True).first()
        if existing:
            parent = existing
            continue
        row, err = create_task_folder(user, name=nm, parent=parent)
        if err or row is None:
            stats.errors.append(err or f"无法创建分类「{nm}」")
            return None
        stats.folders_created += 1
        parent = row
    return parent


def _unique_task_code(name: str) -> str:
    from django.utils.text import slugify

    base = slugify(name)[:64] or "task"
    code = base
    n = 0
    while LibraryTask.objects.filter(code=code).exists():
        n += 1
        suffix = f"-{n}"
        max_base = max(1, 64 - len(suffix))
        code = (base[:max_base] + suffix)[:64]
    return code


def _upload_leaf_to_library(user, leaf: _UploadLeaf, *, library_task: LibraryTask | None = None) -> LibraryFile | None:
    raw = leaf.read()
    if not raw:
        return None
    buf = type("UploadLike", (), {"read": lambda self: raw, "name": leaf.name})()
    created, skipped = save_library_binary_uploads(
        user,
        [buf],
        LibraryFile.CATEGORY_TEMPLATE,
        enforce_storage_quota=False,
        template_library_task=library_task,
    )
    if skipped and not created:
        return None
    lf = LibraryFile.objects.filter(pk=created[0]["id"]).first()
    if lf is not None:
        return lf
    return None


def _bind_pair_to_task(user, task: LibraryTask, pair: dict[str, _UploadLeaf | None], stats: IngestStats) -> None:
    from apps.core.library_task_template_binding_service import replace_task_template_file_binding

    pdf_leaf = pair.get("pdf")
    json_leaf = pair.get("json")
    for leaf in (pdf_leaf, json_leaf):
        if leaf is None:
            continue
        lf = _upload_leaf_to_library(user, leaf, library_task=task)
        if lf is None:
            continue
        stats.files_uploaded += 1
        result = replace_task_template_file_binding(
            task=task,
            new_file=lf,
            user=user,
            source="folder_tree_upload",
        )
        if not result.get("ok"):
            # 回退：至少建立 M2M，并迁入 current/
            attach_files_to_tasks([lf.pk], [task.pk], user)
            err = result.get("error") or "未知错误"
            stats.errors.append(
                f"「{task.code}」绑定「{lf.original_name}」失败：{err}"
            )


def _create_report_task(
    user,
    device_folder: LibraryTaskFolder,
    name: str,
    pair: dict[str, _UploadLeaf | None],
    site_tasks: list[LibraryTask],
    stats: IngestStats,
) -> LibraryTask | None:
    code = _unique_task_code(name)
    row = LibraryTask.objects.create(
        code=code,
        name=name,
        output_target=LibraryTask.OUTPUT_REPORT,
        task_folder=device_folder,
        created_by=user,
    )
    for st in site_tasks:
        row.report_source_tasks.add(st)
    _bind_pair_to_task(user, row, pair, stats)
    stats.reports_created += 1
    return row


def _ingest_device_type_node(
    user,
    detection_type: str,
    device_type: str,
    device_level: list[_UploadLeaf],
    raw_level: list[_UploadLeaf],
    stats: IngestStats,
) -> None:
    device_folder = _get_or_create_folder_chain(user, [detection_type, device_type], stats)
    if device_folder is None:
        return

    site_tasks: list[LibraryTask] = []
    for stem, pair in _pair_pdf_json(raw_level).items():
        if not pair.get("pdf") and not pair.get("json"):
            continue
        code = _unique_task_code(stem or "现场记录")
        row = LibraryTask.objects.create(
            code=code,
            name=stem or "现场记录",
            output_target=LibraryTask.OUTPUT_SITE_RECORD,
            created_by=user,
        )
        _bind_pair_to_task(user, row, pair, stats)
        stats.site_records_created += 1
        site_tasks.append(row)

    report_pairs = _pair_pdf_json(device_level)
    if not report_pairs and site_tasks:
        stats.messages.append(f"「{detection_type}/{device_type}」仅有现场记录，已跳过报告创建")
        return
    if not report_pairs:
        stats.messages.append(f"「{detection_type}/{device_type}」无 PDF/JSON，已跳过")
        return

    for stem, pair in report_pairs.items():
        label = stem or device_type
        _create_report_task(user, device_folder, label, pair, site_tasks, stats)


def collect_leaves_from_request_files(files) -> list[_UploadLeaf]:
    leaves: list[_UploadLeaf] = []
    for uf in files or []:
        name = getattr(uf, "name", "") or ""
        if not _allowed_template_name(name):
            continue
        rel = getattr(uf, "webkitRelativePath", None) or name
        rel = _normalize_rel_path(rel)
        data = uf.read()

        def _reader_factory(blob: bytes):
            return lambda: blob

        leaves.append(_UploadLeaf(rel_path=rel, name=Path(rel).name, read=_reader_factory(data)))
    return leaves


def collect_leaves_from_zip(uploaded_file) -> list[_UploadLeaf]:
    leaves: list[_UploadLeaf] = []
    raw = uploaded_file.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            if not _allowed_template_name(name):
                continue
            rel = _normalize_rel_path(info.filename)
            blob = zf.read(info)

            def _reader_factory(b: bytes):
                return lambda: b

            leaves.append(_UploadLeaf(rel_path=rel, name=name, read=_reader_factory(blob)))
    return leaves


def _group_by_device_tree(leaves: list[_UploadLeaf], stats: IngestStats) -> dict[tuple[str, str], dict[str, list[_UploadLeaf]]]:
    """
    {(检测类型, 设备类型): {"device": [...], "raw": [...]}}
    """
    grouped: dict[tuple[str, str], dict[str, list[_UploadLeaf]]] = defaultdict(
        lambda: {"device": [], "raw": []}
    )
    for leaf in leaves:
        if not _allowed_template_name(leaf.name):
            stats.ignored_files += 1
            continue
        parts = [p for p in PurePosixPath(leaf.rel_path).parts if p]
        if len(parts) < 2:
            stats.ignored_files += 1
            stats.errors.append(f"路径过浅，已忽略：{leaf.rel_path}")
            continue
        detection, device = parts[0], parts[1]
        if len(parts) == 2:
            grouped[(detection, device)]["device"].append(leaf)
        elif len(parts) >= 3 and parts[2] == BLANK_RAW_FOLDER_NAME:
            if len(parts) == 4:
                grouped[(detection, device)]["raw"].append(leaf)
            else:
                stats.ignored_files += 1
        else:
            stats.ignored_files += 1
    return grouped


@transaction.atomic
def ingest_template_folder_upload(
    user,
    *,
    request_files: Iterable | None = None,
    zip_file=None,
) -> IngestStats:
    stats = IngestStats()
    leaves: list[_UploadLeaf] = []
    if zip_file is not None:
        leaves.extend(collect_leaves_from_zip(zip_file))
    if request_files:
        leaves.extend(collect_leaves_from_request_files(request_files))
    if not leaves:
        stats.errors.append("未收到可识别的 PDF/JSON 文件")
        return stats

    grouped = _group_by_device_tree(leaves, stats)
    if not grouped:
        stats.errors.append(
            f"未解析到有效目录。请使用「{检测类型}/{设备类型}/…」结构，"
            f"现场记录放在「{BLANK_RAW_FOLDER_NAME}」子目录下。"
        )
        return stats

    for (detection, device), buckets in sorted(grouped.items()):
        _ingest_device_type_node(
            user,
            detection,
            device,
            buckets.get("device") or [],
            buckets.get("raw") or [],
            stats,
        )

    return stats
