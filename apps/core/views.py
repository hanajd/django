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
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import Q
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
from apps.core.library_file_service import (
    attach_files_to_projects,
    attach_files_to_tasks,
    detach_files_from_projects,
    detach_files_from_tasks,
    library_file_download_response,
    parse_project_ids,
    safe_library_basename,
    save_library_binary_uploads,
)
from apps.core.library_access import (
    ROLE_DEFAULT_PERMS_BY_CODE,
    ROLE_PERMISSION_MATRIX,
    library_assigned_project_ids,
    library_file_access_allowed,
    library_file_write_blocked_by_project_revocation,
    library_scope_own_files_only,
    library_upload_blocked_revoked_projects,
    role_can_upload_library_category,
    role_has,
)
from apps.core.models import (
    InspectionCase,
    InspectionSubmission,
    InstrumentCatalog,
    LibraryFile,
    LibraryProject,
    LibraryProjectUserRevocation,
    LibraryTask,
    LibraryTaskAssignment,
    Menu,
    Role,
    UserProfile,
)
from utils.ollama_extract import generate_frontend_template_with_ollama
from utils.frontend_schema_rule_engine import build_frontend_schema_by_rules, normalize_field_text_by_underscore_rules

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
            return redirect('dashboard')
        else:
            messages.error(request, '用户名或密码错误')
    
    return render(request, 'login.html')


@login_required
def logout_view(request):
    """登出视图"""
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


@login_required
def database_device_list(request):
    """数据库管理：检测仪器台账。"""
    r = _require_perm(request, "perm_manage_users")
    if r:
        return r

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

    editing_device = None
    edit_id = (request.GET.get("edit") or "").strip()
    if edit_id:
        try:
            editing_device = InstrumentCatalog.objects.get(id=int(edit_id))
        except (ValueError, InstrumentCatalog.DoesNotExist):
            editing_device = None

    devices = InstrumentCatalog.objects.all()
    context = {
        "devices": devices,
        "editing_device": editing_device,
    }
    return render(request, "core/database_device_list.html", context)


@login_required
def user_list(request):
    """用户列表视图"""
    r = _require_perm(request, "perm_manage_users")
    if r:
        return r
    search = request.GET.get('search', '')
    page = request.GET.get('page', 1)
    
    # 搜索过滤
    users = User.objects.all()
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
            UserProfile.objects.create(
                user=user,
                phone=phone or None,
                department=department or None,
                position=position or None,
                role=role,
            )
        
        messages.success(request, '用户创建成功')
        return redirect('user_list')
    
    roles = Role.objects.all()
    context = {
        'roles': roles,
    }
    return render(request, 'core/user_form.html', context)


@login_required
def user_edit(request, user_id):
    """编辑用户视图"""
    r = _require_perm(request, "perm_manage_users")
    if r:
        return r
    user = get_object_or_404(User, id=user_id)
    profile = user.profile
    
    if request.method == 'POST':
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
        
        profile.save()
        
        messages.success(request, '用户更新成功')
        return redirect('user_list')
    
    roles = Role.objects.all()
    context = {
        "edit_user": user,
        "profile": profile,
        "roles": roles,
    }
    return render(request, "core/user_form.html", context)


@login_required
def user_delete(request, user_id):
    """删除用户视图"""
    r = _require_perm(request, "perm_manage_users")
    if r:
        return r
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
    roles = Role.objects.all()
    context = {
        'roles': roles,
    }
    return render(request, 'core/role_list.html', context)


@login_required
def role_create(request):
    """创建角色视图"""
    r = _require_perm(request, "perm_manage_roles")
    if r:
        return r
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
    
    context = {}
    return render(request, 'core/role_form.html', context)


@login_required
def role_edit(request, role_id):
    """编辑角色视图"""
    r = _require_perm(request, "perm_manage_roles")
    if r:
        return r
    role = get_object_or_404(Role, id=role_id)

    if request.method == "POST":
        role.name = request.POST.get("name", role.name)
        role.description = request.POST.get("description", role.description)
        if role.code == "super_admin":
            for k, v in ROLE_DEFAULT_PERMS_BY_CODE["super_admin"].items():
                setattr(role, k, v)
        else:
            for key, _, _ in ROLE_PERMISSION_MATRIX:
                setattr(role, key, request.POST.get(key) == "on")
        role.save()
        messages.success(request, "角色更新成功")
        return redirect("role_list")

    perm_rows = [
        {
            "field": key,
            "title": title,
            "help": help_text,
            "value": bool(getattr(role, key)),
        }
        for key, title, help_text in ROLE_PERMISSION_MATRIX
    ]
    context = {
        "role": role,
        "perm_rows": perm_rows,
    }
    return render(request, "core/role_form.html", context)


@login_required
def role_delete(request, role_id):
    """删除角色视图"""
    r = _require_perm(request, "perm_manage_roles")
    if r:
        return r
    role = get_object_or_404(Role, id=role_id)
    
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
    now = timezone.localtime()
    prefix = now.strftime("%Y%m")
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if now.month == 12:
        next_month_start = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        next_month_start = now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
    month_count = LibraryProject.objects.filter(created_at__gte=month_start, created_at__lt=next_month_start).count()
    seq = month_count + 1
    return f"{prefix}{seq:02d}"


