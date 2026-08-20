# Django 项目文档（接手与全面掌握）

本目录是 **Tablet 检测后台 / REST API / 评价业务** 的维护文档。目标：新人按本文阅读顺序走完后，能独立定位代码、改业务、排障与部署。

**以代码与迁移为准**；文档描述当前仓库结构（整理日期：2026-08-20）。大改后请同步更新本索引与 [07-features-catalog.md](07-features-catalog.md)。

---

## 0. 30 秒定位

| 项 | 内容 |
|----|------|
| 工程名 | `tablet_backend`（Django 4.2 + DRF + JWT） |
| Web | 服务端渲染：`/files/...`、`/evaluation-reports/...`、RBAC、仪器台账 |
| API | `/api/v2/`（推荐）、`/api/v1/`（兼容）；平板检测 + OTA |
| 三大业务线 | **检测报告**（现场→签发）、**评价报告表 F.1**、**评价报告书 LaTeX** |
| 本地启动 | `python manage.py migrate && python manage.py runserver 0.0.0.0:11223` |
| Python | 3.12.x（见根目录 `requirements.txt`） |

三条「报告」勿混：

| 名称 | 路径 / 权限 | 产物 |
|------|-------------|------|
| 检测报告 | 文件库 / hub / App 提交 | 防护检测 PDF |
| 评价报告表（F.1） | `/files/f1-eval/`，`perm_f1_eval` | F.1 表单 PDF（`media/f1_eval/`） |
| 评价报告书 | `/evaluation-reports/`，`perm_evaluation_report` | LaTeX 编译 PDF |

---

## 1. 建议阅读路径（掌握全项目）

### 路径 A：新同事（约 1～2 天）

1. **本文**（索引与边界）  
2. [00-overview.md](00-overview.md) — 目录地图、技术栈、如何跑起来  
3. [07-features-catalog.md](07-features-catalog.md) — **功能总览**（先建立全局菜单）  
4. [01-configuration.md](01-configuration.md) — settings / 媒体路径 / 环境变量  
5. [04-domain-models-and-services.md](04-domain-models-and-services.md) — 核心模型  
6. 按职责二选一深入：  
   - Web → [02-web-ui-core.md](02-web-ui-core.md)  
   - API → [03-rest-api.md](03-rest-api.md)  
7. [05-utils-and-pipelines.md](05-utils-and-pipelines.md) + [06-scripts-and-admin.md](06-scripts-and-admin.md)  
8. 业务专题（按下表「主路径」各读一篇）

### 路径 B：按业务域（改功能时）

| 你要改什么 | 先读 |
|------------|------|
| 检测提交 / 现场记录 / 报告 PDF | [现场记录生成报告流程说明.md](现场记录生成报告流程说明.md)、[WORKFLOW_COMMISSION_DISPATCH.md](WORKFLOW_COMMISSION_DISPATCH.md)、[检测业务多角色与工作流说明.md](检测业务多角色与工作流说明.md) |
| 报告流程枢纽 / 工作台一致性 | [报告流程枢纽与医院委托工作台数据一致性方案.md](报告流程枢纽与医院委托工作台数据一致性方案.md) |
| 模板划框 / 公式 / 映射 | [FRONTEND_FORM_SCHEMA.md](FRONTEND_FORM_SCHEMA.md)、[json公式说明.md](json公式说明.md)、[拟合公式前后端对接说明.md](拟合公式前后端对接说明.md) |
| 评价报告书 LaTeX | [评价报告书-LaTeX功能说明.md](评价报告书-LaTeX功能说明.md) |
| 评价报告表 F.1 | [评价报告表-F1功能说明.md](评价报告表-F1功能说明.md) |
| 仪器 | [仪器台账出库入库.md](仪器台账出库入库.md) |
| 医院 / 产品 | [医院信息-产品管理说明.md](医院信息-产品管理说明.md) |
| App OTA | [ANDROID_OTA_UPDATE_SPEC.md](ANDROID_OTA_UPDATE_SPEC.md)、[APK_PACKAGE_GUIDE.md](APK_PACKAGE_GUIDE.md) |
| 打包 / 新设备安装 | [打包与新设备安装说明.md](打包与新设备安装说明.md)（全量：db+media+依赖） |
| 生产部署与加固 | [部署流程-从零安装与源码保护评估.md](部署流程-从零安装与源码保护评估.md) |
| 演示账号 | [组织主任账号说明.md](组织主任账号说明.md) |

