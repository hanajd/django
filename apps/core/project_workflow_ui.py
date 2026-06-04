"""项目工作台：检测部流程说明与界面分层（与《检测、报告编写流程表》对齐）。"""
from __future__ import annotations

from typing import Any, Dict, List

from apps.core.models import (
    LibraryProject,
    LibraryProjectEquipment,
    LibraryProjectWorkflowMember,
    LibraryTaskAssignment,
)


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
        "subtitle": "统筹人 · 五级岗位 · App 任务",
        "tab": "dispatch",
        "hint": "指定项目统筹人，按检测员→校核员→编制人→审核人→授权签字人登记参与人，再向检测人员同步 App 任务。",
    },
    {
        "key": "submissions",
        "step": "③",
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
            done = has_primary and has_workflow and has_assign
            parts = []
            if has_primary:
                parts.append("已指定统筹人")
            if has_workflow:
                parts.append(f"{workflow_member_count} 名岗位参与人")
            if has_assign:
                parts.append(f"{assignee_count} 人已同步 App 任务")
            detail = " · ".join(parts) if parts else "待指定统筹人、登记岗位、同步任务"
        else:
            done = submission_count > 0
            detail = f"{submission_count} 条检测提交" if done else "暂无提交，待现场/App 填报"
        rows.append({**p, "done": done, "detail": detail})
    return rows
