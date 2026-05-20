# Django 检测业务后台与 API（tablet_backend）

本仓库为 **检测 / 平板业务的后台管理系统** 与 **REST API**：Web 为服务端渲染（模板 + Tailwind CDN），API 供 App / 平板等客户端（JWT 或 Session）。

主要能力概览：

- **Web**：登录、用户 / 角色 / 菜单、**文件库**（多分类、回收站、配额）、**项目工作台**、**任务模板库**、**模板编辑器（HTMLPDF）**、可选 **文档识别管线**、内置 **使用说明与流程练习**。
- **API**：`v1` 与 `v2` 两套前缀；检测流程（提交、草稿、项目—任务、文件、OCR、签名、导出等）；台账（受检单位、设备、案件、现场记录、报告等）。
- **报告 / 现场记录**：检测提交数据与 HTMLPDF 模板字段映射、生成 PDF 并写入文件库及项目 / 任务关联。

默认 HTTP 端口：**`11223`**（可用环境变量 `DJANGO_PORT` 覆盖，见 `tablet_backend/settings.py` 中的 `PORT`）。

更细的架构与阅读顺序见 **`docs/00-overview.md`** 与 **`docs/README.md`**（文档索引）。

---

## 1. 技术栈

- Python **3.10+**（本地请优先使用 `python3`）
- **Django 4.2.x**
- **Django REST Framework** + **SimpleJWT** + **django-filter** + **corsheaders**
- **PyMuPDF（fitz）**、Pillow、pdf2image、pypdf 等（PDF / 图像）

Python 依赖见仓库根目录 **`requirements.txt`**。模板编辑器独立实验目录另有 **`htmlpdf/requirements.txt`**（主站以根目录 `requirements.txt` 为准）。

---

## 2. 目录结构（与当前代码一致）

```text
django/
├── apps/
│   ├── core/                 # Web 视图、模型、权限、文件库、管线入口、HTMLPDF 后端服务（htmlpdf_service）等
│   └── api/                  # REST：urls_v1 / urls_v2、检测与台账 ViewSet / APIView
├── tablet_backend/           # 工程配置：settings、根 urls（Web + /api/v1 + /api/v2）、wsgi
├── templates/                # 全局布局 + core 业务页 + guide 使用说明
├── static/                   # 静态资源
├── media/
│   └── file_library/         # 文件库落盘（uploads、json、templates、…，见 settings）
├── htmlpdf/                  # 模板编辑器单页（templates/index.html）、全文坐标脚本、数据文件等
├── utils/                    # PDF、规则引擎、文档管线、Ollama 等与 Web 解耦的工具
├── docs/                     # 接手说明（配置、Web、API、领域模型、管线、脚本等）
├── manage.py
├── API_DOCUMENTATION.md      # API 说明（维护时请与接口变更同步）
└── README.md                 # 本文件
```

---

## 3. 本地快速启动

### 3.1 虚拟环境（可选）

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3.2 安装依赖

```bash
pip install -r requirements.txt
```

### 3.3 数据库迁移

```bash
python3 manage.py migrate
```

### 3.4 管理员账号（可选）

```bash
python3 manage.py createsuperuser
```

### 3.5 启动开发服务

```bash
python3 manage.py runserver 0.0.0.0:11223
```

浏览器访问：

| 用途 | URL |
|------|-----|
| Web 后台首页 | `http://127.0.0.1:11223/` |
| Django Admin | `http://127.0.0.1:11223/admin/` |
| API v1 | `http://127.0.0.1:11223/api/v1/` |
| API v2（项目—任务等新路由） | `http://127.0.0.1:11223/api/v2/` |

种子数据、演示账号等见 **`docs/06-scripts-and-admin.md`**（如 `init_data`、`ensure_party_a_demo` 等命令）。

---

## 4. 配置与安全

- 主配置：**`tablet_backend/settings.py`**
- 环境变量、文件库路径、JWT、CORS、演示账号 IP 等：**`docs/01-configuration.md`**

开发环境默认：`DEBUG=True`、`ALLOWED_HOSTS=['*']`、数据库 **SQLite**（`db.sqlite3`）、语言 **`zh-hans`**、时区 **`Asia/Shanghai`**。

**生产环境**务必自行设置：`SECRET_KEY`、`DEBUG=False`、`ALLOWED_HOSTS`、数据库、HTTPS、静态与媒体存储、CORS 白名单等。

---

## 5. 核心业务流程（简述）