### 路径 C：甲方 / 产品（非开发）

- [后台管理系统说明（甲方版）.md](后台管理系统说明（甲方版）.md)  
- [后台系统使用说明.md](后台系统使用说明.md)（引导规格）

---

## 2. 核心技术文档（00～07 + 专题主文）

| 文档 | 内容 |
|------|------|
| [00-overview.md](00-overview.md) | 项目定位、**完整目录地图**、运行、技术栈 |
| [01-configuration.md](01-configuration.md) | settings、环境变量、媒体与库路径、JWT/CORS |
| [02-web-ui-core.md](02-web-ui-core.md) | Web 路由与功能域（含 hub / F.1 / 评价报告书） |
| [03-rest-api.md](03-rest-api.md) | `/api/v1`·`v2`、检测接口、OTA |
| [04-domain-models-and-services.md](04-domain-models-and-services.md) | 模型与 `apps/core`·`evaluation_report` 服务 |
| [05-utils-and-pipelines.md](05-utils-and-pipelines.md) | `utils/`、MinerU/Ollama、PDF 工具 |
| [06-scripts-and-admin.md](06-scripts-and-admin.md) | `manage.py` 命令、Admin、运维清单 |
| [07-features-catalog.md](07-features-catalog.md) | **功能总览**（用户可见能力 + 端点索引） |
| [评价报告书-LaTeX功能说明.md](评价报告书-LaTeX功能说明.md) | 评价报告书全流程 |
| [评价报告表-F1功能说明.md](评价报告表-F1功能说明.md) | F.1 工作台与生成包 |
| [打包与新设备安装说明.md](打包与新设备安装说明.md) | **全量迁移**：代码 + db + media + 依赖环境 |
| [部署流程-从零安装与源码保护评估.md](部署流程-从零安装与源码保护评估.md) | 生产 systemd/Nginx、验收、源码保护 |
| [backend_usage_guide.md](backend_usage_guide.md) | 使用说明 / 流程练习代码清单 |

---

## 3. 业务与对接专题

### 3.1 检测主链路

| 文档 | 内容 |
|------|------|
| [WORKFLOW_COMMISSION_DISPATCH.md](WORKFLOW_COMMISSION_DISPATCH.md) | 委托立项 → 派工 → App |
| [检测业务多角色与工作流说明.md](检测业务多角色与工作流说明.md) | 五岗与案件环节 |
| [现场记录生成报告流程说明.md](现场记录生成报告流程说明.md) | 提交/导出、合并、回填、落库 |
| [报告流程枢纽与医院委托工作台数据一致性方案.md](报告流程枢纽与医院委托工作台数据一致性方案.md) | hub 与工作台数据一致性 |
| [合并报告规则说明.md](合并报告规则说明.md) | 多报告合并 |
| [报告单项判定规则与逻辑.md](报告单项判定规则与逻辑.md) | 单项判定回填 |
| [报告导出常见问题与修复清单.md](报告导出常见问题与修复清单.md) | 导出排障 |

### 3.2 模板 / 公式 / App 表单

