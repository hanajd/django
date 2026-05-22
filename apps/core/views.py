"""
Web 视图
使用 Django Template 渲染前后端不分离的页面
"""
import json as json_std
import importlib.util
import os
import re
import uuid
from datetime import date
from mimetypes import guess_type
from pathlib import Path
from urllib.parse import quote, urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.db.models.deletion import ProtectedError
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt

from apps.core import pipeline_service
from apps.core import htmlpdf_service
from apps.core.session_lease import bind_web_session_for_user, clear_web_session_lease
from apps.core.library_file_service import (
    attach_files_to_projects,
    attach_files_to_tasks,
    detach_files_from_projects,
    detach_files_from_tasks,
    library_file_download_response,
    parse_project_ids,
    rename_library_template_file,
    safe_library_basename,
    save_library_binary_uploads,
    soft_delete_library_file,
    hard_delete_library_file_disk_and_row,
)
from apps.core.library_access import (
    APP_SIDE_ROLE_CODES,
    ROLE_DEFAULT_PERMS_BY_CODE,
    ROLE_PERMISSION_MATRIX,
    library_export_merge_allowed_under_own_files_scope,
    library_file_access_allowed,
    library_user_may_delete_library_file,
    library_file_write_blocked_by_project_revocation,
    library_scope_own_files_only,
    library_signatory_assigned_project_selected,
    library_signatory_unrestricted_template_picker,
    library_upload_blocked_revoked_projects,
    library_user_may_browse_shared_library_templates,
    library_user_can_assign_tasks_to_participants,
    library_user_may_access_hospital_info_nav,
    library_user_is_project_primary_responsible,
    library_user_may_assign_on_project,
    library_user_is_template_editor,
    library_user_may_mutate_project_workbench,
    library_user_may_access_assigned_library_task,
    library_user_may_edit_library_task,
    library_user_may_export_task_template_pdf_for_project,
    library_user_may_filled_pdf_toolchain,
    library_user_may_use_htmlpdf_matrix_beta_controls,
    library_user_has_party_a_demo_restrictions,
    library_user_may_access_instrument_database,
    library_test_peer_user_ids,
    library_user_hide_project_workbench_files_tab,
    library_user_may_edit_project_files,
    library_user_may_access_task_template_library_nav,
    library_user_scoped_project_ids,
    library_user_file_library_quota_bytes,
    library_user_file_library_usage_bytes,
    role_can_upload_library_category,
    role_enterprise_catalog,
    role_has,
    role_has_htmlpdf,
    role_permission_groups_for_edit,
)
from apps.core.usage_workflow_tour import (
    register_tour_library_file,
    register_tour_project,
    register_tour_task,
    tour_cleanup_and_clear_session,
    tour_start,
)
from apps.core.commission_org_service import (
    build_commission_org_picker_tree,
    commission_org_subtree_ids,
    commission_orgs_active_queryset,
    create_commission_org_node,
    deactivate_commission_org_node,
    find_or_create_commission_org_chain,
    move_commission_org_to_parent,
    move_project_to_commission_org,
    org_picker_initial_path,
    parse_commission_path_segments,
    project_ids_for_commission_org_path,
    resolve_org_id_from_segments,
    sync_commission_org_subtree_project_names,
    index_commission_orgs,
)
from apps.core.library_folder_service import (
    _parse_segments as _parse_task_fl_segments,
    build_commission_org_nav_tree,
    build_file_library_explorer,
    build_org_tree_for_workbench,
)
from apps.core.library_task_folder_service import (
    UNCATEGORIZED_KEY,
    build_task_category_explorer,
    create_task_folder,
    deactivate_task_folder,
    fl_path_for_task,
    index_task_folders,
    move_report_to_task_folder,
    rename_task_folder,
    report_tasks_for_folder,
    task_folders_active_queryset,
)
from apps.core.task_template_folder_upload import ingest_template_folder_upload
from apps.core.task_template_ui_service import (
    build_htmlpdf_explorer_context,
    htmlpdf_editor_open_url,
    htmlpdf_editor_page_url,
    append_edit_mode_to_explorer_links,
    group_template_files_by_stem,
    suggested_template_json_save_name,
    task_library_edit_mode,
    task_library_page_url,
)
from apps.core.commission_management_service import (
    commission_may_view_user_stats,
    commission_overview_for_viewer,
    commission_project_rows,
    commission_user_summary_rows,
    commission_visible_subject_users,
    library_user_may_access_commission_manage,
)
from apps.core.project_equipment_service import (
    available_equipments_for_project,
    bind_equipments_to_project,
    equipment_scope_label,
    hospital_for_project,
    project_equipment_cards,
    report_task_options_for_project,
    resolve_workbench_equipment_scope_org,
    unbind_equipment_from_project,
    update_project_equipment_report_task,
)
from apps.core.project_workflow_ui import (
    PROCESS_STEPS_REFERENCE,
    ROLE_LADDER,
    build_workbench_pipeline_status,
    group_workflow_members_by_role,
    normalize_workbench_tab,
)
from apps.core.hospital_info_service import (
    backfill_equipment_histories_from_reports,
    bind_equipment_tasks_to_project,
    build_equipment_department_picker,
    department_campus_picker_key,
    departments_under_org,
    equipment_can_bind_to_project,
    equipment_display_status,
    equipment_fields_from_report_file,
    equipment_history_rows,
    equipment_previous_submit_payload,
    equipments_for_org_view,
    library_tasks_for_equipment_binding,
    parse_report_file_id_for_org,
    parse_report_task_id,
    projects_for_org_binding,
    record_equipment_inspection_from_report,
    report_file_option_label,
    report_files_for_org_binding,
    resolve_department_for_equipment_save,
    save_org_hospital_info,
    upsert_department_equipment,
    upsert_org_contact,
)
from apps.core.models import (
    CommissionOrgContact,
    CommissionOrgEquipment,
    CommissionOrganization,
    InspectionCase,
    InspectionCaseWorkflowState,
    InspectionSubmission,
    InstrumentCatalog,
    LibraryFile,
    LibraryFileProject,
    LibraryProject,
    LibraryProjectEquipment,
    LibraryProjectUserRevocation,
    LibraryProjectWorkflowMember,
    LibraryTask,
    LibraryTaskAssignment,
    LibraryTaskFolder,
    Menu,
    Role,
    UserProfile,
)
from utils.ollama_extract import generate_frontend_template_with_ollama
from utils.frontend_schema_rule_engine import (
    apply_table_row_col_to_source,
    build_frontend_schema_by_rules,
    normalize_field_text_by_underscore_rules,
)
from utils.pdf_field_formulas import attach_root_field_formulas_to_pdf_field_rows, merge_field_formulas_into_frontend
from utils.unified_template_fields import compact_unified_pdf_fields_for_storage

_LIBRARYTASK_HAS_REPORT_SOURCE_RELATION = None


def _librarytask_has_report_source_relation() -> bool:
    global _LIBRARYTASK_HAS_REPORT_SOURCE_RELATION
    if _LIBRARYTASK_HAS_REPORT_SOURCE_RELATION is not None:
        return _LIBRARYTASK_HAS_REPORT_SOURCE_RELATION
    try:
        with connection.cursor() as cursor:
            tables = set(connection.introspection.table_names(cursor))
        _LIBRARYTASK_HAS_REPORT_SOURCE_RELATION = "core_librarytask_report_source_tasks" in tables
    except Exception:
        _LIBRARYTASK_HAS_REPORT_SOURCE_RELATION = False
    return _LIBRARYTASK_HAS_REPORT_SOURCE_RELATION


def _require_perm(request, perm: str):
    if not role_has(request.user, perm):
        if request.path.startswith("/files/htmlpdf/api/"):
            return JsonResponse({"error": "forbidden"}, status=403)
        messages.error(request, "无权访问该功能")
        return redirect(reverse("dashboard"))
    return None


def _deny_party_a_demo_user_or_role_admin(request):
    """甲方演示账号不得管理用户或为他人分配角色（独立于角色布尔字段的防御性校验）。"""
    if library_user_has_party_a_demo_restrictions(request.user):
        messages.error(
            request,
            "当前为甲方演示账号，不可为其他用户分配角色或进入用户/角色管理模块。",
        )
        return redirect(reverse("dashboard"))
    return None


def _require_htmlpdf(request):
    """HTMLPDF 编辑器与 /files/htmlpdf/api/*：使用 perm_htmlpdf 或（兼容旧配置）perm_process_pipeline。"""
    if not role_has_htmlpdf(request.user):
        if request.path.startswith("/files/htmlpdf/api/"):
            return JsonResponse({"error": "forbidden"}, status=403)
        messages.error(request, "无权访问模板编辑器")
        return redirect(reverse("dashboard"))
    return None


def _export_request_field_formulas_raw(data: dict, form_schema: dict):
    """从 HTMLPDF API 请求体中取出 fieldFormulas / pdfFieldFormulas（dict 或 list）。"""
    fs = form_schema if isinstance(form_schema, dict) else {}
    for container in (data, fs):
        if not isinstance(container, dict):
            continue
        for key in ("fieldFormulas", "pdfFieldFormulas"):
            raw = container.get(key)
            if isinstance(raw, dict):
                return raw
            if isinstance(raw, list):
                return raw
    return None


def _parse_perm_overrides_from_post(request) -> dict:
    """POST 中 po_<perm_key> 取值 inherit / allow / deny，生成写入 profile.perm_overrides 的字典。"""
    overrides: dict = {}
    for key, _, _, _ in ROLE_PERMISSION_MATRIX:
        v = (request.POST.get(f"po_{key}") or "inherit").strip().lower()
        if v == "allow":
            overrides[key] = True
        elif v == "deny":
            overrides[key] = False
    return overrides


def _perm_override_rows_for_profile(profile) -> list:
    o: dict = {}
    if profile is not None:
        raw = getattr(profile, "perm_overrides", None)
        if isinstance(raw, dict):
            valid = {x for x, _, _, _ in ROLE_PERMISSION_MATRIX}
            o = {k: bool(v) for k, v in raw.items() if k in valid}
    rows = []
    for key, title, help_text, _cat in ROLE_PERMISSION_MATRIX:
        if key in o:
            state = "allow" if o[key] else "deny"
        else:
            state = "inherit"
        rows.append({"field": key, "title": title, "help": help_text, "state": state})
    return rows


def _require_api_login(request):
    """HTMLPDF API 统一登录检查：未登录时返回 JSON，而不是重定向 HTML 登录页。"""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return JsonResponse({"error": "unauthorized"}, status=401)
    return None


def login_view(request):
    """登录视图"""
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            if getattr(settings, "AUTH_SINGLE_WEB_SESSION_PER_USER", True):
                bind_web_session_for_user(user, request.session.session_key)
            return redirect('dashboard')
        else:
            messages.error(request, '用户名或密码错误')
    
    return render(request, 'login.html')


@login_required
def logout_view(request):
    """登出视图"""
    if request.user.is_authenticated:
        clear_web_session_lease(request.user)
    logout(request)
    return redirect('login')


@login_required
def dashboard(request):
    """仪表盘首页"""
    # 统计数据
    user_count = User.objects.count()
    role_count = Role.objects.count()
    menu_count = Menu.objects.count()
    library_upload_count = LibraryFile.objects.filter(category=LibraryFile.CATEGORY_UPLOAD).count()
    library_json_count = LibraryFile.objects.filter(category=LibraryFile.CATEGORY_JSON).count()

    context = {
        'user_count': user_count,
        'role_count': role_count,
        'menu_count': menu_count,
        'library_upload_count': library_upload_count,
        'library_json_count': library_json_count,
    }
    return render(request, 'dashboard.html', context)


_GUIDE_USAGE_SECTIONS = (
    ("overview", "整体工作顺序", "core/guide/overview.html"),
    ("project", "项目工作台", "core/guide/project.html"),
    ("files", "文件库", "core/guide/files.html"),
    ("tasks", "任务模板库", "core/guide/tasks.html"),
    ("htmlpdf", "模板编辑器", "core/guide/htmlpdf.html"),
    ("records", "现场记录与报告", "core/guide/records.html"),
    ("tips", "常见情况与排查", "core/guide/tips.html"),
)
_GUIDE_USAGE_LOOKUP = {slug: (title, tpl) for slug, title, tpl in _GUIDE_USAGE_SECTIONS}


@login_required
def backend_usage_guide(request, page=None):
    """后台系统使用说明（分页；面向新手；当前仅对部分引导账号开放）。"""
    if not library_user_has_party_a_demo_restrictions(request.user):
        messages.info(request, "当前账号暂不可查看该说明。")
        return redirect(reverse("dashboard"))
    show_task_nav = library_user_may_access_task_template_library_nav(request.user)
    nav_items = [{"slug": slug, "title": title} for slug, title, _tpl in _GUIDE_USAGE_SECTIONS]
    base_ctx = {
        "show_library_task_nav": show_task_nav,
        "guide_nav_items": nav_items,
    }
    if page is None:
        return render(
            request,
            "core/guide/index.html",
            {
                **base_ctx,
                "guide_page": "index",
            },
        )
    if page not in _GUIDE_USAGE_LOOKUP:
        raise Http404("未找到该说明页")
    title, template_name = _GUIDE_USAGE_LOOKUP[page]
    slugs = [s for s, _t, _p in _GUIDE_USAGE_SECTIONS]
    idx = slugs.index(page)
    prev_item = nav_items[idx - 1] if idx > 0 else None
    next_item = nav_items[idx + 1] if idx < len(nav_items) - 1 else None
    return render(
        request,
        template_name,
        {
            **base_ctx,
            "guide_page": page,
            "guide_section_title": title,
            "guide_prev": prev_item,
            "guide_next": next_item,
        },
    )


@login_required
@require_POST
def usage_workflow_tour_start(request):
    """开始「流程练习」会话：仅引导账号；会清理上次未结束的练习残留数据。"""
    if not library_user_has_party_a_demo_restrictions(request.user):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    ok = tour_start(request)
    if not ok:
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def usage_workflow_tour_finish(request):
    """结束练习：删除本会话在练习中登记的项目 / 任务模板 / 模板库文件，并清除会话键。"""
    if not library_user_has_party_a_demo_restrictions(request.user):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    stats = tour_cleanup_and_clear_session(request)
    return JsonResponse({"ok": True, "deleted": stats})