@login_required
def library_projects(request):
    gx = _require_perm(request, "perm_file_library")
    if gx:
        return gx
    selected_raw = request.GET.get("project_id", "").strip()
    try:
        selected_project_id = int(selected_raw) if selected_raw else None
    except ValueError:
        selected_project_id = None
    selected_project = (
        LibraryProject.objects.filter(pk=selected_project_id).first() if selected_project_id else None
    )

    if request.method == "POST":
        action = request.POST.get("action", "").strip()
        if action == "create_project":
            if not role_has(request.user, "perm_assign_tasks"):
                messages.error(request, "当前角色无权创建项目")
                return redirect(reverse("library_projects"))
            code = request.POST.get("project_code", "").strip() or _gen_project_code()
            name = request.POST.get("project_name", "").strip()
            desc = request.POST.get("project_desc", "").strip()
            if not name:
                messages.error(request, "项目名称不能为空")
            elif LibraryProject.objects.filter(code=code).exists():
                messages.error(request, "项目编码已存在")
            else:
                row = LibraryProject.objects.create(
                    code=code,
                    name=name,
                    description=desc,
                    created_by=request.user,
                )
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
                        # 新建项目时若已选择任务，同步把任务已有文件挂到项目下。
                        all_file_ids = set()
                        for t in tasks:
                            all_file_ids.update(t.library_files.values_list("id", flat=True))
                        if all_file_ids:
                            attach_files_to_projects(sorted(all_file_ids), [row.pk], request.user)
                messages.success(request, f"已创建项目：{row.code} · {row.name}")
            return redirect(reverse("library_projects"))

        if action in ("bind_tasks", "unbind_tasks"):
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
            if not role_has(request.user, "perm_assign_tasks"):
                messages.error(request, "当前角色无权关联项目任务")
                return redirect(reverse("library_projects") + f"?project_id={proj.pk}")
            raw_ids = []
            for x in request.POST.getlist("task_ids"):
                try:
                    raw_ids.append(int(x))
                except (TypeError, ValueError):
                    continue
            raw_ids = list(dict.fromkeys([i for i in raw_ids if i > 0]))
            if not raw_ids:
                messages.error(request, "请至少勾选一个任务")
            else:
                tasks = list(LibraryTask.objects.filter(pk__in=raw_ids))
                if len(tasks) != len(raw_ids):
                    messages.error(request, "存在无效任务")
                elif action == "bind_tasks":
                    proj.library_tasks.add(*tasks)
                    # 任务与项目建立关联时，自动同步该任务已有关联文件到项目。
                    all_file_ids = set()
                    for t in tasks:
                        all_file_ids.update(t.library_files.values_list("id", flat=True))
                    if all_file_ids:
                        attach_files_to_projects(sorted(all_file_ids), [proj.pk], request.user)
                    messages.success(request, f"已向项目关联 {len(tasks)} 个任务")
                else:
                    proj.library_tasks.remove(*tasks)
                    # 任务与项目解除关联时，自动移除该任务文件与项目的关联。
                    all_file_ids = set()
                    for t in tasks:
                        all_file_ids.update(t.library_files.values_list("id", flat=True))
                    if all_file_ids:
                        detach_files_from_projects(sorted(all_file_ids), [proj.pk])
                    messages.success(request, f"已从项目移除 {len(tasks)} 个任务")
            return redirect(reverse("library_projects") + f"?project_id={proj.pk}")

        if action == "update_project_report_sources":
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
            if not role_has(request.user, "perm_assign_tasks"):
                messages.error(request, "当前角色无权维护报告来源任务")
                return redirect(reverse("library_projects") + f"?project_id={proj.pk}")
            if not _librarytask_has_report_source_relation():
                messages.error(request, "当前环境尚未启用报告来源任务关联，请先完成数据库迁移")
                return redirect(reverse("library_projects") + f"?project_id={proj.pk}")

            project_tasks = proj.library_tasks.all()
            report_tasks = list(project_tasks.filter(output_target=LibraryTask.OUTPUT_REPORT).only("id", "code", "name"))
            report_task_ids = {x.id for x in report_tasks}
            try:
                report_task_id = int(request.POST.get("report_task_id", "") or 0)
            except ValueError:
                report_task_id = 0
            if report_task_id <= 0 or report_task_id not in report_task_ids:
                messages.error(request, "请选择当前项目内的报告任务")
                return redirect(reverse("library_projects") + f"?project_id={proj.pk}")
            report_task = next((x for x in report_tasks if x.id == report_task_id), None)
            if report_task is None:
                messages.error(request, "报告任务不存在")
                return redirect(reverse("library_projects") + f"?project_id={proj.pk}")

            site_task_ids = set(
                project_tasks.filter(output_target=LibraryTask.OUTPUT_SITE_RECORD).values_list("id", flat=True)
            )
            selected = []
            for raw in request.POST.getlist("source_site_task_ids"):
                try:
                    sid = int(raw)
                except ValueError:
                    continue
                if sid > 0:
                    selected.append(sid)
            selected = sorted(set(selected))
            if any(sid not in site_task_ids for sid in selected):
                messages.error(request, "存在不属于当前项目的现场记录来源任务")
                return redirect(
                    reverse("library_projects") + f"?project_id={proj.pk}&report_task_id={report_task_id}"
                )
            report_task.report_source_tasks.set(selected)
            messages.success(request, f"已保存报告任务「{report_task.code}」的现场记录来源关联")
            return redirect(reverse("library_projects") + f"?project_id={proj.pk}&report_task_id={report_task_id}")

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
            if not role_has(request.user, "perm_assign_tasks"):
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
                if library_scope_own_files_only(request.user):
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
            return redirect(reverse("library_projects") + f"?project_id={proj.pk}&project_file_tab={file_tab}")

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
            if not role_has(request.user, "perm_assign_tasks"):
                messages.error(request, "无权操作")
                return redirect(reverse("library_projects") + f"?project_id={proj.pk}")
            try:
                uid = int(request.POST.get("user_id", "") or 0)
            except ValueError:
                uid = 0
            target = User.objects.filter(pk=uid, profile__role__code="app_user", is_active=True).first()
            if target is None or not LibraryTaskAssignment.objects.filter(
                project=proj, assignee=target
            ).exists():
                messages.error(request, "用户无效或从未被分配到本项目")
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
            return redirect(reverse("library_projects") + f"?project_id={proj.pk}")

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
            if not role_has(request.user, "perm_assign_tasks"):
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
            return redirect(reverse("library_projects") + f"?project_id={proj.pk}")

        if action == "delete_project":
            if not role_has(request.user, "perm_assign_tasks"):
                messages.error(request, "当前角色无权删除项目")
                return redirect(reverse("library_projects"))
            post_raw = request.POST.get("project_id", "").strip()
            try:
                post_pid = int(post_raw) if post_raw else None
            except ValueError:
                post_pid = None
            proj = LibraryProject.objects.filter(pk=post_pid).first() if post_pid else None
            if proj is None:
                messages.error(request, "项目不存在")
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
        if library_scope_own_files_only(request.user):
            fq = fq.filter(created_by=request.user)
        project_file_rows = list(fq[:600])
        project_file_ids = set(
            selected_project.library_files.filter(category=project_file_cat).values_list("id", flat=True)
        )

    project_assignees = []
    if selected_project and role_has(request.user, "perm_assign_tasks"):
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
    if selected_project and role_has(request.user, "perm_assign_tasks"):
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

    return render(
        request,
        "core/library_projects.html",
        {
            "projects": LibraryProject.objects.order_by("-is_active", "code"),
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
            "can_assign_tasks": role_has(request.user, "perm_assign_tasks"),
            "file_library_tabs": [
                {"key": "ocr", "label": "OCR文件"},
                {"key": "json", "label": "JSON 文件"},
                {"key": "template", "label": "模板"},
                {"key": "site_record", "label": "现场记录"},
                {"key": "report", "label": "报告"},
                {"key": "attachment", "label": "附件"},
                {"key": "inspection_submit", "label": "检测提交"},
            ],
        },
    )


