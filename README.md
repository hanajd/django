# Django 检测业务后台与 API（tablet_backend）

放射检测 / 平板业务的 **Web 后台** 与 **REST API**（工程包名 `tablet_backend`）。

| 项 | 说明 |
|----|------|
| Web | 服务端渲染（Django 模板 + Tailwind CDN） |
| API | JWT / Session；**`/api/v2/` 推荐**，`/api/v1/` 兼容 |
| 默认端口 | **`11223`**（可用环境变量 `DJANGO_PORT` 覆盖） |
| Python | **3.12.x**（见 `requirements.txt`；核心 Web/API 在 3.10+ 亦可试跑） |

## 主要能力

- **检测主链路**：文件库、项目工作台、委托/医院、任务模板、HTMLPDF 模板编辑、报告流程枢纽（`/files/hub/*`）、仪器台账、App 检测提交 → 现场记录/报告 PDF  
- **评价报告表（F.1）**：`/files/f1-eval/`（`perm_f1_eval`）  
- **评价报告书（LaTeX）**：`/evaluation-reports/`（`perm_evaluation_report`）  
- **其它**：RBAC、使用说明/流程练习、Android OTA（`/settings/app-ota/` + `/api/v2/app/*`）

三条「报告」勿混：

| 名称 | 入口 | 产物 |
|------|------|------|
| 检测报告 | 文件库 / hub / App | 防护检测 PDF |
| 评价报告表 F.1 | `/files/f1-eval/` | F.1 表单 PDF |
| 评价报告书 | `/evaluation-reports/` | LaTeX 编译 PDF |

接手与全面掌握请从 **[docs/README.md](docs/README.md)** 开始（含阅读路径与全索引）。功能总览见 [docs/07-features-catalog.md](docs/07-features-catalog.md)。

---

## 1. 技术栈

- **Django 4.2** + DRF + SimpleJWT + django-filter + corsheaders  
- PDF / 图像：PyMuPDF、pypdf、PyPDF2、Pillow、pdf2image  
- 办公文档：openpyxl、python-docx、lxml（评价报告书转换等）  
- 可选 OCR 管线：MinerU、Ollama SDK、outlines  
- 前端辅助（公式叠印 / 引导）：根目录 `package.json`（`mathjax-full`、`driver.js`）

依赖安装：

```bash
pip install -r requirements.txt
npm install   # 需要公式渲染或流程练习时
```

模板编辑器实验目录另有 `htmlpdf/requirements.txt`；**主站以根目录 `requirements.txt` 为准**。

---

## 2. 目录结构（摘要）

```text
django/
├── apps/
│   ├── core/                 # Web、主模型、文件库、F.1、枢纽、HTMLPDF 后端
│   ├── api/                  # REST：urls_v1 / urls_v2、检测、台账、OTA
│   └── evaluation_report/    # 评价报告书（LaTeX）
├── tablet_backend/           # settings、根 urls、wsgi/asgi
├── templates/ · static/ · htmlpdf/
├── utils/                    # 规则引擎、管线、PDF/公式工具
├── converter/ · latex/       # 评价报告书 MD↔LaTeX 与 TeX 工程
├── f1_eval_report/           # F.1 PDF 生成库（主站 import）
├── media/                    # 运行时落盘（file_library、evaluation_reports、f1_eval、apk…）
├── docs/                     # 维护文档（从 README.md 进入）
├── manage.py
└── requirements.txt
```

并行/独立工程（**非** `INSTALLED_APPS`）：`evaluation_report_standalone/`、`f1_eval_standalone/` 等。详见 [docs/00-overview.md](docs/00-overview.md)。

---

## 3. 本地快速启动

```bash
python3 -m venv .venv && source .venv/bin/activate   # 建议
pip install -r requirements.txt
python3 manage.py migrate
python3 manage.py init_data                 # 角色/菜单种子（按需）
python3 manage.py createsuperuser           # 可选
python3 manage.py runserver 0.0.0.0:11223
```

| 用途 | URL |
|------|-----|
| Web 后台 | `http://127.0.0.1:11223/` |
| Django Admin | `http://127.0.0.1:11223/admin/` |
| API v2 | `http://127.0.0.1:11223/api/v2/` |
| API v1 | `http://127.0.0.1:11223/api/v1/` |
| 评价报告书 | `http://127.0.0.1:11223/evaluation-reports/` |
| F.1 评价报告表 | `http://127.0.0.1:11223/files/f1-eval/` |

其它种子命令（演示账号、产品目录、评价报告书模板等）见 [docs/06-scripts-and-admin.md](docs/06-scripts-and-admin.md)。组织主任演示账号见 [docs/组织主任账号说明.md](docs/组织主任账号说明.md)。

评价报告书编译还需本机 **TeX**（lualatex/xelatex 等）及中文字体；详见 [docs/评价报告书-LaTeX功能说明.md](docs/评价报告书-LaTeX功能说明.md)。

