# 01 配置与环境

## 1. 核心配置文件

| 文件 | 作用 |
|------|------|
| `tablet_backend/settings.py` | `INSTALLED_APPS`、数据库、模板上下文、静态/媒体、`REST_FRAMEWORK`、`SIMPLE_JWT`、`CORS`、文件库目录、`MINERU_BACKEND`、`OLLAMA_HOST` 等 |
| `tablet_backend/urls.py` | 挂载 `admin`、`apps.core.urls`（Web 根路径）、`api/v1`、`api/v2`；`DEBUG` 下静态/媒体 |

## 2. 已安装应用

- Django 自带：`admin`、`auth`、`sessions` 等  
- 第三方：`rest_framework`、`rest_framework_simplejwt`、`corsheaders`、`django_filters`  
- 业务：`apps.core`、`apps.api`

## 3. 模板与全局上下文

- 模板目录：`settings.TEMPLATES` → `BASE_DIR / 'templates'`，且 `APP_DIRS=True`（应用内模板亦可加载）。  
- 全局注入：`apps.core.context_processors.menu_context`（菜单、角色权限摘要、使用说明开关、流程练习 manifest 等）。

## 4. 媒体与文件库路径（重要）

`settings.py` 中在 `MEDIA_ROOT` 下约定多类子目录，例如：

- `FILE_LIBRARY_ROOT`、`FILE_LIBRARY_UPLOAD_DIR`、`FILE_LIBRARY_TEMPLATE_DIR`、现场记录/报告/附件/检测提交等目录常量。  
- 管线临时目录：`PIPELINE_TEMP_PDF`、`PIPELINE_BATCH_ARCHIVE` 等。

**接手注意**：迁移存储或清理磁盘时，需与 `apps.core.library_file_service`、`pipeline_service` 中的路径假设一致。

## 5. 认证与安全

- Web：Session + `CsrfViewMiddleware`，`LOGIN_URL` / `LOGIN_REDIRECT_URL` 见 `settings`。  
- API：`JWTAuthentication` + `SessionAuthentication`；SimpleJWT 的 lifetime、刷新策略见 `SIMPLE_JWT`。  
- **生产**务必通过环境变量设置 `SECRET_KEY`，关闭 `DEBUG`，收紧 `ALLOWED_HOSTS` 与 `CORS_ALLOWED_ORIGINS`。

### 5.1 演示账号（默认登录名 `test`，旧名 `party_a_demo` / `party_a_demo_restrictions`）外网加固

`tablet_backend/settings.py` 中可选环境变量（默认不改变行为）：

| 变量 | 作用 |
|------|------|
| `PARTY_A_DEMO_ALLOWED_IPS` | 逗号分隔允许访问的客户端 IP；支持 CIDR（如 `203.0.113.0/24`）。**非空**时，带演示限制的已登录用户仅从列表内 IP 可继续访问，否则 **403**。留空则不校验。 |
| `PARTY_A_DEMO_TRUST_X_FORWARDED_FOR` | 为 `1`/`true`/`yes` 时从 `X-Forwarded-For` 取客户端 IP（须在反向代理后且代理可信，否则易被伪造）。 |
| `PARTY_A_DEMO_DISABLE_PIPELINE` | 为 `1`/`true`/`yes` 时，演示限制用户无法使用「OCR 处理管线」独立页面（减轻 GPU/子进程负载）；文件库 OCR 分类仍可用。 |

中间件：`apps.core.middleware.PartyADemoSecurityMiddleware`（在 `AuthenticationMiddleware` 之后）。

**文件库**：演示限制账号与普通「仅本人数据」用户一致——模板编辑器里可访问的模板 JSON/PDF 仍由 `library_file_access_allowed` 判定：**本人创建的模板**，以及已绑定到「本人已分配项目」所挂载任务上的共享模板（见 `library_template_granted_via_assigned_projects_tasks`）；**不会**因演示身份而读取全库模板。

**说明**：Django 默认不会通过 URL 提供 `.py` 源码；防爬取与信息泄露主要依赖生产关闭 `DEBUG`、不暴露仓库与 `.env`、反向代理限流/WAF 及最小权限。IP 白名单在网关层配置通常更可靠，应用层校验可作为补充。

## 6. 外部服务（可选能力）

- **MinerU**：`MINERU_BACKEND` 等，注释见 `settings`（管线前会写入环境变量供子进程使用）。  
- **Ollama**：`OLLAMA_HOST`、`OLLAMA_OPTIONS` 等，与 `utils/ollama_extract`、管线配合。

具体调用链见 [05-utils-and-pipelines.md](05-utils-and-pipelines.md)。
