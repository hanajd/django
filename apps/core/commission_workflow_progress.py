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

# 已签字且流程停在该签字推进后的环节时，允许同岗重新叠印覆盖
REPORT_SIGN_RESIGN_SPECS: tuple[tuple[str, str, str, str], ...] = (
    (
        InspectionCaseWorkflowState.STAGE_REPORT_AUDIT,
        "reportAuthor",
        LibraryProjectWorkflowMember.ROLE_REPORT_AUTHOR,
        "重新提交编制人签名",
    ),
    (
        InspectionCaseWorkflowState.STAGE_REPORT_SIGN,
        "reportAuditor",
        LibraryProjectWorkflowMember.ROLE_REPORT_AUDITOR,
        "重新提交审核人签名",
    ),
    (
        InspectionCaseWorkflowState.STAGE_ISSUED,
        "authorizedSignatory",
        LibraryProjectWorkflowMember.ROLE_AUTH_SIGNATORY,
        "重新提交授权签字人签名",
    ),
)

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


# 进入校核/编制前须有现场记录或检测提交数据
_STAGES_REQUIRING_SITE_SOURCE: frozenset[str] = frozenset(
    {
        InspectionCaseWorkflowState.STAGE_SITE_REVIEW,
        InspectionCaseWorkflowState.STAGE_REPORT_DRAFT,
    }
)


