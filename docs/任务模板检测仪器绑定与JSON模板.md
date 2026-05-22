# 任务模板检测仪器绑定与 JSON 模板说明

本文说明：**任务模板（LibraryTask）**在后台绑定**仪器主数据（InstrumentCatalog）**后，如何进入检测提交的 `instruments`、如何参与 **PDF 占位符回填**与**前端导出 JSON 根级初值**，以及 App / 前端在「检测仪器」模块的推荐实现方式。

---

## 1. 数据模型

| 概念 | 说明 |
|------|------|
| 仪器主数据 | `InstrumentCatalog`：仪器编号 `code`（**唯一**，一台物理仪器一条）、名称 `name`、型号 `model` 等；**出库/入库**见 `docs/仪器台账出库入库.md`。 |
| 任务模板绑定 | `bindingMode=kinds`：仅存种类 `{"qualityControl":[{"name","model"},…], …}`。`bindingMode=ids` 或旧列表 `[qc_id, rp_id]`：仍兼容。具体编号统一在 **`LibraryProject.assigned_instrument_ids`**（人员派工 / 同步 App 任务时写入）。 |
| 出库顺序 | 派工分配时默认同种在库按台账 **`code` 升序** 依次占用；旧版模板若指定编号已被占用，自动选用同种下一编号。详见 `docs/仪器台账出库入库.md`。 |
| 检测提交 | 提交体 `raw_payload.instruments`：数组，元素为字典，与前端模板根级 **`instruments`** 同形；分 scope 提交见 `instruments.qualityControl` / `instruments.radiationProtection`。 |

后台入口：**任务模板库** → **模板默认检测仪器（种类）**；**项目工作台 → 人员派工** → 同步任务时按顺序分配具体编号并出库。

---

## 2. 合并规则（与提交 JSON）

在以下流程中，会在内存里对 `payload` 做浅拷贝并合并仪器（**不修改数据库中的历史 submit**）：

- 按任务 **HTMLPDF 模板回填**（`_build_filled_template_fields_for_task`）；
- **按 taskNo 导出前端 JSON**（`InspectionTaskFrontendJsonExportAPIView` 等）：根级 **`instruments`** 按上节规则（无提交用模板绑定，有提交以前端为准）；
- **HTMLPDF 保存前端模板**（`POST /files/htmlpdf/api/export-frontend-json/`）：保存前写入根级 **`instruments`**；通过 **`libraryTaskId`** 或 **`libraryTemplateFileId`**（及 `meta` 中同名键）解析到 `LibraryTask` 后，按 `bound_instrument_ids` **预填**数组（见 §7）。

规则要点：

1. **优先任务模板**：`payload.instruments` 缺失、非列表或**空数组**时，按绑定对象中质控一套、防护一套的 id 列表从主数据生成根级 `instruments`（质控全套在前、防护全套在后、去重；`is_active=False` 仍会生成，避免历史绑定因停用而整行消失）。
2. **以前端提交为准**：若 `payload.instruments` 为**非空列表**，则**不再**从模板追加或删减条目，完全保留前端已保存的顺序与条目（具体项目上手改仪器即以此为准）。
3. 合并仅在内存中进行，**不改写**数据库里历史 submit 的 `raw_payload`。

**追加 / 导出条目的字段形态**（与当前 `*_template_frontend.json` 根级及 submit 对齐，示例见仓库内 `media/file_library/templates/eb8a67e344b8479c846865f27cc3feaf_template_frontend.json` 根对象 `instruments`）：

| 字段 | 说明 |
|------|------|
| `id`、`instrumentId` | 字符串形式的主键（与 `InstrumentCatalog.id` 一致）。 |
| `identifier` | 仪器编号（对应主数据 `code`）。 |
| `name` | 仪器设备名称。 |
| `model` | 型号，可空字符串。 |
| `certificateNo` | 证书编号，可空字符串。 |
| `validUntil` | 证书有效期日的 **ISO8601**（带时区，如 `2026-10-30T00:00:00+08:00`）；无日期时为 `""`。 |
| `enabled` | 布尔，一般为 `true`。 |

