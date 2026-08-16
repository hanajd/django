# 评价报告表制作 · 独立开发版（f1_eval_standalone）

从主仓库 `django/` 项目中提取的 **F.1 预评价报告表制作** 功能最小闭包。
在此目录内独立开发、调试该功能；开发完成后按下方「回嵌真实后台」说明整体迁回。

**完整嵌入说明（含如何带走已调模板、且不影响现有库与其它功能）：见 [EMBED_INTO_DJANGO.md](./EMBED_INTO_DJANGO.md)。**

## 快速开始

```powershell
cd f1_eval_standalone
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

浏览器访问 http://127.0.0.1:8000/ ，登录后自动进入 `files/f1-eval/` 工作台。

## 目录结构与组件来源

| 目录 / 文件 | 来源 | 说明 |
| --- | --- | --- |
| `apps/core/f1_eval_views.py` | `django/apps/core/` 原样复制 | 13+ 个视图端点（工作台 / 栏目 / 表格 / 附图 / 生成 / 下载） |
| `apps/core/f1_eval_service.py` | 同上 | 工作区管理、JSON 存储、调用 f1_eval_report 生成 PDF |
| `apps/core/f1_eval_editor.py` | 同上 | 栏目 / 表格编辑逻辑 |
| `apps/core/f1_eval_table_preview.py` | 同上 | 表格 HTML 预览 |
| `apps/core/f1_eval_grid.py` | 同上 | 表格网格模型 |
| `apps/core/library_access.py` | **桩**（仅独立版） | 只提供 `role_has`，已登录即放行 |
| `apps/core/urls.py` | 新写 | f1-eval 路由与真实后台逐条一致 + dashboard 兜底 |
| `templates/core/f1_eval_workbench.html` | `django/templates/core/` 原样复制 | 工作台前端（2100+ 行） |
| `templates/base.html` | **极简替代**（仅独立版） | Tailwind CDN + remixicon CDN |
| `templates/registration/login.html` | 新写 | 独立版登录页 |
| `f1_eval_report/` | 仓库根目录完整复制 | 报告组装引擎（sections / base / embed / templates 模板库） |
| `radiation_detection_report/` | 仓库根目录最小子集 | PDF 绘制引擎 12 个模块 + fonts + layout |
| `config/`、`manage.py` | 新写 | 最小 Django 壳（sqlite、无自定义 models） |

注：主仓库中 `radiation_detection_report` 有两份，**生效的是仓库根目录版**
（`django/tablet_backend/settings.py` 将仓库根插到 sys.path 最前），本目录复制的即根目录版。

## 与真实后台的耦合点（回嵌时关注）

1. **权限**：f1_eval 只通过 `apps.core.library_access.role_has(user, "perm_file_library")`
   接入权限系统。回嵌时删除本目录的 `apps/core/library_access.py` 桩即可自动接回真实实现。
2. **dashboard**：视图权限不足时 `redirect(reverse("dashboard"))`，真实后台已有该路由。
3. **base.html**：workbench 只用到 `block title / page_title / content` 与
   Tailwind 工具类、remixicon 图标；真实后台的 base.html 已满足。
4. **settings**：真实后台需存在（当前已有）：
   - `F1_EVAL_PACKAGE_DIR`（指向 f1_eval_report 包目录）
   - `F1_EVAL_WORKSPACE_ROOT`（默认 `MEDIA_ROOT/f1_eval/workspaces`）
   - sys.path 能导入 `f1_eval_report` 与 `radiation_detection_report`

## 回嵌真实后台步骤

1. 覆盖复制 `apps/core/f1_eval_*.py` 5 个文件 → 真实后台 `apps/core/`
   （**不要**复制 `library_access.py` 与 `urls.py`）。
2. 覆盖复制 `templates/core/f1_eval_workbench.html` → 真实后台 `templates/core/`。
3. 将 `apps/core/urls.py` 中「F.1 预评价报告表制作」区块同步回真实 `urls.py`
   （若本次开发新增了路由；URL name 不可改动）。
4. 覆盖同步 `f1_eval_report/` 与 `radiation_detection_report/` 到仓库根目录对应包。
5. 数据无迁移负担：功能不使用 ORM，用户数据都在
   `MEDIA_ROOT/f1_eval/workspaces/user_{id}/` 的 JSON / 文件中。

## 注意事项

- 本独立版无自定义数据库模型，`migrate` 仅建 Django 自带表（auth / session 等）。
- `templates/base.html` 使用 Tailwind / remixicon 公网 CDN，仅限本地开发；
  真实后台使用自托管静态资源，不受影响。
- 生成 PDF 依赖 `radiation_detection_report/fonts/` 内字体（SIMSUN.TTC 等），已随包复制。
