# 评价报告 Standalone

独立运行的**评价报告书制作**子系统，从主项目 `django/` 中抽离，便于单独测试与优化转换/编译链路。业务代码（`apps/evaluation_report`、`converter/`）与主站保持同构，嵌入主项目时见 [docs/EMBEDDING.md](docs/EMBEDDING.md)。

## 功能范围

- 评价报告 CRUD（医院、设备类型、LaTeX 模板）
- **正文结构化编辑**（文本 / 列表 / 表格 / 图片，无需手写 LaTeX）
- LaTeX 模板库（内置 `yp250420` + 上传 ZIP）
- 评价信息表 / 附件上传与预览（Word/PDF → MD → TeX）
- 项目关键词映射（封面/声明字段）
- 一键生成报告 PDF（`lualatex`）

**不包含**：检测任务、文件库完整 UI、HTMLPDF、OCR/MinerU、App API。

## 环境要求

| 组件 | 说明 |
|------|------|
| Python | 3.10+（推荐 conda env `latex`） |
| TeX | MiKTeX / TeX Live，`lualatex` 在 PATH 中 |
| 依赖 | `pip install -r requirements.txt` |

## 快速启动

```powershell
cd evaluation_report_standalone
conda activate latex   # 或你的虚拟环境
pip install -r requirements.txt

python manage.py migrate
python manage.py bootstrap_standalone
python manage.py runserver 0.0.0.0:22335
```

浏览器打开：**http://127.0.0.1:22335/**

默认账号：`admin` / `admin123`

后台挂起（PowerShell）：

```powershell
Start-Process python -ArgumentList "manage.py","runserver","0.0.0.0:22335" `
  -WorkingDirectory "C:\Users\13785\Desktop\markdown2latex\evaluation_report_standalone" `
  -RedirectStandardOutput "runserver.log" -RedirectStandardError "runserver.err" -NoNewWindow
```

## 目录结构

```
evaluation_report_standalone/
├── apps/
│   ├── core/                 # 薄兼容层（医院、文件库、权限）— 仅 standalone 使用
│   └── evaluation_report/    # 与主项目同构的业务 App
├── converter/                # word2md / md2latex / pdf2latex / latex_compiler …
├── latex/yp250420/           # 内置 LaTeX 工程模板
├── templates/                # base + evaluation_report + includes
├── config/                   # settings / urls / wsgi
├── media/                    # 工作区、上传、输出 PDF
├── manage.py
├── requirements.txt
├── README.md                 # 本文件
└── docs/EMBEDDING.md         # 嵌入主 Django 项目说明
```

## 与主项目的关系

| 路径 | Standalone | 主项目 `django/` |
|------|------------|------------------|
| `apps/evaluation_report` | 副本（测试改这里） | 正式运行副本 |
| `converter/` | 副本 | 正式副本 |
| `apps/core` | **薄 shim** | 完整 tablet_backend core |
| `latex/yp250420` | 副本 | 仓库根 `latex/yp250420` |

优化转换/模板逻辑时：在 standalone 改 → 验证 → 按 EMBEDDING.md 同步回主项目。

## 常见问题

**编译 PDF 失败**：确认 `lualatex --version` 可用；查看报告工作区 `media/evaluation_reports/work/report_<id>/` 下日志。

**没有医院可选**：执行 `python manage.py bootstrap_standalone`，或在 `/admin/` 添加 `CommissionOrganization`（level=hospital）。

**权限**：standalone 对已登录用户开放 `perm_evaluation_report`；主项目仍走 Role 权限。
