# 将 f1_eval_standalone 嵌入现有 Django 项目

本文说明如何把当前独立版「评价报告表制作（F.1）」合回你正在使用的 Django 后台，并带走已调好的模板，同时：

- **不覆盖、不迁移、不改写**现有业务数据库；
- **不影响**其他功能（文件库、检测流程、用户角色等）；
- **可带回**包内默认模板 + 工作区里已编辑过的常用模板。

独立版入口目录：`f1_eval_standalone/`  
真实后台示例路径（本仓库）：`django/`（以你实际项目根为准，下文称「目标 Django」）。

---

## 1. 为什么不会动现有数据库

| 点 | 说明 |
| --- | --- |
| 无 F.1 专用 Model | `f1_eval_*` 不建表，不写 ORM |
| 数据在文件里 | 用户工作区位于 `MEDIA_ROOT/f1_eval/workspaces/user_{id}/`（JSON、上传图、PDF 输出） |
| migrate 无新增 | 回嵌后**不必**为 F.1 再跑业务迁移；已有 `auth` / 业务表保持原样 |
| 独立版 `db.sqlite3` | 仅本地调试用，**不要**拷进生产 / 目标项目 |

结论：嵌入 = 拷代码 + 拷模板/字体 + 接路由与配置；**不要**导入独立版数据库。

---

## 2. 组件清单（拷什么 / 不拷什么）

### 2.1 必须同步的代码与资源

从 `f1_eval_standalone/` 拷到目标 Django（或仓库根，见下表）：

| 来源（独立版） | 目标位置 | 操作 |
| --- | --- | --- |
| `apps/core/f1_eval_views.py` | `apps/core/` | **覆盖**同名文件 |
| `apps/core/f1_eval_service.py` | 同上 | 覆盖 |
| `apps/core/f1_eval_editor.py` | 同上 | 覆盖 |
| `apps/core/f1_eval_grid.py` | 同上 | 覆盖 |
| `apps/core/f1_eval_table_preview.py` | 同上 | 覆盖 |
| `apps/core/f1_eval_projects.py` | 同上 | 覆盖 |
| `templates/core/f1_eval_workbench.html` | `templates/core/` | 覆盖 |
| `f1_eval_report/` 整包 | 仓库根 `f1_eval_report/`（与 `settings.F1_EVAL_PACKAGE_DIR` 一致） | 整目录同步（建议 robocopy / rsync） |
| `radiation_detection_report/` 整包 | 仓库根 `radiation_detection_report/` | 整目录同步（含 `fonts/`） |

本仓库 Django 的配置约定（`django/tablet_backend/settings.py`）：

```text
_REPO_ROOT = BASE_DIR.parent          # 即 pandoctest/
F1_EVAL_PACKAGE_DIR = _REPO_ROOT / 'f1_eval_report'
F1_EVAL_WORKSPACE_ROOT = MEDIA_ROOT / 'f1_eval' / 'workspaces'
```

`sys.path` 已把仓库根插到最前，因此引擎包放在**仓库根**，不要只放进 `django/` 子目录却改不了导入路径。

### 2.2 禁止拷贝（避免破坏现网）

| 文件 / 目录 | 原因 |
| --- | --- |
| `apps/core/library_access.py` | 独立版是「已登录即放行」桩；目标项目已有真实权限实现 |
| `apps/core/urls.py` 整文件覆盖 | 独立版路由极简；应**只合并** F.1 路由段（见 §4） |
| `config/`、`manage.py`、`db.sqlite3` | 独立 Django 壳，与目标项目无关 |
| `templates/base.html`、`templates/registration/` | 独立版 CDN 壳；目标项目用自有布局 |
| 整份 `media/` 盲目覆盖 | 可能冲掉目标项目其他 media；按 §5 选择性拷贝工作区模板 |

### 2.3 Python 依赖

目标环境需已具备（与 `requirements.txt` 一致）：

```text
Django>=4.2,<5.0   # 以你现网版本为准，不必降级
PyMuPDF>=1.23
openpyxl>=3.1.0
```

缺则 `pip install PyMuPDF openpyxl`，**不要**为 F.1 单独换一套 Django 主版本。

---

## 3. settings 检查（一般已有，勿改库配置）

在目标 `settings.py` 中确认（有则保留，无则追加；**不要改 `DATABASES`**）：

