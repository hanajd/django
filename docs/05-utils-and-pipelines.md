# 05 utils 与文档管线

> **安装 Ollama / MinerU / TeX**：见 [外部依赖安装-Ollama-MinerU-LaTeX.md](外部依赖安装-Ollama-MinerU-LaTeX.md)。  
> **环境变量默认值**：见 [01-configuration.md](01-configuration.md) §6。

---

## 1. `utils/` 模块地图（接手索引）

| 文件 / 区域 | 作用 |
|-------------|------|
| `document_pipeline.py` | 文档管线主逻辑（MinerU → 清洗 → Ollama 抽取） |
| `pipeline_config.py` | 模型名、目录常量、`MAX_RETRIES` 等 |
| `mineru_ops.py` | MinerU CLI 封装、超时、输出 MD 查找 |
| `gpu_scheduler.py` | 多 GPU / 显存与 MinerU、Ollama 调度 |
| `ollama_extract.py` | Ollama 辅助结构化抽取（设备 JSON） |
| `extract_frontend_template.py` | 可选 LLM 前端模板草稿 |
| `dynamic_form_expression.py` | 现场公式安全求值（含 `a~b` / `a;b` 连接） |
| `verdict_from_criterion.py` | 判定标准文本 → 合格/不合格 |
| `pdf_merge.py` / 填报相关 | 检测报告 PDF 合并与叠印 |
| `mathjax_pdf_render.py` | `$$…$$` 公式图（需 Node MathJax） |
| `pdf_field_formulas.py` | 模板字段公式 / 拟合 latex 判定 |
| `conditional_field_rules.py` | 条件公式规则 |
| `frontend_schema_rule_engine.py` | 导出 schema 与规则 |

Django 桥接：`apps/core/pipeline_service.py`（注入 `OLLAMA_HOST`、`MINERU_BACKEND` 等）。

---

## 2. 管线（Pipeline）端到端

```text
上传 PDF
   → media/file_library/temp/...
   → mineru -p … -o …/batches/<batch>/mineru_output/
   → 选取最大有效 .md
   → 清洗
   → Ollama 抽取设备 JSON（失败则空骨架）
   → 写回业务 / 预览
```

| 项 | 说明 |
|----|------|
| Web 入口 | `/files/process/`（`perm_process_pipeline`） |
| API | `POST .../library/files/ocr/` 等（见 [03-rest-api.md](03-rest-api.md)、[07-features-catalog.md](07-features-catalog.md)） |
| 目录 | `settings` 中 `PIPELINE_*`、`FILE_LIBRARY_TEMP_*`；批次在 `media/file_library/temp` |
| 外部引擎 | MinerU（`MINERU_BACKEND`）、Ollama（`OLLAMA_HOST`） |
| 演示账号 | `PARTY_A_DEMO_DISABLE_PIPELINE` 可禁独立管线页 |

### 2.1 失败语义（勿误解为「管线成功」）

| 失败点 | 典型表现 |
|--------|----------|
| 无 `mineru` CLI | 日志未安装；错误常含 `MinerU未生成MD` |
| MinerU 超时/崩 | 批次失败；可 `MINERU_KEEP_OUTPUT=1` 留目录 |
| Ollama 不通 | 重试后仍返回**空字段设备**；主站其它功能正常 |
| 模型名错误 | 抽取失败 / 空结果 |

验收要看**结构化字段是否有内容**，不能只看 HTTP 200。

### 2.2 关键目录（排障时打开）

```text
media/file_library/temp/
  batches/<batch_id>/mineru_output/   # MinerU 原始输出
  mineru_md/                          # 聚合 MD（配置名 PIPELINE_MINERU_MD）
  preview_images/                     # 预览图
```

---

## 3. HTMLPDF 与 PDF 工具链

- 规则引擎 + 统一字段模块与 `htmlpdf_service`、编辑器 `api/*` 联动。  
- 改导出 JSON 时同步考虑：**编辑器前端**、**文件库 template**、**App 填报** 是否同一 schema。  
- 检测回填主路径在 `apps/api/inspection_report_make.py`，内部会用到本目录公式/合并工具。  
- 公式连接符：`~`（范围）、`;`（分号连接）——见 [json公式说明.md](json公式说明.md) §2.2。

---

## 4. 其它与 PDF / 报告相关的仓库包

| 目录 | 说明 |
|------|------|
| `converter/` | 评价报告书 MD↔LaTeX、关键词、**`latex_compiler.py`** |
| `latex/` | 评价报告书 TeX 工程（`yp250420` 等） |
| `fonts/` | 评价报告书字体（工作区链接） |
| `htmlpdf/fonts/` | 检测报告叠印字体 |
| `f1_eval_report/` | F.1 表单 PDF 生成库 |
| `radiation_detection_report/` | 可移植检测结果表包 |

评价报告书业务步骤：[评价报告书-LaTeX功能说明.md](评价报告书-LaTeX功能说明.md)。

---

## 5. 调试建议

| 现象 | 排查 |
|------|------|
| 管线失败 | `temp/batches`、`MINERU_BACKEND` / `OLLAMA_HOST`、`which mineru`、GPU 日志；对照外部依赖手册 |
| 模板保存失败 | `library_file_service` 分类枚举与磁盘路径 |
| 公式/判定不符 | `dynamic_form_expression`、`verdict_from_criterion`、现场同行「不合格」强制逻辑 |
| 合并报告异常 | `pdf_merge` + [合并报告规则说明.md](合并报告规则说明.md) |
| 评价书编译失败 | `LATEX_ENGINE`、TeX 宏包、工作区 `fonts/`、`.log` |