def _file_library_query_string(
    tab: str, date_from: str = "", date_to: str = "", uploader: str = "", project: str = ""
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
    return "?" + urlencode(q)


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
    {"ocr", "json", "template", "site_record", "report", "attachment", "inspection_submit"}
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


def _annotate_inspection_submit_project_task(files: list) -> None:
    """为检测提交文件库行补充「项目 / 任务」展示文案（写回各 LibraryFile 实例属性）。"""
    from apps.api.inspection_pdf_service import _resolve_library_task_for_task_no

    case_ids: list[int] = []
    for f in files:
        if (
            f.category == LibraryFile.CATEGORY_INSPECTION_SUBMIT
            and f.link_entity == LibraryFile.LINK_ENTITY_INSPECTION_CASE
            and f.link_object_id
        ):
            case_ids.append(int(f.link_object_id))
    cases_by_id = {}
    if case_ids:
        for c in InspectionCase.objects.filter(pk__in=sorted(set(case_ids))).select_related("library_project"):
            cases_by_id[c.pk] = c
    for f in files:
        if f.category != LibraryFile.CATEGORY_INSPECTION_SUBMIT:
            continue
        if f.link_entity != LibraryFile.LINK_ENTITY_INSPECTION_CASE or not f.link_object_id:
            f.inspection_submit_project_task = "未绑定检测案件"
            continue
        case = cases_by_id.get(int(f.link_object_id))
        if case is None:
            f.inspection_submit_project_task = "关联案件不存在"
            continue
        proj = case.library_project
        if proj is None:
            f.inspection_submit_project_task = f"任务编号 {case.case_no} · 未绑定项目"
            continue
        lt = _resolve_library_task_for_task_no(case.case_no, proj)
        proj_part = f"{proj.code} · {proj.name}"
        if lt is not None:
            ot = lt.get_output_target_display()
            f.inspection_submit_project_task = f"{proj_part} / {lt.code} · {lt.name}（{ot}）"
        else:
            f.inspection_submit_project_task = f"{proj_part} / 任务编号 {case.case_no}"


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

    if request.method == "POST" and request.POST.get("action") == "create_project":
        if not role_has(request.user, "perm_assign_tasks"):
            messages.error(request, "当前角色无权管理项目")
            return redirect(reverse("file_library") + _file_library_query_string(tab, "", "", "", project_selected))
        code = request.POST.get("project_code", "").strip()
        name = request.POST.get("project_name", "").strip()
        desc = request.POST.get("project_desc", "").strip()
        if not code or not name:
            messages.error(request, "项目编码与名称不能为空")
        elif LibraryProject.objects.filter(code=code).exists():
            messages.error(request, "项目编码已存在")
        else:
            LibraryProject.objects.create(
                code=code,
                name=name,
                description=desc,
                created_by=request.user,
            )
            messages.success(request, f"已创建项目：{name}")
        return redirect(reverse("file_library") + _file_library_query_string(tab, "", "", "", project_selected))

    if request.method == "POST" and request.POST.get("action") == "deactivate_project":
        if not role_has(request.user, "perm_assign_tasks"):
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

    if request.method == "POST" and request.POST.get("action") == "upload":
        if not role_can_upload_library_category(request.user, LibraryFile.CATEGORY_UPLOAD):
            messages.error(request, "当前角色无权向「OCR文件」分类上传")
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
            messages.error(request, "当前角色无权上传 JSON 文件")
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
            messages.error(request, "请选择要上传的 JSON 文件")
        else:
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
                    messages.warning(request, f"内容不是合法 JSON，已跳过: {safe}")
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
                messages.success(request, f"已上传 {added} 个 JSON 文件")
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
            n = _persist_binary_library_files(
                request, files, LibraryFile.CATEGORY_TEMPLATE, project_ids=post_project_ids
            )
            if n:
                messages.success(request, f"已上传 {n} 个模板文件")
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
        if not role_has(request.user, "perm_process_pipeline"):
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
            messages.error(request, "请先勾选至少一条检测提交 JSON")
            df = request.POST.get("date_from", "").strip()
            dt = request.POST.get("date_to", "").strip()
            up = request.POST.get("uploader", "").strip()
            return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up, project_selected))

        from apps.api.inspection_views import (
            _build_filled_template_fields_for_task,
            _pick_submit_generation_tasks,
            _persist_filled_pdf_from_submit,
        )

        qs = (
            LibraryFile.objects.filter(pk__in=ids, category=LibraryFile.CATEGORY_INSPECTION_SUBMIT)
            .select_related("created_by")
            .order_by("-created_at")
        )
        success_count = 0
        skipped_count = 0
        skip_reasons = []
        for lf in qs:
            if not library_file_access_allowed(request.user, lf):
                skipped_count += 1
                skip_reasons.append(f"{lf.original_name}: 无权访问该文件")
                continue
            try:
                p = pipeline_service.library_absolute_path(lf.relative_path)
                payload = json_std.loads(p.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                skipped_count += 1
                skip_reasons.append(f"{lf.original_name}: JSON 读取或解析失败")
                continue
            required_keys = {"reportInfo", "hospitalInfo", "equipmentInfo", "testResult"}
            if not isinstance(payload, dict) or not required_keys.issubset(set(payload.keys())):
                skipped_count += 1
                skip_reasons.append(f"{lf.original_name}: 不是原始 submit JSON")
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
                    task_obj, payload, project=case.library_project, task_no=case.case_no
                )
                if not filled_fields:
                    skip_reasons.append(
                        f"{lf.original_name} / {task_obj.code}({task_obj.output_target}): {fill_reason or '模板填充失败'}"
                    )
                    continue
                ok, pdf_reason = _persist_filled_pdf_from_submit(
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
        if tab != "inspection_submit":
            messages.error(request, "仅支持在「检测提交」分类执行手动导出报告")
            df = request.POST.get("date_from", "").strip()
            dt = request.POST.get("date_to", "").strip()
            up = request.POST.get("uploader", "").strip()
            return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up, project_selected))
        if not role_has(request.user, "perm_process_pipeline"):
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
            messages.error(request, "请先勾选至少一条检测提交 JSON")
            df = request.POST.get("date_from", "").strip()
            dt = request.POST.get("date_to", "").strip()
            up = request.POST.get("uploader", "").strip()
            return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up, project_selected))

        from apps.api.inspection_pdf_service import (
            _build_filled_template_fields_for_task,
            _load_site_record_payload_for_report,
            _persist_filled_pdf_from_submit,
            _resolve_report_task_for_case,
        )

        qs = (
            LibraryFile.objects.filter(pk__in=ids, category=LibraryFile.CATEGORY_INSPECTION_SUBMIT)
            .select_related("created_by")
            .order_by("-created_at")
        )
        success_count = 0
        skipped_count = 0
        skip_reasons = []
        for lf in qs:
            if not library_file_access_allowed(request.user, lf):
                skipped_count += 1
                skip_reasons.append(f"{lf.original_name}: 无权访问该文件")
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
            report_task = _resolve_report_task_for_case(case.case_no, case.library_project)
            if report_task is None:
                skipped_count += 1
                skip_reasons.append(f"{lf.original_name}: 项目下未找到报告任务")
                continue
            source_payload, source_reason = _load_site_record_payload_for_report(case, case.library_project, report_task)
            if not isinstance(source_payload, dict):
                skipped_count += 1
                skip_reasons.append(f"{lf.original_name}: {source_reason or '读取现场记录 JSON 失败'}")
                continue
            filled_fields, template_pdf_id, fill_reason, template_json_name = _build_filled_template_fields_for_task(
                report_task,
                source_payload,
                project=case.library_project,
                task_no=case.case_no,
            )
            if not filled_fields:
                skipped_count += 1
                skip_reasons.append(
                    f"{lf.original_name} / {report_task.code}({report_task.output_target}): {fill_reason or '模板填充失败'}"
                )
                continue
            ok, pdf_reason = _persist_filled_pdf_from_submit(
                request.user,
                case.case_no,
                case,
                case.library_project,
                filled_fields,
                template_pdf_id=template_pdf_id,
                template_json_name=template_json_name,
                task_obj=report_task,
            )
            if not ok:
                skipped_count += 1
                skip_reasons.append(
                    f"{lf.original_name} / {report_task.code}({report_task.output_target}): {pdf_reason or '报告导出失败'}"
                )
                continue
            if pdf_reason:
                messages.warning(request, f"{lf.original_name} / {report_task.code}: {pdf_reason}")
            success_count += 1

        if success_count:
            messages.success(request, f"已为 {success_count} 条检测提交从现场记录 JSON 导出报告")
        if skipped_count:
            messages.warning(request, f"有 {skipped_count} 条记录未导出报告")
            for reason in skip_reasons[:20]:
                messages.warning(request, reason)
            if len(skip_reasons) > 20:
                messages.warning(request, f"其余 {len(skip_reasons) - 20} 条原因已省略")
        df = request.POST.get("date_from", "").strip()
        dt = request.POST.get("date_to", "").strip()
        up = request.POST.get("uploader", "").strip()
        return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up, project_selected))

    date_err, d0, d1, date_from, date_to = _parse_file_library_date_range(request)
    if date_err:
        messages.warning(request, date_err)
        if request.GET.get("date_from") or request.GET.get("date_to"):
            return redirect(reverse("file_library") + f"?tab={tab}")
        d0, d1, date_from, date_to = None, None, "", ""

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

    files = LibraryFile.objects.filter(category=cat).select_related("created_by")
    if project_selected_id:
        files = files.filter(projects__id=project_selected_id).distinct()
    if restricted:
        assigned_pids = library_assigned_project_ids(request.user)
        if assigned_pids:
            files = files.filter(
                Q(created_by=request.user) | Q(projects__id__in=assigned_pids)
            ).distinct()
        else:
            files = files.filter(created_by=request.user)
    elif uploader_id is not None:
        files = files.filter(created_by_id=uploader_id)

    if d0 is not None and d1 is not None:
        files = files.filter(created_at__date__gte=d0, created_at__date__lte=d1)

    files = files.order_by("-created_at", "-id")
    show_submit_project_task = tab == "inspection_submit"
    if show_submit_project_task:
        files = list(files)
        _annotate_inspection_submit_project_task(files)

    uploader_choices = []
    if not restricted:
        chooser_qs = LibraryFile.objects.filter(category=cat).exclude(
            created_by_id__isnull=True
        )
        if project_selected_id:
            chooser_qs = chooser_qs.filter(projects__id=project_selected_id)
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
        ap = library_assigned_project_ids(request.user)
        projects_qs = projects_qs.filter(pk__in=ap) if ap else projects_qs.none()

    can_batch_delete = role_has(request.user, "perm_file_delete") and not restricted
    file_library_table_colspan = 5 + (1 if can_batch_delete else 0) + (1 if show_submit_project_task else 0)

    return render(
        request,
        "core/file_library.html",
        {
            "projects": projects_qs,
            "project_selected": project_selected,
            "tab": tab,
            "file_library_tabs": [
                {"key": "ocr", "label": "OCR文件"},
                {"key": "json", "label": "JSON 文件"},
                {"key": "template", "label": "模板"},
                {"key": "site_record", "label": "现场记录"},
                {"key": "report", "label": "报告"},
                {"key": "attachment", "label": "附件"},
                {"key": "inspection_submit", "label": "检测提交"},
            ],
            "files": files,
            "show_submit_project_task": show_submit_project_task,
            "file_library_table_colspan": file_library_table_colspan,
            "date_from": date_from,
            "date_to": date_to,
            "date_filter_active": bool(d0 and d1),
            "uploader_selected": uploader_selected,
            "uploader_choices": uploader_choices,
            "file_scope_own_only": restricted,
            "can_batch_delete": can_batch_delete,
            "can_manual_export_submit_pdf": (
                tab == "inspection_submit"
                and role_has(request.user, "perm_process_pipeline")
                and role_has(request.user, "perm_file_delete")
                and not restricted
            ),
            "can_manual_export_report_from_site_record": (
                tab == "inspection_submit"
                and role_has(request.user, "perm_process_pipeline")
                and role_has(request.user, "perm_file_delete")
                and not restricted
            ),
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
    if library_file_write_blocked_by_project_revocation(request.user, lf):
        messages.error(request, "您已被取消在该项目上的编辑权限，仅可下载与预览，不能删除文件。")
        tab = tab_guess
        df = request.POST.get("date_from", "").strip()
        dt = request.POST.get("date_to", "").strip()
        up = request.POST.get("uploader", "").strip()
        return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up))
    tab = tab_guess
    try:
        p = pipeline_service.library_absolute_path(lf.relative_path)
        if p.is_file():
            p.unlink()
    except (ValueError, OSError):
        pass
    lf.delete()
    messages.success(request, "已删除")
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
    if library_scope_own_files_only(request.user):
        messages.error(request, "当前角色在「仅本人数据」模式下不可批量删除")
        return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up))
    ids = request.POST.getlist("ids")
    if not ids:
        messages.error(request, "未选择文件")
        return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up))

    cat = _library_category_for_tab(tab)
    qs = LibraryFile.objects.filter(pk__in=ids, category=cat)
    allowed_pks = [
        lf.pk
        for lf in qs
        if library_file_access_allowed(request.user, lf)
        and not library_file_write_blocked_by_project_revocation(request.user, lf)
    ]
    if set(ids) != {str(pk) for pk in allowed_pks}:
        messages.error(request, "部分所选文件不存在或无权删除")
        return redirect(reverse("file_library") + _file_library_query_string(tab, df, dt, up))
    qs = LibraryFile.objects.filter(pk__in=allowed_pks, category=cat)
    for lf in qs:
        try:
            p = pipeline_service.library_absolute_path(lf.relative_path)
            if p.is_file():
                p.unlink()
        except (ValueError, OSError):
            pass
    deleted, _ = qs.delete()
    messages.success(request, f"已删除 {deleted} 个文件")
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
    if not role_has(request.user, "perm_process_pipeline"):
        messages.error(request, "当前角色无权使用流程处理")
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
                f"已按仪器拆分并保存 {n_json} 个 JSON 文件到文件库「JSON 文件」分类。",
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
    gx = _require_perm(request, "perm_process_pipeline")
    if gx:
        return gx
    rows = (
        LibraryFile.objects.filter(category=LibraryFile.CATEGORY_TEMPLATE)
        .order_by("-created_at")
        .select_related("created_by")
    )
    files = []
    for lf in rows[:500]:
        if not library_file_access_allowed(request.user, lf):
            continue
        if not lf.original_name.lower().endswith(".pdf"):
            continue
        files.append(lf)
    return render(request, "core/htmlpdf_files.html", {"template_pdf_files": files})


