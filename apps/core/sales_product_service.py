"""销售产品目录与委托挂载。"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Prefetch, QuerySet

from apps.core.biz_operation_log import record_biz_operation
from apps.core.equipment_device_type_service import (
    EQUIPMENT_DEVICE_TYPE_CHOICES,
    INSPECTION_TYPE_FOLDER_SPECS,
)
from apps.core.models import (
    BizOperationLog,
    CommissionOrgEquipment,
    LibraryProject,
    LibraryProjectProduct,
    LibraryProjectProductEquipment,
    SalesProduct,
    SalesProductCategory,
    SalesProductLine,
)


def inspection_type_choices() -> list[str]:
    return [s["inspection_type"] for s in INSPECTION_TYPE_FOLDER_SPECS]


def device_type_choices() -> tuple[str, ...]:
    return EQUIPMENT_DEVICE_TYPE_CHOICES


def _dec(raw, default: Decimal = Decimal("0")) -> Decimal:
    try:
        return Decimal(str(raw).strip() or "0")
    except (InvalidOperation, AttributeError, TypeError, ValueError):
        return default


def _safe_int(raw, default: int = 0) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError, AttributeError):
        return default


def normalize_name_list(raw) -> list[str]:
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace("，", ",").split(",") if p.strip()]
        return parts
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        s = str(item or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def categories_active_qs() -> QuerySet[SalesProductCategory]:
    return SalesProductCategory.objects.filter(is_active=True).order_by("sort_order", "id")


def lines_active_qs() -> QuerySet[SalesProductLine]:
    return SalesProductLine.objects.filter(is_active=True).order_by("sort_order", "id")


def products_queryset(*, listed_only: bool = False, q: str = "") -> QuerySet[SalesProduct]:
    qs = SalesProduct.objects.filter(is_active=True).select_related("category", "product_line")
    if listed_only:
        qs = qs.filter(status=SalesProduct.STATUS_LISTED)
    needle = (q or "").strip()
    if needle:
        qs = qs.filter(name__icontains=needle)
    return qs.order_by("-updated_at", "-id")


def product_snapshot(product: SalesProduct) -> dict[str, Any]:
    return {
        "name": product.name,
        "standard_price": str(product.standard_price),
        "unit": product.unit,
        "status": product.status,
        "listed_at": product.listed_at.isoformat() if product.listed_at else "",
        "category_id": product.category_id,
        "category": product.category.name if product.category_id else "",
        "product_line_id": product.product_line_id,
        "product_line": product.product_line.name if product.product_line_id else "",
        "owner_name": product.owner_name,
        "notes": product.notes,
        "specs": product.specs,
        "default_device_types": list(product.default_device_types or []),
        "default_device_counts": dict(product.default_device_counts or {}),
        "default_inspection_types": list(product.default_inspection_types or []),
    }


def normalize_device_bindings(
    device_types,
    device_counts=None,
) -> tuple[list[str], dict[str, int]]:
    """多选设备类型 + 每类台数（≥1）。"""
    types = normalize_name_list(device_types)
    allowed = set(EQUIPMENT_DEVICE_TYPE_CHOICES)
    for dt in types:
        if dt not in allowed:
            raise ValueError(f"不支持的设备类型：{dt}")
    counts_in = device_counts if isinstance(device_counts, dict) else {}
    out_counts: dict[str, int] = {}
    for dt in types:
        raw = counts_in.get(dt, counts_in.get(str(dt), 1))
        try:
            n = int(raw)
        except (TypeError, ValueError):
            n = 1
        out_counts[dt] = max(1, n)
    return types, out_counts


def apply_listed_at(row: SalesProduct, *, new_status: str, previous_status: str | None = None) -> None:
    """上架时写入/刷新 listed_at；下架保留历史时间。"""
    from django.utils import timezone

    if new_status == SalesProduct.STATUS_LISTED:
        if previous_status != SalesProduct.STATUS_LISTED or row.listed_at is None:
            row.listed_at = timezone.now()
    # 下架不清空 listed_at，便于查看上次上架时间


def upsert_category(*, name: str, sort_order: int = 0, category_id: int | None = None) -> tuple[SalesProductCategory | None, str | None]:
    name = (name or "").strip()
    if not name:
        return None, "分类名称不能为空"
    dup = SalesProductCategory.objects.filter(name=name, is_active=True)
    if category_id:
        dup = dup.exclude(pk=category_id)
    if dup.exists():
        return None, f"分类「{name}」已存在"
    if category_id:
        row = SalesProductCategory.objects.filter(pk=category_id, is_active=True).first()
        if row is None:
            return None, "分类不存在"
        row.name = name
        row.sort_order = sort_order
        row.save(update_fields=["name", "sort_order", "updated_at"])
        return row, None
    return SalesProductCategory.objects.create(name=name, sort_order=sort_order), None


def deactivate_category(category_id: int) -> str | None:
    row = SalesProductCategory.objects.filter(pk=category_id, is_active=True).first()
    if row is None:
        return "分类不存在"
    if row.products.filter(is_active=True).exists():
        return "该分类下仍有产品，无法删除"
    row.is_active = False
    row.save(update_fields=["is_active", "updated_at"])
    return None


def upsert_product_line(*, name: str, sort_order: int = 0, line_id: int | None = None) -> tuple[SalesProductLine | None, str | None]:
    name = (name or "").strip()
    if not name:
        return None, "产品线名称不能为空"
    dup = SalesProductLine.objects.filter(name=name, is_active=True)
    if line_id:
        dup = dup.exclude(pk=line_id)
    if dup.exists():
        return None, f"产品线「{name}」已存在"
    if line_id:
        row = SalesProductLine.objects.filter(pk=line_id, is_active=True).first()
        if row is None:
            return None, "产品线不存在"
        row.name = name
        row.sort_order = sort_order
        row.save(update_fields=["name", "sort_order", "updated_at"])
        return row, None
    return SalesProductLine.objects.create(name=name, sort_order=sort_order), None


def deactivate_product_line(line_id: int) -> str | None:
    row = SalesProductLine.objects.filter(pk=line_id, is_active=True).first()
    if row is None:
        return "产品线不存在"
    if row.products.filter(is_active=True).exists():
        return "该产品线下仍有产品，无法删除"
    row.is_active = False
    row.save(update_fields=["is_active", "updated_at"])
    return None


def save_sales_product(
    *,
    user: User | None,
    product_id: int | None = None,
    name: str = "",
    standard_price=0,
    unit: str = "",
    status: str = SalesProduct.STATUS_LISTED,
    category_id: int | None = None,
    product_line_id: int | None = None,
    owner_name: str = "",
    notes: str = "",
    specs: str = "",
    default_device_types=None,
    default_device_counts=None,
    default_inspection_types=None,
) -> tuple[SalesProduct | None, str | None, dict[str, Any] | None]:
    name = (name or "").strip()
    if not name:
        return None, "产品名称不能为空", None
    if status not in (SalesProduct.STATUS_LISTED, SalesProduct.STATUS_UNLISTED):
        status = SalesProduct.STATUS_LISTED
    price = _dec(standard_price)
    if price < 0:
        return None, "标准价格不能为负", None

    category = None
    if category_id:
        category = SalesProductCategory.objects.filter(pk=category_id, is_active=True).first()
        if category is None:
            return None, "分类不存在或已停用", None
    product_line = None
    if product_line_id:
        product_line = SalesProductLine.objects.filter(pk=product_line_id, is_active=True).first()
        if product_line is None:
            return None, "产品线不存在或已停用", None

    try:
        device_types, device_counts = normalize_device_bindings(
            default_device_types, default_device_counts
        )
    except ValueError as exc:
        return None, str(exc), None
    insp_types = normalize_name_list(default_inspection_types)
    allowed_insp = set(inspection_type_choices())
    for it in insp_types:
        if it not in allowed_insp:
            return None, f"不支持的检测类型：{it}", None

    before: dict[str, Any] | None = None
    prev_status = None
    if product_id:
        row = SalesProduct.objects.filter(pk=product_id, is_active=True).select_related(
            "category", "product_line"
        ).first()
        if row is None:
            return None, "产品不存在", None
        before = product_snapshot(row)
        prev_status = row.status
    else:
        row = SalesProduct(created_by=user if getattr(user, "is_authenticated", False) else None)

    row.name = name
    row.standard_price = price
    row.unit = (unit or "").strip()[:32]
    apply_listed_at(row, new_status=status, previous_status=prev_status)
    row.status = status
    row.category = category
    row.product_line = product_line
    row.owner_name = (owner_name or "").strip()[:64]
    row.notes = (notes or "").strip()[:500]
    row.specs = (specs or "").strip()
    row.default_device_types = device_types
    row.default_device_counts = device_counts
    row.default_inspection_types = insp_types
    row.save()
    after = product_snapshot(row)
    changes = None
    if before is not None:
        changes = {k: {"from": before[k], "to": after[k]} for k in after if before.get(k) != after.get(k)}
    return row, None, changes


def set_product_status(product_id: int, status: str) -> tuple[SalesProduct | None, str | None]:
    if status not in (SalesProduct.STATUS_LISTED, SalesProduct.STATUS_UNLISTED):
        return None, "状态无效"
    row = SalesProduct.objects.filter(pk=product_id, is_active=True).first()
    if row is None:
        return None, "产品不存在"
    prev = row.status
    apply_listed_at(row, new_status=status, previous_status=prev)
    row.status = status
    row.save(update_fields=["status", "listed_at", "updated_at"])
    return row, None


def deactivate_product(product_id: int) -> str | None:
    row = SalesProduct.objects.filter(pk=product_id, is_active=True).first()
    if row is None:
        return "产品不存在"
    if row.project_links.exists():
        return "该产品已挂到委托，无法删除；可改为下架"
    row.is_active = False
    row.status = SalesProduct.STATUS_UNLISTED
    row.save(update_fields=["is_active", "status", "updated_at"])
    return None


def ensure_default_taxonomy() -> None:
    """确保基础分类/产品线存在（幂等）。"""
    if not categories_active_qs().filter(name="放射卫生").exists():
        SalesProductCategory.objects.create(name="放射卫生", sort_order=0)
    for i, name in enumerate(
        ("个人剂量监测", "核医学类", "放射治疗", "普放", "核磁", "办证服务", "其他")
    ):
        if not lines_active_qs().filter(name=name).exists():
            SalesProductLine.objects.create(name=name, sort_order=i)


def projects_with_products_for_org(org) -> tuple[list[dict[str, Any]], list[SalesProduct]]:
    """医院信息页：委托 → 产品 → 设备/检测。"""
    from apps.core.hospital_info_service import projects_for_org_binding
    from apps.core.models import LibraryProjectEquipment

    projects = list(
        projects_for_org_binding(org)
        .prefetch_related(
            Prefetch(
                "project_products",
                queryset=LibraryProjectProduct.objects.select_related("product", "product__category")
                .prefetch_related(
                    Prefetch(
                        "equipment_links",
                        queryset=LibraryProjectProductEquipment.objects.select_related(
                            "equipment", "equipment__department"
                        ).order_by("sort_order", "id"),
                    )
                )
                .order_by("sort_order", "id"),
            ),
            Prefetch(
                "project_equipments",
                queryset=LibraryProjectEquipment.objects.select_related(
                    "equipment", "report_task"
                ).order_by("sort_order", "id"),
            ),
        )
        .order_by("-updated_at", "-id")[:80]
    )
    listed = list(products_queryset(listed_only=True)[:500])
    out: list[dict[str, Any]] = []
    for p in projects:
        products = []
        for pp in p.project_products.all():
            links = []
            for link in pp.equipment_links.all():
                eq = link.equipment
                links.append(
                    {
                        "id": link.pk,
                        "equipment_id": eq.pk,
                        "equipment_name": eq.name,
                        "device_type": eq.device_type or "",
                        "inspection_type": link.inspection_type or "",
                        "dept_name": eq.department.name if eq.department_id else "",
                    }
                )
            products.append(
                {
                    "id": pp.pk,
                    "product_id": pp.product_id,
                    "product_name": pp.product.name if pp.product_id else "",
                    "quantity": pp.quantity,
                    "unit_price": pp.unit_price,
                    "amount": pp.amount,
                    "unit": pp.product.unit if pp.product_id else "",
                    "notes": pp.notes,
                    "equipment_links": links,
                }
            )
        direct_eqs = []
        for pe in p.project_equipments.all():
            direct_eqs.append(
                {
                    "id": pe.pk,
                    "equipment_name": pe.equipment.name if pe.equipment_id else "",
                    "inspection_type": pe.inspection_type or "",
                    "device_type": (pe.equipment.device_type if pe.equipment_id else "") or "",
                }
            )
        out.append(
            {
                "id": p.pk,
                "code": p.code,
                "name": p.name,
                "products": products,
                "direct_equipments": direct_eqs,
                "product_count": len(products),
                "direct_equipment_count": len(direct_eqs),
            }
        )
    return out, listed


def add_product_to_project(
    *,
    user: User | None,
    project_id: int,
    product_id: int,
    quantity=1,
    unit_price=None,
    notes: str = "",
) -> tuple[LibraryProjectProduct | None, str | None]:
    project = LibraryProject.objects.filter(pk=project_id, is_active=True).first()
    if project is None:
        return None, "委托项目不存在"
    product = SalesProduct.objects.filter(pk=product_id, is_active=True).first()
    if product is None:
        return None, "产品不存在"
    if not product.is_listed:
        return None, "该产品已下架，无法挂载到委托"
    qty = _dec(quantity, Decimal("1"))
    if qty <= 0:
        return None, "数量须大于 0"
    price = product.standard_price if unit_price is None or str(unit_price).strip() == "" else _dec(unit_price)
    if price < 0:
        return None, "单价不能为负"
    row = LibraryProjectProduct.objects.create(
        project=project,
        product=product,
        quantity=qty,
        unit_price=price,
        notes=(notes or "").strip()[:500],
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    return row, None


def remove_project_product(project_product_id: int) -> str | None:
    row = LibraryProjectProduct.objects.filter(pk=project_product_id).select_related("project").first()
    if row is None:
        return "委托产品记录不存在"
    row.delete()
    return None


def bind_equipment_to_project_product(
    *,
    project_product_id: int,
    equipment_id: int,
    inspection_type: str = "",
) -> tuple[LibraryProjectProductEquipment | None, str | None]:
    pp = (
        LibraryProjectProduct.objects.filter(pk=project_product_id)
        .select_related("project", "project__commission_org", "product")
        .first()
    )
    if pp is None:
        return None, "委托产品记录不存在"
    eq = (
        CommissionOrgEquipment.objects.filter(pk=equipment_id, is_active=True)
        .select_related("department")
        .first()
    )
    if eq is None:
        return None, "设备不存在"
    hospital = pp.project.commission_org.hospital_root() if pp.project.commission_org_id else None
    eq_hospital = eq.department.hospital_root() if eq.department_id else None
    if hospital and eq_hospital and hospital.pk != eq_hospital.pk:
        return None, "设备不属于该委托医院"
    itype = (inspection_type or "").strip()
    defaults = list(pp.product.default_inspection_types or []) if pp.product_id else []
    if itype and itype not in set(inspection_type_choices()) and itype not in defaults:
        # 允许自由文本检测类型，但仍建议使用标准值
        pass
    if LibraryProjectProductEquipment.objects.filter(
        project_product=pp, equipment=eq, inspection_type=itype
    ).exists():
        return None, "该设备与检测类型已挂在此产品下"
    link = LibraryProjectProductEquipment.objects.create(
        project_product=pp,
        equipment=eq,
        inspection_type=itype,
    )
    return link, None


def unbind_equipment_from_project_product(link_id: int) -> str | None:
    link = LibraryProjectProductEquipment.objects.filter(pk=link_id).first()
    if link is None:
        return "挂载记录不存在"
    link.delete()
    return None


@transaction.atomic
def seed_starter_products(user: User | None = None) -> dict[str, int]:
    """从 apps/core/data/sales_products_catalog.json 幂等写入产品目录。"""
    import json
    from pathlib import Path

    ensure_default_taxonomy()
    cat = categories_active_qs().filter(name="放射卫生").first()
    line_map = {x.name: x for x in lines_active_qs()}
    data_path = Path(__file__).resolve().parent / "data" / "sales_products_catalog.json"
    try:
        rows = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"created": 0, "skipped": 0, "error": str(exc)}

    if not isinstance(rows, list):
        return {"created": 0, "skipped": 0, "error": "catalog json must be a list"}

    created = 0
    skipped = 0
    updated = 0
    for item in rows:
        if not isinstance(item, dict):
            skipped += 1
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            skipped += 1
            continue
        line_name = str(item.get("line") or "").strip()
        devices = item.get("devices") or []
        # 支持 devices: ["CT"] 或 [{"type":"CT","count":2}]
        type_names: list[str] = []
        counts: dict[str, int] = {}
        for d in devices:
            if isinstance(d, dict):
                t = str(d.get("type") or d.get("device_type") or "").strip()
                if not t:
                    continue
                type_names.append(t)
                try:
                    counts[t] = max(1, int(d.get("count") or 1))
                except (TypeError, ValueError):
                    counts[t] = 1
            else:
                t = str(d or "").strip()
                if t:
                    type_names.append(t)
                    counts[t] = max(counts.get(t, 0), 1)
        type_names = [d for d in type_names if d in EQUIPMENT_DEVICE_TYPE_CHOICES]
        counts = {k: v for k, v in counts.items() if k in type_names}
        insps = list(item.get("insps") or [])
        existing = SalesProduct.objects.filter(name=name, is_active=True).first()
        row, err, changes = save_sales_product(
            user=user,
            product_id=existing.pk if existing else None,
            name=name,
            standard_price=item.get("price", "0"),
            unit=str(item.get("unit") or ""),
            status=SalesProduct.STATUS_LISTED,
            category_id=cat.pk if cat else None,
            product_line_id=line_map[line_name].pk if line_name in line_map else None,
            owner_name=str(item.get("owner") or ""),
            default_device_types=type_names,
            default_device_counts=counts,
            default_inspection_types=insps,
        )
        if err or row is None:
            skipped += 1
            continue
        if existing:
            if changes:
                updated += 1
                record_biz_operation(
                    actor=user,
                    scope=BizOperationLog.SCOPE_PRODUCT,
                    action=BizOperationLog.ACTION_UPDATE,
                    summary=f"同步产品目录：{row.name}",
                    entity_type="sales_product",
                    entity_id=row.pk,
                    detail={"changes": changes},
                )
            else:
                skipped += 1
        else:
            created += 1
            record_biz_operation(
                actor=user,
                scope=BizOperationLog.SCOPE_PRODUCT,
                action=BizOperationLog.ACTION_CREATE,
                summary=f"初始化产品：{row.name}",
                entity_type="sales_product",
                entity_id=row.pk,
                detail=product_snapshot(row),
            )
    return {"created": created, "skipped": skipped, "updated": updated}
