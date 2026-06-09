"""SQLite 数据库备份：使用 sqlite3 backup API，避免直接复制时写入不一致。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.conf import settings


def sqlite_database_path() -> Path:
    return Path(settings.DATABASES["default"]["NAME"]).resolve()


def default_backup_dir() -> Path:
    custom = getattr(settings, "DATABASE_BACKUP_DIR", None)
    if custom:
        return Path(custom).resolve()
    return Path(settings.BASE_DIR) / "backups" / "db"


def backup_sqlite_database(
    *,
    label: str = "",
    keep: int | None = None,
) -> dict[str, Any]:
    """
    在线备份当前 SQLite 库到 backups/db/，并裁剪旧备份。

    返回 {"ok": True, "path": str, "size": int, "pruned": int} 或 {"ok": False, "error": str}
    """
    db_path = sqlite_database_path()
    if not db_path.is_file():
        return {"ok": False, "error": f"数据库文件不存在：{db_path}"}

    backup_dir = default_backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_label = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (label or "").strip())
    suffix = f"_{safe_label}" if safe_label else ""
    dest = backup_dir / f"db_{ts}{suffix}.sqlite3"

    try:
        src_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
        dst_conn = sqlite3.connect(str(dest), timeout=30)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()
    except Exception as exc:
        if dest.exists():
            dest.unlink(missing_ok=True)
        return {"ok": False, "error": str(exc)}

    meta = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": str(db_path),
        "label": label or "",
        "size": dest.stat().st_size,
    }
    meta_path = dest.with_suffix(dest.suffix + ".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    keep_n = keep if keep is not None else int(getattr(settings, "DATABASE_BACKUP_KEEP", 48))
    pruned = _prune_old_backups(backup_dir, keep=max(1, keep_n))

    return {
        "ok": True,
        "path": str(dest),
        "meta_path": str(meta_path),
        "size": meta["size"],
        "pruned": pruned,
    }


def list_database_backups(*, limit: int = 20) -> list[dict[str, Any]]:
    backup_dir = default_backup_dir()
    if not backup_dir.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(backup_dir.glob("db_*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True):
        if limit and len(rows) >= limit:
            break
        meta_path = path.with_suffix(path.suffix + ".meta.json")
        meta: dict[str, Any] = {}
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        rows.append(
            {
                "path": str(path),
                "size": path.stat().st_size,
                "mtime": path.stat().st_mtime,
                "label": meta.get("label") or "",
                "created_at": meta.get("created_at") or "",
            }
        )
    return rows


def restore_sqlite_database_from_backup(backup_path: Path, *, pre_restore_label: str = "pre_restore") -> dict[str, Any]:
    """从备份恢复数据库（恢复前会自动再备一份当前库）。"""
    src = Path(backup_path).resolve()
    if not src.is_file():
        return {"ok": False, "error": f"备份不存在：{src}"}

    pre = backup_sqlite_database(label=pre_restore_label)
    if not pre.get("ok"):
        return {"ok": False, "error": f"恢复前备份失败：{pre.get('error')}"}

    db_path = sqlite_database_path()
    try:
        src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
        dst_conn = sqlite3.connect(str(db_path), timeout=30)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "pre_restore_backup": pre.get("path")}

    return {"ok": True, "restored_from": str(src), "pre_restore_backup": pre.get("path")}


def _prune_old_backups(backup_dir: Path, *, keep: int) -> int:
    files = sorted(backup_dir.glob("db_*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
    pruned = 0
    for old in files[keep:]:
        meta = old.with_suffix(old.suffix + ".meta.json")
        old.unlink(missing_ok=True)
        meta.unlink(missing_ok=True)
        pruned += 1
    return pruned


def maybe_auto_backup_on_startup() -> dict[str, Any] | None:
    """
    DEBUG 开发环境下，进程启动时若距上次备份超过间隔则自动备份一次。
    由 AppConfig.ready 调用；生产环境默认关闭（DATABASE_AUTO_BACKUP_ON_STARTUP=False）。
    """
    if not getattr(settings, "DATABASE_AUTO_BACKUP_ON_STARTUP", settings.DEBUG):
        return None
    interval_h = float(getattr(settings, "DATABASE_AUTO_BACKUP_INTERVAL_HOURS", 12))
    backup_dir = default_backup_dir()
    latest_mtime = 0.0
    if backup_dir.is_dir():
        for path in backup_dir.glob("db_*.sqlite3"):
            latest_mtime = max(latest_mtime, path.stat().st_mtime)
    if latest_mtime:
        age_h = (datetime.now(timezone.utc).timestamp() - latest_mtime) / 3600.0
        if age_h < interval_h:
            return None
    return backup_sqlite_database(label="auto_startup")