| 文档 | 内容 |
|------|------|
| [FRONTEND_FORM_SCHEMA.md](FRONTEND_FORM_SCHEMA.md) | `frontend_form_schema/v1` |
| [json公式说明.md](json公式说明.md) | 公式与判定表达式 |
| [拟合公式前后端对接说明.md](拟合公式前后端对接说明.md) | 曲线拟合 + R² |
| [条件公式与前端type约定.md](条件公式与前端type约定.md) | 条件公式与 type |
| [勾选框条件公式实现说明.md](勾选框条件公式实现说明.md) | 勾选框条件 |
| [动态表单后端对齐与前端配合说明.md](动态表单后端对齐与前端配合说明.md) | 表达式求值对齐 |
| [统一模板JSON冗余与瘦身说明.md](统一模板JSON冗余与瘦身说明.md) | 模板 JSON 瘦身 |
| [质控拍照照片提交说明.md](质控拍照照片提交说明.md) | `sectionPhotos` |
| [前端检测仪器两栏位改版说明.md](前端检测仪器两栏位改版说明.md) | 质控/防护仪器 scope |
| [任务模板检测仪器绑定与JSON模板.md](任务模板检测仪器绑定与JSON模板.md) | 仪器绑定与 JSON |
| [模板编辑器修改说明.md](模板编辑器修改说明.md) | 编辑器改造记录（部分已演进） |

### 3.3 组织 / 医院 / 仪器 / 评价

| 文档 | 内容 |
|------|------|
| [组织主任账号说明.md](组织主任账号说明.md) | 行政/检测/评价主任演示账号 |
| [医院信息-产品管理说明.md](医院信息-产品管理说明.md) | 产品目录与委托挂载 |
| [仪器台账出库入库.md](仪器台账出库入库.md) | 出库入库 |
| [业务线扩展-行政检测评价工作流评估.md](业务线扩展-行政检测评价工作流评估.md) | 业务线扩展评估草案（含 §3.5 报告书现状） |

### 3.4 客户端与运维

| 文档 | 内容 |
|------|------|
| [打包与新设备安装说明.md](打包与新设备安装说明.md) | 源码/数据打包与新机安装 |
| [部署流程-从零安装与源码保护评估.md](部署流程-从零安装与源码保护评估.md) | 生产部署、验收、源码保护 |
| [ANDROID_OTA_UPDATE_SPEC.md](ANDROID_OTA_UPDATE_SPEC.md) | App 版本检查与 APK 下载 |
| [APK_PACKAGE_GUIDE.md](APK_PACKAGE_GUIDE.md) | 平板 APK 打包指南 |
| [ADMIN_PERFORMANCE_OPTIMIZATION.md](ADMIN_PERFORMANCE_OPTIMIZATION.md) | 后台性能路线图 |

---

## 4. 排障 / 审计存档（不必通读）

调查当时现场的只读报告，**不是**日常主路径；需要时按标题检索：

| 文档 | 性质 |
|------|------|
| [任务模板与文件库数量不一致排查报告.md](任务模板与文件库数量不一致排查报告.md) | 绑定 vs 磁盘 |
| [任务模板number类型栏位排查报告.md](任务模板number类型栏位排查报告.md) | `type:number` 清单 |
| [现场记录current-第五章本底与公式审计.md](现场记录current-第五章本底与公式审计.md) | 第五章审计 |
| [第五章公式问题统计-六类设备.md](第五章公式问题统计-六类设备.md) | 公式问题统计 |
| [chapter5-fix-report-20260720.json](chapter5-fix-report-20260720.json) | 审计数据 |

示例数据：[instruments_frontend_example.json](instruments_frontend_example.json)

---

## 5. 仓库外 / 并行子系统（勿与主站 INSTALLED_APPS 混淆）

| 目录 | 说明 |
|------|------|
| `evaluation_report_standalone/` | 评价报告书独立工程（可单独跑；嵌入说明见其 `docs/`） |
| `f1_eval_standalone/` | F.1 独立最小闭包 |
| `f1_eval_report/` | F.1 PDF 生成库（被主站 import） |
| `radiation_detection_report/` | 可移植检测结果表 PDF 包 |
| `scripts/` | 一次性修复脚本（非 `manage.py`） |

---

## 6. 维护约定

1. 改路由 / 权限 / 模型 → 更新 **07** + **02 或 04**。  
2. 改评价报告书 / F.1 → 更新对应专题 + README 三线对照表。  
3. 新增「长期有效」专题 → 挂到本文 §2 或 §3；一次性排查报告 → 放 §4。  
4. 不要在文档里写密钥；演示密码见组织主任说明并要求改密。
