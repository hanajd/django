"""检验案件流程：环节顺序、退回上一环节（供视图/API 调用）。"""
from __future__ import annotations

from typing import Optional

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from apps.core.models import InspectionCaseWorkflowState


def _wf() -> type[InspectionCaseWorkflowState]:
    return InspectionCaseWorkflowState


def stage_sequence() -> tuple[str, ...]:
    w = _wf()
    return (
        w.STAGE_SITE_FILL,
        w.STAGE_SITE_REVIEW,
        w.STAGE_REPORT_DRAFT,
        w.STAGE_REPORT_AUDIT,
        w.STAGE_REPORT_SIGN,
        w.STAGE_ISSUED,
    )


def previous_stage_for_reject(current: str) -> Optional[str]:
    """后方环节打回时，默认退回的上一业务环节（不可再退则返回 None）。"""
    w = _wf()
    mapping = {
        w.STAGE_ISSUED: w.STAGE_REPORT_SIGN,
        w.STAGE_REPORT_SIGN: w.STAGE_REPORT_AUDIT,
        w.STAGE_REPORT_AUDIT: w.STAGE_REPORT_DRAFT,
        w.STAGE_REPORT_DRAFT: w.STAGE_SITE_REVIEW,
        w.STAGE_SITE_REVIEW: w.STAGE_SITE_FILL,
        w.STAGE_SITE_FILL: None,
    }
    return mapping.get(current)


def get_or_create_workflow_state(case) -> InspectionCaseWorkflowState:
    st, _ = InspectionCaseWorkflowState.objects.get_or_create(
        case=case,
        defaults={"stage": _wf().STAGE_SITE_FILL},
    )
    return st


@transaction.atomic
def transition_case_stage(
    case,
    new_stage: str,
    *,
    user: User | None,
    return_reason: str = "",
    issue_date=None,
) -> InspectionCaseWorkflowState:
    """将案件流程更新为 new_stage，并记录操作者与退回说明。"""
    w = _wf()
    valid = {c for c, _ in w.STAGE_CHOICES}
    if new_stage not in valid:
        raise ValueError(f"invalid stage: {new_stage}")
    st = get_or_create_workflow_state(case)
    st.stage = new_stage
    st.return_reason = (return_reason or "").strip()
    st.updated_by = user
    update_fields = ["stage", "return_reason", "updated_by", "updated_at"]
    if issue_date is not None:
        st.issue_date = issue_date
        update_fields.append("issue_date")
    st.save(update_fields=update_fields)
    return st


@transaction.atomic
def reject_case_to_previous_stage(
    case,
    *,
    user: User | None,
    reason: str,
) -> InspectionCaseWorkflowState | None:
    """将流程退回上一环节；已在最前环节则返回 None。"""
    st = get_or_create_workflow_state(case)
    prev = previous_stage_for_reject(st.stage)
    if prev is None:
        return None
    return transition_case_stage(case, prev, user=user, return_reason=reason)


def set_issue_date_from_signatory(case, *, user: User | None) -> InspectionCaseWorkflowState:
    """授权签字人签发：进入已签发并写入签发日期（默认当天，可由调用方传入）。"""
    w = _wf()
    today = timezone.localdate()
    return transition_case_stage(
        case,
        w.STAGE_ISSUED,
        user=user,
        return_reason="",
        issue_date=today,
    )
