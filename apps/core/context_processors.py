"""
上下文处理器
用于在模板中提供动态菜单等全局数据
"""

from django.urls import reverse

from apps.core.library_access import (
    library_user_has_party_a_demo_restrictions,
    library_user_is_commission_coordinator,
    library_user_is_coordinator_managed_workflow_user,
    library_user_may_view_coordinator_usage_guide,
    library_user_may_access_hospital_info_nav,
    library_user_may_access_instrument_database,
    library_user_may_access_task_template_library_nav,
    role_has,
    role_permission_map,
    role_ui_context,
)
from apps.core.menu_context_cache import (
    get_cached_menu_context_payload,
    set_cached_menu_context_payload,
)
from apps.core.models import Menu
from apps.core.usage_workflow_tour import tour_is_active


def _usage_site_tour_pop(title: str, description: str, side: str = "bottom", align: str = "start") -> dict:
    return {"title": title, "description": description, "side": side, "align": align}


def _build_usage_site_tour_manifest(request):
    """
    跨页「流程练习」：按账号类型返回 Driver.js 步骤清单。
    test 演示账号、委托统筹、统筹创建的流程岗位各有独立路线。
    """
    user = request.user
    if library_user_has_party_a_demo_restrictions(user):
        manifest = _build_party_a_usage_site_tour_manifest(request)
        if manifest is not None:
            manifest["storage_key"] = "usage_site_tour_v1"
        return manifest
    if library_user_is_commission_coordinator(user):
        manifest = _build_coordinator_site_tour_manifest(request)
        if manifest is not None:
            manifest["storage_key"] = "coordinator_site_tour_v1"
        return manifest
    if library_user_is_coordinator_managed_workflow_user(user):
        manifest = _build_coordinator_workflow_site_tour_manifest(request)
        if manifest is not None:
            manifest["storage_key"] = "coordinator_workflow_site_tour_v1"
        return manifest
    return None


