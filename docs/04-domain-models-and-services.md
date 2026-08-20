# 04 领域模型与服务（apps.core）

## 1. 模型一览（`apps/core/models.py`）

以下为**名称级**索引，字段与约束以模型定义及 `migrations/` 为准。

### 1.1 身份与导航

| 模型 | 业务含义 |
|------|----------|
| `Role` | 角色及默认权限位（含文件库、管线、HTMLPDF、台账、`perm_f1_eval`、`perm_evaluation_report` 等） |
| `UserProfile` | 用户扩展：角色、`perm_overrides` 个性化、文件库配额 |
| `UserInviteToken` | 邀请注册/入职用令牌 |
| `Menu` | 动态菜单树，M2M `roles` |

### 1.2 文件库体系

| 模型 | 业务含义 |
|------|----------|
| `LibraryFile` | 文件库元数据；分类：upload/json/template/site_record/report/attachment/inspection_submit/temp |
| `LibraryTask` | 检测任务模板；`output_target`：现场记录 / 报告；`report_source_tasks`；`bound_instrument_ids` |
| `LibraryTaskFolder` | 任务模板库分类树（检测类型 / 设备类型） |
| `LibraryTaskAssignment` | 任务模板分配给用户（App 任务派发） |
| `LibraryOCRProcessTask` | OCR 异步任务状态 |
| `LibraryProject` | 检测项目工作台；`commission_org`、`assigned_instrument_ids`、`primary_responsible` |
| `LibraryProjectUserRevocation` | 项目级编辑权限撤销 |
| `LibraryFileProject` / `LibraryFileTask` | 文件与项目、与任务模板的 M2M 关联 |
| `LibraryTaskTemplateBindingHistory` | 模板 PDF/JSON 轮换历史（支持回溯恢复） |

### 1.3 委托单位（Commission）

| 模型 | 业务含义 |
|------|----------|
| `CommissionOrganization` | 委托单位树（hospital → campus → department）；可绑 `merged_report_file` |
| `CommissionOrgContact` | 委托单位联系人 |
| `CommissionOrgEquipment` | 科室设备；可绑 `report_task` / `report_file` |
| `CommissionOrgEquipmentHistory` | 设备检测历史 |
| `LibraryProjectEquipment` | 项目立项时勾选的科室设备快照 |

### 1.4 台账与检测提交

| 模型 | 业务含义 |
|------|----------|
| `InspectedOrganization` / `BizContact` / `BizDevice` | 台账：受检单位、联系人、设备 |
| `InspectionCase` | 检验案件；关联 `library_project`、现场记录、报告、提交 |
| `SiteRecord` / `Report` | 台账侧现场记录与报告 |
| `InspectionSubmission` | 检测提交实例（`task_no` 唯一）；JSON 分段 + 签名 |
| `InspectionSubmissionInstrument` | 提交与仪器快照 |
| `LibraryProjectWorkflowMember` | 项目流程五岗成员 |
| `InspectionCaseWorkflowState` | 案件流程状态（SITE_FILL → … → ISSUED） |
| `CaseReportSignatureRecord` | 报告编制/审核/授权签字叠印留痕（可按导出版本分槽） |

### 1.5 仪器台账

| 模型 | 业务含义 |
|------|----------|
| `InstrumentCatalog` | 仪器主数据；`code` 全局唯一；`checkout_project_ids` |
| `InstrumentCheckoutLog` | 出库/入库审计流水 |

### 1.6 签名、操作日志与销售产品

| 模型 | 业务含义 |
|------|----------|
| `UserSignature` / `UserSignatureEvent` | 用户签名图与变更事件（账号页 `/account/signatures/`） |
| `BizOperationLog` | 业务操作审计日志 |
| `SalesProductCategory` / `SalesProductLine` / `SalesProduct` | 销售产品目录（分类 / 产品线 / 产品） |
| `LibraryProjectProduct` / `LibraryProjectProductEquipment` | 项目挂载销售产品及关联设备 |

产品管理页：`/files/hospital-info/products/`。详见 [医院信息-产品管理说明.md](医院信息-产品管理说明.md)。

### 1.7 评价报告书（`apps/evaluation_report/models.py`）

| 模型 | 业务含义 |
|------|----------|
| `EvaluationLatexTemplate` | 内置/上传 LaTeX 模板 |
| `EvaluationReport` | 评价报告书实例与工作区、输出 PDF |
| `EvaluationKeywordSchema` / `EvaluationReportKeyword` | 通用映射与项目关键词 |
| `EvaluationReportUpload` / `EvaluationReportUploadFile` | 材料槽位与附件文件；后者含 `page_mode` / `rotate` / `width_percent` |

详见 [评价报告书-LaTeX功能说明.md](评价报告书-LaTeX功能说明.md)。