@login_required
def database_device_list(request):
    """数据库管理：检测仪器台账。"""
    if not library_user_may_access_instrument_database(request.user):
        return _require_perm(request, "perm_manage_users")

    action = (request.POST.get("action") or "").strip()
    if request.method == "POST" and action:
        code = (request.POST.get("code") or "").strip()
        name = (request.POST.get("name") or "").strip()
        model = (request.POST.get("model") or "").strip()
        calibration_org = (request.POST.get("calibration_org") or "").strip()
        certificate_no = (request.POST.get("certificate_no") or "").strip()
        certificate_valid_until = (request.POST.get("certificate_valid_until") or "").strip()
        remarks = (request.POST.get("remarks") or "").strip()

        if action == "create":
            if not code or not name:
                messages.error(request, "仪器编号和仪器设备名称不能为空")
                return redirect("database_device_list")
            if InstrumentCatalog.objects.filter(code=code).exists():
                messages.error(request, "仪器编号已存在")
                return redirect("database_device_list")
            InstrumentCatalog.objects.create(
                code=code,
                name=name,
                model=model,
                calibration_org=calibration_org,
                certificate_no=certificate_no,
                certificate_valid_until=certificate_valid_until or None,
                remarks=remarks,
                is_active=True,
            )
            messages.success(request, "检测仪器创建成功")
            return redirect("database_device_list")

        if action == "update":
            device_id = (request.POST.get("device_id") or "").strip()
            try:
                device = InstrumentCatalog.objects.get(id=int(device_id))
            except (ValueError, InstrumentCatalog.DoesNotExist):
                messages.error(request, "仪器不存在")
                return redirect("database_device_list")
            if not code or not name:
                messages.error(request, "仪器编号和仪器设备名称不能为空")
                return redirect("database_device_list")
            if InstrumentCatalog.objects.filter(code=code).exclude(id=device.id).exists():
                messages.error(request, "仪器编号已存在")
                return redirect("database_device_list")
            device.code = code
            device.name = name
            device.model = model
            device.calibration_org = calibration_org
            device.certificate_no = certificate_no
            device.certificate_valid_until = certificate_valid_until or None
            device.remarks = remarks
            device.save(update_fields=["code", "name", "model", "calibration_org", "certificate_no", "certificate_valid_until", "remarks", "updated_at"])
            messages.success(request, "检测仪器更新成功")
            return redirect("database_device_list")

        if action == "delete":
            device_id = (request.POST.get("device_id") or "").strip()
            try:
                device = InstrumentCatalog.objects.get(id=int(device_id))
            except (ValueError, InstrumentCatalog.DoesNotExist):
                messages.error(request, "仪器不存在")
                return redirect("database_device_list")
            device.delete()
            messages.success(request, "检测仪器删除成功")
            return redirect("database_device_list")

        if action == "instrument_checkout":
            device_id = (request.POST.get("device_id") or "").strip()
            project_id = (request.POST.get("project_id") or "").strip()
            try:
                inst = InstrumentCatalog.objects.get(pk=int(device_id), is_active=True)
                proj = LibraryProject.objects.get(pk=int(project_id), is_active=True)
            except (ValueError, InstrumentCatalog.DoesNotExist, LibraryProject.DoesNotExist):
                messages.error(request, "仪器或项目无效")
                return redirect("database_device_list")
            from apps.core.instrument_inventory_service import checkout_instruments_to_project

            n, errs = checkout_instruments_to_project(
                proj, [inst.pk], user=request.user, note="台账手动出库"
            )
            if errs:
                messages.error(request, errs[0])
            elif n:
                messages.success(request, f"已出库 {inst.code} 至项目 {proj.code}")
            return redirect("database_device_list")

        if action == "instrument_checkin":
            device_id = (request.POST.get("device_id") or "").strip()
            try:
                inst = InstrumentCatalog.objects.select_related("checkout_project").get(
                    pk=int(device_id)
                )
            except (ValueError, InstrumentCatalog.DoesNotExist):
                messages.error(request, "仪器不存在")
                return redirect("database_device_list")
            if not inst.checkout_project_id:
                messages.info(request, f"{inst.code} 已在库，无需入库")
                return redirect("database_device_list")
            from apps.core.instrument_inventory_service import checkin_instruments_from_project

            n = checkin_instruments_from_project(
                inst.checkout_project,
                [inst.pk],
                user=request.user,
                note="台账手动入库",
            )
            if n:
                messages.success(request, f"已入库 {inst.code}")
            return redirect("database_device_list")

    editing_device = None
    edit_id = (request.GET.get("edit") or "").strip()
    if edit_id:
        try:
            editing_device = InstrumentCatalog.objects.get(id=int(edit_id))
        except (ValueError, InstrumentCatalog.DoesNotExist):
            editing_device = None

    search_q = (request.GET.get("q") or "").strip()
    status_filter = (request.GET.get("status") or "").strip()

    devices_qs = InstrumentCatalog.objects.select_related(
        "checkout_project", "checked_out_by"
    )
    if search_q:
        from django.db.models import Q

        devices_qs = devices_qs.filter(
            Q(code__icontains=search_q)
            | Q(name__icontains=search_q)
            | Q(model__icontains=search_q)
            | Q(certificate_no__icontains=search_q)
        )
    if status_filter == "in_stock":
        devices_qs = devices_qs.filter(checkout_project__isnull=True)
    elif status_filter == "checked_out":
        devices_qs = devices_qs.exclude(checkout_project__isnull=True)

    devices = list(devices_qs.order_by("code", "id"))

    base_stats = InstrumentCatalog.objects.all()
    instrument_stats = {
        "total": base_stats.count(),
        "in_stock": base_stats.filter(checkout_project__isnull=True).count(),
        "checked_out": base_stats.exclude(checkout_project__isnull=True).count(),
        "filtered": len(devices),
    }

    active_projects = LibraryProject.objects.filter(is_active=True)
    if library_user_has_party_a_demo_restrictions(request.user):
        scoped = list(library_user_scoped_project_ids(request.user))
        if scoped:
            active_projects = active_projects.filter(pk__in=scoped)
    active_projects = list(active_projects.order_by("-updated_at")[:200])

    show_device_form = bool(editing_device) or (request.GET.get("new") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    context = {
        "devices": devices,
        "editing_device": editing_device,
        "active_projects": active_projects,
        "instrument_stats": instrument_stats,
        "search_q": search_q,
        "status_filter": status_filter,
        "show_device_form": show_device_form,
    }
    return render(request, "core/database_device_list.html", context)


@login_required
def user_list(request):
    """用户列表视图"""
    r = _require_perm(request, "perm_manage_users")
    if r:
        return r
    gx = _deny_party_a_demo_user_or_role_admin(request)
    if gx:
        return gx
    search = request.GET.get('search', '')
    page = request.GET.get('page', 1)
    
    # 搜索过滤（稳定排序，避免分页 UnorderedObjectListWarning）
    users = User.objects.select_related("profile").order_by("username", "id")
    if search:
        users = users.filter(
            Q(username__icontains=search) |
            Q(email__icontains=search) |
            Q(first_name__icontains=search)
        )
    
    # 分页
    paginator = Paginator(users, 20)
    users_page = paginator.get_page(page)
    
    context = {
        'users': users_page,
        'search': search,
    }
    return render(request, 'core/user_list.html', context)


@login_required
def user_create(request):
    """创建用户视图"""
    r = _require_perm(request, "perm_manage_users")
    if r:
        return r
    gx = _deny_party_a_demo_user_or_role_admin(request)
    if gx:
        return gx
    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        password = request.POST.get('password')
        first_name = request.POST.get('first_name', '')
        last_name = request.POST.get('last_name', '')
        phone = request.POST.get('phone', '')
        department = request.POST.get('department', '')
        position = request.POST.get('position', '')
        role_id = request.POST.get('role')
        
        # 验证
        if User.objects.filter(username=username).exists():
            messages.error(request, '用户名已存在')
            return redirect('user_create')
        
        # 创建用户与资料（同一事务）
        role = None
        if role_id:
            try:
                role = Role.objects.get(id=role_id)
            except (Role.DoesNotExist, ValueError):
                messages.error(request, "所选角色无效")
                return redirect("user_create")

        with transaction.atomic():
            user = User.objects.create_user(
                username=username,
                email=email or "",
                password=password,
                first_name=first_name,
                last_name=last_name,
            )
            # post_save 信号会为 User 自动 get_or_create UserProfile，此处只更新字段避免重复插入。
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.phone = phone or None
            profile.department = department or None
            profile.position = position or None
            profile.role = role
            profile.perm_overrides = _parse_perm_overrides_from_post(request)
            profile.save()
        
        messages.success(request, '用户创建成功')
        return redirect('user_list')
    
    roles = Role.objects.all()
    context = {
        'roles': roles,
        'perm_override_rows': _perm_override_rows_for_profile(None),
        'perm_override_disabled': False,
    }
    return render(request, 'core/user_form.html', context)


@login_required
def user_edit(request, user_id):
    """编辑用户视图"""
    r = _require_perm(request, "perm_manage_users")
    if r:
        return r
    gx = _deny_party_a_demo_user_or_role_admin(request)
    if gx:
        return gx
    user = get_object_or_404(User, id=user_id)
    profile, _ = UserProfile.objects.get_or_create(user=user)
    
    if request.method == 'POST':
        new_username = (request.POST.get("username") or "").strip()
        if not new_username:
            messages.error(request, "用户名不能为空")
            return redirect("user_edit", user_id=user_id)
        if new_username != user.username:
            if User.objects.filter(username=new_username).exclude(pk=user.pk).exists():
                messages.error(request, "用户名已存在")
                return redirect("user_edit", user_id=user_id)
            user.username = new_username

        user.email = request.POST.get('email', user.email)
        user.first_name = request.POST.get('first_name', user.first_name)
        user.last_name = request.POST.get('last_name', user.last_name)
        
        # 如果提供了新密码
        new_password = request.POST.get('password')
        if new_password:
            user.set_password(new_password)
        
        user.save()
        
        # 更新资料
        profile.phone = request.POST.get('phone', profile.phone)
        profile.department = request.POST.get('department', profile.department)
        profile.position = request.POST.get('position', profile.position)
        
        role_id = request.POST.get('role')
        if role_id:
            profile.role = Role.objects.get(id=role_id)
        
        skip_perm_overrides = user.is_superuser or (getattr(profile.role, "code", None) == "super_admin")
        if not skip_perm_overrides:
            profile.perm_overrides = _parse_perm_overrides_from_post(request)
        
        profile.save()
        
        messages.success(request, '用户更新成功')
        return redirect('user_list')
    
    roles = Role.objects.all()
    perm_override_disabled = user.is_superuser or (getattr(profile.role, "code", None) == "super_admin")
    context = {
        "edit_user": user,
        "profile": profile,
        "roles": roles,
        "perm_override_rows": _perm_override_rows_for_profile(profile),
        "perm_override_disabled": perm_override_disabled,
    }
    return render(request, "core/user_form.html", context)


@login_required
def user_delete(request, user_id):
    """删除用户视图"""
    r = _require_perm(request, "perm_manage_users")
    if r:
        return r
    gx = _deny_party_a_demo_user_or_role_admin(request)
    if gx:
        return gx
    user = get_object_or_404(User, id=user_id)
    
    if request.method == 'POST':
        user.delete()
        messages.success(request, '用户删除成功')
        return redirect('user_list')
    
    context = {
        'user': user,
    }
    return render(request, 'core/user_confirm_delete.html', context)


@login_required
def role_list(request):
    """角色列表视图"""
    r = _require_perm(request, "perm_manage_roles")
    if r:
        return r
    gx = _deny_party_a_demo_user_or_role_admin(request)
    if gx:
        return gx
    roles = list(
        Role.objects.annotate(user_count=Count("user_profiles", distinct=True)).order_by("code")
    )
    for r in roles:
        r.enterprise = role_enterprise_catalog(r.code)
    context = {
        "roles": roles,
    }
    return render(request, "core/role_list.html", context)


@login_required
def role_create(request):
    """创建角色视图"""
    r = _require_perm(request, "perm_manage_roles")
    if r:
        return r
    gx = _deny_party_a_demo_user_or_role_admin(request)
    if gx:
        return gx
    if request.method == 'POST':
        name = request.POST.get('name')
        code = request.POST.get('code')
        description = request.POST.get('description', '')
        
        # 验证
        if Role.objects.filter(code=code).exists():
            messages.error(request, '角色代码已存在')
            return redirect('role_create')
        
        defaults = dict(ROLE_DEFAULT_PERMS_BY_CODE.get(code, ROLE_DEFAULT_PERMS_BY_CODE["app_user"]))
        Role.objects.create(
            name=name,
            code=code,
            description=description,
            **defaults,
        )
        
        messages.success(request, '角色创建成功')
        return redirect('role_list')

    context = {"role_code_choices": Role.ROLE_CHOICES, "show_enterprise_hints": True}
    return render(request, 'core/role_form.html', context)


@login_required
def role_edit(request, role_id):
    """编辑角色视图"""
    r = _require_perm(request, "perm_manage_roles")
    if r:
        return r
    gx = _deny_party_a_demo_user_or_role_admin(request)
    if gx:
        return gx
    role = get_object_or_404(Role, id=role_id)

    if request.method == "POST":
        role.name = request.POST.get("name", role.name)
        role.description = request.POST.get("description", role.description)
        if role.code == "super_admin":
            for k, v in ROLE_DEFAULT_PERMS_BY_CODE["super_admin"].items():
                setattr(role, k, v)
        else:
            for key, _, _, _ in ROLE_PERMISSION_MATRIX:
                setattr(role, key, request.POST.get(key) == "on")
        role.save()
        messages.success(request, "角色更新成功")
        return redirect("role_list")

    perm_groups = role_permission_groups_for_edit(role)
    context = {
        "role": role,
        "perm_groups": perm_groups,
        "role_catalog": role_enterprise_catalog(role.code),
        "role_code_choices": Role.ROLE_CHOICES,
    }
    return render(request, "core/role_form.html", context)


@login_required
def role_delete(request, role_id):
    """删除角色视图"""
    r = _require_perm(request, "perm_manage_roles")
    if r:
        return r
    gx = _deny_party_a_demo_user_or_role_admin(request)
    if gx:
        return gx
    role = get_object_or_404(
        Role.objects.annotate(user_count=Count("user_profiles", distinct=True)),
        id=role_id,
    )
    if role.code == "super_admin":
        messages.error(request, "超级管理员为系统内置角色，不可删除")
        return redirect("role_list")

    if request.method == 'POST':
        role.delete()
        messages.success(request, '角色删除成功')
        return redirect('role_list')
    
    context = {
        'role': role,
    }
    return render(request, 'core/role_confirm_delete.html', context)


# 侧栏已由 context_processors.menu_context 注入 `menus`（已排除「文件与提取」）。
# 菜单管理页若仍使用键名 `menus`，会覆盖侧栏上下文，导致侧栏短暂出现多余项。
_LEGACY_SIDEBAR_MENU_EXCLUDE = ("文件与提取",)


def _menus_for_menu_admin():
    """菜单管理 CRUD 使用的顶级菜单列表（与侧栏展示策略一致）。"""
    return (
        Menu.objects.filter(parent=None)
        .exclude(name__in=_LEGACY_SIDEBAR_MENU_EXCLUDE)
        .prefetch_related("children")
        .order_by("sort_order", "id")
    )


@login_required
def menu_list(request):
    """菜单列表视图"""
    r = _require_perm(request, "perm_manage_menus")
    if r:
        return r
    admin_menus = _menus_for_menu_admin()
    context = {
        "admin_menus": admin_menus,
    }
    return render(request, 'core/menu_list.html', context)


@login_required
def menu_create(request):
    """创建菜单视图"""
    r = _require_perm(request, "perm_manage_menus")
    if r:
        return r
    if request.method == 'POST':
        name = request.POST.get('name')
        path = request.POST.get('path', '')
        icon = request.POST.get('icon', '')
        parent_id = request.POST.get('parent')
        sort_order = request.POST.get('sort_order', 0)
        
        # 创建菜单
        menu = Menu.objects.create(
            name=name,
            path=path,
            icon=icon,
            parent_id=parent_id if parent_id else None,
            sort_order=sort_order
        )
        
        # 设置角色权限
        role_ids = request.POST.getlist('roles')
        if role_ids:
            menu.roles.set(role_ids)
        
        messages.success(request, '菜单创建成功')
        return redirect('menu_list')
    
    admin_menus = _menus_for_menu_admin()
    roles = Role.objects.all()
    context = {
        "admin_menus": admin_menus,
        "roles": roles,
    }
    return render(request, 'core/menu_form.html', context)


@login_required
def menu_edit(request, menu_id):
    """编辑菜单视图"""
    r = _require_perm(request, "perm_manage_menus")
    if r:
        return r
    menu = get_object_or_404(Menu, id=menu_id)
    
    if request.method == 'POST':
        menu.name = request.POST.get('name', menu.name)
        menu.path = request.POST.get('path', menu.path)
        menu.icon = request.POST.get('icon', menu.icon)
        parent_id = request.POST.get('parent')
        menu.parent_id = parent_id if parent_id else None
        menu.sort_order = request.POST.get('sort_order', menu.sort_order)
        menu.is_visible = request.POST.get('is_visible') == 'on'
        menu.save()
        
        # 设置角色权限
        role_ids = request.POST.getlist('roles')
        menu.roles.set(role_ids)
        
        messages.success(request, '菜单更新成功')
        return redirect('menu_list')
    
    admin_menus = _menus_for_menu_admin()
    roles = Role.objects.all()
    context = {
        "menu": menu,
        "admin_menus": admin_menus,
        "roles": roles,
    }
    return render(request, 'core/menu_form.html', context)


@login_required
def menu_delete(request, menu_id):
    """删除菜单视图"""
    r = _require_perm(request, "perm_manage_menus")
    if r:
        return r
    menu = get_object_or_404(Menu, id=menu_id)
    
    if request.method == 'POST':
        menu.delete()
        messages.success(request, '菜单删除成功')
        return redirect('menu_list')
    
    context = {
        'menu': menu,
    }
    return render(request, 'core/menu_confirm_delete.html', context)


def _safe_filename(name: str) -> str:
    base = os.path.basename(name.replace("\\", "/"))
    return base[:240] if base else "unnamed"


def _gen_project_code() -> str:
    from apps.core.project_numbering import generate_library_project_code

    return generate_library_project_code()


_COMMISSION_ORG_UNSET_LABEL = "（未填写委托单位）"
_COMMISSION_ORG_URL_NONE = "__none__"


def _library_project_commission_org_display(project) -> str:
    org_fk = getattr(project, "commission_org", None)
    if org_fk is not None:
        return org_fk.full_display_name
    return (getattr(project, "commission_organization", None) or "").strip() or _COMMISSION_ORG_UNSET_LABEL


def _commission_org_url_slug(org_display: str) -> str:
    if org_display == _COMMISSION_ORG_UNSET_LABEL:
        return _COMMISSION_ORG_URL_NONE
    return org_display


def _commission_org_from_url_slug(slug: str) -> str:
    s = (slug or "").strip()
    if s == _COMMISSION_ORG_URL_NONE:
        return _COMMISSION_ORG_UNSET_LABEL
    return s


def _group_library_projects_by_commission_org(projects) -> list[tuple[str, list]]:
    """按委托单位名称分组，组内项目按编码排序。"""
    buckets: dict[str, list] = {}
    for p in projects:
        key = _library_project_commission_org_display(p)
        buckets.setdefault(key, []).append(p)
    for items in buckets.values():
        items.sort(key=lambda x: (x.code or "", x.name or ""))
    return sorted(buckets.items(), key=lambda kv: (kv[0] == _COMMISSION_ORG_UNSET_LABEL, kv[0]))


def _library_projects_filter_by_commission_org(qs, org_display: str):
    if not org_display:
        return qs
    if org_display == _COMMISSION_ORG_UNSET_LABEL:
        return qs.filter(commission_organization="")
    return qs.filter(commission_organization=org_display)


def _library_project_ids_for_commission_org_display(org_display: str, projects_qs) -> list[int]:
    if not org_display:
        return []
    return list(_library_projects_filter_by_commission_org(projects_qs, org_display).values_list("pk", flat=True))


def _request_wants_json(request) -> bool:
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    accept = request.headers.get("Accept") or ""
    return "application/json" in accept


def _action_json_or_redirect(request, *, ok: bool, message: str, redirect_url: str, extra: dict | None = None):
    if _request_wants_json(request):
        payload = {"ok": ok, "message": message, "redirect": redirect_url}
        if extra:
            payload.update(extra)
        return JsonResponse(payload, status=200 if ok else 400)
    if ok:
        messages.success(request, message)
    else:
        messages.error(request, message)
    return redirect(redirect_url)


def _can_manage_commission_organizations(user) -> bool:
    return library_user_can_assign_tasks_to_participants(user) or role_has(
        user, "perm_create_library_project"
    )


def _resolve_or_create_commission_org(
    user,
    *,
    org_id_raw: str = "",
    org_name: str = "",
    hospital_name: str = "",
    campus_name: str = "",
    department_name: str = "",
) -> CommissionOrganization | None:
    oid_s = (org_id_raw or "").strip()
    if oid_s:
        try:
            org = CommissionOrganization.objects.filter(pk=int(oid_s), is_active=True).first()
            if org:
                return org
        except (TypeError, ValueError):
            pass
    h = (hospital_name or org_name or "").strip()
    if h:
        return find_or_create_commission_org_chain(
            user,
            hospital_name=h,
            campus_name=campus_name,
            department_name=department_name,
        )
    return None


def _sync_project_commission_org_name(project: LibraryProject) -> None:
    if project.commission_org_id:
        nm = project.commission_org.full_display_name
        if nm != (project.commission_organization or "").strip():
            project.commission_organization = nm
            project.save(update_fields=["commission_organization", "updated_at"])


def _sync_library_project_tasks_to_user(project, assignee, assigned_by):
    """
    将项目下任务模板同步给指定用户（幂等），并登记流程参与、模板文件关联。
    返回 (created_count, template_file_count)。
    """
    from apps.core.project_equipment_service import project_tasks_for_user_assignment

    project_tasks = project_tasks_for_user_assignment(project)
    if not project_tasks:
        return 0, 0
    created_count = 0
    all_file_ids: set[int] = set()
    for library_task in project_tasks:
        exists = LibraryTaskAssignment.objects.filter(
            library_task=library_task,
            project=project,
            assignee=assignee,
        ).exists()
        if not exists:
            LibraryTaskAssignment.objects.create(
                library_task=library_task,
                project=project,
                assignee=assignee,
                assigned_by=assigned_by,
            )
            created_count += 1
        for fid in library_task.library_files.filter(category=LibraryFile.CATEGORY_TEMPLATE).values_list(
            "pk", flat=True
        ):
            all_file_ids.add(fid)
    LibraryProjectWorkflowMember.ensure_for_project_assignment(project, assignee)
    if all_file_ids:
        attach_files_to_projects(sorted(all_file_ids), [project.pk], assigned_by)
    return created_count, len(all_file_ids)


@login_required
def library_projects(request):
    gx = _require_perm(request, "perm_file_library")
    if gx:
        return gx
    commission_org_raw = request.GET.get("commission_org", "").strip()
    selected_commission_org = _commission_org_from_url_slug(commission_org_raw) if commission_org_raw else ""
    selected_raw = request.GET.get("project_id", "").strip()
    try:
        selected_project_id = int(selected_raw) if selected_raw else None
    except ValueError:
        selected_project_id = None
    selected_project = (
        LibraryProject.objects.filter(pk=selected_project_id)
        .select_related("created_by", "primary_responsible")
        .first()
        if selected_project_id
        else None
    )
    hide_project_workbench_files_tab = library_user_hide_project_workbench_files_tab(
        request.user, selected_project
    )
    if (
        selected_project is not None
        and library_scope_own_files_only(request.user)
        and not library_user_can_assign_tasks_to_participants(request.user)
    ):
        allowed = set(library_user_scoped_project_ids(request.user))
        if selected_project.pk not in allowed:
            messages.warning(request, "无权访问该项目，请在左侧选择已分配或您本人创建的项目。")
            selected_project = None
            selected_project_id = None

    if request.method == "POST":
        action = request.POST.get("action", "").strip()
        if action in (
            "create_commission_organization",
            "update_commission_organization",
            "move_commission_org_parent",
        ):
            messages.error(
                request,
                "委托单位组织架构请在「医院信息管理」中维护，项目工作台仅用于浏览与选择。",
            )
            fl = (request.POST.get("fl_path") or "").strip()
            q = reverse("library_projects") + (f"?fl_path={quote(fl)}" if fl else "")
            return redirect(q)

        if action == "move_project_to_commission_org":
            if not _can_manage_commission_organizations(request.user):
                return _action_json_or_redirect(
                    request,
                    ok=False,
                    message="当前角色无权移动项目",
                    redirect_url=reverse("library_projects"),
                )
            try:
                pid = int(request.POST.get("project_id", "") or 0)
                oid = int(request.POST.get("target_org_id", "") or 0)
            except ValueError:
                pid, oid = 0, 0
            proj = LibraryProject.objects.filter(pk=pid, is_active=True).first()
            org = CommissionOrganization.objects.filter(pk=oid, is_active=True).first()
            fl = (request.POST.get("fl_path") or "").strip()
            redir = reverse("library_projects") + (f"?fl_path={quote(fl)}" if fl else "")
            if proj is None or org is None:
                return _action_json_or_redirect(
                    request, ok=False, message="项目或目标委托单位无效", redirect_url=redir
                )
            if not library_user_may_mutate_project_workbench(request.user, proj):
                return _action_json_or_redirect(
                    request, ok=False, message="无权移动该项目", redirect_url=redir
                )
            err = move_project_to_commission_org(proj, org)
            if err:
                return _action_json_or_redirect(request, ok=False, message=err, redirect_url=redir)
            redir = reverse("library_projects") + f"?fl_path={quote(org.folder_path())}"
            return _action_json_or_redirect(
                request,
                ok=True,
                message=f"已将项目「{proj.name}」移至「{org.full_display_name}」",
                redirect_url=redir,
            )

        if action == "move_commission_org_parent":
            if not _can_manage_commission_organizations(request.user):
                return _action_json_or_redirect(
                    request,
                    ok=False,
                    message="当前角色无权移动委托单位",
                    redirect_url=reverse("library_projects"),
                )
            try:
                oid = int(request.POST.get("org_id", "") or 0)
                parent_id = int(request.POST.get("target_org_id", "") or 0)
            except ValueError:
                oid, parent_id = 0, 0
            org = CommissionOrganization.objects.filter(pk=oid, is_active=True).first()
            parent = CommissionOrganization.objects.filter(pk=parent_id, is_active=True).first()
            fl = (request.POST.get("fl_path") or "").strip()
            redir = reverse("library_projects") + (f"?fl_path={quote(fl)}" if fl else "")
            if org is None or parent is None:
                return _action_json_or_redirect(
                    request, ok=False, message="委托单位无效", redirect_url=redir
                )
            err = move_commission_org_to_parent(org, parent)
            if err:
                return _action_json_or_redirect(request, ok=False, message=err, redirect_url=redir)
            redir = reverse("library_projects") + f"?fl_path={quote(org.folder_path())}"
            return _action_json_or_redirect(
                request,
                ok=True,
                message=f"已移动「{org.full_display_name}」",
                redirect_url=redir,
            )

        if action == "update_project_meta":
            try:
                pid = int(request.POST.get("project_id", "") or 0)
            except ValueError:
                pid = 0
            proj = LibraryProject.objects.filter(pk=pid).first()
            if proj is None:
                messages.error(request, "项目不存在")
                return redirect(reverse("library_projects"))
            if not library_user_may_mutate_project_workbench(request.user, proj):
                messages.error(request, "无权编辑该项目")
                return redirect(reverse("library_projects") + f"?project_id={pid}")
            new_name = request.POST.get("project_name", "").strip()
            org_id_raw = (request.POST.get("commission_org_id") or "").strip()
            org = None
            if org_id_raw:
                try:
                    org = CommissionOrganization.objects.filter(
                        pk=int(org_id_raw), is_active=True
                    ).first()
                except (TypeError, ValueError):
                    org = None
            if org is None:
                messages.error(request, "请选择委托单位（可在医院、院区或科室任一层级保存）")
                return redirect(
                    reverse("library_projects")
                    + f"?project_id={pid}&tab={request.POST.get('tab', 'overview')}"
                )
            if new_name:
                proj.name = new_name
            proj.commission_org = org
            proj.commission_organization = org.full_display_name
            proj.save()
            messages.success(request, "已保存项目信息")
            if proj.commission_org_id:
                fl = f"{proj.commission_org.folder_path()}/p-{proj.pk}"
            else:
                fl = f"o-none/p-{proj.pk}"
            return redirect(
                reverse("library_projects")
                + f"?fl_path={quote(fl)}&project_id={proj.pk}&tab={request.POST.get('tab', 'overview')}"
            )

        if action == "create_project":
            if not (
                library_user_can_assign_tasks_to_participants(request.user)
                or role_has(request.user, "perm_create_library_project")
            ):
                messages.error(request, "当前角色无权创建项目")
                return redirect(reverse("library_projects"))
            org_id_raw = (request.POST.get("commission_org_id") or "").strip()
            org = None
            if org_id_raw:
                try:
                    org = CommissionOrganization.objects.filter(
                        pk=int(org_id_raw), is_active=True
                    ).first()
                except (TypeError, ValueError):
                    org = None
            name = request.POST.get("project_name", "").strip()
            code = _gen_project_code()
            if org is None:
                messages.error(
                    request,
                    "请选择委托单位；新建医院/院区/科室请前往「医院信息管理」。",
                )
                return redirect(reverse("library_projects"))
            if not name:
                messages.error(request, "项目名称不能为空")
                return redirect(reverse("library_projects"))
            elif LibraryProject.objects.filter(code=code).exists():
                messages.error(request, "项目编码生成冲突，请重试")
                return redirect(reverse("library_projects"))
            else:
                row = LibraryProject.objects.create(
                    code=code,
                    name=name,
                    commission_org=org,
                    commission_organization=org.full_display_name,
                    created_by=request.user,
                )
                register_tour_project(request, row.pk)
                task_ids = []
                for x in request.POST.getlist("task_ids"):
                    try:
                        task_ids.append(int(x))
                    except (TypeError, ValueError):
                        continue
                task_ids = list(dict.fromkeys([i for i in task_ids if i > 0]))
                if task_ids:
                    tasks = list(LibraryTask.objects.filter(pk__in=task_ids))
                    if len(tasks) == len(task_ids):
                        row.library_tasks.set(tasks)
                        # 新建项目时若已选择任务，仅同步任务模板上的「模板」文件到项目（其它文件在项目页单独维护）。
                        all_file_ids = set()
                        for t in tasks:
                            all_file_ids.update(
                                t.library_files.filter(category=LibraryFile.CATEGORY_TEMPLATE).values_list(
                                    "id", flat=True
                                )
                            )
                        if all_file_ids:
                            attach_files_to_projects(sorted(all_file_ids), [row.pk], request.user)
                messages.success(
                    request,
                    f"已创建项目：{row.commission_organization} · {row.name}（{row.code}）",
                )
                fl = f"{row.commission_org.folder_path()}/p-{row.pk}"
                return redirect(
                    reverse("library_projects")
                    + f"?fl_path={quote(fl)}&project_id={row.pk}&tab=commission"
                )

        if action in (
            "bind_project_equipments",
            "unbind_project_equipment",
            "update_project_equipment_report_task",
        ):
            post_raw = request.POST.get("project_id", "").strip()
            try:
                post_pid = int(post_raw) if post_raw else None
            except ValueError:
                post_pid = None
            proj = LibraryProject.objects.filter(pk=post_pid).first() if post_pid else None
            if proj is None:
                proj = selected_project
            if proj is None:
                messages.error(request, "请先在上方选择项目")
                return redirect(reverse("library_projects"))
            if not library_user_may_mutate_project_workbench(request.user, proj):
                messages.error(request, "当前角色无权维护项目委托设备")
                return redirect(reverse("library_projects") + f"?project_id={proj.pk}&tab=commission")
            post_fl = (request.POST.get("fl_path") or "").strip()
            tab_q = f"?project_id={proj.pk}&tab=commission"
            if post_fl:
                tab_q += f"&fl_path={quote(post_fl)}"

            if action == "bind_project_equipments":
                raw_ids = []
                for x in request.POST.getlist("equipment_ids"):
                    try:
                        raw_ids.append(int(x))
                    except (TypeError, ValueError):
                        continue
                raw_ids = list(dict.fromkeys([i for i in raw_ids if i > 0]))
                if not raw_ids:
                    messages.error(request, "请至少勾选一台设备")
                else:
                    scope_org = resolve_workbench_equipment_scope_org(proj, fl_path=post_fl)
                    added, errs = bind_equipments_to_project(
                        proj, raw_ids, request.user, scope_org=scope_org, fl_path=post_fl
                    )
                    if added:
                        messages.success(request, f"已向本次委托加入 {added} 台设备，相关任务模板已同步到项目")
                    for e in errs:
                        messages.warning(request, e)
                    if not added and not errs:
                        messages.info(request, "所选设备均已在本项目中")
                    if added or LibraryProjectEquipment.objects.filter(project=proj).exists():
                        from apps.core.hospital_info_service import (
                            sync_project_library_tasks_from_equipments,
                        )

                        n_sync = sync_project_library_tasks_from_equipments(
                            proj, request.user
                        )
                        if n_sync and added:
                            messages.info(
                                request,
                                f"项目任务模板已按当前委托设备整理为 {n_sync} 个（已移除无关残留）",
                            )
            elif action == "unbind_project_equipment":
                try:
                    link_id = int(request.POST.get("link_id", "") or 0)
                except ValueError:
                    link_id = 0
                err = unbind_equipment_from_project(proj, link_id, request.user)
                if err:
                    messages.error(request, err)
                else:
                    messages.success(request, "已从本次委托中移除该设备")
            else:
                try:
                    link_id = int(request.POST.get("link_id", "") or 0)
                except ValueError:
                    link_id = 0
                try:
                    task_id = int(request.POST.get("report_task_id", "") or 0)
                except ValueError:
                    task_id = 0
                err = update_project_equipment_report_task(
                    proj, link_id, task_id or None, request.user
                )
                if err:
                    messages.error(request, err)
                else:
                    messages.success(request, "已更新该设备的检测任务模板并同步到项目")
            return redirect(reverse("library_projects") + tab_q)

        if action == "set_primary_responsible":
            project_raw = request.POST.get("project_id", "").strip()
            assignee_raw = request.POST.get("assignee", "").strip()
            project = None
            project_id = 0
            if not assignee_raw:
                messages.error(request, "请选择主要负责人")
            elif not project_raw:
                messages.error(request, "请选择项目")
            else:
                try:
                    assignee_id = int(assignee_raw)
                except ValueError:
                    assignee_id = 0
                try:
                    project_id = int(project_raw)
                except ValueError:
                    project_id = 0
                project = LibraryProject.objects.filter(pk=project_id, is_active=True).first()
                if project is None:
                    messages.error(request, "请选择有效项目")
                    return redirect(reverse("library_projects"))
                if not library_user_may_assign_on_project(request.user, project):
                    messages.error(request, "无权指定该项目主要负责人")
                    return redirect(reverse("library_projects") + f"?project_id={project.pk}&tab=dispatch")
                assignee = User.objects.filter(
                    pk=assignee_id,
                    profile__role__code__in=APP_SIDE_ROLE_CODES,
                    is_active=True,
                ).first()
                if assignee is None:
                    messages.error(request, "主要负责人须为已启用的检测流程参与人账号")
                elif not project.library_tasks.exists():
                    messages.error(request, "该项目尚未关联任务模板，请先在「任务与模板」中为项目勾选任务模板")
                else:
                    project.primary_responsible = assignee
                    project.save(update_fields=["primary_responsible", "updated_at"])
                    messages.success(
                        request,
                        f"已指定 {assignee.username} 为项目「{project.name}」主要负责人。"
                        "请在下方「向参与人同步任务」中为其或检测人员手动分配任务。",
                    )
            return redirect(
                reverse("library_projects")
                + f"?project_id={project.pk if project else project_id}&tab=dispatch"
            )

        if action == "assign":
            project_raw = request.POST.get("project_id", "").strip()
            assignee_raw = request.POST.get("assignee", "").strip()
            if not assignee_raw:
                messages.error(request, "请选择接收用户")
            elif not project_raw:
                messages.error(request, "请选择项目")
            else:
                try:
                    assignee_id = int(assignee_raw)
                except ValueError:
                    assignee_id = 0
                try:
                    project_id = int(project_raw)
                except ValueError:
                    project_id = 0
                project = LibraryProject.objects.filter(pk=project_id, is_active=True).first()
                if project is None:
                    messages.error(request, "请选择有效项目")
                    return redirect(reverse("library_projects"))
                if not library_user_may_mutate_project_workbench(request.user, project):
                    messages.error(request, "无权在此项目中操作分配")
                    return redirect(reverse("library_projects"))
                if not library_user_may_assign_on_project(request.user, project):
                    if assignee_id != request.user.id:
                        messages.error(request, "无分配权限：仅可向本人同步本项目任务")
                        return redirect(reverse("library_projects") + f"?project_id={project.pk}&tab=dispatch")
                assignee = User.objects.filter(
                    pk=assignee_id,
                    profile__role__code__in=APP_SIDE_ROLE_CODES,
                    is_active=True,
                ).first()
                if assignee is None:
                    messages.error(request, "接收用户必须是已启用的检测流程参与人（检测员/校核员/编制人等）")
                else:
                    project_tasks = list(project.library_tasks.all().order_by("code"))
                    if not project_tasks:
                        messages.error(request, "该项目尚未关联任务模板，请先在「任务与模板」中为项目勾选任务模板")
                    else:
                        from apps.core.instrument_inventory_service import (
                            ensure_instruments_for_project_dispatch,
                        )

                        inv = ensure_instruments_for_project_dispatch(
                            project, user=request.user
                        )
                        if not inv.ok:
                            messages.error(request, inv.message)
                        else:
                            if inv.checked_out_count:
                                messages.info(request, inv.message)
                            created_count, file_n = _sync_library_project_tasks_to_user(
                                project, assignee, request.user
                            )
                            if created_count:
                                messages.success(
                                    request,
                                    f"已向 {assignee.username} 分配项目「{project.name}」下 {created_count} 个任务模板，"
                                    f"并同步 {file_n} 个模板文件到项目。",
                                )
                            else:
                                messages.info(
                                    request,
                                    f"{assignee.username} 已拥有该项目全部任务模板；已同步 {file_n} 个模板文件到项目。",
                                )
            redir_pid = int(project_raw) if (project_raw or "").strip().isdigit() else (selected_project_id or 0)
            tab_q = f"?project_id={redir_pid}&tab=dispatch" if redir_pid else "?tab=dispatch"
            return redirect(reverse("library_projects") + tab_q)

        if action == "unassign_project":
            project_raw = request.POST.get("project_id", "").strip()
            assignee_raw = request.POST.get("assignee_id", "").strip()
            try:
                project_id = int(project_raw)
            except ValueError:
                project_id = 0
            try:
                assignee_id = int(assignee_raw)
            except ValueError:
                assignee_id = 0
            project = LibraryProject.objects.filter(pk=project_id).first() if project_id else None
            assignee = (
                User.objects.filter(pk=assignee_id, is_active=True).select_related("profile__role").first()
                if assignee_id
                else None
            )
            if project is None or assignee is None:
                messages.error(request, "撤回失败：项目或用户无效")
                return redirect(reverse("library_projects"))
            if not LibraryTaskAssignment.objects.filter(project=project, assignee=assignee).exists():
                messages.error(request, "撤回失败：该用户在本项目上无任务分配记录")
                return redirect(reverse("library_projects") + f"?project_id={project.pk}&tab=dispatch")
            if not library_user_may_mutate_project_workbench(request.user, project):
                messages.error(request, "无权撤回此项目下的分配")
                return redirect(reverse("library_projects") + f"?project_id={project.pk}&tab=dispatch")
            if not library_user_may_assign_on_project(request.user, project):
                if assignee_id != request.user.id:
                    messages.error(request, "仅能撤回本人在本项目上的任务分配")
                    return redirect(reverse("library_projects") + f"?project_id={project.pk}&tab=dispatch")
            deleted_count, _ = LibraryTaskAssignment.objects.filter(
                project=project,
                assignee=assignee,
            ).delete()
            if deleted_count:
                messages.success(request, f"已撤回 {assignee.username} 在项目「{project.name}」上的分配")
            else:
                messages.info(request, f"{assignee.username} 在项目「{project.name}」上无可撤回分配")
            return redirect(reverse("library_projects") + f"?project_id={project.pk}&tab=dispatch")

        if action in ("bind_project_files", "unbind_project_files"):
            post_raw = request.POST.get("project_id", "").strip()
            try:
                post_pid = int(post_raw) if post_raw else None
            except ValueError:
                post_pid = None
            proj = LibraryProject.objects.filter(pk=post_pid).first() if post_pid else None
            if proj is None:
                proj = selected_project
            if proj is None:
                messages.error(request, "请先在上方选择项目")
                return redirect(reverse("library_projects"))
            if hide_project_workbench_files_tab:
                messages.warning(
                    request,
                    "当前账号不在此维护项目文件；请在「委托情况」绑定设备，模板文件会随检测任务同步到项目。",
                )
                return redirect(reverse("library_projects") + f"?project_id={proj.pk}&tab=commission")
            if not library_user_may_edit_project_files(request.user, proj):
                messages.error(request, "无权维护项目文件（统筹人请在「人员派工」页面向参与人同步 App 任务）")
                return redirect(reverse("library_projects") + f"?project_id={proj.pk}&tab=commission")
            if not library_user_may_mutate_project_workbench(request.user, proj):
                messages.error(request, "当前角色无权维护项目文件")
                return redirect(reverse("library_projects") + f"?project_id={proj.pk}")

            file_tab = _normalize_library_tab(request.POST.get("project_file_tab", "ocr"))
            file_cat = _library_category_for_tab(file_tab)
            raw_ids = []
            for x in request.POST.getlist("file_ids"):
                try:
                    raw_ids.append(int(x))
                except (TypeError, ValueError):
                    continue
            raw_ids = list(dict.fromkeys([i for i in raw_ids if i > 0]))
            if not raw_ids:
                messages.error(request, "请至少勾选一个文件")
            else:
                qs = LibraryFile.objects.filter(pk__in=raw_ids, category=file_cat)
                if library_scope_own_files_only(request.user) and not library_signatory_assigned_project_selected(
                    request.user, proj.pk
                ):
                    qs = qs.filter(created_by=request.user)
                valid_ids = list(qs.values_list("id", flat=True))
                if len(valid_ids) != len(raw_ids):
                    messages.error(request, "存在无效文件，或文件分类与当前标签不一致")
                elif action == "bind_project_files":
                    attach_files_to_projects(valid_ids, [proj.pk], request.user)
                    messages.success(request, f"已向项目关联 {len(valid_ids)} 个文件")
                else:
                    detach_files_from_projects(valid_ids, [proj.pk])
                    messages.success(request, f"已从项目移除 {len(valid_ids)} 个文件")
            return redirect(reverse("library_projects") + f"?project_id={proj.pk}&project_file_tab={file_tab}&tab=files")

        if action in ("revoke_project_user", "restore_project_user"):
            post_raw = request.POST.get("project_id", "").strip()
            try:
                post_pid = int(post_raw) if post_raw else None
            except ValueError:
                post_pid = None
            proj = LibraryProject.objects.filter(pk=post_pid).first() if post_pid else None
            if proj is None:
                proj = selected_project
            if proj is None:
                messages.error(request, "请选择项目")
                return redirect(reverse("library_projects"))
            if not library_user_may_assign_on_project(request.user, proj):
                messages.error(request, "无权操作")
                return redirect(reverse("library_projects") + f"?project_id={proj.pk}")
            try:
                uid = int(request.POST.get("user_id", "") or 0)
            except ValueError:
                uid = 0
            target = User.objects.filter(pk=uid, is_active=True).select_related("profile__role").first()
            if target is None:
                messages.error(request, "用户不存在或未启用")
            elif not LibraryTaskAssignment.objects.filter(project=proj, assignee=target).exists():
                messages.error(request, "该用户在本项目上无任务分配，无法取消或恢复编辑权限")
            elif action == "revoke_project_user":
                LibraryProjectUserRevocation.objects.get_or_create(
                    project=proj,
                    user=target,
                    defaults={"revoked_by": request.user},
                )
                messages.success(request, f"已取消 {target.username} 在本项目上的编辑权限（仍可下载）。")
            else:
                LibraryProjectUserRevocation.objects.filter(project=proj, user=target).delete()
                messages.success(request, f"已恢复 {target.username} 在本项目上的编辑权限。")
            return redirect(reverse("library_projects") + f"?project_id={proj.pk}&tab=dispatch")

        if action == "rollback_submission_to_pending":
            post_raw = request.POST.get("project_id", "").strip()
            try:
                post_pid = int(post_raw) if post_raw else None
            except ValueError:
                post_pid = None
            proj = LibraryProject.objects.filter(pk=post_pid).first() if post_pid else None
            if proj is None:
                proj = selected_project
            if proj is None:
                messages.error(request, "请选择项目")
                return redirect(reverse("library_projects"))
            if not library_user_may_mutate_project_workbench(request.user, proj):
                messages.error(request, "无权操作任务状态")
                return redirect(reverse("library_projects") + f"?project_id={proj.pk}")
            try:
                sid = int(request.POST.get("submission_id", "") or 0)
            except ValueError:
                sid = 0
            sub = InspectionSubmission.objects.filter(pk=sid, project=proj).first() if sid else None
            if sub is None:
                messages.error(request, "提交记录不存在或不属于当前项目")
            elif sub.status != InspectionSubmission.STATUS_SUBMITTED:
                messages.error(request, "仅已提交（submitted）状态可回退为 pending")
            else:
                sub.status = InspectionSubmission.STATUS_PENDING
                sub.submitted_at = None
                sub.save(update_fields=["status", "submitted_at", "updated_at"])
                messages.success(request, f"已将任务 {sub.task_no} 从 submitted 回退为 pending。")
            return redirect(reverse("library_projects") + f"?project_id={proj.pk}&tab=submissions")

        if action in ("add_workflow_member", "remove_workflow_member"):
            post_raw = request.POST.get("project_id", "").strip()
            try:
                post_pid = int(post_raw) if post_raw else None
            except ValueError:
                post_pid = None
            proj = LibraryProject.objects.filter(pk=post_pid).first() if post_pid else None
            if proj is None:
                proj = selected_project
            if proj is None:
                messages.error(request, "请选择项目")
                return redirect(reverse("library_projects"))
            if not library_user_may_assign_on_project(request.user, proj):
                messages.error(request, "无权维护项目流程成员（须为高权限账号或本项目主要负责人）")
                return redirect(reverse("library_projects") + f"?project_id={proj.pk}")
            if action == "add_workflow_member":
                try:
                    uid = int(request.POST.get("user_id", "") or 0)
                except ValueError:
                    uid = 0
                wf_role = (request.POST.get("workflow_role") or "").strip()
                valid_roles = {c for c, _ in LibraryProjectWorkflowMember.WORKFLOW_ROLE_CHOICES}
                if uid <= 0 or wf_role not in valid_roles:
                    messages.error(request, "请选择用户与有效的流程岗位")
                else:
                    u = User.objects.filter(pk=uid, is_active=True).select_related("profile__role").first()
                    if u is None:
                        messages.error(request, "用户不存在或未启用")
                    elif not getattr(u, "profile", None) or not u.profile.role:
                        messages.error(request, "该用户未绑定角色")
                    elif library_user_has_party_a_demo_restrictions(request.user) and u.id != request.user.id:
                        messages.error(request, "演示账号仅可将流程岗位分配给本人")
                    elif u.profile.role.code not in APP_SIDE_ROLE_CODES:
                        messages.error(request, "所选用户角色不属于可参与检测流程的账号")
                    else:
                        _, created = LibraryProjectWorkflowMember.objects.get_or_create(
                            project=proj,
                            user=u,
                            workflow_role=wf_role,
                        )
                        if created:
                            messages.success(
                                request,
                                f"已添加流程参与人：{u.username} → {dict(LibraryProjectWorkflowMember.WORKFLOW_ROLE_CHOICES).get(wf_role, wf_role)}",
                            )
                        else:
                            messages.info(request, f"{u.username} 已在该岗位登记，无需重复添加")
            else:
                try:
                    mid = int(request.POST.get("member_id", "") or 0)
                except ValueError:
                    mid = 0
                row = LibraryProjectWorkflowMember.objects.filter(pk=mid, project=proj).first()
                if row is None:
                    messages.error(request, "记录不存在")
                elif library_user_has_party_a_demo_restrictions(request.user) and row.user_id != request.user.id:
                    messages.error(request, "演示账号仅可移除本人担任的流程岗位")
                else:
                    row.delete()
            return redirect(reverse("library_projects") + f"?project_id={proj.pk}&tab=dispatch")

        if action == "instrument_checkin_project":
            post_raw = request.POST.get("project_id", "").strip()
            try:
                post_pid = int(post_raw) if post_raw else None
            except ValueError:
                post_pid = None
            proj = LibraryProject.objects.filter(pk=post_pid).first() if post_pid else None
            if proj is None:
                proj = selected_project
            if proj is None:
                messages.error(request, "请选择项目")
                return redirect(reverse("library_projects"))
            if not library_user_may_mutate_project_workbench(request.user, proj):
                messages.error(request, "无权操作本项目仪器入库")
            else:
                from apps.core.instrument_inventory_service import checkin_instruments_from_project

                n = checkin_instruments_from_project(
                    proj, None, user=request.user, note="项目工作台手动入库"
                )
                messages.success(
                    request,
                    f"已入库 {n} 台仪器（解除与项目「{proj.name}」的出库绑定）",
                )
            return redirect(reverse("library_projects") + f"?project_id={proj.pk}&tab=dispatch")

        if action == "delete_project":
            post_raw = request.POST.get("project_id", "").strip()
            try:
                post_pid = int(post_raw) if post_raw else None
            except ValueError:
                post_pid = None
            proj = LibraryProject.objects.filter(pk=post_pid).first() if post_pid else None
            if proj is None:
                messages.error(request, "项目不存在")
                return redirect(reverse("library_projects"))
            can_delete = library_user_can_assign_tasks_to_participants(request.user)
            if (
                not can_delete
                and role_has(request.user, "perm_create_library_project")
                and getattr(proj, "created_by_id", None) == request.user.id
            ):
                can_delete = True
            if not can_delete:
                messages.error(request, "当前角色无权删除项目")
                return redirect(reverse("library_projects"))
            label = f"{proj.code} · {proj.name}"
            try:
                proj.delete()
            except ProtectedError:
                messages.error(
                    request,
                    "项目删除失败：该项目已被检测提交等记录引用，请先清理关联数据后再删除。",
                )
                return redirect(reverse("library_projects") + f"?project_id={proj.pk}")
            messages.success(request, f"已删除项目：{label}")
            return redirect(reverse("library_projects"))

    all_library_tasks = LibraryTask.objects.order_by("code")
    if not _librarytask_has_report_source_relation():
        all_library_tasks = all_library_tasks.only("id", "code", "name", "output_target")
    if library_user_is_template_editor(request.user):
        all_library_tasks = all_library_tasks.filter(created_by=request.user)
    project_file_tab = _normalize_library_tab(request.GET.get("project_file_tab", "ocr"))
    project_file_cat = _library_category_for_tab(project_file_tab)
    project_task_ids = set()
    project_report_tasks = []
    project_site_tasks = []
    selected_report_task_id = ""
    selected_report_source_ids = set()
    project_file_ids = set()
    project_file_rows = []
    if selected_project:
        project_task_ids = set(selected_project.library_tasks.values_list("id", flat=True))
        if _librarytask_has_report_source_relation():
            project_site_tasks = list(
                selected_project.library_tasks.filter(output_target=LibraryTask.OUTPUT_SITE_RECORD).order_by("code", "id")
            )
            project_site_task_ids = [x.id for x in project_site_tasks]
            project_report_tasks = list(
                selected_project.library_tasks.filter(output_target=LibraryTask.OUTPUT_REPORT).order_by("code", "id")
            )
            selected_report_raw = (request.GET.get("report_task_id") or "").strip()
            try:
                selected_report_int = int(selected_report_raw) if selected_report_raw else 0
            except ValueError:
                selected_report_int = 0
            if not selected_report_int and project_report_tasks:
                selected_report_int = project_report_tasks[0].id
            selected_report_obj = next((x for x in project_report_tasks if x.id == selected_report_int), None)
            if selected_report_obj:
                selected_report_task_id = str(selected_report_obj.id)
                selected_report_source_ids = set(
                    selected_report_obj.report_source_tasks.filter(pk__in=project_site_task_ids).values_list("id", flat=True)
                )
        fq = LibraryFile.objects.filter(category=project_file_cat).select_related("created_by").order_by("-created_at")
        if library_scope_own_files_only(request.user) and not library_signatory_assigned_project_selected(
            request.user, selected_project.pk
        ):
            fq = fq.filter(created_by=request.user)
        project_file_rows = list(fq[:600])
        project_file_ids = set(
            selected_project.library_files.filter(category=project_file_cat).values_list("id", flat=True)
        )

    project_assignees = []
    if selected_project and library_user_may_mutate_project_workbench(request.user, selected_project):
        assignee_ids = list(
            LibraryTaskAssignment.objects.filter(project=selected_project)
            .values_list("assignee_id", flat=True)
            .distinct()
        )
        revoked_ids = set(
            LibraryProjectUserRevocation.objects.filter(project=selected_project).values_list(
                "user_id", flat=True
            )
        )
        for u in User.objects.filter(pk__in=assignee_ids).order_by("username"):
            project_assignees.append({"user": u, "revoked": u.pk in revoked_ids})

    project_submissions = []
    if selected_project and library_user_may_mutate_project_workbench(request.user, selected_project):
        subs = (
            InspectionSubmission.objects.filter(project=selected_project)
            .select_related("case", "created_by")
            .order_by("-updated_at")[:600]
        )
        for s in subs:
            project_submissions.append(
                {
                    "id": s.pk,
                    "task_no": s.task_no,
                    "status": s.status,
                    "status_display": s.get_status_display(),
                    "case_no": s.case.case_no if s.case_id else "",
                    "updated_at": s.updated_at,
                    "submitted_at": s.submitted_at,
                    "created_by": s.created_by.username if s.created_by_id else "",
                    "can_rollback": s.status == InspectionSubmission.STATUS_SUBMITTED,
                }
            )

    workflow_members = []
    assignable_workflow_users = []
    project_workflow_cases = []
    workflow_stage_definitions = list(InspectionCaseWorkflowState.STAGE_CHOICES)
    if selected_project:
        workflow_members = list(
            LibraryProjectWorkflowMember.objects.filter(project=selected_project)
            .select_related("user", "user__profile", "user__profile__role")
            .order_by("workflow_role", "user__username")
        )
        if library_user_may_assign_on_project(request.user, selected_project):
            if library_user_has_party_a_demo_restrictions(request.user):
                assignable_workflow_users = list(
                    User.objects.filter(pk=request.user.pk, is_active=True).select_related("profile__role")
                )
            else:
                assignable_workflow_users = list(
                    User.objects.filter(is_active=True, profile__role__code__in=APP_SIDE_ROLE_CODES)
                    .select_related("profile__role")
                    .order_by("username")
                )
            stage_labels = dict(InspectionCaseWorkflowState.STAGE_CHOICES)
            for c in (
                InspectionCase.objects.filter(library_project=selected_project)
                .select_related("workflow_state")
                .order_by("-created_at")[:200]
            ):
                st = getattr(c, "workflow_state", None)
                cur = st.stage if st else InspectionCaseWorkflowState.STAGE_SITE_FILL
                project_workflow_cases.append(
                    {
                        "case": c,
                        "stage": cur,
                        "stage_display": stage_labels.get(cur, cur),
                        "return_reason": (st.return_reason if st else "") or "",
                        "issue_date": st.issue_date if st else None,
                    }
                )

    workbench_tab = normalize_workbench_tab(request.GET.get("tab") or "overview")
    if workbench_tab == "tasks":
        workbench_tab = "commission"
    if hide_project_workbench_files_tab and workbench_tab == "files":
        workbench_tab = "commission"
    if workbench_tab not in (
        "overview",
        "commission",
        "dispatch",
        "files",
        "submissions",
    ):
        workbench_tab = "overview"

    app_users_for_assign = []
    can_assign_on_selected_project = bool(
        selected_project and library_user_may_assign_on_project(request.user, selected_project)
    )
    if can_assign_on_selected_project:
        app_users_for_assign = list(
            User.objects.filter(profile__role__code__in=APP_SIDE_ROLE_CODES, is_active=True)
            .select_related("profile__role")
            .order_by("username")
        )
    elif selected_project and library_user_may_mutate_project_workbench(request.user, selected_project):
        app_users_for_assign = [request.user]

    project_scoped_assignment_records = []
    if selected_project and library_user_may_mutate_project_workbench(request.user, selected_project):
        assignment_rows = list(
            LibraryTaskAssignment.objects.filter(project_id=selected_project.pk)
            .select_related("assignee", "assigned_by", "project")
            .order_by("-created_at")[:400]
        )
        if not can_assign_on_selected_project:
            assignment_rows = [r for r in assignment_rows if r.assignee_id == request.user.id]
        grouped = {}
        for row in assignment_rows:
            key = (row.assignee_id, row.project_id)
            cur = grouped.get(key)
            if cur is None or row.created_at > cur["created_at"]:
                grouped[key] = {
                    "assignee": row.assignee,
                    "assigned_by": row.assigned_by,
                    "project": row.project,
                    "created_at": row.created_at,
                }
        proj = selected_project
        project_files_map: dict = {}
        pfiles = (
            LibraryFile.objects.filter(projects__id=proj.pk)
            .select_related("created_by")
            .order_by("original_name")
            .distinct()
        )
        for f in pfiles:
            for pid in f.projects.filter(pk=proj.pk).values_list("id", flat=True):
                project_files_map.setdefault(pid, []).append(f)
        for item in grouped.values():
            assignee = item["assignee"]
            assigned_tasks = []
            if assignee is not None:
                assigned_tasks = list(
                    LibraryTask.objects.filter(
                        assignments__project=proj,
                        assignments__assignee=assignee,
                    )
                    .distinct()
                    .order_by("code")
                )
            project_scoped_assignment_records.append(
                {
                    "assignee": assignee,
                    "assigned_by": item["assigned_by"],
                    "project": proj,
                    "created_at": item["created_at"],
                    "tasks": assigned_tasks,
                    "files": project_files_map.get(proj.pk, []),
                }
            )
        project_scoped_assignment_records.sort(key=lambda x: x["created_at"], reverse=True)

    projects_qs = LibraryProject.objects.order_by("-is_active", "code").select_related(
        "commission_org", "commission_org__parent", "commission_org__parent__parent"
    )
    if library_scope_own_files_only(request.user) and not library_user_can_assign_tasks_to_participants(request.user):
        sp = library_user_scoped_project_ids(request.user)
        projects_qs = projects_qs.filter(pk__in=sp) if sp else projects_qs.none()

    projects_list = list(projects_qs)
    commission_org_rows = list(commission_orgs_active_queryset())
    commission_org_rows.sort(key=lambda o: (o.full_display_name or "", o.pk))
    fl_path = (request.GET.get("fl_path") or "").strip()
    if selected_project and not fl_path:
        if selected_project.commission_org_id and selected_project.commission_org:
            fl_path = f"{selected_project.commission_org.folder_path()}/p-{selected_project.pk}"
        else:
            fl_path = f"o-none/p-{selected_project.pk}"

    commission_org_groups = _group_library_projects_by_commission_org(projects_list)
    if selected_commission_org:
        org_names = {g[0] for g in commission_org_groups}
        if selected_commission_org not in org_names:
            selected_commission_org = ""
    commission_org_nav = [
        {
            "label": label,
            "slug": _commission_org_url_slug(label),
            "projects": items,
            "count": len(items),
        }
        for label, items in commission_org_groups
    ]
    commission_org_query = ""
    if selected_commission_org:
        commission_org_query = f"&commission_org={quote(_commission_org_url_slug(selected_commission_org))}"

    _org_by_id, _, _ = index_commission_orgs(commission_org_rows)
    selected_org_id = resolve_org_id_from_segments(
        parse_commission_path_segments(fl_path), _org_by_id
    )

    folder_tree, folder_breadcrumbs, folder_entries, explorer_project, explorer_org = (
        build_org_tree_for_workbench(
            commission_org_rows,
            projects_list,
            selected_org_id=selected_org_id,
            selected_project_id=selected_project_id,
            fl_path=fl_path,
        )
    )
    if explorer_project is not None and selected_project is None:
        selected_project = explorer_project
        selected_project_id = explorer_project.pk
    can_manage_commission_orgs = _can_manage_commission_organizations(request.user)
    workbench_explorer_base = reverse("library_projects") + f"?tab={workbench_tab}"
    workbench_fl_query = f"&fl_path={quote(fl_path)}" if fl_path else ""
    explorer_org_child_levels: list[tuple[str, str]] = []
    if explorer_org:
        for lv in explorer_org.allowed_child_levels():
            label = dict(CommissionOrganization.LEVEL_CHOICES).get(lv, lv)
            explorer_org_child_levels.append((lv, label))

    can_assign_tasks = library_user_can_assign_tasks_to_participants(request.user)
    can_edit_project_workbench = bool(
        selected_project and library_user_may_mutate_project_workbench(request.user, selected_project)
    )
    can_edit_project_files = bool(
        selected_project and library_user_may_edit_project_files(request.user, selected_project)
    )
    participant_assign_self_only = can_edit_project_workbench and not can_assign_on_selected_project
    workbench_projects_limited_to_task_assignments = bool(
        library_scope_own_files_only(request.user) and not can_assign_tasks
    )
    can_delete_selected_project = False
    if selected_project:
        if library_user_can_assign_tasks_to_participants(request.user):
            can_delete_selected_project = True
        elif library_user_is_project_primary_responsible(request.user, selected_project):
            can_delete_selected_project = True
        elif role_has(request.user, "perm_create_library_project") and getattr(
            selected_project, "created_by_id", None
        ) == request.user.id:
            can_delete_selected_project = True

    project_commission_equipment_cards: list[dict] = []
    project_available_equipments: list[CommissionOrgEquipment] = []
    project_report_task_options: list[dict] = []
    project_equipment_scope_label = ""
    project_has_hospital = False
    if selected_project:
        project_has_hospital = hospital_for_project(selected_project) is not None
        project_commission_equipment_cards = project_equipment_cards(selected_project)
        equipment_scope_org = None
        if selected_org_id and selected_org_id in _org_by_id:
            equipment_scope_org = _org_by_id[selected_org_id]
        equipment_scope_org = resolve_workbench_equipment_scope_org(
            selected_project,
            scope_org=equipment_scope_org,
            fl_path=fl_path,
        )
        if equipment_scope_org is not None:
            project_equipment_scope_label = equipment_scope_label(equipment_scope_org)
        project_available_equipments = list(
            available_equipments_for_project(
                selected_project,
                scope_org=equipment_scope_org,
                fl_path=fl_path,
            )[:500]
        )
        project_report_task_options = report_task_options_for_project()

    workbench_tabs = [
        ("overview", "概览"),
        ("commission", "委托立项"),
        ("dispatch", "人员派工"),
    ]
    if not hide_project_workbench_files_tab:
        workbench_tabs.append(("files", "文件"))
    workbench_tabs.append(("submissions", "进度跟踪"))

    workflow_members_by_role = group_workflow_members_by_role(workflow_members)
    project_equipment_count = len(project_commission_equipment_cards)
    project_instrument_dispatch_panel: dict = {}
    if selected_project:
        from apps.core.instrument_inventory_service import build_project_instrument_dispatch_panel

        project_instrument_dispatch_panel = build_project_instrument_dispatch_panel(
            selected_project
        )
    workbench_pipeline_status = build_workbench_pipeline_status(
        selected_project,
        equipment_count=project_equipment_count,
        assignee_count=len(project_assignees),
        workflow_member_count=len(workflow_members),
        submission_count=len(project_submissions),
    )

    return render(
        request,
        "core/library_projects.html",
        {
            "projects": projects_qs,
            "commission_org_groups": commission_org_groups,
            "commission_org_nav": commission_org_nav,
            "commission_org_query": commission_org_query,
            "commission_org_rows": commission_org_rows,
            "can_manage_commission_orgs": can_manage_commission_orgs,
            "explorer_org": explorer_org,
            "explorer_org_child_levels": explorer_org_child_levels,
            "fl_path": fl_path,
            "folder_tree_nodes": folder_tree,
            "folder_breadcrumbs": folder_breadcrumbs,
            "folder_entries": folder_entries,
            "workbench_explorer_base": workbench_explorer_base,
            "workbench_fl_query": workbench_fl_query,
            "explorer_dnd_enabled": can_manage_commission_orgs,
            "explorer_dnd_allow_org_move": False,
            "explorer_dnd_post_url": reverse("library_projects"),
            "tree_root_label": "全部委托单位",
            "selected_commission_org": selected_commission_org,
            "commission_org_unset_label": _COMMISSION_ORG_UNSET_LABEL,
            "selected_project": selected_project,
            "selected_project_id": str(selected_project.pk) if selected_project else "",
            "all_library_tasks": all_library_tasks,
            "project_task_ids": project_task_ids,
            "project_report_tasks": project_report_tasks,
            "project_site_tasks": project_site_tasks,
            "selected_report_task_id": selected_report_task_id,
            "selected_report_source_ids": selected_report_source_ids,
            "has_report_source_task_column": _librarytask_has_report_source_relation(),
            "project_file_tab": project_file_tab,
            "project_file_rows": project_file_rows,
            "project_file_ids": project_file_ids,
            "project_assignees": project_assignees,
            "project_submissions": project_submissions,
            "workflow_members": workflow_members,
            "assignable_workflow_users": assignable_workflow_users,
            "project_workflow_cases": project_workflow_cases,
            "workflow_stage_definitions": workflow_stage_definitions,
            "workflow_role_choices": LibraryProjectWorkflowMember.WORKFLOW_ROLE_CHOICES,
            "can_assign_tasks": can_assign_tasks,
            "can_assign_on_selected_project": can_assign_on_selected_project,
            "can_edit_project_workbench": can_edit_project_workbench,
            "can_edit_project_files": can_edit_project_files,
            "participant_assign_self_only": participant_assign_self_only,
            "workbench_projects_limited_to_task_assignments": workbench_projects_limited_to_task_assignments,
            "can_delete_selected_project": can_delete_selected_project,
            "workbench_tab": workbench_tab,
            "app_users_for_assign": app_users_for_assign,
            "project_scoped_assignment_records": project_scoped_assignment_records,
            "workbench_tabs": workbench_tabs,
            "hide_project_workbench_files_tab": hide_project_workbench_files_tab,
            "commission_org_picker_tree": build_commission_org_picker_tree(commission_org_rows),
            "project_picker_initial": {
                "orgId": selected_project.commission_org_id
                if selected_project and selected_project.commission_org_id
                else None,
                "path": org_picker_initial_path(
                    selected_project.commission_org if selected_project else None
                ),
            },
            "file_library_tabs": list(_FILE_LIBRARY_TAB_DEFS),
            "project_commission_equipment_cards": project_commission_equipment_cards,
            "project_available_equipments": project_available_equipments,
            "project_report_task_options": project_report_task_options,
            "project_has_hospital": project_has_hospital,
            "project_equipment_scope_label": project_equipment_scope_label,
            "workbench_pipeline_status": workbench_pipeline_status,
            "workflow_members_by_role": workflow_members_by_role,
            "role_ladder": ROLE_LADDER,
            "process_steps_reference": PROCESS_STEPS_REFERENCE,
            "project_equipment_count": project_equipment_count,
            "project_instrument_dispatch_panel": project_instrument_dispatch_panel,
        },
    )


@login_required
def commission_manage(request):
    """委托管理：查看用户/项目委托情况，分配或撤回任务、指定主要负责人。"""
    gx = _require_perm(request, "perm_file_library")
    if gx:
        return gx
    if not library_user_may_access_commission_manage(request.user):
        messages.error(request, "当前账号无权使用委托管理")
        return redirect(reverse("dashboard"))

    subject_user: User | None = None
    subject_raw = (request.GET.get("user_id") or request.POST.get("user_id") or "").strip()
    if subject_raw:
        try:
            uid = int(subject_raw)
        except ValueError:
            uid = 0
        if uid:
            candidate = User.objects.filter(pk=uid, is_active=True).select_related("profile__role").first()
            if candidate and commission_may_view_user_stats(request.user, candidate):
                subject_user = candidate

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        try:
            project_id = int(request.POST.get("project_id", "") or 0)
        except ValueError:
            project_id = 0
        project = (
            LibraryProject.objects.filter(pk=project_id, is_active=True)
            .select_related("primary_responsible")
            .first()
            if project_id
            else None
        )
        redir = reverse("commission_manage")
        q_parts = []
        if subject_user:
            q_parts.append(f"user_id={subject_user.pk}")
        tab_q = ("?" + "&".join(q_parts)) if q_parts else ""

        if project is None:
            messages.error(request, "项目无效")
            return redirect(redir + tab_q)

        if action == "set_primary_responsible":
            if not library_user_may_assign_on_project(request.user, project):
                messages.error(request, "无权指定该项目主要负责人")
                return redirect(redir + tab_q)
            try:
                assignee_id = int(request.POST.get("assignee_id", "") or 0)
            except ValueError:
                assignee_id = 0
            assignee = User.objects.filter(
                pk=assignee_id,
                profile__role__code__in=APP_SIDE_ROLE_CODES,
                is_active=True,
            ).first()
            if assignee is None:
                messages.error(request, "请选择有效的检测侧用户")
            else:
                project.primary_responsible = assignee
                project.save(update_fields=["primary_responsible", "updated_at"])
                messages.success(
                    request,
                    f"已指定 {assignee.username} 为项目「{project.name}」主要负责人",
                )
        elif action == "assign":
            if not library_user_may_assign_on_project(request.user, project):
                if int(request.POST.get("assignee_id", "") or 0) != request.user.id:
                    messages.error(request, "无权向他人分配该项目任务")
                    return redirect(redir + tab_q)
            try:
                assignee_id = int(request.POST.get("assignee_id", "") or 0)
            except ValueError:
                assignee_id = 0
            assignee = User.objects.filter(
                pk=assignee_id,
                profile__role__code__in=APP_SIDE_ROLE_CODES,
                is_active=True,
            ).first()
            if assignee is None:
                messages.error(request, "接收用户无效")
            elif not project.library_tasks.exists():
                messages.error(request, "该项目尚未关联任务模板，请先在项目工作台绑定设备或任务")
            else:
                from apps.core.instrument_inventory_service import (
                    ensure_instruments_for_project_dispatch,
                )

                inv = ensure_instruments_for_project_dispatch(project, user=request.user)
                if not inv.ok:
                    messages.error(request, inv.message)
                else:
                    if inv.checked_out_count:
                        messages.info(request, inv.message)
                    created_count, file_n = _sync_library_project_tasks_to_user(
                        project, assignee, request.user
                    )
                    if created_count:
                        messages.success(
                            request,
                            f"已向 {assignee.username} 分配 {created_count} 个任务模板",
                        )
                    else:
                        messages.info(request, f"{assignee.username} 已拥有该项目全部任务模板")
        elif action == "instrument_checkin_project":
            from apps.core.instrument_inventory_service import checkin_instruments_from_project

            if not library_user_may_mutate_project_workbench(request.user, project):
                messages.error(request, "无权操作本项目仪器入库")
            else:
                n = checkin_instruments_from_project(
                    project, None, user=request.user, note="委托管理/项目仪器入库"
                )
                messages.success(
                    request,
                    f"已入库 {n} 台仪器（解除与项目「{project.name}」的出库绑定）",
                )
            return redirect(
                reverse("library_projects") + f"?project_id={project.pk}&tab=dispatch"
            )
        elif action == "unassign":
            if not library_user_may_assign_on_project(request.user, project):
                try:
                    aid = int(request.POST.get("assignee_id", "") or 0)
                except ValueError:
                    aid = 0
                if aid != request.user.id:
                    messages.error(request, "无权撤回他人在该项目上的分配")
                    return redirect(redir + tab_q)
            try:
                assignee_id = int(request.POST.get("assignee_id", "") or 0)
            except ValueError:
                assignee_id = 0
            assignee = User.objects.filter(pk=assignee_id, is_active=True).first()
            if assignee is None:
                messages.error(request, "用户无效")
            elif not LibraryTaskAssignment.objects.filter(project=project, assignee=assignee).exists():
                messages.error(request, "该用户在本项目上无任务分配")
            else:
                n, _ = LibraryTaskAssignment.objects.filter(project=project, assignee=assignee).delete()
                messages.success(request, f"已撤回 {assignee.username} 在项目「{project.name}」上的 {n} 条分配")
        else:
            messages.error(request, "未知操作")
        return redirect(redir + tab_q)

    status_filter = (request.GET.get("status") or "").strip()
    if status_filter not in ("", "completed", "incomplete"):
        status_filter = ""
    search = (request.GET.get("search") or "").strip()

    visible_users = list(commission_visible_subject_users(request.user))
    show_user_table = len(visible_users) > 1 or library_user_can_assign_tasks_to_participants(request.user)
    user_summary_rows = commission_user_summary_rows(request.user) if show_user_table else []
    overview = commission_overview_for_viewer(
        request.user, subject_user=subject_user or request.user
    )
    if subject_user is None and not show_user_table:
        subject_user = request.user
    project_rows = commission_project_rows(
        request.user,
        subject_user=subject_user,
        status_filter=status_filter,
        search=search,
    )
    app_users_for_assign = list(
        User.objects.filter(profile__role__code__in=APP_SIDE_ROLE_CODES, is_active=True)
        .select_related("profile__role")
        .order_by("username")
    )
    can_assign_globally = library_user_can_assign_tasks_to_participants(request.user)

    return render(
        request,
        "core/commission_manage.html",
        {
            "subject_user": subject_user,
            "visible_users": visible_users,
            "show_user_table": show_user_table,
            "user_summary_rows": user_summary_rows,
            "overview": overview,
            "project_rows": project_rows,
            "status_filter": status_filter,
            "search": search,
            "app_users_for_assign": app_users_for_assign,
            "can_assign_globally": can_assign_globally,
        },
    )


def _file_library_query_string(
    tab: str,
    date_from: str = "",
    date_to: str = "",
    uploader: str = "",
    project: str = "",
    commission_org: str = "",
    co_path: str = "",
    fl_path: str = "",
) -> str:
    q = {"tab": tab}
    df = (date_from or "").strip()
    dt = (date_to or "").strip()
    if df and dt:
        q["date_from"] = df
        q["date_to"] = dt
    up = (uploader or "").strip()
    if up:
        q["uploader"] = up
    pj = (project or "").strip()
    if pj:
        q["project"] = pj
    co = (commission_org or "").strip()
    if co:
        q["commission_org"] = co
    cp = (co_path or "").strip()
    if cp:
        q["co_path"] = cp
    fp = (fl_path or "").strip()
    if fp:
        q["fl_path"] = fp
    return "?" + urlencode(q)


def _file_library_post_ids_unique(qd) -> list[int]:
    """POST QueryDict 中 name=ids 的整数主键列表（去重、升序）。"""
    out: list[int] = []
    for x in qd.getlist("ids"):
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


def _parse_file_library_date_range(request):
    """
    从 GET 解析日期区间；须同时提供起止日期，且含首尾最多连续 30 天。
    返回 (error_msg, date_start, date_end, date_from_str, date_to_str)。
    """
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    if not date_from and not date_to:
        return "", None, None, "", ""
    if not date_from or not date_to:
        return "请同时选择开始日期与结束日期。", None, None, date_from, date_to
    try:
        d0 = date.fromisoformat(date_from)
        d1 = date.fromisoformat(date_to)
    except ValueError:
        return "日期格式无效，请使用 YYYY-MM-DD。", None, None, date_from, date_to
    if d0 > d1:
        return "开始日期不能晚于结束日期。", None, None, date_from, date_to
    if (d1 - d0).days + 1 > 30:
        return "日历筛选最多支持连续 30 天（含起止日）。", None, None, date_from, date_to
    return "", d0, d1, date_from, date_to


_FILE_LIBRARY_VALID_TABS = frozenset(
    {"ocr", "json", "template", "site_record", "report", "attachment", "inspection_submit", "trash"}
)

_FILE_LIBRARY_TAB_DEFS = (
    {"key": "ocr", "label": "待识别文件"},
    {"key": "json", "label": "数据文件"},
    {"key": "template", "label": "模板"},
    {"key": "site_record", "label": "现场记录"},
    {"key": "report", "label": "报告"},
    {"key": "attachment", "label": "附件"},
    {"key": "inspection_submit", "label": "检测提交"},
    {"key": "trash", "label": "回收站"},
)


def _normalize_library_tab(tab: str) -> str:
    if tab == "upload":
        return "ocr"
    if tab in _FILE_LIBRARY_VALID_TABS:
        return tab
    return "ocr"


def _library_category_for_tab(tab: str) -> str:
    t = _normalize_library_tab(tab)
    return {
        "ocr": LibraryFile.CATEGORY_UPLOAD,
        "json": LibraryFile.CATEGORY_JSON,
        "template": LibraryFile.CATEGORY_TEMPLATE,
        "site_record": LibraryFile.CATEGORY_SITE_RECORD,
        "report": LibraryFile.CATEGORY_REPORT,
        "attachment": LibraryFile.CATEGORY_ATTACHMENT,
        "inspection_submit": LibraryFile.CATEGORY_INSPECTION_SUBMIT,
    }[t]


def _library_tab_for_category(category: str) -> str:
    return {
        LibraryFile.CATEGORY_UPLOAD: "ocr",
        LibraryFile.CATEGORY_JSON: "json",
        LibraryFile.CATEGORY_TEMPLATE: "template",
        LibraryFile.CATEGORY_SITE_RECORD: "site_record",
        LibraryFile.CATEGORY_REPORT: "report",
        LibraryFile.CATEGORY_ATTACHMENT: "attachment",
        LibraryFile.CATEGORY_INSPECTION_SUBMIT: "inspection_submit",
    }.get(category, "ocr")


def _persist_binary_library_files(request, files, category: str, project_ids=None) -> int:
    """写入非 JSON 校验类文件；返回成功保存条数。"""
    created, skipped = save_library_binary_uploads(
        request.user, files, category, project_ids=project_ids or []
    )
    for s in skipped:
        fn = s.get("filename") or "(无名)"
        messages.warning(request, f"跳过文件 {fn}: {s.get('reason', '')}")
    return len(created)


def _file_library_business_header(project, task_no: str) -> str:
    """
    文件库列表首行：委托编号｜项目名称｜报告任务名称｜现场记录任务名称（缺省段省略，用「｜」分隔）。
    task_no 通常为案件的任务编号（与检测提交 taskNo 一致），用于解析报告/现场记录任务名。
    """
    from apps.api.inspection_pdf_service import _resolve_library_task_for_task_no
    from apps.api.inspection_report_make import _resolve_report_task_for_case

    if project is None:
        return "未关联委托项目"
    parts: list[str] = []
    code = (getattr(project, "code", None) or "").strip()
    pname = (getattr(project, "name", None) or "").strip()
    if code:
        parts.append(code)
    if pname:
        parts.append(pname)
    tn = (task_no or "").strip()
    report_nm = ""
    site_nm = ""
    if tn:
        rt = _resolve_report_task_for_case(tn, project)
        if rt is not None:
            report_nm = (rt.name or "").strip()
        lt = _resolve_library_task_for_task_no(tn, project)
        if lt is not None and lt.output_target == LibraryTask.OUTPUT_SITE_RECORD:
            site_nm = (lt.name or "").strip()
    if not report_nm:
        rt0 = (
            project.library_tasks.filter(output_target=LibraryTask.OUTPUT_REPORT)
            .order_by("code", "id")
            .first()
        )
        if rt0 is not None:
            report_nm = (rt0.name or "").strip()
    if not site_nm:
        st0 = (
            project.library_tasks.filter(output_target=LibraryTask.OUTPUT_SITE_RECORD)
            .order_by("code", "id")
            .first()
        )
        if st0 is not None:
            site_nm = (st0.name or "").strip()
    if report_nm:
        parts.append(report_nm)
    if site_nm:
        parts.append(site_nm)
    return "｜".join(parts) if parts else "未关联委托项目"


def _file_library_content_label(f: LibraryFile, case) -> str:
    """文件库列表第二行：面向业务人员的「这份文件是什么」，尽量不出现技术文件名。"""
    on = (f.original_name or "").strip()
    low = on.lower()
    cat = f.category

    if cat == LibraryFile.CATEGORY_INSPECTION_SUBMIT:
        if low.endswith(".json"):
            if "填写数据" in on:
                return "填写数据"
            return "文本信息"
        if low.endswith("检测员签名.png") or "检测员签名" in on and low.endswith(".png"):
            return "检测员签名"
        if low.endswith("校核员签名.png") or ("校核员签名" in on and low.endswith(".png")):
            return "校核员签名"
        if low.endswith("受检单位陪同人签名.png") or ("受检单位陪同人签名" in on and low.endswith(".png")):
            return "受检单位陪同人签名"
        m = re.search(r"_signature_(inspector|checker|accompanyingperson)_", low)
        if m:
            role = m.group(1).lower()
            return {
                "inspector": "检测员签名",
                "checker": "校核员签名",
                "accompanyingperson": "受检单位陪同人签名",
            }.get(role, "签名图片")
        if "signature_inspector" in low:
            return "检测员签名"
        if "_signature_reviewer_" in low or "_signature_reviewer." in low:
            return "校核员签名"
        if "_signature_authorized" in low or "_signature_approver_" in low:
            return "受检单位陪同人签名"
        return "检测提交相关文件"

    if cat == LibraryFile.CATEGORY_SITE_RECORD and low.endswith(".pdf"):
        if "__task__" in on and "-现场记录" in on:
            prefix = on.split("__task__", 1)[0].strip(" _")
            if prefix and len(prefix) < 120:
                return f"现场记录（{prefix}）"
        return "现场记录（PDF）"
    if cat == LibraryFile.CATEGORY_REPORT and low.endswith(".pdf"):
        if "合并报告" in on:
            return "合并报告（PDF）"
        return "检测报告（PDF）"
    if cat == LibraryFile.CATEGORY_TEMPLATE:
        if low.endswith(".json"):
            return "任务模板（JSON）"
        if low.endswith(".pdf"):
            return "任务模板（PDF）"
        return "任务模板文件"
    if cat == LibraryFile.CATEGORY_JSON:
        return "JSON 数据文件"
    if cat == LibraryFile.CATEGORY_UPLOAD:
        return "待识别文件（OCR）"
    if cat == LibraryFile.CATEGORY_ATTACHMENT:
        return "附件"
    if low.endswith(".json"):
        return "JSON 数据文件"
    if low.endswith(".pdf"):
        return "PDF 文档"
    if low.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
        return "图片文件"
    return "文件"


_EXT_FRIENDLY_CN = {
    "pdf": "PDF",
    "json": "JSON",
    "png": "图片",
    "jpg": "图片",
    "jpeg": "图片",
    "webp": "图片",
    "gif": "图片",
    "doc": "Word",
    "docx": "Word",
    "xls": "表格",
    "xlsx": "表格",
    "txt": "文本",
}


def _humanize_library_display_name(filename: str) -> str:
    """去掉常见 UUID 前缀，把下划线换成空格，让原始文件名更易读。"""
    s = (filename or "").strip()
    if not s:
        return "未命名文件"
    stem, ext = s, ""
    if "." in s:
        stem, ext = s.rsplit(".", 1)
        ext = ext.lower()
    stem = re.sub(r"^[0-9a-f]{32}_?", "", stem, flags=re.I)
    stem = re.sub(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_?",
        "",
        stem,
        flags=re.I,
    )
    stem = stem.replace("_", " ").replace("-", " ").strip()
    if not stem:
        stem = "文件"
    if ext:
        tip = _EXT_FRIENDLY_CN.get(ext)
        if tip:
            return f"{stem}（{tip}）"
        if len(ext) <= 5 and ext.isalnum():
            return f"{stem}.{ext}"
    return stem


def _file_library_kind_badge(f: LibraryFile, case) -> str:
    """列表左侧小标签：短、好认。"""
    if f.category == LibraryFile.CATEGORY_INSPECTION_SUBMIT:
        return _file_library_content_label(f, case)
    return {
        LibraryFile.CATEGORY_SITE_RECORD: "现场记录",
        LibraryFile.CATEGORY_REPORT: "报告",
        LibraryFile.CATEGORY_TEMPLATE: "模板",
        LibraryFile.CATEGORY_JSON: "数据(JSON)",
        LibraryFile.CATEGORY_UPLOAD: "纸质扫描",
        LibraryFile.CATEGORY_ATTACHMENT: "附件",
    }.get(f.category, "文件")


def _file_library_primary_title(f: LibraryFile, case) -> str:
    """主标题：优先易读原名，否则退回业务类型说明。"""
    hum = _humanize_library_display_name((f.original_name or "").strip())
    lab = _file_library_content_label(f, case)
    if hum in ("文件", "未命名文件") or hum == lab:
        return lab
    if len(hum) <= 8 and re.fullmatch(r"[0-9a-fA-F\s.()（）]+", hum):
        return lab
    return hum


def _file_library_where_sentence(
    project,
    task_no: str,
    f: LibraryFile,
    case,
    tasks: list,
) -> str:
    """每条只给极短补充（项目/报告/任务名已在左侧分组标题里，不在此重复长句）。"""
    if f.category == LibraryFile.CATEGORY_TEMPLATE:
        if not tasks:
            return "未绑定"
        if len(tasks) == 1:
            return ""
        return f"共绑{len(tasks)}个环节"

    if project is None:
        return "未关联项目"

    if case is not None:
        return f"委托 {case.case_no}"
    tn = (task_no or "").strip()
    if tn:
        return f"委托 {tn}"
    return ""


def _file_library_search_blob(f: LibraryFile) -> str:
    """供本页就地搜索：拼常见检索词。"""
    parts = [
        f.original_name or "",
        getattr(f, "file_library_primary_title", ""),
        getattr(f, "file_library_content_label", ""),
        getattr(f, "file_library_kind_badge", ""),
        getattr(f, "file_library_where_sentence", ""),
        getattr(f, "file_library_secondary", ""),
        getattr(f, "file_library_business_header", ""),
        getattr(f, "file_library_site_record_heading", ""),
    ]
    u = getattr(f, "created_by", None)
    if u is not None:
        parts.append(getattr(u, "username", "") or "")
        parts.append(str(getattr(u, "pk", "") or ""))
    return " ".join(x for x in parts if x)


def _annotate_file_library_display(user, files: list) -> None:
    """文件库列表：写入业务表头、类型说明、易读主标题、白话「归属」句、就地搜索串、分组键等。"""
    from apps.api.inspection_pdf_service import _resolve_library_task_for_task_no
    from apps.api.inspection_report_make import _resolve_report_task_for_case

    case_ids: list[int] = []
    for f in files:
        if f.link_entity == LibraryFile.LINK_ENTITY_INSPECTION_CASE and f.link_object_id:
            try:
                case_ids.append(int(f.link_object_id))
            except (TypeError, ValueError):
                continue
    cases_by_id: dict[int, InspectionCase] = {}
    if case_ids:
        for c in InspectionCase.objects.filter(pk__in=sorted(set(case_ids))).select_related("library_project"):
            cases_by_id[c.pk] = c

    for f in files:
        projs = list(f.projects.all()[:12])
        tasks = list(f.library_tasks.all()[:12])
        case = None
        if f.link_entity == LibraryFile.LINK_ENTITY_INSPECTION_CASE and f.link_object_id:
            case = cases_by_id.get(int(f.link_object_id))

        project = None
        if case is not None and case.library_project_id:
            project = case.library_project
        elif projs:
            project = projs[0]

        task_no = case.case_no if case is not None else ""

        f.file_library_business_header = _file_library_business_header(project, task_no)
        f.file_library_content_label = _file_library_content_label(f, case)
        f.file_library_kind_badge = _file_library_kind_badge(f, case)
        f.file_library_primary_title = _file_library_primary_title(f, case)
        f.file_library_where_sentence = _file_library_where_sentence(
            project, task_no, f, case, tasks
        )
        f.file_library_uploader_display = (
            (f.created_by.username or "").strip()
            if getattr(f, "created_by", None)
            else "—"
        )

        secondary_bits: list[str] = []
        if tasks and f.category != LibraryFile.CATEGORY_INSPECTION_SUBMIT:
            if not (f.category == LibraryFile.CATEGORY_TEMPLATE and len(tasks) == 1):
                labels = [((t.name or "").strip() or t.code or "?") for t in tasks[:2]]
                if len(tasks) > 2:
                    secondary_bits.append("环节：" + "、".join(labels) + f" 等{len(tasks)}个")
                else:
                    secondary_bits.append("环节：" + "、".join(labels))
        elif (
            f.category != LibraryFile.CATEGORY_INSPECTION_SUBMIT
            and case is not None
            and case.library_project_id
            and not tasks
        ):
            lt = _resolve_library_task_for_task_no(case.case_no, case.library_project)
            if lt is not None:
                nm = (lt.name or "").strip() or lt.code
                secondary_bits.append(f"环节：{nm}")

        f.file_library_secondary = " · ".join(secondary_bits) if secondary_bits else ""

        f.file_technical_storage_name = (f.original_name or "").strip() or "—"

        if project is not None:
            f.file_library_project_key = str(project.pk)
            f.file_library_project_heading = f"{project.code}｜{project.name}"
        else:
            f.file_library_project_key = "none"
            f.file_library_project_heading = "未关联项目"

        report_task = _resolve_report_task_for_case(task_no, project) if project is not None else None
        if report_task is not None:
            f.file_library_report_key = str(report_task.pk)
            f.file_library_report_heading = f"{report_task.code} · {report_task.name}"
        else:
            f.file_library_report_key = "none"
            f.file_library_report_heading = "未配置报告任务"

        if f.category == LibraryFile.CATEGORY_INSPECTION_SUBMIT:
            site_task = None
            site_tasks = [
                t
                for t in tasks
                if getattr(t, "output_target", None) == LibraryTask.OUTPUT_SITE_RECORD
            ]
            if site_tasks:
                site_task = sorted(site_tasks, key=lambda x: ((x.code or ""), x.id))[0]
            elif project is not None and task_no:
                lt = _resolve_library_task_for_task_no(task_no, project)
                if lt is not None and lt.output_target == LibraryTask.OUTPUT_SITE_RECORD:
                    site_task = lt
            if site_task is not None:
                f.file_library_site_record_key = str(site_task.pk)
                nm = (site_task.name or "").strip() or (site_task.code or "").strip() or "?"
                cc = (site_task.code or "").strip()
                f.file_library_site_record_heading = f"{cc} · {nm}" if cc else nm
            else:
                f.file_library_site_record_key = "none"
                f.file_library_site_record_heading = "未绑定现场记录任务"
            from apps.core.library_file_service import classify_inspection_submit_library_file

            f.file_library_submit_bucket = classify_inspection_submit_library_file(f)
        else:
            f.file_library_site_record_key = ""
            f.file_library_site_record_heading = ""
            f.file_library_submit_bucket = ""

        if f.category == LibraryFile.CATEGORY_TEMPLATE:
            if tasks:
                t0 = sorted(tasks, key=lambda x: ((x.code or ""), x.id))[0]
                f.file_library_task_group_key = str(t0.pk)
                f.file_library_task_group_heading = f"{t0.code} · {t0.name}"
                f.file_library_task_output_target = (
                    getattr(t0, "output_target", None) or LibraryTask.OUTPUT_SITE_RECORD
                )
            else:
                f.file_library_task_group_key = "none"
                f.file_library_task_group_heading = "未绑定任务模板"
                f.file_library_task_output_target = "none"
        else:
            f.file_library_task_group_key = ""
            f.file_library_task_group_heading = ""
            f.file_library_task_output_target = ""

        f.file_library_search_blob = _file_library_search_blob(f)
        f.file_library_may_delete = library_user_may_delete_library_file(user, f)


def _leaf_latest_ts(file_list: list) -> float:
    latest = None
    for lf in file_list:
        if lf.created_at and (latest is None or lf.created_at > latest):
            latest = lf.created_at
    return latest.timestamp() if latest else 0.0


def _nest_file_library_by_project_report(files: list) -> list[dict]:
    """两级：项目 → 报告任务 → 文件列表。"""
    from collections import defaultdict

    tree: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    project_heading: dict[str, str] = {}
    report_heading: dict[tuple[str, str], str] = {}

    for f in files:
        pk = getattr(f, "file_library_project_key", "none")
        rk = getattr(f, "file_library_report_key", "none")
        tree[pk][rk].append(f)
        project_heading[pk] = getattr(f, "file_library_project_heading", "未关联项目")
        report_heading[(pk, rk)] = getattr(f, "file_library_report_heading", "报告")

    def _project_latest_ts(pkey: str) -> float:
        latest = None
        for _rk, flist in tree[pkey].items():
            for lf in flist:
                if lf.created_at and (latest is None or lf.created_at > latest):
                    latest = lf.created_at
        return latest.timestamp() if latest else 0.0

    sorted_pkeys = sorted(tree.keys(), key=lambda k: (-_project_latest_ts(k), project_heading.get(k, "")))

    out: list[dict] = []
    for pkey in sorted_pkeys:
        rmap = tree[pkey]
        sorted_rkeys = sorted(rmap.keys(), key=lambda rk: (-_leaf_latest_ts(rmap[rk]), report_heading.get((pkey, rk), "")))
        reports: list[dict] = []
        total_n = 0
        for rkey in sorted_rkeys:
            flist = rmap[rkey]
            flist.sort(
                key=lambda lf: (
                    -(lf.created_at.timestamp() if lf.created_at else 0),
                    -lf.pk,
                )
            )
            reports.append(
                {
                    "report_key": rkey,
                    "report_heading": report_heading.get((pkey, rkey), "报告"),
                    "files": flist,
                    "file_count": len(flist),
                }
            )
            total_n += len(flist)
        out.append(
            {
                "project_key": pkey,
                "project_heading": project_heading.get(pkey, "未关联项目"),
                "file_count": total_n,
                "reports": reports,
            }
        )
    return out


def _split_inspection_submit_files(flist: list) -> tuple[list, list, list]:
    """将现场记录分组内文件拆为：提交数据(JSON)、签名、留存照片。"""
    data_files: list = []
    signature_files: list = []
    photo_files: list = []
    for f in flist:
        bucket = getattr(f, "file_library_submit_bucket", None) or "legacy"
        if bucket == "signature":
            signature_files.append(f)
        elif bucket == "photo":
            photo_files.append(f)
        elif bucket == "data":
            data_files.append(f)
        else:
            name = (getattr(f, "original_name", None) or "").lower()
            if name.endswith(".json") or "填写数据" in name:
                data_files.append(f)
            elif "签名" in name:
                signature_files.append(f)
            elif name.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
                photo_files.append(f)
            else:
                data_files.append(f)
    return data_files, signature_files, photo_files


def _nest_file_library_by_project_report_site(files: list) -> list[dict]:
    """三级：检测项目 → 报告任务 → 现场记录任务 → 文件（检测提交分类列表）。"""
    from collections import defaultdict

    tree: dict[str, dict[str, dict[str, list]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    project_heading: dict[str, str] = {}
    report_heading: dict[tuple[str, str], str] = {}
    site_heading: dict[tuple[str, str, str], str] = {}

    for f in files:
        pk = getattr(f, "file_library_project_key", "none")
        rk = getattr(f, "file_library_report_key", "none")
        sk = getattr(f, "file_library_site_record_key", "none") or "none"
        tree[pk][rk][sk].append(f)
        project_heading[pk] = getattr(f, "file_library_project_heading", "未关联项目")
        report_heading[(pk, rk)] = getattr(f, "file_library_report_heading", "报告")
        site_heading[(pk, rk, sk)] = getattr(f, "file_library_site_record_heading", "现场记录")

    def _project_latest_ts(pkey: str) -> float:
        latest = None
        for _rk, smap in tree[pkey].items():
            for _sk, flist in smap.items():
                for lf in flist:
                    if lf.created_at and (latest is None or lf.created_at > latest):
                        latest = lf.created_at
        return latest.timestamp() if latest else 0.0

    sorted_pkeys = sorted(tree.keys(), key=lambda k: (-_project_latest_ts(k), project_heading.get(k, "")))

    out: list[dict] = []
    for pkey in sorted_pkeys:
        rmap = tree[pkey]
        sorted_rkeys = sorted(
            rmap.keys(),
            key=lambda rk: (
                -max((_leaf_latest_ts(rmap[rk][sk]) for sk in rmap[rk]), default=0.0),
                report_heading.get((pkey, rk), ""),
            ),
        )
        reports: list[dict] = []
        total_n = 0
        for rkey in sorted_rkeys:
            smap = rmap[rkey]
            sorted_skeys = sorted(
                smap.keys(),
                key=lambda sk: (-_leaf_latest_ts(smap[sk]), site_heading.get((pkey, rkey, sk), "")),
            )
            site_records: list[dict] = []
            subtotal = 0
            for skey in sorted_skeys:
                flist = smap[skey]
                flist.sort(
                    key=lambda lf: (
                        -(lf.created_at.timestamp() if lf.created_at else 0),
                        -lf.pk,
                    )
                )
                data_files, signature_files, photo_files = _split_inspection_submit_files(flist)
                site_records.append(
                    {
                        "site_key": skey,
                        "site_heading": site_heading.get((pkey, rkey, skey), "现场记录"),
                        "files": flist,
                        "data_files": data_files,
                        "signature_files": signature_files,
                        "photo_files": photo_files,
                        "file_count": len(flist),
                    }
                )
                subtotal += len(flist)
            reports.append(
                {
                    "report_key": rkey,
                    "report_heading": report_heading.get((pkey, rkey), "报告"),
                    "file_count": subtotal,
                    "site_records": site_records,
                }
            )
            total_n += subtotal
        out.append(
            {
                "project_key": pkey,
                "project_heading": project_heading.get(pkey, "未关联项目"),
                "file_count": total_n,
                "reports": reports,
            }
        )
    return out


def _nest_file_library_by_library_task(files: list) -> list[dict]:
    """模板分类：现场记录 / 报告 → 环节任务（与任务模板库层级一致）。"""
    from collections import defaultdict

    from apps.core.library_folder_service import _TASK_OUTPUT_LABELS

    by_task: dict[str, list] = defaultdict(list)
    headings: dict[str, str] = {}
    output_by_task: dict[str, str] = {}

    for f in files:
        tk = getattr(f, "file_library_task_group_key", None) or "none"
        if tk == "":
            tk = "none"
        by_task[tk].append(f)
        headings[tk] = getattr(f, "file_library_task_group_heading", "未绑定任务模板")
        output_by_task[tk] = getattr(f, "file_library_task_output_target", None) or "none"

    def _group_latest_ts(tkey: str) -> float:
        latest = None
        for lf in by_task[tkey]:
            if lf.created_at and (latest is None or lf.created_at > latest):
                latest = lf.created_at
        return latest.timestamp() if latest else 0.0

    def _sort_files(flist: list) -> list:
        flist.sort(
            key=lambda lf: (
                -(lf.created_at.timestamp() if lf.created_at else 0),
                -lf.pk,
            )
        )
        return flist

    task_ids: list[int] = []
    for tk in by_task:
        if str(tk).isdigit():
            task_ids.append(int(tk))
    tasks_by_id = {
        t.pk: t
        for t in LibraryTask.objects.filter(pk__in=task_ids).prefetch_related("report_source_tasks")
    }

    by_output: dict[str, list[dict]] = defaultdict(list)
    for tkey, flist in by_task.items():
        _sort_files(flist)
        tg = {
            "task_key": tkey,
            "task_heading": headings.get(tkey, "未绑定任务模板"),
            "file_count": len(flist),
            "files": flist,
            "linked_site_groups": [],
        }
        target = output_by_task.get(tkey) or "none"
        if tkey.isdigit():
            task_obj = tasks_by_id.get(int(tkey))
            if task_obj is not None:
                target = getattr(task_obj, "output_target", None) or LibraryTask.OUTPUT_SITE_RECORD
        if target == LibraryTask.OUTPUT_REPORT and tkey.isdigit():
            report_task = tasks_by_id.get(int(tkey))
            if report_task is not None:
                linked: list[dict] = []
                for st in report_task.report_source_tasks.all():
                    sk = str(st.pk)
                    site_files = _sort_files(list(by_task.get(sk, [])))
                    if not site_files:
                        continue
                    nm = (st.name or "").strip() or (st.code or "").strip() or "?"
                    cc = (st.code or "").strip()
                    linked.append(
                        {
                            "task_key": sk,
                            "task_heading": f"{cc} · {nm}" if cc else nm,
                            "file_count": len(site_files),
                            "files": site_files,
                        }
                    )
                linked.sort(
                    key=lambda g: (
                        -_group_latest_ts(g["task_key"]),
                        g.get("task_heading") or "",
                    )
                )
                tg["linked_site_groups"] = linked
        bucket = target if target in _TASK_OUTPUT_LABELS else "none"
        by_output[bucket].append(tg)

    out: list[dict] = []
    for target in (LibraryTask.OUTPUT_SITE_RECORD, LibraryTask.OUTPUT_REPORT, "none"):
        groups = by_output.get(target) or []
        if not groups:
            continue
        groups.sort(
            key=lambda g: (
                -_group_latest_ts(str(g.get("task_key") or "none")),
                g.get("task_heading") or "",
            )
        )
        total = sum(int(g.get("file_count") or 0) for g in groups)
        for g in groups:
            for lg in g.get("linked_site_groups") or []:
                total += int(lg.get("file_count") or 0)
        out.append(
            {
                "output_key": target,
                "output_label": _TASK_OUTPUT_LABELS.get(target, "未分类"),
                "file_count": total,
                "task_groups": groups,
            }
        )
    return out


@login_required
def file_library(request):
    tab_raw = request.GET.get("tab")
    if not tab_raw and request.method == "POST":
        tab_raw = request.POST.get("tab")
    tab = _normalize_library_tab(tab_raw or "ocr")

    gx = _require_perm(request, "perm_file_library")
    if gx:
        return gx
    project_raw = request.GET.get("project", "").strip()
    try:
        project_selected_id = int(project_raw) if project_raw else None
    except ValueError:
        project_selected_id = None
    project_selected = str(project_selected_id) if project_selected_id else ""
    commission_org_raw = request.GET.get("commission_org", "").strip()
    selected_commission_org = _commission_org_from_url_slug(commission_org_raw) if commission_org_raw else ""
    co_path = (request.GET.get("co_path") or "").strip()
    fl_path_early = (request.GET.get("fl_path") or "").strip()

    if request.method == "POST" and request.POST.get("action") == "dnd_attach_files_to_project":
        redir = reverse("file_library") + _file_library_query_string(
            tab, request.POST.get("date_from", "").strip(), request.POST.get("date_to", "").strip(),
            request.POST.get("uploader", "").strip(), project_selected, commission_org_raw,
            co_path=request.POST.get("co_path", "").strip() or co_path,
            fl_path=request.POST.get("fl_path", "").strip() or fl_path_early,
        )
        fl = (request.POST.get("fl_path") or "").strip()
        if fl:
            redir += ("&" if "?" in redir else "?") + f"fl_path={quote(fl)}"
        try:
            target_pid = int(request.POST.get("project_id", "") or 0)
        except ValueError:
            target_pid = 0
        raw_ids = (request.POST.get("file_ids") or "").strip()
        file_ids = []
        for part in raw_ids.split(","):
            part = part.strip()
            if part.isdigit():
                file_ids.append(int(part))
        proj = LibraryProject.objects.filter(pk=target_pid, is_active=True).first()
        if not proj or not file_ids:
            return _action_json_or_redirect(
                request, ok=False, message="目标项目或文件无效", redirect_url=redir
            )
        allowed_ids = []
        for fid in file_ids:
            lf = LibraryFile.objects.filter(pk=fid).first()
            if lf and library_file_access_allowed(request.user, lf):
                if not library_file_write_blocked_by_project_revocation(request.user, lf):
                    allowed_ids.append(fid)
        if not allowed_ids:
            return _action_json_or_redirect(
                request, ok=False, message="无权关联所选文件", redirect_url=redir
            )
        attach_files_to_projects(sorted(set(allowed_ids)), [proj.pk], request.user)
        redir = reverse("file_library") + _file_library_query_string(
            tab, request.POST.get("date_from", "").strip(), request.POST.get("date_to", "").strip(),
            request.POST.get("uploader", "").strip(), str(proj.pk), commission_org_raw,
        )
        redir += ("&" if "?" in redir else "?") + f"fl_path={quote(f'p-{proj.pk}')}"
        return _action_json_or_redirect(
            request,
            ok=True,
            message=f"已将 {len(allowed_ids)} 个文件关联到项目「{proj.name}」",
            redirect_url=redir,
        )

    if request.method == "POST" and request.POST.get("action") == "create_project":
        if not library_user_can_assign_tasks_to_participants(request.user):
            messages.error(request, "当前角色无权管理项目")
            return redirect(
                reverse("file_library")
                + _file_library_query_string(tab, "", "", "", project_selected, commission_org_raw)
            )
        commission_org = request.POST.get("commission_organization", "").strip()
        name = request.POST.get("project_name", "").strip()
        code = _gen_project_code()
        if not commission_org or not name:
            messages.error(request, "委托单位名称与项目名称不能为空")
        elif LibraryProject.objects.filter(code=code).exists():
            messages.error(request, "项目编码生成冲突，请重试")
        else:
            row = LibraryProject.objects.create(
                code=code,
                name=name,
                commission_organization=commission_org,
                created_by=request.user,
            )
            register_tour_project(request, row.pk)
            messages.success(request, f"已创建项目：{commission_org} · {name}")
            commission_org_raw = _commission_org_url_slug(commission_org)
        return redirect(
            reverse("file_library")
            + _file_library_query_string(tab, "", "", "", project_selected, commission_org_raw)
        )

    if request.method == "POST" and request.POST.get("action") == "deactivate_project":
        if not library_user_can_assign_tasks_to_participants(request.user):
            messages.error(request, "当前角色无权管理项目")
            return redirect(reverse("file_library") + _file_library_query_string(tab, "", "", "", project_selected))
        try:
            pid = int(request.POST.get("project_id", "0"))
        except ValueError:
            pid = 0
        row = LibraryProject.objects.filter(pk=pid, is_active=True).first()
        if not row:
            messages.error(request, "项目不存在或已停用")
        else:
            row.is_active = False
            row.save(update_fields=["is_active", "updated_at"])
            messages.success(request, f"已停用项目：{row.name}")
        return redirect(reverse("file_library") + _file_library_query_string(tab, "", "", "", project_selected))

    post_project_ids = parse_project_ids(request.POST.getlist("project_ids"))
    if not post_project_ids and request.POST.get("project"):
        post_project_ids = parse_project_ids([request.POST.get("project", "")])

    if request.method == "POST" and request.POST.get("action") == "restore_library_file":
        df = request.POST.get("date_from", "").strip()
        dt = request.POST.get("date_to", "").strip()
        up = request.POST.get("uploader", "").strip()
        pj = (request.POST.get("project") or "").strip() or project_selected
        redir = reverse("file_library") + _file_library_query_string("trash", df, dt, up, pj)
        if not role_has(request.user, "perm_file_delete"):
            messages.error(request, "当前角色无权恢复文件")
            return redirect(redir)
        try:
            rid = int(request.POST.get("file_id", "0"))
        except ValueError:
            rid = 0
        lf = LibraryFile.all_objects.filter(pk=rid).first()
        if lf is None or lf.deleted_at is None:
            messages.error(request, "记录不存在或不在回收站中")
            return redirect(redir)
        if not library_file_access_allowed(request.user, lf):
            messages.error(request, "无权恢复该文件")
            return redirect(redir)
        if not library_user_may_delete_library_file(request.user, lf):
            messages.error(request, "只能恢复本人删除的文件")
            return redirect(redir)
        lf.deleted_at = None
        lf.save(update_fields=["deleted_at"])
        messages.success(request, "已从回收站恢复")
        return redirect(redir)

    if request.method == "POST" and request.POST.get("action") == "purge_library_file_permanent":
        df = request.POST.get("date_from", "").strip()
        dt = request.POST.get("date_to", "").strip()
        up = request.POST.get("uploader", "").strip()
        pj = (request.POST.get("project") or "").strip() or project_selected
        redir = reverse("file_library") + _file_library_query_string("trash", df, dt, up, pj)
        if not role_has(request.user, "perm_file_delete"):
            messages.error(request, "当前角色无权彻底删除")
            return redirect(redir)
        try:
            rid = int(request.POST.get("file_id", "0"))
        except ValueError:
            rid = 0
        lf = LibraryFile.all_objects.filter(pk=rid, deleted_at__isnull=False).first()
        if lf is None:
            messages.error(request, "未找到回收站中的文件")
            return redirect(redir)
        if not library_file_access_allowed(request.user, lf):
            messages.error(request, "无权删除该文件")
            return redirect(redir)
        if not library_user_may_delete_library_file(request.user, lf):
            messages.error(request, "只能彻底删除本人删除的文件；删除他人回收项需系统管理员权限。")
            return redirect(redir)
        hard_delete_library_file_disk_and_row(lf)
        messages.success(request, "已永久删除")
        return redirect(redir)

    if request.method == "POST" and request.POST.get("action") == "restore_library_files_bulk":
        df = request.POST.get("date_from", "").strip()
        dt = request.POST.get("date_to", "").strip()
        up = request.POST.get("uploader", "").strip()
        pj = (request.POST.get("project") or "").strip() or project_selected
        redir = reverse("file_library") + _file_library_query_string("trash", df, dt, up, pj)
        if not role_has(request.user, "perm_file_delete"):
            messages.error(request, "当前角色无权恢复文件")
            return redirect(redir)
        id_ints = _file_library_post_ids_unique(request.POST)
        if not id_ints:
            messages.error(request, "请勾选要恢复的文件")
            return redirect(redir)
        restored = 0
        skipped = 0
        for rid in id_ints:
            lf = LibraryFile.all_objects.filter(pk=rid).first()
            if lf is None or lf.deleted_at is None:
                skipped += 1
                continue
            if not library_file_access_allowed(request.user, lf):
                skipped += 1
                continue
            if not library_user_may_delete_library_file(request.user, lf):
                skipped += 1
                continue
            lf.deleted_at = None
            lf.save(update_fields=["deleted_at"])
            restored += 1
        if restored and skipped:
            messages.success(request, f"已从回收站恢复 {restored} 个文件")
            messages.warning(request, f"另有 {skipped} 条未处理（不存在、不在回收站或无权操作）。")
        elif restored:
            messages.success(request, f"已从回收站恢复 {restored} 个文件")
        else:
            messages.error(request, "所选记录均无法恢复")
        return redirect(redir)

    if request.method == "POST" and request.POST.get("action") == "purge_library_files_permanent_bulk":
        df = request.POST.get("date_from", "").strip()
        dt = request.POST.get("date_to", "").strip()
        up = request.POST.get("uploader", "").strip()
        pj = (request.POST.get("project") or "").strip() or project_selected
        redir = reverse("file_library") + _file_library_query_string("trash", df, dt, up, pj)
        if not role_has(request.user, "perm_file_delete"):
            messages.error(request, "当前角色无权彻底删除")
            return redirect(redir)
        id_ints = _file_library_post_ids_unique(request.POST)
        if not id_ints:
            messages.error(request, "请勾选要彻底删除的文件")
            return redirect(redir)
        purged = 0
        skipped = 0
        for rid in id_ints:
            lf = LibraryFile.all_objects.filter(pk=rid, deleted_at__isnull=False).first()
            if lf is None:
                skipped += 1
                continue
            if not library_file_access_allowed(request.user, lf):
                skipped += 1
                continue
            if not library_user_may_delete_library_file(request.user, lf):
                skipped += 1
                continue
            hard_delete_library_file_disk_and_row(lf)
            purged += 1
        if purged and skipped:
            messages.success(request, f"已永久删除 {purged} 个文件")
            messages.warning(request, f"另有 {skipped} 条未删除（不存在、不在回收站或无权操作）。")
        elif purged:
            messages.success(request, f"已永久删除 {purged} 个文件")
        else:
            messages.error(request, "所选记录均无法彻底删除")
        return redirect(redir)

    if request.method == "POST" and request.POST.get("action") == "upload":
        if not role_can_upload_library_category(request.user, LibraryFile.CATEGORY_UPLOAD):
            messages.error(request, "当前角色无权向「待识别文件」分类上传")
            return redirect(
                reverse("file_library") + _file_library_query_string(tab, "", "", "")
            )
        upx = library_upload_blocked_revoked_projects(request.user, post_project_ids)
        if upx:
            messages.error(request, upx)
            df = request.POST.get("date_from", "").strip()
            dt = request.POST.get("date_to", "").strip()
            up = request.POST.get("uploader", "").strip()
            return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up, project_selected))
        files = request.FILES.getlist("files")
        if not files:
            messages.error(request, "请选择要上传的文件")
        else:
            n = _persist_binary_library_files(
                request, files, LibraryFile.CATEGORY_UPLOAD, project_ids=post_project_ids
            )
            if n:
                messages.success(request, f"已上传 {n} 个文件")
        df = request.POST.get("date_from", "").strip()
        dt = request.POST.get("date_to", "").strip()
        up = request.POST.get("uploader", "").strip()
        return redirect(reverse("file_library") + _file_library_query_string("ocr", df, dt, up, project_selected))

    if request.method == "POST" and request.POST.get("action") == "upload_json":
        if not role_can_upload_library_category(request.user, LibraryFile.CATEGORY_JSON):
            messages.error(request, "当前角色无权上传到「数据文件」分类")
            return redirect(
                reverse("file_library") + _file_library_query_string("json", "", "", "")
            )
        upx = library_upload_blocked_revoked_projects(request.user, post_project_ids)
        if upx:
            messages.error(request, upx)
            df = request.POST.get("date_from", "").strip()
            dt = request.POST.get("date_to", "").strip()
            up = request.POST.get("uploader", "").strip()
            return redirect(
                reverse("file_library") + _file_library_query_string("json", df, dt, up, project_selected)
            )
        files = request.FILES.getlist("files")
        if not files:
            messages.error(request, "请选择要上传的数据文件（.json）")
        else:
            cap = library_user_file_library_quota_bytes(request.user)
            if cap is not None:
                tot = sum((getattr(f, "size", None) or 0) for f in files)
                if library_user_file_library_usage_bytes(request.user) + tot > cap:
                    messages.error(request, "已超过文件库容量配额，无法继续上传。")
                    df = request.POST.get("date_from", "").strip()
                    dt = request.POST.get("date_to", "").strip()
                    up = request.POST.get("uploader", "").strip()
                    return redirect(
                        reverse("file_library") + _file_library_query_string("json", df, dt, up, project_selected)
                    )
            pipeline_service.ensure_file_library_dirs()
            json_dir = Path(settings.FILE_LIBRARY_JSON_DIR)
            added = 0
            for f in files:
                raw = f.read()
                if not raw:
                    messages.warning(request, f"跳过空文件: {f.name}")
                    continue
                safe = _safe_filename(f.name)
                if not safe.lower().endswith(".json"):
                    messages.warning(request, f"已跳过（仅支持 .json）: {safe}")
                    continue
                try:
                    text = raw.decode("utf-8-sig")
                    json_std.loads(text)
                except (UnicodeDecodeError, json_std.JSONDecodeError):
                    messages.warning(request, f"文件内容格式不正确，已跳过: {safe}")
                    continue
                uid = uuid.uuid4().hex
                disk_name = f"{uid}_{safe}"
                rel = f"json/{disk_name}"
                abs_p = json_dir / disk_name
                abs_p.write_bytes(raw)
                lf = LibraryFile.objects.create(
                    original_name=safe,
                    relative_path=rel,
                    category=LibraryFile.CATEGORY_JSON,
                    size=len(raw),
                    created_by=request.user,
                )
                attach_files_to_projects([lf.pk], post_project_ids, request.user)
                added += 1
            if added:
                messages.success(request, f"已成功上传 {added} 个文件")
        df = request.POST.get("date_from", "").strip()
        dt = request.POST.get("date_to", "").strip()
        up = request.POST.get("uploader", "").strip()
        return redirect(reverse("file_library") + _file_library_query_string("json", df, dt, up, project_selected))

    if request.method == "POST" and request.POST.get("action") == "upload_template":
        if not role_can_upload_library_category(
            request.user, LibraryFile.CATEGORY_TEMPLATE
        ):
            messages.error(request, "当前角色无权向「模板」分类上传")
            return redirect(
                reverse("file_library") + _file_library_query_string(tab, "", "", "")
            )
        upx = library_upload_blocked_revoked_projects(request.user, post_project_ids)
        if upx:
            messages.error(request, upx)
            df = request.POST.get("date_from", "").strip()
            dt = request.POST.get("date_to", "").strip()
            up = request.POST.get("uploader", "").strip()
            return redirect(
                reverse("file_library") + _file_library_query_string("template", df, dt, up, project_selected)
            )
        files = request.FILES.getlist("files")
        if not files:
            messages.error(request, "请选择要上传的文件")
        else:
            created, skipped = save_library_binary_uploads(
                request.user, files, LibraryFile.CATEGORY_TEMPLATE, project_ids=post_project_ids
            )
            for s in skipped:
                fn = s.get("filename") or "(无名)"
                messages.warning(request, f"跳过文件 {fn}: {s.get('reason', '')}")
            for row in created:
                register_tour_library_file(request, int(row["id"]))
            if created:
                messages.success(request, f"已上传 {len(created)} 个模板文件")
        df = request.POST.get("date_from", "").strip()
        dt = request.POST.get("date_to", "").strip()
        up = request.POST.get("uploader", "").strip()
        return redirect(
            reverse("file_library") + _file_library_query_string("template", df, dt, up, project_selected)
        )

    if request.method == "POST" and request.POST.get("action") == "upload_site_record":
        if not role_can_upload_library_category(
            request.user, LibraryFile.CATEGORY_SITE_RECORD
        ):
            messages.error(request, "当前角色无权向「现场记录」分类上传")
            return redirect(
                reverse("file_library") + _file_library_query_string(tab, "", "", "")
            )
        upx = library_upload_blocked_revoked_projects(request.user, post_project_ids)
        if upx:
            messages.error(request, upx)
            df = request.POST.get("date_from", "").strip()
            dt = request.POST.get("date_to", "").strip()
            up = request.POST.get("uploader", "").strip()
            return redirect(
                reverse("file_library") + _file_library_query_string("site_record", df, dt, up, project_selected)
            )
        files = request.FILES.getlist("files")
        if not files:
            messages.error(request, "请选择要上传的文件")
        else:
            n = _persist_binary_library_files(
                request, files, LibraryFile.CATEGORY_SITE_RECORD, project_ids=post_project_ids
            )
            if n:
                messages.success(request, f"已上传 {n} 个现场记录文件")
        df = request.POST.get("date_from", "").strip()
        dt = request.POST.get("date_to", "").strip()
        up = request.POST.get("uploader", "").strip()
        return redirect(
            reverse("file_library") + _file_library_query_string("site_record", df, dt, up, project_selected)
        )

    if request.method == "POST" and request.POST.get("action") == "upload_report":
        if not role_can_upload_library_category(
            request.user, LibraryFile.CATEGORY_REPORT
        ):
            messages.error(request, "当前角色无权向「报告」分类上传")
            return redirect(
                reverse("file_library") + _file_library_query_string(tab, "", "", "")
            )
        upx = library_upload_blocked_revoked_projects(request.user, post_project_ids)
        if upx:
            messages.error(request, upx)
            df = request.POST.get("date_from", "").strip()
            dt = request.POST.get("date_to", "").strip()
            up = request.POST.get("uploader", "").strip()
            return redirect(
                reverse("file_library") + _file_library_query_string("report", df, dt, up, project_selected)
            )
        files = request.FILES.getlist("files")
        if not files:
            messages.error(request, "请选择要上传的文件")
        else:
            n = _persist_binary_library_files(
                request, files, LibraryFile.CATEGORY_REPORT, project_ids=post_project_ids
            )
            if n:
                messages.success(request, f"已上传 {n} 个报告文件")
        df = request.POST.get("date_from", "").strip()
        dt = request.POST.get("date_to", "").strip()
        up = request.POST.get("uploader", "").strip()
        return redirect(
            reverse("file_library") + _file_library_query_string("report", df, dt, up, project_selected)
        )

    if request.method == "POST" and request.POST.get("action") == "upload_attachment":
        if not role_can_upload_library_category(
            request.user, LibraryFile.CATEGORY_ATTACHMENT
        ):
            messages.error(request, "当前角色无权向「附件」分类上传")
            return redirect(
                reverse("file_library") + _file_library_query_string(tab, "", "", "")
            )
        upx = library_upload_blocked_revoked_projects(request.user, post_project_ids)
        if upx:
            messages.error(request, upx)
            df = request.POST.get("date_from", "").strip()
            dt = request.POST.get("date_to", "").strip()
            up = request.POST.get("uploader", "").strip()
            return redirect(
                reverse("file_library") + _file_library_query_string("attachment", df, dt, up, project_selected)
            )
        files = request.FILES.getlist("files")
        if not files:
            messages.error(request, "请选择要上传的附件")
        else:
            n = _persist_binary_library_files(
                request, files, LibraryFile.CATEGORY_ATTACHMENT, project_ids=post_project_ids
            )
            if n:
                messages.success(request, f"已上传 {n} 个附件")
        df = request.POST.get("date_from", "").strip()
        dt = request.POST.get("date_to", "").strip()
        up = request.POST.get("uploader", "").strip()
        return redirect(
            reverse("file_library") + _file_library_query_string("attachment", df, dt, up, project_selected)
        )

    if request.method == "POST" and request.POST.get("action") == "manual_export_submit_pdf":
        if tab != "inspection_submit":
            messages.error(request, "仅支持在「检测提交」分类执行手动导出 PDF")
            df = request.POST.get("date_from", "").strip()
            dt = request.POST.get("date_to", "").strip()
            up = request.POST.get("uploader", "").strip()
            return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up, project_selected))
        if not library_user_may_filled_pdf_toolchain(request.user):
            messages.error(request, "当前角色无权执行手动导出 PDF")
            df = request.POST.get("date_from", "").strip()
            dt = request.POST.get("date_to", "").strip()
            up = request.POST.get("uploader", "").strip()
            return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up, project_selected))
        ids = []
        for x in request.POST.getlist("ids"):
            try:
                ids.append(int(x))
            except (TypeError, ValueError):
                continue
        ids = list(dict.fromkeys([i for i in ids if i > 0]))
        if not ids:
            messages.error(request, "请先勾选至少一条检测提交记录")
            df = request.POST.get("date_from", "").strip()
            dt = request.POST.get("date_to", "").strip()
            up = request.POST.get("uploader", "").strip()
            return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up, project_selected))

        from apps.api.inspection_views import (
            _build_filled_template_fields_for_task,
            _pick_submit_generation_tasks,
            _persist_filled_pdf_from_submit,
        )
        from apps.api.inspection_pdf_service import dedupe_inspection_submit_selection_latest_per_site_record

        id_order = {pk: i for i, pk in enumerate(ids)}
        files_list = list(
            LibraryFile.objects.filter(pk__in=ids, category=LibraryFile.CATEGORY_INSPECTION_SUBMIT)
            .select_related("created_by")
            .prefetch_related("library_tasks")
        )
        files_list.sort(key=lambda f: id_order.get(f.pk, 10**9))
        files_list, dedupe_notes = dedupe_inspection_submit_selection_latest_per_site_record(files_list)
        for msg in dedupe_notes:
            messages.info(request, msg)

        success_count = 0
        skipped_count = 0
        skip_reasons = []
        non_json_skipped = 0
        for lf in files_list:
            if not library_file_access_allowed(request.user, lf):
                skipped_count += 1
                skip_reasons.append(f"{lf.original_name}: 无权访问该文件")
                continue
            if not (lf.original_name or "").strip().lower().endswith(".json"):
                non_json_skipped += 1
                continue
            try:
                p = pipeline_service.library_absolute_path(lf.relative_path)
                payload = json_std.loads(p.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                skipped_count += 1
                skip_reasons.append(f"{lf.original_name}: 文件读取或格式解析失败")
                continue
            required_keys = {"reportInfo", "hospitalInfo", "equipmentInfo", "testResult"}
            if not isinstance(payload, dict) or not required_keys.issubset(set(payload.keys())):
                skipped_count += 1
                skip_reasons.append(f"{lf.original_name}: 不是有效的检测提交原始数据")
                continue
            if lf.link_entity != LibraryFile.LINK_ENTITY_INSPECTION_CASE or not lf.link_object_id:
                skipped_count += 1
                skip_reasons.append(f"{lf.original_name}: 未关联 inspection_case")
                continue
            case = InspectionCase.objects.select_related("library_project").filter(pk=lf.link_object_id).first()
            if case is None or not case.library_project_id:
                skipped_count += 1
                skip_reasons.append(f"{lf.original_name}: 关联案件或项目不存在")
                continue
            generated_any = False
            for task_obj in _pick_submit_generation_tasks(case.case_no, case.library_project):
                filled_fields, template_pdf_id, fill_reason, template_json_name = _build_filled_template_fields_for_task(
                    task_obj,
                    payload,
                    project=case.library_project,
                    task_no=case.case_no,
                    inspection_case=case,
                )
                if not filled_fields:
                    skip_reasons.append(
                        f"{lf.original_name} / {task_obj.code}({task_obj.output_target}): {fill_reason or '模板填充失败'}"
                    )
                    continue
                ok, pdf_reason, _ = _persist_filled_pdf_from_submit(
                    request.user,
                    case.case_no,
                    case,
                    case.library_project,
                    filled_fields,
                    template_pdf_id=template_pdf_id,
                    template_json_name=template_json_name,
                    task_obj=task_obj,
                )
                if not ok:
                    skip_reasons.append(
                        f"{lf.original_name} / {task_obj.code}({task_obj.output_target}): {pdf_reason or 'PDF 生成失败'}"
                    )
                    continue
                if pdf_reason:
                    messages.warning(request, f"{lf.original_name} / {task_obj.code}: {pdf_reason}")
                generated_any = True
            if generated_any:
                success_count += 1
            else:
                skipped_count += 1
        if non_json_skipped:
            messages.info(
                request,
                f"手动导出 PDF 仅处理检测提交 JSON 文件：已跳过 {non_json_skipped} 个非 JSON 附件（如签名图）。",
            )
        if success_count:
            messages.success(request, f"已为 {success_count} 条检测提交执行手动导出 PDF")
        if skipped_count:
            messages.warning(request, f"有 {skipped_count} 条记录未导出")
            for reason in skip_reasons[:20]:
                messages.warning(request, reason)
            if len(skip_reasons) > 20:
                messages.warning(request, f"其余 {len(skip_reasons) - 20} 条原因已省略")
        df = request.POST.get("date_from", "").strip()
        dt = request.POST.get("date_to", "").strip()
        up = request.POST.get("uploader", "").strip()
        return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up, project_selected))

    if request.method == "POST" and request.POST.get("action") == "manual_export_report_from_site_record":
        if tab not in ("inspection_submit", "site_record"):
            messages.error(request, "仅支持在「检测提交」或「现场记录」分类执行手动导出报告")
            df = request.POST.get("date_from", "").strip()
            dt = request.POST.get("date_to", "").strip()
            up = request.POST.get("uploader", "").strip()
            return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up, project_selected))
        if not library_user_may_filled_pdf_toolchain(request.user):
            messages.error(request, "当前角色无权执行手动导出报告")
            df = request.POST.get("date_from", "").strip()
            dt = request.POST.get("date_to", "").strip()
            up = request.POST.get("uploader", "").strip()
            return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up, project_selected))

        ids = []
        for x in request.POST.getlist("ids"):
            try:
                ids.append(int(x))
            except (TypeError, ValueError):
                continue
        ids = list(dict.fromkeys([i for i in ids if i > 0]))
        if not ids:
            empty_hint = (
                "请先勾选至少一条现场记录 PDF"
                if tab == "site_record"
                else "请先勾选至少一条检测提交记录"
            )
            messages.error(request, empty_hint)
            df = request.POST.get("date_from", "").strip()
            dt = request.POST.get("date_to", "").strip()
            up = request.POST.get("uploader", "").strip()
            return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up, project_selected))

        from apps.api.inspection_pdf_service import (
            _build_filled_template_fields_for_task,
            _persist_filled_pdf_from_submit,
            _resolve_report_task_for_case,
            accumulate_inspection_payloads_ordered_merge,
            dedupe_inspection_submit_selection_latest_per_site_record,
            load_report_payload_for_manual_export,
            resolve_manual_report_device_count,
            resolve_submit_payload_for_site_record_pdf_lf,
            site_record_task_count_for_project,
        )

        submit_required = {"reportInfo", "hospitalInfo", "equipmentInfo", "testResult"}
        id_order = {pk: i for i, pk in enumerate(ids)}
        errors: list[str] = []
        rows_ok: list = []

        if tab == "inspection_submit":
            raw_submit_files = list(
                LibraryFile.objects.filter(pk__in=ids, category=LibraryFile.CATEGORY_INSPECTION_SUBMIT).select_related(
                    "created_by"
                ).prefetch_related("library_tasks")
            )
            loaded_submit_pks = {f.pk for f in raw_submit_files}
            raw_submit_files.sort(key=lambda f: id_order.get(f.pk, 10**9))
            files_list, dedupe_notes = dedupe_inspection_submit_selection_latest_per_site_record(raw_submit_files)
            for msg in dedupe_notes:
                messages.info(request, msg)
            json_files = [lf for lf in files_list if (lf.original_name or "").strip().lower().endswith(".json")]
            if len(json_files) < len(files_list):
                messages.info(
                    request,
                    f"手动导出报告仅汇总检测提交 JSON：已忽略 {len(files_list) - len(json_files)} 个非 JSON 附件。",
                )
            files_list = json_files
            for pk in ids:
                if pk not in loaded_submit_pks:
                    errors.append(f"文件 id={pk} 不存在或不是「检测提交」分类")
            for lf in files_list:
                if not library_file_access_allowed(request.user, lf):
                    errors.append(f"{lf.original_name}: 无权访问该文件")
                    continue
                if lf.link_entity != LibraryFile.LINK_ENTITY_INSPECTION_CASE or not lf.link_object_id:
                    errors.append(f"{lf.original_name}: 未关联 inspection_case")
                    continue
                case = InspectionCase.objects.select_related("library_project").filter(pk=lf.link_object_id).first()
                if case is None or not case.library_project_id:
                    errors.append(f"{lf.original_name}: 关联案件或项目不存在")
                    continue
                try:
                    p = pipeline_service.library_absolute_path(lf.relative_path)
                    submit_payload = json_std.loads(p.read_text(encoding="utf-8", errors="replace"))
                except Exception as exc:
                    errors.append(f"{lf.original_name}: 文件读取或格式解析失败 ({exc})")
                    continue
                if not isinstance(submit_payload, dict) or not submit_required.issubset(submit_payload.keys()):
                    errors.append(
                        f"{lf.original_name}: 检测提交数据不完整（缺少报告、受检信息、设备或检测结果等必要内容）"
                    )
                    continue
                rows_ok.append((lf, case, submit_payload))
        else:
            files_list = list(
                LibraryFile.objects.filter(pk__in=ids, category=LibraryFile.CATEGORY_SITE_RECORD).select_related(
                    "created_by"
                )
            )
            files_list.sort(key=lambda f: id_order.get(f.pk, 10**9))
            found_pks = {f.pk for f in files_list}
            for pk in ids:
                if pk not in found_pks:
                    errors.append(f"文件 id={pk} 不存在或不是「现场记录」分类")
            for lf in files_list:
                if not library_file_access_allowed(request.user, lf):
                    errors.append(f"{lf.original_name}: 无权访问该文件")
                    continue
                submit_payload, case, err = resolve_submit_payload_for_site_record_pdf_lf(lf)
                if err or case is None or not isinstance(submit_payload, dict):
                    errors.append(f"{lf.original_name}: {err or '未能解析为检测提交数据'}")
                    continue
                rows_ok.append((lf, case, submit_payload))

        df = request.POST.get("date_from", "").strip()
        dt = request.POST.get("date_to", "").strip()
        up = request.POST.get("uploader", "").strip()
        redir = reverse("file_library") + _file_library_query_string(tab, df, dt, up, project_selected)

        if errors:
            messages.error(request, "所选文件存在问题，未导出报告（同一项目仅生成一份报告）")
            for msg in errors[:25]:
                messages.warning(request, msg)
            if len(errors) > 25:
                messages.warning(request, f"其余 {len(errors) - 25} 条原因已省略")
            return redirect(redir)

        if not rows_ok:
            messages.error(
                request,
                "没有可用的现场记录，或缺少可用的检测提交数据",
            )
            return redirect(redir)

        project_ids = {r[1].library_project_id for r in rows_ok}
        if len(project_ids) != 1:
            messages.error(
                request,
                "勾选的文件对应了多个项目；一个项目只导出一份报告，请仅选择同一项目下的记录后再试",
            )
            return redirect(redir)

        cases_for_load: list[InspectionCase] = []
        seen_case_pk: set[int] = set()
        for _lf, c, _p in rows_ok:
            if int(c.pk) in seen_case_pk:
                continue
            seen_case_pk.add(int(c.pk))
            cases_for_load.append(c)

        project = cases_for_load[0].library_project
        persist_case = min(cases_for_load, key=lambda x: (x.case_no or ""))
        report_task = _resolve_report_task_for_case(persist_case.case_no, project)
        if report_task is None:
            messages.error(request, "该项目下未找到报告任务，无法导出报告")
            return redirect(redir)

        # 多份 JSON 深度合并：同路径以后勾选为准，避免「整段 testResult 被第一份独占」导致后份完全无效
        merged_submit = accumulate_inspection_payloads_ordered_merge([p for _lf, _c, p in rows_ok])

        # 勾选的多份检测提交 JSON，或现场记录 PDF（解析为同 taskNo 的 JSON）→ 汇总 → 填入同一份报告 PDF
        source_payload, merge_note = load_report_payload_for_manual_export(
            cases_for_load,
            project,
            report_task,
            merged_submit,
            submit_merge_is_authoritative=True,
        )
        if not isinstance(source_payload, dict) or not source_payload:
            messages.error(request, merge_note or "报告数据汇总失败")
            return redirect(redir)
        if merge_note:
            messages.info(request, merge_note)

        # taskNo 在 ordered_merge 中已锚定第一份，与报告/占位绑定一致
        task_no_for_fill = str(merged_submit.get("taskNo") or "").strip() or str(persist_case.case_no or "").strip()

        distinct_case_n = len(cases_for_load)
        n_site_tasks = site_record_task_count_for_project(project)
        manual_device_count = resolve_manual_report_device_count(distinct_case_n)
        if n_site_tasks > 0 and distinct_case_n > n_site_tasks:
            messages.warning(
                request,
                f"勾选涉及 {distinct_case_n} 个不同案件，但本项目在任务管理中仅关联 {n_site_tasks} 个现场记录类任务，请核对项目—任务配置。",
            )

        filled_fields, template_pdf_id, fill_reason, template_json_name = _build_filled_template_fields_for_task(
            report_task,
            source_payload,
            project=project,
            task_no=task_no_for_fill,
            inspection_case=persist_case,
            manual_device_count=manual_device_count,
            ordered_submit_payloads=[p for _lf, _c, p in rows_ok],
        )
        if not filled_fields:
            messages.error(
                request,
                f"{report_task.code}({report_task.output_target}): {fill_reason or '模板填充失败'}",
            )
            return redirect(redir)

        ok, pdf_reason, _ = _persist_filled_pdf_from_submit(
            request.user,
            task_no_for_fill,
            persist_case,
            project,
            filled_fields,
            template_pdf_id=template_pdf_id,
            template_json_name=template_json_name,
            task_obj=report_task,
        )
        if not ok:
            messages.error(
                request,
                f"{report_task.code}({report_task.output_target}): {pdf_reason or '报告导出失败'}",
            )
        else:
            n_files = len(rows_ok)
            case_nos = ", ".join(sorted({c.case_no for c in cases_for_load}))
            src_lbl = "现场记录 PDF" if tab == "site_record" else "检测提交数据"
            msg = (
                f"已为项目 {project.code} 导出 1 份报告（"
                f"不同案件 {distinct_case_n} 个（受检台数以此为准），勾选{src_lbl} {n_files} 份；"
                f"项目任务管理中现场记录类任务 {n_site_tasks} 个；"
                f"多份数据：testResult 等仍按勾选顺序合并；dynamicData 按份与对应现场模板解析为占位符词条后汇入同一份报告（勿依赖跨模板 f 号）；taskNo 取第一份；案件号：{case_nos}）"
            )
            if pdf_reason:
                msg = f"{msg}；提示：{pdf_reason}"
            messages.success(request, msg)
        return redirect(redir)

    if request.method == "POST" and request.POST.get("action") == "merge_reports":
        df = request.POST.get("date_from", "").strip()
        dt = request.POST.get("date_to", "").strip()
        up = request.POST.get("uploader", "").strip()
        redir = reverse("file_library") + _file_library_query_string("report", df, dt, up, project_selected)
        if tab != "report":
            messages.error(request, "仅支持在「报告」分类执行报告合并")
            return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up, project_selected))
        if not library_user_may_filled_pdf_toolchain(request.user):
            messages.error(request, "当前角色无权执行报告合并")
            return redirect(redir)
        if not library_export_merge_allowed_under_own_files_scope(request.user, project_selected_id):
            messages.error(request, "当前账号范围下不支持报告合并")
            return redirect(redir)
        ids_merge = []
        for x in request.POST.getlist("ids"):
            try:
                ids_merge.append(int(x))
            except (TypeError, ValueError):
                continue
        ids_merge = list(dict.fromkeys([i for i in ids_merge if i > 0]))
        if len(ids_merge) < 2:
            messages.warning(request, "请至少勾选两份「报告」分类下的文件后再试")
            return redirect(redir)

        from utils.pdf_merge import merge_report_pdfs_header_toc_sections
        from apps.api.inspection_pdf_service import (
            build_report_merge_overlay,
            collect_report_merge_source_rows,
            commission_no_from_merge_source_rows,
        )
        from apps.api.inspection_report_make import build_merged_report_pdf_original_name

        id_order = {pk: i for i, pk in enumerate(ids_merge)}
        report_files = list(
            LibraryFile.objects.filter(pk__in=ids_merge, category=LibraryFile.CATEGORY_REPORT).select_related(
                "created_by"
            )
        )
        report_files.sort(key=lambda f: id_order.get(f.pk, 10**9))
        found_pks = {f.pk for f in report_files}
        merge_errors: list[str] = []
        for pk in ids_merge:
            if pk not in found_pks:
                merge_errors.append(f"文件 id={pk} 不存在或不是「报告」分类")

        paths: list[str] = []
        titles: list[str] = []
        project_pids_union: set[int] = set()
        for lf in report_files:
            if not library_file_access_allowed(request.user, lf):
                merge_errors.append(f"{lf.original_name}: 无权访问该文件")
                continue
            if not (lf.original_name or "").lower().endswith(".pdf"):
                merge_errors.append(f"{lf.original_name}: 仅支持合并 PDF 报告")
                continue
            abs_p = pipeline_service.library_absolute_path(lf.relative_path)
            paths.append(str(abs_p))
            titles.append(lf.original_name)
            project_pids_union.update(lf.projects.values_list("pk", flat=True))

        if merge_errors:
            messages.error(request, "所选报告存在问题，未执行合并")
            for msg in merge_errors[:25]:
                messages.warning(request, msg)
            return redirect(redir)
        if len(paths) < 2:
            messages.warning(request, "有效可合并的报告 PDF 不足两份")
            return redirect(redir)
        if len(project_pids_union) > 1:
            messages.error(request, "所选报告须属于同一项目，请仅勾选同一项目下的报告后再试")
            return redirect(redir)

        report_commission_code = ""
        if len(project_pids_union) == 1:
            _pid = next(iter(project_pids_union))
            _code = LibraryProject.objects.filter(pk=_pid).values_list("code", flat=True).first()
            if _code:
                report_commission_code = str(_code)

        merge_rows = collect_report_merge_source_rows(report_files)
        merge_overlay, merge_overlay_hint = build_report_merge_overlay(
            report_files, merge_time=timezone.now(), source_rows=merge_rows
        )
        pdf_bytes, merge_err = merge_report_pdfs_header_toc_sections(
            paths,
            section_titles=titles,
            commission_no_suffix6=report_commission_code,
            merge_overlay=merge_overlay,
        )
        if merge_err or not pdf_bytes:
            messages.error(request, merge_err or "报告合并失败")
            return redirect(redir)

        com_no = commission_no_from_merge_source_rows(merge_rows)
        project_obj = (
            LibraryProject.objects.filter(pk=next(iter(project_pids_union))).first()
            if len(project_pids_union) == 1
            else None
        )
        fname = (
            build_merged_report_pdf_original_name(project_obj, com_no)
            if project_obj is not None
            else f"合并报告-{timezone.now().strftime('%Y%m%d-%H%M%S')}.pdf"
        )
        wrapped = type("UploadLike", (), {"read": lambda self: pdf_bytes, "name": fname})()
        lf0 = report_files[0]
        created, _skipped = save_library_binary_uploads(
            request.user,
            [wrapped],
            LibraryFile.CATEGORY_REPORT,
            link_entity=(lf0.link_entity or "") if lf0.link_entity else "",
            link_object_id=lf0.link_object_id,
            project_ids=sorted(project_pids_union),
            enforce_storage_quota=False,
        )
        if not created:
            messages.error(request, "合并 PDF 已生成但保存到文件库失败")
            return redirect(redir)
        messages.success(
            request,
            f"已生成并保存合并报告：{fname}（{merge_overlay_hint}；"
            f"封面报告日期为合并当日；合并报告名称与受检台数、评价及受检编号序列表已按合并规则叠印；"
            f"插入目录为报告第 2 页，「三、检测结果」从报告第 3 页起；"
            f"目录页眉「共 y 页」为 PDF 总页数减封面与声明 2 页；共合并 {len(paths)} 份）",
        )
        return redirect(redir)

    date_err, d0, d1, date_from, date_to = _parse_file_library_date_range(request)
    if date_err:
        messages.warning(request, date_err)
        if request.GET.get("date_from") or request.GET.get("date_to"):
            return redirect(reverse("file_library") + f"?tab={tab}")
        d0, d1, date_from, date_to = None, None, "", ""

    if tab == "trash":
        qs = LibraryFile.all_objects.filter(deleted_at__isnull=False).select_related("created_by").prefetch_related(
            "projects", "library_tasks"
        )
        is_lib_admin = bool(getattr(request.user, "is_superuser", False))
        if not is_lib_admin:
            try:
                rc = request.user.profile.role.code if request.user.profile.role_id else ""
                is_lib_admin = rc in ("super_admin", "admin")
            except Exception:
                pass
        if not is_lib_admin:
            qs = qs.filter(created_by=request.user)
        if d0 is not None and d1 is not None:
            qs = qs.filter(deleted_at__date__gte=d0, deleted_at__date__lte=d1)
        projects_qs = LibraryProject.objects.filter(is_active=True).order_by("code")
        if library_scope_own_files_only(request.user):
            ap = library_user_scoped_project_ids(request.user)
            projects_qs = projects_qs.filter(pk__in=ap) if ap else projects_qs.none()
        if co_path and not project_selected_id:
            commission_org_rows_tr = list(commission_orgs_active_queryset())
            _org_by_id_tr, _, _ = index_commission_orgs(commission_org_rows_tr)
            org_pids_tr = project_ids_for_commission_org_path(
                co_path, org_by_id=_org_by_id_tr, projects_qs=projects_qs
            )
            if org_pids_tr is not None:
                if org_pids_tr:
                    has_any_project = LibraryFileProject.objects.filter(library_file_id=OuterRef("pk"))
                    qs = (
                        qs.annotate(_fl_has_any_project=Exists(has_any_project))
                        .filter(Q(projects__id__in=org_pids_tr) | Q(_fl_has_any_project=False))
                        .distinct()
                    )
                else:
                    qs = qs.none()
        files = list(qs.order_by("-deleted_at", "-id"))
        _annotate_file_library_display(request.user, files)
        usage_b = library_user_file_library_usage_bytes(request.user)
        quota_b = library_user_file_library_quota_bytes(request.user)
        commission_org_groups_trash = _group_library_projects_by_commission_org(list(projects_qs))
        commission_org_rows_trash = list(commission_orgs_active_queryset())
        commission_org_tree_nodes_trash = build_commission_org_nav_tree(
            commission_org_rows_trash,
            list(projects_qs),
            co_path=co_path,
        )
        _org_by_id_nav_tr, _, _ = index_commission_orgs(commission_org_rows_trash)
        selected_co_org_id_tr = resolve_org_id_from_segments(
            parse_commission_path_segments(co_path), _org_by_id_nav_tr
        )
        explorer_co_org_tr = (
            _org_by_id_nav_tr.get(selected_co_org_id_tr) if selected_co_org_id_tr else None
        )
        co_explorer_base_tr = reverse("file_library") + _file_library_query_string(
            tab,
            date_from,
            date_to,
            "",
            project_selected,
            commission_org_raw,
            fl_path=fl_path_early,
        )
        fl_base_tr = reverse("file_library") + _file_library_query_string(
            tab,
            date_from,
            date_to,
            "",
            project_selected,
            commission_org_raw,
            co_path=co_path,
        )
        return render(
            request,
            "core/file_library.html",
            {
                "projects": projects_qs,
                "project_selected": project_selected,
                "selected_commission_org": selected_commission_org,
                "commission_org_raw": commission_org_raw,
                "commission_org_groups": commission_org_groups_trash,
                "commission_org_nav": [
                    {
                        "label": label,
                        "slug": _commission_org_url_slug(label),
                        "projects": items,
                        "count": len(items),
                    }
                    for label, items in commission_org_groups_trash
                ],
                "commission_org_unset_label": _COMMISSION_ORG_UNSET_LABEL,
                "tab": tab,
                "file_library_tabs": list(_FILE_LIBRARY_TAB_DEFS),
                "files": files,
                "file_library_nested_groups": [],
                "file_library_nested_mode": "trash",
                "file_library_template_shared_browse": False,
                "file_library_table_colspan": 5,
                "date_from": date_from,
                "date_to": date_to,
                "date_filter_active": bool(d0 and d1),
                "uploader_selected": "",
                "uploader_choices": [],
                "file_scope_own_only": library_scope_own_files_only(request.user),
                "can_batch_delete": False,
                "file_library_row_selection": False,
                "can_manual_export_submit_pdf": False,
                "can_manual_export_report_from_site_record": False,
                "can_merge_reports": False,
                "trash_days_notice": 30,
                "file_library_usage_bytes": usage_b,
                "file_library_quota_bytes": quota_b,
                "file_library_quota_limited": quota_b is not None,
                "co_path": co_path,
                "commission_org_tree_nodes": commission_org_tree_nodes_trash,
                "co_explorer_base": co_explorer_base_tr,
                "explorer_co_org": explorer_co_org_tr,
                "fl_path": fl_path_early,
                "folder_tree_nodes": [],
                "explorer_base_url": fl_base_tr,
                "tree_root_label": "回收站",
                "file_library_use_explorer": False,
                "explorer_dnd_enabled": False,
            },
        )

    cat = _library_category_for_tab(tab)
    restricted = library_scope_own_files_only(request.user)

    uploader_selected = ""
    uploader_id = None
    if not restricted:
        raw_u = request.GET.get("uploader", "").strip()
        if raw_u:
            try:
                uploader_id = int(raw_u)
                uploader_selected = str(uploader_id)
            except ValueError:
                uploader_id = None
                uploader_selected = ""

    files = (
        LibraryFile.objects.filter(category=cat)
        .select_related("created_by")
        .prefetch_related("projects", "library_tasks")
    )
    if project_selected_id:
        has_any_project = LibraryFileProject.objects.filter(library_file_id=OuterRef("pk"))
        files = (
            files.annotate(_fl_has_any_project=Exists(has_any_project))
            .filter(Q(projects__id=project_selected_id) | Q(_fl_has_any_project=False))
            .distinct()
        )
    else:
        projects_qs_early = LibraryProject.objects.filter(is_active=True)
        if restricted:
            ap_early = library_user_scoped_project_ids(request.user)
            projects_qs_early = (
                projects_qs_early.filter(pk__in=ap_early) if ap_early else projects_qs_early.none()
            )
        org_pids: list[int] | None = None
        if co_path:
            commission_org_rows_early = list(commission_orgs_active_queryset())
            _org_by_id_early, _, _ = index_commission_orgs(commission_org_rows_early)
            org_pids = project_ids_for_commission_org_path(
                co_path, org_by_id=_org_by_id_early, projects_qs=projects_qs_early
            )
        elif selected_commission_org:
            org_pids = _library_project_ids_for_commission_org_display(
                selected_commission_org, projects_qs_early
            )
        if org_pids is not None:
            if org_pids:
                has_any_project = LibraryFileProject.objects.filter(library_file_id=OuterRef("pk"))
                files = (
                    files.annotate(_fl_has_any_project=Exists(has_any_project))
                    .filter(Q(projects__id__in=org_pids) | Q(_fl_has_any_project=False))
                    .distinct()
                )
            else:
                files = files.none()
    if restricted:
        if cat == LibraryFile.CATEGORY_TEMPLATE and library_user_may_browse_shared_library_templates(request.user):
            if not library_user_can_assign_tasks_to_participants(request.user):
                scoped_pids = library_user_scoped_project_ids(request.user)
                if scoped_pids:
                    task_ids = list(
                        LibraryTask.objects.filter(projects__id__in=scoped_pids)
                        .values_list("id", flat=True)
                        .distinct()
                    )
                    files = files.filter(
                        Q(created_by=request.user)
                        | Q(projects__id__in=scoped_pids)
                        | Q(library_tasks__id__in=task_ids)
                    ).distinct()
                else:
                    files = files.filter(created_by=request.user)
        else:
            scoped_pids = library_user_scoped_project_ids(request.user)
            if scoped_pids:
                files = files.filter(
                    Q(created_by=request.user) | Q(projects__id__in=scoped_pids)
                ).distinct()
            else:
                files = files.filter(created_by=request.user)
    elif uploader_id is not None:
        files = files.filter(created_by_id=uploader_id)

    if d0 is not None and d1 is not None:
        files = files.filter(created_at__date__gte=d0, created_at__date__lte=d1)

    files = files.order_by("-created_at", "-id")
    files = list(files)
    _annotate_file_library_display(request.user, files)
    if tab == "template":
        file_library_nested_groups = _nest_file_library_by_library_task(files)
        file_library_nested_mode = "library_task"
    elif tab == "inspection_submit":
        file_library_nested_groups = _nest_file_library_by_project_report_site(files)
        file_library_nested_mode = "project_report_site"
    else:
        file_library_nested_groups = _nest_file_library_by_project_report(files)
        file_library_nested_mode = "project_report"

    uploader_choices = []
    if not restricted:
        chooser_qs = LibraryFile.objects.filter(category=cat).exclude(
            created_by_id__isnull=True
        )
        if project_selected_id:
            has_any_project = LibraryFileProject.objects.filter(library_file_id=OuterRef("pk"))
            chooser_qs = (
                chooser_qs.annotate(_ch_has_any_project=Exists(has_any_project))
                .filter(Q(projects__id=project_selected_id) | Q(_ch_has_any_project=False))
                .distinct()
            )
        if d0 is not None and d1 is not None:
            chooser_qs = chooser_qs.filter(
                created_at__date__gte=d0, created_at__date__lte=d1
            )
        uid_list = list(chooser_qs.values_list("created_by_id", flat=True).distinct()[:500])
        uploader_choices = list(
            User.objects.filter(pk__in=uid_list)
            .order_by("username")
            .values("id", "username")
        )

    projects_qs = LibraryProject.objects.filter(is_active=True).order_by("code")
    if restricted:
        ap = library_user_scoped_project_ids(request.user)
        projects_qs = projects_qs.filter(pk__in=ap) if ap else projects_qs.none()

    commission_org_groups = _group_library_projects_by_commission_org(list(projects_qs))
    if selected_commission_org:
        org_names = {g[0] for g in commission_org_groups}
        if selected_commission_org not in org_names:
            selected_commission_org = ""
    commission_org_nav = [
        {
            "label": label,
            "slug": _commission_org_url_slug(label),
            "projects": items,
            "count": len(items),
        }
        for label, items in commission_org_groups
    ]

    fl_path = fl_path_early
    file_library_tree_mode = (request.GET.get("tree_mode") or "project").strip() or "project"
    tab_labels = {t["key"]: t["label"] for t in _FILE_LIBRARY_TAB_DEFS}
    fl_tab_label = tab_labels.get(tab, tab)

    commission_org_rows = list(commission_orgs_active_queryset())
    projects_list_for_org_nav = list(projects_qs)
    commission_org_tree_nodes = build_commission_org_nav_tree(
        commission_org_rows,
        projects_list_for_org_nav,
        co_path=co_path,
    )
    _org_by_id_nav, _, _ = index_commission_orgs(commission_org_rows)
    selected_co_org_id = resolve_org_id_from_segments(
        parse_commission_path_segments(co_path), _org_by_id_nav
    )
    explorer_co_org = _org_by_id_nav.get(selected_co_org_id) if selected_co_org_id else None

    folder_tree, folder_breadcrumbs, folder_entries, files_at_path = build_file_library_explorer(
        tab=fl_tab_label,
        nested_mode=file_library_nested_mode,
        nested_groups=file_library_nested_groups,
        fl_path=fl_path,
        tree_mode=file_library_tree_mode,
        commission_org_nav=commission_org_nav,
    )
    file_library_use_explorer = True
    fl_base = reverse("file_library") + _file_library_query_string(
        tab,
        date_from,
        date_to,
        uploader_selected,
        project_selected,
        commission_org_raw,
        co_path=co_path,
    )
    co_explorer_base = reverse("file_library") + _file_library_query_string(
        tab,
        date_from,
        date_to,
        uploader_selected,
        project_selected,
        commission_org_raw,
        fl_path=fl_path,
    )

    can_batch_delete = role_has(request.user, "perm_file_delete")
    scope_export_ok = library_export_merge_allowed_under_own_files_scope(request.user, project_selected_id)
    fill_export_ok = library_user_may_filled_pdf_toolchain(request.user) and scope_export_ok
    file_library_row_selection = can_batch_delete or (
        fill_export_ok and tab in ("inspection_submit", "site_record", "report")
    )
    file_library_table_colspan = 4 + (1 if file_library_row_selection else 0)

    usage_b = library_user_file_library_usage_bytes(request.user)
    quota_b = library_user_file_library_quota_bytes(request.user)

    return render(
        request,
        "core/file_library.html",
        {
            "projects": projects_qs,
            "project_selected": project_selected,
            "selected_commission_org": selected_commission_org,
            "commission_org_raw": commission_org_raw,
            "commission_org_groups": commission_org_groups,
            "commission_org_nav": commission_org_nav,
            "commission_org_unset_label": _COMMISSION_ORG_UNSET_LABEL,
            "co_path": co_path,
            "commission_org_tree_nodes": commission_org_tree_nodes,
            "co_explorer_base": co_explorer_base,
            "explorer_co_org": explorer_co_org,
            "tab": tab,
            "file_library_tabs": list(_FILE_LIBRARY_TAB_DEFS),
            "files": files,
            "file_library_nested_groups": file_library_nested_groups,
            "file_library_nested_mode": file_library_nested_mode,
            "file_library_template_shared_browse": (
                cat == LibraryFile.CATEGORY_TEMPLATE
                and library_user_may_browse_shared_library_templates(request.user)
            ),
            "file_library_table_colspan": file_library_table_colspan,
            "date_from": date_from,
            "date_to": date_to,
            "date_filter_active": bool(d0 and d1),
            "uploader_selected": uploader_selected,
            "uploader_choices": uploader_choices,
            "file_scope_own_only": restricted,
            "can_batch_delete": can_batch_delete,
            "file_library_row_selection": file_library_row_selection,
            "can_manual_export_submit_pdf": (
                tab == "inspection_submit"
                and fill_export_ok
            ),
            "can_manual_export_report_from_site_record": (
                tab in ("inspection_submit", "site_record")
                and fill_export_ok
            ),
            "can_merge_reports": (
                tab == "report"
                and fill_export_ok
            ),
            "trash_days_notice": 30,
            "file_library_usage_bytes": usage_b,
            "file_library_quota_bytes": quota_b,
            "file_library_quota_limited": quota_b is not None,
            "file_library_use_explorer": file_library_use_explorer,
            "fl_path": fl_path,
            "folder_tree_nodes": folder_tree,
            "folder_breadcrumbs": folder_breadcrumbs,
            "folder_entries": folder_entries,
            "files_at_path": files_at_path,
            "explorer_base_url": fl_base,
            "tree_root_label": fl_tab_label,
            "explorer_dnd_enabled": file_library_nested_mode
            in ("project_report", "project_report_site"),
            "explorer_dnd_post_url": reverse("file_library"),
            "explorer_dnd_mode": "file_library",
        },
    )


