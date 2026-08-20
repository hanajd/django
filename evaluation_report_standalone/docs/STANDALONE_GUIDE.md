# 评价报告 Standalone：项目现状与 Django 嵌入指南

本文是 `evaluation_report_standalone` 的当前实现记录。它描述的是实际代码和当前 URL，不是旧版根目录 `backend` 的说明。Standalone 用于先独立验证评价报告制作、结构化编辑、材料转换和 LaTeX 编译链路；验证稳定后，再把同构业务代码嵌回正在运行的 Django 主站。

## 1. 当前定位

### 1.1 目标

Standalone 是一套可登录的 Django 测试应用，核心目标是：

1. 创建一份评价报告，绑定医院、设备类型、报告子类型和 LaTeX 模板。
2. 在浏览器中编辑模板章节，不要求用户直接手写完整 LaTeX。
3. 上传评价信息表和附件，支持预览、排序、删除和提交。
4. 将结构化内容、项目关键词、评价信息表和附件合并到工作区中的 LaTeX 工程。
5. 调用 `lualatex` 编译并保存 PDF，供预览或下载。

### 1.2 明确不属于当前功能

Standalone 不包含主项目中的检测任务、OCR/MinerU、HTML PDF、App API，也不承担完整文件库 UI。`converter` 目录中的检测报告转换脚本属于历史辅助代码，不是本功能的主入口。

## 2. 目录和职责

```text
evaluation_report_standalone/
├── apps/core/                    # 本地兼容层：医院、文件库、权限和路径接口
├── apps/evaluation_report/       # 评价报告业务 App，嵌入主站时同步这一层
├── converter/                    # Word/PDF/Markdown/LaTeX 转换和编译器
├── latex/yp250420/               # 内置 LaTeX 报告工程模板
├── templates/                    # 页面模板
├── config/                       # standalone Django 配置和根 URL
├── media/                        # 数据库之外的上传、工作区和输出文件
├── db.sqlite3                    # standalone 本地测试数据库
└── manage.py
```

代码边界如下：

| 层 | 主要文件 | 作用 |
| --- | --- | --- |
| 路由 | `config/urls.py`、`apps/evaluation_report/urls.py` | 登录、管理后台、报告页面和 AJAX 保存接口 |
| 页面视图 | `views.py`、`structured_views.py`、`latex_template_views.py` | 权限检查、表单处理、文件操作和页面渲染 |
| 业务服务 | `services.py`、`library_integration.py`、`upload_staging.py` | 材料清单、staging、转换、附件注入和 PDF 构建 |
| 编辑模型 | `structured_blocks.py`、`tabular_codec.py`、`flowchart_codec.py` | `.tex` 章节与文本、列表、表格、公式、流程图之间的解析和序列化 |
| 模板服务 | `latex_template_service.py`、`template_preview.py` | 内置/上传模板、文件树、源码编辑和资源预览 |
| 本地兼容层 | `apps/core/` | 提供主站已有的同名模型和服务接口，便于 standalone 单独运行 |

## 3. 当前用户流程

### 3.1 从创建到 PDF

```text
登录
  -> 评价报告列表
  -> 创建报告（自动匹配栏目模板）
  -> 上传评价信息表
  -> 预览转换结果并生成基础报告
  -> 结构化正文编辑
  -> 保存并进入附件管理
  -> 上传、排序、预览和微调附件
  -> 确认生成 PDF
  -> PDF 预览或导出

最终构建时会执行：转换评价信息表 + 复制/保留工作区模板 + 注入项目关键词和附件 + lualatex 编译。
```

创建报告后会建立 `media/evaluation_reports/work/report_<id>/` 工作区，并预先创建上传槽位。编辑器修改的是工作区中的 LaTeX 文件，不是把内容暂时放在浏览器 session 中；因此刷新页面后仍能看到已保存内容。

### 3.2 编辑能力

- 章节编辑：章、节、小节、正文、列表、表格、公式、图片和交叉引用。
- 表格编辑：插入/删除行列、合并/取消合并、对齐、列宽、表格环境和边框设置。
- 公式编辑：显示公式和行内公式，使用 KaTeX 做浏览器预览，保存为 LaTeX。
- 流程图编辑：维护步骤模型，生成 TikZ/LaTeX，并支持服务端预览。
- 模板预览：浏览 `main.tex`、章节、图片和 PDF 资源；上传模板可编辑 `.tex`，内置模板默认只读。

### 3.3 材料处理

上传文件先进入 staging，用户可以在提交前查看缩略图、预览内容、排序或移除。提交后文件落入 standalone 的 `LibraryFile`，并通过 `EvaluationReportUpload` 绑定到报告和材料槽位。

评价信息表的构建路径：

- PDF：调用 `converter.pdf2latex`，提取 LaTeX 正文并写入 `99-eval-info.tex`。
- DOCX：调用 `converter.word2md`，再调用 `converter.md2latex`，生成 LaTeX 片段。
- 附件：根据槽位规则复制或转换到工作区，并由 `appendix_service` 注入模板。

## 4. URL 入口

