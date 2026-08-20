# 05 工具库与管线（utils + 管线集成）

**整理日期**：2026-08-20

---

## 1. `utils/` 目录清单

与 Django App 解耦的共享库；Web/API 应优先经 `pipeline_service`、`htmlpdf_service`、`inspection_*` 等入口调用。

| 模块 | 用途 |
|------|------|
| `document_pipeline.py` | 文档管线主逻辑（MinerU → 清洗 → 抽取） |
| `pipeline_config.py` | 管线目录配置与 settings 同步 |
| `mineru_ops.py` | MinerU 调用封装 |
| `gpu_scheduler.py` | 多 GPU / 显存与 MinerU、Ollama 调度 |
| `ollama_extract.py` | Ollama 辅助结构化抽取 |
| `extract_frontend_template.py` | 从抽取结果辅助生成前端模板 |
| `md_clean.py` / `json_utils.py` | Markdown / JSON 工具 |
| `preview.py` | 预览相关 |
| `frontend_schema_rule_engine.py` | PDF 坐标 → `frontend_form_schema/v1` |
| `frontend_export_pipeline.py` / `frontend_runtime_export.py` | 前端 JSON 导出管线 / runtime 形态 |
| `unified_template_fields.py` | 统一模板字段压缩与存储形态 |
| `pdf_field_formulas.py` | PDF 字段公式合并 |
| `dynamic_form_expression.py` | 动态表单表达式求值 |
| `conditional_field_rules.py` | 条件字段规则 |
| `fit_ref_eval.py` / `fit_ref_expand.py` | 拟合公式求值与展开 |
| `verdict_from_criterion.py` | 判据 → 合格/不合格推断 |
| `report_fill_helpers.py` | 报告填充辅助 |
| `task_bound_instruments.py` | 任务绑定仪器辅助 |
| `pdf_merge.py` | 多报告 PDF 合并 |
| `pdf_compress.py` | PDF 压缩 |
| `pymupdf_fonts.py` | PyMuPDF 字体注册 |
| `mathjax_pdf_render.py` | MathJax 公式渲染进 PDF |

公式 / schema 专题：[FRONTEND_FORM_SCHEMA.md](FRONTEND_FORM_SCHEMA.md)、[json公式说明.md](json公式说明.md)、[拟合公式前后端对接说明.md](拟合公式前后端对接说明.md)。

---

## 2. 管线（Pipeline）

- **入口**：Web `/files/process/`（`pipeline_service`）与 API `POST /library/files/ocr/`。  
- **目录**：与 `settings` 中 `PIPELINE_*`、`FILE_LIBRARY_TEMP_*` 一致；批次落在 `media/file_library/temp`。  
- **外部引擎**：MinerU（`MINERU_BACKEND`）、Ollama（`OLLAMA_HOST`）；未安装时功能降级。  
- **演示账号**：`PARTY_A_DEMO_DISABLE_PIPELINE` 可禁独立管线页。

---

## 3. HTMLPDF 与 PDF 工具链

- 规则引擎 + 统一字段模块与 `htmlpdf_service`、编辑器 `api/*` 联动。  
- 改导出 JSON 时同步考虑：**编辑器前端**、**文件库 template**、**App 填报** 是否同一 schema。  
- 检测回填主路径在 `apps/api/inspection_report_make.py`，内部会用到本目录公式/合并工具。

---

## 4. 其它与 PDF 相关的仓库包

| 目录 | 说明 |
|------|------|
| `converter/` | 评价报告书 MD↔LaTeX、关键词、编译辅助（非 utils） |
| `latex/` | 评价报告书 TeX 工程 |
| `f1_eval_report/` | F.1 表单 PDF 生成库 |
| `radiation_detection_report/` | 可移植检测结果表包 |

---

## 5. 调试建议

| 现象 | 排查 |
|------|------|
| 管线失败 | `media/file_library/temp` 批次、`MINERU_BACKEND` / `OLLAMA_HOST`、GPU 调度日志 |
| 模板保存失败 | `library_file_service` 分类枚举与磁盘路径 |
| 公式/判定不符 | `dynamic_form_expression`、`verdict_from_criterion`、现场同行「不合格」强制逻辑 |
| 合并报告异常 | `pdf_merge` + [合并报告规则说明.md](合并报告规则说明.md) |
