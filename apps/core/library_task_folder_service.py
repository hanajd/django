"""任务模板库：任务分类（文件夹）→ 报告 → 现场记录 三级导航。"""
from __future__ import annotations

from apps.core.library_folder_service import (
    BreadcrumbItem,
    FolderEntry,
    TreeNode,
    _annotate_forest_expanded,
    _norm_path,
    _parse_segments,
)
from apps.core.models import LibraryTask, LibraryTaskFolder

UNCATEGORIZED_KEY = "0"
UNCATEGORIZED_LABEL = "未分类报告"


def task_folders_active_queryset():
    return LibraryTaskFolder.objects.filter(is_active=True).select_related("parent")


def index_task_folders(rows: list[LibraryTaskFolder]) -> tuple[
    dict[int, LibraryTaskFolder],
    dict[int | None, list[LibraryTaskFolder]],
]:
    by_id = {f.pk: f for f in rows}
    children: dict[int | None, list[LibraryTaskFolder]] = {}
    for f in rows:
        children.setdefault(f.parent_id, []).append(f)
    for lst in children.values():
        lst.sort(key=lambda x: (x.sort_order, x.name or "", x.pk))
    return by_id, children


def folder_from_path_segment(val: str, by_id: dict[int, LibraryTaskFolder]) -> LibraryTaskFolder | None:
    if val == UNCATEGORIZED_KEY:
        return None
    try:
        fid = int(val)
    except ValueError:
        return None
    return by_id.get(fid)


def report_tasks_for_folder(
    tasks: list[LibraryTask],
    folder_id: int | None,
) -> list[LibraryTask]:
    out: list[LibraryTask] = []
    for t in tasks:
        if t.output_target != LibraryTask.OUTPUT_REPORT:
            continue
        tid = getattr(t, "task_folder_id", None)
        if folder_id is None:
            if tid is None:
                out.append(t)
        elif tid == folder_id:
            out.append(t)
    out.sort(key=lambda x: (x.code or "", x.name or ""))
    return out


def site_tasks_for_report(
    report: LibraryTask,
    task_by_id: dict[int, LibraryTask],
    *,
    has_report_source_relation: bool,
) -> list[LibraryTask]:
    if not has_report_source_relation:
        return []
    return list(
        report.report_source_tasks.filter(output_target=LibraryTask.OUTPUT_SITE_RECORD).order_by(
            "code", "id"
        )
    )


def _folder_tree_nodes(
    folder: LibraryTaskFolder,
    children_by_parent: dict[int | None, list[LibraryTaskFolder]],
    report_by_folder: dict[int | None, list[LibraryTask]],
    task_by_id: dict[int, LibraryTask],
    *,
    manage_task_id: int | None,
    has_report_source_relation: bool,
    base_path: str,
) -> TreeNode:
    fpath = f"{base_path}/{folder.folder_segment()}" if base_path else folder.folder_segment()
    subfolders = children_by_parent.get(folder.pk, [])
    reports = report_by_folder.get(folder.pk, [])
    child_nodes: list[TreeNode] = []
    for sf in subfolders:
        child_nodes.append(
            _folder_tree_nodes(
                sf,
                children_by_parent,
                report_by_folder,
                task_by_id,
                manage_task_id=manage_task_id,
                has_report_source_relation=has_report_source_relation,
                base_path=fpath,
            )
        )
    for rt in reports:
        sources = site_tasks_for_report(
            rt, task_by_id, has_report_source_relation=has_report_source_relation
        )
        src_nodes = [
            TreeNode(
                id=f"s-{st.pk}",
                label=st.name,
                path=f"{fpath}/r-{rt.pk}/s-{st.pk}",
                node_type="site",
                is_active=manage_task_id == st.pk,
                link_suffix=f"&manage_task={st.pk}",
            )
            for st in sources
        ]
        child_nodes.append(
            TreeNode(
                id=f"r-{rt.pk}",
                label=rt.name,
                path=f"{fpath}/r-{rt.pk}",
                node_type="report",
                count=len(sources),
                children=src_nodes,
                is_active=manage_task_id == rt.pk,
                link_suffix=f"&manage_task={rt.pk}",
                drag_type="report",
                drag_id=rt.pk,
            )
        )
    return TreeNode(
        id=folder.folder_segment(),
        label=folder.name,
        path=fpath,
        node_type="folder",
        count=len(subfolders) + len(reports),
        children=child_nodes,
        drop_accepts="report",
        drop_id=folder.pk,
    )


