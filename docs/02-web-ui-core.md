# 02 Web 后台（apps.core）

## 1. 路由入口

| 文件 | 说明 |
|------|------|
| `apps/core/urls.py` | 全部 Web 路径：`login`、`dashboard`、RBAC、文件库、项目工作台、任务模板库、HTMLPDF 与 API、管线预览、使用说明与流程练习等 |

根站点将 `''` 指向该文件，故业务路径多为 **`/files/...`、`/users/...`** 等（无前缀）。

## 2. 视图主体

| 文件 | 说明 |
|------|------|
| `apps/core/views.py` | 体量大：聚合了认证、仪表盘、用户/角色/菜单 CRUD、文件库、项目工作台、任务模板、HTMLPDF 编辑器与 JSON API、管线入口、使用说明视图、流程练习 start/finish 等 |

**维护建议**：新功能若继续膨胀，可按域拆为 `views/` 包（需同步调整 import 与测试）；当前为单文件历史结构。

## 3. 权限与访问控制

| 文件 | 说明 |
|------|------|
| `apps/core/library_access.py` | 文件库、项目、任务模板、模板编辑器、甲方演示账号等细粒度判断函数 |
| `apps/core/permissions.py` | DRF/权限相关（若 Web 与 API 共用逻辑，注意两处引用） |

视图中常见模式：`_require_perm(request, "perm_...")` 未通过则重定向或 JSON 403。

## 4. 业务服务层（与 views 强相关）

| 文件 | 说明 |
|------|------|
| `apps/core/library_file_service.py` | 上传、关联项目/任务、下载响应、安全文件名等 |
| `apps/core/htmlpdf_service.py` | 模板 PDF/JSON 路径、解析、与编辑器相关的服务端逻辑 |
| `apps/core/pipeline_service.py` | 文档处理管线编排、与 `utils/document_pipeline`、MinerU、临时目录协作 |
| `apps/core/workflow_service.py` | 项目检测流程成员与状态等（与 `InspectionCaseWorkflowState` 等模型配合） |
| `apps/core/library_test_account.py` | 测试/演示账号辅助逻辑 |

## 5. 模板（`templates/`）

| 区域 | 说明 |
|------|------|
| `templates/base.html` | 主布局：侧栏菜单、顶栏、消息、`usage_site_tour_manifest` + Driver、使用说明入口 |
| `templates/core/*.html` | 各业务页：文件库、项目、任务管理、HTMLPDF 等 |
| `templates/core/guide/*` | 后台使用说明（见 [backend_usage_guide.md](backend_usage_guide.md)） |

## 6. 流程练习与演示

| 文件 | 说明 |
|------|------|
| `apps/core/usage_workflow_tour.py` | 练习会话、登记新建实体、结束时删除 |
| `apps/core/context_processors.py` | `_build_usage_site_tour_manifest`：跨页步骤与锚点 |
| `templates/core/guide/partials/usage_site_tour_runner.html` | 前端 Driver 与 `/help/usage/tour/*` 交互 |

业务页上 `id="guide-*"` 锚点与 manifest 中 `element` 需保持一致。

## 7. 其它核心文件

| 文件 | 说明 |
|------|------|
| `apps/core/models.py` | 全站主数据模型（见 [04-domain-models-and-services.md](04-domain-models-and-services.md)） |
| `apps/core/admin.py` | Django Admin 注册 |
| `apps/core/signals.py` | 信号 |
| `apps/core/serializers*.py` | 若 Web 或其它模块序列化复用 |