@login_required
@require_POST
def file_library_delete(request, pk):
    lf = get_object_or_404(LibraryFile, pk=pk)
    tab_guess = _library_tab_for_category(lf.category)
    if not role_has(request.user, "perm_file_library"):
        messages.error(request, "无权访问文件库")
        tab = tab_guess
        df = request.POST.get("date_from", "").strip()
        dt = request.POST.get("date_to", "").strip()
        up = request.POST.get("uploader", "").strip()
        return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up))
    if not role_has(request.user, "perm_file_delete"):
        messages.error(request, "当前角色无权删除文件库文件")
        tab = tab_guess
        df = request.POST.get("date_from", "").strip()
        dt = request.POST.get("date_to", "").strip()
        up = request.POST.get("uploader", "").strip()
        return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up))
    if not library_file_access_allowed(request.user, lf):
        messages.error(request, "无权删除该文件")
        tab = tab_guess
        df = request.POST.get("date_from", "").strip()
        dt = request.POST.get("date_to", "").strip()
        up = request.POST.get("uploader", "").strip()
        return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up))
    if not library_user_may_delete_library_file(request.user, lf):
        messages.error(request, "只能删除本人上传的文件；删除他人文件需系统管理员权限。")
        tab = tab_guess
        df = request.POST.get("date_from", "").strip()
        dt = request.POST.get("date_to", "").strip()
        up = request.POST.get("uploader", "").strip()
        return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up))
    if library_file_write_blocked_by_project_revocation(request.user, lf):
        messages.error(request, "您已被取消在该项目上的编辑权限，仅可下载与预览，不能删除文件。")
        tab = tab_guess
        df = request.POST.get("date_from", "").strip()
        dt = request.POST.get("date_to", "").strip()
        up = request.POST.get("uploader", "").strip()
        return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up))
    tab = tab_guess
    soft_delete_library_file(lf)
    messages.success(request, "已移入回收站（可在「回收站」中恢复或彻底删除）")
    df = request.POST.get("date_from", "").strip()
    dt = request.POST.get("date_to", "").strip()
    up = request.POST.get("uploader", "").strip()
    return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up))


