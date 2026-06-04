# 后台管理界面性能优化路线图

## 背景

当前后台为 **Django 服务端模板 + 整页刷新**：每次点击侧栏菜单都会重新请求后端、渲染 `base.html`、执行全局 `menu_context`、浏览器重新解析资源。体感慢的主要来源通常不是「动画不丝滑」，而是上述全量重复工作。

## 优化优先级（推荐实施顺序）

### 1. 去掉 Tailwind CDN 运行时（高优先级 · 已落地）

- **问题**：`templates/base.html` 每页加载 `https://cdn.tailwindcss.com`，在浏览器端实时编译 Tailwind，整页跳转时 CPU 与首屏解析成本明显。
- **方案**：改为构建好的本地 CSS，例如 `static/css/admin.css`；图标库 Remix Icon 本地化到 `static/vendor/remixicon/` 并长期缓存。
- **状态**：已提供 `static/css/admin-input.css` + 构建产物 `admin.css`（可用 `scripts/build_admin_css.sh` 重新生成）。

### 2. 局部刷新（中优先级 · 已做轻量版）

- **问题**：侧栏、顶栏每次整页重建。
- **方案**：HTMX / Turbo / PJAX 渐进改造，仅替换主内容区。
- **状态**：已在 `static/js/admin-pjax.js` 用原生 `fetch` + `DOMParser` 实现**无第三方库**的局部导航（侧栏同站内链接、表单 GET 链接触发）；复杂 POST/下载/外链仍走整页。

### 3. 缓存全局菜单与权限上下文（高优先级 · 已落地）

- **问题**：`apps/core/context_processors.py` 的 `menu_context` 每请求执行，含权限矩阵、菜单查询、流程练习 manifest 等。
- **方案**：按用户/角色缓存 `role_perm`、`role_ui`、导航开关、`usage_site_tour_manifest`；菜单树仍每请求一次查询（带 `prefetch_related`，成本低）。角色或菜单变更时调用 `invalidate_menu_context_cache()`。
- **配置**：`MENU_CONTEXT_CACHE_TIMEOUT`（秒，默认 300），`CACHES` 使用进程内 LocMem。

### 4. 重页面按需加载（中优先级 · 待迭代）

- **问题**：`file_library`、`library_projects`、`library_task_management` 首屏组装大量树与统计。
- **方案**：首屏只加载当前 tab/目录，其余 tab、树节点、统计用 JSON 接口懒加载。
- **状态**：文档规划；未在本轮大改视图，避免影响业务。

### 5. 请求耗时与 SQL 计数（高优先级 · 已落地）

- **方案**：`apps/core/middleware.RequestStatsMiddleware`，环境变量 `ADMIN_REQUEST_STATS=1` 或 `DEBUG=True` 时，对 HTML 请求在响应头写入 `X-Request-Duration-Ms`、`X-DB-Query-Count`，并写入 `logs/request_stats.log`。
- **重点页面**：`/files/`、`/files/projects/`、`/files/task-management/`。

### 6. 生产运行配置（部署阶段）

- **现状**：`tablet_backend/settings.py` 中 `DEBUG = True`，数据库为 SQLite。
- **建议**：生产 `DEBUG=False`，Gunicorn/Uvicorn + Nginx，数据库换 PostgreSQL/MySQL，常用筛选字段加索引。

## 最推荐路线（成本 / 收益）

1. **先做 1 + 3 + 5**（成本低、见效快）——本轮已实现。
2. **再做 2 的局部刷新深化**（切界面「丝滑」的关键体验）。
3. **然后做 4 + 6**（数据量变大后的结构性优化）。

## 运维说明

- 修改菜单/角色权限后若侧栏未更新：重启进程或调用 `python manage.py shell` → `from apps.core.menu_context_cache import invalidate_menu_context_cache; invalidate_menu_context_cache()`。
- 重新构建 Tailwind：`bash scripts/build_admin_css.sh`（需网络拉取 `npx tailwindcss`，不写入 `package.json` 依赖）。