def _build_party_a_usage_site_tour_manifest(request):
    """
    test 演示账号：新建项目 → 上传模板 → 任务模板库 → 挂任务。
    练习会话内登记的数据在「完成练习」时清理。
    """
    user = request.user

    role_perm = role_permission_map(user)
    has_file = bool(role_perm.get("perm_file_library"))
    show_task_nav = library_user_may_access_task_template_library_nav(user)
    has_htmlpdf = role_has(user, "perm_htmlpdf")

    help_index = reverse("backend_usage_guide")

    segments: list[dict] = [
        {
            "id": "help_index",
            "path": help_index,
            "search": "",
            "steps": [
                {
                    "element": "#guide-tour-hero",
                    "popover": _usage_site_tour_pop(
                        "流程练习（可操作页面）",
                        "接下来进入真实后台页面：<strong>高亮区域内的表单、按钮、链接均可直接点击</strong>（蒙层未镂空处不要点，以免误关练习）。"
                        "请按弹层说明完成<strong>新建项目 → 上传模板 → 新建任务模板 → 绑定模板文件 → 把任务挂到项目</strong>；仅在<strong>最后一页</strong>会出现「完成练习」，用于结束并删除本次练习登记的数据。",
                    ),
                },
                {
                    "element": "#guide-site-nav",
                    "popover": _usage_site_tour_pop(
                        "说明分页仍在使用说明里",
                        "各专题的<strong>图文细节</strong>在这里阅读；本流程负责在真实菜单里走一遍关键操作。",
                    ),
                },
                {
                    "element": "#guide-card-deck",
                    "popover": _usage_site_tour_pop(
                        "与左侧菜单对应",
                        "下方卡片与各专题说明一一对应。点「下一步」将登记练习会话并进入<strong>项目工作台</strong>（先建项目，再到文件库上传模板，再建任务并绑定文件）。",
                    ),
                },
            ],
        },
    ]

    if not has_file:
        return {"finish_label": "完成练习", "segments": segments}

    segments.append(
        {
            "id": "project_workbench",
            "path": reverse("library_projects"),
            "search": "",
            "steps": [
                {
                    "element": "#guide-project-header",
                    "popover": _usage_site_tour_pop(
                        "项目工作台",
                        "在此<strong>选项目</strong>并在右侧维护任务与流程。本练习请先完成左侧「新建项目」；后面还会回到这里把<strong>任务模板挂到项目</strong>。",
                    ),
                },
                {
                    "element": "#guide-project-sidebar",
                    "popover": _usage_site_tour_pop(
                        "项目列表",
                        "创建成功后左侧会出现新项目。点「下一步」前往<strong>文件库 · 模板</strong>上传至少一个版式文件，供后面任务模板绑定使用。",
                    ),
                },
                {
                    "element": "#guide-project-create",
                    "allow_interaction": True,
                    "popover": _usage_site_tour_pop(
                        "新建项目（请操作）",
                        "点左侧<strong>新建项目</strong>，在弹窗中按文件夹选择医院/院区/科室，"
                        "勾选设备与检测类型，指定统筹人后点<strong>创建项目</strong>。"
                        "创建成功后会进入项目工作台对应页签。",
                    ),
                },
                {
                    "element": "#guide-project-workbench-tabs",
                    "popover": _usage_site_tour_pop(
                        "确认已进入「任务与模板」",
                        "若未自动切到「任务与模板」，请点标签切换。此处可先不勾选任务，留到流程最后再挂。点「下一步」去<strong>文件库</strong>上传模板。",
                    ),
                },
            ],
        }
    )

    segments.append(
        {
            "id": "file_library_template",
            "path": reverse("file_library"),
            "search": "?tab=template",
            "steps": [
                {
                    "element": "#guide-file-library-tabs",
                    "popover": _usage_site_tour_pop(
                        "文件库 · 模板",
                        "确认当前在「模板」分类；下面将<strong>上传至少一个 PDF 等版式文件</strong>到模板库，供任务模板绑定。",
                    ),
                },
                {
                    "element": "#guide-file-library-template-upload",
                    "allow_interaction": True,
                    "popover": _usage_site_tour_pop(
                        "上传模板文件（请操作）",
                        "在下方区域选择文件并点<strong>上传</strong>。若无上传权限，请换有权限的账号或由同事预置模板后再练习。上传成功后点「下一步」。",
                    ),
                },
                {
                    "element": "#guide-file-library-filters",
                    "allow_interaction": True,
                    "popover": _usage_site_tour_pop(
                        "确认列表中出现新文件",
                        "可在列表中展开、筛选，确认刚上传的模板在库中。点「下一步」进入<strong>任务模板库</strong>新建任务。",
                    ),
                },
            ],
        }
    )

    if show_task_nav:
        segments.append(
            {
                "id": "task_template_new",
                "path": reverse("library_task_management"),
                "search": "?new_task=1",
                "steps": [
                    {
                        "element": "#guide-task-intro",
                        "popover": _usage_site_tour_pop(
                            "任务模板库",
                            "接下来新建<strong>至少一个任务模板</strong>；创建后要在「模板与仪器」里把刚上传的模板文件绑定到该任务。",
                        ),
                    },
                    {
                        "element": "#guide-task-new-form",
                        "allow_interaction": True,
                        "popover": _usage_site_tour_pop(
                            "新建任务模板（请操作）",
                            "填写<strong>名称</strong>，输出目标一般选「现场记录」即可，点<strong>创建</strong>。提交后会进入该模板的概览页；再点「下一步」。",
                        ),
                    },
                ],
            }
        )
        segments.append(
            {
                "id": "task_post_create_tabs",
                "path": reverse("library_task_management"),
                "match_path_only": True,
                "merge_query_from_current": True,
                "merge_query": {},
                "require_query_substrings": ["manage_task="],
                "require_query_absent_substrings": ["edit_task=1"],
                "steps": [
                    {
                        "element": "#guide-task-mgmt-tabs",
                        "allow_interaction": True,
                        "popover": _usage_site_tour_pop(
                            "打开「模板与仪器」（请操作）",
                            "请点击顶部的<strong>模板与仪器</strong>标签，进入可勾选文件库模板、并「将勾选加入模板」的页面。进入后点「下一步」。",
                        ),
                    },
                ],
            }
        )
        segments.append(
            {
                "id": "task_bind_templates",
                "path": reverse("library_task_management"),
                "match_path_only": True,
                "require_query_substrings": ["manage_task=", "edit_task=1"],
                "merge_query_from_current": True,
                "merge_query": {"edit_task": "1", "task_tab": "template"},
                "steps": [
                    {
                        "element": "#guide-task-template-bind-panel",
                        "allow_interaction": True,
                        "popover": _usage_site_tour_pop(
                            "绑定模板文件到任务（请操作）",
                            "在表格中<strong>勾选</strong>至少一个「模板」分类文件（建议选刚上传的 PDF），点<strong>将勾选加入模板</strong>。"
                            "绑定成功后再点「下一步」，回到项目工作台把该任务挂到项目。",
                        ),
                    },
                ],
            }
        )

    segments.append(
        {
            "id": "project_task_bind",
            "path": reverse("library_projects"),
            "search": "?tab=tasks",
            "steps": [
                {
                    "element": "#guide-project-bind-workspace",
                    "allow_interaction": True,
                    "popover": _usage_site_tour_pop(
                        "任务挂到项目（请操作）",
                        "在左侧选中<strong>本练习新建的项目</strong>，勾选刚创建并已绑定模板的<strong>任务模板</strong>，点<strong>加入项目</strong>。完成后点「下一步」。",
                    ),
                },
            ],
        }
    )

    if has_htmlpdf:
        segments.append(
            {
                "id": "htmlpdf_entry",
                "path": reverse("htmlpdf_editor"),
                "search": "",
                "steps": [
                    {
                        "element": "#guide-htmlpdf-entry",
                        "allow_interaction": True,
                        "popover": _usage_site_tour_pop(
                            "模板编辑器入口（可选）",
                            "若需微调版式可在此打开编辑器；保存到模板库的版式也会在「完成练习」时一并清理。不需要则直接点「下一步」。",
                        ),
                    },
                ],
            }
        )
        segments.append(
            {
                "id": "help_htmlpdf_doc",
                "path": reverse("backend_usage_guide_page", kwargs={"page": "htmlpdf"}),
                "search": "",
                "steps": [
                    {
                        "element": "#guide-site-nav",
                        "popover": _usage_site_tour_pop(
                            "对照图文说明",
                            "这是<strong>模板编辑器</strong>专题说明。点「完成练习」将删除练习中新建的项目、任务模板、上传/保存的模板文件等登记数据。",
                            side="bottom",
                            align="start",
                        ),
                    },
                ],
            }
        )
    else:
        segments.append(
            {
                "id": "help_wrap",
                "path": help_index,
                "search": "",
                "steps": [
                    {
                        "element": "#guide-tour-hero",
                        "popover": _usage_site_tour_pop(
                            "完成练习",
                            "已走完：新建项目 → 文件库上传模板 → 新建任务并绑定模板 → 任务挂到项目。点「完成练习」将删除上述练习登记的数据。",
                        ),
                    },
                ],
            }
        )

    return {"finish_label": "完成练习", "segments": segments}