@login_required
@require_POST
def file_library_batch_delete(request):
    tab = _normalize_library_tab(request.POST.get("tab", "ocr"))
    df = request.POST.get("date_from", "").strip()
    dt = request.POST.get("date_to", "").strip()
    up = request.POST.get("uploader", "").strip()
    if not role_has(request.user, "perm_file_library"):
        messages.error(request, "无权访问文件库")
        return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up))
    if not role_has(request.user, "perm_file_delete"):
        messages.error(request, "当前角色无权批量删除文件")
        return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up))
    ids = request.POST.getlist("ids")
    if not ids:
        messages.error(request, "未选择文件")
        return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up))

    cat = _library_category_for_tab(tab)
    id_ints: list[int] = []
    for x in ids:
        try:
            id_ints.append(int(x))
        except (TypeError, ValueError):
            continue
    id_ints = sorted(set(id_ints))
    if not id_ints:
        messages.error(request, "所选记录无效")
        return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up))

    qs = list(LibraryFile.objects.filter(pk__in=id_ints, category=cat))
    found_pks = {lf.pk for lf in qs}
    missing = len(id_ints) - len(found_pks)

    deletable = [
        lf
        for lf in qs
        if library_user_may_delete_library_file(request.user, lf)
        and not library_file_write_blocked_by_project_revocation(request.user, lf)
    ]
    if not deletable:
        messages.error(
            request,
            "所选文件均不可删除（默认仅本人上传；删除他人文件需系统管理员权限；无权限的项目上文件亦不可删）。",
        )
        return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up))

    n = 0
    for lf in deletable:
        soft_delete_library_file(lf)
        n += 1

    skipped = len(qs) - n + missing
    if skipped:
        messages.warning(
            request,
            f"已将 {n} 个文件移入回收站；另有 {skipped} 条不存在、无删除权限或项目已收回编辑权，已跳过。",
        )
    else:
        messages.success(request, f"已将 {n} 个文件移入回收站")
    return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up))


