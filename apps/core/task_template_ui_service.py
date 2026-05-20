"""任务模板库与模板编辑器共用的 UI 辅助（编辑模式 URL、文件分组、层级目录）。"""
from __future__ import annotations

from urllib.parse import quote

from django.urls import reverse

from apps.core.library_folder_service import TreeNode, _norm_path, _parse_segments
from apps.core.library_task_folder_service import (
    UNCATEGORIZED_KEY,
    UNCATEGORIZED_LABEL,
    build_task_category_explorer,
    fl_path_for_task,
    index_task_folders,
    report_tasks_for_folder,
    site_tasks_for_report,
    task_folders_active_queryset,
)
from apps.core.models import LibraryFile, LibraryTask, LibraryTaskFolder


def task_library_edit_mode(request) -> bool:
    v = (request.GET.get("edit") or request.POST.get("edit") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def task_library_page_url(
    fl_path: str = "",
    *,
    manage_task_id: int | None = None,
    edit: bool = False,
    return_project: str = "",
    export_project_id: str = "",
    new_report: bool = False,
    new_site: bool = False,
) -> str:
    base = reverse("library_task_management")
    parts: list[str] = []
    if manage_task_id:
        parts.append(f"manage_task={manage_task_id}")
    if fl_path:
        parts.append(f"fl_path={quote(fl_path)}")
    if return_project:
        parts.append(f"return_project={quote(str(return_project))}")
    if export_project_id:
        parts.append(f"export_project_id={quote(str(export_project_id))}")
    if edit:
        parts.append("edit=1")
    if new_report:
        parts.append("new_report=1")
    if new_site:
        parts.append("new_site=1")
    if not parts:
        return base
    return base + "?" + "&".join(parts)


def htmlpdf_editor_page_url(
    *,
    manage_task_id: int | None = None,
    fl_path: str = "",
) -> str:
    base = reverse("htmlpdf_editor")
    parts: list[str] = []
    if manage_task_id:
        parts.append(f"manage_task={manage_task_id}")
    if fl_path:
        parts.append(f"fl_path={quote(fl_path)}")
    if not parts:
        return base
    return base + "?" + "&".join(parts)


def htmlpdf_editor_open_url(
    pdf_id: int,
    *,
    manage_task_id: int | None = None,
    fl_path: str = "",
) -> str:
    base = reverse("htmlpdf_editor_open", kwargs={"pk": pdf_id})
    parts: list[str] = []
    if manage_task_id:
        parts.append(f"return_manage_task={manage_task_id}")
    if fl_path:
        parts.append(f"return_fl_path={quote(fl_path)}")
    if not parts:
        return base
    return base + "?" + "&".join(parts)


def group_template_files_for_bind(files: list) -> dict[str, list]:
    """将模板分类文件按 PDF / JSON / 其它分组，便于绑定面板展示。"""
    grouped: dict[str, list] = {"pdf": [], "json": [], "other": []}
    for f in files:
        name = (getattr(f, "original_name", None) or "").lower()
        if name.endswith(".pdf"):
            grouped["pdf"].append(f)
        elif name.endswith(".json"):
            grouped["json"].append(f)
        else:
            grouped["other"].append(f)
    return grouped


def _template_pdfs_for_task(
    task: LibraryTask,
    *,
    file_allowed,
) -> list[dict]:
    rows: list[dict] = []
    for lf in task.library_files.filter(category=LibraryFile.CATEGORY_TEMPLATE).order_by(
        "original_name"
    ):
        if not file_allowed(lf):
            continue
        if not (lf.original_name or "").lower().endswith(".pdf"):
            continue
        rows.append(
            {
                "id": lf.pk,
                "name": lf.original_name,
                "editor_url": htmlpdf_editor_open_url(
                    lf.pk,
                    manage_task_id=task.pk,
                    fl_path="",  # filled by caller if needed
                ),
            }
        )
    return rows


def _htmlpdf_task_detail(
    task: LibraryTask,
    task_by_id: dict[int, LibraryTask],
    folder_by_id: dict[int, LibraryTaskFolder],
    *,
    has_report_source_relation: bool,
    file_allowed,
) -> dict:
    fl = fl_path_for_task(
        task,
        task_by_id,
        folder_by_id,
        has_report_source_relation=has_report_source_relation,
    )
    pdfs = _template_pdfs_for_task(task, file_allowed=file_allowed)
    for p in pdfs:
        p["editor_url"] = htmlpdf_editor_open_url(
            p["id"], manage_task_id=task.pk, fl_path=fl
        )
    return {
        "task_id": task.pk,
        "task_code": task.code,
        "task_name": task.name,
        "output_label": task.get_output_target_display(),
        "output_target": task.output_target,
        "fl_path": fl,
        "manage_url": task_library_page_url(fl, manage_task_id=task.pk),
        "template_pdfs": pdfs,
    }


def _clear_tree_link_suffix(nodes: list[TreeNode]) -> None:
    for n in nodes:
        n.link_suffix = ""
        if n.children:
            _clear_tree_link_suffix(n.children)


def _fl_path_is_site_leaf(fl_path: str) -> bool:
    segs = _parse_segments(_norm_path(fl_path))
    return bool(segs) and segs[-1][0] == "s"


def _node_pdf_count(
    node: TreeNode,
    task_by_id: dict[int, LibraryTask],
    file_allowed,
) -> int:
    if node.node_type == "report":
        own = 0
        try:
            tid = int(node.id[2:])
        except ValueError:
            tid = 0
        task = task_by_id.get(tid) if tid else None
        if task:
            own = len(_template_pdfs_for_task(task, file_allowed=file_allowed))
        child_pdfs = (
            sum(_node_pdf_count(c, task_by_id, file_allowed) for c in (node.children or []))
            if node.children
            else 0
        )
        return own + child_pdfs
    if node.node_type == "site":
        try:
            tid = int(node.id[2:])
        except ValueError:
            return 0
        task = task_by_id.get(tid)
        return len(_template_pdfs_for_task(task, file_allowed=file_allowed)) if task else 0
    if node.children:
        return sum(_node_pdf_count(c, task_by_id, file_allowed) for c in node.children)
    return 0


def _annotate_tree_pdf_counts(
    nodes: list[TreeNode],
    task_by_id: dict[int, LibraryTask],
    file_allowed,
) -> None:
    for n in nodes:
        if n.children:
            _annotate_tree_pdf_counts(n.children, task_by_id, file_allowed)
        n.count = _node_pdf_count(n, task_by_id, file_allowed)


def build_htmlpdf_explorer_context(
    tasks: list[LibraryTask],
    *,
    fl_path: str = "",
    manage_task_id: int | None = None,
    has_report_source_relation: bool,
    file_allowed,
) -> dict:
    """文件夹式模板编辑器：左侧树 + 当前路径面包屑/子文件夹 + 选中任务的 PDF 列表。"""
    folder_rows = list(task_folders_active_queryset())
    folder_by_id, _ = index_task_folders(folder_rows)
    task_by_id = {t.pk: t for t in tasks}

    if manage_task_id and manage_task_id in task_by_id and not fl_path:
        fl_path = fl_path_for_task(
            task_by_id[manage_task_id],
            task_by_id,
            folder_by_id,
            has_report_source_relation=has_report_source_relation,
        )

    tree, crumbs, entries, resolved_id = build_task_category_explorer(
        tasks,
        folder_rows,
        fl_path=fl_path,
        manage_task_id=manage_task_id,
        has_report_source_relation=has_report_source_relation,
    )
    _clear_tree_link_suffix(tree)
    _annotate_tree_pdf_counts(tree, task_by_id, file_allowed)

    active_task_id = manage_task_id or resolved_id
    selected_task = None
    selected_report = None
    report_site_records: list[dict] = []
    if active_task_id and active_task_id in task_by_id:
        t = task_by_id[active_task_id]
        if t.output_target == LibraryTask.OUTPUT_SITE_RECORD:
            selected_task = _htmlpdf_task_detail(
                t,
                task_by_id,
                folder_by_id,
                has_report_source_relation=has_report_source_relation,
                file_allowed=file_allowed,
            )
        elif t.output_target == LibraryTask.OUTPUT_REPORT and not _fl_path_is_site_leaf(fl_path):
            selected_report = _htmlpdf_task_detail(
                t,
                task_by_id,
                folder_by_id,
                has_report_source_relation=has_report_source_relation,
                file_allowed=file_allowed,
            )
            if has_report_source_relation:
                for st in site_tasks_for_report(
                    t, task_by_id, has_report_source_relation=True
                ):
                    report_site_records.append(
                        _htmlpdf_task_detail(
                            st,
                            task_by_id,
                            folder_by_id,
                            has_report_source_relation=has_report_source_relation,
                            file_allowed=file_allowed,
                        )
                    )
            entries = []

    return {
        "folder_tree_nodes": tree,
        "folder_breadcrumbs": crumbs,
        "folder_entries": entries,
        "fl_path": fl_path,
        "selected_task": selected_task,
        "selected_report": selected_report,
        "report_site_records": report_site_records,
        "active_task_id": active_task_id,
    }


def build_htmlpdf_task_catalog(
    tasks: list[LibraryTask],
    *,
    has_report_source_relation: bool,
    file_allowed,
    filter_manage_task_id: int | None = None,
) -> list[dict]:
    """
    按任务模板库层级（分类 → 报告 → 现场记录）列出可编辑的模板 PDF。
    返回 folder_sections，每项含 reports；report 含 template_pdfs 与 site_records。
    """
    folder_rows = list(task_folders_active_queryset())
    folder_by_id, children_by_parent = index_task_folders(folder_rows)
    task_by_id = {t.pk: t for t in tasks}

    def _leaf(task: LibraryTask, parent_fl: str) -> dict:
        fl = fl_path_for_task(
            task,
            task_by_id,
            folder_by_id,
            has_report_source_relation=has_report_source_relation,
        )
        pdfs = _template_pdfs_for_task(task, file_allowed=file_allowed)
        for p in pdfs:
            p["editor_url"] = htmlpdf_editor_open_url(
                p["id"], manage_task_id=task.pk, fl_path=fl
            )
        return {
            "task_id": task.pk,
            "task_code": task.code,
            "task_name": task.name,
            "output_label": task.get_output_target_display(),
            "fl_path": fl,
            "manage_url": task_library_page_url(fl, manage_task_id=task.pk),
            "template_pdfs": pdfs,
        }

    def _report_block(report: LibraryTask) -> dict:
        sites = []
        if has_report_source_relation:
            for st in site_tasks_for_report(
                report, task_by_id, has_report_source_relation=True
            ):
                sites.append(_leaf(st, ""))
        block = _leaf(report, "")
        block["site_records"] = sites
        block["is_report"] = True
        return block

    def _folder_section(folder: LibraryTaskFolder | None, folder_path: str, label: str) -> dict:
        fid = folder.pk if folder else None
        reports = [_report_block(r) for r in report_tasks_for_folder(tasks, fid)]
        return {
            "folder_id": fid,
            "folder_name": label,
            "folder_path": folder_path,
            "reports": reports,
            "report_count": len(reports),
        }

    sections: list[dict] = []

    def _walk_folders(parent_id: int | None, path_prefix: str) -> None:
        for f in children_by_parent.get(parent_id, []):
            seg = f.folder_segment()
            fpath = f"{path_prefix}/{seg}" if path_prefix else seg
            sec = _folder_section(f, fpath, f.name)
            if filter_manage_task_id is None or _section_matches_task(sec, filter_manage_task_id):
                sections.append(sec)
            _walk_folders(f.pk, fpath)

    _walk_folders(None, "")
    unc = _folder_section(None, f"f-{UNCATEGORIZED_KEY}", UNCATEGORIZED_LABEL)
    if unc["report_count"]:
        if filter_manage_task_id is None or _section_matches_task(unc, filter_manage_task_id):
            sections.append(unc)

    return sections


def _section_matches_task(section: dict, task_id: int) -> bool:
    for rep in section.get("reports") or []:
        if rep.get("task_id") == task_id:
            return True
        for site in rep.get("site_records") or []:
            if site.get("task_id") == task_id:
                return True
    return False
