"""
Django 与 utils 文档管线的桥接（目录配置 + LibraryFile 索引）。
"""
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from django.conf import settings

from utils.document_pipeline import process_all_files
from utils.pipeline_config import set_pipeline_directories


def ensure_file_library_dirs():
    root = Path(settings.FILE_LIBRARY_ROOT)
    for p in (
        root,
        settings.FILE_LIBRARY_UPLOAD_DIR,
        settings.FILE_LIBRARY_JSON_DIR,
        settings.FILE_LIBRARY_TEMPLATE_DIR,
        settings.FILE_LIBRARY_SITE_RECORD_DIR,
        settings.FILE_LIBRARY_REPORT_DIR,
        settings.FILE_LIBRARY_ATTACHMENT_DIR,
        settings.FILE_LIBRARY_TEMP_ROOT,
        settings.PIPELINE_TEMP_PDF,
        settings.PIPELINE_BATCH_ARCHIVE,
        settings.PIPELINE_PREVIEW_IMG,
        settings.PIPELINE_STATIC,
        settings.PIPELINE_MINERU_MD,
    ):
        Path(p).mkdir(parents=True, exist_ok=True)


def configure_pipeline_from_django():
    ensure_file_library_dirs()
    if "MINERU_BACKEND" not in os.environ:
        os.environ["MINERU_BACKEND"] = str(
            getattr(settings, "MINERU_BACKEND", "pipeline")
        )
    host = getattr(settings, "OLLAMA_HOST", None)
    if host:
        os.environ.setdefault("OLLAMA_HOST", str(host))
    set_pipeline_directories(
        upload_folder=str(settings.FILE_LIBRARY_UPLOAD_DIR),
        mineru_md_folder=str(settings.PIPELINE_MINERU_MD),
        output_json_folder=str(settings.FILE_LIBRARY_JSON_DIR),
        static_folder=str(settings.PIPELINE_STATIC),
        temp_pdf_folder=str(settings.PIPELINE_TEMP_PDF),
        batch_archive=str(settings.PIPELINE_BATCH_ARCHIVE),
        preview_img=str(settings.PIPELINE_PREVIEW_IMG),
    )


def library_absolute_path(relative_path: str) -> Path:
    rel = Path(relative_path)
    if rel.is_absolute():
        raise ValueError("relative_path must be relative")
    base = Path(settings.FILE_LIBRARY_ROOT).resolve()
    full = (base / rel).resolve()
    full.relative_to(base)
    return full


def run_process_all_files(
    file_payloads: List[Tuple[str, bytes]], batch_id: str
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], dict]:
    configure_pipeline_from_django()
    return process_all_files(file_payloads, batch_id)


def collect_temp_paths_for_batch(batch_id: str) -> List[Tuple[str, str]]:
    """
    Returns list of (original_name, relative_path_under_file_library) for temp artifacts.
    """
    out: List[Tuple[str, str]] = []
    root = Path(settings.FILE_LIBRARY_ROOT).resolve()
    batch_root = Path(settings.PIPELINE_BATCH_ARCHIVE) / batch_id
    if batch_root.is_dir():
        for dirpath, _, filenames in os.walk(batch_root):
            for fn in filenames:
                abs_p = Path(dirpath) / fn
                rel = abs_p.relative_to(root)
                out.append((fn, str(rel).replace("\\", "/")))

    tpdf = Path(settings.PIPELINE_TEMP_PDF)
    if tpdf.is_dir():
        for p in tpdf.iterdir():
            if p.is_file() and p.name.startswith(batch_id):
                rel = p.resolve().relative_to(root)
                out.append((p.name, str(rel).replace("\\", "/")))
    return out


def json_output_relative(batch_id: str) -> str:
    rel = Path("json") / f"{batch_id}.json"
    return str(rel).replace("\\", "/")


def _slug_instrument_label(text: str, max_len: int = 64) -> str:
    s = (text or "").strip()
    for c in '<>:"/\\|?*\n\r\t':
        s = s.replace(c, "_")
    s = re.sub(r"\s+", "_", s).strip("._")
    if not s:
        return ""
    return s[:max_len]


def _device_json_name_stem(device: Dict[str, Any], index: int) -> str:
    """设备名称_设备型号_设备编号（各段做文件名安全处理，段可为空）。"""
    keys = ("设备名称", "设备型号", "设备编号")
    parts = [_slug_instrument_label(str(device.get(k) or ""), 72) for k in keys]
    stem = "_".join(parts).strip("_")
    if not stem:
        stem = f"instrument_{index + 1:03d}"
    # 整段路径不宜过长，为后续附加 batch_id 留余量
    return stem[:160].rstrip("_")


def write_split_device_json_files(
    batch_id: str, devices: List[Dict[str, Any]], user=None
) -> int:
    """
    将管线返回的设备列表按「单台仪器」拆成多个 JSON 文件，写入文件库 json/ 并登记 LibraryFile。
    展示名：设备名称_设备型号_设备编号.json；磁盘名在此基础上附加批次 id，避免跨批次重名覆盖。
    """
    from apps.core.models import LibraryFile

    configure_pipeline_from_django()
    ensure_file_library_dirs()
    json_dir = Path(settings.FILE_LIBRARY_JSON_DIR)
    written = 0
    stem_used: Dict[str, int] = {}

    for idx, device in enumerate(devices):
        if not isinstance(device, dict):
            continue
        stem = _device_json_name_stem(device, idx)
        n = stem_used.get(stem, 0) + 1
        stem_used[stem] = n
        if n > 1:
            stem = f"{stem}_{n}"

        display_name = f"{stem}.json"
        if len(display_name) > 240:
            display_name = display_name[:237] + "..."

        disk_name = f"{stem}_{batch_id}.json"
        if len(disk_name) > 240:
            over = len(disk_name) - 240
            stem = stem[: max(1, len(stem) - over)].rstrip("_")
            disk_name = f"{stem}_{batch_id}.json"

        rel = str(Path("json") / disk_name).replace("\\", "/")
        abs_p = json_dir / disk_name
        body = json.dumps(device, ensure_ascii=False, indent=2)
        abs_p.write_text(body, encoding="utf-8")
        if not LibraryFile.objects.filter(
            relative_path=rel, category=LibraryFile.CATEGORY_JSON
        ).exists():
            index_library_file(
                original_name=display_name,
                relative_path=rel,
                category=LibraryFile.CATEGORY_JSON,
                batch_id=batch_id,
                user=user,
            )
        written += 1
    return written


def index_library_file(
    *,
    original_name: str,
    relative_path: str,
    category: str,
    batch_id: str = "",
    user=None,
) -> None:
    from apps.core.models import LibraryFile

    abs_p = library_absolute_path(relative_path)
    size = abs_p.stat().st_size if abs_p.is_file() else 0
    LibraryFile.objects.create(
        original_name=original_name,
        relative_path=relative_path.replace("\\", "/"),
        category=category,
        batch_id=batch_id or "",
        size=size,
        created_by=user,
    )


def sync_temp_and_json_records(batch_id: str, user=None) -> None:
    """登记批次临时文件；JSON 产出由 write_split_device_json_files 按仪器拆分写入。"""
    from apps.core.models import LibraryFile

    for name, rel in collect_temp_paths_for_batch(batch_id):
        if LibraryFile.objects.filter(relative_path=rel, category=LibraryFile.CATEGORY_TEMP).exists():
            continue
        index_library_file(
            original_name=name,
            relative_path=rel,
            category=LibraryFile.CATEGORY_TEMP,
            batch_id=batch_id,
            user=user,
        )
