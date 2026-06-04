# 后台使用说明（Help / Usage Guide）相关代码说明

> 全项目维护文档索引见 [README.md](README.md)。

本文档说明「后台系统使用说明」功能在项目中的**文件清单**、**路由与数据流**、**可见性条件**、以及**流程练习（跨页导读）**的关联实现，便于维护与扩展。

---

## 1. 功能概述

- **入口 URL**：`/help/usage/`（总览）、`/help/usage/<page>/`（各专题分页，如 `project`、`files`）。
- **视图**：`backend_usage_guide` 根据 `page` 渲染 `core/guide/index.html` 或对应专题模板。
- **权限**：由 `library_user_has_party_a_demo_restrictions` 等控制；未开放账号访问会被重定向并提示（见 `views.backend_usage_guide`）。
- **全局展示**：侧栏与顶栏「后台系统使用说明」链接由上下文变量 `show_backend_usage_guide` 控制（见 `context_processors.menu_context`）。
- **流程练习**：在引导账号下，通过 `usage_site_tour_manifest` + Driver.js 跨真实业务页高亮；练习会话与清理见 `usage_workflow_tour` 及 `help/usage/tour/start|finish/`。

---

## 2. 相关文件清单（按职责）

### 2.1 路由

| 文件 | 说明 |
|------|------|
| `apps/core/urls.py` | `backend_usage_guide`、`backend_usage_guide_page`；以及 `usage_workflow_tour_start`、`usage_workflow_tour_finish`（流程练习 API）。 |

### 2.2 视图与业务逻辑

| 文件 | 说明 |
|------|------|
| `apps/core/views.py` | `_GUIDE_USAGE_SECTIONS` / `_GUIDE_USAGE_LOOKUP`：分页 slug 与模板路径映射；`backend_usage_guide`：渲染说明页；`usage_workflow_tour_start` / `usage_workflow_tour_finish`：练习会话开始与结束清理。 |
| `apps/core/library_access.py` | `library_user_has_party_a_demo_restrictions`：是否视为「引导/演示」账号从而可使用说明与流程练习等（与产品策略一致）。 |
| `apps/core/usage_workflow_tour.py` | 练习会话 session 键、登记新建项目/任务/模板文件 ID、`tour_cleanup_and_clear_session` 等。 |
| `apps/core/context_processors.py` | `menu_context` 注入 `show_backend_usage_guide`、`usage_site_tour_manifest`、`usage_workflow_tour_active`；`_build_usage_site_tour_manifest` 构建跨页导读 JSON。 |

### 2.3 模板 — 说明站主体（`templates/core/guide/`）

| 文件 | 说明 |
|------|------|
| `templates/core/guide/index.html` | 使用说明**总览**（卡片入口、流程练习按钮）。 |
| `templates/core/guide/section_base.html` | 各专题页公共外壳：图例、导航、正文插槽。 |
| `templates/core/guide/overview.html` | 专题：整体工作顺序。 |
| `templates/core/guide/project.html` | 专题：项目工作台。 |
| `templates/core/guide/files.html` | 专题：文件库。 |
| `templates/core/guide/tasks.html` | 专题：任务模板库。 |
| `templates/core/guide/htmlpdf.html` | 专题：模板编辑器。 |
| `templates/core/guide/records.html` | 专题：现场记录与报告。 |
| `templates/core/guide/tips.html` | 专题：常见情况与排查。 |
| `templates/core/guide/partials/nav.html` | 分页目录导航（含 `guide-site-nav` 等锚点）。 |
| `templates/core/guide/partials/pager.html` | 上/下一篇分页链接。 |
| `templates/core/guide/partials/legend.html` | 界面用语图例。 |
| `templates/core/guide/partials/ui_styles.html` | 说明站内样式片段。 |
| `templates/core/guide/partials/tour_driver_theme.html` | Driver.js 弹层主题（与流程练习共用）。 |
| `templates/core/guide/partials/usage_site_tour_runner.html` | 流程练习前端：读取 manifest、Driver 生命周期、POST start/finish、分段 URL 匹配等。 |

### 2.4 全局布局与外部依赖

