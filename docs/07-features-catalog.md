# 07 功能总览（维护者版）

本文档按**用户可见能力**整理当前 Django 项目的全部功能，便于接手、排障与文档同步。实现细节以代码为准；业务规则专题见文末「专题文档索引」。

**最后整理**：2026-08-20

---

## 1. 系统定位

| 维度 | 说明 |
|------|------|
| 项目名 | `tablet_backend` — 放射检测 / 平板业务后台 |
| Web | 服务端渲染：文件库、项目工作台、模板编辑、委托管理、仪器台账、**报告枢纽**、**评价报告表 F.1**、**评价报告书**、OTA 发版等 |
| API | JWT REST：`/api/v2/`（推荐）、`/api/v1/`（兼容） |
| 客户端 | 平板 App 检测流程；Web 与 API 共用 `apps.core.models` |

---

## 2. Web 功能模块

路由入口：`apps/core/urls.py`；视图主体：`apps/core/views.py`。

### 2.1 认证与首页

| 路径 | 功能 |
|------|------|
| `/login/` | 用户名密码登录，支持单浏览器 Web 会话绑定 |
| `/logout/` | 登出并清除 Web 会话租约 |
| `/` | 仪表盘：用户/角色/菜单/文件库 OCR·JSON 统计 |

### 2.2 系统管理（RBAC）

| 路径 | 功能 |
|------|------|
| `/users/` | 用户列表、创建、编辑、删除；含角色、权限个性化覆盖、文件库配额 |
| `/roles/` | 角色列表、创建、编辑、删除；按三类权限矩阵勾选 |
| `/menus/` | 动态侧栏菜单树：父级、路径、角色可见性 |

### 2.3 使用说明与流程练习

| 路径 | 功能 |
|------|------|
| `/help/usage/` | 后台使用说明首页（限甲方演示/引导账号） |
| `/help/usage/<page>/` | 分章节：overview / project / files / tasks / htmlpdf / records / tips |
| `/help/usage/tour/start/` | 启动 Driver.js 流程练习会话 |
| `/help/usage/tour/finish/` | 结束练习并清理演示数据 |

### 2.4 文件库与项目业务

| 路径 | 功能 |
|------|------|
| `/files/` | **文件库主界面**：按 tab（OCR/JSON/模板/现场记录/报告/附件/检测提交）浏览、上传、筛选、关联项目、**合并报告** |
| `/files/projects/` | **项目工作台**：委托单位树、立项、设备勾选、人员派工、仪器分配、模拟提交 |
| `/files/projects/create-wizard-options/` | 创建立项向导 JSON 选项 |
| `/files/projects/mock-inspection-submit/` | 模拟 App 检测提交（需 `perm_mock_inspection_submit`） |
| `/files/commission-manage/` | **委托管理**：按用户汇总项目/任务，分配/撤回、指定主要负责人 |
| `/files/hospital-info/` | **医院信息管理**：委托单位树、联系人、科室设备、合并报告绑定；详情含**委托销售产品**挂载 |
| `/files/hospital-info/products/` | **产品管理**：销售目录 CRUD、分类/产品线、上下架、默认设备/检测类型、修改日志 |
| `/files/hospital-info/api/equipment/` | 科室设备 CRUD JSON API |
| `/files/hospital-info/api/equipment/history/` | 设备检测历史 JSON API |
| `/files/task-management/` | **任务模板库**：分类树、报告/现场记录模板绑定、默认仪器种类、**绑定历史回溯** |
| `/files/<pk>/preview/` | 在线预览 PDF/图片/JSON/Markdown |
| `/files/<pk>/raw/` | 原始文件流 |
| `/files/<pk>/download/` | 文件下载 |
| `/files/<pk>/delete/` | 单条删除（移入回收站） |
| `/files/batch-delete/` | 批量删除 |
| `/files/temp/<batch_id>/` | 管线/OCR 临时批次结果浏览 |

### 2.5 文档识别与模板编辑器

