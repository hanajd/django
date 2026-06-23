# 00 项目总览

## 1. 项目定位

本仓库为 **检测/平板业务的后台管理系统** 与 **REST API**（`tablet_backend`），主要能力包括：

### Web 后台

- **认证与 RBAC**：登录、用户/角色/菜单管理、细粒度权限矩阵
- **文件库**：按分类 tab 管理 OCR/JSON/模板/现场记录/报告/附件/检测提交；预览、下载、回收站、**合并报告**
- **项目工作台**：委托单位树导航、立项、设备勾选、人员派工、仪器分配、模拟提交
- **委托管理**：按用户汇总项目/任务，分配/撤回、指定主要负责人
- **医院信息管理**：维护委托单位树、联系人、科室设备、合并报告绑定
- **任务模板库**：分类树、报告/现场记录模板绑定、仪器种类、**绑定历史回溯**
- **模板编辑器（HTMLPDF）**：划框、红框识别、导出统一/前端 JSON、报告-现场字段映射
- **文档识别管线**：MinerU + Ollama 异步结构化抽取（可选）
- **仪器台账**：`InstrumentCatalog` 登记、出库/入库
- **使用说明与流程练习**：内置 Driver.js 引导（限演示账号）

### REST API

- JWT 认证；平板/App 侧 **检测流程**（开始/草稿/提交、OCR、签名、附件、导出前端 JSON、手工导出报告）
- **台账/registry**：受检单位、联系人、设备、仪器（只读）、案件、现场记录、报告及全链路追溯

前后端关系：**Web 为服务端渲染模板**；**API 供外部客户端**（与 Web 可共用 Session，亦可用 JWT）。

> 完整功能清单见 [07-features-catalog.md](07-features-catalog.md)。

## 2. 仓库顶层结构（与 Django 相关）

```
tablet_backend/          # 工程配置：settings、根 urls、wsgi
apps/core/               # Web 主应用：模型、视图、权限、文件库、管线、模板编辑器后端等
apps/api/                # REST API：认证、检测、台账 ViewSet 与独立 APIView
templates/               # 全局模板 + core 业务页 + guide 使用说明
htmlpdf/templates/       # 模板编辑器前端
static/                  # 静态资源
media/                   # 用户上传与文件库落盘（见 settings 中 FILE_LIBRARY_*）
utils/                   # 与业务解耦的工具：PDF、规则引擎、管线、Ollama 等
manage.py
docs/                    # 维护文档（本目录）
```

## 3. 技术栈（简要）

- Django 4.x 风格项目、SQLite 默认（`settings.DATABASES` 可改为 MySQL/PostgreSQL）。
- **DRF** + **SimpleJWT** + **django-filter** + **corsheaders**。
- 模板 + Tailwind CDN（见 `templates/base.html`）。

## 4. 本地运行（接手后第一步）

```bash
cd /path/to/django   # 本仓库根目录（含 manage.py）
python3 manage.py migrate
python3 manage.py createsuperuser   # 可选
python3 manage.py runserver 0.0.0.0:11223   # 端口与 settings.PORT 一致时可省略 host
```

生产环境需自行配置：`SECRET_KEY`、`DEBUG=False`、`ALLOWED_HOSTS`、数据库、静态/媒体存储、HTTPS、CORS 白名单等。

## 5. 阅读顺序建议（新同事）

1. 本文 + [07-features-catalog.md](07-features-catalog.md)（全貌）  
2. [01-configuration.md](01-configuration.md)（环境与配置）  
3. [04-domain-models-and-services.md](04-domain-models-and-services.md)（理解数据模型）  
4. [02-web-ui-core.md](02-web-ui-core.md) 或 [03-rest-api.md](03-rest-api.md)（按负责 Web 还是 API）  
5. [05-utils-and-pipelines.md](05-utils-and-pipelines.md)（若接触 PDF/管线/OCR）  
6. 业务专题文档（见 [README.md](README.md)「业务与对接文档」表）  
7. [backend_usage_guide.md](backend_usage_guide.md)（若改使用说明或流程练习）
