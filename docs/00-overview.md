# 00 项目总览

## 1. 项目定位

本仓库为 **检测/平板业务的后台管理系统** 与 **REST API**（`tablet_backend`），主要能力包括：

- Web：登录、角色与菜单、用户管理、**文件库**、**项目工作台**、**任务模板库**、**模板编辑器（HTMLPDF）**、可选 **文档处理管线**、内置 **使用说明与流程练习**。
- API：JWT 认证；平板/App 侧 **检测流程**（提交、草稿、OCR、签名、导出等）；**台账/registry**（受检单位、设备、案件、现场记录、报告等）。

前后端关系：**Web 为服务端渲染模板**；**API 供外部客户端**（与 Web 可共用 Session，亦可用 JWT）。

## 2. 仓库顶层结构（与 Django 相关）

```
tablet_backend/          # 工程配置：settings、根 urls、wsgi
apps/core/               # Web 主应用：模型、视图、权限、文件库、管线、模板编辑器后端等
apps/api/                # REST API：认证、检测、台账 ViewSet 与独立 APIView
templates/               # 全局模板 + core 业务页 + guide 使用说明
static/                  # 静态资源
media/                   # 用户上传与文件库落盘（见 settings 中 FILE_LIBRARY_*）
utils/                   # 与业务解耦的工具：PDF、规则引擎、管线、Ollama 等
manage.py
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

1. 本文 + [01-configuration.md](01-configuration.md)  
2. [04-domain-models-and-services.md](04-domain-models-and-services.md)（理解数据模型）  
3. [02-web-ui-core.md](02-web-ui-core.md) 或 [03-rest-api.md](03-rest-api.md)（按负责 Web 还是 API）  
4. [05-utils-and-pipelines.md](05-utils-and-pipelines.md)（若接触 PDF/管线/OCR）  
5. [backend_usage_guide.md](backend_usage_guide.md)（若改使用说明或流程练习）