| 路径 | 功能 |
|------|------|
| `/files/process/` | **文档识别管线**：OCR 文件 → MinerU + Ollama 异步结构化抽取 |
| `/files/htmlpdf/` | HTMLPDF 模板编辑器入口 |
| `/files/htmlpdf/editor/<pk>/` | 打开指定库文件进入画板 |
| `/files/htmlpdf/api/*` | 模板列表、导入/导出 JSON、导出前端 JSON/矩阵 JSON、保存 PDF、红框自动识别、报告-现场字段映射、任务上下文等 |

### 2.6 仪器台账

| 路径 | 功能 |
|------|------|
| `/database/devices/` | **检测仪器台账**：`InstrumentCatalog` 登记、编辑、出库/入库 |

### 2.7 报告流程枢纽

| 路径 | 功能 |
|------|------|
| `/files/hub/site-records/` | 枢纽：现场记录侧 |
| `/files/hub/report-generate/` | 枢纽：报告生成 |
| `/files/hub/review-sign/` | 枢纽：审核签字 |
| `/files/hub/report-download/` | 枢纽：报告下载 |

服务：`workflow_hub_service.py`。一致性：[报告流程枢纽与医院委托工作台数据一致性方案.md](报告流程枢纽与医院委托工作台数据一致性方案.md)。

### 2.8 评价报告表（F.1）

权限：`perm_f1_eval`。详述：[评价报告表-F1功能说明.md](评价报告表-F1功能说明.md)。

| 路径 | 功能 |
|------|------|
| `/files/f1-eval/` | F.1 工作台（项目 / 章节 / 表格 / 附件相册 / 格式 / 生成 PDF） |
| `/files/f1-eval/api/*` | 章节保存、表格 CRUD、Excel 导入导出、图件、封面元数据、附件、生成、项目 CRUD/派工等 |
| `/files/f1-eval/media/*`、`/download/<kind>/` | 附件/资源媒体与 PDF/产物下载 |

### 2.9 评价报告书（LaTeX）

路由：`apps/evaluation_report/urls.py`，挂载前缀 `/evaluation-reports/`。权限：`perm_evaluation_report`。详述：[评价报告书-LaTeX功能说明.md](评价报告书-LaTeX功能说明.md)。

| 路径 | 功能 |
|------|------|
| `/evaluation-reports/` | 报告列表 |
| `/evaluation-reports/create/` | 新建（医院、子类型、模板） |
| `/evaluation-reports/<pk>/evaluation-form/` | ① 评价信息表上传 |
| `/evaluation-reports/<pk>/generate-base/` | ② 生成基础报告 |
| `/evaluation-reports/<pk>/edit/` | ③ 结构化正文编辑（个性化字段蓝底高亮） |
| `/evaluation-reports/<pk>/attachments/` | ④ 附件：选文件预览 → 待上传 → 确认；图片可调旋转 / A3 横置 / 宽度 |
| `/evaluation-reports/<pk>/build/`、`/pdf/` | ⑤ 编译与预览 PDF |
| `/evaluation-reports/<pk>/keywords/` | 项目关键词（项目信息） |
| `/evaluation-reports/templates/`、`.../edit/` | LaTeX 模板库与模板结构化编辑 |
| `/evaluation-reports/keywords/schema/` | 通用关键词映射 |

与「评价报告表」（F.1）不是同一功能。

### 2.10 设置、账号与其它

| 路径 | 功能 |
|------|------|
| `/settings/app-ota/` | Android APK OTA 发版（超管） |
| `/settings/debug/` | 调试相关设置 |
| `/account/profile/`、`/account/signatures/` | 个人资料与签名图 |
| `/help/coordinator/` | 委托协调员帮助 |

### 2.11 Django Admin

| 路径 | 功能 |
|------|------|
| `/admin/` | Django 管理后台（`apps/core/admin.py`、`apps/evaluation_report/admin.py`） |

---

## 3. REST API 功能

挂载：`tablet_backend/urls.py` → `/api/v1/`、`/api/v2/`。**维护以 `urls_v1.py`、`urls_v2.py` 为准**；`apps/api/urls.py` 未被根路由引用。

### 3.1 认证与系统

| 端点 | 功能 |
|------|------|
| `POST /auth/login/` | JWT 登录（access + refresh） |
| `POST /auth/token/refresh/` | 刷新 Token |
| `GET/POST/PUT/PATCH/DELETE /users/` | 用户 CRUD |
| `GET /roles/` | 角色只读列表 |
| `GET /menus/` | 菜单只读列表 |

