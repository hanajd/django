# -*- coding: utf-8 -*-
"""F.1 评价报告表「项目」存档：列表 / 新建 / 打开 / 保存到后台工作区。"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from apps.core import f1_eval_service as svc

# 在工作区与项目存档之间同步的子目录（不含共享 templates）
_SYNC_DIRS = ("data", "assets", "uploads", "output", "base")


def projects_root(ws: Path) -> Path:
    root = ws / "projects"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _index_path(ws: Path) -> Path:
    return projects_root(ws) / "index.json"


def _current_path(ws: Path) -> Path:
    return ws / "current_project.json"


def _project_dir(ws: Path, project_id: str) -> Path:
    pid = _safe_project_id(project_id)
    return projects_root(ws) / pid


def _safe_project_id(project_id: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_\-]", "", str(project_id or "").strip())
    if not s or ".." in s:
        raise ValueError("非法项目 ID")
    return s


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_index(ws: Path) -> List[Dict[str, Any]]:
    raw = _read_json(_index_path(ws), [])
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict) and x.get("id")]


def _save_index(ws: Path, items: List[Dict[str, Any]]) -> None:
    _write_json(_index_path(ws), items)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _project_display_name(ws: Path, fallback: str = "未命名项目") -> str:
    data = svc.load_report_data(ws)
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    name = str(fields.get("project_name") or "").strip()
    return name or fallback


def get_current_project(ws: Path) -> Optional[Dict[str, Any]]:
    cur = _read_json(_current_path(ws), {})
    if not isinstance(cur, dict):
        return None
    pid = str(cur.get("id") or "").strip()
    if not pid:
        return None
    for item in _load_index(ws):
        if str(item.get("id")) == pid:
            return dict(item)
    return {"id": pid, "name": cur.get("name") or pid}


def set_current_project(ws: Path, project_id: str, name: str = "") -> Dict[str, Any]:
    pid = _safe_project_id(project_id)
    meta = {"id": pid, "name": name or pid}
    _write_json(_current_path(ws), meta)
    return meta


def list_projects(ws: Path) -> List[Dict[str, Any]]:
    ensure_projects_initialized(ws)
    items = _load_index(ws)
    items.sort(key=lambda x: str(x.get("updated_at") or ""), reverse=True)
    cur = get_current_project(ws)
    cur_id = str((cur or {}).get("id") or "")
    for it in items:
        it["is_current"] = str(it.get("id")) == cur_id
    return items


def _copy_sync_dirs(src_root: Path, dst_root: Path) -> None:
    dst_root.mkdir(parents=True, exist_ok=True)
    for name in _SYNC_DIRS:
        src = src_root / name
        dst = dst_root / name
        if dst.exists():
            shutil.rmtree(dst)
        if src.exists():
            shutil.copytree(src, dst)
        else:
            dst.mkdir(parents=True, exist_ok=True)


def _init_empty_project_tree(project_ws: Path) -> None:
    for name in _SYNC_DIRS:
        (project_ws / name).mkdir(parents=True, exist_ok=True)
    data_path = project_ws / "data" / "report_data.json"
    if not data_path.exists():
        data: Dict[str, Any] = {"fields": {}, "attachment_pages": []}
        svc.ensure_fixed_field_defaults(data, ws=project_ws)
        data_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    # 版式：若空则从包默认拷贝
    pkg = svc.package_dir()
    base = project_ws / "base"
    base.mkdir(parents=True, exist_ok=True)
    for fname in ("format.json", "attachment_format.json"):
        dst = base / fname
        src = pkg / "base" / fname
        if not dst.exists() and src.exists():
            shutil.copy2(src, dst)


def ensure_projects_initialized(ws: Path) -> Dict[str, Any]:
    """
    首次使用：把当前工作区内容登记为默认项目，便于之后打开/保存。
    """
    items = _load_index(ws)
    cur = get_current_project(ws)
    if items:
        if not cur:
            set_current_project(ws, str(items[0]["id"]), str(items[0].get("name") or ""))
        return get_current_project(ws) or items[0]

    pid = "p_" + uuid.uuid4().hex[:10]
    name = _project_display_name(ws, "默认项目")
    pdir = _project_dir(ws, pid)
    _copy_sync_dirs(ws, pdir)
    # 若工作区几乎为空，也保证项目目录结构完整
    _init_empty_project_tree(pdir)
    meta = {
        "id": pid,
        "name": name,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    _save_index(ws, [meta])
    set_current_project(ws, pid, name)
    return meta


def create_project(ws: Path, name: str = "") -> Dict[str, Any]:
    ensure_projects_initialized(ws)
    pid = "p_" + uuid.uuid4().hex[:10]
    title = (name or "").strip() or "新建项目"
    pdir = _project_dir(ws, pid)
    _init_empty_project_tree(pdir)
    # 写入项目名到 report_data
    data_path = pdir / "data" / "report_data.json"
    data = _read_json(data_path, {"fields": {}, "attachment_pages": []})
    if not isinstance(data, dict):
        data = {"fields": {}, "attachment_pages": []}
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    fields["project_name"] = title
    data["fields"] = fields
    svc.ensure_fixed_field_defaults(data, ws=pdir)
    _write_json(data_path, data)

    meta = {
        "id": pid,
        "name": title,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    items = _load_index(ws)
    items.append(meta)
    _save_index(ws, items)
    return meta


def save_project(
    ws: Path,
    *,
    project_id: Optional[str] = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """把当前编辑工作区内容保存到指定（或当前）项目存档。"""
    ensure_projects_initialized(ws)
    cur = get_current_project(ws)
    pid = _safe_project_id(project_id or (cur or {}).get("id") or "")
    items = _load_index(ws)
    found = None
    for it in items:
        if str(it.get("id")) == pid:
            found = it
            break
    if not found:
        raise FileNotFoundError(f"项目不存在: {pid}")

    display = (name or "").strip() or _project_display_name(ws, str(found.get("name") or "未命名项目"))
    # 同步项目名到 report_data
    data = svc.load_report_data(ws)
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    fields["project_name"] = display
    data["fields"] = fields
    svc.save_report_data(ws, data)

    pdir = _project_dir(ws, pid)
    _copy_sync_dirs(ws, pdir)

    found["name"] = display
    found["updated_at"] = _now_iso()
    _save_index(ws, items)
    set_current_project(ws, pid, display)
    return dict(found)


def open_project(ws: Path, project_id: str) -> Dict[str, Any]:
    """打开项目：将存档覆盖到当前编辑工作区。"""
    ensure_projects_initialized(ws)
    pid = _safe_project_id(project_id)
    pdir = _project_dir(ws, pid)
    if not pdir.is_dir():
        raise FileNotFoundError(f"项目目录不存在: {pid}")
    items = _load_index(ws)
    found = None
    for it in items:
        if str(it.get("id")) == pid:
            found = it
            break
    if not found:
        raise FileNotFoundError(f"项目不存在: {pid}")

    _copy_sync_dirs(pdir, ws)
    for name in ("uploads", "assets", "output", "data", "base"):
        (ws / name).mkdir(parents=True, exist_ok=True)
    (ws / "uploads" / "attachments").mkdir(parents=True, exist_ok=True)
    set_current_project(ws, pid, str(found.get("name") or ""))
    return dict(found)


def delete_project(ws: Path, project_id: str) -> Dict[str, Any]:
    pid = _safe_project_id(project_id)
    items = _load_index(ws)
    cur = get_current_project(ws)
    if cur and str(cur.get("id")) == pid:
        raise ValueError("不能删除当前正在编辑的项目，请先打开其他项目")
    new_items = [it for it in items if str(it.get("id")) != pid]
    if len(new_items) == len(items):
        raise FileNotFoundError(f"项目不存在: {pid}")
    pdir = _project_dir(ws, pid)
    if pdir.exists():
        shutil.rmtree(pdir)
    _save_index(ws, new_items)
    return {"deleted": pid, "count": len(new_items)}