---

## 4. 配置与安全

- 主配置：`tablet_backend/settings.py`  
- 环境变量、媒体路径、JWT/CORS、演示账号加固：[docs/01-configuration.md](docs/01-configuration.md)  
- **整机搬迁（库 + media + 依赖）**：[docs/打包与新设备安装说明.md](docs/打包与新设备安装说明.md)  
- 生产常驻与加固：[docs/部署流程-从零安装与源码保护评估.md](docs/部署流程-从零安装与源码保护评估.md)

开发默认：`DEBUG=True`、`ALLOWED_HOSTS=['*']`、SQLite（`db.sqlite3`）、`zh-hans` / `Asia/Shanghai`。

**生产**务必设置：`SECRET_KEY`、`DEBUG=False`、`ALLOWED_HOSTS`、数据库、HTTPS、静态/媒体、CORS 白名单。勿提交密钥与 `.env`。

---

## 5. 核心业务（简述）

### 5.1 检测：提交 → 现场记录 / 报告 PDF

1. App 或 Web 模拟走检测 API（v2：项目 → 任务 → start/draft/submit）  
2. 写入 `InspectionSubmission` 与文件库「检测提交」等分类  
3. `inspection_report_make` 按任务模板绑定回填 HTMLPDF → 现场记录 / 报告 PDF  

主逻辑：`apps/api/inspection_report_make.py`、`inspection_pdf_service.py`。专题：[docs/现场记录生成报告流程说明.md](docs/现场记录生成报告流程说明.md)。

### 5.2 报告流程枢纽

`/files/hub/site-records/` → `report-generate/` → `review-sign/` → `report-download/`。服务：`apps/core/workflow_hub_service.py`。

### 5.3 文档识别管线（可选）

`/files/process/` 或 API OCR；MinerU + Ollama，见 [docs/05-utils-and-pipelines.md](docs/05-utils-and-pipelines.md)。未安装时可降级，不影响日常文件库与报告导出。

### 5.4 评价业务

- F.1：[docs/评价报告表-F1功能说明.md](docs/评价报告表-F1功能说明.md)  
- 评价报告书：[docs/评价报告书-LaTeX功能说明.md](docs/评价报告书-LaTeX功能说明.md)

---

## 6. API 文档

| 文档 | 说明 |
|------|------|
| [docs/03-rest-api.md](docs/03-rest-api.md) | v1 / v2 分工与维护约定 |
| [docs/API_DOCUMENTATION_V2.md](docs/API_DOCUMENTATION_V2.md) | v2 接口说明（优先） |
| [docs/API_DOCUMENTATION_V1.md](docs/API_DOCUMENTATION_V1.md) | v1 兼容 |
| 根目录 `openapi.yaml` | 草稿；**以 `apps/api/urls_v2.py` 为准** |

---

## 7. 权限与文件关联（要点）

- 权限矩阵与「仅本人数据」：`apps/core/library_access.py`  
- 项目挂载任务会同步关联文件；解除挂载按策略清理可见关联  
- 评价能力：`perm_f1_eval` / `perm_evaluation_report`（与检测岗位权限分离）

---

## 8. 常见问题

**模板栏位未填上**  
核对占位/映射 key、`fieldType`（text/check/image）与提交数据是否一致。见 [docs/json公式说明.md](docs/json公式说明.md)。

**文件库或 API 列表为空**  
核对用户可见项目/任务范围、文件是否已关联、`perm_file_library` 等。

**手动导出报告失败**  
核对项目是否关联报告输出任务、模板绑定与现场记录/提交数据源。

**OCR / 管线失败**  
查 `media/file_library/temp`、`MINERU_BACKEND`、`OLLAMA_HOST`；无 GPU 时用默认 `pipeline` 后端。

**评价报告书编译失败**  
查本机 TeX、`latex/` 宏包、工作区 `media/evaluation_reports/` 权限。

---

## 9. 开发建议

1. 先读 [docs/README.md](docs/README.md) → [docs/00-overview.md](docs/00-overview.md) → [docs/07-features-catalog.md](docs/07-features-catalog.md)  
2. 改检测/报告 PDF：`apps/api/inspection_report_make.py` 等  
3. 改 Web 路由：`apps/core/urls.py`；改 API：优先扩展 **v2**  
4. 改评价报告书：`apps/evaluation_report/` + `converter/` + `latex/`  
5. 改 F.1：`apps/core/f1_eval_*.py` + `f1_eval_report/`  

```bash
python3 -m py_compile apps/api/*.py apps/core/*.py apps/evaluation_report/*.py
```

改路由 / 权限 / 模型后，请同步更新 `docs/07-features-catalog.md` 与相关专题。

---

## 10. 许可

仓库若未包含 `LICENSE`，对外分发前请自行补充许可证并在本段注明。