@login_required
@xframe_options_sameorigin
def file_library_raw(request, pk):
    """允许同源预览页通过 iframe/embed 嵌入 PDF（默认中间件会为 DENY，导致内嵌失败）。"""
    lf = get_object_or_404(LibraryFile, pk=pk)
    if not role_has(request.user, "perm_file_preview"):
        raise PermissionDenied("无权预览该文件")
    if not library_file_access_allowed(request.user, lf):
        raise PermissionDenied("无权访问该文件")
    try:
        path = pipeline_service.library_absolute_path(lf.relative_path)
    except ValueError as e:
        raise Http404(str(e)) from e
    if not path.is_file():
        raise Http404("文件不存在")
    ext = path.suffix.lower()
    ctype = "application/octet-stream"
    if ext in (".png",):
        ctype = "image/png"
    elif ext in (".jpg", ".jpeg"):
        ctype = "image/jpeg"
    elif ext in (".gif",):
        ctype = "image/gif"
    elif ext in (".webp",):
        ctype = "image/webp"
    elif ext in (".pdf",):
        ctype = "application/pdf"
    elif ext in (".json",):
        ctype = "application/json"
    elif ext in (".md", ".markdown"):
        ctype = "text/markdown; charset=utf-8"
    resp = FileResponse(path.open("rb"), content_type=ctype)
    resp["Content-Disposition"] = f'inline; filename="{_safe_filename(lf.original_name)}"'
    return resp


@login_required
def file_library_download(request, pk):
    lf = get_object_or_404(LibraryFile, pk=pk)
    if not role_has(request.user, "perm_file_download"):
        raise PermissionDenied("无权下载该文件")
    if not library_file_access_allowed(request.user, lf):
        raise PermissionDenied("无权下载该文件")
    return library_file_download_response(lf)


