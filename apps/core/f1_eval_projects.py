# -*- coding: utf-8 -*-
"""F.1 评价报告表「项目」存档：共享目录列表 / 新建 / 打开 / 保存 / 指派。

项目实体存放在 ``MEDIA/f1_eval/shared_evaluation/``（不写业务库）；
各用户工作区仅作编辑草稿，打开时从共享拉取，保存时写回共享。
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from django.conf import settings

from apps.core import f1_eval_service as svc

# 在工作区与项目存档之间同步的子目录（不含共享 templates）
_SYNC_DIRS = ("data", "assets", "uploads", "output", "base")


def shared_catalog_root() -> Path:
    root = Path(
        getattr(
            settings,
            "F1_EVAL_SHARED_ROOT",
            Path(settings.MEDIA_ROOT) / "f1_eval" / "shared_evaluation",
        )
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def projects_root(ws: Path | None = None) -> Path:
    """共享项目根目录（``ws`` 参数保留兼容，不再按用户隔离项目实体）。"""
    del ws
    root = shared_catalog_root() / "projects"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _index_path(ws: Path | None = None) -> Path:
    return projects_root(ws) / "index.json"


def _current_path(ws: Path) -> Path:
    return ws / "current_project.json"


def _project_dir(ws: Path | None, project_id: str) -> Path:
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


def _load_index(ws: Path | None = None) -> List[Dict[str, Any]]:
    raw = _read_json(_index_path(ws), [])
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict) and x.get("id")]


def _save_index(ws: Path | None, items: List[Dict[str, Any]]) -> None:
    _write_json(_index_path(ws), items)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _normalize_assignee_ids(raw: Any) -> List[int]:
    out: List[int] = []
    if not isinstance(raw, (list, tuple)):
        return out
    for x in raw:
        try:
            uid = int(x)
        except (TypeError, ValueError):
            continue
        if uid > 0 and uid not in out:
            out.append(uid)
    return out


def _assignee_labels(ids: Sequence[int]) -> List[Dict[str, Any]]:
    if not ids:
        return []
    from django.contrib.auth.models import User

    by_id = {
        u.pk: u
        for u in User.objects.filter(pk__in=list(ids)).select_related("profile", "profile__role")
    }
    rows: List[Dict[str, Any]] = []
    for uid in ids:
        u = by_id.get(uid)
        if u is None:
            rows.append({"id": uid, "username": f"#{uid}", "role_name": ""})
            continue
        role = getattr(getattr(u, "profile", None), "role", None)
        rows.append(
            {
                "id": u.pk,
                "username": u.username,
                "role_name": (role.name if role else "") or "",
            }
        )
    return rows


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


def clear_current_project(ws: Path) -> None:
    path = _current_path(ws)
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


def _org_name_from_project_dir(ws: Path | None, project_id: str) -> str:
    data_path = _project_dir(ws, project_id) / "data" / "report_data.json"
    data = _read_json(data_path, {})
    if not isinstance(data, dict):
        return ""
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    return str(fields.get("org_name") or "").strip()


def _project_search_meta(ws: Path | None, project_id: str) -> Dict[str, str]:
    """从项目存档提取检索用字段（编号、报告号等）。"""
    data_path = _project_dir(ws, project_id) / "data" / "report_data.json"
    data = _read_json(data_path, {})
    fields: Dict[str, Any] = {}
    cover: Dict[str, Any] = {}
    if isinstance(data, dict):
        if isinstance(data.get("fields"), dict):
            fields = data["fields"]
        if isinstance(data.get("cover_meta"), dict):
            cover = data["cover_meta"]
    report_code = str(
        cover.get("report_code") or fields.get("report_code") or ""
    ).strip()
    report_no = str(cover.get("report_no") or fields.get("report_no") or "").strip()
    return {
        "report_code": report_code,
        "report_no": report_no,
        "org_name": str(fields.get("org_name") or "").strip(),
    }


def _enrich_hospital_meta(ws: Path | None, item: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(item)
    hid = out.get("hospital_id")
    hname = str(out.get("hospital_name") or "").strip()
    meta = _project_search_meta(ws, str(out.get("id") or ""))
    if not hname:
        hname = meta.get("org_name") or _org_name_from_project_dir(ws, str(out.get("id") or ""))
        if hname:
            out["hospital_name"] = hname
    if not str(out.get("report_code") or "").strip() and meta.get("report_code"):
        out["report_code"] = meta["report_code"]
    if not str(out.get("report_no") or "").strip() and meta.get("report_no"):
        out["report_no"] = meta["report_no"]
    if hid is not None and hid != "":
        try:
            out["hospital_id"] = int(hid)
        except (TypeError, ValueError):
            out["hospital_id"] = hid
    else:
        out["hospital_id"] = None
    if not out.get("hospital_name"):
        out["hospital_name"] = ""
    out.setdefault("report_code", "")
    out.setdefault("report_no", "")
    assignee_ids = _normalize_assignee_ids(out.get("assignee_ids"))
    out["assignee_ids"] = assignee_ids
    out["assignees"] = _assignee_labels(assignee_ids)
    try:
        out["created_by_id"] = int(out.get("created_by_id") or 0) or None
    except (TypeError, ValueError):
        out["created_by_id"] = None
    out["created_by_username"] = str(out.get("created_by_username") or "").strip()
    return out


def user_may_open_project(user, item: Dict[str, Any]) -> bool:
    from apps.core.library_access import (
        library_user_may_access_f1_eval,
        library_user_may_manage_f1_eval_projects,
    )

    if not library_user_may_access_f1_eval(user):
        return False
    # 主任 / test / 超管等项目管理员可看全部
    if library_user_may_manage_f1_eval_projects(user):
        return True
    uid = int(getattr(user, "pk", 0) or 0)
    if not uid:
        return False
    # 创建人默认可编辑，无需再把自己放进指派列表
    try:
        creator_id = int(item.get("created_by_id") or 0)
    except (TypeError, ValueError):
        creator_id = 0
    if creator_id and creator_id == uid:
        return True
    return uid in _normalize_assignee_ids(item.get("assignee_ids"))


def user_may_edit_project(user, item: Dict[str, Any]) -> bool:
    """创建人、项目管理员、已指派员工可编辑。"""
    return user_may_open_project(user, item)


def _capabilities_for(user) -> Dict[str, bool]:
    from apps.core.library_access import (
        library_user_may_assign_f1_eval_projects,
        library_user_may_manage_f1_eval_projects,
    )

    return {
        "can_create": library_user_may_manage_f1_eval_projects(user),
        "can_delete": library_user_may_manage_f1_eval_projects(user),
        "can_assign": library_user_may_assign_f1_eval_projects(user),
    }


def list_projects(ws: Path, user=None) -> List[Dict[str, Any]]:
    ensure_projects_initialized(ws)
    items = [_enrich_hospital_meta(ws, x) for x in _load_index(ws)]
    dirty = False
    raw = _load_index(ws)
    by_id = {str(x.get("id")): x for x in raw}
    for it in items:
        src = by_id.get(str(it.get("id")))
        if not src:
            continue
        if not str(src.get("hospital_name") or "").strip() and it.get("hospital_name"):
            src["hospital_name"] = it["hospital_name"]
            dirty = True
        if src.get("hospital_id") in (None, "") and it.get("hospital_id") not in (None, ""):
            src["hospital_id"] = it["hospital_id"]
            dirty = True
        norm_ids = _normalize_assignee_ids(src.get("assignee_ids"))
        if src.get("assignee_ids") != norm_ids:
            src["assignee_ids"] = norm_ids
            dirty = True
    if dirty:
        _save_index(ws, raw)
        items = [_enrich_hospital_meta(ws, x) for x in raw]

    if user is not None:
        items = [it for it in items if user_may_open_project(user, it)]

    items.sort(key=lambda x: str(x.get("updated_at") or ""), reverse=True)
    cur = get_current_project(ws)
    cur_id = str((cur or {}).get("id") or "")
    for it in items:
        it["is_current"] = str(it.get("id")) == cur_id
        if user is not None:
            it["can_open"] = True
            it["can_edit"] = user_may_edit_project(user, it)
    return items


def list_projects_grouped(ws: Path, user=None) -> Dict[str, Any]:
    """按医院分组的项目列表（仅文件元数据，不写业务库）。"""
    items = list_projects(ws, user=user)
    groups: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    ungrouped: List[Dict[str, Any]] = []
    for it in items:
        hid = it.get("hospital_id")
        hname = str(it.get("hospital_name") or "").strip()
        if hid not in (None, "") or hname:
            key = f"id:{hid}" if hid not in (None, "") else f"name:{hname}"
            if key not in groups:
                groups[key] = {
                    "key": key,
                    "hospital_id": hid if hid not in (None, "") else None,
                    "hospital_name": hname
                    or (f"医院#{hid}" if hid not in (None, "") else "未命名医院"),
                    "projects": [],
                }
                order.append(key)
            elif hname and not groups[key].get("hospital_name"):
                groups[key]["hospital_name"] = hname
            groups[key]["projects"].append(it)
        else:
            ungrouped.append(it)
    caps = (
        _capabilities_for(user)
        if user is not None
        else {"can_create": False, "can_delete": False, "can_assign": False}
    )
    return {
        "hospitals": [groups[k] for k in order],
        "ungrouped": ungrouped,
        "projects": items,
        "capabilities": caps,
    }


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
    pkg = svc.package_dir()
    base = project_ws / "base"
    base.mkdir(parents=True, exist_ok=True)
    for fname in ("format.json", "attachment_format.json"):
        dst = base / fname
        src = pkg / "base" / fname
        if not dst.exists() and src.exists():
            shutil.copy2(src, dst)


def ensure_projects_initialized(ws: Path) -> Optional[Dict[str, Any]]:
    """
    确保共享索引存在；并把各用户工作区里的旧项目幂等合并进共享目录。
    若已有当前项目指针则返回元数据，否则返回 None。
    """
    projects_root(ws)
    migrate_legacy_user_projects_into_shared()
    items = _load_index(ws)
    if not items:
        _save_index(ws, [])
        return get_current_project(ws)
    cur = get_current_project(ws)
    if cur and any(str(x.get("id")) == str(cur.get("id")) for x in items):
        return cur
    return None


def migrate_legacy_user_projects_into_shared() -> int:
    """
    将 ``workspaces/user_*/projects`` 中尚未进入共享目录的项目合并进来（幂等）。
    解决切换共享库后超管/主任看不到历史项目的问题。
    """
    root = svc.workspace_root()
    if not root.is_dir():
        return 0
    items = _load_index(None)
    by_id = {str(x.get("id")): dict(x) for x in items if x.get("id")}
    imported = 0
    for user_dir in sorted(root.glob("user_*")):
        if not user_dir.is_dir():
            continue
        legacy_index_path = user_dir / "projects" / "index.json"
        if not legacy_index_path.is_file():
            continue
        legacy_items = _read_json(legacy_index_path, [])
        if not isinstance(legacy_items, list):
            continue
        try:
            owner_id = int(str(user_dir.name).split("_", 1)[1])
        except (TypeError, ValueError, IndexError):
            owner_id = 0
        owner_name = ""
        if owner_id:
            try:
                from django.contrib.auth.models import User

                owner_name = (
                    User.objects.filter(pk=owner_id).values_list("username", flat=True).first()
                    or ""
                )
            except Exception:
                owner_name = ""
        for raw in legacy_items:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            try:
                pid = _safe_project_id(str(raw.get("id")))
            except ValueError:
                continue
            legacy_dir = user_dir / "projects" / pid
            shared_dir = _project_dir(None, pid)
            existing = by_id.get(pid)
            if existing is not None:
                # 共享索引已有：若目录缺失则补拷
                if not shared_dir.exists() and legacy_dir.is_dir():
                    shutil.copytree(legacy_dir, shared_dir)
                    imported += 1
                continue
            if legacy_dir.is_dir():
                if shared_dir.exists():
                    shutil.rmtree(shared_dir)
                shutil.copytree(legacy_dir, shared_dir)
            else:
                _init_empty_project_tree(shared_dir)
            meta = dict(raw)
            meta["id"] = pid
            meta.setdefault("assignee_ids", [])
            if not meta.get("created_by_id") and owner_id:
                meta["created_by_id"] = owner_id
            if not meta.get("created_by_username") and owner_name:
                meta["created_by_username"] = owner_name
            meta.setdefault("created_at", _now_iso())
            meta.setdefault("updated_at", meta.get("created_at") or _now_iso())
            by_id[pid] = meta
            imported += 1
    if imported:
        # 保持较新 updated_at 靠前无强制顺序；list_projects 会再排序
        _save_index(None, list(by_id.values()))
    elif not items:
        _save_index(None, [])
    return imported


def create_project(
    ws: Path,
    name: str = "",
    *,
    hospital_id: Any = None,
    hospital_name: str = "",
    user=None,
) -> Dict[str, Any]:
    from apps.core.library_access import library_user_may_manage_f1_eval_projects

    if user is not None and not library_user_may_manage_f1_eval_projects(user):
        raise PermissionError("无权新建评价报告表项目")

    ensure_projects_initialized(ws)
    pid = "p_" + uuid.uuid4().hex[:10]
    title = (name or "").strip() or "新建项目"
    hname = (hospital_name or "").strip()
    hid: Any = None
    if hospital_id not in (None, ""):
        try:
            hid = int(hospital_id)
        except (TypeError, ValueError):
            hid = hospital_id
    pdir = _project_dir(ws, pid)
    _init_empty_project_tree(pdir)
    data_path = pdir / "data" / "report_data.json"
    data = _read_json(data_path, {"fields": {}, "attachment_pages": []})
    if not isinstance(data, dict):
        data = {"fields": {}, "attachment_pages": []}
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    fields["project_name"] = title
    if hname:
        fields["org_name"] = hname
    data["fields"] = fields
    svc.ensure_fixed_field_defaults(data, ws=pdir)
    _write_json(data_path, data)

    created_by_id = int(getattr(user, "pk", 0) or 0) or None
    created_by_username = str(getattr(user, "username", "") or "")
    meta = {
        "id": pid,
        "name": title,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "hospital_id": hid,
        "hospital_name": hname,
        "created_by_id": created_by_id,
        "created_by_username": created_by_username,
        "assignee_ids": [],
    }
    items = _load_index(ws)
    items.append(meta)
    _save_index(ws, items)
    return _enrich_hospital_meta(ws, meta)


def update_project_hospital(
    ws: Path,
    project_id: str,
    *,
    hospital_id: Any = None,
    hospital_name: str = "",
    user=None,
) -> Dict[str, Any]:
    """重新挂载 / 更换所属医院（医院信息管理中的医院节点）。"""
    pid = _safe_project_id(project_id)
    items = _load_index(ws)
    found = None
    for it in items:
        if str(it.get("id")) == pid:
            found = it
            break
    if not found:
        raise FileNotFoundError(f"项目不存在: {pid}")
    if user is not None and not user_may_edit_project(user, found):
        raise PermissionError("无权修改该项目的医院挂载")

    hname = (hospital_name or "").strip()
    hid: Any = None
    if hospital_id not in (None, ""):
        try:
            hid = int(hospital_id)
        except (TypeError, ValueError):
            hid = hospital_id
        # 从医院台账补全名称，保证挂到「医院信息管理」下的正式医院
        if hid is not None and not hname:
            try:
                from apps.core.models import CommissionOrganization

                row = (
                    CommissionOrganization.objects.filter(
                        pk=hid,
                        level=CommissionOrganization.LEVEL_HOSPITAL,
                        is_active=True,
                    )
                    .values("name")
                    .first()
                )
                if row:
                    hname = str(row.get("name") or "").strip()
                else:
                    raise ValueError("所选医院不存在或已停用，请从医院信息管理中选择")
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError(f"读取医院台账失败: {exc}") from exc

    found["hospital_id"] = hid
    found["hospital_name"] = hname
    found["updated_at"] = _now_iso()
    if hname:
        data_path = _project_dir(ws, pid) / "data" / "report_data.json"
        data = _read_json(data_path, {"fields": {}, "attachment_pages": []})
        if isinstance(data, dict):
            fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
            fields["org_name"] = hname
            data["fields"] = fields
            _write_json(data_path, data)
    _save_index(ws, items)
    return _enrich_hospital_meta(ws, found)


def save_project(
    ws: Path,
    *,
    project_id: Optional[str] = None,
    name: Optional[str] = None,
    user=None,
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
    if user is not None and not user_may_edit_project(user, found):
        raise PermissionError("无权保存该项目")

    display = (name or "").strip() or _project_display_name(
        ws, str(found.get("name") or "未命名项目")
    )
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
    return _enrich_hospital_meta(ws, found)


def open_project(ws: Path, project_id: str, user=None) -> Dict[str, Any]:
    """打开项目：将共享存档覆盖到当前编辑工作区。"""
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
    if user is not None and not user_may_open_project(user, found):
        raise PermissionError("该项目未指派给您，无法打开")

    _copy_sync_dirs(pdir, ws)
    for name in ("uploads", "assets", "output", "data", "base"):
        (ws / name).mkdir(parents=True, exist_ok=True)
    (ws / "uploads" / "attachments").mkdir(parents=True, exist_ok=True)
    try:
        from apps.core.f1_eval_service import load_report_data

        load_report_data(ws)
    except Exception:
        pass
    set_current_project(ws, pid, str(found.get("name") or ""))
    return _enrich_hospital_meta(ws, found)


def delete_project(ws: Path, project_id: str, user=None) -> Dict[str, Any]:
    from apps.core.library_access import library_user_may_manage_f1_eval_projects

    if user is not None and not library_user_may_manage_f1_eval_projects(user):
        raise PermissionError("无权删除评价报告表项目")

    pid = _safe_project_id(project_id)
    items = _load_index(ws)
    cur = get_current_project(ws)
    if cur and str(cur.get("id")) == pid:
        raise ValueError("不能删除当前正在编辑的项目，请先打开其他项目或返回大厅")
    new_items = [it for it in items if str(it.get("id")) != pid]
    if len(new_items) == len(items):
        raise FileNotFoundError(f"项目不存在: {pid}")
    pdir = _project_dir(ws, pid)
    if pdir.exists():
        shutil.rmtree(pdir)
    _save_index(ws, new_items)
    return {"deleted": pid, "count": len(new_items)}


def assign_project(
    ws: Path,
    project_id: str,
    *,
    assignee_ids: Sequence[Any],
    user=None,
    replace: bool = True,
) -> Dict[str, Any]:
    """
    指派项目给评价部用户。
    ``replace=True`` 时覆盖原指派列表；否则追加。
    """
    from apps.core.library_access import (
        f1_eval_assignable_users_for,
        library_user_may_assign_f1_eval_projects,
    )

    if user is None or not library_user_may_assign_f1_eval_projects(user):
        raise PermissionError("无权指派评价报告表项目")

    pid = _safe_project_id(project_id)
    items = _load_index(ws)
    found = None
    for it in items:
        if str(it.get("id")) == pid:
            found = it
            break
    if not found:
        raise FileNotFoundError(f"项目不存在: {pid}")

    allowed = {int(u.pk) for u in f1_eval_assignable_users_for(user)}
    requested = _normalize_assignee_ids(assignee_ids)
    invalid = [uid for uid in requested if uid not in allowed]
    if invalid:
        raise PermissionError("只能指派给评价部员工")

    if replace:
        found["assignee_ids"] = requested
    else:
        merged = _normalize_assignee_ids(list(found.get("assignee_ids") or []) + requested)
        found["assignee_ids"] = merged
    found["updated_at"] = _now_iso()
    _save_index(ws, items)
    return _enrich_hospital_meta(ws, found)
