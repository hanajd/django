# 01 配置与环境

## 1. 核心配置文件

| 文件 | 作用 |
|------|------|
| `tablet_backend/settings.py` | `INSTALLED_APPS`、数据库、模板上下文、静态/媒体、`REST_FRAMEWORK`、`SIMPLE_JWT`、`CORS`、文件库目录、`MINERU_BACKEND`、`OLLAMA_HOST` 等 |
| `tablet_backend/urls.py` | 挂载 `admin`、`apps.core.urls`（Web 根路径）、`api/v1`、`api/v2`；`DEBUG` 下静态/媒体 |

## 2. 已安装应用

- Django 自带：`admin`、`auth`、`sessions` 等  
- 第三方：`rest_framework`、`rest_framework_simplejwt`、`corsheaders`、`django_filters`  
- 业务：`apps.core`、`apps.api`、`apps.evaluation_report`

## 3. 模板与全局上下文

- 模板目录：`settings.TEMPLATES` → `BASE_DIR / 'templates'`，且 `APP_DIRS=True`（应用内模板亦可加载）。  
- 全局注入：`apps.core.context_processors.menu_context`（菜单、角色权限摘要、使用说明开关、流程练习 manifest 等）。

## 4. 媒体与文件库路径（重要）

`settings.py` 中在 `MEDIA_ROOT` 下约定多类子目录，例如：

- `FILE_LIBRARY_ROOT`、`FILE_LIBRARY_UPLOAD_DIR`、`FILE_LIBRARY_TEMPLATE_DIR`、现场记录/报告/附件/检测提交等目录常量。  
- 管线临时目录：`PIPELINE_TEMP_PDF`、`PIPELINE_BATCH_ARCHIVE` 等。
- **评价报告书**：`LATEX_TEMPLATE_ROOT`（仓库 `latex/`）、`EVALUATION_REPORT_WORK_ROOT`、`EVALUATION_LATEX_TEMPLATE_ROOT`、`CONVERTER_KEYWORDS_CSV`（见 [评价报告书-LaTeX功能说明.md](评价报告书-LaTeX功能说明.md)）。
- **评价报告表 F.1**：`F1_EVAL_PACKAGE_DIR`（仓库 `f1_eval_report/`）、`F1_EVAL_WORKSPACE_ROOT`（默认 `media/f1_eval/workspaces`）。
- **App OTA**：`APP_OTA_APK_DIR`（默认 `media/apk`）、`APP_OTA_RUNTIME_CONFIG_FILE`（根目录 `app_ota_runtime.json`）。

**接手注意**：迁移存储或清理磁盘时，需与 `library_file_service`、`pipeline_service`、评价报告书/F.1 工作区路径假设一致。

**启动校验**（`apps.core.library_media_integrity`）：服务启动后扫描未在回收站中的 `LibraryFile`，若 `FILE_LIBRARY_ROOT` 下无对应磁盘文件则自动移入回收站；卡住的 OCR 任务（源文件均缺失）标记为失败。可通过 `LIBRARY_MEDIA_INTEGRITY_ON_STARTUP=0` 或 `LIBRARY_MEDIA_INTEGRITY_SKIP=1` 关闭；亦可手动执行 `python manage.py check_library_media`（`--dry-run` 仅统计）。

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

## 6. 外部服务与重量级依赖（默认值表）

> **安装命令与验收**：以 [外部依赖安装-Ollama-MinerU-LaTeX.md](外部依赖安装-Ollama-MinerU-LaTeX.md) 为准。  
> **调用链**：见 [05-utils-and-pipelines.md](05-utils-and-pipelines.md)。

### 6.1 Ollama

| 变量 / 项 | 默认 | 说明 |
|-----------|------|------|
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | 无单独端口变量 |
| `EQUIPMENT_MODEL_NAME` | `qwen3:14b-q4_K_M` | 须与 `ollama list` 一致 |
| `OLLAMA_OPTIONS` | 不设时代码侧倾向 `{"num_gpu":999}` | JSON；非法则忽略 |
| `ENABLE_LLM_FRONTEND_TEMPLATE_DRAFT` | `0`（关） | 为 `1` 才用 LLM 生成前端模板草稿 |
| 运行时覆盖 | 根目录 `llm_runtime.json` | 调试页可改 provider/host/model |

失败时：设备抽取返回空骨架、模板生成返回 `{}`，一般不拖垮整站。

### 6.2 MinerU

| 变量 / 项 | 默认 | 说明 |
|-----------|------|------|
| `MINERU_BACKEND` | `pipeline` | Django 管线会写入环境；慎用 `hybrid-auto-engine` |
| `MINERU_TIMEOUT` | `600` | 秒 |
| `MINERU_KEEP_OUTPUT` | 空=清空输出目录 | `1`/`true`/`yes` 保留便于排障 |
| `MINERU_CUDA_VISIBLE_DEVICES` | 空=可自动选卡 | 见 `gpu_scheduler` |
| CLI | `mineru`（venv PATH） | `mineru -p pdf -o out [-b backend]` |
| 批次输出 | `media/file_library/temp/batches/<id>/mineru_output/` | |

`torch` 为传递依赖，与 CUDA 强相关。未安装 CLI → 管线常见错误 `MinerU未生成MD`。

### 6.3 LaTeX（评价报告书）

| 变量 / 项 | 默认 | 说明 |
|-----------|------|------|
| `LATEX_TEMPLATE_ROOT` | `BASE_DIR/latex` | 内置工程 |
| `LATEX_ENGINE` | `lualatex` | 可改 `xelatex` |
| `LATEX_RUN_TIMES` | `2` | 编译遍数 |
| `EVALUATION_REPORT_WORK_ROOT` | `media/evaluation_reports/work` | 每报告工作区 |
| `EVALUATION_LATEX_TEMPLATE_ROOT` | `media/evaluation_reports/latex_templates` | 用户模板 |
| `CONVERTER_KEYWORDS_CSV` | `converter/data/project_keywords.csv` | 关键词 |
| 主文件 | 硬编码 `main.tex` | 主站无 `LATEX_MAIN_FILE` 配置项 |
| 字体 | 项目根 `fonts/` | 另：检测 PDF 用 `htmlpdf/fonts/` |

缺引擎 → 明确报错，报告 `FAILED`，无非 TeX 降级产物。

### 6.4 GPU 调度（可选）

见 `utils/gpu_scheduler.py`：`PIPELINE_GPU_AUTO_OLLAMA`、`PIPELINE_GPU_AUTO_MINERU`、`OLLAMA_PREFERRED_GPU_INDEX`、`PIPELINE_OLLAMA_NUM_CTX_*` 等。外部依赖手册 §5 有摘要。

## 7. 运行时 JSON（可选热更新）

仓库根目录可能存在（以 `settings` 中路径为准）：

| 文件 | 用途 |
|------|------|
| `jwt_runtime.json` | JWT 寿命等（`set_jwt_runtime` 可写） |
| `app_ota_runtime.json` | Android 版本与 APK 元数据 |
| `llm_runtime.json` / `pdf_fill_runtime.json` / `decimal_precision_runtime.json` | LLM / 填报精度等可调参数 |

生产勿把含密钥的文件提交进 Git；备份时与 `MEDIA_ROOT`、数据库一并纳入。