@login_required
def file_preview(request, pk):
    lf = get_object_or_404(LibraryFile, pk=pk)
    if not role_has(request.user, "perm_file_preview"):
        raise PermissionDenied("无权预览该文件")
    if not library_file_access_allowed(request.user, lf):
        raise PermissionDenied("无权预览该文件")
    kind = lf.preview_kind
    if kind == "unsupported":
        return render(
            request,
            "core/file_preview.html",
            {"lf": lf, "kind": "unsupported", "json_text": None, "md_payload": None},
        )
    json_text = None
    md_payload = None
    if kind == "json":
        try:
            p = pipeline_service.library_absolute_path(lf.relative_path)
            raw = p.read_text(encoding="utf-8", errors="replace")
            json_text = json_std.dumps(json_std.loads(raw), ensure_ascii=False, indent=2)
        except (ValueError, OSError, json_std.JSONDecodeError):
            try:
                p = pipeline_service.library_absolute_path(lf.relative_path)
                json_text = p.read_text(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                json_text = ""
    elif kind == "markdown":
        try:
            p = pipeline_service.library_absolute_path(lf.relative_path)
            md_raw = p.read_text(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            md_raw = ""
        md_payload = {"text": md_raw}
    return render(
        request,
        "core/file_preview.html",
        {"lf": lf, "kind": kind, "json_text": json_text, "md_payload": md_payload},
    )


@login_required
def process_pipeline(request):
    if library_user_has_party_a_demo_restrictions(request.user) and getattr(
        settings, "PARTY_A_DEMO_DISABLE_PIPELINE", False
    ):
        messages.error(request, "演示账号未开放文档识别处理页")
        return redirect(reverse("file_library") + "?tab=ocr")
    if not role_has(request.user, "perm_process_pipeline"):
        messages.error(request, "当前角色无权使用文档识别")
        return redirect(reverse("file_library") + "?tab=ocr")
    project_raw = request.GET.get("project", "").strip()
    try:
        project_id = int(project_raw) if project_raw else None
    except ValueError:
        project_id = None
    uploads = LibraryFile.objects.filter(category=LibraryFile.CATEGORY_UPLOAD).order_by("-created_at")
    if project_id:
        uploads = uploads.filter(projects__id=project_id).distinct()
    if library_scope_own_files_only(request.user):
        if library_signatory_assigned_project_selected(request.user, project_id):
            pass
        else:
            uploads = uploads.filter(created_by=request.user)
    if request.method == "POST":
        post_project_ids = parse_project_ids(request.POST.getlist("project_ids"))
        if not post_project_ids and request.POST.get("project"):
            post_project_ids = parse_project_ids([request.POST.get("project", "")])
        ids = request.POST.getlist("file_ids")
        if not ids:
            messages.error(request, "请至少选择一个上传文件")
            return redirect(reverse("process_pipeline"))
        selected = list(
            LibraryFile.objects.filter(
                pk__in=ids,
                category=LibraryFile.CATEGORY_UPLOAD,
            )
        )
        if len(selected) != len(ids):
            messages.error(request, "选择无效")
            return redirect(reverse("process_pipeline"))
        if not all(library_file_access_allowed(request.user, lf) for lf in selected):
            messages.error(request, "包含无权处理的文件")
            return redirect(reverse("process_pipeline"))
        upx = library_upload_blocked_revoked_projects(request.user, post_project_ids)
        if upx:
            messages.error(request, upx)
            return redirect(reverse("process_pipeline"))

        payloads = []
        for lf in selected:
            try:
                p = pipeline_service.library_absolute_path(lf.relative_path)
            except ValueError:
                messages.error(request, f"路径无效: {lf.original_name}")
                return redirect(reverse("process_pipeline"))
            if not p.is_file():
                messages.error(request, f"文件缺失: {lf.original_name}")
                return redirect(reverse("process_pipeline"))
            ext = p.suffix.lower().lstrip(".")
            if ext not in ("pdf", "jpg", "jpeg", "png"):
                messages.error(request, f"不支持的格式（仅 PDF/JPG/PNG）: {lf.original_name}")
                return redirect(reverse("process_pipeline"))
            payloads.append((lf.original_name, p.read_bytes()))

        batch_id = str(uuid.uuid4())
        try:
            devices, pipeline, image_data = pipeline_service.run_process_all_files(payloads, batch_id)
        except Exception as exc:
            messages.error(request, f"处理失败: {exc}")
            return redirect(reverse("process_pipeline"))

        n_json = pipeline_service.write_split_device_json_files(
            batch_id, devices, request.user
        )
        if post_project_ids:
            json_ids = list(
                LibraryFile.objects.filter(category=LibraryFile.CATEGORY_JSON, batch_id=batch_id)
                .values_list("id", flat=True)
            )
            attach_files_to_projects(json_ids, post_project_ids, request.user)
        pipeline_service.sync_temp_and_json_records(batch_id, request.user)
        if n_json:
            messages.success(
                request,
                f"已按仪器拆分并保存 {n_json} 个数据文件到文件库「数据文件」分类。",
            )
        context = {
            "uploads": uploads,
            "projects": LibraryProject.objects.filter(is_active=True).order_by("code"),
            "project_selected": str(project_id or ""),
            "result": {
                "batch_id": batch_id,
                "devices_json": json_std.dumps(devices, ensure_ascii=False, indent=2),
                "pipeline_json": json_std.dumps(pipeline, ensure_ascii=False, indent=2),
                "images_json": json_std.dumps(image_data, ensure_ascii=False, indent=2),
            },
        }
        return render(request, "core/process_pipeline.html", context)

    return render(
        request,
        "core/process_pipeline.html",
        {
            "uploads": uploads,
            "result": None,
            "projects": LibraryProject.objects.filter(is_active=True).order_by("code"),
            "project_selected": str(project_id or ""),
        },
    )


@login_required
def pipeline_preview_static(request, name):
    """Serve MinerU / PDF preview PNGs from pipeline PREVIEW_IMG (URLs like /static/preview/...)."""
    base = Path(settings.PIPELINE_PREVIEW_IMG).resolve()
    target = (base / Path(name).name).resolve()
    target.relative_to(base)
    if not target.is_file():
        raise Http404("预览图不存在")
    return FileResponse(target.open("rb"), content_type="image/png")


@login_required
def htmlpdf_editor(request):
    gx = _require_htmlpdf(request)
    if gx:
        return gx
    has_report_source_relation = _librarytask_has_report_source_relation()
    tasks = list(
        LibraryTask.objects.filter(
            output_target__in={
                LibraryTask.OUTPUT_REPORT,
                LibraryTask.OUTPUT_SITE_RECORD,
            }
        ).order_by("code", "id")
    )
    manage_task_id: int | None = None
    mt_raw = (request.GET.get("manage_task") or "").strip()
    if mt_raw:
        try:
            manage_task_id = int(mt_raw)
        except ValueError:
            manage_task_id = None
    fl_path = (request.GET.get("fl_path") or "").strip()
    file_allowed = lambda lf: library_file_access_allowed(request.user, lf)
    explorer = build_htmlpdf_explorer_context(
        tasks,
        fl_path=fl_path,
        manage_task_id=manage_task_id,
        has_report_source_relation=has_report_source_relation,
        file_allowed=file_allowed,
    )
    fl_path = explorer["fl_path"]
    active_task_id = explorer.get("active_task_id")
    return render(
        request,
        "core/htmlpdf_files.html",
        {
            "htmlpdf_explorer_base": reverse("htmlpdf_editor"),
            "folder_tree_nodes": explorer["folder_tree_nodes"],
            "folder_breadcrumbs": explorer["folder_breadcrumbs"],
            "folder_entries": explorer["folder_entries"],
            "fl_path": fl_path,
            "selected_task": explorer["selected_task"],
            "selected_report": explorer["selected_report"],
            "report_site_records": explorer["report_site_records"],
            "active_task_id": active_task_id,
            "task_library_url": task_library_page_url(
                fl_path,
                manage_task_id=active_task_id,
            ),
            "show_library_task_nav": library_user_may_access_task_template_library_nav(
                request.user
            ),
        },
    )


@login_required
def htmlpdf_editor_open(request, pk: int):
    gx = _require_htmlpdf(request)
    if gx:
        return gx
    lf = get_object_or_404(LibraryFile, pk=pk, category=LibraryFile.CATEGORY_TEMPLATE)
    if not library_file_access_allowed(request.user, lf):
        raise PermissionDenied("无权访问该模板文件")
    if not lf.original_name.lower().endswith(".pdf"):
        messages.error(request, "仅支持从模板分类中的 PDF 进入编辑器")
        return redirect(reverse("htmlpdf_editor"))
    return_manage_raw = (request.GET.get("return_manage_task") or "").strip()
    return_fl_path = (request.GET.get("return_fl_path") or "").strip()
    return_task_url = ""
    if return_manage_raw:
        try:
            mid = int(return_manage_raw)
            return_task_url = task_library_page_url(
                return_fl_path, manage_task_id=mid
            )
        except ValueError:
            return_task_url = task_library_page_url(return_fl_path)
    index_path = Path(settings.BASE_DIR) / "htmlpdf" / "templates" / "index.html"
    if not index_path.is_file():
        return HttpResponse("htmlpdf 模板页面不存在", status=404)
    html = index_path.read_text(encoding="utf-8")
    party_demo = library_user_has_party_a_demo_restrictions(request.user)
    matrix_beta = library_user_may_use_htmlpdf_matrix_beta_controls(request.user)
    inject = (
        "<script>"
        f"window.HTMLPDF_INITIAL_TEMPLATE_PDF_ID={lf.pk};"
        f"window.HTMLPDF_PARTY_A_DEMO_UI={'true' if party_demo else 'false'};"
        f"window.HTMLPDF_MATRIX_BETA_UI={'true' if matrix_beta else 'false'};"
    )
    if return_manage_raw:
        try:
            inject += f"window.HTMLPDF_INITIAL_LIBRARY_TASK_ID={json_std.dumps(str(int(return_manage_raw)))};"
        except ValueError:
            pass
    inject += "</script>"
    # 必须出现在主内联脚本之前，否则读取 MATRIX_BETA / PARTY_A_DEMO 时 window 尚未赋值，strip 逻辑与权限不一致。
    if return_task_url:
        bar = (
            '<div id="htmlpdf-return-bar" style="position:fixed;top:0;left:0;right:0;z-index:99999;'
            "background:#1e293b;color:#f8fafc;padding:8px 16px;font:14px/1.4 system-ui,sans-serif;"
            'display:flex;align-items:center;gap:12px;box-shadow:0 2px 8px rgba(0,0,0,.15);">'
            f'<a href="{return_task_url}" style="color:#a5b4fc;text-decoration:none;font-weight:600;">'
            "← 返回任务模板库</a>"
            f'<span style="opacity:.7;font-size:12px;margin-left:auto;">{lf.original_name}</span></div>'
            "<style>body{padding-top:44px!important;}</style>"
        )
        inject = bar + inject
    html = html.replace("<body>", "<body>\n" + inject + "\n", 1)
    return HttpResponse(html, content_type="text/html; charset=utf-8")


@login_required
def htmlpdf_template_file(request, pk: int):
    gx = _require_htmlpdf(request)
    if gx:
        return gx
    lf = get_object_or_404(LibraryFile, pk=pk, category=LibraryFile.CATEGORY_TEMPLATE)
    if not library_file_access_allowed(request.user, lf):
        raise PermissionDenied("无权访问模板文件")
    path = pipeline_service.library_absolute_path(lf.relative_path)
    if not path.is_file():
        raise Http404("模板文件不存在")
    ctype = guess_type(lf.original_name)[0] or "application/octet-stream"
    return FileResponse(path.open("rb"), content_type=ctype)


@login_required
def htmlpdf_api_template_pdfs(request):
    gx = _require_htmlpdf(request)
    if gx:
        return JsonResponse({"error": "forbidden"}, status=403)
    rows = []
    qs = (
        LibraryFile.objects.filter(category=LibraryFile.CATEGORY_TEMPLATE)
        .order_by("-created_at")
        .only("id", "original_name", "created_at")
    )
    for lf in qs[:200]:
        if not library_file_access_allowed(request.user, lf):
            continue
        if not lf.original_name.lower().endswith(".pdf"):
            continue
        rows.append(
            {
                "id": lf.pk,
                "name": lf.original_name,
                "url": reverse("htmlpdf_template_file", kwargs={"pk": lf.pk}),
            }
        )
    return JsonResponse({"templates": rows})


@login_required
def htmlpdf_api_template_jsons(request):
    gx = _require_htmlpdf(request)
    if gx:
        return JsonResponse({"error": "forbidden"}, status=403)
    rows = []
    qs = (
        LibraryFile.objects.filter(category=LibraryFile.CATEGORY_TEMPLATE)
        .order_by("-created_at")
        .only("id", "original_name")
    )
    from apps.core.library_task_template_binding_service import (
        is_auxiliary_template_json_filename,
    )

    for lf in qs[:500]:
        if not library_file_access_allowed(request.user, lf):
            continue
        if not lf.original_name.lower().endswith(".json"):
            continue
        if is_auxiliary_template_json_filename(lf.original_name or ""):
            continue
        rows.append({"id": lf.pk, "name": lf.original_name})
    return JsonResponse({"templates": rows})


@csrf_exempt
@login_required
@require_POST
def htmlpdf_api_import_json_from_library(request):
    gx = _require_htmlpdf(request)
    if gx:
        return JsonResponse({"error": "forbidden"}, status=403)
    try:
        data = json_std.loads(request.body.decode("utf-8"))
        template_id = int(data.get("template_id"))
    except Exception:
        return JsonResponse({"error": "template_id 无效"}, status=400)
    lf = get_object_or_404(LibraryFile, pk=template_id, category=LibraryFile.CATEGORY_TEMPLATE)
    if not library_file_access_allowed(request.user, lf):
        return JsonResponse({"error": "无权访问该模板"}, status=403)
    path = pipeline_service.library_absolute_path(lf.relative_path)
    if not path.is_file():
        return JsonResponse({"error": "模板文件不存在"}, status=404)
    if path.suffix.lower() != ".json":
        return JsonResponse({"error": "仅支持 JSON 模板"}, status=400)
    try:
        parsed = htmlpdf_service.parse_template_json(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return JsonResponse({"error": "JSON文件格式无效"}, status=400)
    fields = parsed.get("fields") if isinstance(parsed.get("fields"), list) else []
    skip_sections = htmlpdf_service.library_template_file_linked_to_report_task(lf)
    lt_raw = data.get("library_task_id") or data.get("libraryTaskId")
    if lt_raw is not None and str(lt_raw).strip() != "":
        try:
            t = LibraryTask.objects.filter(pk=int(lt_raw)).first()
            if t is not None and t.output_target == LibraryTask.OUTPUT_REPORT:
                skip_sections = True
        except (TypeError, ValueError):
            pass
    if fields:
        parsed["fields"] = htmlpdf_service.assign_template_sections_for_editor(
            request.user.id,
            fields,
            skip_for_report=skip_sections,
        )
    return JsonResponse(parsed)


@csrf_exempt
@login_required
@require_POST
def htmlpdf_api_use_template_pdf(request):
    gx = _require_htmlpdf(request)
    if gx:
        return JsonResponse({"error": "forbidden"}, status=403)
    try:
        data = json_std.loads(request.body.decode("utf-8"))
        template_id = int(data.get("template_id"))
    except Exception:
        return JsonResponse({"error": "template_id 无效"}, status=400)
    lf = get_object_or_404(LibraryFile, pk=template_id, category=LibraryFile.CATEGORY_TEMPLATE)
    if not library_file_access_allowed(request.user, lf):
        return JsonResponse({"error": "无权访问该模板"}, status=403)
    path = pipeline_service.library_absolute_path(lf.relative_path)
    if not path.is_file():
        return JsonResponse({"error": "模板文件不存在"}, status=404)
    if path.suffix.lower() != ".pdf":
        return JsonResponse({"error": "仅支持 PDF 模板"}, status=400)
    dst = htmlpdf_service.htmlpdf_source_pdf_path(request.user.id)
    dst.write_bytes(path.read_bytes())
    meta = {
        "source_type": "template",
        "template_file_id": lf.pk,
        "template_file_name": lf.original_name,
    }
    htmlpdf_service.htmlpdf_source_meta_path(request.user.id).write_text(
        json_std.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    linked_ids = list(
        LibraryTask.objects.filter(library_files=lf).order_by("code").values_list("pk", flat=True)[:80]
    )
    from apps.core.htmlpdf_report_mapping_service import (
        resolve_library_task_for_template_pdf,
    )

    preferred_task = None
    lt_raw = data.get("library_task_id") or data.get("libraryTaskId")
    if lt_raw is not None and str(lt_raw).strip() != "":
        try:
            preferred_task = LibraryTask.objects.filter(pk=int(lt_raw)).first()
        except (TypeError, ValueError):
            preferred_task = None
    task_for_defaults = resolve_library_task_for_template_pdf(
        lf.pk, preferred_task=preferred_task
    )

    default_json_template_id = None
    auto_import_json = False
    if task_for_defaults is not None:
        from apps.core.library_task_template_binding_service import (
            resolve_default_json_template_id,
            task_has_pdf_and_json_bound,
        )

        if task_has_pdf_and_json_bound(task_for_defaults):
            jid = resolve_default_json_template_id(
                task_for_defaults, pdf_template_id=lf.pk
            )
            if jid:
                default_json_template_id = int(jid)
                auto_import_json = True

    return JsonResponse(
        {
            "ok": True,
            "pdf_url": reverse("htmlpdf_template_file", kwargs={"pk": lf.pk}),
            "linked_library_task_ids": [int(x) for x in linked_ids],
            "library_task_id": task_for_defaults.pk if task_for_defaults else None,
            "library_task_output_target": (
                task_for_defaults.output_target if task_for_defaults else None
            ),
            "default_json_template_id": default_json_template_id,
            "auto_import_json": auto_import_json,
            "suggested_json_save_name": suggested_template_json_save_name(
                lf.original_name or ""
            ),
            "template_pdf_stem": Path(lf.original_name or "").stem,
        }
    )


@csrf_exempt
@login_required
@require_POST
def htmlpdf_api_upload_pdf(request):
    gx = _require_htmlpdf(request)
    if gx:
        return JsonResponse({"error": "forbidden"}, status=403)
    up = request.FILES.get("pdf")
    if not up:
        return JsonResponse({"error": "请选择PDF"}, status=400)
    if not (up.name or "").lower().endswith(".pdf"):
        return JsonResponse({"error": "仅支持PDF"}, status=400)
    dst = htmlpdf_service.htmlpdf_source_pdf_path(request.user.id)
    dst.write_bytes(up.read())
    meta = {
        "source_type": "upload",
        "filename": safe_library_basename(up.name or "uploaded.pdf"),
    }
    htmlpdf_service.htmlpdf_source_meta_path(request.user.id).write_text(
        json_std.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return JsonResponse({"ok": True})


@csrf_exempt
@login_required
@require_POST
def htmlpdf_api_import_json(request):
    gx = _require_htmlpdf(request)
    if gx:
        return JsonResponse({"error": "forbidden"}, status=403)
    file = request.FILES.get("jsonFile")
    if not file:
        return JsonResponse({"error": "请选择JSON文件"}, status=400)
    try:
        parsed = htmlpdf_service.parse_template_json(file.read().decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "JSON文件格式无效"}, status=400)
    fields = parsed.get("fields") if isinstance(parsed.get("fields"), list) else []
    if fields:
        parsed["fields"] = htmlpdf_service.assign_template_sections_for_editor(
            request.user.id,
            fields,
            skip_for_report=_htmlpdf_is_report_template_context(request, {}),
        )
    return JsonResponse(parsed)


@csrf_exempt
@require_POST
def htmlpdf_api_export_json(request):
    auth_resp = _require_api_login(request)
    if auth_resp:
        return auth_resp
    gx = _require_htmlpdf(request)
    if gx:
        return JsonResponse({"error": "forbidden"}, status=403)
    try:
        data = json_std.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "请求体不是合法JSON"}, status=400)
    fields = _sanitize_pdf_field_texts(data.get("fields", []))
    form_schema = data.get("form_schema") if isinstance(data.get("form_schema"), dict) else {}
    bindings = data.get("bindings") if isinstance(data.get("bindings"), dict) else {}
    raw_map = data.get("report_site_field_map") or data.get("reportSiteFieldMap")
    if isinstance(raw_map, list):
        from apps.core.htmlpdf_report_mapping_service import merge_report_site_map_into_bindings

        bindings = merge_report_site_map_into_bindings(bindings, raw_map)
    template_meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    report_type = str(data.get("report_type") or "").strip()
    standard = str(data.get("standard") or "").strip()
    name = safe_library_basename((data.get("name") or "template").strip() or "template")
    if not name.lower().endswith(".json"):
        name = f"{name}.json"
    source_meta = {}
    meta_path = htmlpdf_service.htmlpdf_source_meta_path(request.user.id)
    if meta_path.is_file():
        try:
            source_meta = json_std.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            source_meta = {}
    binding_meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    is_report_tpl = _htmlpdf_is_report_template_context(request, data, binding_meta)
    normalized_fields = _normalize_pdf_fields_for_unified_template(fields, form_schema, bindings)
    _assign_htmlpdf_template_sections_to_fields(
        request.user.id, normalized_fields, skip_for_report=is_report_tpl
    )
    constants = form_schema.get("constants") if isinstance(form_schema.get("constants"), dict) else {}
    enums = form_schema.get("enums") if isinstance(form_schema.get("enums"), dict) else {}
    steps = form_schema.get("steps") if isinstance(form_schema.get("steps"), list) else []
    _ff_raw_export = _export_request_field_formulas_raw(data, form_schema)
    # 模板保存统一走规则引擎，保证分桶与排序稳定：
    # reportInfo/hospitalInfo/equipmentInfo/instruments/testResult/signatures
    rule_template_obj = {
        "templateId": template_meta.get("templateId") or name.rsplit(".", 1)[0],
        "templateName": template_meta.get("templateName") or name,
        "version": template_meta.get("version") or "1.0.0",
        "reportType": report_type,
        "standard": standard,
        "pdfUrl": str(template_meta.get("pdfUrl") or ""),
        "locale": str(template_meta.get("locale") or "zh-CN"),
        "constants": constants,
        "enums": enums,
        "steps": steps,
        "pdf": {"fields": normalized_fields},
        "meta": template_meta,
    }
    if _ff_raw_export is not None:
        rule_template_obj["fieldFormulas"] = _ff_raw_export
    try:
        rule_payload = build_frontend_schema_by_rules(rule_template_obj, merge_split_dates=False)
    except Exception:
        rule_payload = {}
    if isinstance(rule_payload, dict) and isinstance(rule_payload.get("steps"), list) and rule_payload.get("steps"):
        constants = rule_payload.get("constants") if isinstance(rule_payload.get("constants"), dict) else constants
        enums = rule_payload.get("enums") if isinstance(rule_payload.get("enums"), dict) else enums
        steps = rule_payload.get("steps") or steps
    tid = template_meta.get("templateId") or name.rsplit(".", 1)[0]
    tname = template_meta.get("templateName") or name
    tver = template_meta.get("version") or "1.0.0"
    payload = _slim_unified_v2_template_library_payload(
        template_id=tid,
        template_name=tname,
        version=tver,
        report_type=report_type,
        standard=standard,
        pdf_url=str(template_meta.get("pdfUrl") or ""),
        locale=str(template_meta.get("locale") or "zh-CN"),
        source_pdf=source_meta,
        normalized_pdf_fields=normalized_fields,
        constants=constants,
        enums=enums,
        steps=steps,
        bindings=bindings if isinstance(bindings, dict) else None,
        field_formulas=_ff_raw_export,
    )
    payload = _sanitize_json_payload_text(payload)
    try:
        raw = json_std.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    except UnicodeEncodeError:
        payload = _sanitize_json_payload_text(payload)
        raw = json_std.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    wrapped = type("UploadLike", (), {"read": lambda self: raw, "name": name})()
    created, skipped = save_library_binary_uploads(
        request.user,
        [wrapped],
        LibraryFile.CATEGORY_TEMPLATE,
    )
    if skipped and not created:
        return JsonResponse({"error": "模板保存失败"}, status=500)
    row = created[0]
    register_tour_library_file(request, int(row["id"]))
    binding_meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    binding_rotation = _maybe_rotate_task_json_binding_after_export(
        request, data, binding_meta, row, source="editor_export_json"
    )
    resp = {
        "ok": True,
        "saved_to": "template",
        "file": {
            "id": row["id"],
            "name": row["original_name"],
            "category": row["category"],
            "library_url": reverse("file_library") + "?tab=template",
            "download_url": reverse("file_library_download", kwargs={"pk": row["id"]}),
        },
    }
    if binding_rotation:
        resp["binding_rotation"] = binding_rotation
    return JsonResponse(resp)


def _frontend_type_from_pdf_field_type(field_type: str) -> str:
    ft = (field_type or "").strip().lower()
    if ft == "check":
        return "boolean"
    if ft == "image":
        return "signature"
    return "text"


def _iter_form_fields(form_schema: dict):
    steps = form_schema.get("steps") if isinstance(form_schema.get("steps"), list) else []
    for step in steps:
        if not isinstance(step, dict):
            continue
        sections = step.get("sections") if isinstance(step.get("sections"), list) else []
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            fields = sec.get("fields") if isinstance(sec.get("fields"), list) else []
            for fld in fields:
                if isinstance(fld, dict):
                    yield fld


def _build_form_field_meta(form_schema: dict) -> dict:
    out = {}
    for fld in _iter_form_fields(form_schema):
        fid = str(fld.get("id") or "").strip()
        if not fid:
            continue
        out[fid] = {
            "label": str(fld.get("label") or fid).strip(),
            "type": str(fld.get("type") or "text").strip().lower(),
        }
    return out


def _normalize_pdf_fields_for_unified_template(fields, form_schema: dict, bindings: dict):
    form_meta = _build_form_field_meta(form_schema)
    rows = bindings.get("field_to_pdf") if isinstance(bindings.get("field_to_pdf"), list) else []
    by_pdf_id = {}
    by_placeholder = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        pdf_id = str(row.get("pdfFieldId") or "").strip()
        ph = str(row.get("placeholder") or "").strip()
        if pdf_id:
            by_pdf_id[pdf_id] = row
        if ph:
            by_placeholder[ph] = row
    out = []
    for idx, item in enumerate(fields or []):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        # Preserve physical placeholder id from PDF (e.g. f78) as top priority.
        # 优先保证 pdfFieldId 仍是 f 序号，避免被语义 id（如 委托单位_委托单位）覆盖。
        raw_pdf_id = str(row.get("pdfFieldId") or "").strip()
        old_placeholder = str(row.get("placeholder") or "").strip()
        link = by_pdf_id.get(raw_pdf_id) or by_placeholder.get(old_placeholder) or {}
        # 物理 pdfFieldId 不与 bindings 里的 pdfFieldId 混用：否则多个占位/同占位会挤到同一 f 号，
        # 与 HTMLPDF 左侧栏「第 N 栏」顺序不一致；bindings 仅用于语义 fieldId / title。
        id_fallback = str(row.get("id") or "").strip()
        candidates = [raw_pdf_id, id_fallback]
        old_pdf_id = next((c for c in candidates if re.match(r"^f\d+$", c)), f"f{idx + 1}")
        field_id = str(link.get("fieldId") or old_placeholder or old_pdf_id).strip()
        title = normalize_field_text_by_underscore_rules(
            str(
                link.get("title")
                or form_meta.get(field_id, {}).get("label")
                or row.get("title")
                or old_placeholder
                or field_id
            ).strip()
        )
        row["pdfFieldId"] = old_pdf_id
        row["id"] = normalize_field_text_by_underscore_rules(field_id) or field_id
        row["title"] = title or field_id
        # Keep legacy key for compatibility with old pipeline readers.
        row["placeholder"] = normalize_field_text_by_underscore_rules(field_id) or field_id
        out.append(row)
    # 保证 f 号唯一且与导出时 fields 数组顺序（即侧栏序号）一致：重复时按下标收编为 f{idx+1} 并顺延。
    used_pdf = set()

    def _next_free_f_num(start: int) -> str:
        n = max(1, int(start))
        while f"f{n}" in used_pdf:
            n += 1
        return f"f{n}"

    for idx, row in enumerate(out):
        pid = str(row.get("pdfFieldId") or "").strip()
        if not re.match(r"^f\d+$", pid):
            pid = _next_free_f_num(idx + 1)
        if pid in used_pdf:
            pid = _next_free_f_num(idx + 1)
        used_pdf.add(pid)
        row["pdfFieldId"] = pid
    return out


def _htmlpdf_is_report_template_context(
    request, data: dict | None = None, meta: dict | None = None
) -> bool:
    """当前 HTMLPDF 编辑上下文是否为报告类任务模板（非现场记录）。"""
    task = _resolve_library_task_for_htmlpdf_frontend_export(
        request, data if isinstance(data, dict) else {}, meta
    )
    if task is None:
        return False
    return getattr(task, "output_target", None) == LibraryTask.OUTPUT_REPORT


def _assign_htmlpdf_template_sections_to_fields(
    user_id: int, fields: list, *, skip_for_report: bool = False
) -> None:
    """按 PDF 章节锚点写入 templateSectionKey（仅现场记录；报告模板不写入六大章节）。"""
    if not fields:
        return
    if skip_for_report:
        htmlpdf_service.strip_site_record_section_fields(
            fields, remove_all_section_keys=True
        )
        return
    try:
        pdf_path = htmlpdf_service.user_original_pdf_path(int(user_id))
    except Exception:
        return
    if not pdf_path.is_file():
        return
    try:
        from htmlpdf.full_text_coordinate_boxing import assign_template_sections_to_fields

        assign_template_sections_to_fields(str(pdf_path), fields)
    except Exception:
        pass


def _sanitize_pdf_field_texts(fields):
    out = []
    for item in fields or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        for key in ("id", "title", "placeholder"):
            if key in row:
                val = row.get(key)
                if isinstance(val, str):
                    row[key] = normalize_field_text_by_underscore_rules(val)
        out.append(row)
    return out


def _slim_unified_v2_template_library_payload(
    *,
    template_id: str,
    template_name: str,
    version: str,
    report_type: str,
    standard: str,
    pdf_url: str,
    locale: str,
    source_pdf: dict,
    normalized_pdf_fields: list,
    constants: dict,
    enums: dict,
    steps: list,
    bindings: dict | None = None,
    field_formulas: dict | list | None = None,
) -> dict:
    """On-disk unified template: single ``formSchema``, compact ``pdf.fields``, no duplicate root blocks."""
    orphans = {}
    if field_formulas is not None:
        orphans = attach_root_field_formulas_to_pdf_field_rows(normalized_pdf_fields, field_formulas)
    compact_fields = compact_unified_pdf_fields_for_storage(normalized_pdf_fields)
    out = {
        "schema": "unified_form_template/v2",
        "templateId": template_id,
        "templateName": template_name,
        "version": version,
        "reportType": report_type,
        "standard": standard,
        "pdfUrl": pdf_url,
        "locale": locale,
        "pdf": {"source_pdf": source_pdf if isinstance(source_pdf, dict) else {}, "fields": compact_fields},
    }
    if isinstance(orphans, dict) and orphans:
        out["fieldFormulas"] = orphans
    out["formSchema"] = {
        "constants": constants if isinstance(constants, dict) else {},
        "enums": enums if isinstance(enums, dict) else {},
        "steps": steps if isinstance(steps, list) else [],
    }
    if isinstance(bindings, dict) and bindings:
        out["bindings"] = bindings
    return out


def _build_frontend_field_items(fields):
    """Build one frontend field definition for every PDF field item."""
    out = []
    id_counter = {}
    for idx, item in enumerate(fields or []):
        if not isinstance(item, dict):
            continue
        field_id_raw = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        placeholder = str(item.get("placeholder") or "").strip()
        pdf_field_id = str(item.get("pdfFieldId") or item.get("id") or f"f{idx + 1}")
        base_id = field_id_raw or placeholder or pdf_field_id
        n = id_counter.get(base_id, 0) + 1
        id_counter[base_id] = n
        field_id = base_id if n == 1 else f"{base_id}_{n}"
        page = item.get("page") or 1
        try:
            page = int(page)
        except (TypeError, ValueError):
            page = 1
        anchor_type = str(item.get("fieldType") or "text").lower() or "text"
        out.append(
            {
                "id": field_id,
                "title": title or placeholder or field_id,
                "label": title or placeholder or field_id,
                "type": _frontend_type_from_pdf_field_type(anchor_type),
                "required": False,
                "defaultValue": None,
                "source": {
                    # 新版约定：前后端直连优先使用 pdfFieldId 作为绑定键。
                    "key": pdf_field_id,
                    "bindKey": pdf_field_id,
                    "pdfFieldId": pdf_field_id,
                    "page": page,
                    "anchorType": anchor_type,
                },
            }
        )
    return out


def _matrix_field_type_from_pdf(field_type: str) -> str:
    ft = str(field_type or "").strip().lower()
    if ft == "check":
        return "boolean"
    if ft == "image":
        return "signature"
    return "text"


def _prefer_pdf_field_id_token(item: dict, fallback: str) -> str:
    """
    严格优先使用 pdfFieldId（f+序号），禁止回退到语义名称污染绑定键。
    """
    cands = [
        str(item.get("pdfFieldId") or "").strip(),
        str(item.get("id") or "").strip(),
        str(item.get("placeholder") or "").strip(),
    ]
    for c in cands:
        if re.match(r"^f\d+$", c):
            return c
    return fallback


def _build_matrix_steps_from_auto_fields(fields, source_pdf_path, table_struct_by_page=None):
    """
    依据自动划框字段 + 表格单元格定位，生成 matrixTable steps。
    目标是给后端统一模板 JSON 提供可结构化的固定表格骨架。
    """
    try:
        hits = htmlpdf_service.detect_table_cells_for_field_boxes(source_pdf_path, fields)
    except Exception:
        hits = []
    hit_by_idx = {int(x.get("index")): x for x in hits if isinstance(x, dict)}

    mapped = []
    for idx, f in enumerate(fields or []):
        if not isinstance(f, dict):
            continue
        hit = hit_by_idx.get(idx) or {}
        cell = hit.get("cell") if isinstance(hit.get("cell"), dict) else None
        if not cell:
            continue
        page = int(hit.get("page") or f.get("page") or 1)
        row = dict(f)
        row["_page"] = page
        row["_cell"] = cell
        mapped.append(row)
    if not mapped:
        return []

    all_sections = []
    by_page = {}
    for item in mapped:
        by_page.setdefault(int(item.get("_page") or 1), []).append(item)

    def _norm(s: str) -> str:
        return normalize_field_text_by_underscore_rules(str(s or "")).lower()

    value_col_tokens = ("检测值", "报出值", "计算结果", "检测结果", "measured", "report")
    unit_candidates = ("μSv/h", "μGy/min", "μGy/s", "mGy/min")

    for page in sorted(by_page.keys()):
        items = by_page[page]
        page_tables = (table_struct_by_page or {}).get(page) if isinstance(table_struct_by_page, dict) else None
        if not isinstance(page_tables, list) or not page_tables:
            continue

        for tbl in page_tables:
            cells = tbl.get("cells") if isinstance(tbl, dict) else []
            if not cells:
                continue

            row_values = sorted(set(int(c.get("row") or 0) for c in cells))
            col_values = sorted(set(int(c.get("col") or 0) for c in cells))
            if not row_values or not col_values:
                continue

            text_grid = {}
            bbox_grid = {}
            for c in cells:
                r = int(c.get("row") or 0)
                k = int(c.get("col") or 0)
                txt = str(c.get("text") or "").strip()
                text_grid[(r, k)] = txt
                bbox_grid[(r, k)] = (
                    float(c.get("x") or 0.0),
                    float(c.get("y") or 0.0),
                    float(c.get("w") or 0.0),
                    float(c.get("h") or 0.0),
                )

            # 字段映射到此表格 cell
            field_grid = {}
            for it in items:
                cx = float((it.get("_cell") or {}).get("x") or 0.0) + float((it.get("_cell") or {}).get("w") or 0.0) / 2.0
                cy = float((it.get("_cell") or {}).get("y") or 0.0) + float((it.get("_cell") or {}).get("h") or 0.0) / 2.0
                for (r, k), bb in bbox_grid.items():
                    x0, y0, w, h = bb
                    if x0 <= cx <= x0 + w and y0 <= cy <= y0 + h:
                        field_grid.setdefault((r, k), []).append(it)
                        break
            if not field_grid:
                continue

            data_rows = sorted(set(r for (r, _k) in field_grid.keys()))
            if not data_rows:
                continue
            min_data_row = min(data_rows)

            header_row = None
            for r in range(min_data_row - 1, -1, -1):
                row_txt = "".join(str(text_grid.get((r, c), "") or "") for c in col_values)
                if row_txt.strip():
                    header_row = r
                    break

            # 顶部参数区（headerFields）：数据行之前且该行存在输入框
            header_fields = []
            if min_data_row > 0:
                for r in range(0, min_data_row):
                    for c in col_values:
                        arr = field_grid.get((r, c), [])
                        if not arr:
                            continue
                        for slot, it in enumerate(arr, start=1):
                            token = text_grid.get((r, c), "") or str(it.get("placeholder") or "")
                            semantic = _norm(token) or f"header_{r}_{c}_{slot}"
                            semantic = re.sub(r"[^a-z0-9_]+", "_", semantic).strip("_") or f"header_{r}_{c}_{slot}"
                            fid = f"mx_p{page}_t{int(tbl.get('table_id') or 0)}_{semantic}"
                            pdf_field_id = _prefer_pdf_field_id_token(it, f"f{len(header_fields)+1}")
                            src_path = f"testResult.matrixAuto.page{page}.table{int(tbl.get('table_id') or 0)}.header.{semantic}"
                            cell_src: dict = {
                                "pdfFieldId": pdf_field_id,
                                "page": page,
                                "anchorType": str(it.get("fieldType") or "text"),
                                "submitBucket": "testResult",
                                "submitPath": src_path,
                                "legacySubmitPath": src_path,
                                "key": f"step_qc_items.sec_t{page}_{int(tbl.get('table_id') or 0)}_matrix.header.{semantic}",
                            }
                            auto_sem = it.get("autoSemantic") if isinstance(it.get("autoSemantic"), dict) else None
                            apply_table_row_col_to_source(
                                cell_src,
                                table_id=int(tbl.get("table_id") or 0),
                                row=r,
                                col=c,
                                auto_semantic=auto_sem,
                            )
                            header_fields.append(
                                {
                                    "id": fid,
                                    "type": _matrix_field_type_from_pdf(str(it.get("fieldType") or "text")),
                                    "label": token or semantic,
                                    "required": False,
                                    "defaultValue": None,
                                    "precision": 1,
                                    "unit": "",
                                    "source": cell_src,
                                }
                            )

            # value 列优先按表头词识别，其次按有输入框列
            value_cols = []
            if header_row is not None:
                for c in col_values:
                    title = str(text_grid.get((header_row, c), "") or "")
                    nt = _norm(title)
                    if any(tok in title for tok in ("检测值", "报出值", "计算结果", "检测结果")) or any(tok in nt for tok in ("measured", "report", "result")):
                        value_cols.append(c)
            if not value_cols:
                value_cols = sorted(set(c for (_r, c) in field_grid.keys()))
            if not value_cols:
                continue

            row_header_cols = [c for c in col_values if c not in value_cols]
            row_header_columns = []
            for i, c in enumerate(row_header_cols):
                title = str(text_grid.get((header_row, c), "") if header_row is not None else "").strip()
                row_header_columns.append(
                    {
                        "id": f"h{i+1}",
                        "title": title or f"行头{i+1}",
                        "merge": "none" if ("点位" in title or "point" in _norm(title)) else "auto",
                        "width": round(0.50 / max(1, len(row_header_cols)), 4),
                    }
                )
            if not row_header_columns:
                row_header_columns = [{"id": "serialNo", "title": "序号", "merge": "none", "width": 0.08}]

            value_columns = []
            for i, c in enumerate(value_cols):
                title = str(text_grid.get((header_row, c), "") if header_row is not None else "").strip()
                value_columns.append(
                    {
                        "id": "measuredValue" if i == 0 else ("reportValue" if i == 1 else f"value{i+1}"),
                        "title": title or ("检测值" if i == 0 else ("报出值" if i == 1 else f"值{i+1}")),
                        "fieldType": "number",
                        "unit": "μSv/h" if any(u in "".join(text_grid.values()) for u in unit_candidates) else "",
                        "width": round(0.46 / max(1, len(value_cols)), 4),
                    }
                )

            matrix_rows = []
            # 行信息全量保留：数据区从首个输入行开始，包含后续所有表格行（即使该行无输入框）
            body_rows = [r for r in row_values if r >= min_data_row]
            row_seq = 0
            for r in body_rows:
                headers = {}
                if row_header_cols:
                    for i, c in enumerate(row_header_cols):
                        headers[f"h{i+1}"] = str(text_grid.get((r, c), "")).strip()
                else:
                    headers["serialNo"] = str(row_seq + 1)

                row_cells = {}
                static_cells = {}
                for i, c in enumerate(value_cols):
                    key = value_columns[i]["id"]
                    arr = field_grid.get((r, c), [])
                    if not arr:
                        raw_txt = str(text_grid.get((r, c), "")).strip()
                        # 兼容前端 matrix 渲染：即使无输入框，也输出完整 cell schema（只读占位）
                        submit_path = f"testResult.matrixAuto.page{page}.table{int(tbl.get('table_id') or 0)}.rows[{row_seq}].{key}"
                        legacy = f"testResult.legacy.page{page}.table{int(tbl.get('table_id') or 0)}.r{row_seq}.{key}"
                        static_src: dict = {
                            "pdfFieldId": "",
                            "page": page,
                            "anchorType": "text",
                            "submitBucket": "testResult",
                            "submitPath": submit_path,
                            "legacySubmitPath": legacy,
                            "key": f"step_qc_items.sec_t{page}_{int(tbl.get('table_id') or 0)}_matrix.rows[{row_seq}].{key}",
                        }
                        apply_table_row_col_to_source(
                            static_src,
                            table_id=int(tbl.get("table_id") or 0),
                            row=r,
                            col=c,
                        )
                        row_cells[key] = {
                            "id": f"t{int(tbl.get('table_id') or 0)}_row_{row_seq}_{key}",
                            "type": "text",
                            "label": value_columns[i]["title"],
                            "required": False,
                            "defaultValue": raw_txt or None,
                            "precision": 1,
                            "unit": value_columns[i].get("unit") or "",
                            "editable": False,
                            "source": static_src,
                        }
                        if raw_txt:
                            static_cells[key] = {"text": raw_txt, "editable": False}
                        continue
                    it = arr[0]
                    pdf_field_id = _prefer_pdf_field_id_token(it, "")
                    legacy = f"testResult.legacy.page{page}.table{int(tbl.get('table_id') or 0)}.r{row_seq}.{key}"
                    submit_path = f"testResult.matrixAuto.page{page}.table{int(tbl.get('table_id') or 0)}.rows[{row_seq}].{key}"
                    cell_src = {
                        "pdfFieldId": pdf_field_id,
                        "page": page,
                        "anchorType": str(it.get("fieldType") or "text"),
                        "submitBucket": "testResult",
                        "submitPath": submit_path,
                        "legacySubmitPath": legacy,
                        "key": f"step_qc_items.sec_t{page}_{int(tbl.get('table_id') or 0)}_matrix.rows[{row_seq}].{key}",
                    }
                    auto_sem = it.get("autoSemantic") if isinstance(it.get("autoSemantic"), dict) else None
                    apply_table_row_col_to_source(
                        cell_src,
                        table_id=int(tbl.get("table_id") or 0),
                        row=r,
                        col=c,
                        auto_semantic=auto_sem,
                    )
                    row_cells[key] = {
                        "id": f"t{int(tbl.get('table_id') or 0)}_row_{row_seq}_{key}",
                        "type": "number" if str(it.get("fieldType") or "text").lower() == "text" else _matrix_field_type_from_pdf(str(it.get("fieldType") or "text")),
                        "label": value_columns[i]["title"],
                        "required": False,
                        "defaultValue": None,
                        "precision": 1,
                        "unit": value_columns[i].get("unit") or "",
                        "source": cell_src,
                    }
                # 允许“纯静态行”存在，以便前端最大化还原 PDF 表格排版
                if not row_cells and not static_cells and not any(str(v or "").strip() for v in headers.values()):
                    continue
                row_obj = {
                    "id": f"t{int(tbl.get('table_id') or 0)}_row_{row_seq}",
                    "rowIndex": row_seq,
                    "pdfRow": r,
                    "tableId": int(tbl.get("table_id") or 0),
                    "headers": headers,
                    "cells": row_cells,
                }
                if static_cells:
                    row_obj["staticCells"] = static_cells
                matrix_rows.append(row_obj)
                row_seq += 1

            if not matrix_rows:
                continue
            section_id = f"sec_t{page}_{int(tbl.get('table_id') or 0)}_matrix"
            section_title = ""
            if row_header_cols and matrix_rows:
                section_title = matrix_rows[0].get("headers", {}).get("h2") or matrix_rows[0].get("headers", {}).get("h1") or ""
            all_sections.append(
                {
                    "id": section_id,
                    "title": section_title or f"固定表格(P{page}-T{int(tbl.get('table_id') or 0)})",
                    "layout": "matrixTable",
                    "matrix": {
                        "headerFields": header_fields,
                        "rowHeaderColumns": row_header_columns,
                        "valueColumns": value_columns,
                        "rows": matrix_rows,
                        # 提供 PDF 表格全量单元格信息（含坐标+文本），输入框仍仅来自预设识别字段
                        "sourceTable": {
                            "page": page,
                            "tableId": int(tbl.get("table_id") or 0),
                            "cells": [
                                {
                                    "row": int(c.get("row") or 0),
                                    "col": int(c.get("col") or 0),
                                    "x": float(c.get("x") or 0.0),
                                    "y": float(c.get("y") or 0.0),
                                    "w": float(c.get("w") or 0.0),
                                    "h": float(c.get("h") or 0.0),
                                    "text": str(c.get("text") or ""),
                                }
                                for c in cells
                            ],
                        },
                    },
                }
            )

    if not all_sections:
        return []
    return [{"id": "step_qc_items", "title": "质控检测项目", "sections": all_sections}]


def _clean_surrogate_text(value):
    """
    清理字符串中的孤立 surrogate，避免 json dumps -> utf-8 encode 时报错。
    """
    if not isinstance(value, str):
        return value
    # encode/decode with ignore 可移除非法代理字符，保留合法 UTF-8 文本
    return value.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")


def _sanitize_json_payload_text(value):
    if isinstance(value, dict):
        return {k: _sanitize_json_payload_text(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_json_payload_text(v) for v in value]
    if isinstance(value, tuple):
        return [_sanitize_json_payload_text(v) for v in value]
    if isinstance(value, str):
        return _clean_surrogate_text(value)
    return value


def _load_full_text_coordinate_boxing_module():
    path = Path(settings.BASE_DIR) / "htmlpdf" / "full_text_coordinate_boxing.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("full_text_coordinate_boxing", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _extract_table_struct_from_full_text_boxing(source_pdf_path: Path):
    """
    直接复用 full_text_coordinate_boxing 的表格提取链路，输出按页结构化网格。
    """
    mod = _load_full_text_coordinate_boxing_module()
    if mod is None or not source_pdf_path.exists():
        return {}
    out = {}
    doc = mod.fitz.open(str(source_pdf_path))
    try:
        cfg = mod.Config()
        for i in range(len(doc)):
            page = doc[i]
            page_no = i + 1
            spans = mod.extract_text_spans(page)
            table_cells = mod._filter_noise_tables(mod.extract_table_cells(page), cfg) + mod.extract_vector_rect_cells(page, page_no)
            maps = mod._build_structured_cell_maps(page, table_cells, spans)
            table_cells_by_rc = maps.get("table_cells") or {}
            cell_texts = maps.get("cell_texts") or {}
            page_tables = []
            for table_id, cells_by_rc in table_cells_by_rc.items():
                rows = []
                cols = []
                cells = []
                for (r, c), cell in sorted(cells_by_rc.items(), key=lambda kv: (int(kv[0][0]), int(kv[0][1]))):
                    rect = cell.get("rect")
                    if rect is None:
                        continue
                    rows.append(int(r))
                    cols.append(int(c))
                    cells.append(
                        {
                            "row": int(r),
                            "col": int(c),
                            "x": float(rect.x0),
                            "y": float(rect.y0),
                            "w": float(rect.width),
                            "h": float(rect.height),
                            "text": str(cell_texts.get((int(table_id), int(r), int(c)), "") or ""),
                        }
                    )
                if not cells:
                    continue
                page_tables.append(
                    {
                        "table_id": int(table_id),
                        "rows": sorted(set(rows)),
                        "cols": sorted(set(cols)),
                        "cells": cells,
                    }
                )
            if page_tables:
                out[page_no] = page_tables
    finally:
        doc.close()
    return out


def _maybe_rotate_task_json_binding_after_export(
    request, data: dict, meta: dict | None, new_file_row: dict, *, source: str
) -> dict | None:
    """编辑器保存统一坐标模板 JSON 后，将任务主 JSON 绑定轮换为新文件（前端 JSON 不参与）。"""
    if str(source or "").strip() == "editor_export_frontend_json":
        return None
    task = _resolve_library_task_for_htmlpdf_frontend_export(request, data, meta)
    if task is None:
        return {
            "ok": False,
            "skipped": True,
            "error": (
                "未解析到库任务（LibraryTask）。请从「任务模板库」进入编辑器（URL 含 return_manage_task），"
                "或保存时提交 libraryTaskId / 已绑定任务的模板 PDF 的 library_template_file_id。"
            ),
        }
    try:
        new_id = int(new_file_row.get("id"))
    except (TypeError, ValueError):
        return {"ok": False, "skipped": True, "error": "新模板文件 id 无效"}
    lf = LibraryFile.objects.filter(
        pk=new_id, category=LibraryFile.CATEGORY_TEMPLATE
    ).first()
    if lf is None:
        return {"ok": False, "skipped": True, "error": "新模板文件不存在"}
    name = (lf.original_name or "").lower()
    if not name.endswith(".json"):
        return {"ok": False, "skipped": True, "error": "仅 JSON 模板可绑定任务"}
    from apps.core.library_task_template_binding_service import (
        is_auxiliary_template_json_file,
        replace_task_template_file_binding,
    )

    if is_auxiliary_template_json_file(lf):
        return {
            "ok": False,
            "skipped": True,
            "error": "前端规则 JSON / 矩阵 JSON 不参与任务主模板绑定，请使用「保存坐标模板 JSON」",
        }

    return replace_task_template_file_binding(
        task=task,
        new_file=lf,
        user=request.user,
        source=source,
    )


def _resolve_library_task_for_htmlpdf_frontend_export(request, data: dict, meta=None):
    """
    从导出请求中解析 LibraryTask，用于写入 bound_instrument_ids 对应的 instruments。
    优先显式 libraryTaskId；否则用文件库模板文件 id（与任务模板 M2M 关联的 JSON/PDF 均可）。
    """
    if meta is None or not isinstance(meta, dict):
        meta = {}
    def _task_ok(t):
        if t is None:
            return False
        if role_has(request.user, "perm_assign_tasks"):
            return library_user_may_edit_library_task(request.user, t)
        return True

    lt_raw = (
        data.get("libraryTaskId")
        or data.get("library_task_id")
        or meta.get("libraryTaskId")
        or meta.get("library_task_id")
    )
    if lt_raw is not None and str(lt_raw).strip() != "":
        try:
            t = LibraryTask.objects.filter(pk=int(lt_raw)).first()
        except (TypeError, ValueError):
            t = None
        if _task_ok(t):
            return t
    tf_raw = (
        data.get("library_template_file_id")
        or data.get("libraryTemplateFileId")
        or data.get("template_file_id")
        or meta.get("library_template_file_id")
        or meta.get("libraryTemplateFileId")
        or meta.get("template_file_id")
    )
    if tf_raw is None or str(tf_raw).strip() == "":
        return None
    try:
        fid = int(tf_raw)
    except (TypeError, ValueError):
        return None
    lf = LibraryFile.objects.filter(pk=fid, category=LibraryFile.CATEGORY_TEMPLATE).first()
    if lf is None or not library_file_access_allowed(request.user, lf):
        return None
    preferred = None
    if lt_raw is not None and str(lt_raw).strip() != "":
        try:
            preferred = LibraryTask.objects.filter(pk=int(lt_raw)).first()
        except (TypeError, ValueError):
            preferred = None
        if preferred is not None and not _task_ok(preferred):
            preferred = None
    name_low = (lf.original_name or "").lower()
    if name_low.endswith(".pdf"):
        from apps.core.htmlpdf_report_mapping_service import resolve_library_task_for_template_pdf

        t = resolve_library_task_for_template_pdf(lf.pk, preferred_task=preferred)
        return t if _task_ok(t) else None
    tasks = list(LibraryTask.objects.filter(library_files=lf).distinct().order_by("code"))
    tasks = [t for t in tasks if _task_ok(t)]
    if not tasks:
        return None
    if preferred is not None:
        for t in tasks:
            if t.pk == preferred.pk:
                return t
    for t in tasks:
        from utils.task_bound_instruments import bound_instrument_ids_for_legacy_list, normalize_task_bound_instruments

        if bound_instrument_ids_for_legacy_list(
            normalize_task_bound_instruments(getattr(t, "bound_instrument_ids", None) or [])
        ):
            return t
    return tasks[0]


def _try_read_library_template_blob_for_formulas(request, data: dict, meta: dict | None) -> dict | None:
    """若请求未带 fieldFormulas，尝试按模板文件 id 从文件库读取统一模板 JSON（供公式合并）。"""
    if not isinstance(data, dict):
        data = {}
    if not isinstance(meta, dict):
        meta = {}
    tf_raw = (
        data.get("library_template_file_id")
        or data.get("libraryTemplateFileId")
        or data.get("template_file_id")
        or meta.get("library_template_file_id")
        or meta.get("libraryTemplateFileId")
        or meta.get("template_file_id")
    )
    if tf_raw is None or str(tf_raw).strip() == "":
        return None
    try:
        fid = int(tf_raw)
    except (TypeError, ValueError):
        return None
    lf = LibraryFile.objects.filter(pk=fid, category=LibraryFile.CATEGORY_TEMPLATE).first()
    if lf is None or not library_file_access_allowed(request.user, lf):
        return None
    from apps.core.library_task_template_binding_service import (
        is_auxiliary_template_json_file,
        pick_task_primary_json_template,
    )

    read_lf = lf
    if is_auxiliary_template_json_file(lf):
        tasks = list(LibraryTask.objects.filter(library_files=lf).distinct().order_by("code"))
        for t in tasks:
            primary = pick_task_primary_json_template(t)
            if primary is not None:
                read_lf = primary
                break
        else:
            return None
    try:
        p = pipeline_service.library_absolute_path(read_lf.relative_path)
        blob = json_std.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    return blob if isinstance(blob, dict) else None


@csrf_exempt
@require_POST
def htmlpdf_api_export_frontend_json(request):
    auth_resp = _require_api_login(request)
    if auth_resp:
        return auth_resp
    gx = _require_htmlpdf(request)
    if gx:
        return JsonResponse({"error": "forbidden"}, status=403)
    if not library_user_may_use_htmlpdf_matrix_beta_controls(request.user):
        return JsonResponse({"error": "当前账号不可使用内测中的前端 JSON 导出能力"}, status=403)
    try:
        data = json_std.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "请求体不是合法JSON"}, status=400)
    if library_user_has_party_a_demo_restrictions(request.user):
        mode = str(data.get("export_mode") or data.get("exportMode") or "rule").strip().lower()
        if mode == "llm":
            return JsonResponse({"error": "演示账号不可用 AI 方式保存前端 JSON"}, status=403)

    name = safe_library_basename((data.get("name") or "template_frontend").strip() or "template_frontend")
    if not name.lower().endswith(".json"):
        name = f"{name}.json"

    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    template_id = str(data.get("templateId") or meta.get("templateId") or "").strip()
    template_name = str(data.get("templateName") or meta.get("templateName") or "").strip()
    template_version = str(data.get("version") or meta.get("version") or "1.0.0").strip()
    report_type = str(data.get("reportType") or data.get("report_type") or meta.get("reportType") or "").strip()
    standard = str(data.get("standard") or meta.get("standard") or "").strip()
    form_schema = data.get("form_schema") if isinstance(data.get("form_schema"), dict) else {}
    if not form_schema:
        form_schema = data.get("formSchema") if isinstance(data.get("formSchema"), dict) else {}
    constants = data.get("constants") if isinstance(data.get("constants"), dict) else {}
    if not constants:
        constants = form_schema.get("constants") if isinstance(form_schema.get("constants"), dict) else {}
    enums = data.get("enums") if isinstance(data.get("enums"), dict) else {}
    if not enums:
        enums = form_schema.get("enums") if isinstance(form_schema.get("enums"), dict) else {}
    steps = data.get("steps") if isinstance(data.get("steps"), list) else []
    if not steps:
        steps = form_schema.get("steps") if isinstance(form_schema.get("steps"), list) else []
    input_fields = _sanitize_pdf_field_texts(data.get("fields") if isinstance(data.get("fields"), list) else [])
    # 强制规范 pdfFieldId：必须为 f+序号，防止前端传入语义名称污染绑定键。
    input_fields = _normalize_pdf_fields_for_unified_template(input_fields, {}, {})
    is_report_fe = _htmlpdf_is_report_template_context(
        request, data, meta if isinstance(meta, dict) else {}
    )
    _assign_htmlpdf_template_sections_to_fields(
        request.user.id, input_fields, skip_for_report=is_report_fe
    )
    payload = {
        "templateId": template_id or name.rsplit(".", 1)[0],
        "templateName": template_name or name,
        "version": template_version or "1.0.0",
        "reportType": report_type,
        "standard": standard,
        "pdfUrl": str(data.get("pdfUrl") or meta.get("pdfUrl") or ""),
        "locale": str(data.get("locale") or meta.get("locale") or "zh-CN"),
        "constants": constants,
        "enums": enums,
        "steps": steps,
    }
    has_matrix_layout = False
    for st in payload.get("steps") if isinstance(payload.get("steps"), list) else []:
        if not isinstance(st, dict):
            continue
        for sec in st.get("sections") if isinstance(st.get("sections"), list) else []:
            if not isinstance(sec, dict):
                continue
            if str(sec.get("layout") or "").strip().lower() == "matrixtable":
                has_matrix_layout = True
                break
        if has_matrix_layout:
            break
    # 先以规则引擎生成标准结构（分桶 + 坐标排序），再按模式决定是否被 LLM 覆盖。
    base_rule_payload = {}
    if not has_matrix_layout:
        try:
            base_rule_payload = build_frontend_schema_by_rules(
                {
                    "templateId": payload["templateId"],
                    "templateName": payload["templateName"],
                    "version": payload["version"],
                    "reportType": payload["reportType"],
                    "standard": payload["standard"],
                    "pdfUrl": payload["pdfUrl"],
                    "locale": payload["locale"],
                    "constants": payload["constants"],
                    "enums": payload["enums"],
                    "steps": payload["steps"],
                    "pdf": {"fields": input_fields},
                    "meta": meta,
                }
            )
        except Exception:
            base_rule_payload = {}
        if isinstance(base_rule_payload, dict) and isinstance(base_rule_payload.get("steps"), list) and base_rule_payload.get("steps"):
            payload = {
                "templateId": str(base_rule_payload.get("templateId") or payload["templateId"]),
                "templateName": str(base_rule_payload.get("templateName") or payload["templateName"]),
                "version": str(base_rule_payload.get("version") or payload["version"]),
                "reportType": str(base_rule_payload.get("reportType") or payload["reportType"]),
                "standard": str(base_rule_payload.get("standard") or payload["standard"]),
                "pdfUrl": str(base_rule_payload.get("pdfUrl") or payload["pdfUrl"]),
                "locale": str(base_rule_payload.get("locale") or payload["locale"]),
                "constants": base_rule_payload.get("constants") if isinstance(base_rule_payload.get("constants"), dict) else payload["constants"],
                "enums": base_rule_payload.get("enums") if isinstance(base_rule_payload.get("enums"), dict) else payload["enums"],
                "steps": base_rule_payload.get("steps") or payload["steps"],
            }

    # 保存前端 JSON 支持三种导出模式：
    # - rule: 纯规则引擎（不经过大模型）
    # - llm: 强制大模型（失败回退规则引擎）
    # - auto: 自动模式（默认）
    export_mode = str(data.get("export_mode") or "auto").strip().lower()
    if export_mode not in {"auto", "llm", "rule"}:
        export_mode = "auto"
    llm_auto = str(os.environ.get("ENABLE_LLM_FRONTEND_EXPORT", "1")).strip().lower() in {"1", "true", "yes", "on"}
    llm_force = str(data.get("force_llm_steps") or "").strip().lower() in {"1", "true", "yes", "on"}
    need_llm = (not has_matrix_layout) and (
        export_mode == "llm" or (export_mode == "auto" and llm_auto and (llm_force or not payload.get("steps")))
    )
    need_rule = (export_mode == "rule") and (not has_matrix_layout)
    rule_template_obj = {
        "templateId": payload["templateId"],
        "templateName": payload["templateName"],
        "version": payload["version"],
        "reportType": payload["reportType"],
        "standard": payload["standard"],
        "pdfUrl": payload["pdfUrl"],
        "locale": payload["locale"],
        "constants": payload["constants"],
        "enums": payload["enums"],
        "steps": payload["steps"],
        "pdf": {"fields": input_fields},
        "meta": meta,
    }
    if need_llm:
        sample_template_obj = {}
        sample_path = Path(
            os.environ.get(
                "FRONTEND_TEMPLATE_SAMPLE_PATH",
                str(settings.BASE_DIR / "media" / "file_library" / "templates" / "365af054b1d14cf9938440cd8c62f1f8_template_frontend.json"),
            )
        )
        try:
            if sample_path.is_file():
                sample_template_obj = json_std.loads(sample_path.read_text(encoding="utf-8"))
        except Exception:
            sample_template_obj = {}
        llm_template_obj = {
            "schema": "unified_form_template/v2",
            "templateId": payload["templateId"],
            "templateName": payload["templateName"],
            "version": payload["version"],
            "reportType": payload["reportType"],
            "standard": payload["standard"],
            "pdfUrl": payload["pdfUrl"],
            "locale": payload["locale"],
            "constants": payload["constants"],
            "enums": payload["enums"],
            "steps": payload["steps"],
            "pdf": {"fields": input_fields},
            "formSchema": {
                "constants": payload["constants"],
                "enums": payload["enums"],
                "steps": payload["steps"],
            },
            "bindings": data.get("bindings") if isinstance(data.get("bindings"), dict) else {},
        }
        llm_payload = generate_frontend_template_with_ollama(
            llm_template_obj,
            source_file_hint=1,
            sample_template_obj=sample_template_obj,
        )
        if isinstance(llm_payload, dict) and isinstance(llm_payload.get("steps"), list) and llm_payload.get("steps"):
            payload = {
                "templateId": str(llm_payload.get("templateId") or payload["templateId"]),
                "templateName": str(llm_payload.get("templateName") or payload["templateName"]),
                "version": str(llm_payload.get("version") or payload["version"]),
                "reportType": str(llm_payload.get("reportType") or payload["reportType"]),
                "standard": str(llm_payload.get("standard") or payload["standard"]),
                "pdfUrl": str(llm_payload.get("pdfUrl") or payload["pdfUrl"]),
                "locale": str(llm_payload.get("locale") or payload["locale"]),
                "constants": llm_payload.get("constants") if isinstance(llm_payload.get("constants"), dict) else payload["constants"],
                "enums": llm_payload.get("enums") if isinstance(llm_payload.get("enums"), dict) else payload["enums"],
                "steps": llm_payload.get("steps"),
            }
        else:
            need_rule = True

    if need_rule or (export_mode == "auto" and not payload.get("steps")):
        rule_payload = build_frontend_schema_by_rules(rule_template_obj)
        if isinstance(rule_payload, dict) and isinstance(rule_payload.get("steps"), list):
            payload = {
                "templateId": str(rule_payload.get("templateId") or payload["templateId"]),
                "templateName": str(rule_payload.get("templateName") or payload["templateName"]),
                "version": str(rule_payload.get("version") or payload["version"]),
                "reportType": str(rule_payload.get("reportType") or payload["reportType"]),
                "standard": str(rule_payload.get("standard") or payload["standard"]),
                "pdfUrl": str(rule_payload.get("pdfUrl") or payload["pdfUrl"]),
                "locale": str(rule_payload.get("locale") or payload["locale"]),
                "constants": rule_payload.get("constants") if isinstance(rule_payload.get("constants"), dict) else payload["constants"],
                "enums": rule_payload.get("enums") if isinstance(rule_payload.get("enums"), dict) else payload["enums"],
                "steps": rule_payload.get("steps") or payload["steps"],
            }

    # 根级 instruments：无 submit 时用任务模板 bound_instrument_ids 预填；有 submit 时由导出/提交链路传入 payload（以前端为准）
    from apps.api.inspection_report_make import build_instruments_root_for_frontend_export

    lib_task_for_inst = _resolve_library_task_for_htmlpdf_frontend_export(
        request, data, meta if isinstance(meta, dict) else {}
    )
    _ff_src: dict = {"pdf": {"fields": input_fields}}
    _ff_raw_fe = _export_request_field_formulas_raw(data, form_schema)
    if _ff_raw_fe is not None:
        _ff_src["fieldFormulas"] = _ff_raw_fe
    else:
        _blob = _try_read_library_template_blob_for_formulas(request, data, meta if isinstance(meta, dict) else {})
        if isinstance(_blob, dict):
            _ff_src = _blob
    payload = merge_field_formulas_into_frontend(payload, _ff_src)
    payload.update(build_instruments_root_for_frontend_export(task_obj=lib_task_for_inst, payload={}))
    payload.pop("instrumentCatalogOptions", None)
    raw_map_fe = data.get("report_site_field_map") or data.get("reportSiteFieldMap")
    if isinstance(raw_map_fe, list):
        from apps.core.htmlpdf_report_mapping_service import merge_report_site_map_into_bindings

        existing_b = payload.get("bindings") if isinstance(payload.get("bindings"), dict) else {}
        payload["bindings"] = merge_report_site_map_into_bindings(existing_b, raw_map_fe)
    from utils.frontend_schema_rule_engine import _compact_form_schema_payload

    payload = _compact_form_schema_payload(payload)

    try:
        raw = json_std.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    except UnicodeEncodeError:
        payload = _sanitize_json_payload_text(payload)
        raw = json_std.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    wrapped = type("UploadLike", (), {"read": lambda self: raw, "name": name})()
    created, skipped = save_library_binary_uploads(
        request.user,
        [wrapped],
        LibraryFile.CATEGORY_TEMPLATE,
    )
    if skipped and not created:
        return JsonResponse({"error": "前端JSON保存失败"}, status=500)
    row = created[0]
    register_tour_library_file(request, int(row["id"]))
    detached_aux_ids: list[int] = []
    if lib_task_for_inst is not None:
        from apps.core.library_task_template_binding_service import (
            detach_auxiliary_template_json_from_task,
        )

        detached_aux_ids = detach_auxiliary_template_json_from_task(
            lib_task_for_inst, user=request.user
        )
    resp = {
        "ok": True,
        "saved_to": "template",
        "auxiliary_json": True,
        "task_template_binding": False,
        "file": {
            "id": row["id"],
            "name": row["original_name"],
            "category": row["category"],
            "library_url": reverse("file_library") + "?tab=template",
            "download_url": reverse("file_library_download", kwargs={"pk": row["id"]}),
        },
    }
    if detached_aux_ids:
        resp["detached_auxiliary_from_task"] = detached_aux_ids
    return JsonResponse(resp)


@csrf_exempt
@require_POST
def htmlpdf_api_export_matrix_json(request):
    auth_resp = _require_api_login(request)
    if auth_resp:
        return auth_resp
    gx = _require_htmlpdf(request)
    if gx:
        return JsonResponse({"error": "forbidden"}, status=403)
    if not library_user_may_use_htmlpdf_matrix_beta_controls(request.user):
        return JsonResponse({"error": "当前账号不可使用内测中的固定表格模板 JSON 导出"}, status=403)
    try:
        data = json_std.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "请求体不是合法JSON"}, status=400)
    fields = _sanitize_pdf_field_texts(data.get("fields", []))
    if not fields:
        # 全自动模式：无需先点击自动划框，后端直接跑自动提取。
        fields = _sanitize_pdf_field_texts(htmlpdf_service.htmlpdf_auto_red_text_fields_for_editor(request.user.id))
    template_meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    report_type = str(data.get("report_type") or "").strip()
    standard = str(data.get("standard") or "").strip()
    name = safe_library_basename((data.get("name") or "template_matrix").strip() or "template_matrix")
    if not name.lower().endswith(".json"):
        name = f"{name}.json"

    source_meta = {}
    meta_path = htmlpdf_service.htmlpdf_source_meta_path(request.user.id)
    if meta_path.is_file():
        try:
            source_meta = json_std.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            source_meta = {}

    normalized_fields = _normalize_pdf_fields_for_unified_template(fields, {}, {})
    table_struct = _extract_table_struct_from_full_text_boxing(htmlpdf_service.htmlpdf_source_pdf_path(request.user.id))
    steps = _build_matrix_steps_from_auto_fields(
        normalized_fields,
        htmlpdf_service.htmlpdf_source_pdf_path(request.user.id),
        table_struct_by_page=table_struct,
    )
    if not steps:
        return JsonResponse({"error": "未识别到可结构化的表格单元格，请先执行自动划框并确认字段落在表格中"}, status=409)

    tid = template_meta.get("templateId") or name.rsplit(".", 1)[0]
    tname = template_meta.get("templateName") or name
    tver = template_meta.get("version") or "1.0.0"
    payload = _slim_unified_v2_template_library_payload(
        template_id=tid,
        template_name=tname,
        version=tver,
        report_type=report_type,
        standard=standard,
        pdf_url=str(template_meta.get("pdfUrl") or ""),
        locale=str(template_meta.get("locale") or "zh-CN"),
        source_pdf=source_meta,
        normalized_pdf_fields=normalized_fields,
        constants={},
        enums={},
        steps=steps,
        bindings=None,
    )
    from utils.frontend_schema_rule_engine import _compact_form_schema_payload

    payload = _compact_form_schema_payload(payload)
    payload = _sanitize_json_payload_text(payload)
    try:
        raw = json_std.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    except UnicodeEncodeError:
        # 最终兜底：允许代理对透传，避免单个脏字符导致接口 500
        raw = json_std.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8", errors="surrogatepass")
    wrapped = type("UploadLike", (), {"read": lambda self: raw, "name": name})()
    created, skipped = save_library_binary_uploads(
        request.user,
        [wrapped],
        LibraryFile.CATEGORY_TEMPLATE,
    )
    if skipped and not created:
        return JsonResponse({"error": "模板保存失败"}, status=500)
    row = created[0]
    return JsonResponse(
        {
            "ok": True,
            "saved_to": "template",
            "file": {
                "id": row["id"],
                "name": row["original_name"],
                "category": row["category"],
                "library_url": reverse("file_library") + "?tab=template",
                "download_url": reverse("file_library_download", kwargs={"pk": row["id"]}),
            },
            "steps_count": len(steps),
        }
    )