def build_task_category_explorer(
    tasks: list,
    folders: list[LibraryTaskFolder],
    *,
    fl_path: str,
    manage_task_id: int | None,
    has_report_source_relation: bool = False,
) -> tuple[list[TreeNode], list[BreadcrumbItem], list[FolderEntry], int | None]:
    """任务 > 报告 > 现场记录；fl_path 形如 f-1/f-2/r-3/s-4 或 f-0（未分类）。"""
    path = _norm_path(fl_path)
    segs = _parse_segments(path)
    task_by_id = {t.pk: t for t in tasks}
    folder_by_id, children_by_parent = index_task_folders(folders)

    report_by_folder: dict[int | None, list[LibraryTask]] = {}
    for t in tasks:
        if t.output_target != LibraryTask.OUTPUT_REPORT:
            continue
        report_by_folder.setdefault(getattr(t, "task_folder_id", None), []).append(t)
    for lst in report_by_folder.values():
        lst.sort(key=lambda x: (x.code or "", x.name or ""))

    tree: list[TreeNode] = []
    for f in children_by_parent.get(None, []):
        tree.append(
            _folder_tree_nodes(
                f,
                children_by_parent,
                report_by_folder,
                task_by_id,
                manage_task_id=manage_task_id,
                has_report_source_relation=has_report_source_relation,
                base_path="",
            )
        )
    uncategorized = report_by_folder.get(None, [])
    if uncategorized:
        unc_nodes = []
        for rt in uncategorized:
            sources = site_tasks_for_report(
                rt, task_by_id, has_report_source_relation=has_report_source_relation
            )
            src_nodes = [
                TreeNode(
                    id=f"s-{st.pk}",
                    label=st.name,
                    path=f"f-{UNCATEGORIZED_KEY}/r-{rt.pk}/s-{st.pk}",
                    node_type="site",
                    is_active=manage_task_id == st.pk,
                    link_suffix=f"&manage_task={st.pk}",
                )
                for st in sources
            ]
            unc_nodes.append(
                TreeNode(
                    id=f"r-{rt.pk}",
                    label=rt.name,
                    path=f"f-{UNCATEGORIZED_KEY}/r-{rt.pk}",
                    node_type="report",
                    count=len(sources),
                    children=src_nodes,
                    is_active=manage_task_id == rt.pk,
                    link_suffix=f"&manage_task={rt.pk}",
                    drag_type="report",
                    drag_id=rt.pk,
                )
            )
        tree.append(
            TreeNode(
                id=f"f-{UNCATEGORIZED_KEY}",
                label=UNCATEGORIZED_LABEL,
                path=f"f-{UNCATEGORIZED_KEY}",
                node_type="folder",
                count=len(uncategorized),
                children=unc_nodes,
                drop_accepts="report",
                drop_id=int(UNCATEGORIZED_KEY),
            )
        )

    crumbs = [BreadcrumbItem("任务模板", "")]
    folders_panel: list[FolderEntry] = []
    resolved_task_id: int | None = None

    if not segs:
        for f in children_by_parent.get(None, []):
            n_reports = len(report_by_folder.get(f.pk, []))
            n_children = len(children_by_parent.get(f.pk, []))
            folders_panel.append(
                FolderEntry(
                    kind="folder",
                    label=f.name,
                    path=f.folder_segment(),
                    meta=f"{n_reports} 个报告" + (f" · {n_children} 个子分类" if n_children else ""),
                    count=n_reports + n_children,
                    drop_accepts="report",
                    entity_pk=f.pk,
                )
            )
        if uncategorized:
            folders_panel.append(
                FolderEntry(
                    kind="folder",
                    label=UNCATEGORIZED_LABEL,
                    path=f"f-{UNCATEGORIZED_KEY}",
                    meta=f"{len(uncategorized)} 个报告",
                    count=len(uncategorized),
                    drop_accepts="report",
                    entity_pk=int(UNCATEGORIZED_KEY),
                )
            )
        _annotate_forest_expanded(tree, fl_path)
        return tree, crumbs, folders_panel, None

    folder_path_parts: list[str] = []
    i = 0
    current_folder_id: int | None = None
    while i < len(segs) and segs[i][0] == "f":
        folder_path_parts.append(f"f-{segs[i][1]}")
        if segs[i][1] == UNCATEGORIZED_KEY:
            current_folder_id = None
            crumbs.append(BreadcrumbItem(UNCATEGORIZED_LABEL, "/".join(folder_path_parts)))
        else:
            try:
                current_folder_id = int(segs[i][1])
            except ValueError:
                current_folder_id = None
            fo = folder_by_id.get(current_folder_id) if current_folder_id else None
            crumbs.append(
                BreadcrumbItem(
                    fo.name if fo else "分类",
                    "/".join(folder_path_parts),
                )
            )
        i += 1

    if i < len(segs) and segs[i][0] == "r":
        try:
            rid = int(segs[i][1])
        except ValueError:
            rid = None
        report = task_by_id.get(rid) if rid else None
        if report is not None:
            base = "/".join(folder_path_parts)
            rpath = f"{base}/r-{report.pk}" if base else f"r-{report.pk}"
            crumbs.append(BreadcrumbItem(report.name, rpath))
            i += 1
            if i >= len(segs):
                resolved_task_id = report.pk
                for st in site_tasks_for_report(
                    report, task_by_id, has_report_source_relation=has_report_source_relation
                ):
                    folders_panel.append(
                        FolderEntry(
                            kind="folder",
                            label=st.name,
                            path=f"{rpath}/s-{st.pk}",
                            meta=st.code or "现场记录",
                            icon="site",
                            link_suffix=f"&manage_task={st.pk}",
                        )
                    )
                _annotate_forest_expanded(tree, fl_path)
                return tree, crumbs, folders_panel, resolved_task_id

            if i < len(segs) and segs[i][0] == "s":
                try:
                    sid = int(segs[i][1])
                except ValueError:
                    sid = None
                site = task_by_id.get(sid) if sid else None
                if site is not None:
                    crumbs.append(BreadcrumbItem(site.name, _norm_path(fl_path)))
                resolved_task_id = sid
                _annotate_forest_expanded(tree, fl_path)
                return tree, crumbs, folders_panel, resolved_task_id

    if i == len([s for s in segs if s[0] == "f"]) and folder_path_parts:
        base = "/".join(folder_path_parts)
        if current_folder_id is not None:
            for sf in children_by_parent.get(current_folder_id, []):
                folders_panel.append(
                    FolderEntry(
                        kind="folder",
                        label=sf.name,
                        path=f"{base}/{sf.folder_segment()}",
                        meta="子分类",
                        icon="folder",
                        drop_accepts="report",
                        entity_pk=sf.pk,
                    )
                )
        for rt in report_tasks_for_folder(tasks, current_folder_id):
            sources = site_tasks_for_report(
                rt, task_by_id, has_report_source_relation=has_report_source_relation
            )
            meta = rt.code or ""
            if sources:
                meta = (meta + " · " if meta else "") + f"{len(sources)} 个现场记录"
            folders_panel.append(
                FolderEntry(
                    kind="folder",
                    label=rt.name,
                    path=f"{base}/r-{rt.pk}",
                    meta=meta,
                    count=len(sources),
                    icon="project",
                    link_suffix=f"&manage_task={rt.pk}",
                    drag_type="report",
                    entity_pk=rt.pk,
                )
            )

    _annotate_forest_expanded(tree, fl_path)
    return tree, crumbs, folders_panel, resolved_task_id


