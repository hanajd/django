"""委托单位层级：医院 → 院区（可选）→ 科室（可选）。"""
from __future__ import annotations

from apps.core.models import CommissionOrganization, CommissionOrgEquipment, LibraryProject

_LEVEL_ORDER = {
    CommissionOrganization.LEVEL_HOSPITAL: 0,
    CommissionOrganization.LEVEL_CAMPUS: 1,
    CommissionOrganization.LEVEL_DEPARTMENT: 2,
}


def commission_orgs_active_queryset():
    return CommissionOrganization.objects.filter(is_active=True).select_related("parent", "parent__parent")


def index_commission_orgs(org_rows: list[CommissionOrganization]) -> tuple[
    dict[int, CommissionOrganization],
    dict[int | None, list[CommissionOrganization]],
    list[CommissionOrganization],
]:
    org_by_id = {o.pk: o for o in org_rows}
    children_by_parent: dict[int | None, list[CommissionOrganization]] = {}
    for o in org_rows:
        pid = o.parent_id
        children_by_parent.setdefault(pid, []).append(o)
    for lst in children_by_parent.values():
        lst.sort(key=lambda x: (_LEVEL_ORDER.get(x.level, 9), x.name or "", x.pk))
    hospitals = children_by_parent.get(None, [])
    hospitals = [h for h in hospitals if h.level == CommissionOrganization.LEVEL_HOSPITAL]
    hospitals.sort(key=lambda x: (x.name or "", x.pk))
    return org_by_id, children_by_parent, hospitals


def parse_commission_path_segments(fl_path: str) -> list[tuple[str, str]]:
    from apps.core.library_folder_service import _norm_path, _parse_segments

    return _parse_segments(_norm_path(fl_path))


def commission_org_subtree_ids(root_org_id: int) -> list[int]:
    """委托单位节点及其全部下级（院区、科室）的主键列表。"""
    ids: set[int] = {root_org_id}
    stack = [root_org_id]
    while stack:
        oid = stack.pop()
        for kid in CommissionOrganization.objects.filter(parent_id=oid, is_active=True).values_list(
            "pk", flat=True
        ):
            if kid not in ids:
                ids.add(kid)
                stack.append(kid)
    return sorted(ids)


def project_ids_for_commission_org_path(
    co_path: str,
    *,
    org_by_id: dict[int, CommissionOrganization],
    projects_qs,
) -> list[int] | None:
    """
    按 co_path（h-1/c-2… 或 o-none）返回应纳入文件库筛选的项目 id 列表。
    返回 None 表示不按委托单位路径筛选。
    """
    from apps.core.library_folder_service import _norm_path

    p = _norm_path(co_path)
    if not p:
        return None
    segs = parse_commission_path_segments(p)
    if not segs:
        return None
    if segs[0][0] == "o" and segs[0][1] == "none":
        return list(projects_qs.filter(commission_org__isnull=True).values_list("pk", flat=True))
    oid = resolve_org_id_from_segments(segs, org_by_id)
    if oid is None:
        return None
    subtree = commission_org_subtree_ids(oid)
    return list(projects_qs.filter(commission_org_id__in=subtree).values_list("pk", flat=True))


def resolve_org_id_from_segments(
    segs: list[tuple[str, str]], org_by_id: dict[int, CommissionOrganization]
) -> int | None:
    last_id: int | None = None
    for key, val in segs:
        if key not in ("h", "c", "d", "o"):
            continue
        try:
            last_id = int(val)
        except ValueError:
            last_id = None
    if last_id is not None and last_id in org_by_id:
        return last_id
    return None


def org_from_legacy_path_segment(key: str, val: str, org_by_id: dict[int, CommissionOrganization]) -> CommissionOrganization | None:
    if key != "o":
        return None
    try:
        oid = int(val)
    except ValueError:
        return None
    return org_by_id.get(oid)


def sync_commission_org_project_names(org: CommissionOrganization) -> None:
    """更新挂载在该节点上的项目的委托单位显示名。"""
    full = org.full_display_name
    LibraryProject.objects.filter(commission_org_id=org.pk).update(commission_organization=full)