@login_required
def htmlpdf_editor_open(request, pk: int):
    gx = _require_perm(request, "perm_process_pipeline")
    if gx:
        return gx
    lf = get_object_or_404(LibraryFile, pk=pk, category=LibraryFile.CATEGORY_TEMPLATE)
    if not library_file_access_allowed(request.user, lf):
        raise PermissionDenied("无权访问该模板文件")
    if not lf.original_name.lower().endswith(".pdf"):
        messages.error(request, "仅支持从模板分类中的 PDF 进入编辑器")
        return redirect(reverse("htmlpdf_editor"))
    index_path = Path(settings.BASE_DIR) / "htmlpdf" / "templates" / "index.html"
    if not index_path.is_file():
        return HttpResponse("htmlpdf 模板页面不存在", status=404)
    html = index_path.read_text(encoding="utf-8")
    inject = (
        "<script>"
        f"window.HTMLPDF_INITIAL_TEMPLATE_PDF_ID={lf.pk};"
        "</script>"
    )
    html = html.replace("</body>", inject + "\n</body>")
    return HttpResponse(html, content_type="text/html; charset=utf-8")


@login_required
def htmlpdf_template_file(request, pk: int):
    gx = _require_perm(request, "perm_process_pipeline")
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
    gx = _require_perm(request, "perm_process_pipeline")
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
    gx = _require_perm(request, "perm_process_pipeline")
    if gx:
        return JsonResponse({"error": "forbidden"}, status=403)
    rows = []
    qs = (
        LibraryFile.objects.filter(category=LibraryFile.CATEGORY_TEMPLATE)
        .order_by("-created_at")
        .only("id", "original_name")
    )
    for lf in qs[:500]:
        if not library_file_access_allowed(request.user, lf):
            continue
        if not lf.original_name.lower().endswith(".json"):
            continue
        rows.append({"id": lf.pk, "name": lf.original_name})
    return JsonResponse({"templates": rows})