@csrf_exempt
@require_POST
def htmlpdf_api_save_pdf(request):
    auth_resp = _require_api_login(request)
    if auth_resp:
        return auth_resp
    gx = _require_htmlpdf(request)
    if gx:
        return JsonResponse({"error": "forbidden"}, status=403)
    try:
        data = json_std.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "请求体不是合法JSON"}, status=400)
    fields = data.get("fields", [])
    output_category = (data.get("output_category") or "").strip()
    task_id_raw = str(data.get("task_id") or "").strip()
    if not output_category and task_id_raw:
        try:
            task_id = int(task_id_raw)
        except ValueError:
            task_id = 0
        task_obj = LibraryTask.objects.filter(pk=task_id).only("output_target").first() if task_id else None
        if task_obj and task_obj.output_target == LibraryTask.OUTPUT_REPORT:
            output_category = LibraryFile.CATEGORY_REPORT
        else:
            output_category = LibraryFile.CATEGORY_SITE_RECORD
    if not output_category:
        output_category = LibraryFile.CATEGORY_SITE_RECORD
    if output_category not in (LibraryFile.CATEGORY_SITE_RECORD, LibraryFile.CATEGORY_REPORT):
        return JsonResponse({"error": "output_category 仅支持 site_record/report"}, status=400)
    try:
        pdf_bytes = htmlpdf_service.build_filled_pdf(
            fields, htmlpdf_service.htmlpdf_source_pdf_path(request.user.id)
        )
    except FileNotFoundError:
        return JsonResponse({"error": "请先上传PDF"}, status=400)
    except Exception as exc:
        return JsonResponse({"error": f"生成PDF失败: {exc}"}, status=500)

    filename = safe_library_basename((data.get("name") or "填写完成").strip() or "填写完成")
    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"
    wrapped = type("UploadLike", (), {"read": lambda self: pdf_bytes, "name": filename})()
    created, skipped = save_library_binary_uploads(
        request.user,
        [wrapped],
        output_category,
    )
    if skipped and not created:
        return JsonResponse({"error": "保存PDF失败"}, status=500)
    row = created[0]
    target_tab = "site_record" if output_category == LibraryFile.CATEGORY_SITE_RECORD else "report"
    return JsonResponse(
        {
            "ok": True,
            "saved_to": target_tab,
            "file": {
                "id": row["id"],
                "name": row["original_name"],
                "category": row["category"],
                "library_url": reverse("file_library") + f"?tab={target_tab}",
                "download_url": reverse("file_library_download", kwargs={"pk": row["id"]}),
            },
        }
    )


@csrf_exempt
@require_POST
def htmlpdf_api_table_cell_at_point(request):
    auth_resp = _require_api_login(request)
    if auth_resp:
        return auth_resp
    gx = _require_htmlpdf(request)
    if gx:
        return JsonResponse({"error": "forbidden"}, status=403)
    try:
        data = json_std.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "请求体不是合法JSON"}, status=400)
    try:
        page_no = int(data.get("page") or 0)
        x = float(data.get("x"))
        y = float(data.get("y"))
    except Exception:
        return JsonResponse({"error": "page/x/y 参数无效"}, status=400)
    cell = htmlpdf_service.detect_table_cell_bbox_at_point(
        htmlpdf_service.htmlpdf_source_pdf_path(request.user.id),
        page_no,
        x,
        y,
    )
    if not cell:
        return JsonResponse({"found": False, "cell": None})
    return JsonResponse({"found": True, "cell": cell})


@csrf_exempt
@login_required
@require_POST
def htmlpdf_api_report_task_context(request):
    gx = _require_htmlpdf(request)
    if gx:
        return JsonResponse({"error": "forbidden"}, status=403)
    try:
        data = json_std.loads(request.body.decode("utf-8"))
        task_raw = data.get("library_task_id") or data.get("libraryTaskId")
        task_id = int(task_raw) if task_raw not in (None, "") else None
        json_fid = data.get("template_json_file_id") or data.get("templateJsonFileId")
        json_fid = int(json_fid) if json_fid not in (None, "") else None
        pdf_fid = data.get("template_pdf_file_id") or data.get("templatePdfFileId")
        pdf_fid = int(pdf_fid) if pdf_fid not in (None, "") else None
    except (TypeError, ValueError, json_std.JSONDecodeError):
        return JsonResponse({"error": "参数无效"}, status=400)
    from apps.core.htmlpdf_report_mapping_service import build_report_task_template_context

    payload = build_report_task_template_context(
        request.user,
        library_task_id=task_id,
        template_json_file_id=json_fid,
        template_pdf_file_id=pdf_fid,
    )
    if not payload.get("ok"):
        return JsonResponse({"error": payload.get("error") or "加载失败"}, status=400)
    return JsonResponse(payload)


@csrf_exempt
@login_required
@require_POST
def htmlpdf_api_site_template_bundle(request):
    gx = _require_htmlpdf(request)
    if gx:
        return JsonResponse({"error": "forbidden"}, status=403)
    try:
        data = json_std.loads(request.body.decode("utf-8"))
        site_task_id = int(data.get("site_task_id") or data.get("siteTaskId") or 0)
    except (TypeError, ValueError, json_std.JSONDecodeError):
        return JsonResponse({"error": "site_task_id 无效"}, status=400)
    from apps.core.htmlpdf_report_mapping_service import build_site_template_bundle

    payload = build_site_template_bundle(request.user, site_task_id=site_task_id)
    if not payload.get("ok"):
        return JsonResponse({"error": payload.get("error") or "加载失败"}, status=400)
    return JsonResponse(payload)


@csrf_exempt
@require_POST
def htmlpdf_api_auto_red_text_boxes(request):
    auth_resp = _require_api_login(request)
    if auth_resp:
        return auth_resp
    gx = _require_htmlpdf(request)
    if gx:
        return JsonResponse({"error": "forbidden"}, status=403)
    try:
        data = json_std.loads(request.body.decode("utf-8")) if request.body else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    rows = htmlpdf_service.htmlpdf_auto_red_text_fields_for_editor(request.user.id)
    if _htmlpdf_is_report_template_context(request, data):
        htmlpdf_service.strip_site_record_section_fields(
            rows, remove_all_section_keys=True
        )
    return JsonResponse({"fields": rows, "count": len(rows)})


@csrf_exempt
@require_POST
def htmlpdf_api_assign_template_sections(request):
    """按当前 PDF 章节锚点为编辑器栏位写入 templateSectionKey / templateSectionTitle。"""
    auth_resp = _require_api_login(request)
    if auth_resp:
        return auth_resp
    gx = _require_htmlpdf(request)
    if gx:
        return JsonResponse({"error": "forbidden"}, status=403)
    try:
        data = json_std.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "请求体不是合法JSON"}, status=400)
    fields = _sanitize_pdf_field_texts(data.get("fields") if isinstance(data.get("fields"), list) else [])
    if not fields:
        return JsonResponse({"error": "fields 为空"}, status=400)
    if _htmlpdf_is_report_template_context(request, data):
        htmlpdf_service.strip_site_record_section_fields(
            fields, remove_all_section_keys=True
        )
        return JsonResponse(
            {
                "fields": fields,
                "count": len(fields),
                "skipped": True,
                "message": "报告模板不使用现场记录六大章节",
            }
        )
    rows = htmlpdf_service.assign_template_sections_for_editor(request.user.id, fields)
    anchors: list = []
    try:
        pdf_path = htmlpdf_service.htmlpdf_source_pdf_path(request.user.id)
        if pdf_path.is_file():
            from htmlpdf.full_text_coordinate_boxing import site_record_section_anchors_for_pdf

            anchors = site_record_section_anchors_for_pdf(str(pdf_path))
    except Exception:
        anchors = []
    return JsonResponse({"fields": rows, "count": len(rows), "anchors": anchors})


@login_required
def file_temp_batch(request, batch_id):
    rows = LibraryFile.objects.filter(batch_id=batch_id, category=LibraryFile.CATEGORY_TEMP).order_by(
        "relative_path"
    )
    return render(
        request,
        "core/file_temp_batch.html",
        {"batch_id": batch_id, "files": rows},
    )


def _unique_library_task_code(name: str, explicit_code: str = "") -> str:
    from django.utils.text import slugify

    base = (slugify(explicit_code)[:64] if explicit_code else "") or slugify(name)[:64] or "task"
    code = base
    n = 0
    while LibraryTask.objects.filter(code=code).exists():
        n += 1
        suffix = f"-{n}"
        max_base = max(1, 64 - len(suffix))
        code = (base[:max_base] + suffix)[:64]
    return code


def _library_task_management_redirect_url(request) -> str:
    u = reverse("library_task_management")
    q = []
    mt = (request.POST.get("manage_task_id") or request.GET.get("manage_task") or "").strip()
    if mt:
        try:
            int(mt)
            q.append("manage_task=" + mt)
        except ValueError:
            pass
    tt = (request.POST.get("task_tab") or request.GET.get("task_tab") or "").strip()
    if tt in _FILE_LIBRARY_VALID_TABS:
        q.append("task_tab=" + tt)
    if task_library_edit_mode(request):
        q.append("edit=1")
    nt = (request.POST.get("new_task") or request.GET.get("new_task") or "").strip().lower()
    if nt in ("1", "true", "yes", "on"):
        q.append("new_task=1")
    ep = (request.POST.get("export_project_id") or request.GET.get("export_project_id") or "").strip()
    if ep:
        try:
            int(ep)
            q.append("export_project_id=" + ep)
        except ValueError:
            pass
    rp = (request.POST.get("return_project") or request.GET.get("return_project") or "").strip()
    if rp:
        try:
            int(rp)
            q.append("return_project=" + rp)
        except ValueError:
            pass
    fl = (request.POST.get("fl_path") or request.GET.get("fl_path") or "").strip()
    if fl:
        q.append("fl_path=" + quote(fl))
    if q:
        u += "?" + "&".join(q)
    return u


@login_required
def redirect_to_task_management(request):
    """旧路径 /files/tasks/ 与 /files/library-tasks/ 跳转至任务管理。"""
    u = reverse("library_task_management")
    q = request.GET.urlencode()
    if q:
        u += "?" + q
    return redirect(u)