### 5.1 检测提交 → 现场记录 / 报告 PDF（taskNo / 项目—任务）

1. 客户端调用检测提交相关 API（具体路径见 `API_DOCUMENTATION.md` 与 `docs/03-rest-api.md`）。
2. 后端持久化提交数据，并可在文件库 **检测提交** 分类写入结构化文件。
3. 按项目、任务与模板绑定选择 HTMLPDF 模板与映射规则。
4. 填充字段并生成 PDF，落库到 **现场记录** / **报告** 等分类，并维护与项目、任务的关联。

报告制作、字段归一化等大逻辑主要在 **`apps/api/inspection_report_make.py`**；通用 PDF 填充与模板解析也在 **`apps/api/inspection_pdf_service.py`**。

### 5.2 文件库内手动导出

在 Web **文件库**对应分类中勾选记录后，可触发现场记录 / 报告 PDF 等导出（权限与项目范围见角色与 `library_access`）。与自动链路共用同一套填充与合并规则。

### 5.3 文档识别管线（可选）

上传 **待识别文件** 分类中的 PDF / 图片后，可在 **文档识别** 独立页运行管线（MinerU / Ollama 等由环境变量与 `utils/document_pipeline` 配置；详见 **`docs/05-utils-and-pipelines.md`**）。产出可写入文件库 **数据文件（json）** 分类并参与后续业务。

---

## 6. 映射与模板维护

| 内容 | 位置 |
|------|------|
| 提交占位符 → 模板字段映射 | `apps/api/inspection_submit_placeholder_maps.py` |
| 模板解析、text/check/image 填充、PDF 渲染 | `apps/api/inspection_pdf_service.py` |
| 报告合并、bindings、与 HTMLPDF 协同的大量逻辑 | `apps/api/inspection_report_make.py` |
| 浏览器内 PDF 模板编辑页 | `htmlpdf/templates/index.html` |
| 编辑器后端（导入导出、临时 PDF、build_filled_pdf 等） | `apps/core/htmlpdf_service.py` + `apps/core/views.py` 中 `htmlpdf_*` 与 `htmlpdf_api_*` |
| 公式与判定字段语义（人读） | `docs/json公式说明.md` |

模板与上传文件实体默认在 **`media/file_library/`** 下各子目录（见 settings 中 `FILE_LIBRARY_*`）。

---

## 7. API 文档

- **`API_DOCUMENTATION.md`**：接口列表与约定。
- **`docs/03-rest-api.md`**：`/api/v1` 与 `/api/v2` 分工说明。

接口变更时请同步更新 `API_DOCUMENTATION.md`。

---

## 8. 项目、任务与文件关联（重要）

- 项目挂载任务时会同步任务关联的文件；解除挂载时会按策略移除不应再见的关联。
- 任务侧文件变更会传播到已关联项目（具体规则以代码为准）。
- 文件库列表受 **角色权限** 与 **「仅本人数据」** 等范围控制（见 `apps/core/library_access.py`）。

---

## 9. 常见问题（FAQ）

### Q1：模板里某些栏位没有填上值？

- 核对模板里占位与映射 key 是否一致（含大小写）。
- 核对提交或来源数据中是否存在对应字段。
- 核对 `fieldType`（text / check / image）是否与控件一致。

### Q2：文件库列表或 API 文件列表为空？

- 核对当前用户可见的 **项目 / 任务** 范围与文件是否已关联。
- 核对请求的分类与权限（`perm_file_library` 等）。

### Q3：手动导出报告未生成？

- 核对项目是否关联报告输出任务、是否绑定可解析的 HTMLPDF 模板与数据源（现场记录 / 检测提交等）。

---

## 10. 开发建议

- 先读 **`docs/00-overview.md`**，再按负责范围读 **`docs/02-web-ui-core.md`** 或 **`docs/03-rest-api.md`**。
- 改检测提交流 / 报告 PDF：**`inspection_report_make.py`**、**`inspection_pdf_service.py`**、**`inspection_submit_placeholder_maps.py`**。
- 改模板编辑器交互：**`htmlpdf/templates/index.html`**；改保存契约时再动 **`apps/core/views.py`**（`htmlpdf_api_*`）与 **`htmlpdf_service.py`**。
- 提交前可做语法检查（示例）：

```bash
python3 -m py_compile apps/api/*.py apps/core/*.py
```

---

## 11. 许可

仓库若未包含 `LICENSE`，对外分发前请自行补充许可证并在本段注明。
