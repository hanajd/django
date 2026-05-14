"""
上下文处理器
用于在模板中提供动态菜单等全局数据
"""

from django.urls import reverse

from apps.core.library_access import (
    library_user_has_party_a_demo_restrictions,
    library_user_may_access_task_template_library_nav,
    role_has,
    role_permission_map,
    role_ui_context,
)
from apps.core.models import Menu
from apps.core.usage_workflow_tour import tour_is_active


def _usage_site_tour_pop(title: str, description: str, side: str = "bottom", align: str = "start") -> dict:
    return {"title": title, "description": description, "side": side, "align": align}


def _build_usage_site_tour_manifest(request):
    """
    跨页「流程练习」：按使用说明顺序跳转真实页面，关键步骤允许直接操作界面；
    练习会话内新建的项目、任务模板及模板库中的版式数据，会在点「完成练习」时由系统清理。
    仅面向可查看后台使用说明的引导账号；无文件库权限时仅保留说明总览段。
    """
    user = request.user
    if not library_user_has_party_a_demo_restrictions(user):
        return None

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
                        "展开「新建项目」，填写<strong>项目名称</strong>（编码可留空），点<strong>创建</strong>。"
                        "创建成功后，一般会停留在当前项目并自动打开上方的「任务与模板」页签；若没有，请手动点该页签。确认后点「下一步」前往文件库上传模板。",
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


def menu_context(request):
    """动态菜单上下文处理器"""
    user = request.user

    if not user.is_authenticated:
        out = {
            "menus": [],
            "hide_process_pipeline": True,
            "role_perm": {},
            "show_library_task_nav": False,
            "show_backend_usage_guide": False,
            "usage_site_tour_manifest": None,
            "usage_workflow_tour_active": False,
        }
        out.update(role_ui_context(user))
        return out

    role_perm = role_permission_map(user)

    # 超级管理员可以访问所有菜单
    if user.is_superuser:
        menus = Menu.objects.filter(
            parent=None,
            is_visible=True,
        ).exclude(name="文件与提取").prefetch_related("children")
    else:
        try:
            role = user.profile.role
            menus = Menu.objects.filter(
                parent=None,
                is_visible=True,
                roles=role,
            ).exclude(name="文件与提取").prefetch_related("children")
        except Exception:
            menus = []

    # 文件库 / OCR 处理由 base 模板固定展示，此处排除同名动态菜单以免重复。
    # 演示类账号在文件库「OCR」分类已可走 OCR，侧栏不再单独展示本入口以免重复。
    out = {
        "menus": menus,
        "hide_process_pipeline": (not role_has(user, "perm_process_pipeline"))
        or library_user_has_party_a_demo_restrictions(user),
        "role_perm": role_perm,
        "show_library_task_nav": library_user_may_access_task_template_library_nav(user),
        "show_backend_usage_guide": library_user_has_party_a_demo_restrictions(user),
        "usage_site_tour_manifest": _build_usage_site_tour_manifest(request),
        "usage_workflow_tour_active": tour_is_active(request),
    }
    out.update(role_ui_context(user))
    return out