def create_task_folder(
    user,
    *,
    name: str,
    parent: LibraryTaskFolder | None = None,
    notes: str = "",
) -> tuple[LibraryTaskFolder | None, str | None]:
    name = (name or "").strip()
    if not name:
        return None, "分类名称不能为空"
    if LibraryTaskFolder.objects.filter(parent=parent, name=name, is_active=True).exists():
        return None, f"同级下已存在「{name}」"
    row = LibraryTaskFolder.objects.create(
        parent=parent,
        name=name,
        notes=(notes or "").strip(),
        created_by=user,
    )
    return row, None


def move_report_to_task_folder(
    report: LibraryTask,
    target_folder_id: int | None,
) -> tuple[LibraryTaskFolder | None, str | None]:
    """将报告模板拖到另一任务分类（target_folder_id=None 表示未分类）。"""
    if report.output_target != LibraryTask.OUTPUT_REPORT:
        return None, "仅报告模板可拖动归类"
    target_folder: LibraryTaskFolder | None = None
    if target_folder_id is not None:
        if target_folder_id == int(UNCATEGORIZED_KEY):
            target_folder = None
        else:
            target_folder = LibraryTaskFolder.objects.filter(
                pk=target_folder_id, is_active=True
            ).first()
            if target_folder is None:
                return None, "目标任务分类不存在"
    if report.task_folder_id == (target_folder.pk if target_folder else None):
        return target_folder, None
    report.task_folder = target_folder
    report.save(update_fields=["task_folder_id", "updated_at"])
    return target_folder, None


