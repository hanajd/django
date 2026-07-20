"""医院信息 / 委托项目写库操作日志。"""

from __future__ import annotations

import json
import threading
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from django.db.models import QuerySet
from django.utils import timezone

from apps.core.models import BizOperationLog, CommissionOrganization, LibraryProject

_prune_lock = threading.Lock()
_writes_since_prune = 0


def _log_limits() -> tuple[int, int, int, int]:
    max_rows = max(100, int(getattr(settings, "BIZ_OPERATION_LOG_MAX_ROWS", 5000) or 5000))
    retention_days = max(1, int(getattr(settings, "BIZ_OPERATION_LOG_RETENTION_DAYS", 180) or 180))
    detail_max = max(200, int(getattr(settings, "BIZ_OPERATION_LOG_DETAIL_MAX_CHARS", 2000) or 2000))
    prune_every = max(1, int(getattr(settings, "BIZ_OPERATION_LOG_PRUNE_EVERY", 20) or 20))
    return max_rows, retention_days, detail_max, prune_every


def _compact_detail(detail: dict[str, Any] | None, max_chars: int) -> dict[str, Any]:
    if not isinstance(detail, dict) or not detail:
        return {}
    try:
        raw = json.dumps(detail, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {"_note": "detail_omitted"}
    if len(raw) <= max_chars:
        return detail
    # 超长则只保留截断说明，避免 JSON 字段膨胀
    return {
        "_truncated": True,
        "_preview": raw[: max(0, max_chars - 80)],
    }


def prune_biz_operation_logs(*, force: bool = False) -> dict[str, int]:
    """按保留天数与最大条数清理旧日志。返回删除统计。"""
    max_rows, retention_days, _, prune_every = _log_limits()
    global _writes_since_prune
    with _prune_lock:
        if not force:
            _writes_since_prune += 1
            if _writes_since_prune < prune_every:
                return {"deleted_by_age": 0, "deleted_by_count": 0}
            _writes_since_prune = 0

    deleted_by_age = 0
    deleted_by_count = 0
    try:
        cutoff = timezone.now() - timedelta(days=retention_days)
        deleted_by_age, _ = BizOperationLog.objects.filter(created_at__lt=cutoff).delete()

        total = BizOperationLog.objects.count()
        excess = total - max_rows
        if excess > 0:
            old_ids = list(
                BizOperationLog.objects.order_by("created_at", "id").values_list("id", flat=True)[
                    :excess
                ]
            )
            if old_ids:
                deleted_by_count, _ = BizOperationLog.objects.filter(id__in=old_ids).delete()
    except Exception:
        return {"deleted_by_age": deleted_by_age, "deleted_by_count": deleted_by_count}
    return {"deleted_by_age": deleted_by_age, "deleted_by_count": deleted_by_count}


def record_biz_operation(
    *,
    actor: User | None,
    scope: str,
    action: str,
    summary: str,
    organization: CommissionOrganization | None = None,
    project: LibraryProject | None = None,
    entity_type: str = "",
    entity_id: int | None = None,
    detail: dict[str, Any] | None = None,
) -> BizOperationLog | None:
    """记录一条写库相关操作；失败时静默（不影响主流程）。"""
    summary = (summary or "").strip()
    if not summary:
        return None
    if scope not in (BizOperationLog.SCOPE_HOSPITAL, BizOperationLog.SCOPE_PROJECT):
        scope = BizOperationLog.SCOPE_PROJECT
    valid_actions = {c for c, _ in BizOperationLog.ACTION_CHOICES}
    if action not in valid_actions:
        action = BizOperationLog.ACTION_OTHER
    _, _, detail_max, _ = _log_limits()
    try:
        org = organization
        proj = project
        if org is None and proj is not None and getattr(proj, "commission_org_id", None):
            org = getattr(proj, "commission_org", None)
        row = BizOperationLog.objects.create(
            scope=scope,
            action=action,
            organization=org,
            project=proj,
            entity_type=(entity_type or "")[:64],
            entity_id=entity_id,
            summary=summary[:512],
            detail=_compact_detail(detail, detail_max),
            actor=actor if getattr(actor, "is_authenticated", False) else None,
        )
        prune_biz_operation_logs(force=False)
        return row
    except Exception:
        return None


def biz_operation_logs_queryset(
    *,
    scope: str,
    organization_id: int | None = None,
    project_id: int | None = None,
) -> QuerySet[BizOperationLog]:
    qs = BizOperationLog.objects.select_related("actor", "organization", "project")
    if scope == BizOperationLog.SCOPE_HOSPITAL:
        qs = qs.filter(scope=BizOperationLog.SCOPE_HOSPITAL)
        if organization_id:
            qs = qs.filter(organization_id=organization_id)
        return qs
    if scope == BizOperationLog.SCOPE_PROJECT:
        qs = qs.filter(scope=BizOperationLog.SCOPE_PROJECT)
        if project_id:
            qs = qs.filter(project_id=project_id)
        return qs
    return qs.none()


def hospital_logs_for_org_subtree(org: CommissionOrganization) -> QuerySet[BizOperationLog]:
    """当前机构及其下级相关的医院信息日志。"""
    from apps.core.commission_org_service import commission_org_subtree_ids

    ids = list(commission_org_subtree_ids(org.pk)) if org else []
    if not ids:
        return BizOperationLog.objects.none()
    return (
        BizOperationLog.objects.filter(
            scope=BizOperationLog.SCOPE_HOSPITAL,
            organization_id__in=ids,
        )
        .select_related("actor", "organization", "project")
        .order_by("-created_at", "-id")
    )


def serialize_biz_operation_log(row: BizOperationLog) -> dict[str, Any]:
    org_label = ""
    if row.organization_id and row.organization:
        org_label = row.organization.full_display_name or row.organization.name
    proj_label = ""
    if row.project_id and row.project:
        proj_label = f"{row.project.code} · {row.project.name}"
    return {
        "id": row.pk,
        "scope": row.scope,
        "action": row.action,
        "action_label": row.action_label,
        "summary": row.summary,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "organization_label": org_label,
        "project_label": proj_label,
        "actor": row.actor_label,
        "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
        "detail": row.detail if isinstance(row.detail, dict) else {},
    }