```python
from pathlib import Path
import sys

# 示例：仓库根在 Django 项目上一级
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # 按实际调整
# 保证能 import f1_eval_report / radiation_detection_report
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

MEDIA_ROOT = ...  # 保持现有，不要换成独立版路径

F1_EVAL_PACKAGE_DIR = _REPO_ROOT / "f1_eval_report"
F1_EVAL_WORKSPACE_ROOT = Path(MEDIA_ROOT) / "f1_eval" / "workspaces"

# 信息表 / 附件可能较大（若已有更大值可不动）
DATA_UPLOAD_MAX_MEMORY_SIZE = max(getattr(settings, "DATA_UPLOAD_MAX_MEMORY_SIZE", 0) or 0, 50 * 1024 * 1024)
FILE_UPLOAD_MAX_MEMORY_SIZE = max(getattr(settings, "FILE_UPLOAD_MAX_MEMORY_SIZE", 0) or 0, 50 * 1024 * 1024)
```

`DEBUG` 下需能通过 URL 访问 `MEDIA`（目标项目一般已有 `static(settings.MEDIA_URL, ...)`）。

权限：视图通过 `apps.core.library_access.role_has(user, "perm_file_library")` 校验。目标项目已有该函数即可；角色需打开「文件库访问」一类权限。

---

## 4. 路由：只合并 F.1 段，不覆盖全局 urls

1. 打开独立版 `apps/core/urls.py`，复制注释块 **「F.1 预评价报告表制作」** 下全部 `path(...)`（约 `files/f1-eval/` 开头的那些）。
2. 粘贴进目标 `apps/core/urls.py`（或你集中挂载文件库路由的文件）。
3. **不要**复制独立版的：
   - `path('', RedirectView... name='dashboard')`（目标已有仪表盘）
4. **URL name 必须保持不变**（工作台 `{% url 'f1_eval_workbench' %}` 等依赖同名）。

入口地址：`/files/f1-eval/`（登录且具备文件库权限后）。

若侧边栏用动态菜单，在菜单管理里加一项指向该 URL 即可，无需改其他业务菜单。

---

## 5. 把「做好的模板」一并带过去

模板分两层，回嵌时建议**两层都带**：

### 5.1 包内默认模板（所有新用户的种子）

路径：`f1_eval_standalone/f1_eval_report/templates/`  
（含 `common/`、`body/`、`front/`、`back/`、`catalog.json`、样式图资源等）

同步整包 `f1_eval_report/` 时已经覆盖。  
首次进入工作台时，`ensure_workspace()` 会把包内模板**复制到用户工作区**（已存在文件**不覆盖**）。

若你在独立版里改过包内文件，务必以独立版 `f1_eval_report/templates/` 为准覆盖仓库根同名目录。

### 5.2 工作区已调模板（你当前账号下的成品）

独立版调试账号工作区示例：

```text
f1_eval_standalone/media/f1_eval/workspaces/user_1/templates/
```

其中 `common/`（防护措施表、健康影响、放射管理等 md/json）、`body/`、`catalog.json` 等若已手工改过，需要拷到目标用户工作区，否则线上仍是旧种子。

**推荐做法（不冲掉他人数据）：**

```text
# 仅拷模板，不拷整个 media、不拷别人的 projects
源:  .../f1_eval_standalone/media/f1_eval/workspaces/user_1/templates/
目标: <目标 MEDIA_ROOT>/f1_eval/workspaces/user_<目标用户ID>/templates/
```

PowerShell 示例（按实际 ID / 路径改）：

```powershell
$src = "C:\path\to\f1_eval_standalone\media\f1_eval\workspaces\user_1\templates"
$dst = "C:\path\to\django\media\f1_eval\workspaces\user_3\templates"  # 换成真实用户 id
New-Item -ItemType Directory -Force -Path $dst | Out-Null
robocopy $src $dst /E /XO
# /XO = 跳过目标更新的文件；若要以独立版为准强制覆盖，去掉 /XO
```

可选：若还要带走某个测试项目数据（非必须）：

```text
源:  .../workspaces/user_1/projects/<project_id>/
目标: .../workspaces/user_<id>/projects/<project_id>/
```

并同步该用户的 `projects/index.json`、`current_project.json`（注意合并，勿直接抹掉目标已有项目列表）。

### 5.3 版式 JSON

包内：`f1_eval_report/base/format.json`、`attachment_format.json`  
工作区：`workspaces/user_*/base/`（用户改过页边距等才会有）

同步包即可；若工作区有改过的 `base/*.json`，一并拷到对应用户 `base/`。

### 5.4 样式图 PNG 等资源

包内常见位置：`f1_eval_report/templates/common/protection_measures/style_figures/`  
工作区/项目：`assets/`、`uploads/`  

随 `f1_eval_report` 同步包内图；项目级图随 `projects/` 按需拷贝。

---

## 6. 推荐操作顺序（清单）

