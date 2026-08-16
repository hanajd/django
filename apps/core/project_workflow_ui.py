"""项目工作台：检测部流程说明与界面分层（与《检测、报告编写流程表》对齐）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from apps.core.models import (
    InspectionCaseWorkflowState,
    LibraryProject,
    LibraryProjectEquipment,
    LibraryProjectWorkflowMember,
    LibraryTaskAssignment,
)
from apps.core.workflow_service import stage_sequence

# 签发流程六步（与 InspectionCaseWorkflowState 一致）
ISSUANCE_PROGRESS_STAGES: List[Dict[str, str]] = [
    {"code": InspectionCaseWorkflowState.STAGE_SITE_FILL, "short": "现场", "full": "检测员填写现场记录"},
    {"code": InspectionCaseWorkflowState.STAGE_SITE_REVIEW, "short": "校核", "full": "校核员校核现场记录"},
    {"code": InspectionCaseWorkflowState.STAGE_REPORT_DRAFT, "short": "编制", "full": "编制人编制报告"},
    {"code": InspectionCaseWorkflowState.STAGE_REPORT_AUDIT, "short": "审核", "full": "审核人审核报告"},
    {"code": InspectionCaseWorkflowState.STAGE_REPORT_SIGN, "short": "签发", "full": "授权签字人签发"},
    {"code": InspectionCaseWorkflowState.STAGE_ISSUED, "short": "完成", "full": "已签发"},
]

_STAGE_CODE_TO_INDEX: Dict[str, int] = {
    row["code"]: idx for idx, row in enumerate(ISSUANCE_PROGRESS_STAGES)
}
_STAGE_FULL_LABELS: Dict[str, str] = {row["code"]: row["full"] for row in ISSUANCE_PROGRESS_STAGES}


def workflow_stage_index(stage_code: Optional[str]) -> int:
    code = (stage_code or "").strip()
    if not code:
        return -1
    return _STAGE_CODE_TO_INDEX.get(code, -1)


def max_workflow_stage(a: Optional[str], b: Optional[str]) -> str:
    ia, ib = workflow_stage_index(a), workflow_stage_index(b)
    if ia < 0:
        return (b or "").strip()
    if ib < 0:
        return (a or "").strip()
    return (a or "") if ia >= ib else (b or "")


def build_issuance_progress_bar(stage_code: Optional[str]) -> Dict[str, Any]:
    """
    构建签发流程进度条数据（六步固定顺序；stage_code 为空表示未开始）。
    """
    code = (stage_code or "").strip()
    if code and code not in _STAGE_CODE_TO_INDEX:
        valid = set(_STAGE_CODE_TO_INDEX)
        if code not in valid:
            code = InspectionCaseWorkflowState.STAGE_SITE_FILL
    current_idx = _STAGE_CODE_TO_INDEX.get(code, -1)
    total = len(ISSUANCE_PROGRESS_STAGES)
    steps: List[Dict[str, Any]] = []
    for idx, row in enumerate(ISSUANCE_PROGRESS_STAGES):
        if current_idx < 0:
            done = False
            current = False
        elif idx < current_idx:
            done = True
            current = False
        elif idx == current_idx:
            done = code == InspectionCaseWorkflowState.STAGE_ISSUED
            current = True
        else:
            done = False
            current = False
        steps.append(
            {
                "code": row["code"],
                "short_label": row["short"],
                "full_label": row["full"],
                "done": done,
                "current": current,
            }
        )
    if current_idx < 0:
        percent = 0
        stage_label = "未开始"
    elif code == InspectionCaseWorkflowState.STAGE_ISSUED:
        percent = 100
        stage_label = _STAGE_FULL_LABELS[code]
    else:
        percent = max(8, round((current_idx + 0.5) / total * 100))
        stage_label = _STAGE_FULL_LABELS.get(code, code)
    return {
        "stage_code": code or "",
        "stage_label": stage_label,
        "stage_index": current_idx + 1 if current_idx >= 0 else 0,
        "total_steps": total,
        "percent": percent,
        "steps": steps,
    }


def aggregate_issuance_progress_bars(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """多检测项目汇总：取最慢环节作为委托整体进度，并统计已签发数量。"""
    if not bars:
        return build_issuance_progress_bar(None)
    seq = stage_sequence()
    min_idx: Optional[int] = None
    issued = 0
    for bar in bars:
        code = bar.get("stage_code") or ""
        if code == InspectionCaseWorkflowState.STAGE_ISSUED:
            issued += 1
        idx = _STAGE_CODE_TO_INDEX.get(code, -1)
        if idx >= 0:
            min_idx = idx if min_idx is None else min(min_idx, idx)
    if min_idx is None:
        out = build_issuance_progress_bar(None)
    else:
        out = build_issuance_progress_bar(seq[min_idx])
    out["issued_count"] = issued
    out["total_items"] = len(bars)
    return out


# 权限从低到高（与流程表一致）
ROLE_LADDER: List[Dict[str, str]] = [
    {
        "code": LibraryProjectWorkflowMember.ROLE_FIELD_INSPECTOR,
        "label": "检测员",
        "level": "1",
        "summary": "现场检测、原始记录、仪器出入库、影像留存",
    },
    {
        "code": LibraryProjectWorkflowMember.ROLE_SITE_REVIEWER,
        "label": "校核员",
        "level": "2",
        "summary": "校核现场数据与原始记录",
    },
    {
        "code": LibraryProjectWorkflowMember.ROLE_REPORT_AUTHOR,
        "label": "编制人",
        "level": "3",
        "summary": "数据处理、报告编制、打印装订与归档整理",
    },
    {
        "code": LibraryProjectWorkflowMember.ROLE_REPORT_AUDITOR,
        "label": "审核人",
        "level": "4",
        "summary": "一/二审、上报协同",
    },
    {
        "code": LibraryProjectWorkflowMember.ROLE_AUTH_SIGNATORY,
        "label": "授权签字人",
        "level": "5",
        "summary": "三审签发、派单统筹、最终签字盖章",
    },
]

ROLE_CODE_ORDER = [r["code"] for r in ROLE_LADDER]

# 系统内三步（医院→设备→人）
WORKBENCH_PIPELINE: List[Dict[str, Any]] = [
    {
        "key": "commission",
        "step": "①",
        "title": "委托立项",
        "subtitle": "医院 · 项目 · 受检设备",
        "tab": "commission",
        "hint": "左侧进入医院/院区/科室；勾选设备并选择本次检测类型，系统自动挂载对应报告与现场记录任务。",
    },
    {
        "key": "dispatch",
        "step": "②",
        "title": "人员派工",
        "subtitle": "统筹人 · 五级岗位",
        "tab": "dispatch",
        "hint": "指定项目统筹人，按检测员→校核员→编制人→审核人→授权签字人登记参与人；登记后系统自动同步 App 任务。",
    },
    {
        "key": "instruments",
        "step": "③",
        "title": "仪器管理",
        "subtitle": "种类 · 编号 · 出库",
        "tab": "instruments",
        "hint": "按任务模板所需仪器种类分配具体编号并登记出库；与人员派工分开维护。",
    },
    {
        "key": "submissions",
        "step": "④",
        "title": "进度跟踪",
        "subtitle": "现场记录 · 报告 · 环节",
        "tab": "submissions",
        "hint": "查看检测提交与案件当前环节（填写现场记录→校核→编制→审核→签发）。",
    },
]

# 线下全流程摘要（展示用；标注系统是否覆盖）
PROCESS_STEPS_REFERENCE: List[Dict[str, str]] = [
    {"no": "01", "title": "派单与前期沟通", "roles": "授权签字人、检测员", "online": "partial"},
    {"no": "02", "title": "准备与仪器出库", "roles": "检测员", "online": "offline"},
    {"no": "03", "title": "现场检测与影像", "roles": "检测员", "online": "app"},
    {"no": "04", "title": "完成检测与仪器入库", "roles": "检测员", "online": "partial"},
    {"no": "05", "title": "数据处理与报告编写", "roles": "检测员、校核员、编制人", "online": "app"},
    {"no": "06", "title": "报告审核（一至三审）", "roles": "审核人、授权签字人", "online": "app"},
    {"no": "07", "title": "报告打印签字装订", "roles": "编制人、校核员、授权签字人", "online": "offline"},
    {"no": "08", "title": "报告登记上报归档", "roles": "编制人、审核人、授权签字人", "online": "partial"},
]


def normalize_workbench_tab(tab: str) -> str:
    """旧标签 workflow / assign 合并为 dispatch。"""
    t = (tab or "").strip().lower()
    if t in ("workflow", "assign"):
        return "dispatch"
    return t or "overview"


# 全局账号角色 → 流程权限等级（数值越大权限越高，可登记不高于自身等级的流程岗位）
GLOBAL_ROLE_WORKFLOW_RANK: Dict[str, int] = {
    "app_user": 1,
    "field_inspector": 1,
    "site_reviewer": 2,
    "report_author": 3,
    "report_auditor": 4,
    "authorized_signatory": 5,
}

WORKFLOW_ROLE_RANK: Dict[str, int] = {
    LibraryProjectWorkflowMember.ROLE_FIELD_INSPECTOR: 1,
    LibraryProjectWorkflowMember.ROLE_SITE_REVIEWER: 2,
    LibraryProjectWorkflowMember.ROLE_REPORT_AUTHOR: 3,
    LibraryProjectWorkflowMember.ROLE_REPORT_AUDITOR: 4,
    LibraryProjectWorkflowMember.ROLE_AUTH_SIGNATORY: 5,
}


def global_role_code_for_user(user) -> str:
    prof = getattr(user, "profile", None)
    role = getattr(prof, "role", None) if prof else None
    return (getattr(role, "code", None) or "").strip()


def global_workflow_rank(user) -> int:
    return GLOBAL_ROLE_WORKFLOW_RANK.get(global_role_code_for_user(user), 0)


def workflow_role_rank(workflow_role: str) -> int:
    return WORKFLOW_ROLE_RANK.get(workflow_role, 99)


def user_eligible_for_workflow_role(user, workflow_role: str) -> bool:
    """
    高权限账号可登记到不高于自身等级的流程岗位（如授权签字人可兼任检测员）。
    新轨部门员工无全局五岗等级：由主任派工决定，任意流程岗均可挂。
    旧轨五岗账号：仍按原 GLOBAL_ROLE_WORKFLOW_RANK 判断，行为不变。
    """
    code = global_role_code_for_user(user)
    try:
        from apps.core.org_roles import DEPT_STAFF_ROLE_CODES

        if code in DEPT_STAFF_ROLE_CODES:
            return True
    except Exception:
        pass
    rank = global_workflow_rank(user)
    return rank > 0 and rank >= workflow_role_rank(workflow_role)


def count_unique_workflow_member_users(members: List[Any]) -> int:
    """流程岗位登记的去重用户数（同一用户兼任多岗只计一人）。"""
    ids: set[int] = set()
    for m in members or []:
        uid = getattr(m, "user_id", None)
        if uid is None:
            user = getattr(m, "user", None)
            if user is not None:
                uid = getattr(user, "pk", None)
        if uid is not None:
            ids.add(int(uid))
    return len(ids)


def build_workflow_dispatch_role_panels(
    users: List[Any],
    members_by_role: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """各流程岗位：已登记成员 + 可添加候选人（供两步确认添加）。"""
    panels: List[Dict[str, Any]] = []
    for grp in members_by_role:
        code = grp["code"]
        assigned_ids = {int(m.user_id) for m in (grp.get("members") or [])}
        picker_users: List[Dict[str, Any]] = []
        for u in users:
            if u.pk in assigned_ids:
                continue
            if not user_eligible_for_workflow_role(u, code):
                continue
            ur = global_workflow_rank(u)
            rr = workflow_role_rank(code)
            role = getattr(getattr(u, "profile", None), "role", None)
            picker_users.append(
                {
                    "user_id": u.pk,
                    "username": u.username,
                    "full_name": u.get_full_name() or "",
                    "role_label": role.name if role else global_role_code_for_user(u) or "—",
                    "is_cross_role": ur > rr,
                }
            )
        panels.append(
            {
                **grp,
                "picker_users": picker_users,
                "eligible_count": len(picker_users),
                "assigned_count": len(assigned_ids),
            }
        )
    return panels


def build_workflow_role_user_picker(
    users: List[Any],
    members_by_role: List[Dict[str, Any]],
    *,
    selected_role: str,
) -> Dict[str, Any]:
    """人员派工：选定岗位后展示可添加的用户列表（非下拉）。"""
    role_codes = {r["code"] for r in ROLE_LADDER}
    if selected_role not in role_codes:
        selected_role = LibraryProjectWorkflowMember.ROLE_FIELD_INSPECTOR
    assigned_ids: set[int] = set()
    for grp in members_by_role:
        if grp.get("code") == selected_role:
            for m in grp.get("members") or []:
                assigned_ids.add(int(m.user_id))
    eligible_users = [u for u in users if user_eligible_for_workflow_role(u, selected_role)]
    picker_users: List[Dict[str, Any]] = []
    for u in eligible_users:
        rc = global_role_code_for_user(u)
        role = getattr(getattr(u, "profile", None), "role", None)
        ur = global_workflow_rank(u)
        rr = workflow_role_rank(selected_role)
        picker_users.append(
            {
                "user": u,
                "user_id": u.pk,
                "username": u.username,
                "full_name": u.get_full_name() or "",
                "role_label": role.name if role else rc or "—",
                "already_assigned": u.pk in assigned_ids,
                "is_cross_role": ur > rr,
            }
        )
    role_meta = {r["code"]: r for r in ROLE_LADDER}
    sel = role_meta.get(selected_role, ROLE_LADDER[0])
    return {
        "selected_role": selected_role,
        "selected_label": sel["label"],
        "selected_level": sel["level"],
        "users": picker_users,
        "eligible_count": len(eligible_users),
        "assigned_count": len(assigned_ids),
    }


def build_assignment_sync_summary(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """任务同步记录概览（默认折叠明细用）。"""
    user_count = len(records)
    task_total = sum(len(r.get("tasks") or []) for r in records)
    return {
        "user_count": user_count,
        "task_total": task_total,
    }


def group_workflow_members_by_role(members: List[Any]) -> List[Dict[str, Any]]:
    """按岗位阶梯分组流程参与人。"""
    by_role: Dict[str, List[Any]] = {code: [] for code in ROLE_CODE_ORDER}
    for m in members or []:
        code = str(getattr(m, "workflow_role", "") or "")
        if code in by_role:
            by_role[code].append(m)
    out: List[Dict[str, Any]] = []
    meta = {r["code"]: r for r in ROLE_LADDER}
    for code in ROLE_CODE_ORDER:
        out.append(
            {
                "code": code,
                "label": meta[code]["label"],
                "level": meta[code]["level"],
                "summary": meta[code]["summary"],
                "members": by_role.get(code) or [],
            }
        )
    return out


def build_workbench_pipeline_status(
    project: LibraryProject | None,
    *,
    equipment_count: int = 0,
    assignee_count: int = 0,
    workflow_member_count: int = 0,
    submission_count: int = 0,
    instrument_panel: dict | None = None,
) -> List[Dict[str, Any]]:
    """为概览/派工页生成三步完成状态。"""
    rows: List[Dict[str, Any]] = []
    if project is None:
        return [{**p, "done": False, "detail": "请先选择项目"} for p in WORKBENCH_PIPELINE]

    has_equipment = equipment_count > 0
    has_primary = bool(project.primary_responsible_id)
    has_assign = assignee_count > 0
    has_workflow = workflow_member_count > 0

    for p in WORKBENCH_PIPELINE:
        key = p["key"]
        if key == "commission":
            done = has_equipment
            detail = f"已绑定 {equipment_count} 台设备" if done else "尚未绑定受检设备"
        elif key == "dispatch":
            done = has_primary and has_workflow
            parts = []
            if has_primary:
                parts.append("已指定统筹人")
            if has_workflow:
                parts.append(f"{workflow_member_count} 名参与人")
            if has_assign:
                parts.append(f"{assignee_count} 人已同步 App 任务")
            detail = " · ".join(parts) if parts else "待指定统筹人并登记岗位"
        elif key == "instruments":
            from apps.core.instrument_inventory_service import instrument_pipeline_step_status

            done, detail = instrument_pipeline_step_status(instrument_panel)
        else:
            done = submission_count > 0
            detail = f"{submission_count} 条检测提交" if done else "暂无提交，待现场/App 填报"
        rows.append({**p, "done": done, "detail": detail})
    return rows
