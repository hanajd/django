"""委托编号（项目 code）与受检编号（按报告任务）生成规则。"""
from __future__ import annotations

import re
from datetime import datetime

from django.utils import timezone

from apps.core.models import LibraryProject, LibraryTask

# 新格式：YY + 四位流水（如 260001）
PROJECT_CODE_RE = re.compile(r"^\d{6}$")
# 兼容旧格式：YYYYMM + 两位流水
LEGACY_PROJECT_CODE_RE = re.compile(r"^\d{8}$")

_MAX_PROJECT_SERIAL = 9999


def _local_dt(dt=None):
    if dt is None:
        dt = timezone.now()
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return timezone.localtime(dt)


def max_project_serial_for_year_suffix(yy: str) -> int:
    """当年已占用最大流水号（仅统计符合 YY#### 的 code）。"""
    yy = str(yy or "").strip()
    if len(yy) != 2 or not yy.isdigit():
        return 0
    max_seq = 0
    for code in LibraryProject.objects.filter(code__startswith=yy).values_list("code", flat=True):
        s = str(code or "").strip()
        if PROJECT_CODE_RE.fullmatch(s) and s[:2] == yy:
            max_seq = max(max_seq, int(s[2:]))
    return max_seq


def generate_library_project_code(*, now=None) -> str:
    """
    委托编号：年份后两位 + 四位流水（0001–9999），按自然年递增。
    例：2026 年第 1 个 → ``260001``。
    """
    dt = _local_dt(now)
    yy = dt.strftime("%y")
    next_seq = max_project_serial_for_year_suffix(yy) + 1
    if next_seq > _MAX_PROJECT_SERIAL:
        raise ValueError(f"年度委托编号流水号已用尽（{yy} 已超过 {_MAX_PROJECT_SERIAL}）")
    return f"{yy}{next_seq:04d}"


def allocate_library_project_code(*, now=None, max_attempts: int = 24) -> str:
    """
    分配全局唯一的委托编号（格式 YY####）。
    并发创建时若编号已被占用，自动顺延重试。
    """
    last_tried = ""
    for _ in range(max(1, max_attempts)):
        code = generate_library_project_code(now=now)
        last_tried = code
        if not LibraryProject.objects.filter(code=code).exists():
            return code
    raise ValueError(
        f"委托编号分配失败（最近尝试 {last_tried}），请稍后重试"
    )


def is_standard_commission_code(text: str) -> bool:
    """是否为标准委托编号（两位年 + 四位序号）。"""
    return bool(PROJECT_CODE_RE.fullmatch(str(text or "").strip()))


# 手动录入：标准 6 位、兼容旧 8 位，或 4～12 位纯数字（历史委托号）
_MANUAL_PROJECT_CODE_RE = re.compile(r"^\d{4,12}$")


def normalize_manual_project_code(text: str) -> str:
    """去掉首尾空白；空串表示「交给系统自动编号」。"""
    return str(text or "").strip()


def validate_manual_project_code(
    text: str,
    *,
    exclude_pk: int | None = None,
) -> tuple[str | None, str]:
    """
    校验手动填写的委托编号。

    Returns:
        (normalized_code, error_message)
        - 输入为空：返回 (None, "")，调用方应走自动编号或保留原编号。
        - 合法：返回 (code, "")。
        - 非法：返回 (None, 错误说明)。
    """
    code = normalize_manual_project_code(text)
    if not code:
        return None, ""
    if not _MANUAL_PROJECT_CODE_RE.fullmatch(code):
        return (
            None,
            "委托编号须为 4～12 位数字（推荐格式：两位年份 + 四位流水，如 260001）",
        )
    qs = LibraryProject.objects.filter(code=code)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    if qs.exists():
        return None, f"委托编号「{code}」已被其它项目占用，请换一个编号"
    return code, ""


def suggest_next_project_code(*, now=None) -> str:
    """给出下一个可用的标准委托编号（仅作界面提示，创建时仍以实际分配为准）。"""
    try:
        return generate_library_project_code(now=now)
    except ValueError:
        return ""


def project_public_id(project: LibraryProject | None) -> str:
    """对外展示的委托编号（projectId / commissionNo）。"""
    if project is None:
        return ""
    code = str(getattr(project, "code", "") or "").strip()
    if PROJECT_CODE_RE.fullmatch(code) or LEGACY_PROJECT_CODE_RE.fullmatch(code):
        return code
    dt = _local_dt(getattr(project, "created_at", None))
    yy = dt.strftime("%y")
    year_start = dt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    year_end = year_start.replace(year=year_start.year + 1)
    ids = list(
        LibraryProject.objects.filter(created_at__gte=year_start, created_at__lt=year_end)
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    )
    try:
        idx = ids.index(project.id) + 1
    except ValueError:
        idx = 1
    idx = min(idx, _MAX_PROJECT_SERIAL)
    return f"{yy}{idx:04d}"


