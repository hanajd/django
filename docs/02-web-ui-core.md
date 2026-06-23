# 02 Web 后台（apps.core）

## 1. 路由入口

| 文件 | 说明 |
|------|------|
| `apps/core/urls.py` | 全部 Web 路径：`login`、`dashboard`、RBAC、文件库、项目工作台、任务模板库、HTMLPDF 与 API、管线预览、使用说明与流程练习等 |

根站点将 `''` 指向该文件，故业务路径多为 **`/files/...`、`/users/...`** 等（无前缀）。

完整功能清单见 [07-features-catalog.md](07-features-catalog.md) §2。

## 2. 功能域与路由一览

### 2.1 认证与首页

| 路径 | 视图 | 说明 |
|------|------|------|
| `/login/` | `login_view` | 登录 |
| `/logout/` | `logout_view` | 登出 |
| `/` | `dashboard` | 仪表盘统计 |

### 2.2 系统管理

| 路径 | 说明 |
|------|------|
| `/users/`、`/users/create/`、`/users/<id>/edit/`、`/users/<id>/delete/` | 用户 CRUD |
| `/roles/`、`/roles/create/`、`/roles/<id>/edit/`、`/roles/<id>/delete/` | 角色 CRUD |
| `/menus/`、`/menus/create/`、`/menus/<id>/edit/`、`/menus/<id>/delete/` | 菜单 CRUD |

### 2.3 使用说明

| 路径 | 说明 |
|------|------|
| `/help/usage/`、`/help/usage/<page>/` | 分章节使用指南 |
| `/help/usage/tour/start/`、`/help/usage/tour/finish/` | Driver.js 流程练习 |

### 2.4 文件库与项目

| 路径 | 说明 |
|------|------|
| `/files/` | 文件库主界面（含合并报告） |
| `/files/projects/` | 项目工作台 |
| `/files/projects/create-wizard-options/` | 立项向导选项 API |
| `/files/projects/mock-inspection-submit/` | 模拟 App 提交 |
| `/files/commission-manage/` | 委托管理 |
| `/files/hospital-info/` | 医院信息管理 |
| `/files/hospital-info/api/equipment/` | 科室设备 CRUD API |
| `/files/hospital-info/api/equipment/history/` | 设备检测历史 API |
| `/files/task-management/` | 任务模板库（含绑定历史恢复） |
| `/files/<pk>/preview/`、`/raw/`、`/download/` | 预览与下载 |
| `/files/<pk>/delete/`、`/files/batch-delete/` | 删除（回收站） |
| `/files/temp/<batch_id>/` | OCR/管线临时批次 |

### 2.5 文档识别与模板编辑

| 路径 | 说明 |
|------|------|
| `/files/process/` | 文档识别管线 |
| `/static/preview/<name>` | 管线预览静态图 |
| `/files/htmlpdf/` | 模板编辑器入口 |
| `/files/htmlpdf/editor/<pk>/` | 打开编辑器画板 |
| `/files/htmlpdf/template/<pk>/` | 模板 PDF 二进制 |
| `/files/htmlpdf/api/*` | 编辑器后端 API（导入/导出 JSON、红框识别、报告-现场映射等） |

### 2.6 仪器台账

| 路径 | 说明 |
|------|------|
| `/database/devices/` | 检测仪器台账（`InstrumentCatalog`） |

## 3. 视图主体

| 文件 | 说明 |
|------|------|
| `apps/core/views.py` | 体量大：聚合了认证、仪表盘、用户/角色/菜单 CRUD、文件库、项目工作台、任务模板、HTMLPDF 编辑器与 JSON API、管线入口、使用说明视图、流程练习 start/finish 等 |

**维护建议**：新功能若继续膨胀，可按域拆为 `views/` 包（需同步调整 import 与测试）；当前为单文件历史结构。

## 4. 权限与访问控制

| 文件 | 说明 |
|------|------|
| `apps/core/library_access.py` | 文件库、项目、任务模板、模板编辑器、模板历史、甲方演示账号等细粒度判断 |
| `apps/core/permissions.py` | DRF/权限相关（若 Web 与 API 共用逻辑，注意两处引用） |

视图中常见模式：`_require_perm(request, "perm_...")` 未通过则重定向或 JSON 403。

角色与权限矩阵详见 [07-features-catalog.md](07-features-catalog.md) §5。

## 5. 业务服务层（与 views 强相关）

| 文件 | 说明 |
|------|------|
| `library_file_service.py` | 上传、关联项目/任务、下载响应、安全文件名等 |
| `library_folder_service.py` | 文件库/工作台文件夹式导航树 |
| `library_task_folder_service.py` | 任务模板库三级导航 |
| `library_task_template_binding_service.py` | PDF/JSON 模板配对、绑定轮换、历史还原 |
| `htmlpdf_service.py` | 模板 PDF/JSON 路径、解析、编辑器服务端逻辑 |
| `htmlpdf_report_mapping_service.py` | 报告↔现场记录字段映射 |
| `pipeline_service.py` | 文档处理管线编排 |
| `workflow_service.py` | 项目检测流程成员与状态 |
| `commission_org_service.py` | 委托单位树索引 |
| `commission_management_service.py` | 委托管理页数据 |
| `hospital_info_service.py` | 医院信息 CRUD、合并报告 |
| `instrument_inventory_service.py` | 仪器出库/入库、派工分配 |
| `project_equipment_service.py` | 项目工作台设备中心立项 |
| `task_template_ui_service.py` | 任务库编辑模式 session |
| `template_storage_service.py` | 模板分层磁盘布局 |
| `db_backup_service.py` | SQLite 在线备份/恢复 |
| `library_test_account.py` | 测试/演示账号辅助 |

## 6. 模板（`templates/`）

| 区域 | 说明 |
|------|------|
| `templates/base.html` | 主布局：侧栏菜单、顶栏、消息、`usage_site_tour_manifest` + Driver、使用说明入口 |
| `templates/core/*.html` | 各业务页：文件库、项目、任务管理、HTMLPDF、委托管理、医院信息、仪器台账等 |
| `templates/core/guide/*` | 后台使用说明（见 [backend_usage_guide.md](backend_usage_guide.md)） |
| `htmlpdf/templates/` | 模板编辑器前端 |

## 7. 流程练习与演示

| 文件 | 说明 |
|------|------|
| `apps/core/usage_workflow_tour.py` | 练习会话、登记新建实体、结束时删除 |
| `apps/core/context_processors.py` | `_build_usage_site_tour_manifest`：跨页步骤与锚点 |
| `templates/core/guide/partials/usage_site_tour_runner.html` | 前端 Driver 与 `/help/usage/tour/*` 交互 |

业务页上 `id="guide-*"` 锚点与 manifest 中 `element` 需保持一致。

## 8. 其它核心文件

| 文件 | 说明 |
|------|------|
| `apps/core/models.py` | 全站主数据模型（见 [04-domain-models-and-services.md](04-domain-models-and-services.md)） |
| `apps/core/admin.py` | Django Admin 注册 |
| `apps/core/signals.py` | 信号 |
| `apps/core/serializers*.py` | 若 Web 或其它模块序列化复用 |
