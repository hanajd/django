"""委托管理：签发流程有效进度（签名推断 + 手动推进）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from django.contrib.auth.models import User

from apps.core.library_access import (
    _role_code,
    library_user_may_assign_on_project,
)
from apps.core.models import (
    InspectionCase,
    InspectionCaseWorkflowState,
    InspectionSubmission,
    LibraryProject,
    LibraryProjectWorkflowMember,
)
from apps.core.project_workflow_ui import workflow_stage_index
from apps.core.workflow_service import get_or_create_workflow_state, transition_case_stage

# 手动推进：目标环节 ← 所需项目内岗位
MANUAL_ADVANCE_STEPS: tuple[tuple[str, str, str], ...] = (
    (
        InspectionCaseWorkflowState.STAGE_SITE_REVIEW,
        LibraryProjectWorkflowMember.ROLE_FIELD_INSPECTOR,
        "确认现场完成",
    ),
    (
        InspectionCaseWorkflowState.STAGE_REPORT_DRAFT,
        LibraryProjectWorkflowMember.ROLE_SITE_REVIEWER,
        "确认校核完成",
    ),
    (
        InspectionCaseWorkflowState.STAGE_REPORT_AUDIT,
        LibraryProjectWorkflowMember.ROLE_REPORT_AUTHOR,
        "确认编制完成",
    ),
    (
        InspectionCaseWorkflowState.STAGE_REPORT_SIGN,
        LibraryProjectWorkflowMember.ROLE_REPORT_AUDITOR,
        "确认审核完成",
    ),
    (
        InspectionCaseWorkflowState.STAGE_ISSUED,
        LibraryProjectWorkflowMember.ROLE_AUTH_SIGNATORY,
        "确认签发",
    ),
)

# 报告三环节：须叠印个人签名后推进（委托管理 Web）
REPORT_SIGN_ADVANCE_TARGETS: frozenset[str] = frozenset(
    {
        InspectionCaseWorkflowState.STAGE_REPORT_AUDIT,
        InspectionCaseWorkflowState.STAGE_REPORT_SIGN,
        InspectionCaseWorkflowState.STAGE_ISSUED,
    }
)

REPORT_SIGN_ADVANCE_LABELS: dict[str, str] = {
    InspectionCaseWorkflowState.STAGE_REPORT_AUDIT: "使用我的签名，确认编制完成",
    InspectionCaseWorkflowState.STAGE_REPORT_SIGN: "使用我的签名，确认审核通过",
    InspectionCaseWorkflowState.STAGE_ISSUED: "使用我的签名并签发",
}

_GLOBAL_ROLE_TO_WORKFLOW: Dict[str, str] = {
    "field_inspector": LibraryProjectWorkflowMember.ROLE_FIELD_INSPECTOR,
    "site_reviewer": LibraryProjectWorkflowMember.ROLE_SITE_REVIEWER,
    "report_author": LibraryProjectWorkflowMember.ROLE_REPORT_AUTHOR,
    "report_auditor": LibraryProjectWorkflowMember.ROLE_REPORT_AUDITOR,
    "authorized_signatory": LibraryProjectWorkflowMember.ROLE_AUTH_SIGNATORY,
    "app_user": LibraryProjectWorkflowMember.ROLE_FIELD_INSPECTOR,
}


def _stage_at_least(current: str, minimum: str) -> str:
    ci = workflow_stage_index(current)
    mi = workflow_stage_index(minimum)
    if ci < 0:
        return minimum
    if mi < 0:
        return current
    return current if ci >= mi else minimum


def submission_has_checker_signature(sub: InspectionSubmission | None) -> bool:
    """提交是否含校核员签名（库字段或 raw_payload.signatures）。"""
    if sub is None:
        return False
    if sub.sign_reviewer_png:
        return bool(bytes(sub.sign_reviewer_png))
    raw = sub.raw_payload if isinstance(sub.raw_payload, dict) else {}
    sig = raw.get("signatures") if isinstance(raw.get("signatures"), dict) else {}
    for key in ("checker", "reviewer", "校核", "校核员"):
        val = sig.get(key)
        if val is None:
            continue
        if isinstance(val, str) and val.strip():
            return True
        if isinstance(val, dict) and (val.get("path") or val.get("url") or val.get("data")):
            return True
    return False


def stored_case_workflow_stage(case: InspectionCase | None) -> str:
    if case is None:
        return ""
    st = getattr(case, "workflow_state", None)
    if st is None:
        return InspectionCaseWorkflowState.STAGE_SITE_FILL
    return str(st.stage or InspectionCaseWorkflowState.STAGE_SITE_FILL)


def infer_stage_floor_from_submission(sub: InspectionSubmission | None) -> str:
    """根据提交内容推断至少应处于的环节（签名等）。"""
    if sub is None:
        return ""
    # 有校核员签名表示校核已完成，至少应处于编制报告环节
    if submission_has_checker_signature(sub):
        return InspectionCaseWorkflowState.STAGE_REPORT_DRAFT
    return ""


def effective_workflow_stage(
    case: InspectionCase | None,
    sub: InspectionSubmission | None,
) -> str:
    """DB 环节与签名推断取较后者（仅向前，不后退）。"""
    stored = stored_case_workflow_stage(case)
    if not stored:
        stored = InspectionCaseWorkflowState.STAGE_SITE_FILL
    inferred = infer_stage_floor_from_submission(sub)
    if inferred:
        return _stage_at_least(stored, inferred)
    return stored


def user_may_manual_advance_to_stage(
    user: User,
    project: LibraryProject,
    *,
    required_workflow_role: str,
) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False) or _role_code(user) in ("super_admin", "admin"):
        return True
    if library_user_may_assign_on_project(user, project):
        return True
    if LibraryProjectWorkflowMember.objects.filter(
        project=project, user=user, workflow_role=required_workflow_role
    ).exists():
        return True
    mapped = _GLOBAL_ROLE_TO_WORKFLOW.get(_role_code(user) or "")
    return mapped == required_workflow_role


def manual_advance_actions_for_item(
    user: User,
    project: LibraryProject,
    *,
    effective_stage: str,
    has_case: bool,
) -> List[Dict[str, Any]]:
    """当前用户可对单条检测项目执行的手动推进操作。"""
    if not has_case:
        return []
    cur_idx = workflow_stage_index(effective_stage)
    out: List[Dict[str, Any]] = []
    for target_stage, workflow_role, label in MANUAL_ADVANCE_STEPS:
        tidx = workflow_stage_index(target_stage)
        if cur_idx >= tidx:
            continue
        if not user_may_manual_advance_to_stage(
            user, project, required_workflow_role=workflow_role
        ):
            continue
        out.append(
            {
                "target_stage": target_stage,
                "workflow_role": workflow_role,
                "label": (
                    REPORT_SIGN_ADVANCE_LABELS.get(target_stage, label)
                    if target_stage in REPORT_SIGN_ADVANCE_TARGETS
                    else label
                ),
                "action": (
                    "sign_and_advance_workflow"
                    if target_stage in REPORT_SIGN_ADVANCE_TARGETS
                    else "advance_workflow"
                ),
                "requires_report_signature": target_stage in REPORT_SIGN_ADVANCE_TARGETS,
            }
        )
    return out


def apply_manual_workflow_advance(
    *,
    user: User,
    project: LibraryProject,
    submission: InspectionSubmission,
    target_stage: str,
) -> tuple[bool, str]:
    """
    手动推进案件环节。返回 (ok, message)。
    仅允许向前推进到指定 target，且须具备对应岗位权限。
    """
    if submission.case_id is None:
        return False, "该任务尚无关联案件，无法推进"
    case = submission.case
    if case.library_project_id and int(case.library_project_id) != int(project.pk):
        return False, "案件与委托不匹配"

    required_role: str | None = None
    for ts, role, _label in MANUAL_ADVANCE_STEPS:
        if ts == target_stage:
            required_role = role
            break
    if required_role is None:
        return False, "无效的推进目标环节"
    if not user_may_manual_advance_to_stage(
        user, project, required_workflow_role=required_role
    ):
        return False, "无权执行该环节推进"

    effective = effective_workflow_stage(case, submission)
    cur_idx = workflow_stage_index(effective)
    tgt_idx = workflow_stage_index(target_stage)
    if tgt_idx < 0:
        return False, "无效的推进目标环节"
    if cur_idx >= tgt_idx:
        return True, "该环节已推进，无需重复操作"

    get_or_create_workflow_state(case)
    if target_stage == InspectionCaseWorkflowState.STAGE_ISSUED:
        from apps.core.workflow_service import set_issue_date_from_signatory

        set_issue_date_from_signatory(case, user=user)
    else:
        transition_case_stage(case, target_stage, user=user, return_reason="")
    return True, "已推进流程环节"