### 3.2 文件库 / OCR

| 端点 | 功能 |
|------|------|
| `POST /library/files/ocr/` | 上传 OCR 文件并启动异步管线 |
| `GET /library/files/ocr/tasks/<id>/status/` | OCR 任务状态 |
| `POST /library/files/ocr/tasks/<id>/autofill/` | OCR 结果自动回填到 JSON |
| `GET /library/files/<pk>/download/` | 库文件下载 |
| `GET /library/templates/downloadable/` | 可下载模板列表 |
| `POST /library/templates/<pk>/prepare-pdf-download/` | 签发模板 PDF 临时下载 URL |
| `POST /library/templates/temp-pdf-download/` | Word→PDF 临时下载 |
| `POST /library/files/upload-linked/` | 带业务实体关联的文件上传 |

### 3.3 检测流程（v2 推荐）

| 端点 | 功能 |
|------|------|
| `GET /inspections/pending` | 待办项目列表 |
| `GET /inspections/history` | 历史项目列表 |
| `GET /inspections/projects` | 可访问项目列表 |
| `GET /inspections/projects/<id>/tasks` | 项目下任务列表 |
| `GET /inspections/projects/<id>/tasks/<task_no>` | 任务详情 |
| `POST .../start` | 开始检测 |
| `POST .../draft` | 保存草稿 |
| `POST .../submit` | **提交检测数据**（写入 `InspectionSubmission`、生成 PDF） |
| `GET .../export-frontend-json` | 导出前端表单 JSON |
| `POST .../manual-export-report` | 手工触发报告 PDF 导出 |
| `GET/POST .../signatures/<role>` | 签名图下载/上传 |
| `POST .../files/upload` | 任务附件上传 |
| `GET .../files/<category>` | 按分类列文件 |
| `POST .../ocr/upload` + `status` + `autofill` | 任务级 OCR |
| `GET/POST /inspections/<task_no>/*` | **遗留**按 taskNo 直连（平滑迁移用） |

### 3.4 台账 Registry

| 端点 | 功能 |
|------|------|
| `/registry/organizations/` | 受检单位 CRUD |
| `/registry/contacts/` | 业务联系人 CRUD |
| `/registry/devices/` | 业务设备 CRUD |
| `/registry/instruments/` | 仪器主数据**只读** |
| `/registry/cases/` | 检验案件 CRUD |
| `/registry/site-records/` | 现场原始记录 CRUD |
| `/registry/reports/` | 报告 CRUD；`GET .../trace/` 全链路追溯 |

### 3.5 Android OTA（v2）

| 端点 | 功能 |
|------|------|
| `GET /api/v2/app/version` | 查询最新版本与下载信息 |
| `GET /api/v2/app/apk/<filename>` | 下载 APK |

Web 发版：`/settings/app-ota/`。规格：[ANDROID_OTA_UPDATE_SPEC.md](ANDROID_OTA_UPDATE_SPEC.md)。

---

## 4. 核心业务工作流

### 4.1 委托 → 立项 → 派工

1. **医院信息管理**维护 `CommissionOrganization` 树（医院→院区→科室）、联系人、科室设备
2. **项目工作台**创建立项（委托编号 `YY####`），勾选设备与检测类型
3. 同步 `LibraryTask` 绑定、生成 `LibraryTaskAssignment`、人员派工与仪器出库
4. 详见 [WORKFLOW_COMMISSION_DISPATCH.md](WORKFLOW_COMMISSION_DISPATCH.md)

### 4.2 检测提交（App / 模拟）

1. App：`GET projects` → `tasks` → `export-frontend-json`
2. 现场：`start` → `draft` → 附件/OCR/签名/`sectionPhotos`
3. `POST submit` → `InspectionSubmission` + `inspection_submit` JSON → 回填 PDF → 现场记录 + 报告
4. Web 模拟：项目工作台 `mock-inspection-submit` 或 `manage.py mock_inspection_submit`

### 4.3 报告生成与回填