说明：根级 **`instruments`** 不再附带全库 **`instrumentCatalogOptions`**；下拉/全量列表请前端调用登记接口 **`GET …/registry/instruments/`**（或你们环境中等价路径）自行拉取。

---

## 3. 单行展示格式（输入框 / 只读文案）

在**检测仪器**相关 UI 中，每一台仪器对应**一行**展示（或单行只读输入框），**不跨行**，推荐文案格式为：

```text
{model} {name} {identifier}（有效期至yyyy年mm月dd日）
```

规则：

- **`model`、`name`、`identifier`** 之间用**单个半角空格**分隔；若 `model` 为空，连续空格可压成一段空格，或省略型号段（产品可二选一，与 PDF 占位符习惯保持一致即可）。
- 若**没有**有效证书日期（主数据无 `certificate_valid_until` 或条目 `validUntil` 为空），则**不写**「（有效期至…）」整段括号。
- 若有日期：使用 **4 位年 + 两位月 + 两位日** 的中文「有效期至yyyy年mm月dd日」（与后台 PDF 占位符补缺习惯一致）。
- **单行不换行**：容器或输入框使用 `white-space: nowrap`，过长可用 `overflow: hidden; text-overflow: ellipsis` 省略，避免换行破坏版式。

实现参考（逻辑，非唯一实现）：`apps.api.inspection_report_make` 中的 `format_instrument_display_line`、`format_instrument_display_from_submit_item`、`_resolve_instrument_id_display`。

### 3.1 占位符补缺键（仅填空，不覆盖已有映射）

在增强/旧版回填的 `value_mapping` 准备阶段，会根据合并后的 `instruments` **仅对仍为空的键**写入：

| 键 | 含义 |
|----|------|
| `检测仪器1` … `检测仪器N` | 第 N 条仪器对应整行展示串 |
| `仪器1` … `仪器N` | 同上别名 |
| `检测仪器列表` | 所有非空行以中文分号 `；` 拼接 |
| `检测仪器汇总` | 与 `检测仪器列表` **同文**，便于单输入框 / 单 PDF 文本域绑定 |

**单输入框（记录本表用到的全部仪器）**：数据源仍为根级 **`instruments[]`**（可多元素）；界面上只放一个框时，该框展示或提交与上表 **`检测仪器列表` / `检测仪器汇总`** 一致的合并串即可；PDF/HTMLPDF 报告里若也只有一个大文本格，请将域的 `id` / `placeholder` / `title` 设为 **`检测仪器列表`** 或 **`检测仪器汇总`** 之一即可命中回填。勿把多台仪器仅分散写在互不关联的 `dynamicData.f*` 而根级 `instruments` 为空，否则报告侧难以可靠还原多机列表。

HTMLPDF 字段的 `id` / `placeholder` / `title` 与上述键一致时即可命中。

---

## 4. 两条导出链路（根级 `instruments`）

| 链路 | 根级 `instruments` | 说明 |
|------|---------------------|------|
| **A. 检测任务导出前端 JSON** | **有** | `GET …/inspections/…/export-frontend-json`（或带 `project_id` 的 v2 路径）。已知 `taskNo` 与 `LibraryTask`，合并**当前 submit + 任务模板绑定**后写入根级。 |
| **B. HTMLPDF 导出前端 JSON** | **有（可为 `[]`）** | `POST /files/htmlpdf/api/export-frontend-json/` 保存前合并写入根级 **`instruments`**。须在请求体中提供 **`libraryTaskId`** 和/或 **`libraryTemplateFileId`**（或 `meta` 内同名键），后端据此解析 `LibraryTask` 并预填绑定仪器；否则根级可能为 `[]`。不再写入全库 `instrumentCatalogOptions`。 |