def manual_advance_actions_for_item(
    user: User,
    project: LibraryProject,
    *,
    effective_stage: str,
    has_case: bool,
    signed_slots: set[str] | frozenset[str] | None = None,
    has_site_source: bool = True,
    variant_sign_targets: list[dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    """当前用户可对单条检测项目执行的手动推进操作（含可覆盖重签）。

    ``variant_sign_targets``: 各报告版本签字状态，形如
    ``[{"export_variant": "full", "label": "全本报告", "signed_slots": {"reportAuthor"}, "stage_code": "..."}, ...]``。
    多版本时各版本按自身签字链独立生成下一步按钮（互不等待）。
    """
    if not has_case:
        return []
    cur_idx = workflow_stage_index(effective_stage)
    out: List[Dict[str, Any]] = []
    variants = list(variant_sign_targets or [])

    # 非报告签字推进（现场/校核等）：仍按案件总环节，只出下一步
    for target_stage, workflow_role, label in MANUAL_ADVANCE_STEPS:
        if target_stage in REPORT_SIGN_ADVANCE_TARGETS:
            continue
        tidx = workflow_stage_index(target_stage)
        if cur_idx >= tidx:
            continue
        if target_stage in _STAGES_REQUIRING_SITE_SOURCE and not has_site_source:
            continue
        if not user_may_manual_advance_to_stage(
            user, project, required_workflow_role=workflow_role
        ):
            continue
        out.append(
            {
                "target_stage": target_stage,
                "workflow_role": workflow_role,
                "label": label,
                "action": "advance_workflow",
                "requires_report_signature": False,
                "is_resign": False,
                "export_variant": "",
            }
        )
        break

    from apps.core.report_signature_service import (
        ADVANCE_TARGET_SIGN_SLOT,
        variant_report_stage_from_signed_slots,
    )

    expected_for_target = {
        InspectionCaseWorkflowState.STAGE_REPORT_AUDIT: InspectionCaseWorkflowState.STAGE_REPORT_DRAFT,
        InspectionCaseWorkflowState.STAGE_REPORT_SIGN: InspectionCaseWorkflowState.STAGE_REPORT_AUDIT,
        InspectionCaseWorkflowState.STAGE_ISSUED: InspectionCaseWorkflowState.STAGE_REPORT_SIGN,
    }

    if variants:
        for vr in variants:
            v = str(vr.get("export_variant") or "").strip()
            if not v:
                continue
            v_signed = set(vr.get("signed_slots") or [])
            v_stage = str(vr.get("stage_code") or "").strip() or variant_report_stage_from_signed_slots(
                v_signed, case_stage=effective_stage
            )
            vlabel = str(vr.get("label") or v).strip() or v
            for target_stage, workflow_role, label in MANUAL_ADVANCE_STEPS:
                if target_stage not in REPORT_SIGN_ADVANCE_TARGETS:
                    continue
                if v_stage != expected_for_target.get(target_stage):
                    continue
                if not user_may_manual_advance_to_stage(
                    user, project, required_workflow_role=workflow_role
                ):
                    continue
                need_slot = ADVANCE_TARGET_SIGN_SLOT.get(target_stage, "")
                if need_slot and need_slot in v_signed:
                    continue
                base_label = REPORT_SIGN_ADVANCE_LABELS.get(target_stage, label)
                out.append(
                    {
                        "target_stage": target_stage,
                        "workflow_role": workflow_role,
                        "label": f"{base_label}（{vlabel}）",
                        "action": "sign_and_advance_workflow",
                        "requires_report_signature": True,
                        "is_resign": False,
                        "export_variant": v,
                    }
                )
                break
    else:
        for target_stage, workflow_role, label in MANUAL_ADVANCE_STEPS:
            if target_stage not in REPORT_SIGN_ADVANCE_TARGETS:
                continue
            tidx = workflow_stage_index(target_stage)
            if cur_idx >= tidx:
                continue
            if not user_may_manual_advance_to_stage(
                user, project, required_workflow_role=workflow_role
            ):
                continue
            base_label = REPORT_SIGN_ADVANCE_LABELS.get(target_stage, label)
            out.append(
                {
                    "target_stage": target_stage,
                    "workflow_role": workflow_role,
                    "label": base_label,
                    "action": "sign_and_advance_workflow",
                    "requires_report_signature": True,
                    "is_resign": False,
                    "export_variant": "",
                }
            )
            break

    out.extend(
        resign_signature_actions_for_item(
            user,
            project,
            effective_stage=effective_stage,
            signed_slots=signed_slots or set(),
            variant_sign_targets=variants,
        )
    )
    return out


def resign_signature_actions_for_item(
    user: User,
    project: LibraryProject,
    *,
    effective_stage: str,
    signed_slots: set[str] | frozenset[str] | None = None,
    variant_sign_targets: list[dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    """
    当前环节若已完成本岗签字，允许同岗重新叠印覆盖上次签名（不回退/不重复推进流程）。
    多版本时按各版本自身环节分别提供重签按钮。
    """
    signed = set(signed_slots or set())
    stage = (effective_stage or "").strip()
    out: List[Dict[str, Any]] = []
    variants = list(variant_sign_targets or [])
    from apps.core.report_signature_service import variant_report_stage_from_signed_slots

    for target_stage, slot, workflow_role, label in REPORT_SIGN_RESIGN_SPECS:
        if not user_may_manual_advance_to_stage(
            user, project, required_workflow_role=workflow_role
        ):
            continue
        if variants:
            for vr in variants:
                v = str(vr.get("export_variant") or "").strip()
                if not v:
                    continue
                v_signed = set(vr.get("signed_slots") or [])
                v_stage = str(vr.get("stage_code") or "").strip() or variant_report_stage_from_signed_slots(
                    v_signed, case_stage=stage
                )
                if v_stage != target_stage:
                    continue
                if slot not in v_signed:
                    continue
                vlabel = str(vr.get("label") or v).strip() or v
                out.append(
                    {
                        "target_stage": target_stage,
                        "workflow_role": workflow_role,
                        "label": f"{label}（{vlabel}）",
                        "action": "sign_and_advance_workflow",
                        "requires_report_signature": True,
                        "is_resign": True,
                        "export_variant": v,
                    }
                )
        else:
            if stage != target_stage:
                continue
            if slot not in signed:
                continue
            out.append(
                {
                    "target_stage": target_stage,
                    "workflow_role": workflow_role,
                    "label": label,
                    "action": "sign_and_advance_workflow",
                    "requires_report_signature": True,
                    "is_resign": True,
                    "export_variant": "",
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

    if target_stage in _STAGES_REQUIRING_SITE_SOURCE:
        from apps.api.inspection_pdf_service import (
            _resolve_library_task_for_task_no,
            collect_latest_submit_rows_for_site_folder,
            exportable_site_task_keys,
        )
        from apps.core.models import LibraryTask

        site_task = None
        task_no = ""
        # 优先用提交上的 task_no 解析现场任务
        try:
            task_no = str(getattr(submission, "task_no", "") or "").strip()
        except Exception:
            task_no = ""
        if task_no:
            site_task = _resolve_library_task_for_task_no(task_no, project)
            if site_task is not None and site_task.output_target != LibraryTask.OUTPUT_SITE_RECORD:
                site_task = None
        has_source = False
        if site_task is not None:
            rows_ok, _notes, _errs = collect_latest_submit_rows_for_site_folder(
                project, site_task, user
            )
            has_source = bool(rows_ok)
        if not has_source:
            # 回退：项目级导出源集合
            keys = exportable_site_task_keys(user, [project])
            if site_task is not None and (int(project.pk), int(site_task.pk)) in keys:
                has_source = True
        if not has_source:
            return False, "尚无现场记录 PDF 或检测提交数据，不能推进到校核/编制环节"

    get_or_create_workflow_state(case)
    if target_stage == InspectionCaseWorkflowState.STAGE_ISSUED:
        from apps.core.workflow_service import set_issue_date_from_signatory

        set_issue_date_from_signatory(case, user=user)
    else:
        transition_case_stage(case, target_stage, user=user, return_reason="")
    return True, "已推进流程环节"
