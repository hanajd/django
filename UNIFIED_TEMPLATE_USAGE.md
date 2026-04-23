# 统一模板使用文档（新版本 JSON：前后端一体）

本文档定义新版本统一模板 JSON，目标是：

- 保持 HTMLPDF 制作模板能力不变（坐标编辑、PDF 回填、导出）
- 同时生成前端动态输入界面 JSON（`steps/sections/fields`）
- 用一份模板统一管理前端 UI、后端回填、业务提交映射

参考数据来源：

- 前端模板结构样例（`templateId/constants/enums/steps` 格式）
- 旧坐标模板 JSON：`JS-001.json`（`fields[]` 含 `x/y/w/h/placeholder/fieldType`）
- 后端提交 JSON：`*_submit_*.json`（`reportInfo/hospitalInfo/equipmentInfo/testResult/signatures/conclusion`）

---

## 1. 统一设计原则

### 1.1 单一模板源

统一模板文件既包含：

- 前端渲染结构（步骤、分组、字段、公式、判定、显示条件）
- PDF 坐标结构（字段位置、字段类型、`id/title`）
- submit 映射关系（业务路径 -> PDF 字段 `id`）

强制约束：

- HTMLPDF 编辑器内只维护这一份完整统一 JSON（唯一数据源）
- `保存模板`必须保存完整统一 JSON，不允许保存“前端裁剪版”作为模板
- `导出前端模板`仅用于生成前端渲染所需子集 JSON，不回写模板主文件

### 1.2 兼容现有 HTMLPDF

- HTMLPDF 仍编辑 PDF 坐标字段（不改操作习惯）
- 前端 JSON 从统一模板导出（不再单独维护第二套业务字段定义）
- 后端 PDF 回填优先读取模板内映射规则

---

## 2. 新版本统一模板 JSON 结构

主原则：**JSON 信息格式以前端格式为准**（`templateId/constants/enums/steps` 这套结构不改），后端仅追加 HTMLPDF 所需坐标与映射信息。

建议 schema：`unified_form_template/v2`

```json
{
  "schema": "unified_form_template/v2",
  "templateId": "xray_fluoroscopy_ws76",
  "templateName": "X射线透视设备质量控制检测原始记录",
  "version": "1.0.0",
  "reportType": "xray_fluoroscopy",
  "standard": "WS76-2020",
  "locale": "zh-CN",
  "pdfUrl": "templates/xray_fluoroscopy_template.pdf",
  "constants": {},
  "enums": {},
  "steps": [],
  "pdf": {
    "fields": []
  },
  "bindings": {
    "value_rules": [],
    "signature_map": {},
    "field_to_pdf": []
  }
}
```

字段说明：

- `templateId/templateName/version/reportType/standard/locale/pdfUrl`：前端主模板元信息（作为基准）
- `constants/enums/steps`：前端动态表单定义（主干结构，不做后端改形）
- `pdf.fields`：后端新增的 HTMLPDF 坐标字段（从旧 `JS-001.json` 延续）
- `bindings.value_rules`：submit JSON 到 PDF 字段 `id` 的映射
- `bindings.signature_map`：签名字段映射
- `bindings.field_to_pdf`：前端字段 id 与 PDF 锚点（`pdfFieldId`）的绑定（用于自动回填）

---

## 3. 前端动态表单规范（主规范）

以你提供的结构作为标准：

- 顶层：`constants`、`enums`、`steps`
- 层级：`steps[] -> sections[] -> fields[]`
- 字段类型支持：
  - 基础：`text/number/date/textarea/select/radio/boolean`
  - 复杂：`table/group/signature`
  - 计算：`computed/verdict`
- 联动表达式：
  - `visibleWhen`
  - `dependsOn`
  - `formula`
  - `rule`

说明：前端输入界面 JSON 可直接由统一模板的这些节点输出，不再依赖坐标字段推断。

---

## 4. PDF 坐标模板规范（HTMLPDF 不变）

沿用旧前端坐标 JSON（`JS-001.json`）字段模型：

```json
{
  "id": "reportNo",
  "pdfFieldId": "f1",
  "title": "报告编号",
  "page": 1,
  "x": 130.72,
  "y": 91.59,
  "w": 48.97,
  "h": 19.81,
  "fieldType": "text",
  "content": "",
  "checked": false,
  "imageData": ""
}
```

约定：

- `pdf.fields` 是在前端主格式上追加的后端增强区，用于 HTMLPDF 编辑与回填
- `pdf.fields` 是后端回填与 PDF 生成的唯一坐标来源
- 前端界面 JSON 默认不含坐标，但可通过 `bindings.field_to_pdf` 回查 `pdfFieldId`

---

## 5. 后端 submit 映射规范（核心统一）

后端业务 JSON 结构来源于当前 submit 数据：

- `reportInfo`
- `hospitalInfo`
- `equipmentInfo`
- `instruments`
- `testResult`
- `signatures`
- `conclusion`

### 5.1 value_rules（submit path -> PDF 字段 id）

示例：

```json
{
  "from": "reportInfo.reportNo",
  "key": "reportNo",
  "type": "text"
}
```