def sync_commission_org_subtree_project_names(org: CommissionOrganization) -> None:
    ids = {org.pk}
    stack = list(CommissionOrganization.objects.filter(parent_id=org.pk).values_list("pk", flat=True))
    while stack:
        oid = stack.pop()
        if oid in ids:
            continue
        ids.add(oid)
        stack.extend(
            CommissionOrganization.objects.filter(parent_id=oid).values_list("pk", flat=True)
        )
    for row in LibraryProject.objects.filter(commission_org_id__in=ids).select_related(
        "commission_org", "commission_org__parent", "commission_org__parent__parent"
    ):
        if row.commission_org_id:
            full = row.commission_org.full_display_name
            if full != (row.commission_organization or "").strip():
                LibraryProject.objects.filter(pk=row.pk).update(commission_organization=full)


def find_or_create_commission_org_chain(
    user,
    *,
    hospital_name: str,
    campus_name: str = "",
    department_name: str = "",
) -> CommissionOrganization | None:
    hospital_name = (hospital_name or "").strip()
    if not hospital_name:
        return None
    hospital, _ = CommissionOrganization.objects.get_or_create(
        level=CommissionOrganization.LEVEL_HOSPITAL,
        parent=None,
        name=hospital_name,
        is_active=True,
        defaults={"created_by": user},
    )
    leaf = hospital
    campus_name = (campus_name or "").strip()
    if campus_name:
        campus, _ = CommissionOrganization.objects.get_or_create(
            level=CommissionOrganization.LEVEL_CAMPUS,
            parent=hospital,
            name=campus_name,
            is_active=True,
            defaults={"created_by": user},
        )
        leaf = campus
    department_name = (department_name or "").strip()
    if department_name:
        parent = leaf
        dept, _ = CommissionOrganization.objects.get_or_create(
            level=CommissionOrganization.LEVEL_DEPARTMENT,
            parent=parent,
            name=department_name,
            is_active=True,
            defaults={"created_by": user},
        )
        leaf = dept
    return leaf


def validate_child_level(parent: CommissionOrganization | None, level: str) -> str | None:
    if level == CommissionOrganization.LEVEL_HOSPITAL:
        if parent is not None:
            return "医院层级不能有上级"
        return None
    if parent is None:
        return "院区、科室必须挂在医院或院区下"
    if level == CommissionOrganization.LEVEL_CAMPUS:
        if parent.level != CommissionOrganization.LEVEL_HOSPITAL:
            return "院区只能挂在医院下"
        return None
    if level == CommissionOrganization.LEVEL_DEPARTMENT:
        if parent.level not in (
            CommissionOrganization.LEVEL_HOSPITAL,
            CommissionOrganization.LEVEL_CAMPUS,
        ):
            return "科室只能挂在医院或院区下"
        return None
    return "无效的层级"


def build_commission_org_picker_tree(org_rows: list[CommissionOrganization]) -> list[dict]:
    """供前端悬浮选择器使用的医院树（含院区、科室子节点）。"""
    _, children_by_parent, hospitals = index_commission_orgs(org_rows)

    def _node(org: CommissionOrganization) -> dict:
        kids = children_by_parent.get(org.pk, [])
        return {
            "id": org.pk,
            "name": org.name,
            "level": org.level,
            "level_label": org.level_label,
            "full_display_name": org.full_display_name,
            "folder_path": org.folder_path(),
            "notes": (org.notes or "").strip(),
            "children": [_node(c) for c in kids],
        }

    return [_node(h) for h in hospitals]


def org_picker_initial_path(org: CommissionOrganization | None) -> list[int]:
    """从委托单位节点还原选择器内的路径（医院 → … → 当前）。"""
    if org is None:
        return []
    return [n.pk for n in org.ancestors_chain()]


def org_is_descendant_of(org: CommissionOrganization, ancestor_pk: int) -> bool:
    walk: CommissionOrganization | None = org
    while walk is not None:
        if walk.pk == ancestor_pk:
            return True
        walk = walk.parent
    return False