def _build_coordinator_site_tour_manifest(request):
    """委托统筹：医院信息 → 用户 → 立项派工 → 委托管理 → 文件库。"""
    help_index = reverse("coordinator_usage_guide")
    segments: list[dict] = [
        {
            "id": "coord_help_index",
            "path": help_index,
            "search": "",
            "steps": [
                {
                    "element": "#guide-tour-hero",
                    "popover": _usage_site_tour_pop(
                        "流程练习（委托统筹）",
                        "接下来进入<strong>真实后台页面</strong>，高亮区域内可点击操作。"
                        "建议顺序：<strong>医院信息 → 创建岗位账号 → 新建委托并派工 → 查看委托进度 → 文件库</strong>。"
                        "点「完成练习」将<strong>删除本次练习登记的新建项目</strong>；已创建的用户账号不会自动删除，请自行决定是否保留。",
                    ),
                },
                {
                    "element": "#guide-site-nav",
                    "popover": _usage_site_tour_pop(
                        "图文说明在这里",
                        "各专题细节可在此阅读；本练习带您在真实菜单里走一遍关键路径。",
                    ),
                },
                {
                    "element": "#guide-card-deck",
                    "popover": _usage_site_tour_pop(
                        "与左侧菜单对应",
                        "点「下一步」登记练习会话并进入<strong>医院信息管理</strong>。",
                    ),
                },
            ],
        },
        {
            "id": "hospital_info",
            "path": reverse("hospital_info_manage"),
            "search": "",
            "steps": [
                {
                    "element": "#guide-hospital-header",
                    "popover": _usage_site_tour_pop(
                        "医院信息管理",
                        "维护受检单位与设备台账，立项时会引用此处数据。",
                    ),
                },
                {
                    "element": "#guide-hospital-sidebar",
                    "popover": _usage_site_tour_pop(
                        "组织树",
                        "在此浏览医院 → 院区 → 科室；选中后在右侧查看或编辑详情。",
                    ),
                },
                {
                    "element": "#guide-hospital-create",
                    "allow_interaction": True,
                    "popover": _usage_site_tour_pop(
                        "新建机构（请操作）",
                        "点<strong>新建</strong>创建医院、院区或科室，并在科室下<strong>登记设备</strong>（名称、型号等）。完成后点「下一步」。",
                    ),
                },
            ],
        },
        {
            "id": "user_list",
            "path": reverse("user_list"),
            "search": "",
            "steps": [
                {
                    "element": "#guide-user-list-header",
                    "popover": _usage_site_tour_pop(
                        "用户管理",
                        "仅显示您本人创建的检测流程岗位账号（检测员、校核员等五类）。",
                    ),
                },
                {
                    "element": "#guide-user-create",
                    "allow_interaction": True,
                    "popover": _usage_site_tour_pop(
                        "创建岗位账号（请操作）",
                        "点<strong>创建用户</strong>，选择检测岗位并保存。至少创建一个检测员账号便于后续派工。完成后点「下一步」。",
                    ),
                },
            ],
        },
        {
            "id": "project_workbench",
            "path": reverse("library_projects"),
            "search": "",
            "steps": [
                {
                    "element": "#guide-project-header",
                    "popover": _usage_site_tour_pop(
                        "项目工作台",
                        "在此新建委托、挂载设备、登记流程岗位并向 App 同步任务。",
                    ),
                },
                {
                    "element": "#guide-project-sidebar",
                    "popover": _usage_site_tour_pop(
                        "项目列表",
                        "左侧树可进入医院科室；点<strong>新建项目</strong>创建委托。",
                    ),
                },
                {
                    "element": "#guide-project-create",
                    "allow_interaction": True,
                    "popover": _usage_site_tour_pop(
                        "新建委托（请操作）",
                        "点<strong>新建项目</strong>，选择委托单位、受检设备与检测类型，提交创建。"
                        "创建成功后<strong>在左侧选中该项目</strong>，再点「下一步」。",
                    ),
                },
            ],
        },
        {
            "id": "project_commission",
            "path": reverse("library_projects"),
            "match_path_only": True,
            "merge_query_from_current": True,
            "merge_query": {"tab": "commission"},
            "require_query_substrings": ["project_id="],
            "steps": [
                {
                    "element": "#guide-project-workbench-tabs",
                    "allow_interaction": True,
                    "popover": _usage_site_tour_pop(
                        "委托立项（请操作）",
                        "请打开<strong>委托立项</strong>标签（若尚未在该页）。",
                    ),
                },
                {
                    "element": "#guide-project-commission",
                    "allow_interaction": True,
                    "popover": _usage_site_tour_pop(
                        "挂载设备与检测类型（请操作）",
                        "勾选受检设备、选择本次检测类型并<strong>保存</strong>。完成后点「下一步」进入人员派工。",
                    ),
                },
            ],
        },
        {
            "id": "project_dispatch",
            "path": reverse("library_projects"),
            "match_path_only": True,
            "merge_query_from_current": True,
            "merge_query": {"tab": "dispatch"},
            "require_query_substrings": ["project_id="],
            "steps": [
                {
                    "element": "#guide-project-dispatch",
                    "allow_interaction": True,
                    "popover": _usage_site_tour_pop(
                        "人员派工（请操作）",
                        "指定项目统筹人，登记各流程岗位参与人；系统会向 App <strong>同步任务</strong>。完成后点「下一步」。",
                    ),
                },
            ],
        },
        {
            "id": "commission_manage",
            "path": reverse("commission_manage"),
            "search": "",
            "steps": [
                {
                    "element": "#guide-commission-header",
                    "popover": _usage_site_tour_pop(
                        "委托管理",
                        "纵览委托完成度与提醒；立项与派工细节仍在项目工作台修改。",
                    ),
                },
            ],
        },
        {
            "id": "file_library",
            "path": reverse("file_library"),
            "search": "?tab=inspection_submit",
            "steps": [
                {
                    "element": "#guide-file-library-tabs",
                    "popover": _usage_site_tour_pop(
                        "文件库",
                        "外业在 App 提交后，资料出现在<strong>检测提交</strong>、<strong>现场记录</strong>、<strong>报告</strong> 等分类；您可在此导出或合并报告。",
                    ),
                },
                {
                    "element": "#guide-file-library-filters",
                    "popover": _usage_site_tour_pop(
                        "筛选与导出",
                        "按项目筛选文件；勾选后可使用页内<strong>导出</strong>、<strong>合并报告</strong>等按钮（以界面为准）。",
                    ),
                },
            ],
        },
        {
            "id": "coord_help_finish",
            "path": help_index,
            "search": "",
            "steps": [
                {
                    "element": "#guide-tour-hero",
                    "popover": _usage_site_tour_pop(
                        "完成练习",
                        "已走完委托统筹主路径。点「完成练习」将删除本次练习登记的新建<strong>项目</strong>；用户账号需您自行决定是否保留。",
                    ),
                },
            ],
        },
    ]
    return {"finish_label": "完成练习", "segments": segments}


