"""
后台「流程练习」会话：在导读/练习期间登记用户新建的项目与任务模板，结束时统一删除。
仅面向与使用说明相同的引导账号（party_a 限制）；数据仅限 created_by 为当前用户且 id 在会话列表内。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from django.db import transaction

from apps.core.library_access import library_user_has_party_a_demo_restrictions
from apps.core.models import LibraryFile, LibraryProject, LibraryTask

logger = logging.getLogger(__name__)

SESSION_KEY = "usage_workflow_tour"


def tour_session(request) -> Dict[str, Any]:
    raw = request.session.get(SESSION_KEY)
    return raw if isinstance(raw, dict) else {}


def tour_is_active(request) -> bool:
    return bool(tour_session(request).get("active"))


def _touch(request) -> Dict[str, Any]:
    data = tour_session(request)
    if not data.get("active"):
        return data
    request.session[SESSION_KEY] = data
    request.session.modified = True
    return data


def tour_start(request) -> bool:
    if not getattr(request.user, "is_authenticated", False):
        return False
    if not library_user_has_party_a_demo_restrictions(request.user):
        return False
    # 若上次练习未正常结束，先清掉残留登记，避免孤儿数据
    tour_cleanup_and_clear_session(request)
    request.session[SESSION_KEY] = {
        "active": True,
        "project_ids": [],
        "task_ids": [],
        "library_file_ids": [],
    }
    request.session.modified = True
    return True


def register_tour_project(request, project_id: int) -> None:
    if not tour_is_active(request) or project_id <= 0:
        return
    data = _touch(request)
    ids: List[int] = data.setdefault("project_ids", [])
    if project_id not in ids:
        ids.append(project_id)


def register_tour_task(request, task_id: int) -> None:
    if not tour_is_active(request) or task_id <= 0:
        return
    data = _touch(request)
    ids: List[int] = data.setdefault("task_ids", [])
    if task_id not in ids:
        ids.append(task_id)


def register_tour_library_file(request, file_id: int) -> None:
    if not tour_is_active(request) or file_id <= 0:
        return
    data = _touch(request)
    ids: List[int] = data.setdefault("library_file_ids", [])
    if file_id not in ids:
        ids.append(file_id)


def tour_cleanup_and_clear_session(request) -> Dict[str, int]:
    """
    删除本会话登记的练习数据并清空 session 键。
    返回各类删除数量（尽力而为，单条失败不影响其它）。
    """
    data = tour_session(request)
    request.session.pop(SESSION_KEY, None)
    request.session.modified = True
    if not data.get("active"):
        return {"projects": 0, "tasks": 0, "library_files": 0}

    user = request.user
    pids = [int(x) for x in (data.get("project_ids") or []) if int(x) > 0]
    tids = [int(x) for x in (data.get("task_ids") or []) if int(x) > 0]
    fids = [int(x) for x in (data.get("library_file_ids") or []) if int(x) > 0]

    deleted_projects = 0
    deleted_tasks = 0
    deleted_files = 0

    with transaction.atomic():
        for tid in tids:
            t = LibraryTask.objects.filter(pk=tid, created_by=user).first()
            if not t:
                continue
            try:
                t.projects.clear()
                t.delete()
                deleted_tasks += 1
            except Exception as exc:
                logger.warning("usage_workflow_tour: delete task %s failed: %s", tid, exc)

        for pid in pids:
            p = LibraryProject.objects.filter(pk=pid, created_by=user).first()
            if not p:
                continue
            try:
                p.library_tasks.clear()
                p.delete()
                deleted_projects += 1
            except Exception as exc:
                logger.warning("usage_workflow_tour: delete project %s failed: %s", pid, exc)

        for fid in fids:
            lf = LibraryFile.objects.filter(pk=fid, created_by=user).first()
            if not lf:
                continue
            try:
                lf.delete()
                deleted_files += 1
            except Exception as exc:
                logger.warning("usage_workflow_tour: delete library file %s failed: %s", fid, exc)

    return {"projects": deleted_projects, "tasks": deleted_tasks, "library_files": deleted_files}