@csrf_exempt
@login_required
@require_POST
def htmlpdf_api_import_json_from_library(request):
    gx = _require_perm(request, "perm_process_pipeline")
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
    return JsonResponse(parsed)


@csrf_exempt
@login_required
@require_POST
def htmlpdf_api_use_template_pdf(request):
    gx = _require_perm(request, "perm_process_pipeline")
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
    return JsonResponse({"ok": True, "pdf_url": reverse("htmlpdf_template_file", kwargs={"pk": lf.pk})})


@csrf_exempt
@login_required
@require_POST
def htmlpdf_api_upload_pdf(request):
    gx = _require_perm(request, "perm_process_pipeline")
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
    gx = _require_perm(request, "perm_process_pipeline")
    if gx:
        return JsonResponse({"error": "forbidden"}, status=403)
    file = request.FILES.get("jsonFile")
    if not file:
        return JsonResponse({"error": "请选择JSON文件"}, status=400)
    try:
        parsed = htmlpdf_service.parse_template_json(file.read().decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "JSON文件格式无效"}, status=400)
    return JsonResponse(parsed)


@csrf_exempt
@require_POST
def htmlpdf_api_export_json(request):
    auth_resp = _require_api_login(request)
    if auth_resp:
        return auth_resp
    gx = _require_perm(request, "perm_process_pipeline")
    if gx:
        return JsonResponse({"error": "forbidden"}, status=403)
    try:
        data = json_std.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "请求体不是合法JSON"}, status=400)
    fields = _sanitize_pdf_field_texts(data.get("fields", []))
    form_schema = data.get("form_schema") if isinstance(data.get("form_schema"), dict) else {}
    bindings = data.get("bindings") if isinstance(data.get("bindings"), dict) else {}
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
    normalized_fields = _normalize_pdf_fields_for_unified_template(fields, form_schema, bindings)
    constants = form_schema.get("constants") if isinstance(form_schema.get("constants"), dict) else {}
    enums = form_schema.get("enums") if isinstance(form_schema.get("enums"), dict) else {}
    steps = form_schema.get("steps") if isinstance(form_schema.get("steps"), list) else []
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
    try:
        rule_payload = build_frontend_schema_by_rules(rule_template_obj, merge_split_dates=False)
    except Exception:
        rule_payload = {}
    if isinstance(rule_payload, dict) and isinstance(rule_payload.get("steps"), list) and rule_payload.get("steps"):
        constants = rule_payload.get("constants") if isinstance(rule_payload.get("constants"), dict) else constants
        enums = rule_payload.get("enums") if isinstance(rule_payload.get("enums"), dict) else enums
        steps = rule_payload.get("steps") or steps
    payload = {
        # Unified template schema (v2): frontend format first, backend adds PDF anchors/mappings.
        "schema": "unified_form_template/v2",
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
        # Keep v1-compatible blocks for old readers.
        "meta": {
            "templateId": template_meta.get("templateId") or name.rsplit(".", 1)[0],
            "templateName": template_meta.get("templateName") or name,
            "version": template_meta.get("version") or "1.0.0",
            "reportType": report_type,
            "standard": standard,
            "pdfUrl": str(template_meta.get("pdfUrl") or ""),
            "locale": str(template_meta.get("locale") or "zh-CN"),
        },
        "pdf": {
            "source_pdf": source_meta,
            "fields": normalized_fields,
        },
        "formSchema": {"constants": constants, "enums": enums, "steps": steps},
        "bindings": bindings,
        "fields": normalized_fields,
        "source_pdf": source_meta,
    }
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
        }
    )


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
        link_pdf_id = str(link.get("pdfFieldId") or "").strip() if isinstance(link, dict) else ""
        id_fallback = str(row.get("id") or "").strip()
        candidates = [raw_pdf_id, link_pdf_id, id_fallback]
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
    return out


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
                            header_fields.append(
                                {
                                    "id": fid,
                                    "type": _matrix_field_type_from_pdf(str(it.get("fieldType") or "text")),
                                    "label": token or semantic,
                                    "required": False,
                                    "defaultValue": None,
                                    "precision": 1,
                                    "unit": "",
                                    "source": {
                                        "pdfFieldId": pdf_field_id,
                                        "page": page,
                                        "anchorType": str(it.get("fieldType") or "text"),
                                        "submitBucket": "testResult",
                                        "submitPath": src_path,
                                        "legacySubmitPath": src_path,
                                        "key": f"step_qc_items.sec_t{page}_{int(tbl.get('table_id') or 0)}_matrix.header.{semantic}",
                                    },
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
                        row_cells[key] = {
                            "id": f"t{int(tbl.get('table_id') or 0)}_row_{row_seq}_{key}",
                            "type": "text",
                            "label": value_columns[i]["title"],
                            "required": False,
                            "defaultValue": raw_txt or None,
                            "precision": 1,
                            "unit": value_columns[i].get("unit") or "",
                            "editable": False,
                            "source": {
                                "pdfFieldId": "",
                                "page": page,
                                "anchorType": "text",
                                "submitBucket": "testResult",
                                "submitPath": submit_path,
                                "legacySubmitPath": legacy,
                                "key": f"step_qc_items.sec_t{page}_{int(tbl.get('table_id') or 0)}_matrix.rows[{row_seq}].{key}",
                            },
                        }
                        if raw_txt:
                            static_cells[key] = {"text": raw_txt, "editable": False}
                        continue
                    it = arr[0]
                    pdf_field_id = _prefer_pdf_field_id_token(it, "")
                    legacy = f"testResult.legacy.page{page}.table{int(tbl.get('table_id') or 0)}.r{row_seq}.{key}"
                    submit_path = f"testResult.matrixAuto.page{page}.table{int(tbl.get('table_id') or 0)}.rows[{row_seq}].{key}"
                    row_cells[key] = {
                        "id": f"t{int(tbl.get('table_id') or 0)}_row_{row_seq}_{key}",
                        "type": "number" if str(it.get("fieldType") or "text").lower() == "text" else _matrix_field_type_from_pdf(str(it.get("fieldType") or "text")),
                        "label": value_columns[i]["title"],
                        "required": False,
                        "defaultValue": None,
                        "precision": 1,
                        "unit": value_columns[i].get("unit") or "",
                        "source": {
                            "pdfFieldId": pdf_field_id,
                            "page": page,
                            "anchorType": str(it.get("fieldType") or "text"),
                            "submitBucket": "testResult",
                            "submitPath": submit_path,
                            "legacySubmitPath": legacy,
                            "key": f"step_qc_items.sec_t{page}_{int(tbl.get('table_id') or 0)}_matrix.rows[{row_seq}].{key}",
                        },
                    }
                # 允许“纯静态行”存在，以便前端最大化还原 PDF 表格排版
                if not row_cells and not static_cells and not any(str(v or "").strip() for v in headers.values()):
                    continue
                row_obj = {
                    "id": f"t{int(tbl.get('table_id') or 0)}_row_{row_seq}",
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


@csrf_exempt
@require_POST
def htmlpdf_api_export_frontend_json(request):
    auth_resp = _require_api_login(request)
    if auth_resp:
        return auth_resp
    gx = _require_perm(request, "perm_process_pipeline")
    if gx:
        return JsonResponse({"error": "forbidden"}, status=403)
    try:
        data = json_std.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "请求体不是合法JSON"}, status=400)

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
        }
    )


