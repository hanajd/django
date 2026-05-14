# 04 领域模型与服务（apps.core）

## 1. 模型一览（`apps/core/models.py`）

以下为**名称级**索引，字段与约束以模型定义及 `migrations/` 为准。

| 模型 | 业务含义（简述） |
|------|------------------|
| `Role` | 角色及默认权限位 |
| `UserProfile` | 用户扩展：角色、权限覆盖等 |
| `Menu` | 动态菜单树 |
| `LibraryFile` | 文件库文件元数据与分类 |
| `LibraryTask` | 检测任务模板（输出目标：现场记录/报告等） |
| `LibraryTaskAssignment` | 任务模板分配给用户 |
| `LibraryOCRProcessTask` | OCR 异步任务记录 |
| `LibraryProject` | 检测项目工作台主体 |
| `LibraryProjectUserRevocation` | 项目级用户撤销/限制 |
| `LibraryFileProject` / `LibraryFileTask` | 文件与项目、与任务模板的关联 |
| `InspectedOrganization` / `BizContact` / `BizDevice` | 台账：单位、联系人、设备 |
| `InspectionCase` | 案件/委托与项目、任务关联 |
| `SiteRecord` / `Report` | 台账侧现场记录与报告记录 |
| `InspectionSubmission` | 检测提交实例 |
| `InspectionSubmissionInstrument` | 提交与仪器快照 |
| `LibraryProjectWorkflowMember` | 项目流程岗位成员 |
| `InspectionCaseWorkflowState` | 案件流程状态 |
| `InstrumentCatalog` | 仪器主数据台账 |

## 2. 与 API 台账的对应

`registry_views` 中 ViewSet 多与上述 `Inspected*`、`Biz*`、`SiteRecord`、`Report`、`InspectionCase` 等对应；命名空间以路由 `registry/*` 为准。

## 3. 核心服务文件（重复列出便于检索）

| 文件 | 职责 |
|------|------|
| `library_file_service.py` | 文件保存、关联、下载 |
| `htmlpdf_service.py` | 模板编辑器后端 |
| `pipeline_service.py` | 文档管线入口与配置 |
| `workflow_service.py` | 项目流程成员与阶段 |
| `library_access.py` | 权限与数据范围 |
| `library_test_account.py` | 演示/测试账号辅助 |
| `usage_workflow_tour.py` | 使用说明「流程练习」数据生命周期 |

## 4. 迁移

- 路径：`apps/core/migrations/`  
- 改模型后务必生成迁移并在多环境执行；SQLite 与 PostgreSQL 行为差异在大表/并发写入时需额外验证。