根路径 `/` 会跳转到 `/evaluation-reports/`。主要页面分组如下：

| 路径 | 用途 |
| --- | --- |
| `/login/`、`/logout/` | 登录和退出 |
| `/evaluation-reports/` | 报告列表 |
| `/evaluation-reports/create/` | 创建报告 |
| `/evaluation-reports/<id>/` | 报告详情和材料清单 |
| `/evaluation-reports/<id>/edit/` | 结构化章节编辑 |
| `/evaluation-reports/<id>/keywords/` | 当前报告项目关键词 |
| `/evaluation-reports/<id>/upload/<slot>/` | 文件槽位上传和预览 |
| `/evaluation-reports/<id>/template/` | 当前报告 LaTeX 工程预览/编辑 |
| `/evaluation-reports/<id>/build/` | 同步构建 PDF |
| `/evaluation-reports/<id>/pdf-preview/` | PDF 预览 |
| `/evaluation-reports/<id>/download/pdf/` | 下载 PDF |
| `/evaluation-reports/templates/` | LaTeX 模板库 |
| `/evaluation-reports/keywords/schema/` | 公共关键词定义 |
| `/admin/` | standalone 数据管理 |

结构化编辑的表格、公式、流程图保存和图片上传接口均位于同一个报告 URL 下，使用 POST + JSON 或 multipart 请求，并返回 JSON。

## 5. 数据和文件

核心模型：

| 模型 | 作用 |
| --- | --- |
| `EvaluationReport` | 报告基本信息、状态、模板、工作目录、编译日志和输出 PDF |
| `EvaluationReportUpload` | 报告材料槽位、是否必填、排序和提交状态 |
| `EvaluationReportUploadFile` | 一个槽位下的具体文件及顺序 |
| `EvaluationLatexTemplate` | 内置或上传的 LaTeX 模板元数据 |
| `EvaluationKeywordSchema` | 公共 LaTeX 命令与中文字段名 |
| `EvaluationReportKeyword` | 某份报告的字段值 |
| `LibraryFile` | standalone 文件库记录 |
| `CommissionOrganization` | 医院/组织记录 |

重要目录：

```text
media/
├── file_library/evaluation_forms/       # 评价信息表原文件
├── file_library/attachments/            # 附件原文件
├── evaluation_reports/work/report_<id>/ # 报告工作区和编译中间文件
│   ├── latex/                           # 当前报告可编辑的 LaTeX 工程
│   └── images/                          # 编辑器和转换器使用的图片
├── evaluation_reports/latex_templates/  # 上传模板
└── evaluation_reports/output/           # 输出文件
```

`EvaluationReport.allow_incomplete_build` 默认由 `EVALUATION_REPORT_ALLOW_INCOMPLETE` 控制。当前默认是测试模式：缺少必填材料时仍允许尝试编译；正式主站应根据业务要求改为 `False` 或提供明确的测试开关。

## 6. 本地运行

在 `evaluation_report_standalone` 目录执行：

```powershell
conda activate latex
pip install -r requirements.txt
python manage.py migrate
python manage.py bootstrap_standalone
python manage.py runserver 0.0.0.0:22335
```

浏览器地址：`http://127.0.0.1:22335/`。

初始化命令会幂等创建：用户 `admin` / `admin123`、演示医院 `演示医院（Standalone）`、内置模板 `yp250420`，以及从 `converter/data/project_keywords.csv` 导入的关键词定义。

后台运行：

```powershell
Start-Process python -ArgumentList "manage.py","runserver","0.0.0.0:22335" `
  -WorkingDirectory "C:\Users\13785\Desktop\markdown2latex\evaluation_report_standalone" `
  -RedirectStandardOutput "runserver.log" -RedirectStandardError "runserver.err" -NoNewWindow
```

编译前必须确认：

```powershell
lualatex --version
```

当前检查结果（2026-08-11）：Django `check` 通过，数据库迁移已完成，测试数据已存在；但当前系统和 `latex` Conda 环境都没有找到 `lualatex`，所以网页编辑可用，PDF 构建在编译阶段会失败，需先安装 TeX Live/MiKTeX 并把 `lualatex` 加入 PATH。

## 7. 嵌入主 Django 后台

嵌入的原则是：同步业务 App 和转换器，复用主站的 `apps.core`、认证、权限、文件库和媒体配置。不要把 standalone 的 Django 项目外壳或本地兼容层复制到主站。

### 7.1 应同步的内容

| Standalone | 主站 `django/` 或仓库根目录 | 处理方式 |
| --- | --- | --- |
| `apps/evaluation_report/**` | `django/apps/evaluation_report/**` | 同步业务代码和模板相关服务 |
| `converter/**` | `django/converter/**` | 同步实际被评价报告使用的转换器 |
| `templates/evaluation_report/**` | `django/templates/evaluation_report/**` | 同步页面模板 |
| `templates/includes/markdown_render_block.html` | 同路径 | 有改动时同步 |
| `latex/yp250420/**` | 仓库根 `latex/yp250420/**` | 同步内置模板和资源 |