def _build_coordinator_workflow_site_tour_manifest(request):
    """统筹创建的流程岗位：说明总览 → 文件库 → 岗位职责说明。"""
    help_index = reverse("coordinator_usage_guide")
    segments: list[dict] = [
        {
            "id": "wf_help_index",
            "path": help_index,
            "search": "",
            "steps": [
                {
                    "element": "#guide-tour-hero",
                    "popover": _usage_site_tour_pop(
                        "导读练习（检测流程岗位）",
                        "您的主要工作在 <strong>App</strong> 完成；本练习带您认识 Web 侧<strong>文件库</strong>与说明文档。"
                        "高亮区域可点击；结束练习不会删除业务数据。",
                    ),
                },
                {
                    "element": "#guide-card-deck",
                    "popover": _usage_site_tour_pop(
                        "阅读建议",
                        "点「下一步」进入<strong>文件库</strong>，查看检测提交与现场记录存放位置。",
                    ),
                },
            ],
        },
        {
            "id": "wf_file_library",
            "path": reverse("file_library"),
            "search": "?tab=site_record",
            "steps": [
                {
                    "element": "#guide-file-library-tabs",
                    "popover": _usage_site_tour_pop(
                        "文件分类",
                        "检测员、校核员常查看<strong>现场记录</strong>与<strong>检测提交</strong>；编制/审核岗位还会接触<strong>报告</strong>。",
                    ),
                },
                {
                    "element": "#guide-file-library-filters",
                    "popover": _usage_site_tour_pop(
                        "列表与筛选",
                        "若列表为空，请确认委托统筹已向您同步任务，且已在 App 提交数据。",
                    ),
                },
            ],
        },
        {
            "id": "wf_help_workflow",
            "path": reverse("coordinator_usage_guide_page", kwargs={"page": "workflow"}),
            "search": "",
            "steps": [
                {
                    "element": "#guide-site-nav",
                    "popover": _usage_site_tour_pop(
                        "岗位职责专篇",
                        "请对照您的岗位角色阅读「检测员 / 校核员 / …」各节，了解 App 与 Web 分工。",
                    ),
                },
                {
                    "element": "#guide-section-root",
                    "popover": _usage_site_tour_pop(
                        "完成导读",
                        "日常请在 App 完成本岗环节；遇权限或派工问题联系<strong>委托统筹</strong>。点「完成练习」结束导读。",
                    ),
                },
            ],
        },
    ]
    return {"finish_label": "完成导读", "segments": segments}


