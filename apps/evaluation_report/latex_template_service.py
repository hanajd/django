"""评价报告 LaTeX 模板库：注册、上传、删除与路径解析。"""

from __future__ import annotations

import io
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.utils.text import slugify

from .constants import REPORT_TEMPLATES
from .models import EvaluationLatexTemplate, EvaluationReport

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

ALLOWED_SUFFIXES = {
    ".tex",
    ".sty",
    ".cls",
    ".bib",
    ".bst",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".svg",
    ".pdf",
    ".eps",
    ".ttf",
    ".otf",
    ".woff",
    ".woff2",
    ".md",
    ".txt",
    ".csv",
    ".json",
    ".xml",
}

SKIP_NAMES = {
    "__macosx",
    ".ds_store",
    "thumbs.db",
    "desktop.ini",
}


class LatexTemplateError(ValueError):
    pass


@dataclass
class UploadStats:
    files_written: int = 0
    files_skipped: int = 0
    messages: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def get_template_or_none(key: str) -> EvaluationLatexTemplate | None:
    key = (key or "").strip()
    if not key:
        return None
    return EvaluationLatexTemplate.objects.filter(key=key).first()


def get_template(key: str) -> EvaluationLatexTemplate:
    tpl = get_template_or_none(key)
    if tpl is None:
        raise LatexTemplateError(f"未知 LaTeX 模板：{key}")
    return tpl


def template_storage_root(key: str) -> Path:
    tpl = get_template(key)
    root = tpl.storage_root
    if not root.is_dir():
        raise LatexTemplateError(f"模板目录不存在：{root}")
    return root.resolve()


def list_templates() -> list[EvaluationLatexTemplate]:
    return list(EvaluationLatexTemplate.objects.all())


def template_for_type(report_subtype: str) -> EvaluationLatexTemplate | None:
    """按评价报告类型匹配模板。"""
    subtype = (report_subtype or "").strip()
    if not subtype:
        return None
    row = (
        EvaluationLatexTemplate.objects.filter(report_subtype=subtype)
        .order_by("-is_system", "name")
        .first()
    )
    if row:
        return row
    for tpl in REPORT_TEMPLATES.values():
        if tpl.report_subtype == subtype:
            return get_template_or_none(tpl.key)
    return None


def template_choices_for_ui() -> list[dict]:
    items: list[dict] = []
    for tpl in list_templates():
        items.append(
            {
                "key": tpl.key,
                "name": tpl.name,
                "device_category": tpl.device_category,
                "device_label": tpl.device_category_label,
                "report_subtype": tpl.report_subtype,
                "subtype_label": tpl.report_subtype_label,
                "description": tpl.description,
                "source": tpl.source,
                "is_editable": tpl.is_editable,
                "has_main_tex": tpl.has_main_tex,
            }
        )
    return items


def _unique_key(base: str) -> str:
    key = slugify(base).replace("-", "_")[:64] or "template"
    if not KEY_RE.match(key):
        key = "template"
    candidate = key
    n = 0
    while EvaluationLatexTemplate.objects.filter(key=candidate).exists():
        n += 1
        suffix = f"_{n}"
        candidate = (key[: max(1, 64 - len(suffix))] + suffix)[:64]
    return candidate


def _ensure_upload_root(key: str) -> Path:
    root = Path(settings.EVALUATION_LATEX_TEMPLATE_ROOT) / key
    root.mkdir(parents=True, exist_ok=True)
    return root


def _normalize_rel_path(raw: str) -> str | None:
    text = (raw or "").replace("\\", "/").strip().lstrip("/")
    if not text:
        return None
    parts = [p for p in PurePosixPath(text).parts if p not in (".", "")]
    if not parts:
        return None
    if any(p == ".." for p in parts):
        return None
    if parts[0].lower() in SKIP_NAMES:
        return None
    return "/".join(parts)


def _allowed_file(name: str) -> bool:
    base = Path(name).name
    if not base or base.lower() in SKIP_NAMES:
        return False
    if base.startswith("."):
        return False
    suffix = Path(base).suffix.lower()
    if suffix and suffix not in ALLOWED_SUFFIXES:
        return False
    return True