> **F.1 评价报告表**：主站数据以 `media/f1_eval/` 工作区文件为主，**不在** `models.py` 建完整 ORM；见 [评价报告表-F1功能说明.md](评价报告表-F1功能说明.md)。

### 1.8 关系示意

```
LibraryProject ──M2M── LibraryTask
      │                    ├── output_target: site_record | report
      │                    ├── report_source_tasks
      │                    └── bound_instrument_ids
      ├── commission_org → CommissionOrganization
      ├── assigned_instrument_ids
      └── workflow_members → LibraryProjectWorkflowMember

LibraryFile ──M2M── projects / library_tasks
      └── link_entity + link_object_id → 案件/记录/报告

InspectionCase → InspectionSubmission / SiteRecord / Report
               → InspectionCaseWorkflowState
```

## 2. 与 API 台账的对应

`registry_views` 中 ViewSet 多与上述 `Inspected*`、`Biz*`、`SiteRecord`、`Report`、`InspectionCase`、`InstrumentCatalog` 等对应；命名空间以路由 `registry/*` 为准。

## 3. 核心服务文件

### 3.1 权限与文件库

| 文件 | 职责 |
|------|------|
| `library_access.py` | 权限矩阵、角色默认权限、文件库/项目/任务可见范围、模板历史访问 |
| `library_file_service.py` | 文件保存、关联、下载、submit 批次路径 |
| `library_folder_service.py` | 文件库/工作台文件夹式导航树 |
| `library_task_folder_service.py` | 任务模板库三级导航 |
| `library_task_template_binding_service.py` | PDF/JSON 模板配对、绑定轮换、历史还原 |
| `template_storage_service.py` | 模板分层磁盘布局 `templates/{检测类型}/{设备}/report\|site/{code}/current\|history` |

### 3.2 模板编辑与报告映射

| 文件 | 职责 |
|------|------|
| `htmlpdf_service.py` | 模板编辑器后端 |
| `htmlpdf_report_mapping_service.py` | 报告↔现场记录字段映射、多现场 payload 选取 |
| `task_template_ui_service.py` | 任务库编辑模式 session、文件分组 |

### 3.3 项目与委托

| 文件 | 职责 |
|------|------|
| `project_equipment_service.py` | 项目工作台设备中心立项 |
| `equipment_device_type_service.py` | 11 类设备类型与任务库文件夹匹配 |
| `commission_org_service.py` | 委托单位树索引、路径解析 |
| `commission_management_service.py` | 委托管理页数据 |
| `hospital_info_service.py` | 医院信息 CRUD、合并报告、检测历史 |

### 3.4 仪器与流程

| 文件 | 职责 |
|------|------|
| `instrument_inventory_service.py` | 仪器出库/入库、派工自动分配 |
| `workflow_service.py` | 案件流程环节顺序、退回上一环节 |
| `pipeline_service.py` | 文档管线入口与配置 |
| `workflow_hub_service.py` | 报告流程枢纽（现场→生成→审核→下载）页数据 |
| `db_backup_service.py` | SQLite 在线备份/恢复 |

### 3.5 评价报告书（`apps/evaluation_report/`）

| 文件 | 职责 |
|------|------|
| `services.py` | 基础报告生成、PDF 编译与工作区 |
| `keywords_service.py` | 关键词 schema、file-backed、DB↔磁盘 |
| `appendix_service.py` / `upload_staging.py` | 附录注入与附件暂存 |
| `structured_blocks.py` 等 | 章节解析与个性化 `§kw` 标记 |

详见 [评价报告书-LaTeX功能说明.md](评价报告书-LaTeX功能说明.md)。

### 3.6 F.1 评价报告表（`apps/core/`）

| 文件 | 职责 |
|------|------|
| `f1_eval_views.py` 等 `f1_eval_*.py` | 工作台、章节/表格/附件 API、生成与下载 |

详见 [评价报告表-F1功能说明.md](评价报告表-F1功能说明.md)。

### 3.7 演示与测试

| 文件 | 职责 |
|------|------|
| `library_test_account.py` | 演示/测试账号辅助 |
| `usage_workflow_tour.py` | 使用说明「流程练习」数据生命周期 |

### 3.8 API 侧服务（`apps/api/`）

| 文件 | 职责 |
|------|------|
| `inspection_views.py` | 检测全流程 API 视图 |
| `inspection_report_make.py` | 提交 JSON→PDF 回填、仪器合并、判定、合并报告 overlay |
| `inspection_pdf_service.py` | 填充模板字段、持久化 PDF |
| `inspection_submit_payload_service.py` | 提交体规范化 |
| `inspection_frontend_export_service.py` | 导出 runtime 前端 JSON |

## 4. 迁移

- `apps/core/migrations/`、`apps/evaluation_report/migrations/`  
- 改模型后生成迁移并在多环境执行；SQLite 与 PostgreSQL 在大表/并发写入时需额外验证。
