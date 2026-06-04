# Django 项目维护文档索引

本目录为 **Tablet 后台 / API** 项目的接手说明，按「先总览 → 再配置 → Web → API → 领域与工具 → 运维」阅读。

| 文档 | 内容 |
|------|------|
| [00-overview.md](00-overview.md) | 项目定位、目录结构、如何运行、技术栈 |
| [01-configuration.md](01-configuration.md) | `settings`、环境变量、媒体与文件库路径、JWT/CORS |
| [02-web-ui-core.md](02-web-ui-core.md) | Web 路由、`views` 功能域、模板、权限与菜单 |
| [03-rest-api.md](03-rest-api.md) | `/api/v1` 与 `/api/v2`、检测与台账接口分工 |
| [04-domain-models-and-services.md](04-domain-models-and-services.md) | 核心模型一览、`apps/core` 服务模块 |
| [05-utils-and-pipelines.md](05-utils-and-pipelines.md) | `utils/`、PDF/模板规则、管线与 MinerU/Ollama |
| [06-scripts-and-admin.md](06-scripts-and-admin.md) | `manage.py` 自定义命令、Django Admin |
| [backend_usage_guide.md](backend_usage_guide.md) | 「后台使用说明」与流程练习相关代码清单 |
| [后台管理系统说明（甲方版）.md](后台管理系统说明（甲方版）.md) | **面向甲方/业务用户**的后台能力总览、推荐流程与常见问题（无技术实现细节） |

**说明**：业务规则以代码与数据库迁移为准；文档描述的是当前仓库结构，大改模块后请同步更新对应章节。