@login_required
def library_task_management(request):
    """任务模板库：新建任务模板、维护模板文件绑定与默认仪器（分配在项目工作台）。"""
    gx = _require_perm(request, "perm_file_library")
    if gx:
        return gx
    can_assign_tasks = role_has(request.user, "perm_assign_tasks")
    can_write_task_templates = role_has(request.user, "perm_library_task_templates_write")
    can_manage_in_task_library = can_assign_tasks or can_write_task_templates
    can_participant_task_library = (
        library_user_may_filled_pdf_toolchain(request.user)
        and LibraryTaskAssignment.objects.filter(assignee=request.user).exists()
    )
    can_access_task_library = library_user_may_access_task_template_library_nav(request.user)
    if not can_access_task_library:
        messages.info(
            request,
            "进入任务模板库需：具备「分配文件库任务」或（沙箱覆盖）「任务模板自建」权限以维护模板，"
            "或已被分配到检测任务且具备模板填 PDF 能力以试导现场记录/报告。"
            "项目与记录请在「项目工作台」操作。",
        )
        return redirect(reverse("library_projects"))
    projects = (
        LibraryProject.objects.filter(is_active=True)
        .prefetch_related("library_tasks")
        .order_by("code")
    )
    if not can_assign_tasks:
        _scoped_pids = set(library_user_scoped_project_ids(request.user))
        if _scoped_pids:
            projects = projects.filter(pk__in=_scoped_pids)
        else:
            projects = projects.none()

    if request.method == "POST":
        action = request.POST.get("action", "").strip()
        if action == "move_report_to_task_folder":
            if not can_manage_in_task_library:
                return _action_json_or_redirect(
                    request,
                    ok=False,
                    message="当前角色无权移动报告模板",
                    redirect_url=reverse("library_task_management"),
                )
            if not task_library_edit_mode(request):
                return _action_json_or_redirect(
                    request,
                    ok=False,
                    message="请先点击页顶「编辑」后再移动报告模板",
                    redirect_url=_library_task_management_redirect_url(request),
                )
            try:
                rid = int(request.POST.get("report_task_id", "") or 0)
            except ValueError:
                rid = 0
            try:
                target_folder_id = int((request.POST.get("target_folder_id") or "").strip())
            except ValueError:
                target_folder_id = int(UNCATEGORIZED_KEY)
            report = LibraryTask.objects.filter(
                pk=rid, output_target=LibraryTask.OUTPUT_REPORT
            ).first()
            fl = (request.POST.get("fl_path") or "").strip()
            redir = reverse("library_task_management") + (f"?fl_path={quote(fl)}" if fl else "")
            if report is None:
                return _action_json_or_redirect(
                    request, ok=False, message="报告模板无效", redirect_url=redir
                )
            if not library_user_may_edit_library_task(request.user, report):
                return _action_json_or_redirect(
                    request, ok=False, message="无权移动该报告模板", redirect_url=redir
                )
            folder, err = move_report_to_task_folder(report, target_folder_id)
            if err:
                return _action_json_or_redirect(request, ok=False, message=err, redirect_url=redir)
            if folder is not None:
                redir = reverse("library_task_management") + f"?fl_path={quote(folder.folder_path())}"
            else:
                redir = (
                    reverse("library_task_management")
                    + f"?fl_path=f-{UNCATEGORIZED_KEY}/r-{report.pk}"
                )
            label = folder.name if folder else "未分类报告"
            return _action_json_or_redirect(
                request,
                ok=True,
                message=f"已将报告「{report.name}」移至「{label}」",
                redirect_url=redir,
            )

        if action == "export_task_template_pdf" and library_user_may_filled_pdf_toolchain(request.user):
            try:
                tid = int(request.POST.get("manage_task_id", "") or 0)
            except ValueError:
                tid = 0
            try:
                project_id = int(request.POST.get("export_project_id", "") or 0)
            except ValueError:
                project_id = 0
            task_obj = LibraryTask.objects.filter(pk=tid).first() if tid else None
            project = LibraryProject.objects.filter(pk=project_id, is_active=True).first() if project_id else None
            if task_obj is None:
                messages.error(request, "任务模板不存在或无效")
                return redirect(_library_task_management_redirect_url(request))
            if project is None:
                messages.error(request, "请先选择项目")
                return redirect(_library_task_management_redirect_url(request))
            if not library_user_may_export_task_template_pdf_for_project(request.user, task_obj, project):
                messages.error(request, "无权在该项目上对此任务模板执行导出")
                return redirect(_library_task_management_redirect_url(request))
            if task_obj.output_target not in {LibraryTask.OUTPUT_SITE_RECORD, LibraryTask.OUTPUT_REPORT}:
                messages.error(request, "仅现场记录/报告模板支持此导出入口")
                return redirect(_library_task_management_redirect_url(request))
            latest_submission = (
                InspectionSubmission.objects.filter(project=project)
                .select_related("case")
                .order_by("-updated_at", "-id")
                .first()
            )
            if latest_submission is None:
                messages.error(request, "该项目下暂无可用的 submit 记录")
                return redirect(
                    reverse("library_task_management") + f"?manage_task={task_obj.pk}&export_project_id={project.pk}"
                )
            case = latest_submission.case
            if case is None:
                messages.error(request, "最新 submit 记录缺少关联案件，无法导出")
                return redirect(
                    reverse("library_task_management") + f"?manage_task={task_obj.pk}&export_project_id={project.pk}"
                )

            from apps.api.inspection_pdf_service import (
                _build_filled_template_fields_for_task,
                _deep_merge_payload_dicts,
                _load_site_record_payload_for_report,
                _persist_filled_pdf_from_submit,
            )

            payload = latest_submission.raw_payload if isinstance(latest_submission.raw_payload, dict) else {}
            if not payload:
                payload = {
                    "taskNo": latest_submission.task_no,
                    "projectId": project.code,
                    "reportInfo": latest_submission.report_info or {},
                    "hospitalInfo": latest_submission.hospital_info or {},
                    "equipmentInfo": latest_submission.equipment_info or {},
                    "testResult": latest_submission.test_result or {},
                    "updatedAt": latest_submission.updated_at_remote.isoformat()
                    if latest_submission.updated_at_remote
                    else "",
                }
            else:
                payload.setdefault("taskNo", latest_submission.task_no)
                payload.setdefault("projectId", project.code)
                payload.setdefault(
                    "updatedAt",
                    latest_submission.updated_at_remote.isoformat() if latest_submission.updated_at_remote else "",
                )
                payload.setdefault("reportInfo", latest_submission.report_info or {})
                payload.setdefault("hospitalInfo", latest_submission.hospital_info or {})
                payload.setdefault("equipmentInfo", latest_submission.equipment_info or {})
                payload.setdefault("testResult", latest_submission.test_result or {})
            if task_obj.output_target == LibraryTask.OUTPUT_REPORT:
                report_payload, source_reason = _load_site_record_payload_for_report(case, project, task_obj)
                if not isinstance(report_payload, dict) or not report_payload:
                    messages.error(request, source_reason or "未找到可用的现场记录，无法导出报告")
                    return redirect(
                        reverse("library_task_management") + f"?manage_task={task_obj.pk}&export_project_id={project.pk}"
                    )
                report_payload.setdefault("taskNo", latest_submission.task_no)
                report_payload.setdefault("projectId", project.code)
                report_payload.setdefault(
                    "updatedAt",
                    latest_submission.updated_at_remote.isoformat() if latest_submission.updated_at_remote else "",
                )
                payload = _deep_merge_payload_dicts(report_payload, payload)

            filled_fields, template_pdf_id, fill_reason, template_json_name = _build_filled_template_fields_for_task(
                task_obj,
                payload,
                project=project,
                task_no=latest_submission.task_no,
                inspection_case=case,
            )
            if not filled_fields:
                messages.error(request, fill_reason or "模板填充失败")
                return redirect(
                    reverse("library_task_management") + f"?manage_task={task_obj.pk}&export_project_id={project.pk}"
                )
            ok, pdf_reason, _ = _persist_filled_pdf_from_submit(
                request.user,
                latest_submission.task_no,
                case,
                project,
                filled_fields,
                template_pdf_id=template_pdf_id,
                template_json_name=template_json_name,
                task_obj=task_obj,
            )
            if not ok:
                messages.error(request, pdf_reason or "导出 PDF 失败")
            else:
                out_label = "报告" if task_obj.output_target == LibraryTask.OUTPUT_REPORT else "现场记录"
                msg = f"已导出{out_label} PDF（项目：{project.code}，模板：{task_obj.code}，提交：{latest_submission.task_no}）"
                if pdf_reason:
                    msg = f"{msg}，提示：{pdf_reason}"
                messages.success(request, msg)
            return redirect(
                reverse("library_task_management") + f"?manage_task={task_obj.pk}&export_project_id={project.pk}"
            )
        if not can_manage_in_task_library:
            messages.error(request, "当前角色无权执行该操作")
            return redirect(_library_task_management_redirect_url(request))
        if not task_library_edit_mode(request):
            messages.error(request, "请先点击页顶「编辑」后再修改任务模板库")
            return redirect(_library_task_management_redirect_url(request))
        if action == "create_task_folder":
            parent = None
            parent_raw = (request.POST.get("parent_folder_id") or "").strip()
            if parent_raw:
                try:
                    parent = LibraryTaskFolder.objects.filter(
                        pk=int(parent_raw), is_active=True
                    ).first()
                except ValueError:
                    parent = None
            row, err = create_task_folder(
                request.user,
                name=request.POST.get("folder_name", ""),
                parent=parent,
                notes=request.POST.get("folder_notes", ""),
            )
            if err:
                messages.error(request, err)
            else:
                messages.success(request, f"已创建任务分类：{row.name}")
                fl_path = row.folder_path()
                return redirect(reverse("library_task_management") + f"?fl_path={quote(fl_path)}")
            return redirect(_library_task_management_redirect_url(request))
        if action == "delete_task_folder":
            try:
                fid = int(request.POST.get("folder_id", "") or 0)
            except ValueError:
                fid = 0
            folder = LibraryTaskFolder.objects.filter(pk=fid, is_active=True).first()
            if folder is None:
                messages.error(request, "分类不存在")
            else:
                err = deactivate_task_folder(folder)
                if err:
                    messages.error(request, err)
                else:
                    messages.success(request, f"已删除分类：{folder.name}")
                    parent_path = folder.parent.folder_path() if folder.parent_id else ""
                    if parent_path:
                        return redirect(
                            reverse("library_task_management") + f"?fl_path={quote(parent_path)}"
                        )
            return redirect(_library_task_management_redirect_url(request))
        if action == "rename_task_folder":
            try:
                fid = int(request.POST.get("folder_id", "") or 0)
            except ValueError:
                fid = 0
            folder = LibraryTaskFolder.objects.filter(pk=fid, is_active=True).first()
            if folder is None:
                messages.error(request, "分类不存在")
            else:
                err = rename_task_folder(folder, request.POST.get("folder_name", ""))
                if err:
                    messages.error(request, err)
                else:
                    messages.success(request, f"已重命名分类为：{folder.name}")
            return redirect(_library_task_management_redirect_url(request))
        if action == "rename_library_task":
            try:
                tid = int(request.POST.get("manage_task_id", "") or 0)
            except ValueError:
                tid = 0
            t_obj = LibraryTask.objects.filter(pk=tid).first()
            if t_obj is None:
                messages.error(request, "任务模板不存在")
            elif not library_user_may_edit_library_task(request.user, t_obj):
                messages.error(request, "无权编辑该任务模板")
            else:
                new_name = (request.POST.get("name") or "").strip()
                if not new_name:
                    messages.error(request, "名称不能为空")
                else:
                    t_obj.name = new_name
                    t_obj.save(update_fields=["name", "updated_at"])
                    messages.success(request, f"已重命名为：{new_name}")
            return redirect(_library_task_management_redirect_url(request))
        if action == "rename_template_file":
            try:
                file_id = int(request.POST.get("file_id", "") or 0)
            except ValueError:
                file_id = 0
            lf = LibraryFile.objects.filter(pk=file_id, category=LibraryFile.CATEGORY_TEMPLATE).first()
            if lf is None:
                messages.error(request, "模板文件不存在")
            else:
                err = rename_library_template_file(lf, request.POST.get("original_name", ""))
                if err:
                    messages.error(request, err)
                else:
                    messages.success(request, f"已重命名模板文件：{lf.original_name}")
            return redirect(_library_task_management_redirect_url(request))
        if action == "upload_template_folder_tree":
            folder_files = request.FILES.getlist("folder_files")
            zip_upload = request.FILES.get("folder_zip")
            stats = ingest_template_folder_upload(
                request.user,
                request_files=folder_files if folder_files else None,
                zip_file=zip_upload if zip_upload else None,
            )
            if stats.folders_created or stats.reports_created or stats.site_records_created:
                parts = []
                if stats.folders_created:
                    parts.append(f"{stats.folders_created} 个分类")
                if stats.reports_created:
                    parts.append(f"{stats.reports_created} 个报告模板")
                if stats.site_records_created:
                    parts.append(f"{stats.site_records_created} 个现场记录模板")
                if stats.files_uploaded:
                    parts.append(f"{stats.files_uploaded} 个文件")
                messages.success(request, "文件夹导入完成：" + "，".join(parts))
            for msg in stats.messages:
                messages.info(request, msg)
            for err in stats.errors:
                messages.warning(request, err)
            if (
                not stats.reports_created
                and not stats.site_records_created
                and stats.errors
            ):
                messages.error(request, "导入未完成，请检查目录结构")
            return redirect(reverse("library_task_management") + "?edit=1")
        if action == "upload_task_template_files":
            try:
                manage_tid = int(request.POST.get("manage_task_id", "") or 0)
            except ValueError:
                manage_tid = 0
            task_obj = LibraryTask.objects.filter(pk=manage_tid).first() if manage_tid else None
            if task_obj is None:
                messages.error(request, "请先选择任务模板")
            elif not library_user_may_edit_library_task(request.user, task_obj):
                messages.error(request, "无权编辑该任务模板")
            else:
                uploads = list(request.FILES.getlist("template_files"))
                if not uploads:
                    messages.error(request, "请选择 PDF 文件（可同时选择同名 JSON）")
                else:
                    from pathlib import Path as _Path

                    pdf_by_stem: dict[str, object] = {}
                    json_by_stem: dict[str, object] = {}
                    other_files: list = []
                    for uf in uploads:
                        nm = str(getattr(uf, "name", "") or "").strip()
                        if not nm:
                            continue
                        low = nm.lower()
                        stem = _Path(nm).stem
                        if low.endswith(".pdf"):
                            pdf_by_stem[stem] = uf
                        elif low.endswith(".json"):
                            json_by_stem[stem] = uf
                        else:
                            other_files.append(uf)
                    to_save: list = []
                    for stem, pdf_f in pdf_by_stem.items():
                        to_save.append(pdf_f)
                        jf = json_by_stem.pop(stem, None)
                        if jf is not None:
                            to_save.append(jf)
                    to_save.extend(json_by_stem.values())
                    to_save.extend(other_files)
                    created, skipped = save_library_binary_uploads(
                        request.user,
                        to_save,
                        LibraryFile.CATEGORY_TEMPLATE,
                    )
                    bound = 0
                    if created:
                        ids = [int(row["id"]) for row in created if row.get("id")]
                        task_obj.library_files.add(
                            *LibraryFile.objects.filter(pk__in=ids)
                        )
                        bound = len(ids)
                    for s in skipped:
                        fn = s.get("filename") or "(无名)"
                        messages.warning(request, f"跳过 {fn}：{s.get('reason', '')}")
                    if bound:
                        messages.success(
                            request,
                            f"已上传并绑定 {bound} 个文件到「{task_obj.code}」",
                        )
                    elif created:
                        messages.success(request, "文件已上传，请在下方向模板勾选绑定")
                    elif skipped:
                        messages.error(request, "未成功上传任何文件")
            return redirect(_library_task_management_redirect_url(request))
        if action == "create_report_task":
            name = request.POST.get("name", "").strip()
            code_in = request.POST.get("code", "").strip()
            folder_id_raw = (request.POST.get("task_folder_id") or "").strip()
            task_folder = None
            if folder_id_raw and folder_id_raw != UNCATEGORIZED_KEY:
                try:
                    task_folder = LibraryTaskFolder.objects.filter(
                        pk=int(folder_id_raw), is_active=True
                    ).first()
                except ValueError:
                    task_folder = None
            if not name:
                messages.error(request, "报告模板名称不能为空")
            else:
                code = _unique_library_task_code(name, code_in)
                row = LibraryTask.objects.create(
                    code=code,
                    name=name,
                    output_target=LibraryTask.OUTPUT_REPORT,
                    task_folder=task_folder,
                    created_by=request.user,
                )
                register_tour_task(request, row.pk)
                messages.success(request, f"已创建报告模板 {row.code}")
                folder_rows = list(task_folders_active_queryset())
                _, folder_by_id = index_task_folders(folder_rows)
                fl_path = fl_path_for_task(
                    row,
                    {row.pk: row},
                    folder_by_id,
                    has_report_source_relation=_librarytask_has_report_source_relation(),
                )
                return redirect(
                    reverse("library_task_management")
                    + f"?fl_path={quote(fl_path)}&manage_task={row.pk}&edit=1"
                )
            return redirect(_library_task_management_redirect_url(request))
        if action == "create_site_record_task":
            name = request.POST.get("name", "").strip()
            code_in = request.POST.get("code", "").strip()
            try:
                report_id = int(request.POST.get("report_task_id", "") or 0)
            except ValueError:
                report_id = 0
            report = LibraryTask.objects.filter(
                pk=report_id, output_target=LibraryTask.OUTPUT_REPORT
            ).first()
            if report is None:
                messages.error(request, "请先选择有效的报告模板")
            elif not name:
                messages.error(request, "现场记录模板名称不能为空")
            else:
                code = _unique_library_task_code(name, code_in)
                row = LibraryTask.objects.create(
                    code=code,
                    name=name,
                    output_target=LibraryTask.OUTPUT_SITE_RECORD,
                    created_by=request.user,
                )
                if _librarytask_has_report_source_relation():
                    report.report_source_tasks.add(row)
                register_tour_task(request, row.pk)
                messages.success(request, f"已创建现场记录模板 {row.code}，并关联到报告「{report.name}」")
                folder_rows = list(task_folders_active_queryset())
                task_by_id = {row.pk: row, report.pk: report}
                _, folder_by_id = index_task_folders(folder_rows)
                fl_path = fl_path_for_task(
                    row,
                    task_by_id,
                    folder_by_id,
                    has_report_source_relation=_librarytask_has_report_source_relation(),
                )
                return redirect(
                    reverse("library_task_management")
                    + f"?fl_path={quote(fl_path)}&manage_task={row.pk}&edit=1"
                )
            return redirect(_library_task_management_redirect_url(request))
        if action == "create_task":
            name = request.POST.get("name", "").strip()
            code_in = request.POST.get("code", "").strip()
            output_target = (request.POST.get("output_target") or LibraryTask.OUTPUT_SITE_RECORD).strip()
            if output_target not in {LibraryTask.OUTPUT_SITE_RECORD, LibraryTask.OUTPUT_REPORT}:
                output_target = LibraryTask.OUTPUT_SITE_RECORD
            if not name:
                messages.error(request, "任务模板名称不能为空")
            else:
                code = _unique_library_task_code(name, code_in)
                row = LibraryTask.objects.create(
                    code=code,
                    name=name,
                    output_target=output_target,
                    created_by=request.user,
                )
                register_tour_task(request, row.pk)
                messages.success(
                    request,
                    f"已创建任务模板 {row.code}（输出到：{row.get_output_target_display()}）。"
                    f"可在右侧「模板与仪器」中维护文件与仪器绑定。",
                )
                _crp = (request.POST.get("return_project") or "").strip()
                _qs = f"?manage_task={row.pk}"
                if _crp:
                    try:
                        int(_crp)
                        _qs += f"&return_project={_crp}"
                    except ValueError:
                        pass
                return redirect(reverse("library_task_management") + _qs)
        elif action == "update_task_output_target":
            try:
                tid = int(request.POST.get("manage_task_id", "") or request.POST.get("task_id", "") or 0)
            except ValueError:
                tid = 0
            output_target = (request.POST.get("output_target") or LibraryTask.OUTPUT_SITE_RECORD).strip()
            if output_target not in {LibraryTask.OUTPUT_SITE_RECORD, LibraryTask.OUTPUT_REPORT}:
                output_target = LibraryTask.OUTPUT_SITE_RECORD
            t_obj = LibraryTask.objects.filter(pk=tid).first() if tid else None
            if t_obj is None:
                messages.error(request, "任务模板不存在或无效")
            elif not library_user_may_edit_library_task(request.user, t_obj):
                messages.error(request, "无权修改由他人创建的任务模板")
            else:
                t_obj.output_target = output_target
                t_obj.save(update_fields=["output_target", "updated_at"])
                if _librarytask_has_report_source_relation() and output_target != LibraryTask.OUTPUT_REPORT:
                    t_obj.report_source_tasks.clear()
                messages.success(
                    request,
                    f"已更新任务模板「{t_obj.code}」的 PDF 输出目标为：{t_obj.get_output_target_display()}。",
                )
            return redirect(_library_task_management_redirect_url(request))
        elif action == "unbind_task_template_file":
            try:
                tid = int(request.POST.get("manage_task_id", "") or 0)
            except ValueError:
                tid = 0
            try:
                file_id = int(request.POST.get("file_id", "") or 0)
            except ValueError:
                file_id = 0
            include_paired = (request.POST.get("include_paired") or "1").strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            task_obj = LibraryTask.objects.filter(pk=tid).first() if tid else None
            if task_obj is None:
                messages.error(request, "请选择有效任务模板")
            elif file_id <= 0:
                messages.error(request, "无效的文件")
            else:
                from apps.core.library_task_template_binding_service import (
                    unbind_template_files_from_task,
                )

                result = unbind_template_files_from_task(
                    task_obj,
                    file_id,
                    user=request.user,
                    include_paired=include_paired,
                )
                if result.get("ok"):
                    messages.success(request, result.get("message") or "已解除绑定")
                else:
                    messages.error(request, result.get("error") or "解除绑定失败")
            return redirect(_library_task_management_redirect_url(request))
        elif action == "delete_task":
            try:
                tid = int(request.POST.get("task_id", "") or 0)
            except ValueError:
                tid = 0
            t_obj = LibraryTask.objects.filter(pk=tid).first()
            if t_obj is None:
                messages.error(request, "任务模板不存在")
            elif not library_user_may_edit_library_task(request.user, t_obj):
                messages.error(request, "无权删除由他人创建的任务模板")
            else:
                label = f"{t_obj.code} · {t_obj.name}"
                t_obj.delete()
                messages.success(request, f"已删除任务模板：{label}")
            return redirect(_library_task_management_redirect_url(request))
        elif action == "restore_task_template_history":
            try:
                hid = int(request.POST.get("history_id", "") or 0)
            except ValueError:
                hid = 0
            from apps.core.library_task_template_binding_service import (
                restore_task_template_from_history,
            )
            from apps.core.models import LibraryTaskTemplateBindingHistory

            hist = (
                LibraryTaskTemplateBindingHistory.objects.select_related("library_task")
                .filter(pk=hid)
                .first()
            )
            if hist is None:
                messages.error(request, "历史记录不存在")
            elif not library_user_may_edit_library_task(request.user, hist.library_task):
                messages.error(request, "无权维护该任务模板")
            else:
                result = restore_task_template_from_history(
                    history_id=hid, user=request.user
                )
                if result.get("ok"):
                    role_label = "PDF" if result.get("file_role") == "pdf" else "JSON"
                    messages.success(
                        request,
                        f"已恢复任务模板「{hist.library_task.code}」的 {role_label} 绑定。",
                    )
                else:
                    messages.error(request, result.get("error") or "恢复失败")
            return redirect(_library_task_management_redirect_url(request))
        elif action == "update_task_bound_instruments":
            from utils.task_bound_instruments import (
                count_bound_kind_slots,
                kind_spec_from_form_value,
                serialize_task_bound_kinds,
            )

            try:
                tid = int(request.POST.get("manage_task_id", "") or 0)
            except ValueError:
                tid = 0
            task_obj = LibraryTask.objects.filter(pk=tid).first() if tid else None

            def _post_kind_specs(name: str) -> list:
                specs = []
                seen: set[tuple[str, str]] = set()
                for raw in request.POST.getlist(name):
                    spec = kind_spec_from_form_value(raw)
                    if spec is None:
                        continue
                    k = spec.key()
                    if k in seen:
                        continue
                    seen.add(k)
                    specs.append(spec)
                return specs

            cleaned = serialize_task_bound_kinds(
                quality_control_kinds=_post_kind_specs("bound_kind_quality_control"),
                radiation_protection_kinds=_post_kind_specs("bound_kind_radiation_protection"),
            )
            if task_obj is None:
                messages.error(request, "任务模板不存在")
            elif not library_user_may_edit_library_task(request.user, task_obj):
                messages.error(request, "无权编辑由他人创建的任务模板")
            elif task_obj.output_target == LibraryTask.OUTPUT_REPORT:
                messages.error(request, "报告模板不单独绑定仪器，请在各现场记录模板中维护仪器种类")
            else:
                task_obj.bound_instrument_ids = cleaned
                task_obj.save(update_fields=["bound_instrument_ids", "updated_at"])
                from utils.task_bound_instruments import normalize_task_bound_kinds

                kinds_norm = normalize_task_bound_kinds(cleaned)
                qc_n, rp_n = count_bound_kind_slots(kinds_norm)
                messages.success(
                    request,
                    f"已保存任务模板「{task_obj.code}」的仪器种类：质控 {qc_n} 种、防护 {rp_n} 种（具体编号在人员派工时分配）。",
                )
            return redirect(_library_task_management_redirect_url(request))
        else:
            messages.error(request, "未知操作")
        return redirect(_library_task_management_redirect_url(request))

    tasks = LibraryTask.objects.prefetch_related(
        "library_files", "report_source_tasks", "task_folder"
    ).order_by("code")
    if can_assign_tasks and library_user_is_template_editor(request.user):
        tasks = tasks.filter(created_by=request.user)
    elif not can_assign_tasks:
        _assign_ids = list(
            LibraryTaskAssignment.objects.filter(assignee=request.user).values_list(
                "library_task_id", flat=True
            )
        )
        if can_participant_task_library and can_write_task_templates:
            tasks = tasks.filter(Q(pk__in=_assign_ids) | Q(created_by=request.user))
        elif can_participant_task_library:
            tasks = tasks.filter(pk__in=_assign_ids)
        elif can_write_task_templates:
            tasks = tasks.filter(created_by=request.user)

    tasks_list = list(tasks)
    fl_path = (request.GET.get("fl_path") or "").strip()
    mt_from_fl_path: int | None = None
    if fl_path:
        for part in fl_path.split("/"):
            if part.startswith("t-"):
                try:
                    mt_from_fl_path = int(part[2:])
                except ValueError:
                    mt_from_fl_path = None
                break

    manage_task = None
    manage_task_id_val = None
    # 任务模板仅维护「模板」分类；OCR/JSON/附件/检测提交等在「项目工作台」中按项目关联。
    template_visible_tabs = {"template"}
    task_mgmt_tab = _normalize_library_tab(request.GET.get("task_tab", "template"))
    if task_mgmt_tab not in template_visible_tabs:
        task_mgmt_tab = "template"
    task_mgmt_cat = _library_category_for_tab(task_mgmt_tab)
    task_linked_files = []
    task_linked_file_pairs: list = []
    export_project_id_val = ""
    task_library_edit = False
    task_edit_mode = False
    show_new_task_form = False
    task_library_hide_pdf_export_tools = library_user_has_party_a_demo_restrictions(request.user)
    task_show_instrument_binding = False
    if can_manage_in_task_library:
        task_library_edit = task_library_edit_mode(request)
        nt_raw = (request.GET.get("new_task") or "").strip().lower()
        show_new_task_form = nt_raw in ("1", "true", "yes", "on") and task_library_edit

    if can_access_task_library:
        mt_raw = request.GET.get("manage_task", "").strip()
        if not mt_raw and mt_from_fl_path:
            mt_raw = str(mt_from_fl_path)
        if mt_raw:
            try:
                mid = int(mt_raw)
                manage_task = LibraryTask.objects.prefetch_related(
                    "report_source_tasks", "library_files"
                ).filter(pk=mid).first()
                if manage_task and not library_user_may_access_assigned_library_task(request.user, manage_task):
                    messages.warning(request, "无权查看此任务模板（需具备模板维护权限或已被分配该检测任务）")
                    _fb = (request.GET.get("return_project") or "").strip()
                    _u = reverse("library_task_management")
                    if _fb:
                        try:
                            int(_fb)
                            _u += f"?return_project={_fb}"
                        except ValueError:
                            pass
                    return redirect(_u)
                if manage_task:
                    manage_task_id_val = mid
                    export_project_raw = (request.GET.get("export_project_id") or "").strip()
                    if export_project_raw:
                        try:
                            export_project_id_val = str(int(export_project_raw))
                        except ValueError:
                            export_project_id_val = ""
                    task_linked_files = [
                        f
                        for f in manage_task.library_files.filter(
                            category=LibraryFile.CATEGORY_TEMPLATE
                        )
                        .select_related("created_by")
                        .order_by("original_name")
                        if library_file_access_allowed(request.user, f)
                    ]
                    linked_ids = {int(f.pk) for f in task_linked_files}
                    task_linked_file_pairs = group_template_files_by_stem(
                        task_linked_files, linked_ids
                    )
            except ValueError:
                pass
        task_edit_mode = bool(task_library_edit and manage_task)

    task_template_file_count = 0
    if manage_task:
        task_template_file_count = manage_task.library_files.filter(
            category=LibraryFile.CATEGORY_TEMPLATE
        ).count()

    return_project_id = ""
    _rp = (request.GET.get("return_project") or request.GET.get("project_id") or "").strip()
    try:
        if _rp:
            _rpi = int(_rp)
            if LibraryProject.objects.filter(pk=_rpi).exists():
                return_project_id = str(_rpi)
    except ValueError:
        pass
    workbench_back_url = reverse("library_projects")
    if return_project_id:
        workbench_back_url += f"?project_id={return_project_id}&tab=commission"

    if manage_task:
        task_show_instrument_binding = manage_task.output_target == LibraryTask.OUTPUT_SITE_RECORD

    instrument_catalog_for_task: list = []
    task_bound_qc_kind_values: set = set()
    task_bound_rp_kind_values: set = set()
    instrument_kind_groups: list = []
    if manage_task and task_show_instrument_binding:
        from utils.task_bound_instruments import (
            KIND_SEP,
            build_instrument_kind_groups,
            normalize_task_bound_kinds,
        )

        instrument_catalog_for_task = list(
            InstrumentCatalog.objects.filter(is_active=True).order_by("code", "id")
        )
        instrument_kind_groups = build_instrument_kind_groups(instrument_catalog_for_task)
        bound_kinds = normalize_task_bound_kinds(
            getattr(manage_task, "bound_instrument_ids", None) or []
        )
        for spec in bound_kinds.get("qualityControl") or []:
            task_bound_qc_kind_values.add(f"{spec.name}{KIND_SEP}{spec.model}")
        for spec in bound_kinds.get("radiationProtection") or []:
            task_bound_rp_kind_values.add(f"{spec.name}{KIND_SEP}{spec.model}")

    has_report_source_task_relation = _librarytask_has_report_source_relation()

    report_linked_site_tasks: list = []
    if (
        manage_task
        and manage_task.output_target == LibraryTask.OUTPUT_REPORT
        and _librarytask_has_report_source_relation()
    ):
        report_linked_site_tasks = list(
            manage_task.report_source_tasks.filter(
                output_target=LibraryTask.OUTPUT_SITE_RECORD
            ).order_by("code", "id")
        )

    task_primary_json_file = None
    task_auxiliary_json_files: list = []
    task_auxiliary_json_ids: set[int] = set()
    if manage_task:
        from apps.core.library_task_template_binding_service import (
            get_task_template_pair,
            is_auxiliary_template_json_file,
        )

        _pdf_lf, _json_lf = get_task_template_pair(manage_task)
        task_primary_json_file = _json_lf
        for lf in manage_task.library_files.filter(
            category=LibraryFile.CATEGORY_TEMPLATE
        ).order_by("-created_at", "-id"):
            if not (lf.original_name or "").lower().endswith(".json"):
                continue
            if is_auxiliary_template_json_file(lf):
                task_auxiliary_json_files.append(lf)
                task_auxiliary_json_ids.add(int(lf.pk))

    task_template_binding_history: list = []
    if manage_task:
        from apps.core.models import LibraryTaskTemplateBindingHistory

        task_template_binding_history = list(
            LibraryTaskTemplateBindingHistory.objects.filter(library_task=manage_task)
            .select_related("library_file", "replaced_by_file", "replaced_by")
            .order_by("-replaced_at", "-id")[:80]
        )

    parent_report_task_for_site: LibraryTask | None = None
    if manage_task and manage_task.output_target == LibraryTask.OUTPUT_SITE_RECORD:
        parent_report_task_for_site = (
            manage_task.report_target_tasks.filter(output_target=LibraryTask.OUTPUT_REPORT)
            .select_related("task_folder")
            .first()
        )

    task_folder_rows = list(task_folders_active_queryset())
    folder_by_id, children_by_parent = index_task_folders(task_folder_rows)
    task_by_id_map = {t.pk: t for t in tasks_list}
    if manage_task and not fl_path:
        fl_path = fl_path_for_task(
            manage_task,
            task_by_id_map,
            folder_by_id,
            has_report_source_relation=_librarytask_has_report_source_relation(),
        )
    folder_tree, folder_breadcrumbs, folder_entries, _fl_task_id = build_task_category_explorer(
        tasks_list,
        task_folder_rows,
        fl_path=fl_path,
        manage_task_id=manage_task_id_val,
        has_report_source_relation=_librarytask_has_report_source_relation(),
    )
    if task_library_edit:
        append_edit_mode_to_explorer_links(folder_tree, folder_entries, edit=True)
    explorer_current_folder_id = ""
    explorer_current_report_id = ""
    segs_fl = _parse_task_fl_segments(fl_path) if fl_path else []
    for key, val in segs_fl:
        if key == "f":
            explorer_current_folder_id = val
        elif key == "r":
            explorer_current_report_id = val
    show_new_report_form = task_library_edit and (request.GET.get("new_report") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    show_new_site_form = task_library_edit and (request.GET.get("new_site") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    task_explorer_base = reverse("library_task_management")
    rp_q = (request.GET.get("return_project") or "").strip()
    base_q: list[str] = []
    if rp_q:
        base_q.append(f"return_project={quote(rp_q)}")
    if task_library_edit:
        base_q.append("edit=1")
    if base_q:
        task_explorer_base += "?" + "&".join(base_q)
    explorer_nav_suffix = "&edit=1" if task_library_edit else ""

    return render(
        request,
        "core/library_task_management.html",
        {
            "can_manage_templates": can_manage_in_task_library,
            "can_assign": can_assign_tasks,
            "can_participant_task_library": can_participant_task_library,
            "projects": projects,
            "tasks": tasks,
            "manage_task": manage_task,
            "manage_task_id_val": manage_task_id_val,
            "task_mgmt_tab": task_mgmt_tab or "ocr",
            "task_linked_file_pairs": task_linked_file_pairs,
            "explorer_nav_suffix": explorer_nav_suffix,
            "task_library_edit": task_library_edit,
            "htmlpdf_editor_url": htmlpdf_editor_page_url(
                manage_task_id=manage_task_id_val,
                fl_path=fl_path,
            ),
            "task_library_edit_url": task_library_page_url(
                fl_path,
                manage_task_id=manage_task_id_val,
                edit=True,
                return_project=return_project_id,
                export_project_id=export_project_id_val,
            ),
            "task_library_readonly_url": task_library_page_url(
                fl_path,
                manage_task_id=manage_task_id_val,
                edit=False,
                return_project=return_project_id,
                export_project_id=export_project_id_val,
            ),
            "task_linked_files": task_linked_files,
            "task_primary_json_file": task_primary_json_file,
            "task_auxiliary_json_files": task_auxiliary_json_files,
            "task_auxiliary_json_ids": task_auxiliary_json_ids,
            "task_template_binding_history": task_template_binding_history,
            "task_template_file_count": task_template_file_count,
            "export_project_id_val": export_project_id_val,
            "task_edit_mode": task_edit_mode,
            "show_new_task_form": show_new_task_form,
            "instrument_catalog_for_task": instrument_catalog_for_task,
            "task_bound_qc_kind_values": task_bound_qc_kind_values,
            "task_bound_rp_kind_values": task_bound_rp_kind_values,
            "instrument_kind_groups": instrument_kind_groups,
            "workbench_back_url": workbench_back_url,
            "return_project_id": return_project_id,
            "file_library_tabs": [
                {"key": "template", "label": "模板"},
            ],
            "has_report_source_task_relation": has_report_source_task_relation,
            "report_linked_site_tasks": report_linked_site_tasks,
            "parent_report_task_for_site": parent_report_task_for_site,
            "fl_path": fl_path,
            "folder_tree_nodes": folder_tree,
            "folder_breadcrumbs": folder_breadcrumbs,
            "folder_entries": folder_entries,
            "task_explorer_base": task_explorer_base,
            "tree_root_label": "任务模板",
            "task_folder_rows": task_folder_rows,
            "task_children_by_parent": children_by_parent,
            "explorer_current_folder_id": explorer_current_folder_id,
            "explorer_current_report_id": explorer_current_report_id,
            "show_new_report_form": show_new_report_form,
            "show_new_site_form": show_new_site_form,
            "uncategorized_folder_key": UNCATEGORIZED_KEY,
            "task_explorer_dnd_enabled": can_manage_in_task_library and task_library_edit,
            "task_explorer_dnd_post_url": reverse("library_task_management"),
            "task_library_hide_pdf_export_tools": task_library_hide_pdf_export_tools,
            "task_show_instrument_binding": task_show_instrument_binding,
        },
    )


def _hospital_info_edit_mode(request) -> bool:
    v = (request.GET.get("edit") or request.POST.get("edit") or "").strip().lower()
    return v in ("1", "true", "yes")


def _hospital_info_page_url(fl_path: str = "", *, edit: bool = False) -> str:
    base = reverse("hospital_info_manage")
    parts: list[str] = []
    if fl_path:
        parts.append(f"fl_path={quote(fl_path)}")
    if edit:
        parts.append("edit=1")
    if not parts:
        return base
    return base + "?" + "&".join(parts)


@login_required
def hospital_info_manage(request):
    gx = _require_perm(request, "perm_file_library")
    if gx:
        return gx
    if not library_user_may_access_hospital_info_nav(request.user):
        messages.error(request, "当前角色无权访问医院信息管理")
        return redirect(reverse("dashboard"))

    fl_path = (request.GET.get("fl_path") or request.POST.get("fl_path") or "").strip()
    base_url = reverse("hospital_info_manage")
    hospital_info_edit = _hospital_info_edit_mode(request)

    def _redir(*, edit: bool | None = None) -> HttpResponse:
        use_edit = hospital_info_edit if edit is None else edit
        return redirect(_hospital_info_page_url(fl_path, edit=use_edit))

    if request.method == "POST":
        if not hospital_info_edit:
            messages.warning(request, "当前为浏览模式，请点击「编辑」后再修改")
            return _redir(edit=False)
        action = (request.POST.get("action") or "").strip()

        if action == "create_commission_organization":
            level = (request.POST.get("org_level") or CommissionOrganization.LEVEL_HOSPITAL).strip()
            nm = request.POST.get("org_name", "").strip()
            notes = request.POST.get("org_notes", "").strip()
            parent = None
            parent_raw = (request.POST.get("parent_id") or "").strip()
            if parent_raw:
                try:
                    parent = CommissionOrganization.objects.filter(
                        pk=int(parent_raw), is_active=True
                    ).first()
                except ValueError:
                    parent = None
            org, err = create_commission_org_node(
                request.user, level=level, name=nm, parent=parent, notes=notes
            )
            if err:
                messages.error(request, err)
                return _redir(edit=True)
            messages.success(request, f"已添加{org.level_label}：{org.full_display_name}")
            fl_path = org.folder_path()
            return _redir(edit=True)

        if action == "delete_commission_organization":
            try:
                oid = int(request.POST.get("org_id", "") or 0)
            except ValueError:
                oid = 0
            org = CommissionOrganization.objects.filter(pk=oid, is_active=True).first()
            if org is None:
                messages.error(request, "委托单位不存在")
                return _redir(edit=True)
            parent_path = org.parent.folder_path() if org.parent_id else ""
            err = deactivate_commission_org_node(org)
            if err:
                messages.error(request, err)
                fl_path = org.folder_path()
                return _redir(edit=True)
            messages.success(request, f"已删除{org.level_label}：{org.name}")
            fl_path = parent_path
            return _redir(edit=False)

        if action == "save_org_hospital_info":
            try:
                oid = int(request.POST.get("org_id", "") or 0)
            except ValueError:
                oid = 0
            org = (
                CommissionOrganization.objects.filter(pk=oid, is_active=True)
                .select_related("merged_report_file")
                .first()
            )
            if org is None:
                messages.error(request, "委托单位不存在")
                return _redir(edit=True)
            only_merged = (request.POST.get("save_scope") or "").strip() == "merged_report"
            merged_fid = None
            if only_merged or "merged_report_file_id" in request.POST:
                merged_fid = parse_report_file_id_for_org(
                    org, request.user, request.POST.get("merged_report_file_id", "")
                )
                raw_merged = (request.POST.get("merged_report_file_id") or "").strip()
                if raw_merged and merged_fid is None:
                    messages.error(request, "所选合并报告无效或无权访问")
                    return _redir(edit=True)
            err = save_org_hospital_info(
                org,
                name=request.POST.get("org_name", ""),
                notes=request.POST.get("org_notes", ""),
                address=request.POST.get("org_address", ""),
                introduction=request.POST.get("org_introduction", ""),
                merged_report_file_id=merged_fid,
                update_merged_report=only_merged or "merged_report_file_id" in request.POST,
            )
            if err:
                messages.error(request, err)
                return _redir(edit=True)
            messages.success(
                request,
                "已保存合并报告绑定" if only_merged else "已保存机构信息",
            )
            fl_path = org.folder_path()
            return _redir(edit=False)

        if action == "save_org_contact":
            try:
                oid = int(request.POST.get("org_id", "") or 0)
            except ValueError:
                oid = 0
            org = CommissionOrganization.objects.filter(pk=oid, is_active=True).first()
            if org is None:
                messages.error(request, "委托单位不存在")
                return _redir(edit=True)
            cid_raw = (request.POST.get("contact_id") or "").strip()
            cid = int(cid_raw) if cid_raw else None
            row, err = upsert_org_contact(
                org,
                contact_id=cid,
                name=request.POST.get("contact_name", ""),
                phone=request.POST.get("contact_phone", ""),
                title=request.POST.get("contact_title", ""),
            )
            if err:
                messages.error(request, err)
                return _redir(edit=True)
            messages.success(request, f"已保存联系人：{row.name}")
            fl_path = org.folder_path()
            return _redir(edit=False)

        if action == "delete_org_contact":
            try:
                cid = int(request.POST.get("contact_id", "") or 0)
            except ValueError:
                cid = 0
            row = CommissionOrgContact.objects.filter(pk=cid).select_related("organization").first()
            if row is None:
                messages.error(request, "联系人不存在")
            else:
                fl_path = row.organization.folder_path()
                row.delete()
                messages.success(request, "已删除联系人")
            return _redir(edit=False)

        if action == "save_equipment":
            org_ctx = None
            try:
                ctx_oid = int(request.POST.get("context_org_id", "") or 0)
            except ValueError:
                ctx_oid = 0
            if ctx_oid:
                org_ctx = CommissionOrganization.objects.filter(pk=ctx_oid, is_active=True).first()
            if org_ctx is None and fl_path:
                _rows = list(commission_orgs_active_queryset())
                _by_id, _, _ = index_commission_orgs(_rows)
                _oid = resolve_org_id_from_segments(
                    parse_commission_path_segments(fl_path), _by_id
                )
                if _oid:
                    org_ctx = CommissionOrganization.objects.filter(pk=_oid, is_active=True).first()
            if org_ctx is None:
                messages.error(request, "请先选择机构")
                return _redir(edit=True)
            dept, dept_err = resolve_department_for_equipment_save(
                org_ctx, request.POST.get("department_id", "")
            )
            if dept_err:
                messages.error(request, dept_err)
                return _redir(edit=True)
            eid_raw = (request.POST.get("equipment_id") or "").strip()
            eid = int(eid_raw) if eid_raw else None
            raw_report_f = (request.POST.get("report_file_id") or "").strip()
            parsed_report_f = parse_report_file_id_for_org(org_ctx, request.user, raw_report_f)
            if raw_report_f and parsed_report_f is None:
                messages.error(request, "所选设备报告无效或无权访问")
                return _redir(edit=True)
            raw_task = (request.POST.get("report_task_id") or "").strip()
            parsed_task = parse_report_task_id(raw_task) if raw_task else None
            if raw_task and parsed_task is None:
                messages.error(request, "所选检测任务模板无效")
                return _redir(edit=True)
            row, err = upsert_department_equipment(
                dept,
                equipment_id=eid,
                name=request.POST.get("equipment_name", ""),
                model=request.POST.get("equipment_model", ""),
                serial_no=request.POST.get("equipment_serial_no", ""),
                manufacturer=request.POST.get("equipment_manufacturer", ""),
                location=request.POST.get("equipment_location", ""),
                notes=request.POST.get("equipment_notes", ""),
                report_file_id=parsed_report_f,
                report_task_id=parsed_task,
                user=request.user,
                bind_org_for_report=org_ctx if org_ctx.pk != dept.pk else None,
            )
            if err:
                messages.error(request, err)
                return _redir(edit=True)
            messages.success(request, f"已保存设备：{row.name}")
            fl_path = org_ctx.folder_path()
            return _redir(edit=False)

        if action == "bind_equipment_to_project":
            try:
                eid = int(request.POST.get("equipment_id", "") or 0)
                pid = int(request.POST.get("project_id", "") or 0)
            except ValueError:
                eid, pid = 0, 0
            eq = (
                CommissionOrgEquipment.objects.filter(pk=eid, is_active=True)
                .select_related("department", "report_task")
                .first()
            )
            proj = LibraryProject.objects.filter(pk=pid, is_active=True).first()
            if eq is None or proj is None:
                messages.error(request, "设备或项目无效")
                return _redir(edit=True)
            added, err = bind_equipment_tasks_to_project(eq, proj, request.user)
            if err:
                messages.error(request, err)
                return _redir(edit=True)
            if added:
                messages.success(request, f"已向项目「{proj.name}」关联 {added} 个检测任务")
            else:
                messages.info(request, f"项目「{proj.name}」已包含该设备所需的检测任务")
            fl_path = (request.POST.get("fl_path") or "").strip() or eq.department.folder_path()
            return _redir(edit=True)

        if action == "delete_equipment":
            try:
                eid = int(request.POST.get("equipment_id", "") or 0)
            except ValueError:
                eid = 0
            row = CommissionOrgEquipment.objects.filter(pk=eid).select_related("department").first()
            if row is None:
                messages.error(request, "设备不存在")
            else:
                fl_path = row.department.folder_path()
                row.delete()
                messages.success(request, "已删除设备")
            return _redir(edit=False)

        if action == "sync_equipment_from_report":
            try:
                oid = int(request.POST.get("org_id", "") or 0)
                eid = int(request.POST.get("equipment_id", "") or 0)
            except ValueError:
                oid, eid = 0, 0
            dept = CommissionOrganization.objects.filter(
                pk=oid, is_active=True, level=CommissionOrganization.LEVEL_DEPARTMENT
            ).first()
            row = (
                CommissionOrgEquipment.objects.filter(pk=eid, department_id=oid)
                .select_related("report_file")
                .first()
                if eid
                else None
            )
            fl_path = (request.POST.get("fl_path") or "").strip()
            if dept is None:
                messages.error(request, "科室不存在")
                return _redir(edit=True)
            raw_report_f = (request.POST.get("report_file_id") or "").strip()
            if not raw_report_f and row and row.report_file_id:
                raw_report_f = str(row.report_file_id)
            parsed_fid = parse_report_file_id_for_org(dept, request.user, raw_report_f)
            if not parsed_fid:
                messages.error(request, "请选择有效的报告文件")
                fl_path = dept.folder_path()
                return _redir(edit=True)
            lf = LibraryFile.objects.filter(pk=parsed_fid).first()
            fields, err = equipment_fields_from_report_file(lf) if lf else ({}, "报告不存在")
            if err:
                messages.error(request, err)
                fl_path = dept.folder_path()
                return _redir(edit=True)
            if row is None:
                messages.info(
                    request,
                    "新建设备请先填写名称并保存；保存时若已选择报告，系统会自动从检测提交补全空白字段。",
                )
            else:
                row.name = fields.get("name") or row.name
                row.model = fields.get("model") or row.model
                row.serial_no = fields.get("serial_no") or row.serial_no
                row.manufacturer = fields.get("manufacturer") or row.manufacturer
                row.location = fields.get("location") or row.location
                row.report_file_id = parsed_fid
                row.save(
                    update_fields=[
                        "name",
                        "model",
                        "serial_no",
                        "manufacturer",
                        "location",
                        "report_file_id",
                        "updated_at",
                    ]
                )
                messages.success(request, f"已从检测报告更新设备「{row.name}」信息")
            fl_path = dept.folder_path()
            return _redir(edit=False)

        messages.error(request, "未知操作")
        return _redir(edit=True)

    commission_org_rows = list(commission_orgs_active_queryset())
    org_by_id, _, _ = index_commission_orgs(commission_org_rows)
    oid = resolve_org_id_from_segments(parse_commission_path_segments(fl_path), org_by_id)
    explorer_org = (
        CommissionOrganization.objects.filter(pk=oid, is_active=True)
        .select_related("merged_report_file", "parent", "parent__parent")
        .first()
        if oid
        else None
    )
    explorer_org_child_levels: list[tuple[str, str]] = []
    if explorer_org:
        for lv in explorer_org.allowed_child_levels():
            label = dict(CommissionOrganization.LEVEL_CHOICES).get(lv, lv)
            explorer_org_child_levels.append((lv, label))

    folder_tree_nodes = build_commission_org_nav_tree(
        commission_org_rows,
        [],
        co_path=fl_path,
    )
    report_file_options: list[dict] = []
    org_contacts: list[CommissionOrgContact] = []
    org_equipments: list[CommissionOrgEquipment] = []
    org_equipments_editable = False
    org_equipment_pick_department = False
    equipment_cards: list[dict] = []
    equipment_dept_picker: dict = {"mode": "none"}
    report_task_options: list[dict] = []
    project_options: list[dict] = []
    org_project_count = 0
    if explorer_org:
        org_contacts = list(
            explorer_org.contacts.filter(is_active=True).order_by("sort_order", "id")
        )
        org_project_count = projects_for_org_binding(explorer_org).count()
        for lf in report_files_for_org_binding(explorer_org)[:300]:
            if library_file_access_allowed(request.user, lf):
                report_file_options.append(
                    {"id": lf.pk, "label": report_file_option_label(lf)}
                )
        org_equipments = list(equipments_for_org_view(explorer_org))
        org_equipments_editable = hospital_info_edit
        org_equipment_pick_department = (
            hospital_info_edit
            and explorer_org.level != CommissionOrganization.LEVEL_DEPARTMENT
        )
        try:
            backfill_equipment_histories_from_reports(explorer_org)
        except Exception:
            pass
        equipment_cards = [
            {
                "equipment": eq,
                "status": equipment_display_status(eq),
                "can_bind_project": equipment_can_bind_to_project(eq),
            }
            for eq in org_equipments
        ]
        equipment_dept_picker = build_equipment_department_picker(explorer_org)
        report_task_options = [
            {"id": t.pk, "label": f"{t.code} · {t.name}"}
            for t in library_tasks_for_equipment_binding()[:200]
        ]
        project_options = [
            {"id": p.pk, "label": f"{p.code} · {p.name}"}
            for p in projects_for_org_binding(explorer_org)[:100]
        ]
    else:
        equipment_cards = []
        equipment_dept_picker = {"mode": "none"}
        report_task_options = []
        project_options = []
        org_equipment_pick_department = False

    hospital_info_explorer_base = base_url + ("?edit=1" if hospital_info_edit else "")

    from apps.core.library_folder_service import BreadcrumbItem

    folder_breadcrumbs: list[BreadcrumbItem] = [BreadcrumbItem("全部医院", "")]
    if explorer_org:
        for node in explorer_org.ancestors_chain():
            folder_breadcrumbs.append(BreadcrumbItem(node.name, node.folder_path()))

    return render(
        request,
        "core/hospital_info_manage.html",
        {
            "fl_path": fl_path,
            "hospital_info_base": base_url,
            "hospital_info_edit": hospital_info_edit,
            "hospital_info_explorer_base": hospital_info_explorer_base,
            "hospital_info_url_read": _hospital_info_page_url(fl_path, edit=False),
            "hospital_info_url_edit": _hospital_info_page_url(fl_path, edit=True),
            "folder_tree_nodes": folder_tree_nodes,
            "folder_breadcrumbs": folder_breadcrumbs,
            "explorer_org": explorer_org,
            "explorer_org_child_levels": explorer_org_child_levels,
            "org_contacts": org_contacts,
            "org_equipments": org_equipments,
            "equipment_cards": equipment_cards,
            "org_equipments_editable": org_equipments_editable,
            "org_equipment_pick_department": org_equipment_pick_department,
            "equipment_dept_picker": equipment_dept_picker,
            "report_task_options": report_task_options,
            "project_options": project_options,
            "report_file_options": report_file_options,
            "org_project_count": org_project_count,
        },
    )


def _hospital_info_equipment_access(
    request, equipment_id: int
) -> tuple[CommissionOrgEquipment | None, CommissionOrganization | None, JsonResponse | None]:
    if not library_user_may_access_hospital_info_nav(request.user):
        return None, None, JsonResponse({"ok": False, "message": "无权访问"}, status=403)
    eq = (
        CommissionOrgEquipment.objects.filter(pk=equipment_id, is_active=True)
        .select_related("department", "department__parent", "report_file", "report_task")
        .first()
    )
    if eq is None:
        return None, None, JsonResponse({"ok": False, "message": "设备不存在"}, status=404)
    root = eq.department.hospital_root()
    org_ctx = root
    return eq, org_ctx, None


@login_required
def hospital_info_equipment_api(request):
    """设备表单数据 / 检测历史 / 上次提交快照（JSON）。"""
    gx = _require_perm(request, "perm_file_library")
    if gx:
        return gx
    if request.method != "GET":
        return JsonResponse({"ok": False, "message": "仅支持 GET"}, status=405)

    if (request.GET.get("previous") or "").strip() in ("1", "true", "yes"):
        try:
            eid = int(request.GET.get("equipment_id", "") or 0)
        except ValueError:
            eid = 0
        eq, _, err = _hospital_info_equipment_access(request, eid)
        if err is not None:
            return err
        payload = equipment_previous_submit_payload(eq)
        return JsonResponse({"ok": True, "previous": payload})

    eid_raw = (request.GET.get("equipment_id") or "").strip()
    if eid_raw:
        try:
            eid = int(eid_raw)
        except ValueError:
            return JsonResponse({"ok": False, "message": "设备 id 无效"}, status=400)
        eq, org_ctx, err = _hospital_info_equipment_access(request, eid)
        if err is not None:
            return err
        st = equipment_display_status(eq)
        campus_key = department_campus_picker_key(eq.department)
        return JsonResponse(
            {
                "ok": True,
                "equipment": {
                    "id": eq.pk,
                    "department_id": eq.department_id,
                    "campus_picker_key": campus_key,
                    "name": eq.name,
                    "model": eq.model,
                    "serial_no": eq.serial_no,
                    "manufacturer": eq.manufacturer,
                    "location": eq.location,
                    "notes": eq.notes,
                    "report_file_id": eq.report_file_id,
                    "report_task_id": eq.report_task_id,
                },
                "status": st,
                "history": equipment_history_rows(eq, request.user),
            }
        )

    fl_path = (request.GET.get("fl_path") or "").strip()
    org_ctx = None
    if fl_path:
        rows = list(commission_orgs_active_queryset())
        by_id, _, _ = index_commission_orgs(rows)
        oid = resolve_org_id_from_segments(parse_commission_path_segments(fl_path), by_id)
        if oid:
            org_ctx = CommissionOrganization.objects.filter(pk=oid, is_active=True).first()
    if org_ctx is None:
        return JsonResponse({"ok": False, "message": "请先选择机构"}, status=400)
    return JsonResponse(
        {
            "ok": True,
            "equipment": None,
            "department_picker": build_equipment_department_picker(org_ctx),
        }
    )


@login_required
def hospital_info_equipment_history_api(request):
    gx = _require_perm(request, "perm_file_library")
    if gx:
        return gx
    try:
        eid = int(request.GET.get("equipment_id", "") or 0)
    except ValueError:
        eid = 0
    eq, _, err = _hospital_info_equipment_access(request, eid)
    if err is not None:
        return err
    return JsonResponse(
        {
            "ok": True,
            "equipment_name": eq.name,
            "history": equipment_history_rows(eq, request.user),
            "previous_url": reverse("hospital_info_equipment_api")
            + f"?equipment_id={eq.pk}&previous=1",
        }
    )
