# 00 项目总览

> 新人第一篇。读完应能：说出三线业务、画清目录边界、本地跑起 Web。  
> 功能清单见 [07-features-catalog.md](07-features-catalog.md)；阅读顺序见 [README.md](README.md)。

**整理日期**：2026-08-20

---

## 1. 项目定位

本仓库是 **放射检测 / 平板业务的后台管理系统** 与 **REST API**（工程包名 `tablet_backend`）。

### 1.1 Web 后台（SSR）

| 能力 | 说明 |
|------|------|
| 认证与 RBAC | 登录、用户/角色/菜单、权限位与用户级覆盖 |
| 文件库 | OCR/JSON/模板/现场记录/报告/附件/检测提交；预览、回收站、合并报告 |
| 项目工作台 / 委托管理 | 立项、派工、仪器、进度 |
| 报告流程枢纽 | `/files/hub/*`：现场记录 → 生成 → 审核签字 → 下载 |
| 医院信息 / 产品目录 | 委托单位树、设备、销售产品挂载 |
| 任务模板库 | 分类树、报告/现场模板绑定、仪器种类、历史回溯 |
| HTMLPDF 模板编辑器 | 划框、红框识别、统一/前端 JSON、报告-现场映射 |
| 文档识别管线 | MinerU + Ollama（可选） |
| 仪器台账 | 登记、出库/入库 |
| **评价报告表（F.1）** | `/files/f1-eval/`，表单 PDF |
| **评价报告书（LaTeX）** | `/evaluation-reports/`，结构化编辑 + 编译 PDF |
| App OTA 发版 | `/settings/app-ota/` |
| 使用说明 / 流程练习 | Driver.js（限演示账号） |

### 1.2 REST API

- JWT（+ Session）；**v2 推荐**（项目→任务），v1 兼容  
- 检测：开始/草稿/提交、OCR、签名、附件、导出前端 JSON、报告 PDF  
- 台账 registry：受检单位、联系人、设备、仪器（只读）、案件、现场记录、报告  
- **Android OTA**：`/api/v2/app/version`、`/api/v2/app/apk/<file>`

### 1.3 三条报告线（必记）

| 线 | URL / 权限 | 技术要点 |
|----|------------|----------|
| 检测报告 | 文件库 + hub + App | HTMLPDF 模板回填；`InspectionSubmission` |
| 评价报告表 F.1 | `/files/f1-eval/`，`perm_f1_eval` | 数据主要在 `media/f1_eval/`；生成库 `f1_eval_report/` |
| 评价报告书 | `/evaluation-reports/`，`perm_evaluation_report` | 工作区 LaTeX；`converter/` + `latex/` |

---

## 2. 完整目录地图

```text
django/                          # 仓库根（含 manage.py）
├── manage.py                    # 入口；DJANGO_SETTINGS_MODULE=tablet_backend.settings
├── requirements.txt             # Python 3.12 + Django 4.2 + DRF + MinerU/Ollama 等
├── package.json                 # Node：driver.js、mathjax-full（引导与公式渲染）
├── openapi.yaml                 # API 草稿（以 urls_v2 为准核对）
├── tablet_backend/              # 工程配置
│   ├── settings.py
│   ├── urls.py                  # admin + core Web + api/v1 + api/v2
│   └── wsgi.py / asgi.py
├── apps/
│   ├── core/                    # 主业务模型、Web 视图、文件库、F.1、枢纽、HTMLPDF 后端
│   ├── api/                     # REST：检测、台账、OTA
│   └── evaluation_report/       # 评价报告书（LaTeX）
├── templates/                   # SSR：base、core、evaluation_report、login…
├── htmlpdf/                     # 模板编辑器前端页面与脚本
├── static/                      # 静态资源
├── fonts/                       # PDF/报告字体（htmlpdf 常链到此）
├── utils/                       # 规则引擎、管线、PDF/公式、MinerU/Ollama…
├── converter/                   # MD↔LaTeX、评价表正文、关键词 CSV、编译辅助
├── latex/                       # 内置评价报告书工程（yp250420、kp*、linac*、shared）
├── media/                       # 运行时落盘（勿提交密钥）
│   ├── file_library/            # 文件库各分类
│   ├── evaluation_reports/      # 报告书 work / 模板副本 / output
│   ├── f1_eval/                 # F.1 用户工作区
│   ├── apk/                     # OTA APK
│   └── user_signatures/         # 用户签名图
├── docs/                        # 本文档目录
├── scripts/                     # 一次性运维/修复脚本（非 manage 命令）
├── backups/ 、logs/             # 备份与请求统计等
├── f1_eval_report/              # F.1 PDF 生成库（被主站 import）
├── f1_eval_standalone/          # F.1 独立最小工程（非 INSTALLED_APPS）
├── evaluation_report_standalone/# 评价报告书独立工程（非 INSTALLED_APPS）
├── radiation_detection_report/  # 可移植检测结果表 PDF 包
└── report_1/                    # 样例/试验产物（非正式 app）
```

