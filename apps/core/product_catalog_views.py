"""产品目录管理视图。"""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from urllib.parse import quote

from apps.core.biz_operation_log import record_biz_operation
from apps.core.library_access import (
    library_user_may_access_hospital_info_nav,
    library_user_may_edit_hospital_info,
    role_has,
)
from apps.core.models import BizOperationLog, SalesProduct
from apps.core.sales_product_service import (
    categories_active_qs,
    deactivate_category,
    deactivate_product,
    deactivate_product_line,
    device_type_choices,
    ensure_default_taxonomy,
    inspection_type_choices,
    lines_active_qs,
    product_snapshot,
    products_queryset,
    save_sales_product,
    seed_starter_products,
    set_product_status,
    upsert_category,
    upsert_product_line,
)


def _safe_int(raw) -> int:
    s = (str(raw) if raw is not None else "").strip()
    if not s or s.lower() == "undefined":
        return 0
    try:
        return int(s)
    except (TypeError, ValueError):
        return 0


@login_required
def product_catalog_manage(request):
    """产品管理：目录 CRUD、分类/产品线、上下架、默认设备/检测类型、变更日志。"""
    if not role_has(request.user, "perm_file_library"):
        messages.error(request, "无权访问该功能")
        return redirect(reverse("dashboard"))
    if not library_user_may_access_hospital_info_nav(request.user):
        messages.error(request, "当前角色无权访问产品管理")
        return redirect(reverse("dashboard"))

    can_edit = library_user_may_edit_hospital_info(request.user)
    tab = (request.GET.get("tab") or request.POST.get("tab") or "products").strip().lower()
    if tab not in ("products", "categories", "lines", "logs"):
        tab = "products"
    q = (request.GET.get("q") or "").strip()
    status_filter = (request.GET.get("status") or "").strip()

    def _redir() -> HttpResponse:
        url = reverse("product_catalog_manage") + f"?tab={tab}"
        if q:
            url += f"&q={quote(q)}"
        if status_filter:
            url += f"&status={quote(status_filter)}"
        return redirect(url)

    ensure_default_taxonomy()

    if request.method == "POST":
        if not can_edit:
            messages.error(request, "当前角色无权维护产品目录")
            return _redir()
        action = (request.POST.get("action") or "").strip()
        tab = (request.POST.get("tab") or tab).strip().lower() or tab

        if action == "seed_starter_products":
            stats = seed_starter_products(user=request.user)
            messages.success(
                request,
                f"已同步产品目录：新增 {stats.get('created', 0)}，"
                f"更新 {stats.get('updated', 0)}，跳过 {stats.get('skipped', 0)}",
            )
            return _redir()

        if action == "save_category":
            cid = _safe_int(request.POST.get("category_id"))
            row, err = upsert_category(
                name=request.POST.get("category_name", ""),
                sort_order=_safe_int(request.POST.get("sort_order")),
                category_id=cid or None,
            )
            if err:
                messages.error(request, err)
            else:
                messages.success(request, f"已保存分类：{row.name}")
                record_biz_operation(
                    actor=request.user,
                    scope=BizOperationLog.SCOPE_PRODUCT,
                    action=BizOperationLog.ACTION_UPDATE if cid else BizOperationLog.ACTION_CREATE,
                    summary=f"{'编辑' if cid else '添加'}分类：{row.name}",
                    entity_type="sales_product_category",
                    entity_id=row.pk,
                )
            return _redir()

        if action == "delete_category":
            cid = _safe_int(request.POST.get("category_id"))
            err = deactivate_category(cid)
            if err:
                messages.error(request, err)
            else:
                messages.success(request, "已删除分类")
                record_biz_operation(
                    actor=request.user,
                    scope=BizOperationLog.SCOPE_PRODUCT,
                    action=BizOperationLog.ACTION_DELETE,
                    summary=f"删除分类 #{cid}",
                    entity_type="sales_product_category",
                    entity_id=cid,
                )
            return _redir()

        if action == "save_product_line":
            lid = _safe_int(request.POST.get("line_id"))
            row, err = upsert_product_line(
                name=request.POST.get("line_name", ""),
                sort_order=_safe_int(request.POST.get("sort_order")),
                line_id=lid or None,
            )
            if err:
                messages.error(request, err)
            else:
                messages.success(request, f"已保存产品线：{row.name}")
                record_biz_operation(
                    actor=request.user,
                    scope=BizOperationLog.SCOPE_PRODUCT,
                    action=BizOperationLog.ACTION_UPDATE if lid else BizOperationLog.ACTION_CREATE,
                    summary=f"{'编辑' if lid else '添加'}产品线：{row.name}",
                    entity_type="sales_product_line",
                    entity_id=row.pk,
                )
            return _redir()

        if action == "delete_product_line":
            lid = _safe_int(request.POST.get("line_id"))
            err = deactivate_product_line(lid)
            if err:
                messages.error(request, err)
            else:
                messages.success(request, "已删除产品线")
                record_biz_operation(
                    actor=request.user,
                    scope=BizOperationLog.SCOPE_PRODUCT,
                    action=BizOperationLog.ACTION_DELETE,
                    summary=f"删除产品线 #{lid}",
                    entity_type="sales_product_line",
                    entity_id=lid,
                )
            return _redir()

        if action == "save_product":
            pid = _safe_int(request.POST.get("product_id"))
            options = request.POST.getlist("device_type_option")
            device_types: list[str] = []
            device_counts: dict = {}
            for i, dt in enumerate(options):
                if not request.POST.get(f"device_type_selected_{i}"):
                    continue
                device_types.append(dt)
                try:
                    device_counts[dt] = max(1, int(request.POST.get(f"device_type_count_{i}") or 1))
                except (TypeError, ValueError):
                    device_counts[dt] = 1
            row, err, changes = save_sales_product(
                user=request.user,
                product_id=pid or None,
                name=request.POST.get("product_name", ""),
                standard_price=request.POST.get("standard_price", "0"),
                unit=request.POST.get("unit", ""),
                status=request.POST.get("status", "listed"),
                category_id=_safe_int(request.POST.get("category_id")) or None,
                product_line_id=_safe_int(request.POST.get("product_line_id")) or None,
                owner_name=request.POST.get("owner_name", ""),
                notes=request.POST.get("notes", ""),
                specs=request.POST.get("specs", ""),
                default_device_types=device_types,
                default_device_counts=device_counts,
                default_inspection_types=request.POST.getlist("default_inspection_types"),
            )
            if err:
                messages.error(request, err)
            else:
                messages.success(request, f"已保存产品：{row.name}")
                record_biz_operation(
                    actor=request.user,
                    scope=BizOperationLog.SCOPE_PRODUCT,
                    action=BizOperationLog.ACTION_UPDATE if pid else BizOperationLog.ACTION_CREATE,
                    summary=f"{'编辑' if pid else '添加'}产品：{row.name}",
                    entity_type="sales_product",
                    entity_id=row.pk,
                    detail={"changes": changes, "after": product_snapshot(row)},
                )
            return _redir()

        if action == "set_product_status":
            pid = _safe_int(request.POST.get("product_id"))
            status = (request.POST.get("status") or "").strip()
            row, err = set_product_status(pid, status)
            if err:
                messages.error(request, err)
            else:
                messages.success(request, f"已将「{row.name}」设为{row.status_label}")
                record_biz_operation(
                    actor=request.user,
                    scope=BizOperationLog.SCOPE_PRODUCT,
                    action=BizOperationLog.ACTION_UPDATE,
                    summary=f"产品「{row.name}」{row.status_label}",
                    entity_type="sales_product",
                    entity_id=row.pk,
                    detail={"status": status},
                )
            return _redir()

        if action == "delete_product":
            pid = _safe_int(request.POST.get("product_id"))
            err = deactivate_product(pid)
            if err:
                messages.error(request, err)
            else:
                messages.success(request, "已删除产品")
                record_biz_operation(
                    actor=request.user,
                    scope=BizOperationLog.SCOPE_PRODUCT,
                    action=BizOperationLog.ACTION_DELETE,
                    summary=f"删除产品 #{pid}",
                    entity_type="sales_product",
                    entity_id=pid,
                )
            return _redir()

        messages.error(request, "未知操作")
        return _redir()

    products = list(products_queryset(q=q)[:300])
    if status_filter == "listed":
        products = [p for p in products if p.status == SalesProduct.STATUS_LISTED]
    elif status_filter == "unlisted":
        products = [p for p in products if p.status == SalesProduct.STATUS_UNLISTED]

    edit_product = None
    open_new = (request.GET.get("new") or "").strip() in ("1", "true", "yes")
    edit_id = _safe_int(request.GET.get("edit_id"))
    if edit_id:
        edit_product = next((p for p in products if p.pk == edit_id), None)
        if edit_product is None:
            edit_product = (
                SalesProduct.objects.filter(pk=edit_id, is_active=True)
                .select_related("category", "product_line")
                .first()
            )
    open_product_modal = bool(can_edit and tab == "products" and (edit_product or open_new))

    logs = []
    if tab == "logs":
        logs = list(
            BizOperationLog.objects.filter(scope=BizOperationLog.SCOPE_PRODUCT)
            .select_related("actor")
            .order_by("-created_at", "-id")[:100]
        )

    device_choices = list(device_type_choices())
    edit_device_rows = []
    for i, dt in enumerate(device_choices):
        selected = bool(edit_product and dt in (edit_product.default_device_types or []))
        count = edit_product.device_count_for(dt) if edit_product else 1
        edit_device_rows.append({"index": i, "type": dt, "selected": selected, "count": count})

    return render(
        request,
        "core/product_catalog_manage.html",
        {
            "tab": tab,
            "q": q,
            "status_filter": status_filter,
            "can_edit": can_edit,
            "products": products,
            "categories": list(categories_active_qs()),
            "product_lines": list(lines_active_qs()),
            "device_type_choices": device_choices,
            "edit_device_rows": edit_device_rows,
            "inspection_type_choices": inspection_type_choices(),
            "edit_product": edit_product,
            "open_product_modal": open_product_modal,
            "product_logs": logs,
            "hospital_info_url": reverse("hospital_info_manage"),
        },
    )
