# 将 Standalone 评价报告嵌入主 Django 项目

本文说明如何把 `evaluation_report_standalone/` 中的改动，合并回正在运行的 `django/`（tablet_backend）。

Standalone 刻意保持 **`apps.evaluation_report` + `converter/` 与主站同构**；差异只在薄兼容层 `apps.core`（**不要**把 standalone 的 core 拷进主项目）。

---

## 1. 嵌入清单（要拷贝什么）

| Standalone 路径 | 主项目路径 | 说明 |
|-----------------|------------|------|
| `apps/evaluation_report/**` | `django/apps/evaluation_report/**` | 业务视图/服务/模型逻辑 |
| `converter/**`（除 detection/gbz/auto 可选） | `django/converter/**` | 转换与编译 |
| `latex/yp250420/**` | 仓库根 `latex/yp250420/**` | 内置模板（settings `LATEX_TEMPLATE_ROOT`） |
| `templates/evaluation_report/**` | `django/templates/evaluation_report/**` | 页面模板 |
| `templates/includes/markdown_render_block.html` | 同路径 | 若有改动再同步 |

**不要拷贝：**

- `evaluation_report_standalone/apps/core/`（shim）
- `evaluation_report_standalone/config/`
- `evaluation_report_standalone/manage.py`
- standalone 自己生成的 `migrations/`（见第 3 节）

---

## 2. 主项目已具备的挂载（一般无需重做）

主项目已配置好时，确认以下项即可：

### 2.1 `INSTALLED_APPS`

```python
INSTALLED_APPS = [
    # ...
    "apps.core",
    "apps.evaluation_report",
]
```

### 2.2 URL

`apps/core/urls.py`（或根 urls）中：

```python
path("evaluation-reports/", include("apps.evaluation_report.urls")),
```

### 2.3 Settings（`tablet_backend/settings.py`）

```python
LATEX_TEMPLATE_ROOT = REPO_ROOT / "latex"
LATEX_ENGINE = "lualatex"
LATEX_RUN_TIMES = 2
CONVERTER_KEYWORDS_CSV = BASE_DIR / "converter" / "data" / "project_keywords.csv"
EVALUATION_REPORT_WORK_ROOT = MEDIA_ROOT / "evaluation_reports" / "work"
EVALUATION_LATEX_TEMPLATE_ROOT = MEDIA_ROOT / "evaluation_reports" / "latex_templates"
FILE_LIBRARY_EVALUATION_FORM_DIR = FILE_LIBRARY_ROOT / "evaluation_forms"
FILE_LIBRARY_ATTACHMENT_DIR = FILE_LIBRARY_ROOT / "attachments"
```

### 2.4 权限与菜单

- `Role.perm_evaluation_report`
- 菜单 path：`/evaluation-reports/`（`init_data` 等）

### 2.5 依赖接口（主站真实实现）

评价报告 App 通过下列符号依赖 `apps.core`（standalone 用 shim 实现同名接口）：

| Import | 用途 |
|--------|------|
| `apps.core.models.CommissionOrganization` | 医院 FK |
| `apps.core.models.LibraryFile` | 上传文件 FK / 分类常量 |
| `apps.core.library_access.role_has` | `perm_evaluation_report` |
| `apps.core.library_file_service.save_library_binary_uploads` | 落盘 + 建库 |
| `apps.core.pipeline_service.library_absolute_path` | 读文件绝对路径 |

嵌入时保持这些符号可用即可，**无需改 evaluation_report 的 import**。

---

## 3. 同步代码的推荐流程

### 方式 A：手工 / 脚本覆盖（已有评价报告功能时）

在仓库根目录 PowerShell：

```powershell
$src = "evaluation_report_standalone"
$dst = "django"

# 业务 App（排除 migrations，避免冲掉主站迁移历史）
robocopy "$src\apps\evaluation_report" "$dst\apps\evaluation_report" /E `
  /XD migrations __pycache__ /XF *.pyc

# 转换器
robocopy "$src\converter" "$dst\converter" /E /XD __pycache__ templates

# 页面模板
robocopy "$src\templates\evaluation_report" "$dst\templates\evaluation_report" /E

# LaTeX 内置工程（按需）
robocopy "$src\latex\yp250420" "latex\yp250420" /E /XD __pycache__
```

然后在主项目：

```powershell
cd django
python manage.py makemigrations evaluation_report   # 仅当模型有变更
python manage.py migrate
python manage.py check
```

### 方式 B：首次嵌入（主项目尚无该 App）

1. 复制上表全部业务文件（**含**主项目仓库中已有的 `evaluation_report/migrations/`，不要用 standalone 的新迁移）。
2. 若主项目还没有 migrations：从主站 git 历史取，或在主站对完整 `apps.core` 跑 `makemigrations evaluation_report`。
3. 配置 URL / settings / 权限（第 2 节）。
4. `migrate` + 重启 `runserver`。

> Standalone 的 migrations 是相对薄 `core` 重新生成的，**表结构意图一致，但依赖图不同**，切勿直接替换主站 migration 文件。

---

## 4. 模型变更时的迁移策略

1. 在 **standalone** 改 `models.py`，本地 `makemigrations` + `migrate` 验证。
2. 把 `models.py`（及相关代码）同步到主项目。
3. **在主项目**再执行 `makemigrations evaluation_report`，生成依赖真实 `core` 的 migration。
4. 主项目 `migrate`。

---

## 5. 联调检查清单

- [ ] `/evaluation-reports/` 列表可打开（角色有 `perm_evaluation_report`）
- [ ] 医院下拉有数据（`CommissionOrganization` level=hospital）
- [ ] 内置模板：`seed_bundled_templates()` 或后台已有 `yp250420`
- [ ] 上传评价信息表 → 预览 MD/TeX
- [ ] 「生成报告 PDF」成功，`lualatex` 可用
- [ ] 文件库中可见评价信息表 / 附件分类（主站文件库分组 UI 可选）

---

## 6. 开发约定（避免再次分叉）

1. **优先在 standalone 改** `evaluation_report` / `converter` / 相关 templates，验证通过后再同步。
2. 不要在 standalone 的 `evaluation_report` 里写死 `config.settings` 或 standalone 专用路径；继续用 `django.conf.settings` 的 `LATEX_*` / `EVALUATION_*` / `FILE_LIBRARY_*`。
3. 新增对 `apps.core` 的依赖时：先在 standalone shim 补同名 API，并在本文第 2.5 节登记；再在主站补真实实现。
4. 检测报告（`detection_report_md2latex` 等）与本模块无关，可不同步。

---

## 7. 架构对照

```
┌─────────────────────────────────────────┐
│ evaluation_report_standalone            │
│  apps.evaluation_report  ──┐            │
│  converter/                │ 同构       │
│  templates/evaluation_*    │            │
│  apps.core (shim)  ◄───────┘ 仅本地     │
└─────────────────────────────────────────┘
                    │ 同步业务代码
                    ▼
┌─────────────────────────────────────────┐
│ django (tablet_backend)                 │
│  apps.evaluation_report                 │
│  converter/                             │
│  apps.core (完整：权限/文件库/组织)      │
│  URL: /evaluation-reports/              │
└─────────────────────────────────────────┘
```

---

## 8. 端口建议

| 环境 | 建议端口 |
|------|----------|
| 主项目 | `22334`（或 `11223`） |
| Standalone | `22335` |

避免与主站 `runserver` 抢端口。