**INSTALLED_APPS（业务）**：仅 `apps.core`、`apps.api`、`apps.evaluation_report`。  
Standalone 目录用于并行开发/回嵌，**不要**当成主站已挂载应用。

---

## 3. 技术栈

| 层 | 选型 |
|----|------|
| 语言 | Python 3.12.x |
| Web | Django 4.2、模板 + Tailwind CDN（`base.html`） |
| API | DRF、SimpleJWT、django-filter、corsheaders |
| DB | 默认 SQLite；可改 MySQL/PostgreSQL |
| 文档 AI | MinerU、Ollama（可选环境） |
| PDF | PyMuPDF、pypdf、报告字体；评价报告书另需 TeX（lualatex/xelatex） |
| 前端库 | Node：`driver.js`、`mathjax-full` |

---

## 4. 本地运行

```bash
cd /path/to/django          # 含 manage.py 的根目录
python3 -m venv .venv && source .venv/bin/activate   # 建议
pip install -r requirements.txt
python manage.py migrate
python manage.py init_data                 # 角色/菜单种子（按需）
python manage.py createsuperuser           # 可选
python manage.py runserver 0.0.0.0:11223   # 端口与 settings 习惯一致
```

常用演示账号见 [组织主任账号说明.md](组织主任账号说明.md)（`ensure_org_directors`）。

生产必须：`SECRET_KEY`、`DEBUG=False`、`ALLOWED_HOSTS`、数据库、静态/媒体、HTTPS、CORS。完整清单见 [部署流程-从零安装与源码保护评估.md](部署流程-从零安装与源码保护评估.md)。

评价报告书编译还需本机安装 TeX 发行版（含中文字体与 `latex/` 模板依赖宏包）。

---

## 5. URL 挂载总览

| 前缀 | 模块 |
|------|------|
| `/admin/` | Django Admin |
| `/` … `/files/...`、`/users/...`、`/database/...`、`/settings/...` | `apps.core.urls` |
| `/evaluation-reports/` | `apps.evaluation_report.urls` |
| `/api/v1/` | `apps.api.urls_v1` |
| `/api/v2/` | `apps.api.urls_v2`（含 OTA） |

详情：[02-web-ui-core.md](02-web-ui-core.md)、[03-rest-api.md](03-rest-api.md)、[07-features-catalog.md](07-features-catalog.md)。

---

## 6. 权限位（Role，摘要）

| 字段 | 含义 |
|------|------|
| `perm_manage_users` / `roles` / `menus` | RBAC |
| `perm_file_*` | 文件库读写删预览及范围 |
| `perm_process_pipeline` | OCR 管线 |
| `perm_htmlpdf` | 模板编辑器 |
| `perm_assign_tasks` / `perm_create_library_project` | 派工 / 立项 |
| `perm_biz_registry` | 业务台账类 |
| `perm_f1_eval` | **评价报告表** |
| `perm_evaluation_report` | **评价报告书** |

用户可通过 `UserProfile.perm_overrides` 覆盖。细则在 `apps/core/library_access.py`。

---

## 7. 配置与运行时文件

| 位置 | 用途 |
|------|------|
| `tablet_backend/settings.py` | 主配置；`FILE_LIBRARY_*`、`EVALUATION_*`、`F1_*`、MinerU/Ollama |
| 根目录 `*_runtime.json` | 可选热更新：`jwt_runtime`、`app_ota_runtime`、`llm_runtime`、`pdf_fill_runtime`、`decimal_precision_runtime` |
| `converter/data/project_keywords.csv` | 评价报告书关键词 schema 源 |

详见 [01-configuration.md](01-configuration.md)。

---

## 8. 下一步

1. 扫一遍 [07-features-catalog.md](07-features-catalog.md)  
2. 按职责读 [02](02-web-ui-core.md) 或 [03](03-rest-api.md) + [04](04-domain-models-and-services.md)  
3. 深入一条业务线（检测 / F.1 / 评价报告书）对应专题
