"""文件库 / 项目工作台：文件夹式路径导航与树结构（非仅图标）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from apps.core.commission_org_service import (
    index_commission_orgs,
    parse_commission_path_segments,
    resolve_org_id_from_segments,
)
from apps.core.models import CommissionOrganization, LibraryProject


@dataclass
class FolderEntry:
    """主面板中的一行：子文件夹或文件占位（文件在模板侧单独列表）。"""

    kind: str  # folder
    label: str
    path: str
    meta: str = ""
    count: int = 0
    icon: str = "folder"
    link_suffix: str = ""
    drag_type: str = ""  # org | project
    entity_pk: int = 0
    org_level: str = ""
    drop_accepts: str = ""  # 逗号分隔：org,project,file


@dataclass
class BreadcrumbItem:
    label: str
    path: str


@dataclass
class TreeNode:
    id: str
    label: str
    path: str
    node_type: str
    count: int = 0
    children: list[TreeNode] = field(default_factory=list)
    is_active: bool = False
    link_suffix: str = ""
    drag_type: str = ""
    drag_id: int = 0
    org_level: str = ""
    drop_accepts: str = ""
    drop_id: int = 0
    expanded: bool = False


def _norm_path(p: str | None) -> str:
    s = (p or "").strip().strip("/")
    if s in ("", "root"):
        return ""
    return s


def _parse_segments(path: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for part in _norm_path(path).split("/"):
        if not part:
            continue
        if "/" in part:
            continue
        if len(part) < 2:
            continue
        key, _, val = part.partition("-")
        if key and val:
            out.append((key, val))
    return out


def _org_node_type(level: str) -> str:
    if level == CommissionOrganization.LEVEL_HOSPITAL:
        return "hospital"
    if level == CommissionOrganization.LEVEL_CAMPUS:
        return "campus"
    if level == CommissionOrganization.LEVEL_DEPARTMENT:
        return "department"
    return "org"


def _annotate_tree_expanded(node: TreeNode, fl_path: str) -> bool:
    """按当前路径展开祖先节点；默认折叠，仅展开通向当前路径的分支。"""
    p = _norm_path(fl_path)
    self_on_path = bool(p and (p == node.path or p.startswith(node.path + "/")))
    child_on_path = False
    for child in node.children:
        if _annotate_tree_expanded(child, fl_path):
            child_on_path = True
    node.expanded = bool(node.children) and (self_on_path or child_on_path)
    return self_on_path or child_on_path


def _annotate_forest_expanded(nodes: list[TreeNode], fl_path: str) -> None:
    for n in nodes:
        _annotate_tree_expanded(n, fl_path)


def _org_folder_drop_accepts(level: str) -> str:
    if level == CommissionOrganization.LEVEL_HOSPITAL:
        return "org,project"
    if level == CommissionOrganization.LEVEL_CAMPUS:
        return "org,project"
    if level == CommissionOrganization.LEVEL_DEPARTMENT:
        return "project"
    return ""


def _org_level_meta(org: CommissionOrganization, child_count: int, project_count: int) -> str:
    bits: list[str] = []
    if child_count:
        if org.level == CommissionOrganization.LEVEL_HOSPITAL:
            bits.append(f"{child_count} 个子文件夹")
        else:
            bits.append(f"{child_count} 个子级")
    if project_count:
        bits.append(f"{project_count} 个项目")
    if not bits:
        return org.level_label
    return " · ".join(bits)


def _build_org_tree_node(
    org: CommissionOrganization,
    *,
    children_by_parent: dict[int | None, list[CommissionOrganization]],
    projects_by_org: dict[int | None, list[LibraryProject]],
    selected_org_id: int | None,
    selected_project_id: int | None,
) -> TreeNode:
    base_path = org.folder_path()
    kids = children_by_parent.get(org.pk, [])
    child_nodes: list[TreeNode] = []
    for child in kids:
        child_nodes.append(
            _build_org_tree_node(
                child,
                children_by_parent=children_by_parent,
                projects_by_org=projects_by_org,
                selected_org_id=selected_org_id,
                selected_project_id=selected_project_id,
            )
        )
    projs = projects_by_org.get(org.pk, [])
    for p in projs:
        child_nodes.append(
            TreeNode(
                id=f"p-{p.pk}",
                label=p.name,
                path=f"{base_path}/p-{p.pk}",
                node_type="project",
                count=0,
                is_active=selected_project_id == p.pk,
                link_suffix=f"&project_id={p.pk}",
                drag_type="project",
                drag_id=p.pk,
            )
        )
    proj_n = len(projs)
    return TreeNode(
        id=org.folder_segment(),
        label=org.name,
        path=base_path,
        node_type=_org_node_type(org.level),
        count=proj_n if not kids else len(kids),
        children=child_nodes,
        is_active=selected_org_id == org.pk and not selected_project_id,
        drag_type="org" if org.level != CommissionOrganization.LEVEL_HOSPITAL else "",
        drag_id=org.pk if org.level != CommissionOrganization.LEVEL_HOSPITAL else 0,
        org_level=org.level,
        drop_accepts=_org_folder_drop_accepts(org.level),
        drop_id=org.pk,
    )


def _folder_entries_for_org_children(
    org: CommissionOrganization | None,
    *,
    children_by_parent: dict[int | None, list[CommissionOrganization]],
    projects_by_org: dict[int | None, list[LibraryProject]],
) -> list[FolderEntry]:
    folders: list[FolderEntry] = []
    parent_id = org.pk if org else None
    for child in children_by_parent.get(parent_id, []):
        if org is None and child.level != CommissionOrganization.LEVEL_HOSPITAL:
            continue
        sub_n = len(children_by_parent.get(child.pk, []))
        proj_n = len(projects_by_org.get(child.pk, []))
        folders.append(
            FolderEntry(
                kind="folder",
                label=child.name,
                path=child.folder_path(),
                meta=_org_level_meta(child, sub_n, proj_n),
                count=sub_n or proj_n,
                icon=_org_node_type(child.level),
                drag_type="org" if child.level != CommissionOrganization.LEVEL_HOSPITAL else "",
                entity_pk=child.pk,
                org_level=child.level,
                drop_accepts=_org_folder_drop_accepts(child.level),
            )
        )
    if org is not None:
        for p in projects_by_org.get(org.pk, []):
            folders.append(
                FolderEntry(
                    kind="folder",
                    label=p.name,
                    path=f"{org.folder_path()}/p-{p.pk}",
                    meta=p.code or "",
                    count=0,
                    icon="project",
                    link_suffix=f"&project_id={p.pk}",
                    drag_type="project",
                    entity_pk=p.pk,
                )
            )
    return folders


def _build_org_only_tree_node(
    org: CommissionOrganization,
    *,
    children_by_parent: dict[int | None, list[CommissionOrganization]],
    project_count_by_org: dict[int | None, int],
) -> TreeNode:
    kids = children_by_parent.get(org.pk, [])
    child_nodes = [
        _build_org_only_tree_node(
            child,
            children_by_parent=children_by_parent,
            project_count_by_org=project_count_by_org,
        )
        for child in kids
    ]
    sub_n = len(kids)
    proj_n = project_count_by_org.get(org.pk, 0)
    return TreeNode(
        id=org.folder_segment(),
        label=org.name,
        path=org.folder_path(),
        node_type=_org_node_type(org.level),
        count=sub_n or proj_n,
        children=child_nodes,
    )


def build_commission_org_nav_tree(
    org_rows: list[CommissionOrganization],
    projects: list,
    *,
    co_path: str,
) -> list[TreeNode]:
    """文件库左侧：医院 → 院区 → 科室（不含项目子节点，项目在中间栏文件夹树中展示）。"""
    org_by_id, children_by_parent, hospitals = index_commission_orgs(org_rows)
    project_count_by_org: dict[int | None, int] = {}
    for p in projects:
        oid = getattr(p, "commission_org_id", None) or None
        project_count_by_org[oid] = project_count_by_org.get(oid, 0) + 1

    roots: list[TreeNode] = []
    for h in hospitals:
        roots.append(
            _build_org_only_tree_node(
                h,
                children_by_parent=children_by_parent,
                project_count_by_org=project_count_by_org,
            )
        )
    unset_n = project_count_by_org.get(None, 0)
    if unset_n:
        roots.append(
            TreeNode(
                id="o-none",
                label="（未归类委托单位）",
                path="o-none",
                node_type="org",
                count=unset_n,
            )
        )

    norm_co = _norm_path(co_path)

    def _mark_active(nodes: list[TreeNode]) -> None:
        for n in nodes:
            n.is_active = bool(norm_co and norm_co == n.path)
            if n.children:
                _mark_active(n.children)

    _mark_active(roots)
    _annotate_forest_expanded(roots, co_path)
    return roots


def build_org_tree_for_workbench(
    org_rows: list[CommissionOrganization],
    projects: list[LibraryProject],
    *,
    selected_org_id: int | None,
    selected_project_id: int | None,
    fl_path: str,
) -> tuple[list[TreeNode], list[BreadcrumbItem], list[FolderEntry], LibraryProject | None, CommissionOrganization | None]:
    """项目工作台：医院 → 院区（可选）→ 科室（可选）→ 项目。"""
    segs = parse_commission_path_segments(fl_path)
    org_by_id, children_by_parent, hospitals = index_commission_orgs(org_rows)
    projects_by_org: dict[int | None, list[LibraryProject]] = {}
    for p in projects:
        oid = getattr(p, "commission_org_id", None) or None
        projects_by_org.setdefault(oid, []).append(p)
    for lst in projects_by_org.values():
        lst.sort(key=lambda x: (x.code or "", x.name or ""))

    roots: list[TreeNode] = []
    for h in hospitals:
        roots.append(
            _build_org_tree_node(
                h,
                children_by_parent=children_by_parent,
                projects_by_org=projects_by_org,
                selected_org_id=selected_org_id,
                selected_project_id=selected_project_id,
            )
        )
    unset = projects_by_org.get(None, [])
    if unset:
        roots.append(
            TreeNode(
                id="o-none",
                label="（未归类委托单位）",
                path="o-none",
                node_type="org",
                count=len(unset),
                children=[
                    TreeNode(
                        id=f"p-{p.pk}",
                        label=p.name,
                        path=f"o-none/p-{p.pk}",
                        node_type="project",
                        is_active=selected_project_id == p.pk,
                        link_suffix=f"&project_id={p.pk}",
                        drag_type="project",
                        drag_id=p.pk,
                    )
                    for p in unset
                ],
            )
        )

    crumbs = [BreadcrumbItem("委托单位", "")]
    folders: list[FolderEntry] = []
    cur_org: CommissionOrganization | None = None
    cur_proj: LibraryProject | None = None

    if not segs:
        for h in hospitals:
            sub_n = len(children_by_parent.get(h.pk, []))
            proj_n = len(projects_by_org.get(h.pk, []))
            folders.append(
                FolderEntry(
                    kind="folder",
                    label=h.name,
                    path=h.folder_path(),
                    meta=_org_level_meta(h, sub_n, proj_n),
                    count=sub_n or proj_n,
                    icon="hospital",
                    entity_pk=h.pk,
                    org_level=CommissionOrganization.LEVEL_HOSPITAL,
                    drop_accepts=_org_folder_drop_accepts(CommissionOrganization.LEVEL_HOSPITAL),
                )
            )
        if unset:
            folders.append(
                FolderEntry(
                    kind="folder",
                    label="（未归类委托单位）",
                    path="o-none",
                    meta=f"{len(unset)} 个项目",
                    count=len(unset),
                )
            )
        _annotate_forest_expanded(roots, fl_path)
        return roots, crumbs, folders, None, None

    if segs[0][0] == "o" and segs[0][1] == "none":
        crumbs.append(BreadcrumbItem("（未归类委托单位）", "o-none"))
        if len(segs) == 1:
            folders = _folder_entries_for_org_children(
                None, children_by_parent=children_by_parent, projects_by_org=projects_by_org
            )
            for p in unset:
                folders.append(
                    FolderEntry(
                        kind="folder",
                        label=p.name,
                        path=f"o-none/p-{p.pk}",
                        meta=p.code,
                        icon="project",
                        link_suffix=f"&project_id={p.pk}",
                        drag_type="project",
                        entity_pk=p.pk,
                    )
                )
            _annotate_forest_expanded(roots, fl_path)
            return roots, crumbs, folders, None, None

    org_path_segs = [s for s in segs if s[0] in ("h", "c", "d", "o") and not (s[0] == "o" and s[1] == "none")]
    proj_segs = [s for s in segs if s[0] == "p"]

    path_so_far = ""
    for key, val in org_path_segs:
        if key == "o":
            oid = int(val) if val.isdigit() else 0
            node = org_by_id.get(oid)
        else:
            oid = int(val) if val.isdigit() else 0
            node = org_by_id.get(oid)
        if node is None:
            break
        cur_org = node
        path_so_far = node.folder_path() if path_so_far == "" else node.folder_path()
        crumbs.append(BreadcrumbItem(node.name, node.folder_path()))

    if cur_org and not proj_segs:
        folders = _folder_entries_for_org_children(
            cur_org,
            children_by_parent=children_by_parent,
            projects_by_org=projects_by_org,
        )
        _annotate_forest_expanded(roots, fl_path)
        return roots, crumbs, folders, None, cur_org

    if proj_segs:
        try:
            pid = int(proj_segs[-1][1])
        except ValueError:
            pid = 0
        cur_proj = next((p for p in projects if p.pk == pid), None)
        if cur_proj:
            if cur_proj.commission_org_id and cur_proj.commission_org_id in org_by_id:
                cur_org = org_by_id[cur_proj.commission_org_id]
            crumbs.append(BreadcrumbItem(cur_proj.name, _norm_path(fl_path)))
        _annotate_forest_expanded(roots, fl_path)
        return roots, crumbs, folders, cur_proj, cur_org

    _annotate_forest_expanded(roots, fl_path)
    return roots, crumbs, folders, None, None


def _dict_file_count(d: dict, *, files_key: str = "files") -> int:
    """从嵌套分组取文件数：优先 file_count，否则统计 files 列表长度。"""
    if "file_count" in d:
        try:
            return int(d["file_count"])
        except (TypeError, ValueError):
            pass
    return len(d.get(files_key) or [])


def _report_file_count(rg: dict, *, site_level: bool) -> int:
    """报告环节文件夹下的文件总数。"""
    if "file_count" in rg:
        try:
            return int(rg["file_count"])
        except (TypeError, ValueError):
            pass
    if site_level:
        total = 0
        for sg in rg.get("site_records") or []:
            total += _dict_file_count(sg)
        return total
    return len(rg.get("files") or [])


def _project_file_count(pg: dict, *, site_level: bool) -> int:
    """检测项目文件夹下的文件总数。"""
    if "file_count" in pg:
        try:
            return int(pg["file_count"])
        except (TypeError, ValueError):
            pass
    return sum(_report_file_count(rg, site_level=site_level) for rg in pg.get("reports") or [])


def _find_project_group(nested_groups: list, project_key: str) -> dict | None:
    for pg in nested_groups:
        if str(pg.get("project_key")) == project_key:
            return pg
    return None


_INSPECTION_SUBMIT_BUCKETS: tuple[tuple[str, str, str, str], ...] = (
    ("data", "g-data", "记录", "submit_data"),
    ("signature", "g-sig", "签名", "submit_signature"),
    ("photo", "g-img", "留存照片", "submit_photo"),
)

_SUBMIT_BUCKET_BY_GSEG = {g_seg.split("-", 1)[1]: key for key, g_seg, _lbl, _ic in _INSPECTION_SUBMIT_BUCKETS}


def _site_bucket_file_list(sg: dict, bucket: str) -> list:
    return list(sg.get(f"{bucket}_files") or [])


def _site_submit_bucket_folders(
    pk: str, rk: str, sk: str, sg: dict
) -> list[FolderEntry]:
    base = f"p-{pk}/r-{rk}/s-{sk}"
    out: list[FolderEntry] = []
    for bucket, g_seg, label, icon in _INSPECTION_SUBMIT_BUCKETS:
        flist = _site_bucket_file_list(sg, bucket)
        out.append(
            FolderEntry(
                kind="folder",
                label=label,
                path=f"{base}/{g_seg}",
                meta=f"{len(flist)} 个文件",
                count=len(flist),
                icon=icon,
            )
        )
    return out


def _find_template_output_group(nested_groups: list, output_key: str) -> dict | None:
    for og in nested_groups:
        if str(og.get("output_key")) == output_key:
            return og
    return None


def _find_template_task_group(output_group: dict | None, task_key: str) -> dict | None:
    if not output_group:
        return None
    for tg in output_group.get("task_groups") or []:
        if str(tg.get("task_key")) == task_key:
            return tg
    return None


def _explorer_template_library_files(
    nested_groups: list,
    segs: list[tuple[str, str]],
    crumbs: list[BreadcrumbItem],
    tab_label: str,
) -> tuple[list[TreeNode], list[BreadcrumbItem], list[FolderEntry], list]:
    """文件库「模板」：现场记录 / 报告 → 环节任务（报告下可展开关联现场记录）。"""
    if segs and segs[0][0] == "t":
        tk = segs[0][1]
        for og in nested_groups:
            for tg in og.get("task_groups") or []:
                if str(tg.get("task_key")) == tk:
                    target = str(og.get("output_key") or "site_record")
                    segs = [("ot", target), ("t", tk)] + segs[1:]
                    break
            else:
                continue
            break

    files_here: list = []
    folders: list[FolderEntry] = []
    tree: list[TreeNode] = []

    site_og = _find_template_output_group(nested_groups, "site_record")
    report_og = _find_template_output_group(nested_groups, "report")
    none_og = _find_template_output_group(nested_groups, "none")

    site_groups = list((site_og or {}).get("task_groups") or [])
    report_groups = list((report_og or {}).get("task_groups") or [])
    none_groups = list((none_og or {}).get("task_groups") or [])

    site_children = [
        TreeNode(
            id=f"t-{tg.get('task_key', 'none')}",
            label=str(tg.get("task_heading") or "任务"),
            path=f"ot-site_record/t-{tg.get('task_key', 'none')}",
            node_type="task",
            count=_dict_file_count(tg),
        )
        for tg in site_groups
    ]
    if site_groups or site_og:
        tree.append(
            TreeNode(
                id="ot-site_record",
                label=_TASK_OUTPUT_LABELS["site_record"],
                path="ot-site_record",
                node_type="folder",
                count=int((site_og or {}).get("file_count") or 0) or sum(_dict_file_count(t) for t in site_groups),
                children=site_children,
            )
        )

    report_children: list[TreeNode] = []
    for tg in report_groups:
        rk = str(tg.get("task_key", "none"))
        linked = tg.get("linked_site_groups") or []
        src_nodes = [
            TreeNode(
                id=f"t-{lg.get('task_key', 'none')}",
                label=str(lg.get("task_heading") or "现场记录"),
                path=f"ot-report/t-{rk}/t-{lg.get('task_key', 'none')}",
                node_type="site",
                count=_dict_file_count(lg),
            )
            for lg in linked
        ]
        report_children.append(
            TreeNode(
                id=f"t-{rk}",
                label=str(tg.get("task_heading") or "报告"),
                path=f"ot-report/t-{rk}",
                node_type="report",
                count=_dict_file_count(tg) + sum(_dict_file_count(lg) for lg in linked),
                children=src_nodes,
            )
        )
    if report_groups or report_og:
        tree.append(
            TreeNode(
                id="ot-report",
                label=_TASK_OUTPUT_LABELS["report"],
                path="ot-report",
                node_type="folder",
                count=int((report_og or {}).get("file_count") or 0)
                or sum(_dict_file_count(t) for t in report_groups),
                children=report_children,
            )
        )

    if none_groups:
        for tg in none_groups:
            tree.append(
                TreeNode(
                    id=f"t-{tg.get('task_key', 'none')}",
                    label=str(tg.get("task_heading") or "未绑定任务模板"),
                    path=f"ot-none/t-{tg.get('task_key', 'none')}",
                    node_type="task",
                    count=_dict_file_count(tg),
                )
            )

    if not segs:
        for og, target in (
            (site_og, "site_record"),
            (report_og, "report"),
            (none_og, "none"),
        ):
            if not og:
                continue
            n = int(og.get("file_count") or 0)
            meta = f"{n} 个文件"
            if target == "report":
                linked_n = sum(
                    _dict_file_count(lg)
                    for tg in report_groups
                    for lg in (tg.get("linked_site_groups") or [])
                )
                if linked_n:
                    meta = f"{n} 个文件 · 含 {linked_n} 个关联现场记录模板文件"
            label = _TASK_OUTPUT_LABELS.get(target, "未分类")
            folders.append(
                FolderEntry(
                    kind="folder",
                    label=label,
                    path=f"ot-{target}" if target != "none" else "ot-none",
                    meta=meta,
                    count=n,
                    icon="site_record" if target == "site_record" else ("report" if target == "report" else "folder"),
                )
            )
        return tree, crumbs, folders, files_here

    if segs[0][0] != "ot":
        return tree, crumbs, folders, files_here

    target = segs[0][1]
    label = _TASK_OUTPUT_LABELS.get(target, "未分类" if target != "none" else "未绑定任务模板")
    crumbs.append(BreadcrumbItem(label, f"ot-{target}"))

    output_group = _find_template_output_group(nested_groups, target)
    if output_group is None and target == "none":
        output_group = none_og

    if len(segs) == 1:
        for tg in output_group.get("task_groups") or [] if output_group else []:
            tk = str(tg.get("task_key", "none"))
            meta = f"{_dict_file_count(tg)} 个文件"
            linked = tg.get("linked_site_groups") or []
            if target == "report" and linked:
                meta += f" · {len(linked)} 个关联现场记录"
            folders.append(
                FolderEntry(
                    kind="folder",
                    label=str(tg.get("task_heading") or "任务"),
                    path=f"ot-{target}/t-{tk}",
                    meta=meta,
                    count=_dict_file_count(tg),
                    icon="report" if target == "report" else "task",
                )
            )
        return tree, crumbs, folders, files_here

    if len(segs) >= 2 and segs[1][0] == "t":
        tk = segs[1][1]
        task_group = _find_template_task_group(output_group, tk)
        if task_group:
            crumbs.append(
                BreadcrumbItem(str(task_group.get("task_heading") or "任务"), f"ot-{target}/t-{tk}")
            )
        if len(segs) == 2:
            if target == "report" and task_group:
                for lg in task_group.get("linked_site_groups") or []:
                    sk = str(lg.get("task_key", "none"))
                    folders.append(
                        FolderEntry(
                            kind="folder",
                            label=str(lg.get("task_heading") or "现场记录"),
                            path=f"ot-report/t-{tk}/t-{sk}",
                            meta=f"{_dict_file_count(lg)} 个文件",
                            count=_dict_file_count(lg),
                            icon="site",
                        )
                    )
                files_here = list(task_group.get("files") or [])
            elif task_group:
                files_here = list(task_group.get("files") or [])
            return tree, crumbs, folders, files_here

        if len(segs) >= 3 and segs[2][0] == "t" and target == "report":
            sk = segs[2][1]
            if task_group:
                for lg in task_group.get("linked_site_groups") or []:
                    if str(lg.get("task_key")) == sk:
                        crumbs.append(
                            BreadcrumbItem(
                                str(lg.get("task_heading") or "现场记录"),
                                f"ot-report/t-{tk}/t-{sk}",
                            )
                        )
                        files_here = list(lg.get("files") or [])
                        break
            return tree, crumbs, folders, files_here

    return tree, crumbs, folders, files_here


def build_file_library_explorer(
    *,
    tab: str,
    nested_mode: str,
    nested_groups: list,
    fl_path: str,
    tree_mode: str,
    commission_org_nav: list[dict],
) -> tuple[list[TreeNode], list[BreadcrumbItem], list[FolderEntry], list]:
    """
    文件库主区：按路径只展示当前层级的子文件夹；文件列表由 views 在叶子层注入。
    返回 (tree, breadcrumbs, folder_entries, files_at_path)
    """
    path = _norm_path(fl_path)
    segs = _parse_segments(path)
    mode = (tree_mode or "project").strip() or "project"
    tab_label = tab or "files"

    crumbs: list[BreadcrumbItem] = [BreadcrumbItem(tab_label, "")]
    folders: list[FolderEntry] = []
    files_here: list = []
    tree: list[TreeNode] = []

    if nested_mode == "library_task":
        tree, crumbs, folders, files_here = _explorer_template_library_files(
            nested_groups, segs, crumbs, tab_label
        )
        _annotate_forest_expanded(tree, fl_path)
        return tree, crumbs, folders, files_here

    if nested_mode == "project_report_site":
        return _explorer_project_report(path, segs, crumbs, folders, nested_groups, tree, site_level=True)
    if nested_mode == "project_report":
        return _explorer_project_report(path, segs, crumbs, folders, nested_groups, tree, site_level=False)

  # trash / flat: root = all files passed in nested_groups as flat? views handles separately
    return tree, crumbs, folders, files_here


def _explorer_project_report(
    path: str,
    segs: list[tuple[str, str]],
    crumbs: list[BreadcrumbItem],
    folders: list[FolderEntry],
    nested_groups: list,
    tree: list[TreeNode],
    *,
    site_level: bool,
) -> tuple[list[TreeNode], list[BreadcrumbItem], list[FolderEntry], list]:
    files_here: list = []

    for pg in nested_groups:
        pk = str(pg.get("project_key", "none"))
        reports = pg.get("reports") or []
        r_children: list[TreeNode] = []
        for rg in reports:
            rk = str(rg.get("report_key", "none"))
            rn = _report_file_count(rg, site_level=site_level)
            if site_level:
                sites = rg.get("site_records") or []
                s_children = []
                for sg in sites:
                    sk = str(sg.get("site_key", "none"))
                    sn = _dict_file_count(sg)
                    bucket_nodes = [
                        TreeNode(
                            id=f"s-{sk}-{g_seg}",
                            label=label,
                            path=f"p-{pk}/r-{rk}/s-{sk}/{g_seg}",
                            node_type="folder",
                            count=len(_site_bucket_file_list(sg, bucket)),
                        )
                        for bucket, g_seg, label, _icon in _INSPECTION_SUBMIT_BUCKETS
                    ]
                    s_children.append(
                        TreeNode(
                            id=f"s-{sk}",
                            label=str(sg.get("site_heading") or "现场记录"),
                            path=f"p-{pk}/r-{rk}/s-{sk}",
                            node_type="site",
                            count=sn,
                            children=bucket_nodes,
                        )
                    )
                r_children.append(
                    TreeNode(
                        id=f"r-{rk}",
                        label=str(rg.get("report_heading") or "报告"),
                        path=f"p-{pk}/r-{rk}",
                        node_type="report",
                        count=rn,
                        children=s_children,
                    )
                )
            else:
                r_children.append(
                    TreeNode(
                        id=f"r-{rk}",
                        label=str(rg.get("report_heading") or "报告"),
                        path=f"p-{pk}/r-{rk}",
                        node_type="report",
                        count=rn,
                    )
                )
        pn = _project_file_count(pg, site_level=site_level)
        proj_pk = int(pk) if str(pk).isdigit() else 0
        tree.append(
            TreeNode(
                id=f"p-{pk}",
                label=str(pg.get("project_heading") or "项目"),
                path=f"p-{pk}",
                node_type="project",
                count=pn,
                children=r_children,
                drop_accepts="file" if proj_pk else "",
                drop_id=proj_pk,
            )
        )

    if not segs:
        for pg in nested_groups:
            pn = _project_file_count(pg, site_level=site_level)
            pk_raw = str(pg.get("project_key", "none"))
            proj_pk = int(pk_raw) if pk_raw.isdigit() else 0
            folders.append(
                FolderEntry(
                    kind="folder",
                    label=str(pg.get("project_heading") or "项目"),
                    path=f"p-{pk_raw}",
                    meta=f"{pn} 个文件",
                    count=pn,
                    icon="project",
                    entity_pk=proj_pk,
                    drop_accepts="file" if proj_pk else "",
                )
            )
        return tree, crumbs, folders, files_here

    if segs[0][0] != "p":
        return tree, crumbs, folders, files_here
    pk = segs[0][1]
    pg = _find_project_group(nested_groups, pk)
    if not pg:
        return tree, crumbs, folders, files_here
    crumbs.append(BreadcrumbItem(str(pg.get("project_heading") or "项目"), f"p-{pk}"))

    if len(segs) == 1:
        for rg in pg.get("reports") or []:
            rn = _report_file_count(rg, site_level=site_level)
            folders.append(
                FolderEntry(
                    kind="folder",
                    label=str(rg.get("report_heading") or "报告"),
                    path=f"p-{pk}/r-{rg.get('report_key', 'none')}",
                    meta=f"{rn} 个文件",
                    count=rn,
                )
            )
        return tree, crumbs, folders, files_here

    if len(segs) >= 2 and segs[1][0] == "r":
        rk = segs[1][1]
        for rg in pg.get("reports") or []:
            if str(rg.get("report_key")) != rk:
                continue
            crumbs.append(BreadcrumbItem(str(rg.get("report_heading") or "报告"), f"p-{pk}/r-{rk}"))
            if site_level:
                if len(segs) == 2:
                    for sg in rg.get("site_records") or []:
                        sk = str(sg.get("site_key", "none"))
                        sn = _dict_file_count(sg)
                        folders.append(
                            FolderEntry(
                                kind="folder",
                                label=str(sg.get("site_heading") or "现场记录"),
                                path=f"p-{pk}/r-{rk}/s-{sk}",
                                meta=f"{sn} 个文件",
                                count=sn,
                            )
                        )
                    return tree, crumbs, folders, files_here
                if len(segs) >= 3 and segs[2][0] == "s":
                    sk = segs[2][1]
                    site_base = f"p-{pk}/r-{rk}/s-{sk}"
                    for sg in rg.get("site_records") or []:
                        if str(sg.get("site_key")) != sk:
                            continue
                        crumbs.append(
                            BreadcrumbItem(str(sg.get("site_heading") or "现场记录"), site_base)
                        )
                        if len(segs) == 3:
                            folders.extend(_site_submit_bucket_folders(pk, rk, sk, sg))
                            return tree, crumbs, folders, files_here
                        if len(segs) >= 4 and segs[3][0] == "g":
                            g_key = segs[3][1]
                            bucket = _SUBMIT_BUCKET_BY_GSEG.get(g_key, "data")
                            g_seg = f"g-{g_key}"
                            label = next(
                                (lbl for b, gs, lbl, _ic in _INSPECTION_SUBMIT_BUCKETS if b == bucket),
                                "文件夹",
                            )
                            crumbs.append(BreadcrumbItem(label, f"{site_base}/{g_seg}"))
                            files_here = _site_bucket_file_list(sg, bucket)
                        break
            else:
                files_here = list(rg.get("files") or [])
            break

    return tree, crumbs, folders, files_here


_TASK_OUTPUT_LABELS: dict[str, str] = {
    "site_record": "现场记录",
    "report": "报告",
}


def _task_report_source_site_tasks(
    report_task,
    task_by_id: dict[int, Any],
    *,
    has_report_source_relation: bool,
) -> list:
    """报告任务已关联、且在当前可见列表中的现场记录任务模板。"""
    if not has_report_source_relation:
        return []
    if getattr(report_task, "output_target", None) != "report":
        return []
    out: list = []
    rel = getattr(report_task, "report_source_tasks", None)
    if rel is None:
        return out
    for st in rel.all():
        if getattr(st, "output_target", None) != "site_record":
            continue
        if st.pk not in task_by_id:
            continue
        out.append(st)
    out.sort(key=lambda x: (x.code or "", x.name or ""))
    return out


def build_task_template_explorer(
    tasks: list,
    *,
    fl_path: str,
    manage_task_id: int | None,
    has_report_source_relation: bool = False,
) -> tuple[list[TreeNode], list[BreadcrumbItem], list[FolderEntry], int | None]:
    """任务模板库：现场记录 / 报告；报告下可展开关联的现场记录模板。"""
    path = _norm_path(fl_path)
    segs = _parse_segments(path)
    task_by_id = {t.pk: t for t in tasks}
    by_target: dict[str, list] = {}
    for t in tasks:
        key = getattr(t, "output_target", None) or "site_record"
        by_target.setdefault(key, []).append(t)
    for lst in by_target.values():
        lst.sort(key=lambda x: (x.code or "", x.name or ""))

    tree: list[TreeNode] = []
    site_items = by_target.get("site_record", [])
    report_items = by_target.get("report", [])

    site_children = [
        TreeNode(
            id=f"t-{t.pk}",
            label=t.name,
            path=f"ot-site_record/t-{t.pk}",
            node_type="task",
            is_active=manage_task_id == t.pk,
            link_suffix=f"&manage_task={t.pk}",
        )
        for t in site_items
    ]
    tree.append(
        TreeNode(
            id="ot-site_record",
            label=_TASK_OUTPUT_LABELS["site_record"],
            path="ot-site_record",
            node_type="folder",
            count=len(site_items),
            children=site_children,
        )
    )

    report_children: list[TreeNode] = []
    for rt in report_items:
        sources = _task_report_source_site_tasks(
            rt, task_by_id, has_report_source_relation=has_report_source_relation
        )
        src_nodes = [
            TreeNode(
                id=f"t-{st.pk}",
                label=st.name,
                path=f"ot-report/t-{rt.pk}/t-{st.pk}",
                node_type="site",
                is_active=manage_task_id == st.pk,
                link_suffix=f"&manage_task={st.pk}",
            )
            for st in sources
        ]
        report_children.append(
            TreeNode(
                id=f"t-{rt.pk}",
                label=rt.name,
                path=f"ot-report/t-{rt.pk}",
                node_type="task",
                count=len(sources),
                children=src_nodes,
                is_active=manage_task_id == rt.pk and len(segs) <= 2,
                link_suffix=f"&manage_task={rt.pk}",
            )
        )
    tree.append(
        TreeNode(
            id="ot-report",
            label=_TASK_OUTPUT_LABELS["report"],
            path="ot-report",
            node_type="folder",
            count=len(report_items),
            children=report_children,
        )
    )

    crumbs = [BreadcrumbItem("任务模板", "")]
    folders: list[FolderEntry] = []
    resolved_task_id: int | None = None

    if not segs:
        for target, label in _TASK_OUTPUT_LABELS.items():
            n = len(by_target.get(target, []))
            if n:
                meta = f"{n} 个模板"
                if target == "report" and has_report_source_relation:
                    linked = sum(
                        len(
                            _task_report_source_site_tasks(
                                rt,
                                task_by_id,
                                has_report_source_relation=True,
                            )
                        )
                        for rt in report_items
                    )
                    if linked:
                        meta = f"{n} 个报告 · 已关联 {linked} 个现场记录"
                folders.append(
                    FolderEntry(
                        kind="folder",
                        label=label,
                        path=f"ot-{target}",
                        meta=meta,
                        count=n,
                    )
                )
        _annotate_forest_expanded(tree, fl_path)
        return tree, crumbs, folders, None

    if segs[0][0] != "ot":
        _annotate_forest_expanded(tree, fl_path)
        return tree, crumbs, folders, None

    target = segs[0][1]
    label = _TASK_OUTPUT_LABELS.get(target, target)
    crumbs.append(BreadcrumbItem(label, f"ot-{target}"))

    if len(segs) == 1:
        if target == "site_record":
            for t in site_items:
                folders.append(
                    FolderEntry(
                        kind="folder",
                        label=t.name,
                        path=f"ot-site_record/t-{t.pk}",
                        meta=t.code or "",
                        count=0,
                        icon="project",
                        link_suffix=f"&manage_task={t.pk}",
                    )
                )
        elif target == "report":
            for rt in report_items:
                sources = _task_report_source_site_tasks(
                    rt, task_by_id, has_report_source_relation=has_report_source_relation
                )
                meta = rt.code or ""
                if sources:
                    meta = (meta + " · " if meta else "") + f"{len(sources)} 个关联现场记录"
                folders.append(
                    FolderEntry(
                        kind="folder",
                        label=rt.name,
                        path=f"ot-report/t-{rt.pk}",
                        meta=meta,
                        count=len(sources),
                        icon="project",
                        link_suffix=f"&manage_task={rt.pk}",
                    )
                )
        _annotate_forest_expanded(tree, fl_path)
        return tree, crumbs, folders, None

    if len(segs) >= 2 and segs[1][0] == "t":
        try:
            report_or_task_id = int(segs[1][1])
        except ValueError:
            report_or_task_id = None
        report_task = task_by_id.get(report_or_task_id) if report_or_task_id else None

        if target == "report" and report_task is not None:
            crumbs.append(BreadcrumbItem(report_task.name, f"ot-report/t-{report_task.pk}"))

        if len(segs) == 2:
            resolved_task_id = report_or_task_id
            if target == "report" and report_task is not None:
                for st in _task_report_source_site_tasks(
                    report_task,
                    task_by_id,
                    has_report_source_relation=has_report_source_relation,
                ):
                    folders.append(
                        FolderEntry(
                            kind="folder",
                            label=st.name,
                            path=f"ot-report/t-{report_task.pk}/t-{st.pk}",
                            meta=st.code or "现场记录来源",
                            count=0,
                            icon="site",
                            link_suffix=f"&manage_task={st.pk}",
                        )
                    )
            _annotate_forest_expanded(tree, fl_path)
            return tree, crumbs, folders, resolved_task_id

        if len(segs) >= 3 and segs[2][0] == "t" and target == "report":
            try:
                site_task_id = int(segs[2][1])
            except ValueError:
                site_task_id = None
            site_task = task_by_id.get(site_task_id) if site_task_id else None
            if site_task is not None:
                crumbs.append(BreadcrumbItem(site_task.name, _norm_path(fl_path)))
            resolved_task_id = site_task_id
            _annotate_forest_expanded(tree, fl_path)
            return tree, crumbs, folders, resolved_task_id

    _annotate_forest_expanded(tree, fl_path)
    return tree, crumbs, folders, None


def explorer_url(base_qs: str, fl_path: str) -> str:
    p = _norm_path(fl_path)
    if not p:
        return base_qs
    sep = "&" if "?" in base_qs else "?"
    return f"{base_qs}{sep}fl_path={quote(p)}"