**已有旧 JSON 文件的处理建议：**

1. 在 HTMLPDF 再次「保存前端模板」时带上 **`libraryTaskId`** 或 **`libraryTemplateFileId`**（见 §7），或从文件库用模板 PDF 打开编辑器（`use-template-pdf` 会返回 **`linked_library_task_ids`** 供前端写入上下文）；  
2. App 侧可走 **链路 A** 按 `taskNo` 拉取最新前端 JSON；  
3. 全量仪器列表始终通过 **`GET …/registry/instruments/`** 获取，勿依赖导出 JSON 内嵌全表。

---

## 5. 根级字段（与 `templateId`、`steps` 并列）

| 根字段 | 说明 |
|--------|------|
| **`instruments`** | 与 submit body 中 **`instruments`** 数组**同形**（见 §2 字段表）。作为表单**初值**；用户在前端增删行只改内存与提交 payload，**不修改** `InstrumentCatalog` 数据库。 |

**联调示例：** `docs/instruments_frontend_example.json`（精简虚构数据）。

---

## 6. 前端可实现操作（检测仪器模块，建议直接转发本节）

以下描述的是 **App / Web 表单层** 行为：所有「添加 / 删除」指在**当前检测单的 `instruments` 数组与 UI 状态**中增删，**不是**在后台仪器台账里增删记录。

### 6.1 读取仪器主数据（数据库接口）

- 调用 **`GET …/registry/instruments/`**（需登录，与现有登记接口一致）获取启用仪器列表。  
- 用于：搜索、下拉选择「从台账点选一台仪器加入当前检测单」等。  
- 接口返回字段与后端主数据一致；选中某条后，将对应字段映射到 §2 的 `instruments[i]` 结构（`id` / `instrumentId` 用字符串等）。

### 6.2 与导出模板根级 `instruments` 对齐

1. 打开模板 JSON 后，若根上存在 **`instruments`**，作为当前表单的**初值**（草稿可覆盖）。  
2. 若无该字段或为空数组，可视为「本单尚未添加仪器」，用户通过 §6.1 从台账挑选添加，或手填结构化字段（仍建议与 §2 同形，便于提交与 PDF）。

### 6.3 在当前项目中「添加 / 删除」仪器行（仅界面与 payload）

- **添加一行**：在内存中向 `instruments` 数组 `push` 一个新对象（可从台账选中预填 §2 字段，或复制上一行再改）。**不调用** POST/DELETE 去改 `InstrumentCatalog`。  
- **删除一行**：从 `instruments` 数组 `splice` 掉对应下标。**不调用**删除台账仪器的接口。  
- **编辑一行**：直接修改该元素各字段（仍保持 §2 形态）。  
- 若步骤里存在固定 `submitPath`（如 `instruments.0.id`），动态增删行后需与列表渲染索引一致，避免与 PDF 映射错位。

### 6.4 单行展示（与 §3 一致）

- 每一行用 §3 的格式展示：`{model} {name} {identifier}（有效期至yyyy年mm月dd日）`；无日期则省略括号段。  
- **单行不折行**：对展示节点设置 `white-space: nowrap`，必要时 `text-overflow: ellipsis`。  
- 若使用 `<input readonly>` 展示整行文案，同样加上述样式，避免多行换行。

### 6.5 提交与草稿

- Draft / Submit 请求体中保留与其它块同级的 **`instruments`** 数组，结构与根级一致。  
- 后台 PDF 展示格式仍按 §3 从 `instruments` 解析；若前端另有纯展示 Text 字段，可自行拼接与 §3 一致的字符串。

---

## 7. HTMLPDF 保存前端 JSON 时的请求参数

在调用 `export-frontend-json` 的 POST body 中建议包含（根级或 `meta` 内均可，后端会合并读取）：