- **自动**：提交时 `inspection_report_make` 按任务模板 bindings 映射回填
- **手工**：`manual-export-report` 或文件库重导
- **判定**：`verdict.rule` 表达式优先 → `verdict_from_criterion` 数值推断 → 现场同行已「不合格」则强制不合格
- **多现场任务**：`report_site_field_map` 按 `siteTaskId` 选取对应提交 payload
- 详见 [报告单项判定规则与逻辑.md](报告单项判定规则与逻辑.md)、[合并报告规则说明.md](合并报告规则说明.md)

### 4.4 模板编辑器（HTMLPDF）

1. 选 PDF / 上传 → 画板划框 / 自动红框 / 表格识别
2. 导出统一模板 JSON → 存 `template` 分类
3. 导出前端 JSON（`frontend_schema_rule_engine`）→ `auxiliary/*_frontend.json`
4. 报告模板配置 `report_site_field_map` 映射现场记录字段
5. 绑定轮换记入 `LibraryTaskTemplateBindingHistory`；任务上下文下可**恢复历史模板**

### 4.5 合并报告

- **入口**：文件库报告 tab，`POST action=merge_reports`（≥2 份报告）
- **实现**：`collect_report_merge_source_rows` → `build_report_merge_overlay` → `merge_report_pdfs_header_toc_sections`
- **绑定**：可挂到 `CommissionOrganization.merged_report_file`

### 4.6 仪器台账

- **登记**：`/database/devices/` 或 Admin
- **模板绑种类**：任务模板库 `bound_instrument_ids`（`bindingMode=kinds`）
- **派工出库**：`instrument_inventory_service` 按 `code` 升序分配
- 详见 [仪器台账出库入库.md](仪器台账出库入库.md)、[任务模板检测仪器绑定与JSON模板.md](任务模板检测仪器绑定与JSON模板.md)

### 4.7 多角色工作流

- `LibraryProjectWorkflowMember`：项目内五岗（检测/校核/编制/审核/签字）
- `InspectionCaseWorkflowState`：SITE_FILL → SITE_REVIEW → REPORT_DRAFT → REPORT_AUDIT → REPORT_SIGN → ISSUED
- 详见 [检测业务多角色与工作流说明.md](检测业务多角色与工作流说明.md)

### 4.8 文档识别管线

- Web `/files/process/` 或 API `library/files/ocr/`
- 链路：PDF → MinerU → MD 清洗 → Ollama 设备 JSON
- 结果写入 `json` 分类 + `LibraryOCRProcessTask` 状态跟踪

### 4.9 报告流程枢纽

- `/files/hub/*` 四页串联现场记录 → 生成 → 审核签字 → 下载
- 与医院委托工作台数据对齐见专题文档

### 4.10 评价报告表 F.1 / 评价报告书

- F.1：`/files/f1-eval/` → 工作区 → `f1_eval_report` 生成 PDF  
- 评价报告书：信息表 → 基础报告 → 结构化编辑 → 附件 → LaTeX 编译  

---

## 5. 权限与角色

来源：`apps/core/library_access.py` — `ROLE_PERMISSION_MATRIX`

### 5.1 权限位

**基础操作**：`perm_manage_users` · `perm_manage_roles` · `perm_manage_menus` · `perm_file_library` · `perm_file_upload` · `perm_file_upload_attachment` · `perm_file_download` · `perm_file_preview` · `perm_file_delete`

**数据范围**：`perm_file_scope_own_only`（仅本人数据）

**流程与业务**：`perm_process_pipeline` · `perm_htmlpdf` · `perm_assign_tasks` · `perm_create_library_project` · `perm_biz_registry`

**评价业务**：`perm_f1_eval`（评价报告表）· `perm_evaluation_report`（评价报告书）

**仅 UserProfile 覆盖**：`perm_library_task_templates_write`（任务模板沙箱自建）、`perm_mock_inspection_submit`（模拟提交）

### 5.2 角色代码

| code | 定位 |
|------|------|
| `super_admin` / `admin` | 全权限 |
| `field_inspector` | 检测员（App，默认仅本人数据） |
| `site_reviewer` | 校核员 |
| `report_author` | 编制人 |
| `report_auditor` | 审核人 |
| `authorized_signatory` | 授权签字人 |
| `app_user` | 旧版 App 用户（兼容） |
| `template_editor` | 模板编辑 |
| `template_tester` | 模板测试 |
| 组织演示角色 | 行政/检测/评价主任等（见 `ensure_org_*`） |