def _menus_for_user(user):
    if library_user_is_commission_coordinator(user):
        # 委托统筹：侧栏固定入口已覆盖业务功能，不展示数据库/角色等动态菜单
        return Menu.objects.none()
    if user.is_superuser:
        return (
            Menu.objects.filter(parent=None, is_visible=True)
            .exclude(name="文件与提取")
            .prefetch_related("children")
        )
    try:
        role = user.profile.role
        return (
            Menu.objects.filter(parent=None, is_visible=True, roles=role)
            .exclude(name="文件与提取")
            .prefetch_related("children")
        )
    except Exception:
        return []


def _build_menu_context_payload(request):
    """可缓存部分（不含 menus QuerySet）。"""
    user = request.user
    role_perm = role_permission_map(user)
    payload = {
        "hide_process_pipeline": (not role_has(user, "perm_process_pipeline"))
        or library_user_has_party_a_demo_restrictions(user),
        "role_perm": role_perm,
        "can_access_instrument_database": library_user_may_access_instrument_database(user),
        "show_library_task_nav": library_user_may_access_task_template_library_nav(user),
        "show_hospital_info_nav": library_user_may_access_hospital_info_nav(user),
        "show_backend_usage_guide": library_user_has_party_a_demo_restrictions(user),
        "show_coordinator_usage_guide": library_user_may_view_coordinator_usage_guide(user),
        "usage_site_tour_manifest": _build_usage_site_tour_manifest(request),
        "usage_workflow_tour_active": tour_is_active(request),
    }
    payload.update(role_ui_context(user))
    return payload


def menu_context(request):
    """动态菜单上下文处理器"""
    user = request.user

    if not user.is_authenticated:
        out = {
            "menus": [],
            "hide_process_pipeline": True,
            "role_perm": {},
            "can_access_instrument_database": False,
            "show_library_task_nav": False,
            "show_hospital_info_nav": False,
            "show_backend_usage_guide": False,
            "show_coordinator_usage_guide": False,
            "usage_site_tour_manifest": None,
            "usage_workflow_tour_active": False,
        }
        out.update(role_ui_context(user))
        return out

    cached = get_cached_menu_context_payload(user)
    if cached is None:
        cached = _build_menu_context_payload(request)
        set_cached_menu_context_payload(user, cached)
    else:
        # 流程练习会话状态随请求变化，不写入长期缓存
        cached = dict(cached)
        cached["usage_workflow_tour_active"] = tour_is_active(request)

    out = dict(cached)
    out["menus"] = _menus_for_user(user)
    return out