def report_tasks_for_project(project) -> list[LibraryTask]:
    if project is None:
        return []
    return list(
        project.library_tasks.filter(output_target=LibraryTask.OUTPUT_REPORT).order_by("code", "id")
    )


def parent_report_task_for_site_task(site_task: LibraryTask | None, project) -> LibraryTask | None:
    """现场记录任务所属报告（report_source_tasks 反向查询，取首个）。"""
    if site_task is None or project is None:
        return None
    if getattr(site_task, "output_target", None) != LibraryTask.OUTPUT_SITE_RECORD:
        return None
    return (
        project.library_tasks.filter(
            output_target=LibraryTask.OUTPUT_REPORT,
            report_source_tasks=site_task,
        )
        .order_by("code", "id")
        .first()
    )


def project_tasks_ordered(project) -> list[LibraryTask]:
    """项目内任务列表顺序（与 API ``taskNo`` 序号一致）。"""
    if project is None:
        return []
    return list(project.library_tasks.order_by("code", "id"))


def project_task_no_for_library_task(
    library_task: LibraryTask | None,
    project,
    *,
    ordered_tasks: list[LibraryTask] | None = None,
) -> str:
    """库任务在项目内的 API ``taskNo``（01、02…，与 ``library_tasks`` 排序一致）。"""
    if library_task is None or project is None:
        return ""
    tasks = ordered_tasks if ordered_tasks is not None else project_tasks_ordered(project)
    for idx, row in enumerate(tasks, start=1):
        if row.id == library_task.id:
            return f"{idx:02d}"
    return ""


def build_project_task_api_fields(
    project,
    library_task: LibraryTask,
    *,
    task_no: str,
    ordered_tasks: list[LibraryTask] | None = None,
) -> dict:
    """
    任务列表/详情 API 附加字段：区分路由用 ``taskNo`` 与展示用 ``inspectedNo``。

    - ``taskNo``：项目内每个库任务唯一（含报告 + 各现场记录），用于 URL/提交。
    - ``inspectedNo``：按报告任务编号；同报告下现场记录与报告相同。
    - ``reportTaskNo``：所属报告任务的 ``taskNo``（报告任务自身等于 ``taskNo``）。
    """
    tasks = ordered_tasks if ordered_tasks is not None else project_tasks_ordered(project)
    inspected_no = inspected_serial_for_library_task(library_task, project) or ""

    if library_task.output_target == LibraryTask.OUTPUT_REPORT:
        report_task = library_task
        report_task_no = task_no
    else:
        report_task = parent_report_task_for_site_task(library_task, project)
        if report_task is None and len(report_tasks_for_project(project)) == 1:
            report_task = report_tasks_for_project(project)[0]
        report_task_no = project_task_no_for_library_task(report_task, project, ordered_tasks=tasks) if report_task else ""

    id_to_no = {t.id: project_task_no_for_library_task(t, project, ordered_tasks=tasks) for t in tasks}
    related_site_nos: list[str] = []
    if library_task.output_target == LibraryTask.OUTPUT_REPORT:
        for st in library_task.report_source_tasks.filter(
            output_target=LibraryTask.OUTPUT_SITE_RECORD
        ).order_by("code", "id"):
            no = id_to_no.get(st.id)
            if no:
                related_site_nos.append(no)

    return {
        "inspectedNo": inspected_no,
        "reportTaskId": report_task.id if report_task else None,
        "reportTaskNo": report_task_no,
        "relatedSiteRecordTaskNos": related_site_nos,
    }


def inspected_serial_for_library_task(library_task: LibraryTask | None, project) -> str | None:
    """
    受检编号：按项目下**报告任务**顺序 01、02、…；
    同一报告下的现场记录任务与对应报告同号。
    """
    if library_task is None or project is None:
        return None
    reports = report_tasks_for_project(project)
    if library_task.output_target == LibraryTask.OUTPUT_REPORT:
        try:
            idx = [t.id for t in reports].index(library_task.id)
        except ValueError:
            return None
        return f"{idx + 1:02d}"
    if library_task.output_target == LibraryTask.OUTPUT_SITE_RECORD:
        parent = parent_report_task_for_site_task(library_task, project)
        if parent is not None:
            return inspected_serial_for_library_task(parent, project)
        if len(reports) == 1:
            return "01"
    return None