`APP_SIDE_ROLE_CODES`：上述五类检测岗位 + `app_user`；仅这些角色可参与任务分配与检测 API。

---

## 6. 领域模型（摘要）

完整索引见 [04-domain-models-and-services.md](04-domain-models-and-services.md)。

```
身份：Role ↔ UserProfile ↔ User；Menu（树 + M2M roles）

文件库：LibraryFile（分类 tab）↔ LibraryProject ↔ LibraryTask
        LibraryTaskFolder（分类树）
        LibraryTaskTemplateBindingHistory（模板轮换历史）
        LibraryOCRProcessTask

委托：CommissionOrganization → contacts / equipments / equipment_history
      LibraryProjectEquipment（立项设备快照）

检测：InspectionCase → InspectionSubmission / SiteRecord / Report
      InspectionCaseWorkflowState / LibraryProjectWorkflowMember
      CaseReportSignatureRecord（报告签字留痕）

仪器：InstrumentCatalog / InstrumentCheckoutLog
台账：InspectedOrganization / BizContact / BizDevice
签名：UserSignature / UserSignatureEvent
产品：SalesProduct* / LibraryProjectProduct*
评价报告书：EvaluationReport*（独立 app）；F.1 以 media 工作区为主
```

---

## 7. 关键服务层

| 模块 | 职责 |
|------|------|
| `library_access.py` | 权限矩阵、文件库/项目/任务可见范围、模板历史访问 |
| `library_file_service.py` | 上传落盘、SHA256、关联项目/任务、下载 |
| `library_task_template_binding_service.py` | PDF/JSON 模板配对、绑定轮换、历史还原 |
| `htmlpdf_report_mapping_service.py` | 报告↔现场记录字段映射、多现场 payload 选取 |
| `inspection_report_make.py` | 提交 JSON→PDF 回填、仪器合并、判定、合并报告 overlay |
| `frontend_schema_rule_engine.py` | PDF 坐标→`frontend_form_schema/v1` 规则引擎 |
| `instrument_inventory_service.py` | 仪器出库/入库、派工自动分配 |
| `commission_org_service.py` | 委托单位树索引与路径解析 |
| `pdf_merge.py` | 多报告 PDF 合并（封面/目录/叠印） |
| `workflow_hub_service.py` | 报告流程枢纽页数据 |
| `document_pipeline.py` | MinerU + Ollama 文档结构化管线 |

更多见 [04-domain-models-and-services.md](04-domain-models-and-services.md)、[05-utils-and-pipelines.md](05-utils-and-pipelines.md)。

---

## 8. 管理命令

完整表见 [06-scripts-and-admin.md](06-scripts-and-admin.md)。常用：

| 命令 | 用途 |
|------|------|
| `init_data` | 角色与菜单种子 |
| `ensure_party_a_demo` / `ensure_org_directors` / `ensure_org_roles` | 演示与组织角色 |
| `seed_sales_products` / `seed_evaluation_report_templates` | 产品 / 评价报告书模板种子 |
| `backup_database` / `purge_library_trash` / `check_library_media` | 备份与媒体一致性 |
| `migrate_template_storage_layout` 等 | 模板库迁移/修复 |
| `mock_inspection_submit`（api） | 模拟检测提交 |
| `set_jwt_runtime` | JWT 运行时配置 |

---

## 9. 专题文档索引

### 维护者技术文档