@csrf_exempt
@require_POST
def htmlpdf_api_export_matrix_json(request):
    auth_resp = _require_api_login(request)
    if auth_resp:
        return auth_resp
    gx = _require_perm(request, "perm_process_pipeline")
    if gx:
        return JsonResponse({"error": "forbidden"}, status=403)
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

    payload = {
        "schema": "unified_form_template/v2",
        "templateId": template_meta.get("templateId") or name.rsplit(".", 1)[0],
        "templateName": template_meta.get("templateName") or name,
        "version": template_meta.get("version") or "1.0.0",
        "reportType": report_type,
        "standard": standard,
        "pdfUrl": str(template_meta.get("pdfUrl") or ""),
        "locale": str(template_meta.get("locale") or "zh-CN"),
        "constants": {},
        "enums": {},
        "steps": steps,
        "meta": {
            "templateId": template_meta.get("templateId") or name.rsplit(".", 1)[0],
            "templateName": template_meta.get("templateName") or name,
            "version": template_meta.get("version") or "1.0.0",
            "reportType": report_type,
            "standard": standard,
            "pdfUrl": str(template_meta.get("pdfUrl") or ""),
            "locale": str(template_meta.get("locale") or "zh-CN"),
        },
        "pdf": {"source_pdf": source_meta, "fields": normalized_fields},
        "formSchema": {"constants": {}, "enums": {}, "steps": steps},
        "bindings": {},
        "fields": normalized_fields,
        "source_pdf": source_meta,
    }
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
    gx = _require_perm(request, "perm_process_pipeline")
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
    gx = _require_perm(request, "perm_process_pipeline")
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
@require_POST
def htmlpdf_api_auto_red_text_boxes(request):
    auth_resp = _require_api_login(request)
    if auth_resp:
        return auth_resp
    gx = _require_perm(request, "perm_process_pipeline")
    if gx:
        return JsonResponse({"error": "forbidden"}, status=403)
    rows = htmlpdf_service.htmlpdf_auto_red_text_fields_for_editor(request.user.id)
    return JsonResponse({"fields": rows, "count": len(rows)})


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
    et = (request.POST.get("edit_task") or request.GET.get("edit_task") or "").strip().lower()
    if et in ("1", "true", "yes", "on"):
        q.append("edit_task=1")
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
    """项目分配管理：新建任务模板、维护模板文件、按项目分配给 App 用户。"""
    gx = _require_perm(request, "perm_file_library")
    if gx:
        return gx
    can_assign = role_has(request.user, "perm_assign_tasks")
    projects = (
        LibraryProject.objects.filter(is_active=True)
        .prefetch_related("library_tasks")
        .order_by("code")
    )

    if request.method == "POST" and can_assign:
        action = request.POST.get("action", "").strip()
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
                messages.success(
                    request,
                    f"已创建任务模板 {row.code}（输出到：{row.get_output_target_display()}）。"
                    f"已选中该模板，点击「编辑任务模板」可维护文件关联。",
                )
                return redirect(reverse("library_task_management") + f"?manage_task={row.pk}")
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
        elif action in ("bind_task_files", "unbind_task_files"):
            try:
                tid = int(request.POST.get("manage_task_id", "") or 0)
            except ValueError:
                tid = 0
            task_tab = _normalize_library_tab(request.POST.get("task_tab", "ocr"))
            task_cat = _library_category_for_tab(task_tab)
            task_obj = LibraryTask.objects.filter(pk=tid).first() if tid else None
            ids = []
            for x in request.POST.getlist("file_ids"):
                try:
                    ids.append(int(x))
                except (TypeError, ValueError):
                    continue
            ids = list(dict.fromkeys([i for i in ids if i > 0]))
            if task_obj is None:
                messages.error(request, "请选择有效任务模板")
            elif not ids:
                messages.error(request, "请至少勾选一个文件")
            else:
                qs = LibraryFile.objects.filter(pk__in=ids, category=task_cat)
                if library_scope_own_files_only(request.user):
                    qs = qs.filter(created_by=request.user)
                valid_ids = list(qs.values_list("id", flat=True))
                if len(valid_ids) != len(ids):
                    messages.error(request, "所选文件无效或分类与当前标签不一致")
                elif action == "bind_task_files":
                    attach_files_to_tasks(valid_ids, [task_obj.pk], request.user)
                    # 文件挂到任务后，同步挂到该任务关联的所有项目（传递性）。
                    project_ids = list(task_obj.projects.values_list("id", flat=True))
                    if project_ids:
                        attach_files_to_projects(valid_ids, project_ids, request.user)
                    messages.success(request, f"已向任务模板关联 {len(valid_ids)} 个文件")
                else:
                    detach_files_from_tasks(valid_ids, [task_obj.pk])
                    # 文件从任务移除后，同步从该任务关联的所有项目移除（传递性）。
                    project_ids = list(task_obj.projects.values_list("id", flat=True))
                    if project_ids:
                        detach_files_from_projects(valid_ids, project_ids)
                    messages.success(request, f"已从任务模板移除 {len(valid_ids)} 个文件")
        elif action == "delete_task":
            try:
                tid = int(request.POST.get("task_id", "") or 0)
            except ValueError:
                tid = 0
            t_obj = LibraryTask.objects.filter(pk=tid).first()
            if t_obj is None:
                messages.error(request, "任务模板不存在")
            else:
                label = f"{t_obj.code} · {t_obj.name}"
                t_obj.delete()
                messages.success(request, f"已删除任务模板：{label}")
            return redirect(reverse("library_task_management"))
        elif action == "export_task_template_pdf":
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
            if not role_has(request.user, "perm_process_pipeline"):
                messages.error(request, "当前角色无权执行导出 PDF")
                return redirect(_library_task_management_redirect_url(request))
            if task_obj is None:
                messages.error(request, "任务模板不存在或无效")
                return redirect(_library_task_management_redirect_url(request))
            if task_obj.output_target not in {LibraryTask.OUTPUT_SITE_RECORD, LibraryTask.OUTPUT_REPORT}:
                messages.error(request, "仅现场记录/报告模板支持此导出入口")
                return redirect(_library_task_management_redirect_url(request))
            if project is None:
                messages.error(request, "请先选择项目")
                return redirect(_library_task_management_redirect_url(request))
            latest_submission = (
                InspectionSubmission.objects.filter(project=project)
                .select_related("case")
                .order_by("-updated_at", "-id")
                .first()
            )
            if latest_submission is None:
                messages.error(request, "该项目下暂无可用的 submit 记录")
                return redirect(reverse("library_task_management") + f"?manage_task={task_obj.pk}&export_project_id={project.pk}")
            case = latest_submission.case
            if case is None:
                messages.error(request, "最新 submit 记录缺少关联案件，无法导出")
                return redirect(reverse("library_task_management") + f"?manage_task={task_obj.pk}&export_project_id={project.pk}")

            from apps.api.inspection_pdf_service import (
                _build_filled_template_fields_for_task,
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
                    "updatedAt": latest_submission.updated_at_remote.isoformat() if latest_submission.updated_at_remote else "",
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
                    messages.error(request, source_reason or "未找到可用的现场记录 JSON，无法导出报告")
                    return redirect(
                        reverse("library_task_management") + f"?manage_task={task_obj.pk}&export_project_id={project.pk}"
                    )
                # 报告模板优先使用现场记录聚合结果，同时补齐项目/任务上下文字段。
                report_payload.setdefault("taskNo", latest_submission.task_no)
                report_payload.setdefault("projectId", project.code)
                report_payload.setdefault(
                    "updatedAt",
                    latest_submission.updated_at_remote.isoformat() if latest_submission.updated_at_remote else "",
                )
                payload = report_payload

            filled_fields, template_pdf_id, fill_reason, template_json_name = _build_filled_template_fields_for_task(
                task_obj,
                payload,
                project=project,
                task_no=latest_submission.task_no,
            )
            if not filled_fields:
                messages.error(request, fill_reason or "模板填充失败")
                return redirect(reverse("library_task_management") + f"?manage_task={task_obj.pk}&export_project_id={project.pk}")
            ok, pdf_reason = _persist_filled_pdf_from_submit(
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
            return redirect(reverse("library_task_management") + f"?manage_task={task_obj.pk}&export_project_id={project.pk}")
        elif action == "assign":
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
                    return redirect(reverse("library_task_management"))
                assignee = User.objects.filter(
                    pk=assignee_id,
                    profile__role__code="app_user",
                    is_active=True,
                ).first()
                if assignee is None:
                    messages.error(request, "接收用户必须是已启用的 App 用户")
                else:
                    project_tasks = list(project.library_tasks.all().order_by("code"))
                    if not project_tasks:
                        messages.error(request, "该项目尚未关联任务模板，请先在「项目管理」中为项目勾选任务模板")
                    else:
                        created_count = 0
                        all_file_ids = set()
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
                                    assigned_by=request.user,
                                )
                                created_count += 1
                            for fid in library_task.library_files.values_list("pk", flat=True):
                                all_file_ids.add(fid)
                        if all_file_ids:
                            attach_files_to_projects(sorted(all_file_ids), [project.pk], request.user)
                        if created_count:
                            messages.success(
                                request,
                                f"已向 {assignee.username} 分配项目「{project.name}」下 {created_count} 个任务模板，"
                                f"并同步 {len(all_file_ids)} 个文件到项目。",
                            )
                        else:
                            messages.info(
                                request,
                                f"{assignee.username} 已拥有该项目全部任务模板；已同步 {len(all_file_ids)} 个项目文件。",
                            )
            return redirect(reverse("library_task_management"))
        elif action == "unassign_project":
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
            assignee = User.objects.filter(pk=assignee_id, profile__role__code="app_user").first() if assignee_id else None
            if project is None or assignee is None:
                messages.error(request, "撤回失败：项目或用户无效")
                return redirect(reverse("library_task_management"))
            deleted_count, _ = LibraryTaskAssignment.objects.filter(
                project=project,
                assignee=assignee,
            ).delete()
            if deleted_count:
                messages.success(request, f"已撤回 {assignee.username} 在项目「{project.name}」上的分配")
            else:
                messages.info(request, f"{assignee.username} 在项目「{project.name}」上无可撤回分配")
            return redirect(reverse("library_task_management"))
        else:
            messages.error(request, "未知操作")
        return redirect(_library_task_management_redirect_url(request))

    app_users = (
        User.objects.filter(profile__role__code="app_user", is_active=True)
        .select_related("profile__role")
        .order_by("username")
    )

    if can_assign:
        assignment_rows = list(
            LibraryTaskAssignment.objects.filter(project_id__isnull=False)
            .select_related("assignee", "assigned_by", "project")
            .order_by("-created_at")[:800]
        )
    else:
        assignment_rows = list(
            LibraryTaskAssignment.objects.filter(
                assignee=request.user,
                project_id__isnull=False,
            )
            .select_related("assignee", "assigned_by", "project")
            .order_by("-created_at")[:800]
        )

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
    assignment_records = []
    if grouped:
        project_ids = sorted({v["project"].pk for v in grouped.values() if v.get("project")})
        project_map = {
            p.pk: p
            for p in LibraryProject.objects.filter(pk__in=project_ids)
            .prefetch_related("library_tasks")
            .order_by("code")
        }
        project_files_map = {}
        pfiles = (
            LibraryFile.objects.filter(projects__id__in=project_ids)
            .select_related("created_by")
            .order_by("original_name")
            .distinct()
        )
        for f in pfiles:
            for pid in f.projects.filter(pk__in=project_ids).values_list("id", flat=True):
                project_files_map.setdefault(pid, []).append(f)
        for item in grouped.values():
            proj = project_map.get(item["project"].pk) if item.get("project") else None
            if proj is None:
                continue
            assignment_records.append(
                {
                    "assignee": item["assignee"],
                    "assigned_by": item["assigned_by"],
                    "project": proj,
                    "created_at": item["created_at"],
                    "tasks": sorted(list(proj.library_tasks.all()), key=lambda x: x.code),
                    "files": project_files_map.get(proj.pk, []),
                }
            )
        assignment_records.sort(key=lambda x: x["created_at"], reverse=True)

    tasks = LibraryTask.objects.prefetch_related("library_files").order_by("code")

    manage_task = None
    manage_task_id_val = None
    template_visible_tabs = {"ocr", "json", "template", "attachment", "inspection_submit"}
    task_mgmt_tab = _normalize_library_tab(request.GET.get("task_tab", "ocr"))
    if task_mgmt_tab not in template_visible_tabs:
        task_mgmt_tab = "ocr"
    task_mgmt_cat = _library_category_for_tab(task_mgmt_tab)
    task_mgmt_files = []
    task_mgmt_linked_ids = set()
    task_linked_files = []
    export_project_id_val = ""
    task_edit_mode = False
    show_new_task_form = False
    if can_assign:
        et_raw = (request.GET.get("edit_task") or "").strip().lower()
        task_edit_mode = et_raw in ("1", "true", "yes", "on")
        nt_raw = (request.GET.get("new_task") or "").strip().lower()
        show_new_task_form = nt_raw in ("1", "true", "yes", "on")
        mt_raw = request.GET.get("manage_task", "").strip()
        if mt_raw:
            try:
                mid = int(mt_raw)
                manage_task = LibraryTask.objects.filter(pk=mid).first()
                if manage_task:
                    manage_task_id_val = mid
                    export_project_raw = (request.GET.get("export_project_id") or "").strip()
                    if export_project_raw:
                        try:
                            export_project_id_val = str(int(export_project_raw))
                        except ValueError:
                            export_project_id_val = ""
                    if task_edit_mode:
                        tq = (
                            LibraryFile.objects.filter(category=task_mgmt_cat)
                            .select_related("created_by")
                            .order_by("-created_at")[:600]
                        )
                        if library_scope_own_files_only(request.user):
                            tq = tq.filter(created_by=request.user)
                        task_mgmt_files = list(tq)
                        task_mgmt_linked_ids = set(
                            manage_task.library_files.filter(category=task_mgmt_cat).values_list(
                                "id", flat=True
                            )
                        )
                    else:
                        task_linked_files = list(
                            manage_task.library_files.exclude(
                                category__in=[LibraryFile.CATEGORY_SITE_RECORD, LibraryFile.CATEGORY_REPORT]
                            ).select_related("created_by").order_by(
                                "category", "original_name"
                            )
                        )
            except ValueError:
                pass
        if task_edit_mode and manage_task is None:
            task_edit_mode = False

    return render(
        request,
        "core/library_task_management.html",
        {
            "can_assign": can_assign,
            "projects": projects,
            "app_users": app_users,
            "assignment_records": assignment_records,
            "tasks": tasks,
            "manage_task": manage_task,
            "manage_task_id_val": manage_task_id_val,
            "task_mgmt_tab": task_mgmt_tab or "ocr",
            "task_mgmt_files": task_mgmt_files,
            "task_mgmt_linked_ids": task_mgmt_linked_ids,
            "task_linked_files": task_linked_files,
            "export_project_id_val": export_project_id_val,
            "task_edit_mode": task_edit_mode,
            "show_new_task_form": show_new_task_form,
            "file_library_tabs": [
                {"key": "ocr", "label": "OCR文件"},
                {"key": "json", "label": "JSON 文件"},
                {"key": "template", "label": "模板"},
                {"key": "attachment", "label": "附件"},
                {"key": "inspection_submit", "label": "检测提交"},
            ],
        },
    )