| 文件 | 说明 |
|------|------|
| `templates/base.html` | 条件加载 Driver CSS/JS、`json_script` 输出 `usage_site_tour_manifest`、`usage_site_tour_runner`；顶栏/侧栏说明链接；`data-usage-workflow-tour` 标记练习会话。 |
| CDN | `driver.js` / `driver.css`（版本以 `base.html` 中引用为准）。 |

### 2.5 流程练习锚点（业务页上的 `id="guide-*"`）

导读 manifest 中的 `element` 选择器指向这些 DOM，修改布局时需同步检查。

| 文件 | 典型锚点 |
|------|----------|
| `templates/core/library_projects.html` | `guide-project-header`、`guide-project-sidebar`、`guide-project-create`、`guide-project-workbench-tabs`、`guide-project-bind-workspace` |
| `templates/core/file_library.html` | `guide-file-library-tabs`、`guide-file-library-filters`、`guide-file-library-template-upload` |
| `templates/core/library_task_management.html` | `guide-task-intro`、`guide-task-sidebar`、`guide-task-new-form`、`guide-task-mgmt-tabs`、`guide-task-template-bind-panel` |
| `templates/core/htmlpdf_files.html` | `guide-htmlpdf-entry` |

### 2.6 练习数据登记（与说明弱相关但同一产品能力）

在 `views.py` 中，创建项目/任务、模板 JSON 保存、模板分类上传等路径会调用 `usage_workflow_tour.register_tour_*`，便于 `finish` 时删除练习数据（具体以当前 `views.py` 实现为准）。

---

## 3. 数据流（简图）

```
用户请求任意已登录页
    → Django 加载 context_processors.menu_context
        → show_backend_usage_guide（是否显示说明入口）
        → usage_site_tour_manifest（引导账号下的跨页练习 JSON，否则为 null）
        → usage_workflow_tour_active（当前 session 是否在练习中）

用户访问 /help/usage/ 或 /help/usage/<page>/
    → urls.py → views.backend_usage_guide
        → 渲染 core/guide/index.html 或 _GUIDE_USAGE_LOOKUP 中对应模板
```

流程练习：

```
前端 UsageSiteTour.start()
    → POST /help/usage/tour/start/（开启 session 登记）
    → 按 manifest.segments 跳转真实 URL + Driver 高亮
结束 / 关闭 / 最后一段完成
    → POST /help/usage/tour/finish/（按登记删除练习数据并清 session）
```

---

## 4. 扩展说明：新增一篇说明专题

1. 在 `apps/core/views.py` 的 `_GUIDE_USAGE_SECTIONS` 中增加一项：`(slug, 标题, "core/guide/你的模板.html")`。
2. 新建 `templates/core/guide/<slug>.html`，建议 `{% extends "core/guide/section_base.html" %}`，在 `{% block guide_section %}` 中写正文。
3. 如需总览卡片入口，在 `templates/core/guide/index.html` 中增加对应 `article` 与 `backend_usage_guide_page` 链接。
4. `partials/nav.html` 使用视图传入的 `guide_nav_items` 循环生成目录，一般**无需**改 nav（除非要调整顺序或样式）。

---

## 5. 扩展说明：调整流程练习路线

1. 修改 `apps/core/context_processors.py` 中 `_build_usage_site_tour_manifest` 的 `segments` / `steps`（`path`、`search`、`match_path_only`、`require_query_*`、`merge_query_from_current` 等）。
2. 若新增步骤依赖新的页面锚点，在对应业务模板中增加 `id="guide-..."`，并与 manifest 中 `element` 一致。
3. 若新步骤会产生需清理的数据，在对应 `views.py` 写库路径调用 `register_tour_project` / `register_tour_task` / `register_tour_library_file`。
4. 若 Runner 需新 URL 行为，编辑 `templates/core/guide/partials/usage_site_tour_runner.html`。

---

## 6. 小结

| 类型 | 主要位置 |
|------|----------|
| URL | `apps/core/urls.py` |
| 页面逻辑 | `apps/core/views.py` |
| 谁可见 | `apps/core/library_access.py` + `context_processors.py` |
| 说明正文 | `templates/core/guide/**/*.html` |
| 流程练习 | `context_processors.py`（manifest）+ `usage_workflow_tour.py` + `usage_site_tour_runner.html` + `base.html` + 业务页 `guide-*` 锚点 |

如需将本文纳入版本库以外的 Wiki，可直接复制本 Markdown 内容。