def move_commission_org_to_parent(
    org: CommissionOrganization,
    new_parent: CommissionOrganization,
) -> str | None:
    """将院区/科室移动到另一医院或院区下（医院为顶级，不可拖动）。"""
    if org.level == CommissionOrganization.LEVEL_HOSPITAL:
        return "医院为顶级单位，不可移动"
    if new_parent.pk == org.pk:
        return "不能移动到自身"
    if org_is_descendant_of(new_parent, org.pk):
        return "不能移动到自身的下级"
    err = validate_child_level(new_parent, org.level)
    if err:
        return err
    if (
        CommissionOrganization.objects.filter(
            parent=new_parent, name=org.name, is_active=True
        )
        .exclude(pk=org.pk)
        .exists()
    ):
        return "目标下已存在相同名称"
    org.parent = new_parent
    org.save(update_fields=["parent", "updated_at"])
    sync_commission_org_subtree_project_names(org)
    return None


def move_project_to_commission_org(
    project: LibraryProject,
    org: CommissionOrganization,
) -> str | None:
    if not org.is_active:
        return "委托单位无效"
    project.commission_org = org
    project.commission_organization = org.full_display_name
    project.save(update_fields=["commission_org", "commission_organization", "updated_at"])
    return None


def create_commission_org_node(
    user,
    *,
    level: str,
    name: str,
    parent: CommissionOrganization | None = None,
    notes: str = "",
) -> tuple[CommissionOrganization | None, str | None]:
    name = (name or "").strip()
    if not name:
        return None, "名称不能为空"
    err = validate_child_level(parent, level)
    if err:
        return None, err
    if level == CommissionOrganization.LEVEL_HOSPITAL:
        if CommissionOrganization.objects.filter(
            parent__isnull=True, name=name, is_active=True
        ).exists():
            return None, "该医院名称已存在"
        org = CommissionOrganization.objects.create(
            level=level,
            parent=None,
            name=name,
            notes=(notes or "").strip(),
            created_by=user,
        )
        return org, None
    if CommissionOrganization.objects.filter(parent=parent, name=name, is_active=True).exists():
        return None, f"同级下已存在「{name}」"
    org = CommissionOrganization.objects.create(
        level=level,
        parent=parent,
        name=name,
        notes=(notes or "").strip(),
        created_by=user,
    )
    return org, None


def deactivate_commission_org_node(org: CommissionOrganization) -> str | None:
    """软删除委托单位节点（停用）；须无下级机构且无关联设备/项目。"""
    if not org.is_active:
        return "该机构已不存在或已停用"
    child_count = CommissionOrganization.objects.filter(parent_id=org.pk, is_active=True).count()
    if child_count:
        return f"请先删除其下 {child_count} 个下级机构（院区或科室）"
    if org.level == CommissionOrganization.LEVEL_DEPARTMENT:
        eq_count = CommissionOrgEquipment.objects.filter(
            department_id=org.pk, is_active=True
        ).count()
        if eq_count:
            return f"该科室下仍有 {eq_count} 台设备，请先删除设备后再删科室"
    if org.level == CommissionOrganization.LEVEL_HOSPITAL:
        # 含已停用下级：委托可能仍挂在已删院区/科室上，hospital_root 仍指向本医院
        tree_ids: list[int] = []
        pending = [org.pk]
        seen: set[int] = set()
        while pending:
            cur = pending.pop()
            if cur in seen:
                continue
            seen.add(cur)
            tree_ids.append(cur)
            pending.extend(
                CommissionOrganization.objects.filter(parent_id=cur).values_list("pk", flat=True)
            )
        if LibraryProject.objects.filter(
            commission_org_id__in=tree_ids, is_active=True
        ).exists():
            return "该医院下仍有关联项目（含挂在已删院区/科室上的委托），请先调整或停用相关项目"
    org.is_active = False
    org.save(update_fields=["is_active", "updated_at"])
    org.contacts.filter(is_active=True).update(is_active=False)
    return None