1. **备份**目标项目代码与 `MEDIA_ROOT/f1_eval`（若已有）。
2. **不要**动 `DATABASES`、**不要**执行与 F.1 无关的 `migrate` 来「导入」独立版库。
3. 同步 `f1_eval_report/`、`radiation_detection_report/`（含 fonts）到仓库根。
4. 覆盖 `apps/core/f1_eval_*.py`（6 个）与 `templates/core/f1_eval_workbench.html`。
5. **合并** F.1 路由段；确认未覆盖 `library_access.py`。
6. 核对 `F1_EVAL_PACKAGE_DIR` / `F1_EVAL_WORKSPACE_ROOT` / `sys.path`。
7. 将独立版 `user_1/templates/`（及需要的 `base/`、项目）拷到目标 `user_<id>/`。
8. 安装/确认 `PyMuPDF`、`openpyxl`。
9. 重启服务；用有「文件库」权限的账号打开 `/files/f1-eval/`。
10. 抽查：打开常用模板、生成一份 PDF、信息表提取、样式图左右合页与平面布局独立页。

---

## 7. 与现有功能的隔离关系

```text
目标 Django
├── 业务 ORM / 现有 migrate          ← 不动
├── library_access（真实权限）        ← 不动；F.1 只调用 role_has
├── 其他 apps / 菜单 / 管线           ← 不动
├── MEDIA
│   ├── file_library / …             ← 不动
│   └── f1_eval/workspaces/…         ← 仅 F.1 读写此树
├── f1_eval_report / radiation_…     ← 报告引擎（无 DB）
└── apps/core/f1_eval_* + workbench  ← 仅增加/更新这些文件与路由
```

权限不足时视图会 `redirect("dashboard")`，与现网仪表盘衔接，不另起登录体系。

---

## 8. 回滚

若需撤回本次嵌入：

1. 用备份恢复被覆盖的 `f1_eval_*.py`、`f1_eval_workbench.html`、两引擎包。
2. 从 urls 中删除 F.1 路由段。
3. 可选删除 `MEDIA_ROOT/f1_eval/`（仅影响报告表工作区文件，不影响数据库）。

---

## 9. 常见问题

**Q: 嵌入后仍是旧版 PDF / 旧模板？**  
A: 检查 `F1_EVAL_PACKAGE_DIR` 是否指向刚同步的包；工作区 `templates/` 若已存在，种子**不会**自动覆盖——需按 §5.2 手工拷贝或删除对应用户工作区模板后再进工作台重建。

**Q: 独立版能用、嵌入后 403 / 跳仪表盘？**  
A: 账号缺少 `perm_file_library`（或等价文件库权限），在角色管理中打开。

**Q: 字体缺失、中文乱码？**  
A: 确认 `radiation_detection_report/fonts/`（如 `SIMSUN.TTC`）已同步。

**Q: 能否多台服务器共用工作区？**  
A: 工作区是本地文件树；多机需共享 `MEDIA_ROOT`（NFS 等），仍与数据库无关。

---

## 10. 一键同步参考脚本（可选）

在仓库根执行（路径按本机修改；**先 dry-run 看列表**）：

```powershell
$StandAlone = "C:\Users\13785\Desktop\pandoctest\f1_eval_standalone"
$RepoRoot   = "C:\Users\13785\Desktop\pandoctest"
$Django     = Join-Path $RepoRoot "django"

# 引擎包 → 仓库根
robocopy (Join-Path $StandAlone "f1_eval_report") (Join-Path $RepoRoot "f1_eval_report") /E /XD __pycache__ .git
robocopy (Join-Path $StandAlone "radiation_detection_report") (Join-Path $RepoRoot "radiation_detection_report") /E /XD __pycache__ .git

# 视图与工作台 → Django
$coreFiles = @(
  "f1_eval_views.py","f1_eval_service.py","f1_eval_editor.py",
  "f1_eval_grid.py","f1_eval_table_preview.py","f1_eval_projects.py"
)
foreach ($f in $coreFiles) {
  Copy-Item -Force (Join-Path $StandAlone "apps\core\$f") (Join-Path $Django "apps\core\$f")
}
Copy-Item -Force `
  (Join-Path $StandAlone "templates\core\f1_eval_workbench.html") `
  (Join-Path $Django "templates\core\f1_eval_workbench.html")

# 工作区模板（改目标 user_id）
# robocopy ...\user_1\templates ...\django\media\f1_eval\workspaces\user_X\templates /E
```

同步后**手动检查** urls 合并与 settings，再重启服务。

---

## 11. 文档与独立版 README 的关系

- 日常在独立版开发、调试：见同目录 `README.md`。
- **合回生产 / 现用 Django**：以本文为准。
- 嵌入完成后，仍可继续在 `f1_eval_standalone` 迭代，再按本文 §6 增量同步。