| 文档 | 主题 |
|------|------|
| [00-overview.md](00-overview.md) | 项目定位、目录、运行 |
| [01-configuration.md](01-configuration.md) | settings、环境变量 |
| [02-web-ui-core.md](02-web-ui-core.md) | Web 路由与 views |
| [03-rest-api.md](03-rest-api.md) | v1/v2 API、OTA |
| [04-domain-models-and-services.md](04-domain-models-and-services.md) | 模型与服务 |
| [05-utils-and-pipelines.md](05-utils-and-pipelines.md) | utils 与管线 |
| [06-scripts-and-admin.md](06-scripts-and-admin.md) | 管理命令与 Admin |
| [评价报告书-LaTeX功能说明.md](评价报告书-LaTeX功能说明.md) | 评价报告书 LaTeX |
| [评价报告表-F1功能说明.md](评价报告表-F1功能说明.md) | F.1 工作台 |
| [backend_usage_guide.md](backend_usage_guide.md) | 使用说明代码清单 |
| [README.md](README.md) | 接手阅读路径与全索引 |

### 业务与对接文档

| 文档 | 主题 |
|------|------|
| [后台管理系统说明（甲方版）.md](后台管理系统说明（甲方版）.md) | **面向甲方**的后台能力总览 |
| [WORKFLOW_COMMISSION_DISPATCH.md](WORKFLOW_COMMISSION_DISPATCH.md) | 委托→派工→App 链路 |
| [检测业务多角色与工作流说明.md](检测业务多角色与工作流说明.md) | 多角色与流程环节 |
| [报告流程枢纽与医院委托工作台数据一致性方案.md](报告流程枢纽与医院委托工作台数据一致性方案.md) | hub 与工作台一致性 |
| [合并报告规则说明.md](合并报告规则说明.md) | 合并报告规则 |
| [现场记录生成报告流程说明.md](现场记录生成报告流程说明.md) | 现场记录→报告 |
| [医院信息-产品管理说明.md](医院信息-产品管理说明.md) | 销售产品目录 |
| [仪器台账出库入库.md](仪器台账出库入库.md) | 仪器出库入库 |
| [任务模板检测仪器绑定与JSON模板.md](任务模板检测仪器绑定与JSON模板.md) | 仪器绑定与 JSON |
| [FRONTEND_FORM_SCHEMA.md](FRONTEND_FORM_SCHEMA.md) | 前端 JSON 架构 |
| [json公式说明.md](json公式说明.md) | 公式与判定对接 |
| [动态表单后端对齐与前端配合说明.md](动态表单后端对齐与前端配合说明.md) | 动态表达式对齐 |
| [报告单项判定规则与逻辑.md](报告单项判定规则与逻辑.md) | 单项判定回填 |
| [质控拍照照片提交说明.md](质控拍照照片提交说明.md) | sectionPhotos 提交 |
| [前端检测仪器两栏位改版说明.md](前端检测仪器两栏位改版说明.md) | 质控/防护仪器分 scope |
| [ANDROID_OTA_UPDATE_SPEC.md](ANDROID_OTA_UPDATE_SPEC.md) | App OTA |
| [打包与新设备安装说明.md](打包与新设备安装说明.md) | 打包与新机安装 |
| [部署流程-从零安装与源码保护评估.md](部署流程-从零安装与源码保护评估.md) | 从零安装与交付 |
| [组织主任账号说明.md](组织主任账号说明.md) | 演示账号 |
| [ADMIN_PERFORMANCE_OPTIMIZATION.md](ADMIN_PERFORMANCE_OPTIMIZATION.md) | 性能优化路线图 |

---

## 10. 近期重要行为（摘录）

| 主题 | 行为 |
|------|------|
| 三线报告并存 | 检测 PDF / F.1 表 / 评价报告书；权限与 URL 分离 |
| 评价报告书嵌入主站 | 信息表→基础报告→结构化编辑→附件→编译；`perm_evaluation_report` |
| 附件显示选项 | `page_mode` / `rotate` / `width_percent`；选文件即时预览 |
| 个性化字段高亮 | 编辑页 `.se-kw` 蓝底标记展开宏 |
| 报告流程枢纽 | `/files/hub/*` 四阶段入口 |
| App OTA | `/settings/app-ota/` + `/api/v2/app/*` |
| 模板历史恢复 | 任务上下文下历史 JSON 鉴权与编辑器下拉一致 |
| instrument_select | 仅第三章「主要检测仪器」栏位识别为仪器选择 |
| 报告字段映射 | 多现场按 `siteTaskId`；栏位 id 与 placeholder 相同不合并数值 token |
| 单项判定 | 现场同行已「不合格」时强制 `failLabel` |
