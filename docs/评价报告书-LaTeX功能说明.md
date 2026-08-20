# 评价报告书（LaTeX）功能说明

> **适用范围**：主站 `apps/evaluation_report`（已嵌入 Django 主项目）。  
> **易混概念**：侧栏「评价报告表」为 F.1 表单引擎（`perm_f1_eval`，见 [评价报告表-F1功能说明.md](评价报告表-F1功能说明.md)）；本文是 **「评价报告书」**（`perm_evaluation_report`），走 LaTeX 工作区编译 PDF。
> **最后整理**：2026-08-20

Standalone 副本见 `evaluation_report_standalone/`（独立验证用）；主站行为以本文与 `apps/evaluation_report/` 代码为准。

---

## 1. 定位与入口

| 项 | 说明 |
|------|------|
| App | `apps/evaluation_report/` |
| URL 前缀 | `/evaluation-reports/` |
| 权限键 | `perm_evaluation_report`（侧栏名：**评价报告书**） |
| 门禁 | `library_user_may_access_evaluation_report()`（`apps/core/library_access.py`） |
| 默认模板键 | `yp250420`（后装预评）；其它见 `latex/` 下 `kp*` / `yp_linac*` 等 |

配置（`tablet_backend/settings.py`）：

- `LATEX_TEMPLATE_ROOT` → 仓库 `latex/`
- `EVALUATION_REPORT_WORK_ROOT` → `media/evaluation_reports/work/`
- `EVALUATION_LATEX_TEMPLATE_ROOT` → `media/evaluation_reports/latex_templates/`
- `CONVERTER_KEYWORDS_CSV` → `converter/data/project_keywords.csv`

---

## 2. 用户流程（6 步）

```text
列表 → 新建报告 → ① 评价信息表 → ② 生成基础报告
     → ③ 结构化正文编辑 → ④ 附件管理 → ⑤ 确认生成 PDF → 预览/下载
```

| 步骤 | 路径（相对 `/evaluation-reports/`） | 说明 |
|------|-------------------------------------|------|
| 列表 / 新建 | ``、`create/` | 选医院、报告子类型、LaTeX 模板 |
| ① 评价信息表 | `<pk>/evaluation-form/` | 槽位 `evaluation_form`；上传后可转 Markdown 预览 |
| ② 基础报告 | POST `<pk>/generate-base/` | 抽取关键词、拷贝模板到工作区；可跳过直接进编辑 |
| ③ 正文编辑 | `<pk>/edit/` | 章节块编辑（文本/表/公式/图/流程图）；保存写回工作区 `.tex` |
| ④ 附件 | `<pk>/attachments/` | 按附录栏目上传；见 §4 |
| ⑤ 编译 | POST `<pk>/build/` | 注入关键词与附件后 XeLaTeX/LuaLaTeX 编译 |
| PDF | `<pk>/pdf/`、`download/<kind>/` | 预览 / 下载 |

辅助：

- 项目信息（关键词）：`<pk>/keywords/`
- 通用关键词映射：`keywords/schema/`
- LaTeX 模板库：`templates/`、`templates/<key>/edit/`（模板结构化编辑）

---

## 3. 数据模型（要点）

| 模型 | 作用 |
|------|------|
| `EvaluationLatexTemplate` | 内置/上传模板元数据 |
| `EvaluationReport` | 报告实例：医院、子类型、模板键、状态、工作区、`output_pdf` |
| `EvaluationKeywordSchema` | 通用 `\command` ↔ 中文标签 |
| `EvaluationReportKeyword` | 本报告关键词取值 |
| `EvaluationReportUpload` | 材料槽位（评价信息表 / `attachment_01`…） |
| `EvaluationReportUploadFile` | 槽位内单文件 + **显示选项**（见下） |

### 3.1 附件显示选项（`EvaluationReportUploadFile`，迁移 `0002`）

| 字段 | 含义 |
|------|------|
| `page_mode` | `""` = A4 随文；`a3landscape` = A3 横置整页 |
| `rotate` | `0` / `90` / `180` / `270` |
| `width_percent` | 30–100（相对版心宽度；A3 横置时忽略） |

规范化方法：`normalized_display()`。待上传 staging 的 `manifest.json` 使用同名字段，确认上传时写入 ORM。

---

## 4. 附件管理

实现：`upload_staging.py`、`appendix_service.py`、`templates/evaluation_report/attachments.html`。