def rename_task_folder(folder: LibraryTaskFolder, new_name: str) -> str | None:
    new_name = (new_name or "").strip()
    if not new_name:
        return "分类名称不能为空"
    if (
        LibraryTaskFolder.objects.filter(parent_id=folder.parent_id, name=new_name, is_active=True)
        .exclude(pk=folder.pk)
        .exists()
    ):
        return f"同级下已存在「{new_name}」"
    folder.name = new_name
    folder.save(update_fields=["name", "updated_at"])
    return None


def deactivate_task_folder(folder: LibraryTaskFolder) -> str | None:
    if LibraryTaskFolder.objects.filter(parent=folder, is_active=True).exists():
        return "请先删除或移出其下子分类"
    if LibraryTask.objects.filter(task_folder=folder, output_target=LibraryTask.OUTPUT_REPORT).exists():
        return "该分类下仍有报告模板，请先移出或删除"
    folder.is_active = False
    folder.save(update_fields=["is_active", "updated_at"])
    return None


def fl_path_for_task(
    task: LibraryTask,
    task_by_id: dict[int, LibraryTask],
    folder_by_id: dict[int, LibraryTaskFolder],
    *,
    has_report_source_relation: bool,
) -> str:
    if task.output_target == LibraryTask.OUTPUT_REPORT:
        if task.task_folder_id and task.task_folder_id in folder_by_id:
            return f"{folder_by_id[task.task_folder_id].folder_path()}/r-{task.pk}"
        return f"f-{UNCATEGORIZED_KEY}/r-{task.pk}"
    if task.output_target == LibraryTask.OUTPUT_SITE_RECORD and has_report_source_relation:
        parent = (
            task.report_target_tasks.filter(output_target=LibraryTask.OUTPUT_REPORT)
            .select_related("task_folder")
            .first()
        )
        if parent is not None:
            base = fl_path_for_task(parent, task_by_id, folder_by_id, has_report_source_relation=True)
            return f"{base}/s-{task.pk}"
    return f"r-{task.pk}"