def _write_bytes_to_root(root: Path, rel: str, data: bytes, stats: UploadStats) -> None:
    if not _allowed_file(Path(rel).name):
        stats.files_skipped += 1
        return
    dest = root / rel.replace("/", "\\") if "\\" in rel else root / rel
    dest = (root / PurePosixPath(rel)).resolve()
    if not str(dest).startswith(str(root.resolve())):
        stats.errors.append(f"非法路径：{rel}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    stats.files_written += 1


@transaction.atomic
def create_template(
    *,
    name: str,
    key: str = "",
    description: str = "",
    report_subtype: str = "",
    user: User | None = None,
) -> EvaluationLatexTemplate:
    name = (name or "").strip()
    if not name:
        raise LatexTemplateError("请填写模板名称")
    final_key = (key or "").strip().lower() or _unique_key(name)
    if not KEY_RE.match(final_key):
        raise LatexTemplateError("模板键仅允许小写字母、数字、下划线与连字符")
    if EvaluationLatexTemplate.objects.filter(key=final_key).exists():
        raise LatexTemplateError(f"模板键「{final_key}」已存在")
    _ensure_upload_root(final_key)
    return EvaluationLatexTemplate.objects.create(
        key=final_key,
        name=name,
        description=(description or "").strip(),
        source=EvaluationLatexTemplate.Source.UPLOADED,
        device_category="",
        report_subtype=(report_subtype or "").strip(),
        is_system=False,
        created_by=user,
    )


@transaction.atomic
def update_template_metadata(
    tpl: EvaluationLatexTemplate,
    *,
    name: str,
    description: str = "",
    report_subtype: str = "",
) -> EvaluationLatexTemplate:
    name = (name or "").strip()
    if not name:
        raise LatexTemplateError("请填写模板名称")
    tpl.name = name
    tpl.description = (description or "").strip()
    tpl.device_category = ""
    tpl.report_subtype = (report_subtype or "").strip()
    tpl.save(
        update_fields=[
            "name",
            "description",
            "device_category",
            "report_subtype",
            "updated_at",
        ]
    )
    return tpl


def ingest_zip_upload(
    tpl: EvaluationLatexTemplate,
    file_obj: BinaryIO,
    *,
    replace: bool = False,
) -> UploadStats:
    if tpl.source != EvaluationLatexTemplate.Source.UPLOADED:
        raise LatexTemplateError("内置模板不可上传覆盖")
    stats = UploadStats()
    root = _ensure_upload_root(tpl.key)
    if replace and root.exists():
        for child in root.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    try:
        with zipfile.ZipFile(file_obj) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                rel = _normalize_rel_path(info.filename)
                if not rel:
                    stats.files_skipped += 1
                    continue
                try:
                    data = zf.read(info)
                except Exception as exc:
                    stats.errors.append(f"{rel}: {exc}")
                    continue
                _write_bytes_to_root(root, rel, data, stats)
    except zipfile.BadZipFile as exc:
        raise LatexTemplateError(f"无效的 ZIP 文件：{exc}") from exc
    if stats.files_written == 0 and not stats.errors:
        stats.errors.append("ZIP 中未找到可写入的有效文件")
    return stats


def ingest_multipart_files(
    tpl: EvaluationLatexTemplate,
    uploads: Iterable,
    *,
    relative_paths: Iterable[str] | None = None,
    replace: bool = False,
) -> UploadStats:
    if tpl.source != EvaluationLatexTemplate.Source.UPLOADED:
        raise LatexTemplateError("内置模板不可上传覆盖")
    stats = UploadStats()
    root = _ensure_upload_root(tpl.key)
    if replace and root.exists():
        for child in root.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    uploads = list(uploads)
    rel_paths = list(relative_paths or [])
    for index, upload in enumerate(uploads):
        rel_raw = rel_paths[index] if index < len(rel_paths) else getattr(upload, "name", "")
        rel = _normalize_rel_path(rel_raw or "")
        if not rel:
            stats.files_skipped += 1
            continue
        data = upload.read()
        _write_bytes_to_root(root, rel, data, stats)
    if stats.files_written == 0 and not stats.errors:
        stats.errors.append("未收到可写入的有效文件")
    return stats


def list_template_files(tpl: EvaluationLatexTemplate) -> list[dict[str, str]]:
    root = tpl.storage_root
    if not root.is_dir():
        return []
    rows: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.lower() in SKIP_NAMES:
            continue
        rel = path.relative_to(root).as_posix()
        rows.append({"relpath": rel, "size": str(path.stat().st_size)})
    return rows


def _should_skip_zip_member(rel: str) -> bool:
    parts = [p.lower() for p in PurePosixPath(rel).parts]
    return any(p in SKIP_NAMES for p in parts)


def build_template_zip_bytes(tpl: EvaluationLatexTemplate) -> bytes:
    """将模板工程打成 ZIP，供浏览器下载。"""
    root = tpl.storage_root
    if not root.is_dir():
        raise LatexTemplateError("模板目录不存在，无法下载")
    buf = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if _should_skip_zip_member(rel):
                continue
            zf.write(path, arcname=rel)
            count += 1
    if count == 0:
        raise LatexTemplateError("模板中没有可下载的文件")
    return buf.getvalue()


@transaction.atomic
def delete_template(tpl: EvaluationLatexTemplate) -> None:
    if tpl.is_system:
        raise LatexTemplateError("系统内置模板不可删除")
    in_use = EvaluationReport.objects.filter(latex_template_key=tpl.key).exists()
    if in_use:
        raise LatexTemplateError("仍有评价报告引用该模板，无法删除")
    if tpl.source == EvaluationLatexTemplate.Source.UPLOADED:
        root = Path(settings.EVALUATION_LATEX_TEMPLATE_ROOT) / tpl.key
        if root.is_dir():
            shutil.rmtree(root)
    tpl.delete()


def seed_bundled_templates() -> None:
    """将 constants 中的内置模板登记到数据库（幂等）。"""
    for legacy in REPORT_TEMPLATES.values():
        EvaluationLatexTemplate.objects.update_or_create(
            key=legacy.key,
            defaults={
                "name": legacy.name,
                "description": legacy.description,
                "source": EvaluationLatexTemplate.Source.BUNDLED,
                "bundled_dir": legacy.latex_dir,
                "device_category": "",
                "report_subtype": legacy.report_subtype,
                "is_system": True,
            },
        )
