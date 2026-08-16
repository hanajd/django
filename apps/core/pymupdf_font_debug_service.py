# -*- coding: utf-8 -*-
"""PyMuPDF 统一字库目录梳理与调试页文件操作。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from django.utils import timezone

from utils.pymupdf_fonts import ensure_project_fonts_dir, project_fonts_dir

# 允许上传/管理的字体后缀
_FONT_EXTS = {".ttf", ".ttc", ".otf", ".otc"}
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._\-\u4e00-\u9fff]+$")


@dataclass(frozen=True)
class FontDirSpec:
    key: str
    title: str
    relative_path: str
    purpose: str
    writable: bool = True

    def absolute(self) -> Path:
        return project_fonts_dir().resolve()


# 统一字库：现场记录、报告填表、合并、防护表、F1 等共用
FONT_DIR_SPECS: tuple[FontDirSpec, ...] = (
    FontDirSpec(
        key="shared",
        title="统一字库",
        relative_path="fonts",
        purpose=(
            "全站 PyMuPDF 共用（htmlpdf 填表、pdf_merge、防护表、平面图、F1 评价等）。"
            "历史路径 htmlpdf/fonts、radiation_detection_report/fonts 为指向本目录的软链。"
        ),
    ),
)

# 关键角色文件提示（便于上传时对齐文件名）
_ROLE_HINTS: dict[str, str] = {
    "SourceHanSerifSC-VF.ttf": "思源宋体（可变字体，系统按 Regular/Bold 字重实例化后使用）",
    "SourceHanSansSC-VF.ttf": "思源黑体（可变字体，系统按 Regular/Bold 字重实例化后使用）",
    "SIMSUN.TTC": "旧宋体备选（已被思源宋体优先替代）",
    "SIMSUN.TTF": "旧宋体备选",
    "TIMES.TTF": "Times（西文）",
    "SEGUISYM.TTF": "Segoe UI Symbol（勾选 ✓）",
    "SIMHEI.TTF": "旧黑体备选（已被思源黑体优先替代）",
    "wqy-zenhei.ttc": "文泉驿正黑（旧黑体 Linux 回退）",
}


def list_font_dir_specs() -> list[FontDirSpec]:
    return list(FONT_DIR_SPECS)


def get_font_dir_spec(key: str) -> FontDirSpec | None:
    k = (key or "").strip().lower()
    # 兼容旧调试页 key：htmlpdf / radiation
    if k in ("htmlpdf", "radiation", "shared", "fonts", ""):
        return FONT_DIR_SPECS[0]
    for spec in FONT_DIR_SPECS:
        if spec.key == k:
            return spec
    return None


def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.2f} MB"


def sanitize_font_filename(name: str) -> str:
    raw = (name or "").strip().replace("\\", "/").split("/")[-1]
    if not raw or ".." in raw:
        raise ValueError("文件名无效")
    if not _SAFE_NAME_RE.match(raw):
        raise ValueError("文件名仅允许字母、数字、点、下划线、短横线与中文")
    ext = Path(raw).suffix.lower()
    if ext not in _FONT_EXTS:
        raise ValueError(f"仅支持字体后缀：{', '.join(sorted(_FONT_EXTS))}")
    if len(raw) > 180:
        raise ValueError("文件名过长")
    return raw


def _resolve_font_path(spec: FontDirSpec, filename: str) -> Path:
    safe = sanitize_font_filename(filename)
    root = ensure_project_fonts_dir().resolve()
    path = (root / safe).resolve()
    if path.parent != root:
        raise ValueError("路径越界，拒绝操作")
    return path


def list_fonts_in_dir(spec: FontDirSpec) -> list[dict[str, Any]]:
    root = spec.absolute()
    rows: list[dict[str, Any]] = []
    if not root.is_dir():
        return rows
    for p in sorted(root.iterdir(), key=lambda x: x.name.lower()):
        if not p.is_file():
            continue
        if p.suffix.lower() not in _FONT_EXTS:
            continue
        try:
            st = p.stat()
            mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.get_current_timezone())
            rows.append(
                {
                    "name": p.name,
                    "size": st.st_size,
                    "size_label": _human_size(st.st_size),
                    "mtime": mtime,
                    "mtime_label": mtime.strftime("%Y-%m-%d %H:%M"),
                    "role_hint": _ROLE_HINTS.get(p.name) or _ROLE_HINTS.get(p.name.upper()) or "",
                    "ext": p.suffix.lower(),
                }
            )
        except OSError:
            continue
    return rows


def build_font_debug_catalog() -> dict[str, Any]:
    dirs = []
    for spec in FONT_DIR_SPECS:
        root = spec.absolute()
        dirs.append(
            {
                "key": spec.key,
                "title": spec.title,
                "relative_path": spec.relative_path,
                "absolute_path": str(root),
                "purpose": spec.purpose,
                "exists": root.is_dir(),
                "writable": spec.writable and root.parent.is_dir(),
                "files": list_fonts_in_dir(spec),
            }
        )
    return {
        "dirs": dirs,
        "allowed_exts": sorted(_FONT_EXTS),
        "role_hints": [{"name": k, "hint": v} for k, v in _ROLE_HINTS.items()],
        "system_fallbacks": [
            "/usr/share/fonts/truetype/msttcorefonts/times.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        ],
        "legacy_symlinks": [
            "htmlpdf/fonts → ../fonts",
            "radiation_detection_report/fonts → ../fonts",
        ],
    }


def save_uploaded_font(
    *,
    dir_key: str,
    upload,
    target_name: str = "",
    overwrite: bool = True,
) -> Path:
    spec = get_font_dir_spec(dir_key)
    if spec is None:
        raise ValueError("未知字库目录")
    if not spec.writable:
        raise ValueError("该目录不可写")
    upload_name = getattr(upload, "name", "") or ""
    name = sanitize_font_filename(target_name or upload_name)
    path = _resolve_font_path(spec, name)
    if path.exists() and not overwrite:
        raise ValueError(f"已存在同名文件：{name}")
    data = upload.read()
    if not data:
        raise ValueError("上传文件为空")
    if len(data) > 80 * 1024 * 1024:
        raise ValueError("字体文件过大（上限 80MB）")
    path.write_bytes(data)
    return path


def delete_font_file(*, dir_key: str, filename: str) -> None:
    spec = get_font_dir_spec(dir_key)
    if spec is None:
        raise ValueError("未知字库目录")
    path = _resolve_font_path(spec, filename)
    if not path.is_file():
        raise ValueError("文件不存在")
    path.unlink()


def rename_font_file(*, dir_key: str, filename: str, new_name: str) -> Path:
    spec = get_font_dir_spec(dir_key)
    if spec is None:
        raise ValueError("未知字库目录")
    src = _resolve_font_path(spec, filename)
    if not src.is_file():
        raise ValueError("源文件不存在")
    dst = _resolve_font_path(spec, new_name)
    if dst.exists():
        raise ValueError(f"目标已存在：{dst.name}")
    src.rename(dst)
    return dst


def copy_font_across_dirs(
    *,
    source_dir_key: str,
    filename: str,
    target_dir_key: str,
    overwrite: bool = True,
) -> Path:
    """兼容旧接口：统一目录后复制即同目录覆盖/校验。"""
    src_spec = get_font_dir_spec(source_dir_key)
    dst_spec = get_font_dir_spec(target_dir_key)
    if src_spec is None or dst_spec is None:
        raise ValueError("未知字库目录")
    src = _resolve_font_path(src_spec, filename)
    if not src.is_file():
        raise ValueError("源文件不存在")
    # 已合并为单目录：无需跨目录复制
    if src_spec.key == dst_spec.key or src_spec.absolute() == dst_spec.absolute():
        return src
    dst = _resolve_font_path(dst_spec, filename)
    if dst.exists() and not overwrite:
        raise ValueError(f"目标已存在：{dst.name}")
    import shutil

    shutil.copy2(src, dst)
    return dst


def read_font_file_for_download(*, dir_key: str, filename: str) -> tuple[Path, bytes]:
    spec = get_font_dir_spec(dir_key)
    if spec is None:
        raise ValueError("未知字库目录")
    path = _resolve_font_path(spec, filename)
    if not path.is_file():
        raise ValueError("文件不存在")
    return path, path.read_bytes()