### 5.2 signature_map（签名专用）

示例：

```json
{
  "author": { "name": "signatures.preparedBy", "image": "signatures.author" },
  "reviewer": { "name": "signatures.reviewedBy", "image": "signatures.reviewer" },
  "approver": { "name": "signatures.approvedBy", "image": "signatures.approver" }
}
```

### 5.3 field_to_pdf（前端字段 id -> PDF 锚点）

示例：

```json
{
  "fieldId": "reportNo",
  "pdfFieldId": "f1",
  "fieldType": "text"
}
```

用途：将前端 `steps.fields.id` 与 PDF 具体落点解耦，确保 UI 变更不影响坐标层。

---

## 6. HTMLPDF 功能重整（流程不变、产物升级）

### 6.1 继续保留的能力

- 拖拽/编辑 PDF 字段坐标
- 字段类型编辑（text/check/image）
- 保存模板、导出模板
- 按 task submit 数据生成 PDF

### 6.2 升级后的导出能力

- `保存模板`：保存完整 `unified_form_template/v2`（推荐）
- `保存前端JSON`：从统一模板提取前端可直接渲染的 JSON（`constants/enums/steps`）
- `保存前端JSON` 导出的是裁剪产物，不包含 `pdf.fields` 与完整 `bindings`（可保留最小 `field_to_pdf` 引用）

### 6.3 保存前端JSON的LLM标准化约束（导出接口）

导出接口在 `steps` 为空（或强制开启）时，会触发本地大模型进行前端模板标准化。输出必须满足：

- 顶层字段固定且顺序固定：`templateId/templateName/version/reportType/standard/pdfUrl/locale/constants/enums/steps`
- 严禁输出冗余顶层：`schema/meta/pdf/formSchema/bindings/fields/source_pdf`
- 层级固定：`steps -> sections -> fields`
- `layout` 仅允许：`form/table/grid`
- ID规范：
  - `steps[].id` 必须为 `step_英文语义`
  - `sections[].id` 必须为 `sec_英文语义`
  - `fields[].id` 必须为英文小驼峰（禁止中文ID）
- `number` 类型必须包含 `precision`
- `radio/select` 必须使用 `enumRef` 引用顶层 `enums`
- 每个字段嵌入 PDF 联动数据：`pdfFieldId/page/x/y/w/h/pdfAnchor`
  - `pdfAnchor` 结构固定：`{"page": n, "rect": [x, y, w, h]}`
- 条件显隐统一使用 `visibleWhen`
- `computed` 必须包含 `formula` + `dependsOn`
- `verdict` 必须包含 `rule`
- `constants/enums` 空值时返回空对象，不得省略

### 6.4 导出模式（规则引擎 / 大模型）

`/files/htmlpdf/api/export-frontend-json/` 新增 `export_mode`：

- `rule`：仅规则引擎转换（不经过大模型，稳定、可重复）
- `llm`：仅大模型转换（失败自动回退规则引擎）
- `auto`：自动模式（默认；`steps` 为空时优先尝试大模型）

HTMLPDF 工具栏对应两个按钮：

- `保存前端JSON(规则)`：`export_mode=rule`
- `保存前端JSON(AI)`：`export_mode=llm`

这样可以做到：

- htmlpdf 制作模板能力不变
- 自动生成前端输入界面 JSON
- 前后端用同一个版本号和模板 ID 对齐

---

## 7. 数据流（统一后）

1. 在 HTMLPDF 编辑器维护 `pdf.fields`
2. 在模板配置区维护 `constants/enums/steps` 与 `bindings`
3. 保存为统一模板 `unified_form_template/v2`
4. 前端加载统一模板中的 `steps` 渲染输入界面
5. 前端提交业务数据（与当前 submit 结构兼容）
6. 后端按 `bindings` 回填到 `pdf.fields` 并生成 PDF

---

## 8. 迁移策略（从旧模板到新模板）

### 8.1 从旧 `JS-001.json` 迁移

- 将旧 `fields[]` 迁移到新模板的 `pdf.fields`
- 迁移规则：`id <- 业务字段键`，`title <- 自然语言字段名`，`pdfFieldId <- 旧 id`

### 8.2 从当前 submit 迁移

- 不改 submit 主结构
- 把硬编码 placeholder 映射迁入 `bindings.value_rules`（统一改为按 `key=id` 映射）

### 8.3 从旧前端动态模板迁移

- 将你提供的新格式作为 `steps` 主体直接落入统一模板
- 前端界面 JSON由统一模板导出，不再手工维护多份

---

## 9. 最小可落地清单

- 必填：
  - `templateId`
  - `version`
  - `reportType`
  - `steps`
  - `pdf.fields`
  - `bindings.value_rules`
- 推荐：
  - `constants`
  - `enums`
  - `bindings.signature_map`
  - `bindings.field_to_pdf`

---

## 10. 版本建议

- 新模板统一使用：`unified_form_template/v2`
- 每次变更 `steps` 或 `bindings` 必须升级 `version`
- 运行时按 `templateId + version` 强绑定，避免前后端模板漂移

---

文档版本：`2.0`（对应统一模板 `unified_form_template/v2`）
