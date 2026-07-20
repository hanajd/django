"""报告流程枢纽：按医院/时间 → 委托 → 设备 三级浏览。"""
from __future__ import annotations

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
from apps.core.library_access import library_file_access_allowed, role_has
from apps.core.models import (
    InspectionCaseWorkflowState,
    LibraryFile,
    LibraryFileProject,
    LibraryProject,
)


VIEW_HOSPITAL = "hospital"
VIEW_TIME = "time"
VIEW_MODES = (VIEW_HOSPITAL, VIEW_TIME)


def workflow_hub_nav_allowed(user: User) -> bool:
    return bool(getattr(user, "is_authenticated", False) and role_has(user, "perm_file_library"))


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


def _hospital_meta(project: LibraryProject) -> tuple[str, str]:
    org = getattr(project, "commission_org", None)
    if org is None:
        return "no_hospital", "未关联医院"
    try:
        root = org.hospital_root()
    except Exception:
        root = org
    name = (getattr(root, "name", None) or "").strip() or "未命名医院"
    return f"h{root.pk}", name


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


def _file_rows(
    user: User,
    *,
    category: str,
    project_ids: list[int],
    limit: int = 200,
) -> list[dict[str, Any]]:
    if not project_ids:
        return []
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
        )
        .distinct()
        .order_by("-created_at")[:limit]
    )
    rows: list[dict[str, Any]] = []
    for lf in qs:
        if not library_file_access_allowed(user, lf):
            continue
        projects = list(lf.projects.all()[:5])
        proj = None
        for p in projects:
            if p.pk in project_ids:
                proj = p
                break
        if proj is None and projects:
            proj = projects[0]
        tab = "site_record" if category == LibraryFile.CATEGORY_SITE_RECORD else "report"
        rows.append(
            {
                "kind": "file",
                "file": lf,
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
        )
    return rows


def _match_equipment_for_file(name: str, equipment_titles: list[str]) -> tuple[str, str]:
    low = (name or "").lower()
    for title in equipment_titles:
        t = (title or "").strip()
        if not t or t == "—":
            continue
        # 用设备名首段（如 CT）匹配文件名
        head = t.split("·")[0].strip()
        if head and head.lower() in low:
            return t, t
        if t.lower() in low:
            return t, t
    return "其他", "其他"


def _flatten_progress(
    user: User, projects: list[LibraryProject]
) -> dict[int, list[dict[str, Any]]]:
    if not projects:
        return {}
    by_pid = _bulk_project_item_progress(projects, viewer=user, include_large=True)
    out: dict[int, list[dict[str, Any]]] = {}
    for p in projects:
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
            rows.append(row)
        out[p.pk] = rows
    return out


def _leaf_attention(hub: str, leaf: dict) -> bool:
    if hub == "report_generate":
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
    if hub == "report_generate":
        return [r for r in rows if _leaf_attention("report_generate", r)]
    if hub == "review_sign":
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
                r = dict(r)
                r["hub_section"] = "audit"
                audit.append(r)
            if is_sign:
                r2 = dict(r)
                r2["hub_section"] = "sign"
                sign.append(r2)
        tab_n = (tab or "all").lower()
        if tab_n == "audit":
            return audit
        if tab_n == "sign":
            return sign
        return audit + sign
    return rows


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


def _split_equipment_file_versions(equipments: list[dict[str, Any]]) -> None:
    """
    每个设备下：文件类叶子只保留最新一份在 leaves，其余进 history_leaves。
    进度类叶子保持原样与最新文件并列展示。
    """
    for eq in equipments:
        leaves = list(eq.get("leaves") or [])
        files = [l for l in leaves if l.get("kind") == "file"]
        others = [l for l in leaves if l.get("kind") != "file"]
        files.sort(
            key=lambda x: (
                x.get("created_at").timestamp() if x.get("created_at") else 0.0
            ),
            reverse=True,
        )
        history: list[dict[str, Any]] = []
        primary_files: list[dict[str, Any]] = []
        if files:
            latest = dict(files[0])
            latest["is_latest"] = True
            primary_files.append(latest)
            for older in files[1:]:
                row = dict(older)
                row["is_latest"] = False
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
    """编制页：generate 仅进度，preview 仅报告文件，all 两者都要。"""
    t = (tab or "generate").strip().lower()
    if t == "preview":
        return [], files
    if t == "generate":
        return prog, []
    return prog, files


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
        _file_rows(user, category=LibraryFile.CATEGORY_SITE_RECORD, project_ids=pids)
        if hub == "site_records"
        else []
    )
    report_files = (
        _file_rows(user, category=LibraryFile.CATEGORY_REPORT, project_ids=pids)
        if hub in ("report_generate", "report_download")
        else []
    )

    files_by_pid: dict[int, list[dict]] = defaultdict(list)
    for r in site_files + report_files:
        files_by_pid[int(r.get("project_id") or 0)].append(r)

    # 为文件匹配设备名
    for p in projects:
        titles = [
            (row.get("equipment_title") or "").strip()
            for row in progress_map.get(p.pk) or []
            if (row.get("equipment_title") or "").strip()
        ]
        titles = list(dict.fromkeys(titles))
        for fr in files_by_pid.get(p.pk) or []:
            key, title = _match_equipment_for_file(fr["file"].original_name, titles)
            fr["equipment_key"] = key
            fr["equipment_title"] = title

    # 组装每个委托的设备叶子
    commission_nodes: list[dict[str, Any]] = []
    for p in projects:
        hospital_key, hospital_name = _hospital_meta(p)
        prog = progress_map.get(p.pk) or []
        if task_no:
            prog = [r for r in prog if str(r.get("task_no") or "") == task_no]
        prog = _filter_progress_for_hub(hub, prog, tab=tab)

        files = files_by_pid.get(p.pk) or []
        if hub == "site_records":
            files = [f for f in files if f.get("file") and f["file"].category == LibraryFile.CATEGORY_SITE_RECORD]
        elif hub == "report_download":
            files = [f for f in files if f.get("file") and f["file"].category == LibraryFile.CATEGORY_REPORT]
        elif hub == "report_generate":
            files = [f for f in files if f.get("file") and f["file"].category == LibraryFile.CATEGORY_REPORT]
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

        if hub in ("report_generate", "review_sign"):
            for row in prog:
                eq = _equip(row.get("equipment_title") or "其他")
                eq["leaves"].append(row)
                if _leaf_attention(hub, row):
                    eq["attention"] = True
            if hub == "report_generate":
                for fr in files:
                    eq = _equip(fr.get("equipment_title") or "其他")
                    eq["leaves"].append(fr)
        else:
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
        attention_n = 0
        rg_tab = (tab or "generate").strip().lower()
        if hub == "report_generate":
            attention_n = sum(
                1
                for e in equipments
                for leaf in e["leaves"]
                if leaf.get("kind") == "progress" and _leaf_attention(hub, leaf)
            )
        elif hub == "review_sign":
            attention_n = sum(1 for e in equipments for leaf in e["leaves"] if _leaf_attention(hub, leaf))
        else:
            attention_n = leaf_count

        if hub == "site_records":
            badge_label = f"{leaf_count} 份记录"
        elif hub == "report_generate":
            if rg_tab == "preview":
                badge_label = f"{leaf_count} 份报告" if leaf_count else "暂无报告"
            else:
                badge_label = f"{attention_n} 项待编制" if attention_n else "暂无待编制"
        elif hub == "review_sign":
            badge_label = f"{attention_n} 项待办" if attention_n else "暂无待办"
        else:
            badge_label = f"{leaf_count} 份报告"

        # 搜索过滤
        if needle:
            hit = _search_hit(needle, p.code or "", p.name or "", hospital_name)
            if not hit:
                # 设备/文件名命中则保留匹配叶子（含历史版本）
                kept = []
                for eq in equipments:
                    if _search_hit(needle, eq["equipment_title"]):
                        kept.append(eq)
                        continue

                    def _leaf_name(leaf: dict) -> str:
                        if leaf.get("kind") == "file":
                            return leaf["file"].original_name
                        return (
                            f"{leaf.get('equipment_title', '')} "
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
                    attention_n = sum(
                        1
                        for e in equipments
                        for leaf in e["leaves"]
                        if leaf.get("kind") == "progress" and _leaf_attention(hub, leaf)
                    )
                    if rg_tab == "preview":
                        badge_label = f"{leaf_count} 份报告" if leaf_count else "暂无报告"
                    else:
                        badge_label = f"{attention_n} 项待编制" if attention_n else "暂无待编制"
                elif hub == "review_sign":
                    attention_n = sum(
                        1 for e in equipments for leaf in e["leaves"] if _leaf_attention(hub, leaf)
                    )
                    badge_label = f"{attention_n} 项待办" if attention_n else "暂无待办"
                else:
                    attention_n = leaf_count
                    badge_label = (
                        f"{leaf_count} 份记录" if hub == "site_records" else f"{leaf_count} 份报告"
                    )

        if leaf_count == 0 and not (focus_project_id and focus_project_id == p.pk):
            continue

        if hub in ("report_generate", "review_sign") and not (focus_project_id and focus_project_id == p.pk):
            # 无待办委托在待办页可隐藏；若深链指定项目仍显示
            if hub == "report_generate" and rg_tab != "preview" and attention_n == 0:
                continue
            if hub == "review_sign" and attention_n == 0:
                continue

        ts = _commission_ts(p, progress_map.get(p.pk) or [], files_by_pid.get(p.pk) or [])
        bucket_key, bucket_label, bucket_rank = _time_bucket(ts)

        # 仅深链指定委托时展开对应路径，其余一律默认收起
        open_commission = bool(focus_project_id and focus_project_id == p.pk)
        for eq in equipments:
            eq["open"] = False

        commission_nodes.append(
            {
                "project_id": p.pk,
                "code": (p.code or "").strip() or f"#{p.pk}",
                "name": (p.name or "").strip() or "未命名委托",
                "hospital_key": hospital_key,
                "hospital_name": hospital_name,
                "time_key": bucket_key,
                "time_label": bucket_label,
                "time_rank": bucket_rank,
                "updated_at": ts,
                "equipments": equipments,
                "leaf_count": leaf_count,
                "attention": attention_n > 0 if hub != "report_generate" or rg_tab != "preview" else leaf_count > 0,
                "attention_n": attention_n if (hub != "report_generate" or rg_tab != "preview") else leaf_count,
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
                    "leaf_count": 0,
                },
            )
            g["commissions"].append(c)
            g["attention_n"] += c["attention_n"]
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
                    "commissions": [],
                    "attention_n": 0,
                    "leaf_count": 0,
                },
            )
            g["commissions"].append(c)
            g["attention_n"] += c["attention_n"]
            g["leaf_count"] += c["leaf_count"]
        groups = sorted(
            groups_map.values(),
            key=lambda g: (g["attention_n"] == 0, g["title"]),
        )

    for g in groups:
        if view_mode == VIEW_TIME:
            g["commissions"].sort(
                key=lambda c: (
                    not c["attention"],
                    -(c["updated_at"].timestamp() if c.get("updated_at") else 0),
                    c["code"],
                )
            )
        else:
            g["commissions"].sort(key=lambda c: (not c["attention"], c["code"]))
        g["attention"] = g["attention_n"] > 0
        if hub == "site_records":
            g["badge_label"] = f"{g['leaf_count']} 份记录"
        elif hub == "report_generate":
            rg_tab = (tab or "generate").strip().lower()
            if rg_tab == "preview":
                g["badge_label"] = f"{g['leaf_count']} 份报告" if g["leaf_count"] else "暂无报告"
            else:
                g["badge_label"] = f"{g['attention_n']} 项待编制" if g["attention_n"] else "暂无待编制"
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
