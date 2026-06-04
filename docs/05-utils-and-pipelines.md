# 05 工具库与管线（utils + 管线集成）

## 1. `utils/` 目录（与业务解耦）

| 模块 | 用途（简述） |
|------|----------------|
| `frontend_schema_rule_engine.py` | 前端/统一模板规则引擎（与 HTMLPDF 导出 JSON 强相关） |
| `unified_template_fields.py` | 统一模板字段压缩与存储形态 |
| `pdf_field_formulas.py` | PDF 字段公式合并 |
| `pdf_merge.py` | PDF 合并 |
| `report_fill_helpers.py` | 报告填充辅助 |
| `dynamic_form_expression.py` | 动态表单表达式 |
| `json_utils.py` / `md_clean.py` | JSON/Markdown 处理 |
| `preview.py` | 预览相关 |
| `document_pipeline.py` / `pipeline_config.py` | 文档管线主逻辑与目录配置同步 |
| `mineru_ops.py` | MinerU 调用封装 |
| `gpu_scheduler.py` | 多 GPU / 显存与 MinerU、Ollama 调度相关 |
| `ollama_extract.py` / `extract_frontend_template.py` | Ollama 辅助生成前端模板等 |
| `verdict_from_criterion.py` | 判据/结论类逻辑 |

**原则**：Web/API 应尽量少直接依赖 `utils` 细节；优先通过 `pipeline_service`、`htmlpdf_service` 等聚合入口调用，便于替换实现。

## 2. 管线（Pipeline）

- **入口**：Web `process_pipeline`（界面名「OCR处理」）与 `pipeline_service`；与 `settings` 中 `PIPELINE_*`、`FILE_LIBRARY_TEMP_*` 目录一致。  
- **外部引擎**：MinerU、Ollama 等由环境变量与 `settings` 注释说明；未安装时部分功能降级或不可用。

## 3. HTMLPDF 与 PDF 工具链

- 规则与字段归一化：`frontend_schema_rule_engine`、`unified_template_fields` 等与 `htmlpdf_service`、`views` 中 export/import API 联动。  
- 修改导出 JSON 格式时，需同时考虑：**模板编辑器前端**、**文件库模板分类**、**检测填报** 是否消费同一 schema。

## 4. 调试建议

- 管线失败：先查 `media/file_library/temp` 下对应批次与日志；再查 `MINERU_BACKEND` / `OLLAMA_HOST`。  
- 模板保存失败：查 `library_file_service.save_library_binary_uploads` 与分类枚举是否一致。
