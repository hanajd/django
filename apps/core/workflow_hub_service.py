"""报告流程枢纽：按医院/时间 → 委托 → 设备 三级浏览。"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from django.contrib.auth.models import User
from django.db.models import Prefetch
from django.urls import reverse
from django.utils import timezone

from apps.core.commission_management_service import (
    _bulk_project_item_progress,
    commission_projects_queryset,
)
from apps.core.library_access import (
    library_file_access_allowed,
    library_user_may_export_report_library_pdfs,
    role_has,
)
from apps.core.models import (
    InspectionCaseWorkflowState,
    LibraryFile,
    LibraryFileProject,
    LibraryProject,
    LibraryTask,
)
from apps.core.project_equipment_service import task_no_display_index


VIEW_HOSPITAL = "hospital"
VIEW_TIME = "time"
VIEW_MODES = (VIEW_HOSPITAL, VIEW_TIME)


def workflow_hub_nav_allowed(user: User) -> bool:
    """检测报告链路（原始记录/报告生成/审核签发/下载）入口；评价部不开放。"""
    if not getattr(user, "is_authenticated", False):
        return False
    if not role_has(user, "perm_file_library"):
        return False
    try:
        from apps.core.org_roles import user_is_evaluation_org

        if user_is_evaluation_org(user):
            return False
    except Exception:
        pass
    return True


def build_hub_query(
    *,
    project_id: int = 0,
    task_no: str = "",
    tab: str = "",
    q: str = "",
    view: str = "",
) -> str:
    parts: list[tuple[str, str]] = []
    if view and view in VIEW_MODES:
        parts.append(("view", view))
    if project_id:
        parts.append(("project_id", str(project_id)))
    if task_no:
        parts.append(("task_no", task_no))
    if tab:
        parts.append(("tab", tab))
    if q:
        parts.append(("q", q))
    return ("?" + urlencode(parts)) if parts else ""


def resolve_notice_hub_link(notice: dict) -> tuple[str, str]:
    project_id = int(notice.get("project_id") or 0)
    task_no = str(notice.get("task_no") or "").strip()
    act = notice.get("advance_action") if isinstance(notice.get("advance_action"), dict) else {}
    target = str(act.get("target_stage") or notice.get("target_stage") or "").strip()
    stage_code = str(notice.get("stage_code") or "").strip()

    if not target and stage_code:
        if stage_code == InspectionCaseWorkflowState.STAGE_REPORT_DRAFT:
            target = InspectionCaseWorkflowState.STAGE_REPORT_AUDIT
        elif stage_code == InspectionCaseWorkflowState.STAGE_REPORT_AUDIT:
            target = InspectionCaseWorkflowState.STAGE_REPORT_SIGN
        elif stage_code == InspectionCaseWorkflowState.STAGE_REPORT_SIGN:
            target = InspectionCaseWorkflowState.STAGE_ISSUED
        elif stage_code in (
            InspectionCaseWorkflowState.STAGE_SITE_FILL,
            InspectionCaseWorkflowState.STAGE_SITE_REVIEW,
        ):
            target = InspectionCaseWorkflowState.STAGE_SITE_REVIEW
        elif stage_code == InspectionCaseWorkflowState.STAGE_ISSUED:
            return (
                reverse("workflow_hub_report_download")
                + build_hub_query(project_id=project_id, task_no=task_no, view=VIEW_HOSPITAL),
                "去报告下载",
            )

    if target == InspectionCaseWorkflowState.STAGE_REPORT_AUDIT:
        name, label = "workflow_hub_report_generate", "去报告编制"
    elif target in (
        InspectionCaseWorkflowState.STAGE_REPORT_SIGN,
        InspectionCaseWorkflowState.STAGE_ISSUED,
    ):
        name, label = "workflow_hub_review_sign", "去审核签发"
    elif target in (
        InspectionCaseWorkflowState.STAGE_SITE_REVIEW,
        InspectionCaseWorkflowState.STAGE_REPORT_DRAFT,
    ):
        name, label = "workflow_hub_site_records", "去原始记录"
    else:
        name, label = "workflow_hub_review_sign", "去处理"

    kwargs: dict[str, Any] = {"project_id": project_id, "task_no": task_no, "view": VIEW_HOSPITAL}
    if name == "workflow_hub_report_generate":
        kwargs["tab"] = "generate"
    elif name == "workflow_hub_review_sign" and target == InspectionCaseWorkflowState.STAGE_ISSUED:
        kwargs["tab"] = "sign"
    elif name == "workflow_hub_review_sign" and target == InspectionCaseWorkflowState.STAGE_REPORT_SIGN:
        kwargs["tab"] = "audit"
    return reverse(name) + build_hub_query(**kwargs), label


def enrich_commission_notices_with_hub_links(notices: list[dict]) -> list[dict]:
    for n in notices:
        if not isinstance(n, dict):
            continue
        url, label = resolve_notice_hub_link(n)
        n["hub_url"] = url
        n["hub_label"] = label
    return notices


def _norm_view(view: str) -> str:
    v = (view or "").strip().lower()
    return v if v in VIEW_MODES else VIEW_HOSPITAL


def _search_hit(needle: str, *parts: str) -> bool:
    if not needle:
        return True
    blob = " ".join(p for p in parts if p).lower()
    return needle in blob


def _accessible_projects(user: User, *, limit: int = 120) -> list[LibraryProject]:
    return list(
        commission_projects_queryset(user)
        .select_related("commission_org", "commission_org__parent", "commission_org__parent__parent")
        .order_by("-updated_at", "-id")[:limit]
    )


# 医院信息管理中已停用/删除的机构：枢纽单独归入此类，不与在册医院混列
HOSPITAL_KEY_GHOST = "ghost_hospital"
HOSPITAL_TITLE_GHOST = "停用单位"
HOSPITAL_KEY_NONE = "no_hospital"
HOSPITAL_TITLE_NONE = "未关联医院"


def _hospital_meta(project: LibraryProject) -> tuple[str, str, str]:
    """
    返回 (分组 key, 分组标题, 原单位名提示)。

    在册医院以医院信息管理为准（is_active 祖先链全有效）；
    任一点已停用则归入「停用单位」分类，原单位名仅作委托行提示。
    """
    org = getattr(project, "commission_org", None)
    if org is None:
        return HOSPITAL_KEY_NONE, HOSPITAL_TITLE_NONE, ""
    try:
        chain = org.ancestors_chain()
        root = chain[0] if chain else org
    except Exception:
        root = org
        chain = [org]
    legacy = (getattr(root, "name", None) or "").strip() or "未命名单位"
    if any(not bool(getattr(n, "is_active", True)) for n in chain):
        return HOSPITAL_KEY_GHOST, HOSPITAL_TITLE_GHOST, legacy
    return f"h{root.pk}", legacy, ""


def _time_bucket(dt: datetime | None) -> tuple[str, str, int]:
    """
    按自然月分组（常见文档/业务列表习惯）：如 2026年7月。
    rank 用 YYYYMM 倒序数值的取负，便于 sorted 时新月在前。
    """
    if dt is None:
        return "unknown", "时间未知", 10**9
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    local = timezone.localtime(dt)
    key = f"{local.year:04d}-{local.month:02d}"
    label = f"{local.year}年{local.month}月"
    # 越小越靠前：用 -YYYYMM
    rank = -(local.year * 100 + local.month)
    return key, label, rank


def _case_project_id_map(case_ids: list[int]) -> dict[int, int]:
    """InspectionCase.id → library_project_id。"""
    if not case_ids:
        return {}
    from apps.core.models import InspectionCase

    out: dict[int, int] = {}
    for cid, pid in InspectionCase.objects.filter(pk__in=case_ids).values_list(
        "id", "library_project_id"
    ):
        if cid and pid:
            out[int(cid)] = int(pid)
    return out


def _hub_file_belongs_to_project(
    lf: LibraryFile,
    project_id: int,
    *,
    case_project_ids: dict[int, int] | None = None,
) -> bool:
    """
    枢纽文件是否属于该委托（对齐委托内容）。

    已挂 inspection_case 时，以案件所属委托为准，避免仅靠 M2M 把其它委托 PDF 挂进来。
    未挂案件时：须绑定本委托下的现场/报告任务，或仅 M2M 到本委托。
    """
    pid = int(project_id or 0)
    if not pid or lf is None:
        return False
    try:
        if not any(int(p.pk) == pid for p in lf.projects.all()):
            return False
    except Exception:
        return False

    link_entity = str(getattr(lf, "link_entity", "") or "")
    if link_entity == LibraryFile.LINK_ENTITY_INSPECTION_CASE and lf.link_object_id:
        try:
            case_id = int(lf.link_object_id)
        except (TypeError, ValueError):
            return False
        case_pid = None
        if case_project_ids is not None:
            case_pid = case_project_ids.get(case_id)
        else:
            from apps.core.models import InspectionCase

            case_pid = (
                InspectionCase.objects.filter(pk=case_id)
                .values_list("library_project_id", flat=True)
                .first()
            )
            case_pid = int(case_pid) if case_pid else None
        return bool(case_pid and int(case_pid) == pid)

    # 无案件：已通过 M2M 挂到本委托，予以保留
    return True


def _file_rows(
    user: User,
    *,
    category: str,
    project_ids: list[int],
    limit: int = 200,
) -> list[dict[str, Any]]:
    if not project_ids:
        return []
    pid_set = {int(x) for x in project_ids}
    qs = (
        LibraryFile.objects.filter(
            category=category,
            deleted_at__isnull=True,
            projects__pk__in=project_ids,
        )
        .prefetch_related(
            Prefetch(
                "project_links",
                queryset=LibraryFileProject.objects.select_related("project"),
            ),
            "projects",
            "library_tasks",
        )
        .distinct()
        .order_by("-created_at")[:limit]
    )
    file_list = list(qs)
    case_ids: list[int] = []
    for lf in file_list:
        if (
            getattr(lf, "link_entity", None) == LibraryFile.LINK_ENTITY_INSPECTION_CASE
            and lf.link_object_id
        ):
            try:
                case_ids.append(int(lf.link_object_id))
            except (TypeError, ValueError):
                pass
    case_project_ids = _case_project_id_map(case_ids)

    rows: list[dict[str, Any]] = []
    for lf in file_list:
        if not library_file_access_allowed(user, lf):
            continue
        projects = list(lf.projects.all()[:5])
        proj = None
        for p in projects:
            if p.pk in pid_set:
                proj = p
                break
        if proj is None and projects:
            proj = projects[0]
        if proj is None:
            continue
        # 现场记录 / 报告：均按案件所属委托过滤，与委托内容对齐
        if category in (
            LibraryFile.CATEGORY_SITE_RECORD,
            LibraryFile.CATEGORY_REPORT,
        ) and not _hub_file_belongs_to_project(
            lf, proj.pk, case_project_ids=case_project_ids
        ):
            continue
        tab = "site_record" if category == LibraryFile.CATEGORY_SITE_RECORD else "report"
        template_name = ""
        if category == LibraryFile.CATEGORY_SITE_RECORD:
            display_name, template_name = _site_record_hub_display_labels(lf, proj)
        else:
            display_name = _friendly_file_display_name(lf.original_name or "", lf)
        row: dict[str, Any] = {
            "kind": "file",
            "file": lf,
            "display_name": display_name,
            "template_name": template_name,
            "project_id": proj.pk if proj else 0,
            "project_code": getattr(proj, "code", "") if proj else "",
            "project_name": getattr(proj, "name", "") if proj else "",
            "created_at": lf.created_at,
            "equipment_key": "",
            "equipment_title": "其他",
            "preview_url": reverse("file_preview", kwargs={"pk": lf.pk}),
            "download_url": reverse("file_library_download", kwargs={"pk": lf.pk}),
            "library_url": reverse("file_library")
            + "?"
            + urlencode(
                {"tab": tab, **({"project": str(proj.pk)} if proj else {})}
            ),
        }
        if category == LibraryFile.CATEGORY_SITE_RECORD:
            row["photos_url"] = reverse(
                "site_record_retained_photos", kwargs={"pk": lf.pk}
            )
        rows.append(row)
    return rows


def _match_equipment_for_file(
    name: str,
    equipment_titles: list[str],
    *,
    alt_names: list[str] | None = None,
) -> tuple[str, str]:
    """
    用设备标题匹配文件名。
    - 优先更长的设备名首段（避免 CT 误吃「口腔CBCT」、DR 误吃「乳腺DR」）
    - 文件名含「验收检测/状态检测」时优先同类型标题
    - alt_names：规范展示名、模板名等补充匹配源（库内 original_name 常为模板文件名，不含设备类型）
    """
    blobs: list[str] = []
    for raw in (name, *(alt_names or ())):
        s = (raw or "").strip().lower()
        if s and s not in blobs:
            blobs.append(s)
    if not blobs:
        return "其他", "其他"
    joined = " ".join(blobs)

    scored: list[tuple[int, str]] = []
    for title in equipment_titles:
        t = (title or "").strip()
        if not t or t == "—":
            continue
        parts = [p.strip() for p in t.split("·") if p.strip()]
        head = parts[0] if parts else t
        insp = parts[-1] if parts and parts[-1] in {"验收检测", "状态检测"} else ""
        head_l = head.lower()
        title_l = t.lower()
        hit = False
        for blob in blobs:
            if (head_l and head_l in blob) or title_l in blob:
                hit = True
                break
        if not hit:
            continue
        score = len(head) * 10
        if insp and insp in joined:
            score += 20
        elif insp:
            other = "状态检测" if insp == "验收检测" else "验收检测"
            if other in joined:
                score -= 15
        scored.append((score, t))
    if not scored:
        return "其他", "其他"
    scored.sort(key=lambda x: (-x[0], -len(x[1]), x[1]))
    best = scored[0][1]
    return best, best


def _equipment_title_from_linked_site_task(
    lf: LibraryFile, task_id_to_title: dict[int, str]
) -> str:
    """现场 PDF 已绑定 library_task 时，直接取进度里的设备标题（最稳）。"""
    if not task_id_to_title:
        return ""
    try:
        for task in lf.library_tasks.all():
            tid = int(getattr(task, "pk", 0) or 0)
            title = (task_id_to_title.get(tid) or "").strip()
            if title and title != "—":
                return title
    except Exception:
        return ""
    return ""


def _attach_site_record_files_to_progress(
    prog: list[dict[str, Any]],
    files: list[dict[str, Any]],
) -> set[int]:
    """
    把现场记录 PDF 与进度叶子互相关联：
    - 进度行带上预览/下载/照片 URL
    - 文件行带上 progress_bar / stage_label，便于原始记录页直接查看

    返回已匹配到进度的文件 pk（调用方可决定是否去重文件叶子）。
    """
    if not prog or not files:
        return set()

    from apps.core.models import LibraryTask

    progress_rows = [
        r
        for r in prog
        if not r.get("kind") or r.get("kind") == "progress"
    ]
    if not progress_rows:
        return set()

    task_cache: dict[int, LibraryTask | None] = {}

    def _task(tid: int) -> LibraryTask | None:
        if tid <= 0:
            return None
        if tid not in task_cache:
            task_cache[tid] = LibraryTask.objects.filter(pk=tid).first()
        return task_cache[tid]

    def _codes_related(a: str, b: str) -> bool:
        x = (a or "").strip().lower()
        y = (b or "").strip().lower()
        if not x or not y:
            return False
        if x == y:
            return True
        short, long = (x, y) if len(x) <= len(y) else (y, x)
        if long.startswith(short) and (
            len(long) == len(short) or long[len(short)] in "-_."
        ):
            return True

        def _stem(code: str) -> str:
            parts = code.split("-")
            if len(parts) >= 2 and parts[-1].isdigit() and len(parts[-1]) <= 2:
                return "-".join(parts[:-1])
            return code

        return _stem(x) == _stem(y) and len(_stem(x)) >= 8

    def _same_equipment(fr: dict[str, Any], row: dict[str, Any]) -> bool:
        a = (fr.get("equipment_title") or "").strip()
        b = (row.get("equipment_title") or "").strip()
        if not a or not b or a in ("其他", "—") or b in ("其他", "—"):
            return False
        return a == b

    def _score_file(fr: dict[str, Any], row: dict[str, Any], site_task: LibraryTask | None) -> int:
        lf = fr.get("file")
        if lf is None:
            return -1
        score = 0
        blob = f"{fr.get('display_name') or ''} {lf.original_name or ''}".lower()
        eq = (row.get("equipment_title") or "").strip()
        parts = [p.strip() for p in eq.split("·") if p.strip()]
        head = parts[0] if parts else ""
        insp = parts[-1] if parts and parts[-1] in {"验收检测", "状态检测"} else ""
        if head and head.lower() in blob:
            score += 40
            for noise in ("乳腺", "动态", "口腔", "胃肠"):
                if noise in blob and noise not in eq:
                    score -= 30
        if insp and insp in blob:
            score += 25
        elif insp:
            other = "状态检测" if insp == "验收检测" else "验收检测"
            if other in blob:
                score -= 15
        tn = str(row.get("task_no") or "").strip()
        if tn and (tn in blob or tn.replace("/", "_") in blob):
            score += 60
        if site_task is not None:
            scode = (site_task.code or "").strip()
            sname = (site_task.name or "").strip()
            try:
                for task in lf.library_tasks.all():
                    if int(task.pk) == int(site_task.pk):
                        score += 100
                    elif _same_equipment(fr, row) and _codes_related(task.code or "", scode):
                        # 仅同设备下允许「验收/状态」系列任务共享，避免 jxfs 同源码串到 C 形臂/DSA
                        score += 80
                    elif _same_equipment(fr, row) and sname and (task.name or "") == sname:
                        score += 70
            except Exception:
                pass
        return score

    pairs: list[tuple[int, int, int, dict[str, Any], dict[str, Any]]] = []
    for idx, row in enumerate(progress_rows):
        site_tid = 0
        for key in ("site_library_task_id", "library_task_id"):
            site_tid = int(row.get(key) or 0) or 0
            if site_tid:
                break
        site_task = _task(site_tid)
        for fr in files:
            if not fr.get("file"):
                continue
            sc = _score_file(fr, row, site_task)
            # 精确任务命中可跨设备标题；否则必须同设备且分数足够
            exact_task = sc >= 100
            if not exact_task and not _same_equipment(fr, row):
                continue
            if sc < 50:
                continue
            ts = fr.get("created_at").timestamp() if fr.get("created_at") else 0.0
            pairs.append((sc, int(ts), idx, row, fr))

    pairs.sort(key=lambda x: (-x[0], -x[1], x[2]))
    attached: set[int] = set()
    used_rows: set[int] = set()

    def _bind(row: dict[str, Any], matched: dict[str, Any], *, primary: bool) -> None:
        lf = matched["file"]
        pk = int(lf.pk)
        if primary:
            attached.add(pk)
        row["preview_url"] = matched.get("preview_url") or reverse(
            "file_preview", kwargs={"pk": pk}
        )
        row["download_url"] = matched.get("download_url") or reverse(
            "file_library_download", kwargs={"pk": pk}
        )
        row["photos_url"] = matched.get("photos_url") or reverse(
            "site_record_retained_photos", kwargs={"pk": pk}
        )
        row["site_record_file"] = lf
        row["site_record_name"] = matched.get("display_name") or lf.original_name
        row["has_site_file"] = True
        if primary:
            matched["progress_bar"] = row.get("progress_bar")
            matched["stage_code"] = row.get("stage_code") or ""
            matched["stage_label"] = row.get("stage_label") or ""
            matched["task_no"] = row.get("task_no") or matched.get("task_no") or ""
            matched["linked_progress"] = True
            eq_title = (row.get("equipment_title") or "").strip()
            if eq_title and eq_title != "—" and (matched.get("equipment_title") or "其他") in (
                "",
                "其他",
            ):
                matched["equipment_title"] = eq_title
                matched["equipment_key"] = eq_title
        else:
            # 次级关联：可预览；若本进度更靠后，则文件行展示更靠后的流程状态
            from apps.core.project_workflow_ui import workflow_stage_index

            if workflow_stage_index(row.get("stage_code")) > workflow_stage_index(
                matched.get("stage_code")
            ):
                matched["progress_bar"] = row.get("progress_bar")
                matched["stage_code"] = row.get("stage_code") or ""
                matched["stage_label"] = row.get("stage_label") or ""
                matched["task_no"] = row.get("task_no") or matched.get("task_no") or ""
                matched["linked_progress"] = True

    for sc, _ts, idx, row, matched in pairs:
        if idx in used_rows:
            continue
        pk = int(matched["file"].pk)
        if pk in attached:
            continue
        used_rows.add(idx)
        _bind(row, matched, primary=True)

    # 第二轮：仍无文件的进度，仅与同设备标题下已占用的 PDF 共享（验收/状态同源）
    for idx, row in enumerate(progress_rows):
        if idx in used_rows or row.get("has_site_file"):
            continue
        site_tid = 0
        for key in ("site_library_task_id", "library_task_id"):
            site_tid = int(row.get(key) or 0) or 0
            if site_tid:
                break
        site_task = _task(site_tid)
        best: tuple[int, dict[str, Any]] | None = None
        for fr in files:
            if not fr.get("file"):
                continue
            if not _same_equipment(fr, row):
                continue
            sc = _score_file(fr, row, site_task)
            if sc < 80:
                continue
            if best is None or sc > best[0]:
                best = (sc, fr)
        if best is not None:
            used_rows.add(idx)
            _bind(row, best[1], primary=False)

    return attached


_BACKEND_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,63}$")
_PAREN_CODE_RE = re.compile(r"[（(]\s*([A-Za-z][A-Za-z0-9_]{1,63})\s*[）)]$")


def _looks_like_backend_code(text: str) -> bool:
    """ct_qc / radiationQc 一类后台标识，不宜作为人员主标题。"""
    s = (text or "").strip()
    if not s or " " in s or "·" in s:
        return False
    # 纯 ASCII 标识，或去扩展名后仍是标识
    stem = s.rsplit(".", 1)[0] if "." in s else s
    stem = stem.replace(" ", "").replace("-", "_")
    return bool(_BACKEND_CODE_RE.fullmatch(stem))


def _strip_trailing_backend_code_paren(text: str) -> str:
    """去掉「名称（ct_qc）」尾部后台编码括号。"""
    s = (text or "").strip()
    m = _PAREN_CODE_RE.search(s)
    if not m:
        return s
    head = s[: m.start()].strip()
    return head or s


def _pick_human_label_part(raw: str) -> str:
    """从「code · 名称」或「task_no · code · 名称」中取人员可读段。"""
    s = _strip_trailing_backend_code_paren((raw or "").strip())
    if not s or s == "—":
        return ""
    if " · " in s:
        parts = [p.strip() for p in s.split(" · ") if p.strip()]
        # 从右往左找第一段不像后台 code 的
        for part in reversed(parts):
            cleaned = _strip_trailing_backend_code_paren(part)
            if cleaned and not _looks_like_backend_code(cleaned):
                return cleaned
        if parts:
            return parts[-1]
        return s
    if _looks_like_backend_code(s):
        return ""
    return s


def _friendly_task_display_title(row: dict[str, Any], disp: dict[str, Any] | None = None) -> str:
    """进度叶子主标题：优先现场记录/PDF 任务名，去掉 task_no / ct_qc 等模板编码。"""
    disp = disp or {}
    for key in ("site_task_name", "detection_label", "report_task_label"):
        picked = _pick_human_label_part(str(disp.get(key) or row.get(key) or ""))
        if picked:
            return picked
    picked = _pick_human_label_part(str(row.get("task_label") or ""))
    if picked:
        return picked
    return str(row.get("task_no") or "任务").strip() or "任务"


def _pdf_filename_label(original_name: str) -> str:
    """
    枢纽展示用 PDF 文件名：仅去掉 UUID 前缀，保留真实 original_name
    （与「原始记录」里看到的导出 PDF 名一致，不做下划线/代号美化）。
    """
    s = (original_name or "").strip()
    if not s:
        return "未命名文件"
    s = re.sub(r"^[0-9a-f]{32}_?", "", s, flags=re.I)
    s = re.sub(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_?",
        "",
        s,
        flags=re.I,
    )
    s = s.strip()
    return s or (original_name or "").strip() or "未命名文件"


def _is_abstract_pdf_label(name: str) -> bool:
    """代号 / 纯序号类文件名（如 ct_qc.pdf、01-报告.pdf），不宜作为主标题。"""
    stem = (name or "").strip()
    if not stem:
        return True
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    stem = stem.strip()
    low = stem.lower()
    for suf in ("-报告", "_报告", "-现场记录", "_现场记录", "-report", "_report"):
        if low.endswith(suf):
            stem = stem[: -len(suf)].strip("_- ")
            low = stem.lower()
            break
    if not stem:
        return True
    if re.fullmatch(r"\d{1,4}", stem):
        return True
    if re.fullmatch(r"ASG-\d+", stem, flags=re.I):
        return True
    return _looks_like_backend_code(stem)


def _site_record_template_display_name(lf) -> str:
    """现场记录关联的任务模板名称（副标题用）。"""
    try:
        from apps.core.models import LibraryTask

        for task in lf.library_tasks.all():
            if str(getattr(task, "output_target", "") or "") != LibraryTask.OUTPUT_SITE_RECORD:
                continue
            name = (getattr(task, "name", None) or "").strip()
            if name:
                return name
            code = (getattr(task, "code", None) or "").strip()
            if code:
                return code
    except Exception:
        pass
    return ""


def _site_record_hub_display_labels(lf, project) -> tuple[str, str]:
    """
    原始记录枢纽展示：(规范名, 模板名)。
    优先按当前元数据计算规范名；算不出时回退文件 original_name。
    """
    from apps.api.inspection_pdf_service import _resolve_library_task_for_task_no, _task_no_strings_for_library_task
    from apps.api.inspection_report_make import build_standardized_site_record_pdf_original_name
    from apps.core.models import LibraryTask

    template_name = _site_record_template_display_name(lf)
    stored = _pdf_filename_label(lf.original_name or "")
    if project is None:
        return stored, template_name

    site_task = None
    try:
        for task in lf.library_tasks.all():
            if str(getattr(task, "output_target", "") or "") == LibraryTask.OUTPUT_SITE_RECORD:
                site_task = task
                break
    except Exception:
        site_task = None

    task_no = ""
    if site_task is not None:
        try:
            nos = _task_no_strings_for_library_task(project, site_task)
            task_no = nos[0] if nos else ""
        except Exception:
            task_no = ""
    if not site_task and task_no:
        try:
            site_task = _resolve_library_task_for_task_no(task_no, project)
        except Exception:
            site_task = None

    try:
        std = build_standardized_site_record_pdf_original_name(
            project, site_task, task_no, case=None, source_payload=None
        )
        primary = _pdf_filename_label(std)
    except Exception:
        primary = stored

    # 若库内文件已是规范名，以库内为准（与下载名一致）
    if stored and "原始记录" in stored and not _is_abstract_pdf_label(stored):
        # 模板名长串也常含「原始记录」；若 stored 以委托编号开头则更像规范名
        code = str(getattr(project, "code", "") or "").strip()
        if code and stored.startswith(code):
            primary = stored
        elif primary and primary != stored and code and primary.startswith(code):
            pass  # keep computed primary for old template-named files
        else:
            # 无委托编号可判时：优先规范计算结果
            pass

    if not template_name and site_task is not None:
        template_name = (getattr(site_task, "name", None) or "").strip() or (
            getattr(site_task, "code", None) or ""
        ).strip()

    return primary or stored, template_name


def _friendly_file_display_name(original_name: str, lf=None) -> str:
    """与原始记录枢纽一致：直接展示 PDF 文件名。"""
    label = _pdf_filename_label(original_name or "")
    if not _is_abstract_pdf_label(label):
        return label
    if lf is not None:
        try:
            for task in lf.library_tasks.all()[:5]:
                name = (getattr(task, "name", None) or "").strip()
                if name and not _looks_like_backend_code(name):
                    return _pdf_filename_label(
                        name if name.lower().endswith(".pdf") else f"{name}.pdf"
                    )
        except Exception:
            pass
    return label


def _exportable_site_task_keys(
    user: User,
    projects: list[LibraryProject],
) -> set[tuple[int, int]]:
    """见 ``apps.api.inspection_pdf_service.exportable_site_task_keys``。"""
    from apps.api.inspection_pdf_service import exportable_site_task_keys

    return exportable_site_task_keys(user, projects)


def _annotate_report_generate_export_availability(
    progress_map: dict[int, list[dict[str, Any]]],
    exportable: set[tuple[int, int]],
) -> None:
    """无本任务现场数据则不可导出；有数据才显示导出按钮。"""
    rp_cache: dict[int, bool] = {}

    def _site_task_has_rp_chapter(tid: int) -> bool:
        if tid <= 0:
            return False
        if tid in rp_cache:
            return rp_cache[tid]
        hit = False
        try:
            from apps.core import pipeline_service
            from apps.core.library_task_template_binding_service import get_task_template_pair
            import json
            from pathlib import Path

            task = LibraryTask.objects.filter(pk=tid).first()
            if task is not None:
                _pdf, js = get_task_template_pair(task)
                if js is not None and (js.relative_path or ""):
                    path = Path(pipeline_service.library_absolute_path(js.relative_path))
                    if path.is_file():
                        blob = json.loads(path.read_text(encoding="utf-8"))
                        if isinstance(blob, dict):
                            if blob.get("radiationProtectionChapter"):
                                hit = True
                            fs = blob.get("formSchema")
                            if isinstance(fs, dict) and fs.get("radiationProtectionChapter"):
                                hit = True
                            if not hit:
                                fields = (blob.get("pdf") or {}).get("fields") or blob.get("fields") or []
                                if isinstance(fields, list):
                                    for f in fields:
                                        if not isinstance(f, dict):
                                            continue
                                        sec = str(
                                            f.get("templateSectionKey")
                                            or f.get("sectionKey")
                                            or ""
                                        )
                                        if sec == "site_radiation_protection":
                                            hit = True
                                            break
                                        lab = str(f.get("label") or f.get("name") or "")
                                        if "工作场所放射防护" in lab:
                                            hit = True
                                            break
                if not hit:
                    name = f"{getattr(task, 'name', '') or ''}{getattr(task, 'code', '') or ''}"
                    if "防护" in name and "放射" in name:
                        hit = True
        except Exception:
            hit = False
        rp_cache[tid] = hit
        return hit

    for pid, rows in (progress_map or {}).items():
        for row in rows or []:
            if row.get("kind") and row.get("kind") != "progress":
                continue
            sid = int(row.get("site_library_task_id") or row.get("library_task_id") or 0) or 0
            row["can_export_report"] = bool(sid and (int(pid), sid) in exportable)
            row["has_site_export_source"] = row["can_export_report"]
            row["supports_radiation_export_variants"] = bool(
                row["can_export_report"] and _site_task_has_rp_chapter(sid)
            )

def _expected_site_pdf_label(
    project: LibraryProject,
    *,
    task_no: str,
    site_task_id: int,
    disp: dict[str, Any],
) -> str:
    """尚无现场 PDF 时，按导出规则预生成与原始记录一致的文件名。"""
    from apps.api.inspection_pdf_service import _resolve_library_task_for_task_no
    from apps.api.inspection_report_make import build_standardized_site_record_pdf_original_name
    from apps.core.models import LibraryTask

    task = None
    if site_task_id:
        task = LibraryTask.objects.filter(pk=site_task_id).first()
    if task is None and task_no:
        try:
            task = _resolve_library_task_for_task_no(task_no, project)
        except Exception:
            task = None
    if (
        task is not None
        and str(getattr(task, "output_target", "") or "") == LibraryTask.OUTPUT_SITE_RECORD
    ):
        try:
            return _pdf_filename_label(
                build_standardized_site_record_pdf_original_name(
                    project,
                    task,
                    task_no,
                )
            )
        except Exception:
            pass
    for key in ("site_task_name", "detection_label"):
        raw = str(disp.get(key) or "").strip()
        picked = _pick_human_label_part(raw)
        if picked and not _looks_like_backend_code(picked):
            return _pdf_filename_label(
                picked if picked.lower().endswith(".pdf") else f"{picked}.pdf"
            )
    return ""


def _build_site_pdf_label_index(
    projects: list[LibraryProject],
    site_rows: list[dict[str, Any]],
) -> dict[str, dict[tuple, str]]:
    """
    现场记录 PDF → 与原始记录相同的展示名索引。
    键：(project_id, task_no) / (project_id, library_task_id) / (project_id, case_id)
    """
    from apps.api.inspection_pdf_service import _task_no_strings_for_library_task
    from apps.core.models import InspectionSubmission, LibraryTask

    by_task_no: dict[tuple, str] = {}
    by_task_id: dict[tuple, str] = {}
    by_case: dict[tuple, str] = {}
    projects_by_id = {p.pk: p for p in projects}

    case_ids: list[int] = []
    for fr in site_rows:
        lf = fr.get("file")
        if lf is None:
            continue
        if not (lf.original_name or "").lower().endswith(".pdf"):
            continue
        if (
            getattr(lf, "link_entity", None) == LibraryFile.LINK_ENTITY_INSPECTION_CASE
            and lf.link_object_id
        ):
            case_ids.append(int(lf.link_object_id))

    case_task_no: dict[int, str] = {}
    if case_ids:
        for sub in (
            InspectionSubmission.objects.filter(case_id__in=case_ids)
            .order_by("case_id", "-submitted_at", "-id")
            .only("case_id", "task_no")
        ):
            cid = int(sub.case_id or 0)
            if cid and cid not in case_task_no and (sub.task_no or "").strip():
                case_task_no[cid] = str(sub.task_no).strip()

    def _put(bucket: dict[tuple, str], key: tuple, label: str) -> None:
        if not key[0] or not key[1] or not label:
            return
        # site_rows 已按 -created_at；先写入者为最新
        if key not in bucket:
            bucket[key] = label

    for fr in site_rows:
        lf = fr.get("file")
        if lf is None:
            continue
        if not (lf.original_name or "").lower().endswith(".pdf"):
            continue
        pid = int(fr.get("project_id") or 0)
        if not pid:
            continue
        label = fr.get("display_name") or _pdf_filename_label(lf.original_name or "")
        proj = projects_by_id.get(pid)

        for task in lf.library_tasks.all():
            if str(getattr(task, "output_target", "") or "") != LibraryTask.OUTPUT_SITE_RECORD:
                continue
            _put(by_task_id, (pid, int(task.pk)), label)
            if proj is not None:
                try:
                    for tn in _task_no_strings_for_library_task(proj, task):
                        _put(by_task_no, (pid, str(tn).strip()), label)
                except Exception:
                    pass

        if (
            getattr(lf, "link_entity", None) == LibraryFile.LINK_ENTITY_INSPECTION_CASE
            and lf.link_object_id
        ):
            cid = int(lf.link_object_id)
            _put(by_case, (pid, cid), label)
            tn = case_task_no.get(cid) or ""
            if tn and proj is not None:
                # 仅当提交序号确实属于本 PDF 绑定的现场任务时，才按 task_no 建索引，
                # 避免 case_no/task_no 与 M2M 现场任务不一致时把胃肠机 PDF 挂到 DR 叶子上。
                file_task_nos: set[str] = set()
                try:
                    for task in lf.library_tasks.all():
                        if str(getattr(task, "output_target", "") or "") != LibraryTask.OUTPUT_SITE_RECORD:
                            continue
                        for x in _task_no_strings_for_library_task(proj, task):
                            s = str(x or "").strip()
                            if s:
                                file_task_nos.add(s)
                                if s.isdigit():
                                    file_task_nos.add(f"{int(s):02d}")
                                    file_task_nos.add(str(int(s)))
                except Exception:
                    file_task_nos = set()
                tn_s = str(tn).strip()
                tn_ok = tn_s in file_task_nos
                if not tn_ok and tn_s.isdigit():
                    tn_ok = (
                        f"{int(tn_s):02d}" in file_task_nos
                        or str(int(tn_s)) in file_task_nos
                    )
                if tn_ok or not file_task_nos:
                    _put(by_task_no, (pid, tn_s), label)
            elif tn:
                _put(by_task_no, (pid, str(tn).strip()), label)

        # 旧版文件名：{taskNo}-现场记录.pdf / …__task__{taskNo}-现场记录.pdf
        name = (lf.original_name or "").strip()
        lower = name.lower()
        marker = "-现场记录.pdf"
        idx = lower.rfind(marker)
        if idx >= 0:
            base = name[:idx].strip()
            if "__task__" in base:
                base = base.rsplit("__task__", 1)[-1].strip()
            if base:
                _put(by_task_no, (pid, base), label)
                if "_" in base:
                    _put(by_task_no, (pid, base.replace("_", "/")), label)

    return {"by_task_no": by_task_no, "by_task_id": by_task_id, "by_case": by_case}


def _resolve_site_pdf_label(
    index: dict[str, dict[tuple, str]],
    *,
    project_id: int,
    task_no: str = "",
    site_task_id: int = 0,
    case_id: int = 0,
) -> str:
    by_task_no = index.get("by_task_no") or {}
    by_task_id = index.get("by_task_id") or {}
    by_case = index.get("by_case") or {}
    if site_task_id:
        hit = by_task_id.get((project_id, int(site_task_id)))
        if hit and not _is_abstract_pdf_label(hit):
            return hit
    if task_no:
        hit = by_task_no.get((project_id, str(task_no).strip()))
        if hit and not _is_abstract_pdf_label(hit):
            return hit
        # 兼容 01 / 1
        raw = str(task_no).strip()
        if raw.isdigit():
            alt = f"{int(raw):02d}"
            hit = by_task_no.get((project_id, alt)) or by_task_no.get((project_id, str(int(raw))))
            if hit and not _is_abstract_pdf_label(hit):
                return hit
    if case_id:
        hit = by_case.get((project_id, int(case_id)))
        # 已指定现场任务但该任务自身无 PDF 时，勿用「同案件其它任务的 PDF」冒充标题
        if hit and not _is_abstract_pdf_label(hit):
            if site_task_id:
                own = by_task_id.get((project_id, int(site_task_id)))
                if own and own == hit:
                    return hit
            else:
                return hit
    # 抽象名也凑合返回，后面还会用预期名覆盖
    if site_task_id:
        hit = by_task_id.get((project_id, int(site_task_id)))
        if hit:
            return hit
    if task_no:
        hit = by_task_no.get((project_id, str(task_no).strip()))
        if hit:
            return hit
    if case_id and not site_task_id:
        hit = by_case.get((project_id, int(case_id)))
        if hit:
            return hit
    return ""


def _apply_site_pdf_labels_for_report_generate(
    projects: list[LibraryProject],
    progress_map: dict[int, list[dict[str, Any]]],
    report_file_rows: list[dict[str, Any]],
    site_rows: list[dict[str, Any]],
    *,
    exportable: set[tuple[int, int]] | None = None,
) -> None:
    """报告生成/预览：标题与原始记录同一套现场 PDF 规范名（exportable 仅保留兼容，不参与打标）。"""
    _ = exportable
    index = _build_site_pdf_label_index(projects, site_rows)
    projects_by_id = {p.pk: p for p in projects}

    for p in projects:
        disp_index = task_no_display_index(p)
        for row in progress_map.get(p.pk) or []:
            task_no = str(row.get("task_no") or "").strip()
            site_task_id = int(row.get("site_library_task_id") or 0) or 0
            case_id = int(row.get("case_id") or 0) or 0
            disp = disp_index.get(task_no) or {}
            label = _resolve_site_pdf_label(
                index,
                project_id=p.pk,
                task_no=task_no,
                site_task_id=site_task_id,
                case_id=case_id,
            )
            # 必须有现场任务 id，避免「其他提交」仅凭 task_no/case 串用别的 PDF 名
            can_use_pdf_name = bool(
                label and not _is_abstract_pdf_label(label) and site_task_id
            )
            if not can_use_pdf_name:
                # 同设备仅一条进度时，用该设备下原始记录 PDF 名对齐（多任务同设备不串名）
                eq = (row.get("equipment_title") or "").strip()
                if eq and eq not in ("其他", "—", "其他提交"):
                    eq_prog_n = sum(
                        1
                        for r in (progress_map.get(p.pk) or [])
                        if (r.get("equipment_title") or "").strip() == eq
                    )
                    if eq_prog_n == 1:
                        for fr in site_rows:
                            if int(fr.get("project_id") or 0) != int(p.pk):
                                continue
                            if (fr.get("equipment_title") or "").strip() != eq:
                                continue
                            cand = fr.get("display_name") or ""
                            if cand and not _is_abstract_pdf_label(cand):
                                label = cand
                                can_use_pdf_name = True
                                break
            if can_use_pdf_name:
                row["display_title"] = label
                row["detection_label"] = label
            else:
                row["display_title"] = _friendly_task_display_title(row, disp)
                row["detection_label"] = (
                    disp.get("site_task_name")
                    or disp.get("detection_label")
                    or row["display_title"]
                )

    for fr in report_file_rows:
        lf = fr.get("file")
        if lf is None:
            continue
        current = fr.get("display_name") or _pdf_filename_label(lf.original_name or "")
        if not _is_abstract_pdf_label(current):
            fr["display_name"] = current
            continue
        pid = int(fr.get("project_id") or 0)
        proj = projects_by_id.get(pid)
        site_task_id = 0
        task_no = ""
        case_id = 0
        try:
            from apps.core.models import LibraryTask

            for task in lf.library_tasks.all():
                # 报告任务 → 取其源现场记录任务
                if str(getattr(task, "output_target", "") or "") == LibraryTask.OUTPUT_SITE_RECORD:
                    site_task_id = int(task.pk)
                    break
                if str(getattr(task, "output_target", "") or "") == LibraryTask.OUTPUT_REPORT:
                    src = (
                        task.report_source_tasks.filter(
                            output_target=LibraryTask.OUTPUT_SITE_RECORD
                        )
                        .order_by("code", "id")
                        .first()
                    )
                    if src is not None:
                        site_task_id = int(src.pk)
                        break
            if (
                getattr(lf, "link_entity", None) == LibraryFile.LINK_ENTITY_INSPECTION_CASE
                and lf.link_object_id
            ):
                case_id = int(lf.link_object_id)
            if proj is not None and site_task_id:
                from apps.api.inspection_pdf_service import _task_no_strings_for_library_task
                from apps.core.models import LibraryTask as LT

                st = LT.objects.filter(pk=site_task_id).first()
                if st is not None:
                    nos = _task_no_strings_for_library_task(proj, st)
                    task_no = nos[0] if nos else ""
        except Exception:
            pass
        label = _resolve_site_pdf_label(
            index,
            project_id=pid,
            task_no=task_no,
            site_task_id=site_task_id,
            case_id=case_id,
        )
        if not label or _is_abstract_pdf_label(label):
            if proj is not None:
                label = _expected_site_pdf_label(
                    proj,
                    task_no=task_no,
                    site_task_id=site_task_id,
                    disp={},
                )
        if label:
            fr["display_name"] = label


def _flatten_progress(
    user: User, projects: list[LibraryProject]
) -> dict[int, list[dict[str, Any]]]:
    if not projects:
        return {}
    by_pid = _bulk_project_item_progress(projects, viewer=user, include_large=True)
    out: dict[int, list[dict[str, Any]]] = {}
    for p in projects:
        disp_index = task_no_display_index(p)
        rows = []
        for item in by_pid.get(p.pk) or []:
            row = dict(item)
            row["kind"] = "progress"
            row["project"] = p
            row["project_id"] = p.pk
            row["project_code"] = p.code
            row["project_name"] = p.name
            actions = row.get("advance_actions") or []
            row["primary_action"] = actions[0] if actions else None
            title = (row.get("equipment_title") or "—").strip() or "—"
            row["equipment_key"] = title
            row["equipment_title"] = title
            task_no = str(row.get("task_no") or "").strip()
            disp = disp_index.get(task_no) or {}
            row["display_title"] = _friendly_task_display_title(row, disp)
            row["detection_label"] = (
                disp.get("site_task_name")
                or disp.get("detection_label")
                or row["display_title"]
            )
            site_task_id = int(row.get("library_task_id") or 0) or 0
            if not site_task_id and task_no:
                try:
                    from apps.api.inspection_pdf_service import _resolve_library_task_for_task_no
                    from apps.core.models import LibraryTask

                    st = _resolve_library_task_for_task_no(task_no, p)
                    # 序号解析可能落到报告任务；仅采纳现场记录类任务
                    if (
                        st is not None
                        and getattr(st, "pk", None)
                        and str(getattr(st, "output_target", "") or "") == LibraryTask.OUTPUT_SITE_RECORD
                    ):
                        site_task_id = int(st.pk)
                except Exception:
                    site_task_id = 0
            row["site_library_task_id"] = site_task_id
            # 报告生成页会再按是否确有现场数据收紧；其它枢纽仅作占位
            row["can_export_report"] = bool(task_no and site_task_id)
            row["has_site_export_source"] = False
            row["generate_library_url"] = (
                reverse("file_library")
                + "?"
                + urlencode({"tab": "site_record", "project": str(p.pk)})
            )
            row["submit_library_url"] = (
                reverse("file_library")
                + "?"
                + urlencode({"tab": "inspection_submit", "project": str(p.pk)})
            )
            row["report_library_url"] = (
                reverse("file_library")
                + "?"
                + urlencode({"tab": "report", "project": str(p.pk)})
            )
            sig = row.get("report_signature") if isinstance(row.get("report_signature"), dict) else {}
            preview = str(sig.get("report_preview_url") or "").strip()
            row["report_preview_url"] = preview
            # 多版本分开展示时带上版本角标
            row["report_variant_label"] = str(row.get("report_variant_label") or sig.get("export_variant") or "").strip()
            if row.get("export_variant") and not row.get("report_variant_label"):
                try:
                    from radiation_detection_report.report_pdf_integrator import (
                        report_export_variant_label,
                    )

                    row["report_variant_label"] = report_export_variant_label(
                        row.get("export_variant")
                    )
                except Exception:
                    pass
            rows.append(row)
        out[p.pk] = rows
    return out


def _report_generate_status_counts(leaves: list[dict]) -> tuple[int, int, int]:
    """返回 (待编制, 已编制, 已提交编制人签名)。"""
    pending = done = signed = 0
    for leaf in leaves:
        status = str(leaf.get("compile_status") or "")
        if status == "author_signed" or leaf.get("author_signed"):
            signed += 1
        elif status == "done" or leaf.get("has_exported_report"):
            done += 1
        else:
            pending += 1
    return pending, done, signed


def _report_generate_badge_label(pending: int, done: int, signed: int) -> str:
    parts = []
    if pending:
        parts.append(f"{pending} 项待编制")
    if done:
        parts.append(f"{done} 项已编制")
    if signed:
        parts.append(f"{signed} 项已提交编制人签名")
    return " · ".join(parts) if parts else "暂无待编制"


def _report_generate_sort_key(leaf: dict) -> tuple:
    """待编制 → 已编制 → 已提交编制人签名。"""
    status = str(leaf.get("compile_status") or "")
    if status == "pending" or (
        leaf.get("kind") == "progress"
        and not leaf.get("has_exported_report")
        and not leaf.get("author_signed")
    ):
        rank = 0
    elif status == "done":
        rank = 1
    elif status == "author_signed" or leaf.get("author_signed"):
        rank = 2
    else:
        rank = 1
    return (rank, str(leaf.get("display_title") or leaf.get("task_no") or ""))


def _leaf_attention(hub: str, leaf: dict) -> bool:
    if hub == "report_generate":
        # 编制人已签字：仍留在生成页展示「已提交编制人签名」
        if leaf.get("author_signed") or leaf.get("compile_status") == "author_signed":
            return True
        stage = str(leaf.get("stage_code") or "")
        act = leaf.get("primary_action") or {}
        target = str(act.get("target_stage") or "")
        return stage == InspectionCaseWorkflowState.STAGE_REPORT_DRAFT or target == (
            InspectionCaseWorkflowState.STAGE_REPORT_AUDIT
        )
    if hub == "review_sign":
        stage = str(leaf.get("stage_code") or "")
        act = leaf.get("primary_action") or {}
        target = str(act.get("target_stage") or "")
        return target in (
            InspectionCaseWorkflowState.STAGE_REPORT_SIGN,
            InspectionCaseWorkflowState.STAGE_ISSUED,
        ) or stage in (
            InspectionCaseWorkflowState.STAGE_REPORT_AUDIT,
            InspectionCaseWorkflowState.STAGE_REPORT_SIGN,
        )
    return False


def _filter_progress_for_hub(hub: str, rows: list[dict], *, tab: str = "") -> list[dict]:
    """各枢纽均展示全部检测项目进度；审核签发页签仍可只看审核/签发子集。"""
    if hub == "report_generate":
        return list(rows)
    if hub == "review_sign":
        tagged: list[dict] = []
        audit, sign = [], []
        for r in rows:
            stage = str(r.get("stage_code") or "")
            act = r.get("primary_action") or {}
            target = str(act.get("target_stage") or "")
            is_audit = target == InspectionCaseWorkflowState.STAGE_REPORT_SIGN or stage == (
                InspectionCaseWorkflowState.STAGE_REPORT_AUDIT
            )
            is_sign = target == InspectionCaseWorkflowState.STAGE_ISSUED or stage == (
                InspectionCaseWorkflowState.STAGE_REPORT_SIGN
            )
            if is_audit:
                ra = dict(r)
                ra["hub_section"] = "audit"
                audit.append(ra)
            if is_sign:
                rs = dict(r)
                rs["hub_section"] = "sign"
                sign.append(rs)
            row = dict(r)
            if is_audit:
                row["hub_section"] = "audit"
            elif is_sign:
                row["hub_section"] = "sign"
            else:
                row["hub_section"] = ""
            tagged.append(row)
        tab_n = (tab or "all").lower()
        if tab_n == "audit":
            return audit
        if tab_n == "sign":
            return sign
        return tagged
    return list(rows)


def _commission_ts(project: LibraryProject, progress_rows: list[dict], file_rows: list[dict]) -> datetime | None:
    candidates: list[datetime] = []
    if project.updated_at:
        candidates.append(project.updated_at)
    for r in progress_rows:
        ts = r.get("progress_updated_at")
        if ts:
            candidates.append(ts)
    for r in file_rows:
        ts = r.get("created_at")
        if ts:
            candidates.append(ts)
    return max(candidates) if candidates else project.updated_at


def _report_file_version_group_key(leaf: dict[str, Any]) -> str:
    """
    报告文件版本分组键：全本 / 无防护结果 / 仅防护结果各自独立。
    同组内才折叠为「最新 + 历史」，不同导出类型并列显示。
    """
    # 优先用库内原名识别变体（展示名可能被规范成现场记录名）
    lf = leaf.get("file")
    name = ""
    if lf is not None:
        name = str(getattr(lf, "original_name", "") or "").strip()
    if not name:
        name = str(leaf.get("display_name") or "").strip()
    try:
        from radiation_detection_report.report_pdf_integrator import (
            report_export_variant_from_filename,
        )

        return report_export_variant_from_filename(name)
    except Exception:
        return "full"


def _split_equipment_file_versions(equipments: list[dict[str, Any]]) -> None:
    """
    每个设备下：同类文件只保留最新一份在 leaves，其余进 history_leaves。
    报告的全本 / 无防护结果 / 仅防护结果视为不同报告，互不折叠为历史。
    进度类叶子保持原样与最新文件并列展示。
    """
    for eq in equipments:
        leaves = list(eq.get("leaves") or [])
        files = [l for l in leaves if l.get("kind") == "file"]
        others = [l for l in leaves if l.get("kind") != "file"]
        by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for fr in files:
            by_group[_report_file_version_group_key(fr)].append(fr)

        history: list[dict[str, Any]] = []
        primary_files: list[dict[str, Any]] = []
        # 稳定顺序：全本 → 无防护 → 仅防护 → 其它
        group_order = {"full": 0, "front": 1, "rp": 2}
        for gkey in sorted(by_group.keys(), key=lambda k: (group_order.get(k, 9), k)):
            group_files = list(by_group[gkey])
            group_files.sort(
                key=lambda x: (
                    x.get("created_at").timestamp() if x.get("created_at") else 0.0
                ),
                reverse=True,
            )
            if not group_files:
                continue
            latest = dict(group_files[0])
            latest["is_latest"] = True
            latest["report_export_variant"] = gkey
            primary_files.append(latest)
            for older in group_files[1:]:
                row = dict(older)
                row["is_latest"] = False
                row["report_export_variant"] = gkey
                history.append(row)
        eq["leaves"] = others + primary_files
        eq["history_leaves"] = history
        eq["history_count"] = len(history)


def _filter_report_generate_leaves(
    *,
    prog: list[dict],
    files: list[dict],
    tab: str,
) -> tuple[list[dict], list[dict]]:
    """编制页：generate 以进度为主；preview 只展示报告 PDF（便于预览/合并）。"""
    t = (tab or "generate").strip().lower()
    if t == "generate":
        return prog, []
    if t == "preview":
        return [], files
    return prog, files


def _annotate_report_generate_compile_status(
    progress_map: dict[int, list[dict[str, Any]]],
    report_files: list[dict[str, Any]] | None = None,
) -> None:
    """
    报告生成页状态：
    - 待编制：尚无报告 PDF
    - 已编制：已导出报告，编制人尚未签字
    - 已提交编制人签名：编制人已签字（流程已进入审核及之后，或签字位已落章）

    导出成功后即可「重新导出」覆盖；编制人签字后仍留在本页展示状态（推进审核在「审核签发」）。
    """
    case_keys: set[tuple[int, int]] = set()
    # 案件 → 报告所对应的现场任务 id（避免同 case_no 下胃肠机报告把 DR 进度标成已编制）
    case_site_tasks: dict[tuple[int, int], set[int]] = defaultdict(set)
    site_task_has_report: set[tuple[int, int]] = set()
    task_keys: set[tuple[int, str]] = set()
    for fr in report_files or []:
        lf = fr.get("file")
        if lf is None:
            continue
        pid = int(fr.get("project_id") or 0)
        if not pid:
            continue
        case_id = 0
        if (
            getattr(lf, "link_entity", None) == LibraryFile.LINK_ENTITY_INSPECTION_CASE
            and lf.link_object_id
        ):
            case_id = int(lf.link_object_id)
            case_keys.add((pid, case_id))
        try:
            for task in lf.library_tasks.all():
                ot = str(getattr(task, "output_target", "") or "")
                if ot == LibraryTask.OUTPUT_SITE_RECORD:
                    site_task_has_report.add((pid, int(task.pk)))
                    if case_id:
                        case_site_tasks[(pid, case_id)].add(int(task.pk))
                elif ot == LibraryTask.OUTPUT_REPORT:
                    for src in task.report_source_tasks.all():
                        if str(getattr(src, "output_target", "") or "") == LibraryTask.OUTPUT_SITE_RECORD:
                            site_task_has_report.add((pid, int(src.pk)))
                            if case_id:
                                case_site_tasks[(pid, case_id)].add(int(src.pk))
        except Exception:
            pass
        name = (getattr(lf, "original_name", None) or "").strip()
        if name:
            lower = name.lower()
            # 变体后缀插在「报告」与「.pdf」之间，需一并识别
            markers = (
                "报告（仅防护结果）.pdf",
                "报告（无防护结果）.pdf",
                "报告(仅防护结果).pdf",
                "报告(无防护结果).pdf",
                "-报告.pdf",
                "_报告.pdf",
            )
            for marker in markers:
                idx = lower.rfind(marker.lower())
                if idx >= 0:
                    base = name[:idx].strip()
                    if marker.startswith(("报告",)):
                        # 「…_报告（无防护）.pdf」→ 去掉末尾分隔符
                        base = base.rstrip("_- ")
                    if "__task__" in base:
                        base = base.rsplit("__task__", 1)[-1].strip()
                    if base:
                        task_keys.add((pid, base))
                    break

    author_signed_stages = {
        InspectionCaseWorkflowState.STAGE_REPORT_AUDIT,
        InspectionCaseWorkflowState.STAGE_REPORT_SIGN,
        InspectionCaseWorkflowState.STAGE_ISSUED,
    }

    for pid, rows in (progress_map or {}).items():
        for row in rows or []:
            if row.get("kind") and row.get("kind") != "progress":
                continue
            sig = row.get("report_signature") if isinstance(row.get("report_signature"), dict) else {}
            has_report = bool(sig.get("enabled") or sig.get("report_file") or sig.get("report_file_name"))
            row_site = int(row.get("site_library_task_id") or row.get("library_task_id") or 0) or 0
            # 签字/报告若挂在同案件但属其它现场任务，不视为本进度已编制
            if has_report and row_site:
                rf = sig.get("report_file")
                if rf is not None:
                    try:
                        rf_sites: set[int] = set()
                        for task in rf.library_tasks.all():
                            ot = str(getattr(task, "output_target", "") or "")
                            if ot == LibraryTask.OUTPUT_SITE_RECORD:
                                rf_sites.add(int(task.pk))
                            elif ot == LibraryTask.OUTPUT_REPORT:
                                for src in task.report_source_tasks.all():
                                    if str(getattr(src, "output_target", "") or "") == LibraryTask.OUTPUT_SITE_RECORD:
                                        rf_sites.add(int(src.pk))
                        if rf_sites and row_site not in rf_sites:
                            has_report = False
                    except Exception:
                        pass
            if not has_report and row_site and (int(pid), row_site) in site_task_has_report:
                has_report = True
            if not has_report:
                case_id = int(row.get("case_id") or 0) or 0
                task_no = str(row.get("task_no") or "").strip()
                if case_id and (int(pid), case_id) in case_keys:
                    sites = case_site_tasks.get((int(pid), case_id)) or set()
                    # 有现场任务约束时必须匹配，防止串设备
                    if sites and row_site:
                        has_report = row_site in sites
                    elif not sites:
                        has_report = True
                if not has_report and task_no and (int(pid), task_no) in task_keys:
                    has_report = True
                elif not has_report and task_no:
                    safe = task_no.replace("/", "_")
                    if safe != task_no and (int(pid), safe) in task_keys:
                        has_report = True

            author_signed = False
            for slot_ui in sig.get("slots") or []:
                if not isinstance(slot_ui, dict):
                    continue
                if (
                    str(slot_ui.get("slot") or "") == "reportAuthor"
                    and bool(slot_ui.get("signed"))
                ):
                    author_signed = True
                    break
            stage = str(row.get("stage_code") or "").strip()
            if stage in author_signed_stages:
                author_signed = True

            row["has_exported_report"] = has_report or author_signed
            row["author_signed"] = author_signed
            if author_signed:
                row["compile_status"] = "author_signed"
                row["compile_status_label"] = "已提交编制人签名"
                # 生成页优先展示「重新提交编制人签名」；审核推进在「审核签发」
                resign = next(
                    (
                        a
                        for a in (row.get("advance_actions") or [])
                        if isinstance(a, dict)
                        and a.get("is_resign")
                        and str(a.get("target_stage") or "")
                        == InspectionCaseWorkflowState.STAGE_REPORT_AUDIT
                    ),
                    None,
                )
                row["primary_action"] = resign
            elif has_report:
                row["compile_status"] = "done"
                row["compile_status_label"] = "已编制"
            else:
                row["compile_status"] = "pending"
                row["compile_status_label"] = "待编制"


def build_hub_browse_tree(
    user: User,
    *,
    hub: str,
    view: str = VIEW_HOSPITAL,
    search: str = "",
    focus_project_id: int = 0,
    task_no: str = "",
    tab: str = "",
) -> dict[str, Any]:
    """
    hub: site_records | report_generate | review_sign | report_download
    返回三级树 tree_groups。
    """
    view_mode = _norm_view(view)
    needle = (search or "").strip().lower()
    projects = _accessible_projects(user)
    pids = [p.pk for p in projects]
    progress_map = _flatten_progress(user, projects)

    site_files = (
        _file_rows(user, category=LibraryFile.CATEGORY_SITE_RECORD, project_ids=pids, limit=2000)
        if hub == "site_records"
        else []
    )
    # 报告生成页不把现场 PDF 挂进树，但要用其文件名给编制/预览叶子打标
    site_files_for_labels: list[dict[str, Any]] = []
    if hub == "report_generate":
        site_files_for_labels = _file_rows(
            user, category=LibraryFile.CATEGORY_SITE_RECORD, project_ids=pids
        )
    report_files = (
        _file_rows(user, category=LibraryFile.CATEGORY_REPORT, project_ids=pids)
        if hub in ("report_generate", "report_download")
        else []
    )

    if hub == "report_generate":
        exportable_keys = _exportable_site_task_keys(user, projects)
        _annotate_report_generate_export_availability(progress_map, exportable_keys)
        # 先给现场 PDF 打上与进度相同的设备标题，再按设备对齐标题
        for p in projects:
            prog_rows = progress_map.get(p.pk) or []
            titles = list(
                dict.fromkeys(
                    (row.get("equipment_title") or "").strip()
                    for row in prog_rows
                    if (row.get("equipment_title") or "").strip()
                )
            )
            task_id_to_title: dict[int, str] = {}
            for row in prog_rows:
                title = (row.get("equipment_title") or "").strip()
                if not title or title == "—":
                    continue
                for key in ("site_library_task_id", "library_task_id"):
                    tid = int(row.get(key) or 0) or 0
                    if tid and tid not in task_id_to_title:
                        task_id_to_title[tid] = title
            for fr in site_files_for_labels:
                if int(fr.get("project_id") or 0) != int(p.pk):
                    continue
                lf = fr.get("file")
                if lf is None:
                    continue
                linked = _equipment_title_from_linked_site_task(lf, task_id_to_title)
                if linked:
                    fr["equipment_key"] = linked
                    fr["equipment_title"] = linked
                    continue
                key, title = _match_equipment_for_file(
                    fr.get("display_name") or "",
                    titles,
                    alt_names=[
                        lf.original_name or "",
                        fr.get("template_name") or "",
                    ],
                )
                fr["equipment_key"] = key
                fr["equipment_title"] = title
        _apply_site_pdf_labels_for_report_generate(
            projects,
            progress_map,
            report_files,
            site_files_for_labels,
            exportable=exportable_keys,
        )
        _annotate_report_generate_compile_status(progress_map, report_files)

    files_by_pid: dict[int, list[dict]] = defaultdict(list)
    for r in site_files + report_files:
        files_by_pid[int(r.get("project_id") or 0)].append(r)

    # 为文件匹配设备名：优先绑定现场任务 → 再按规范名/文件名启发式匹配
    for p in projects:
        prog_rows = progress_map.get(p.pk) or []
        titles = [
            (row.get("equipment_title") or "").strip()
            for row in prog_rows
            if (row.get("equipment_title") or "").strip()
        ]
        titles = list(dict.fromkeys(titles))
        task_id_to_title: dict[int, str] = {}
        for row in prog_rows:
            title = (row.get("equipment_title") or "").strip()
            if not title or title == "—":
                continue
            for key in ("site_library_task_id", "library_task_id"):
                tid = int(row.get(key) or 0) or 0
                if tid and tid not in task_id_to_title:
                    task_id_to_title[tid] = title
        for fr in files_by_pid.get(p.pk) or []:
            lf = fr["file"]
            linked = _equipment_title_from_linked_site_task(lf, task_id_to_title)
            if linked:
                fr["equipment_key"] = linked
                fr["equipment_title"] = linked
                continue
            key, title = _match_equipment_for_file(
                fr.get("display_name") or "",
                titles,
                alt_names=[
                    lf.original_name or "",
                    fr.get("template_name") or "",
                ],
            )
            fr["equipment_key"] = key
            fr["equipment_title"] = title

    # 组装每个委托的设备叶子
    commission_nodes: list[dict[str, Any]] = []
    for p in projects:
        hospital_key, hospital_name, legacy_hospital_name = _hospital_meta(p)
        prog = progress_map.get(p.pk) or []
        if task_no:
            prog = [r for r in prog if str(r.get("task_no") or "") == task_no]
        prog = _filter_progress_for_hub(hub, prog, tab=tab)

        files = files_by_pid.get(p.pk) or []
        if hub == "site_records":
            files = [f for f in files if f.get("file") and f["file"].category == LibraryFile.CATEGORY_SITE_RECORD]
            # 文件↔进度互相关联；原始记录页保留全部文件叶子，避免已签发项「看不见 PDF」
            _attach_site_record_files_to_progress(prog, files)
            # 已有对应 PDF 的进度不再单独占一行，减少重复；无文件的进度仍展示状态
            prog = [r for r in prog if not r.get("has_site_file")]
        elif hub == "report_download":
            from apps.core.report_signature_service import is_issued_report_snapshot_file

            files = [
                f
                for f in files
                if f.get("file")
                and f["file"].category == LibraryFile.CATEGORY_REPORT
                and not is_issued_report_snapshot_file(f.get("file"))
            ]
        elif hub == "report_generate":
            from apps.core.report_signature_service import is_issued_report_snapshot_file

            files = [
                f
                for f in files
                if f.get("file")
                and f["file"].category == LibraryFile.CATEGORY_REPORT
                and not is_issued_report_snapshot_file(f.get("file"))
            ]
            prog, files = _filter_report_generate_leaves(prog=prog, files=files, tab=tab)
        else:
            files = []

        # 设备分组
        equip_map: dict[str, dict[str, Any]] = {}

        def _equip(title: str) -> dict:
            key = title or "其他"
            if key not in equip_map:
                equip_map[key] = {
                    "equipment_key": key,
                    "equipment_title": key,
                    "leaves": [],
                    "history_leaves": [],
                    "history_count": 0,
                    "attention": False,
                    "open": False,
                }
            return equip_map[key]

        # 各枢纽均挂进度叶子，便于统一展示六步流程状态
        for row in prog:
            eq = _equip(row.get("equipment_title") or "其他")
            eq["leaves"].append(row)
            if _leaf_attention(hub, row):
                eq["attention"] = True
        for fr in files:
            eq = _equip(fr.get("equipment_title") or "其他")
            eq["leaves"].append(fr)

        equipments = list(equip_map.values())
        _split_equipment_file_versions(equipments)
        # 「其他」放最后；有待办的设备靠前
        equipments.sort(
            key=lambda e: (
                e["equipment_title"] == "其他",
                not e["attention"],
                e["equipment_title"],
            )
        )

        leaf_count = sum(len(e["leaves"]) for e in equipments)
        progress_leaves = [
            leaf
            for e in equipments
            for leaf in e["leaves"]
            if leaf.get("kind") == "progress"
        ]
        attention_n = 0
        done_n = 0
        signed_n = 0
        visible_progress_n = len(progress_leaves)
        rg_tab = (tab or "generate").strip().lower()
        if hub == "report_generate":
            attention_n, done_n, signed_n = _report_generate_status_counts(progress_leaves)
        elif hub == "review_sign":
            attention_n = sum(1 for leaf in progress_leaves if _leaf_attention(hub, leaf))
        else:
            attention_n = visible_progress_n

        if hub == "site_records":
            file_n = sum(
                1
                for e in equipments
                for leaf in e["leaves"]
                if leaf.get("kind") == "file"
            )
            badge_label = (
                f"{visible_progress_n} 项进度 · {file_n} 份记录"
                if visible_progress_n
                else (f"{file_n} 份记录" if file_n else "暂无记录")
            )
        elif hub == "report_generate":
            if rg_tab == "preview":
                file_n = sum(
                    1
                    for e in equipments
                    for leaf in e["leaves"]
                    if leaf.get("kind") == "file"
                )
                badge_label = f"{file_n} 份报告" if file_n else "暂无报告"
            else:
                badge_label = _report_generate_badge_label(attention_n, done_n, signed_n)
        elif hub == "review_sign":
            badge_label = (
                f"{visible_progress_n} 项进度"
                + (f" · {attention_n} 项待办" if attention_n else "")
            )
        else:
            file_n = sum(
                1
                for e in equipments
                for leaf in e["leaves"]
                if leaf.get("kind") == "file"
            )
            badge_label = (
                f"{visible_progress_n} 项进度 · {file_n} 份报告"
                if visible_progress_n or file_n
                else "暂无报告"
            )

        # 搜索过滤
        if needle:
            hit = _search_hit(
                needle,
                p.code or "",
                p.name or "",
                hospital_name,
                legacy_hospital_name,
            )
            if not hit:
                # 设备/文件名命中则保留匹配叶子（含历史版本）
                kept = []
                for eq in equipments:
                    if _search_hit(needle, eq["equipment_title"]):
                        kept.append(eq)
                        continue

                    def _leaf_name(leaf: dict) -> str:
                        if leaf.get("kind") == "file":
                            return f"{leaf.get('display_name', '')} {leaf['file'].original_name}"
                        return (
                            f"{leaf.get('equipment_title', '')} "
                            f"{leaf.get('display_title', '')} "
                            f"{leaf.get('task_no', '')} {leaf.get('task_label', '')}"
                        )

                    leaves = [leaf for leaf in eq["leaves"] if _search_hit(needle, _leaf_name(leaf))]
                    history = [
                        leaf
                        for leaf in (eq.get("history_leaves") or [])
                        if _search_hit(needle, _leaf_name(leaf))
                    ]
                    if leaves or history:
                        eq = dict(eq)
                        eq["leaves"] = leaves
                        eq["history_leaves"] = history
                        eq["history_count"] = len(history)
                        kept.append(eq)
                if not kept and not hit:
                    continue
                equipments = kept
                leaf_count = sum(len(e["leaves"]) for e in equipments)
                if hub == "report_generate":
                    pending_leaves = [
                        leaf
                        for e in equipments
                        for leaf in e["leaves"]
                        if leaf.get("kind") == "progress" and _leaf_attention(hub, leaf)
                    ]
                    attention_n, done_n, signed_n = _report_generate_status_counts(pending_leaves)
                    visible_progress_n = len(pending_leaves)
                    if rg_tab == "preview":
                        badge_label = f"{leaf_count} 份报告" if leaf_count else "暂无报告"
                    else:
                        badge_label = _report_generate_badge_label(attention_n, done_n, signed_n)
                elif hub == "review_sign":
                    attention_n = sum(
                        1 for e in equipments for leaf in e["leaves"] if _leaf_attention(hub, leaf)
                    )
                    visible_progress_n = attention_n
                    badge_label = f"{attention_n} 项待办" if attention_n else "暂无待办"
                else:
                    attention_n = leaf_count
                    visible_progress_n = leaf_count
                    badge_label = (
                        f"{leaf_count} 份记录" if hub == "site_records" else f"{leaf_count} 份报告"
                    )

        if leaf_count == 0 and not (focus_project_id and focus_project_id == p.pk):
            continue

        if hub in ("report_generate", "review_sign") and not (focus_project_id and focus_project_id == p.pk):
            # 生成页：无编制进度项才隐藏；仅已编制时仍显示以便再导出
            if hub == "report_generate" and rg_tab != "preview" and visible_progress_n == 0:
                continue
            if hub == "review_sign" and attention_n == 0:
                continue

        ts = _commission_ts(p, progress_map.get(p.pk) or [], files_by_pid.get(p.pk) or [])
        bucket_key, bucket_label, bucket_rank = _time_bucket(ts)

        # 仅深链指定委托时展开对应路径，其余一律默认收起
        open_commission = bool(focus_project_id and focus_project_id == p.pk)
        for eq in equipments:
            eq["open"] = False
            if hub == "report_generate" and rg_tab != "preview":
                eq["leaves"].sort(key=_report_generate_sort_key)
            # 预览页：展开含报告文件的设备分组，便于直接点开预览
            if hub == "report_generate" and rg_tab == "preview":
                if any(leaf.get("kind") == "file" for leaf in eq.get("leaves") or []):
                    eq["open"] = True

        commission_nodes.append(
            {
                "project_id": p.pk,
                "code": (p.code or "").strip() or f"#{p.pk}",
                "name": (p.name or "").strip() or "未命名委托",
                "hospital_key": hospital_key,
                "hospital_name": hospital_name,
                "legacy_hospital_name": legacy_hospital_name,
                "is_ghost_hospital": hospital_key == HOSPITAL_KEY_GHOST,
                "time_key": bucket_key,
                "time_label": bucket_label,
                "time_rank": bucket_rank,
                "updated_at": ts,
                "equipments": equipments,
                "leaf_count": leaf_count,
                "attention": (
                    attention_n > 0
                    if hub != "report_generate" or rg_tab != "preview"
                    else leaf_count > 0
                ),
                "attention_n": (
                    attention_n
                    if (hub != "report_generate" or rg_tab != "preview")
                    else leaf_count
                ),
                "done_n": done_n if hub == "report_generate" and rg_tab != "preview" else 0,
                "signed_n": signed_n if hub == "report_generate" and rg_tab != "preview" else 0,
                "badge_label": badge_label,
                "open": open_commission,
            }
        )

    # 组树
    groups_map: dict[str, dict[str, Any]] = {}
    if view_mode == VIEW_TIME:
        for c in commission_nodes:
            g = groups_map.setdefault(
                c["time_key"],
                {
                    "key": c["time_key"],
                    "title": c["time_label"],
                    "rank": c["time_rank"],
                    "commissions": [],
                    "attention_n": 0,
                    "done_n": 0,
                    "signed_n": 0,
                    "leaf_count": 0,
                },
            )
            g["commissions"].append(c)
            g["attention_n"] += c["attention_n"]
            g["done_n"] += int(c.get("done_n") or 0)
            g["signed_n"] += int(c.get("signed_n") or 0)
            g["leaf_count"] += c["leaf_count"]
        groups = sorted(groups_map.values(), key=lambda g: g["rank"])
    else:
        for c in commission_nodes:
            g = groups_map.setdefault(
                c["hospital_key"],
                {
                    "key": c["hospital_key"],
                    "title": c["hospital_name"],
                    "rank": 0,
                    "is_ghost": c["hospital_key"] == HOSPITAL_KEY_GHOST,
                    "commissions": [],
                    "attention_n": 0,
                    "done_n": 0,
                    "signed_n": 0,
                    "leaf_count": 0,
                },
            )
            g["commissions"].append(c)
            g["attention_n"] += c["attention_n"]
            g["done_n"] += int(c.get("done_n") or 0)
            g["signed_n"] += int(c.get("signed_n") or 0)
            g["leaf_count"] += c["leaf_count"]
        groups = sorted(
            groups_map.values(),
            key=lambda g: (
                2 if g["key"] == HOSPITAL_KEY_GHOST else (1 if g["key"] == HOSPITAL_KEY_NONE else 0),
                g["attention_n"] == 0
                and int(g.get("done_n") or 0) == 0
                and int(g.get("signed_n") or 0) == 0,
                g["title"],
            ),
        )

    for g in groups:
        if view_mode == VIEW_TIME:
            g["commissions"].sort(
                key=lambda c: (
                    not c["attention"]
                    and int(c.get("done_n") or 0) == 0
                    and int(c.get("signed_n") or 0) == 0,
                    -(c["updated_at"].timestamp() if c.get("updated_at") else 0),
                    c["code"],
                )
            )
        else:
            g["commissions"].sort(
                key=lambda c: (
                    not c["attention"]
                    and int(c.get("done_n") or 0) == 0
                    and int(c.get("signed_n") or 0) == 0,
                    c["code"],
                )
            )
        g["attention"] = (
            g["attention_n"] > 0
            or int(g.get("done_n") or 0) > 0
            or int(g.get("signed_n") or 0) > 0
        )
        if hub == "site_records":
            g["badge_label"] = f"{g['leaf_count']} 份记录"
        elif hub == "report_generate":
            rg_tab = (tab or "generate").strip().lower()
            if rg_tab == "preview":
                g["badge_label"] = f"{g['leaf_count']} 份报告" if g["leaf_count"] else "暂无报告"
            else:
                g["badge_label"] = _report_generate_badge_label(
                    g["attention_n"],
                    int(g.get("done_n") or 0),
                    int(g.get("signed_n") or 0),
                )
        elif hub == "review_sign":
            g["badge_label"] = f"{g['attention_n']} 项待办" if g["attention_n"] else "暂无待办"
        else:
            g["badge_label"] = f"{g['leaf_count']} 份报告"
        # 仅当组内含深链委托时展开该组，否则默认收起
        g["open"] = any(c.get("open") for c in g["commissions"])

    focus_project = next((c for c in commission_nodes if c["project_id"] == focus_project_id), None)

    return {
        "view_mode": view_mode,
        "project_search": search,
        "tree_groups": groups,
        "focus_project": focus_project,
        "project_id": focus_project_id,
        "task_no": task_no,
        "has_tree": bool(groups),
        "hub_name": hub,
        "tab": (tab or "").strip().lower(),
    }


def build_site_record_hub(
    user: User,
    *,
    project_id: int = 0,
    task_no: str = "",
    search: str = "",
    view: str = VIEW_HOSPITAL,
) -> dict[str, Any]:
    tree = build_hub_browse_tree(
        user,
        hub="site_records",
        view=view,
        search=search,
        focus_project_id=project_id,
        task_no=task_no,
    )
    tree["file_library_site_url"] = reverse("file_library") + "?" + urlencode(
        {"tab": "site_record", **({"project": str(project_id)} if project_id else {})}
    )
    return tree


def build_report_generate_hub(
    user: User,
    *,
    project_id: int = 0,
    task_no: str = "",
    tab: str = "",
    search: str = "",
    view: str = VIEW_HOSPITAL,
) -> dict[str, Any]:
    tab_norm = (tab or "").strip().lower()
    if tab_norm not in ("generate", "preview"):
        tab_norm = "generate"
    tree = build_hub_browse_tree(
        user,
        hub="report_generate",
        view=view,
        search=search,
        focus_project_id=project_id,
        task_no=task_no,
        tab=tab_norm,
    )
    tree["tab"] = tab_norm
    tree["can_export_report"] = library_user_may_export_report_library_pdfs(user)
    tree["can_merge_reports"] = library_user_may_export_report_library_pdfs(user)
    return tree


def build_review_sign_hub(
    user: User,
    *,
    project_id: int = 0,
    task_no: str = "",
    tab: str = "",
    search: str = "",
    view: str = VIEW_HOSPITAL,
) -> dict[str, Any]:
    tab_norm = (tab or "").strip().lower()
    if tab_norm not in ("audit", "sign", "all"):
        tab_norm = "all"
    tree = build_hub_browse_tree(
        user,
        hub="review_sign",
        view=view,
        search=search,
        focus_project_id=project_id,
        task_no=task_no,
        tab=tab_norm,
    )
    tree["tab"] = tab_norm
    return tree


def build_report_download_hub(
    user: User,
    *,
    project_id: int = 0,
    task_no: str = "",
    search: str = "",
    view: str = VIEW_HOSPITAL,
) -> dict[str, Any]:
    tree = build_hub_browse_tree(
        user,
        hub="report_download",
        view=view,
        search=search,
        focus_project_id=project_id,
        task_no=task_no,
    )
    tree["file_library_report_url"] = reverse("file_library") + "?" + urlencode(
        {"tab": "report", **({"project": str(project_id)} if project_id else {})}
    )
    return tree