1. **选择文件**：本地立刻显示缩略图预览（不必先点「加入待上传」）。
2. **加入待上传**：写入 `work/.../staging/<slot_key>/` + `manifest.json`。
3. **待上传区**：拖动排序、移除；图片可调旋转 / A4·A3 / 宽度（AJAX `stage/display/`）。
4. **确认上传**：入库 `LibraryFile` + `EvaluationReportUploadFile`，并 `apply_attachments_to_latex`。
5. **已上传区**：同样可排序、删、改显示选项（`files/<id>/display/`，立即同步附录）。

附录 LaTeX（`appendix_service`）：

- 普通图：`\includegraphics[angle=…,width=x\linewidth,keepaspectratio]`
- A3 横置：`\BeginAThreeLandscapePage` … 整页图 …（连续多张合并纸张角色 first/middle/last）
- PDF：`\includepdf[pages=-,fitpaper=true,angle=…]`
- 文件落盘：`latex/appendix/attachment_XX_YY.ext`，槽位包装 `attachment_XX.tex`，由 `13-appendix.tex` `\input`

**注意**：编译前 `_prepare_latex_project` 会再次应用关键词与附件；file-backed 长文若磁盘表体结构更优，会优先磁盘并回写 DB（避免旧 DB 覆盖编辑器结果）——见 `keywords_service.apply_file_backed_keywords`。

---

## 5. 结构化编辑与个性化高亮

| 模式 | 入口 | 落盘 |
|------|------|------|
| 报告编辑 | `/evaluation-reports/<pk>/edit/` | `media/.../work/report_<pk>/latex/` |
| 模板库编辑 | `/evaluation-reports/templates/<key>/edit/` | 模板存储（内置只读预览；上传模板可写） |

共用模板：`templates/evaluation_report/structured_edit.html`。

### 5.1 个性化字段高亮（2026-08）

展开 `\command` 时写入编辑标记 `§kw{cmd}§展示文案§/kw§`，前端渲染为：

- CSS 类 **`.se-kw`**：浅蓝底 `#dbeafe`、蓝字、底边（避免仅靠「（…）」括号误判）
- 表格单元格同样高亮（`tabular_codec._cell_html`）
- 整块 `files/*.tex` 注入内容：块级 `.se-block.is-personal`；未填完另加琥珀色 `.is-unfilled`

保存时 `kw_marks_to_commands` → `\cmd{}`；`apply_variable_commands` 仍可按文案回映射。

### 5.2 其它编辑能力

- 表格：合并/列宽/环境；可进独立表格编辑页
- 公式：KaTeX 预览；流程图：TikZ 模型
- 图片：可勾选 A3 横置独占页（正文附图，与附录附件选项独立）

---

## 6. 目录与关键文件

```text
apps/evaluation_report/
  views.py                 # 列表/创建/附件/编译/关键词
  structured_views.py      # 报告结构化编辑
  template_structured_views.py
  appendix_service.py       # 附录注入与图片显示选项
  upload_staging.py        # 待上传暂存
  keywords_service.py      # 关键词 / file-backed / DB↔磁盘
  services.py              # generate_base_report / build_report_pdf
  structured_blocks.py     # 章节解析；§kw 标记
  tabular_codec.py / flowchart_codec.py
  models.py

latex/yp250420/            # 默认内置工程（main.tex、chapters/、files/）
media/evaluation_reports/
  work/report_<id>/        # 工作区（latex/、staging/、images/）
  latex_templates/         # 用户上传模板副本
  output/                  # 编译 PDF
```

迁移：

- `evaluation_report.0001_evaluation_report_embed`（依赖 `core.0073_evaluation_report_embed`）
- `evaluation_report.0002_uploadfile_display_options`（附件显示选项）

---

## 7. 排障提示

| 现象 | 排查 |
|------|------|
| 改了 `files/*.tex` 编译又变回去 | 关键词 DB 覆盖了磁盘；看 `apply_file_backed_keywords` 结构分与回写日志 |
| 附件显示选项无效 | 确认已 migrate `0002`；已上传文件需走 `files/<id>/display/` 并重新 build |
| 个性化只有括号、无蓝底 | 强刷编辑页；确认走结构化编辑而非纯源码预览 |
| 无权访问 | 角色勾选「评价报告书」或评价部角色 / 测试组 |

---

## 8. 相关文档

- 功能总览：[07-features-catalog.md](07-features-catalog.md) §2.8  
- Web 路由：[02-web-ui-core.md](02-web-ui-core.md) §2.7  
- 评价部账号：[组织主任账号说明.md](组织主任账号说明.md)  
- Standalone 嵌入笔记：`evaluation_report_standalone/docs/STANDALONE_GUIDE.md`（与主站可能略有滞后）