不要同步：`apps/core/`、`config/`、`manage.py`、`db.sqlite3`、`media/`、standalone 的 `staticfiles/`，也不要用 standalone 的 migration 覆盖主站已有 migration。

### 7.2 主站配置

确认主站已经安装评价报告 App，并设置以下配置。路径必须按主站的实际 `BASE_DIR` 调整：

```python
INSTALLED_APPS = [
    # ...
    "apps.core",
    "apps.evaluation_report",
]

LATEX_TEMPLATE_ROOT = REPO_ROOT / "latex"
LATEX_MAIN_FILE = "main.tex"
LATEX_ENGINE = "lualatex"
LATEX_RUN_TIMES = 2
CONVERTER_KEYWORDS_CSV = BASE_DIR / "converter" / "data" / "project_keywords.csv"

EVALUATION_REPORT_WORK_ROOT = MEDIA_ROOT / "evaluation_reports" / "work"
EVALUATION_LATEX_TEMPLATE_ROOT = MEDIA_ROOT / "evaluation_reports" / "latex_templates"
FILE_LIBRARY_EVALUATION_FORM_DIR = FILE_LIBRARY_ROOT / "evaluation_forms"
FILE_LIBRARY_ATTACHMENT_DIR = FILE_LIBRARY_ROOT / "attachments"
```

主站根 URL 挂载：

```python
path("evaluation-reports/", include("apps.evaluation_report.urls")),
```

### 7.3 必须保持的 `apps.core` 接口

Standalone 通过兼容层使用这些导入；嵌入主站时，`evaluation_report` 的导入路径不需要改变：

| 接口 | 作用 |
| --- | --- |
| `apps.core.models.CommissionOrganization` | 报告医院外键，需提供 `LEVEL_HOSPITAL` |
| `apps.core.models.LibraryFile` | 文件库外键和分类常量 |
| `apps.core.library_access.role_has(user, perm)` | 报告功能权限检查 |
| `apps.core.library_file_service.save_library_binary_uploads` | 上传文件落盘并建立文件库记录 |
| `apps.core.pipeline_service.library_absolute_path` | 从相对路径解析文件绝对路径 |

主站需要把 `perm_evaluation_report` 接入角色权限和菜单；standalone 的 `role_has` 只为本地测试放宽了判断，不能直接作为生产权限实现。

### 7.4 迁移和数据策略

1. 在 standalone 中修改模型时，先用本地数据库验证功能。
2. 把模型和业务代码同步到主站。
3. 在主站执行 `python manage.py makemigrations evaluation_report`，让 migration 依赖主站真实的 `apps.core`。
4. 执行 `python manage.py migrate`，不要直接复制 standalone 的 migration 文件。
5. 迁移后导入或确认 `EvaluationLatexTemplate`、关键词 schema、医院和材料分类数据。
6. 确认主站已有文件库记录的相对路径能被 `library_absolute_path` 解析，避免把旧文件复制到 standalone 的目录结构后再上线。

### 7.5 推荐同步命令

在仓库根目录运行，先检查差异，再执行覆盖：

```powershell
$src = "evaluation_report_standalone"
$dst = "django"

robocopy "$src\apps\evaluation_report" "$dst\apps\evaluation_report" /E `
  /XD migrations __pycache__ /XF *.pyc
robocopy "$src\converter" "$dst\converter" /E /XD __pycache__
robocopy "$src\templates\evaluation_report" "$dst\templates\evaluation_report" /E
robocopy "$src\templates\includes" "$dst\templates\includes" /E
robocopy "$src\latex\yp250420" "latex\yp250420" /E /XD __pycache__
```

同步后执行：

```powershell
cd django
python manage.py makemigrations evaluation_report
python manage.py migrate
python manage.py check
```

### 7.6 主站联调清单

- `/evaluation-reports/` 能打开，登录用户具备 `perm_evaluation_report`。
- 医院下拉框只显示 active 且 level 为 hospital 的组织。
- `yp250420` 模板能从主站的 `LATEX_TEMPLATE_ROOT` 找到 `main.tex`。
- 评价信息表可上传、预览、提交，并能从主站文件库找到。
- 附件能正确进入报告工作区或 `files/` 片段。
- 结构化编辑保存后，刷新页面仍能恢复章节内容。
- `lualatex` 可执行，生成 PDF 后可预览和下载。
- 主站部署时不要让多个用户同时写同一个报告工作区；编译任务较重时应改为 Celery/RQ 等异步任务，并让页面轮询状态。

## 8. 后续修改约定

优先在 standalone 中修改 `apps/evaluation_report`、`converter` 和对应模板，完成网页回归后再同步到主站。新增对 `apps.core` 的依赖时，先确认 standalone 兼容层和主站真实实现都提供同名接口，并在本文的接口表中登记。检测报告相关代码只有在评价报告确实引用时才同步。

## 9. 反馈记录格式

测试时建议记录：报告 ID、进入的页面、操作步骤、期望结果、实际结果、浏览器控制台错误、服务器 `runserver.err` 内容，以及是否涉及 PDF 编译。这样可以区分页面交互问题、数据保存问题、转换问题和 LaTeX 环境问题。
