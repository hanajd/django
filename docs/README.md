# Django 项目维护文档索引

本目录为 **Tablet 后台 / API** 项目的接手说明，按「先总览 → 功能清单 → 配置 → Web → API → 领域与工具 → 运维 → 业务专题」阅读。

## 维护者文档（技术）

| 文档 | 内容 |
|------|------|
| [00-overview.md](00-overview.md) | 项目定位、目录结构、如何运行、技术栈 |
| [07-features-catalog.md](07-features-catalog.md) | **功能总览**：Web/API 模块、工作流、权限、模型与服务索引 |
| [01-configuration.md](01-configuration.md) | `settings`、环境变量、媒体与文件库路径、JWT/CORS |
| [02-web-ui-core.md](02-web-ui-core.md) | Web 路由、`views` 功能域、模板、权限与菜单 |
| [03-rest-api.md](03-rest-api.md) | `/api/v1` 与 `/api/v2`、检测与台账接口分工 |
| [04-domain-models-and-services.md](04-domain-models-and-services.md) | 核心模型一览、`apps/core` 服务模块 |
| [05-utils-and-pipelines.md](05-utils-and-pipelines.md) | `utils/`、PDF/模板规则、管线与 MinerU/Ollama |
| [06-scripts-and-admin.md](06-scripts-and-admin.md) | `manage.py` 自定义命令、Django Admin |
| [backend_usage_guide.md](backend_usage_guide.md) | 「后台使用说明」与流程练习相关代码清单 |

## 业务与对接文档

| 文档 | 内容 |
|------|------|
| [后台管理系统说明（甲方版）.md](后台管理系统说明（甲方版）.md) | **面向甲方/业务用户**的后台能力总览、推荐流程与常见问题 |
| [对外汇报-检测业务数字化平台说明.md](对外汇报-检测业务数字化平台说明.md) | **对外汇报 / 制作 PPT** 用的详细说明（按页拆分、含 34 页与 18 页大纲） |
| [对外汇报-单页PPT说明.md](对外汇报-单页PPT说明.md) | **单页 PPT** 浓缩版：上屏正文 + 版面建议 + 30 秒口述稿 |
| [WORKFLOW_COMMISSION_DISPATCH.md](WORKFLOW_COMMISSION_DISPATCH.md) | 委托立项 → 派工 → App 检测链路 |
| [检测业务多角色与工作流说明.md](检测业务多角色与工作流说明.md) | 五岗角色与案件流程环节 |
| [合并报告规则说明.md](合并报告规则说明.md) | 多报告合并规则与实现 |
| [现场记录生成报告流程说明.md](现场记录生成报告流程说明.md) | **现场记录 → 报告**：提交/手动导出、数据合并、模板回填、PDF 落库 |
| [仪器台账出库入库.md](仪器台账出库入库.md) | 仪器登记、出库、入库 |
| [任务模板检测仪器绑定与JSON模板.md](任务模板检测仪器绑定与JSON模板.md) | 模板仪器种类绑定与 JSON 合并 |
| [任务模板与文件库数量不一致排查报告.md](任务模板与文件库数量不一致排查报告.md) | **只读调查**：当前绑定 vs manifest/磁盘、保存半失败、回档异常与数据污染 |
| [任务模板number类型栏位排查报告.md](任务模板number类型栏位排查报告.md) | **只读排查**：`type:number` 未参与公式/判定的栏位清单（待确认后批量改 text） |
| [FRONTEND_FORM_SCHEMA.md](FRONTEND_FORM_SCHEMA.md) | 前端表单 JSON 架构（`frontend_form_schema/v1`） |
| [json公式说明.md](json公式说明.md) | 公式与判定表达式对接 |
| [动态表单后端对齐与前端配合说明.md](动态表单后端对齐与前端配合说明.md) | 动态表达式求值对齐 |
| [报告单项判定规则与逻辑.md](报告单项判定规则与逻辑.md) | 报告单项判定回填与数值推断 |
| [质控拍照照片提交说明.md](质控拍照照片提交说明.md) | `sectionPhotos` 提交约定 |
| [前端检测仪器两栏位改版说明.md](前端检测仪器两栏位改版说明.md) | 质控/防护仪器分 scope |
| [模板编辑器修改说明.md](模板编辑器修改说明.md) | 编辑器结构校对改造记录（部分已演进） |
| [ADMIN_PERFORMANCE_OPTIMIZATION.md](ADMIN_PERFORMANCE_OPTIMIZATION.md) | 后台性能优化路线图 |
| [后台系统使用说明.md](后台系统使用说明.md) | Driver.js 交互规格稿（实现见 `usage_workflow_tour.py`） |
| [instruments_frontend_example.json](instruments_frontend_example.json) | 仪器前端 JSON 示例数据 |

**说明**：业务规则以代码与数据库迁移为准；文档描述的是当前仓库结构，大改模块后请同步更新 [07-features-catalog.md](07-features-catalog.md) 及对应章节。
