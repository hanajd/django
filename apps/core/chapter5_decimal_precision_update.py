"""
调试更新：批量给现场记录模板写入第五章小数精度元数据。

- 测量均值：precision=3
- 报出值：precision=2 + precisionRule=magnitude_tiered（展示/回填按量级分档）
仅处理第五章「工作场所放射防护」表，不影响质控等章节。
"""
from __future__ import annotations

import copy
import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings

from radiation_detection_report.chapter5_field_sync import (
    apply_chapter5_decimal_precision_to_template_payload,
)

STAMP = date.today().strftime("%Y%m%d")


def _media_root() -> Path:
    return Path(getattr(settings, "MEDIA_ROOT", "") or "") / "file_library"


def _norm(v: Any) -> str:
    return str(v or "").strip()


def iter_active_site_template_json_paths() -> List[Tuple[Path, Path]]:
    """返回 [(json_path, task_json_path), ...]。"""
    media = _media_root()
    out: List[Tuple[Path, Path]] = []
    if not media.is_dir():
        return out
    for task_path in sorted(media.glob("templates/**/site/**/current/_task.json")):
        try:
            meta = json.loads(task_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        current = (meta.get("files") or {}).get("current") or []
        if not isinstance(current, list):
            continue
        for row in current:
            if not isinstance(row, dict) or row.get("role") != "json":
                continue
            rel = _norm(row.get("relative_path") or "")
            if rel:
                full = media / rel
                if full.is_file():
                    out.append((full, task_path))
                continue
            disk = _norm(row.get("disk_name") or "")
            if disk:
                cand = task_path.parent / disk
                if cand.is_file():
                    out.append((cand, task_path))
    return out


def _backup_to_history(json_path: Path, task_path: Path) -> Optional[Path]:
    """复制到同任务 history/，并尽量登记 _task.json。"""
    history_dir = json_path.parent.parent / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    dest = history_dir / f"{json_path.stem}.pre-ch5-decimal-{STAMP}{json_path.suffix}"
    if dest.exists():
        dest = (
            history_dir
            / f"{json_path.stem}.pre-ch5-decimal-{STAMP}-{json_path.stat().st_mtime_ns}{json_path.suffix}"
        )
    shutil.copy2(json_path, dest)
    try:
        meta = json.loads(task_path.read_text(encoding="utf-8"))
        files = meta.setdefault("files", {})
        hist = files.setdefault("history", [])
        if not isinstance(hist, list):
            hist = []
            files["history"] = hist
        media = _media_root()
        try:
            rel = str(dest.relative_to(media)).replace("\\", "/")
        except ValueError:
            rel = dest.name
        hist.append(
            {
                "role": "json",
                "disk_name": dest.name,
                "relative_path": rel,
                "note": f"pre-ch5-decimal-{STAMP}",
            }
        )
        task_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass
    return dest


def apply_chapter5_decimal_precision_update(
    *,
    dry_run: bool = False,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    扫描全部现场记录活动模板 JSON，写入第五章小数精度。
    dry_run=True 时只统计不写盘。
    """
    paths = iter_active_site_template_json_paths()
    if limit is not None:
        paths = paths[: max(0, int(limit))]

    result: Dict[str, Any] = {
        "ok": True,
        "dry_run": bool(dry_run),
        "scanned": len(paths),
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "mean_fields": 0,
        "report_fields": 0,
        "details": [],
        "errors": [],
    }

    for json_path, task_path in paths:
        rel = str(json_path)
        try:
            media = _media_root()
            try:
                rel = str(json_path.relative_to(media)).replace("\\", "/")
            except ValueError:
                rel = str(json_path)
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                result["skipped"] += 1
                continue
            before = copy.deepcopy(payload)
            stats = apply_chapter5_decimal_precision_to_template_payload(payload)
            if stats.get("fields_touched", 0) <= 0:
                result["skipped"] += 1
                continue
            if payload == before:
                result["skipped"] += 1
                continue
            result["mean_fields"] += int(stats.get("mean") or 0)
            result["report_fields"] += int(stats.get("report") or 0)
            detail = {
                "path": rel,
                "mean": stats.get("mean") or 0,
                "report": stats.get("report") or 0,
            }
            if not dry_run:
                _backup_to_history(json_path, task_path)
                json_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            result["updated"] += 1
            result["details"].append(detail)
        except Exception as exc:
            result["failed"] += 1
            result["errors"].append({"path": rel, "error": str(exc)})

    if result["failed"] and not result["updated"]:
        result["ok"] = False
    return result