- **`libraryTaskId`**（或 `library_task_id`）：`LibraryTask` 主键，与任务模板库中该模板一致。  
- **`libraryTemplateFileId`**（或 `library_template_file_id` / `template_file_id`）：文件库「模板」分类中 **JSON 模板文件**的主键；当未传 `libraryTaskId` 时，后端会据此反查关联的 `LibraryTask` 并预填绑定仪器。

从文件库打开某模板 **PDF** 进入编辑器时，`POST /files/htmlpdf/api/use-template-pdf/` 的响应中会包含 **`linked_library_task_ids`**，前端可将第一个 id 写入 **`libraryTaskId`** 上下文，保存前端 JSON 时一并提交。

---

## 8. 统一 JSON 模板建议（v2 / HTMLPDF）

1. **行内仪器选择**  
   - 控件类型：`instrument_select`（与规则引擎、回填一致）。  
   - `source.submitPath`：指向 `instruments.0.id`、`instruments.1.id` 等与行号对应的路径。

2. **纯文本「检测仪器」格**  
   - 占位符键名：`检测仪器1`、`检测仪器2`，或 **`检测仪器列表` / `检测仪器汇总`**（合并全文，二者同文，见 §3.1）。

3. **证书日期**  
   - 条目中用 `validUntil`（ISO）与主数据对齐；展示层按 §3 转中文日期括号段。

---

## 9. 报告回填与主数据对齐（仅主键 / 缺字段）

前端从 **`GET …/registry/instruments/`** 点选仪器后，提交体里常见两种形态：

1. **完整行**：`instruments[i]` 已带齐 §2 各字段（与登记接口返回一致），报告/PDF 直接按 §3 拼接即可。  
2. **仅主键或缺省**：行内只有 `id` / `instrumentId`（或部分字段省略），为减轻 payload 体积。

后端在 **`inspection_report_make`** 中生成 `检测仪器1…` / `检测仪器列表` 占位符时，会：

- 先用提交行自身字段拼 §3 文案；  
- 若仍为空或不完整，且主键为数字型 **`InstrumentCatalog.id`**，则 **查询主数据表** `InstrumentCatalog` 补全展示（与台账同源）；  
- `instrument_select` 控件仅提交 id 字符串时，同样走主键 → 主数据解析。  
- 合并占位符 **`检测仪器列表`**、**`检测仪器汇总`** 由上述槽位行串以 `；` 拼接生成（单框展示与报告单域回填用）。

因此：**前端用登记接口拉清单、提交里只带回主键** 与 §2 并不矛盾；全量检索仍只用登记接口，报告出字在服务端再次对齐主数据。

---

## 10. 迁移与运维

- 迁移文件：`apps/core/migrations/0028_librarytask_bound_instrument_ids.py`（为 `LibraryTask` 增加 `bound_instrument_ids`）。  
- 部署后执行：`python manage.py migrate`。

---

## 11. 小结

| 能力 | 说明 |
|------|------|
| 后台绑定 | 任务模板库 → 模板与仪器 → 质控/防护各选一套（可多选）保存 → `bound_instrument_ids`（对象，值为 id 数组）。 |
| 任务导出 JSON | 根级 **`instruments`**（合并 submit + 绑定）；无全库 options。 |
| HTMLPDF 导出 JSON | 根级 **`instruments`**；需 **`libraryTaskId` / `libraryTemplateFileId`** 或 PDF 打开返回的 **`linked_library_task_ids`** 以预填绑定。 |
| 全量下拉 | **`GET …/registry/instruments/`**，不在导出 JSON 内嵌全表。 |
| 单行展示 | `{model} {name} {identifier}（有效期至yyyy年mm月dd日）`；无日期省略括号；`nowrap` 单行。 |
| 前端增删 | 只改 **`instruments[]`** 与 UI，**不**增删数据库仪器台账；提交/草稿上传整包 `instruments`。 |
| 报告回填 | 见 **§9**：提交行缺字段时按主键回读 **`InstrumentCatalog`**，与登记接口数据一致。 |
