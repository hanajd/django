# 前端表单 JSON 架构说明（`frontend_form_schema/v1`）

本文说明检测任务导出的 **前端 JSON**（`*_frontend.json` 或 `GET …/export-frontend-json`）的结构与约定，供 Flutter / Web 表单渲染与提交使用。

> **与坐标模板分离**：PDF 回填坐标在另一份 **坐标模板 JSON**（`unified_form_template/v2`，仅含 `pdf.fields[].rect` + `pdfFieldId`）。  
> **前端 JSON 不含 `rect` / `pdfBindings`**，回填时按栏位的 `pdfFieldId` 关联即可。

---

## 1. 根级字段

| 字段 | 说明 |
|------|------|
| `schema` | 固定 `"frontend_form_schema/v1"` |
| `templateId` / `templateName` / `version` | 模板标识 |
| `reportType` / `standard` | 报告类型、依据标准 |
| `pdfUrl` | 空白 PDF 预览地址（可选） |
| `locale` | 默认 `zh-CN` |
| `constants` | 公式/判定用常量（如分辨力上限） |
| `enums` | 仅包含本模板实际用到的枚举（`enumRef` 引用） |
| `steps` | **主结构**：表单步骤与章节 |
| `instruments` | 检测仪器预选列表（根级，可选） |

---

## 2. 层级结构

```
steps[]                    # 办理步骤（通常 1 步「检测原始记录」+ 可选「签字」）
  └── sections[]           # PDF 六大章节 + 质控/签字等
        ├── fields[]       # 普通表单栏位 / 整张透视表平铺栏位
        └── matrix         # 固定表头+多行（matrixTable，可选）
```

### 2.1 现场记录六大章节（运行态）

移动端 `GET …/export-frontend-json`（默认 `mode=runtime`）**始终输出六大业务章节**，即使某章暂无栏位也会保留空 `fields: []`。请用稳定 **`sectionType`** 驱动前端逻辑，**不要**靠中文 `title` 判断章节。

| `sectionType` | `sectionKey` | 典型 `id` | 标题 |
|---------------|--------------|-----------|------|
| `clientBasicInfo` | `client_basic_info` | `sec_client_basic` | 受检单位基本信息 |
| `equipmentBasicInfo` | `equipment_basic_info` | `sec_equipment_basic` | 受检设备基本信息 |
| `instrumentPersonnel` | `instrument_personnel` | `sec_instrument_personnel` | 受检设备主要检测仪器及检测人员（含质控/防护两个 `instrument_select`） |
| `qualityControl` | `quality_control` | `sec_qc` | 质量控制（性能）检测项目及结果 |
| `radiationProtection` | `radiation_protection` | `sec_radiation_protection` | 工作场所放射防护检测结果 |
| `floorPlan` | `floor_plan` | `sec_floor_plan` | 平面布局示意图 |

`section` 运行态保留：

- `id` / `sectionKey` / `sectionType` / `title`
- `layout`：仅非 `form` 时写出（如 `matrixTable`）
- `fields` / `matrix`
- 可选 `capabilities` / `behavior`（轻量行为配置，见 2.2）

示例（空章占位）：

```json
{
  "id": "sec_qc",
  "sectionKey": "quality_control",
  "sectionType": "qualityControl",
  "title": "质量控制（性能）检测项目及结果",
  "fields": []
}
```

### 2.2 质控 / 防护检测仪器（同属第三章）

两个仪器选择格**都在** `sectionType: "instrumentPersonnel"`（「受检设备主要检测仪器及检测人员」）的 `fields[]` 中，**不会**放到质控或防护检测结果章。标签示例：

- `主要检测仪器_质量控制（性能）检测`
- `主要检测仪器_工作场所放射防护检测`

导出为 `type: "instrument_select"`，并带：

| 字段 | 说明 |
|------|------|
| `instrumentScope` | `qualityControl` 或 `radiationProtection`（提交语义，非章节类型） |
| `registrySlot` | `1`（质控）或 `2`（防护） |
| `submitPath` | `instruments.qualityControl` / `instruments.radiationProtection` |
| `submitBucket` | `instruments` |

该章 `capabilities` 含 `instrumentSelect`。根级 `instrumentBindings` 与任务模板独立绑定对齐（后台分别选质控仪器、防护仪器，非按勾选顺序）：

```json
"instrumentBindings": [
  {
    "scope": "qualityControl",
    "sectionType": "instrumentPersonnel",
    "label": "主要检测仪器_质量控制（性能）检测",
    "submitPath": "instruments.qualityControl",
    "registrySlot": 1,
    "defaultInstrumentId": "12"
  },
  {
    "scope": "radiationProtection",
    "sectionType": "instrumentPersonnel",
    "label": "主要检测仪器_工作场所放射防护检测",
    "submitPath": "instruments.radiationProtection",
    "registrySlot": 2,
    "defaultInstrumentId": "15"
  }
]
```

前端：在 **instrumentPersonnel** 章节渲染两个 `instrument_select`；提交写入 `instruments.qualityControl` / `instruments.radiationProtection`；下拉走台账 API。

### 2.3 `capabilities` / `behavior`（可选）

| `sectionType` | `capabilities` | `behavior` |
|---------------|----------------|------------|
| `equipmentBasicInfo` | `["deviceOcr"]` | — |
| `qualityControl` | `["retainPhotos"]` | — |
| `radiationProtection` | `["editableMatrixRows"]` | `{ "maxCustomRows": 22 }` |

---

## 3. 栏位（`fields[]`）通用结构

每个可填写项为**扁平对象**（无嵌套 `source`）：

```json
{
  "id": "doseValue",
  "type": "number",
  "label": "剂量值",
  "pdfFieldId": "f56",
  "submitPath": "testResult.doseValue",
  "submitBucket": "testResult"
}
```

运行态栏位为**扁平对象**（无嵌套 `source`），至少保留 `pdfFieldId`、`submitPath`；有分桶时写 `submitBucket`。`schemaKey` / `hierarchyKey` 等语义键仍在顶层。

### 3.1 必填/常用属性

| 属性 | 说明 |
|------|------|
| `id` | 与 `pdfFieldId` 一致（`f1`、`f2`…）；**允许 `label` 重名**，勿用展示名当唯一键 |
| `type` | 控件类型（见下表） |
| `label` | 展示标签（可与其他栏位相同） |
| `pdfFieldId` | **PDF 回填与 dynamicData 键**（与坐标模板一致；移动端/服务端回填均以此为准） |
| `pdfFieldIds` | 多 PDF 框合并为一个输入时（如多处签名） |
| `submitPath` | **提交/读写**用的点路径（常含 `testResult.auto…`），不是界面标题 |
| `schemaKey` | 稳定逻辑键（原 `source.key`），如 `step_qc_items.sec_xxx.t0_r8_c5.measuredValue` |
| `hierarchyKey` | 下划线分层键，如 `高对比度分辨力_检测结果`，用于分组/排序 |
| `judgmentCriterionText` | 判定标准说明（展示在输入框旁或帮助区） |
| `visibleWhen` | 显隐表达式（如 `commissionOrgMode == 'customCommission'`） |
| `table` | 表格单元语义（行列、长命名、防护点位等，见第 4 节） |
| `enumRef` | 引用根级 `enums` 的键 |
| `defaultValue` | 默认值；`radio`/`select` 为字符串 |
| `required` | 仅 `true` 时输出 |
| `width` | `half` / `full` / 数值比例，默认不写 `half` |

### 3.2 `type` 枚举

| type | 用途 |
|------|------|
| `text` | 单行文本 |
| `number` | 数值（见 `precision`、`unit`） |
| `date` | 日期 |
| `textarea` | 多行 |
| `boolean` | 勾选（非互斥组） |
| `radio` | 互斥单选（检测类型、设备类型、控制方式等） |
| `select` | 下拉 |
| `checkboxGroup` | 选项组（少用） |
| `computed` | 只读计算列（含 `formula`、`dependsOn`） |
| `verdict` | 判定列（含 `rule`） |
| `signature` | 签字（检测员/校核/陪同人等）；**仅**出现在 `step_signature`，不与「仪器及人员」章重复 |
| `instrument_select` | 检测仪器下拉（对接 `GET …/registry/instruments/` + 根级 `instruments[]`） |
| `floorPlan` | **仅**「平面布局示意图」章节内的平面图 image |
| `table` | 检测仪器清单等可编辑表（含 `columns`、`initialRows`） |

### 3.3 `precision`（数值小数位）

- **仅对 `type: "number"` 或 `computed` 有意义**。
- 表示前端展示与校验时保留的小数位数，例如 `"precision": 1` → 显示/提交一位小数（如 `12.3`）。
- 质控测量、剂量率、防护表读数等一般为 `1`；若未写，后端规则引擎默认按 `1` 处理。

### 3.4 `unit`

数值单位提示，如 `kV`、`mA`、`μSv/h`、`%RH`，用于 UI 展示，不参与 `submitPath`。

### 3.5 长命名与短 `label`（质控/表格栏位）

很多 PDF 格子的 **`label` 只有「检测结果」「报出值」等短名**，无法区分具体检测项。完整说明在 **`table`** 与顶层辅助键中：

| 用途 | 字段 | 示例 |
|------|------|------|
| 界面副标题/完整名称 | `table.fieldName` | `CT值线性（仅验收检测）_120_CT实测(HU)值_检测结果` |
| 按检测大项分组 | `table.itemName` | `CT值线性（仅验收检测）` |
| 列角色 | `table.typeName` | `检测结果`、`报出值` |
| 分组排序 | `hierarchyKey` | `高对比度分辨力_检测结果` |
| 稳定 ID（调试/缓存） | `schemaKey` | `step_qc_items.sec_xxx.t0_r8_c5.measuredValue` |
| 写入后端的数据路径 | `submitPath` | `testResult.auto.uca4f9aeade4f.t0_r8_c5.measuredValue92` |
| 判定标准文案 | `judgmentCriterionText` | `≤2.0mm` |

**前端渲染建议**（`label` 为通用短名时）：

1. 主标题：`table.itemName` 或从 `table.fieldName` 解析的第一段  
2. 输入框标签：`table.typeName` 或 `label`  
3. 副标题/帮助：`table.fieldName` 或 `judgmentCriterionText`  
4. 组表：按 `table.itemName` + `table.row`/`table.col` 或 `table.cellId` 聚合成表格/卡片

压平导出时**去掉的是嵌套 `source` 容器**，不是上述语义；请**重新导出**前端 JSON 后核对。

### 3.6 公式与判定

```json
{
  "type": "computed",
  "formula": "avg(f259,f260,f261)",
  "dependsOn": ["f259", "f260", "f261"],
  "precision": 2,
  "unit": "μSv/h"
}
```

```json
{
  "type": "verdict",
  "rule": "<=10"
}
```

---

## 4. 工作场所放射防护：`matrixTable` + 22 可编辑点位

`radiationProtection` 章节在运行态导出为 **`layout: "matrixTable"`**（不再用大量平铺 `fields`）。

- 预留 **22 行**可新增点位：`testResult.protectionPoints[0].point` … `[21].point`
- 行头列 `point`（检测点位）标记 `editable: true`，前端可编辑并随草稿/提交保存
- PDF 模板已有数据的行按 `table.row` 映射到矩阵单元；其余行为空行占位

`rowHeaderColumns` 示例：

```json
{
  "id": "point",
  "title": "检测点位",
  "editable": true,
  "submitPath": "testResult.protectionPoints[0].point",
  "submitBucket": "testResult"
}
```

质控等其它表格仍可使用平铺 `fields` + 顶层 `table` 语义（见 4.1）。

### 4.1 表格语义：`table`（质控等平铺栏位）

带表格语义的栏位，导出为顶层 **`table`** 对象（不再使用嵌套 `source.autoSemantic`）。  
质控表与防护表均保留 **`itemName` / `typeName` / `fieldName` / `row` / `col`**；防护表额外有点位列语义：

**质控表示例**（`site_qc_performance` 等）：

```json
{
  "id": "measuredValue92",
  "type": "number",
  "label": "检测结果",
  "pdfFieldId": "f191",
  "submitPath": "testResult.auto.uca4f9aeade4f.t0_r8_c5.measuredValue92",
  "schemaKey": "step_site_record.site_qc_performance.t0_r8_c5.measuredValue92",
  "hierarchyKey": "CT值线性（仅验收检测）_检测结果",
  "judgmentCriterionText": "…",
  "precision": 1,
  "table": {
    "cellId": "t0_r8_c5",
    "tableId": 0,
    "row": 8,
    "col": 5,
    "itemName": "CT值线性（仅验收检测）",
    "typeName": "检测结果",
    "fieldName": "CT值线性（仅验收检测）_120_检测结果"
  }
}
```

**防护表示例**（`site_radiation_protection`）：

```json
{
  "id": "field280",
  "type": "number",
  "label": "测量读数M",
  "pdfFieldId": "f259",
  "submitPath": "testResult.auto.u3bcf79e8f4a0.t0_r9_c3.field280",
  "precision": 1,
  "unit": "μSv/h",
  "table": {
    "cellId": "t0_r9_c3",
    "tableId": 0,
    "row": 9,
    "col": 3,
    "radiationPoint": "观察窗30cm外表面处_r9",
    "radiationColumn": "测量读数M",
    "readingIndex": 1
  }
}
```

### 4.1 `table` 字段说明

| 键 | 说明 |
|----|------|
| `cellId` | 稳定单元格 ID，格式 `t{tableId}_r{row}_c{col}` |
| `tableId` | 表格序号（防护表多为 `0`） |
| `row` / `col` | 行、列索引（与 PDF 表结构一致） |
| `radiationPoint` | 检测点位（行语义） |
| `radiationColumn` | 列语义：如 `测量读数M`、`测量均值Mbar`、`报出值D`、`备注` |
| `readingIndex` | 同点位多次测量读数序号（1、2、3…） |
| `meanOfReadings` | `true` 表示该格为均值列，可读 `mean: true` 简化标记 |

前端建议：

1. 用 `table.row` / `table.col` / `table.radiationPoint` / `table.radiationColumn` 组表渲染；
2. 用 `pdfFieldId` 做 PDF 预览高亮与回填；
3. 用 `submitPath` 读写提交体；
4. 均值列：`type` 可能为 `computed`，`formula` 引用同点位读数 `pdfFieldId`。

---

## 5. 没有 PDF 坐标时如何画出「详细界面」？

前端 JSON 的职责是 **语义表单**（章节、分组、表格、标签、校验、提交路径），不是 1:1 像素复刻 PDF。

| 能力 | 数据来源 | 说明 |
|------|----------|------|
| 章节/步骤导航 | `steps[].sections[].id` / `title` | 六大现场记录 + 质控/签字 |
| 表格式质控/防护 | `table.row` / `col` / `itemName` / `fieldName` | 按行列表或卡片组，不必叠在 PDF 坐标上 |
| 复杂 DSA 矩阵 | `section.matrix`（`matrixTable`） | 见 `HTMLPDF_MATRIX_TABLE_SCHEMA.md` |
| 输入说明/判定标准 | `judgmentCriterionText`、`table.fieldName` | 非 `submitPath` |
| PDF 预览高亮、打印回填 | **坐标模板** + `pdfFieldId` | 可选：另请求模板 JSON 取 `rect` |

典型流程：

1. 用前端 JSON 渲染可填表单（分组 + 表格 + 副标题）。  
2. 需要「对照 PDF 框」时，用同一任务的坐标模板，按 `pdfFieldId` 画 overlay。  
3. 提交只传业务 JSON（按 `submitPath` 聚合）；生成 PDF 时服务端再按 `pdfFieldId` 写回。

---

## 6. `matrix` / `matrixTable`（DSA 等多级表）

部分复杂质控项使用 `section.layout = "matrixTable"`：

```json
{
  "id": "sec_sv_h_matrix",
  "title": "透视防护区…",
  "layout": "matrixTable",
  "matrix": {
    "headerFields": [ /* 顶部 kV/mA 等 */ ],
    "rows": [
      {
        "id": "row_0",
        "cells": {
          "measuredValue": { "id": "…", "type": "number", "pdfFieldId": "f…", … }
        }
      }
    ]
  }
}
```

详见 `HTMLPDF_MATRIX_TABLE_SCHEMA.md`。

---

## 7. 互斥选项（`radio`）

检测类型、设备类型、剂量率单位等由多个 PDF 勾选框合并为一个 `radio`：

```json
{
  "id": "testType",
  "type": "radio",
  "label": "检测类型",
  "enumRef": "testType",
  "defaultValue": "status",
  "pdfFieldId": "f606",
  "submitPath": "hospitalInfo.testType"
}
```

根级 `enums.testType`：

```json
[
  { "value": "acceptance", "label": "验收检测" },
  { "value": "status", "label": "状态检测" }
]
```

---

## 8. 平面布局图（`floorPlan`）

- **仅** `sectionType: "floorPlan"` 章节内、**非签字**的平面图栏位为 `type: "floorPlan"`。
- 须带 `pdfFieldId` 与 `submitPath`（常为 `signatures.floorPlan`）；前端输出绘制结果 **base64** 图片。

```json
{
  "id": "floorPlan",
  "type": "floorPlan",
  "label": "平面布局示意图",
  "pdfFieldId": "f637",
  "submitPath": "signatures.floorPlan",
  "submitBucket": "signatures"
}
```

签字栏仍为 `signature`（`pdfFieldId` 或 `pdfFieldIds`）。

---

## 9. 提交与回填分工

| 能力 | 使用字段 |
|------|----------|
| 表单渲染 / 校验 / 提交 | `steps` + `submitPath` |
| PDF 预览框选高亮 | `pdfFieldId`（需结合坐标模板 JSON 的 `rect`） |
| PDF 生成回填 | 服务端按 `pdfFieldId` + 提交值写入；**不依赖前端 JSON 中的坐标** |

坐标模板路径示例：任务绑定的 `*_template.json` → `pdf.fields[]`：

```json
{
  "id": "委托编号",
  "pdfFieldId": "f1",
  "rect": [1, 130.35, 120.12, 58.4, 30.35],
  "fieldType": "text"
}
```

---

## 10. 获取方式与导出模式

| 场景 | 接口 | 格式 |
|------|------|------|
| **移动端 / App（默认）** | `GET /api/v2/inspections/projects/{projectId}/tasks/{taskNo}/export-frontend-json` | 运行态 compact JSON，`Content-Type: application/json`，**无** `indent=2`，支持 **ETag / 304** 与 **gzip** |
| 后台调试 | 同上 + `?mode=editor` | 可读 `indent=2` 附件下载（含更多编辑器结构，仅供调试） |
| HTMLPDF 编辑器 | `POST /files/htmlpdf/api/export-frontend-json/` | 文件库辅助 JSON，不写任务主绑定 |

运行态响应序列化：

```python
json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
```

服务端日志（与前端对齐排障）：`taskNo, bytes, steps, sections, fields, matrixRows, matrixCells`。

坐标模板 `*_template.json`（`unified_form_template/v2`）仍单独保存 `pdf.fields[].rect`；**不会**出现在移动端运行态 JSON 中。

---

## 11. 变更摘要（相对早期导出）

| 项目 | 现在 |
|------|------|
| PDF 坐标 | 仅在坐标模板 JSON，前端 JSON 用 `pdfFieldId` |
| `pdfBindings` / `pdfAnchor` | 已移除 |
| 嵌套 `source` | 已压平为顶层字段 |
| 防护表/质控表行列与长命名 | `table.{cellId,row,col,itemName,typeName,fieldName,…}` |
| 判定说明 | 顶层 `judgmentCriterionText` |
| 逻辑键 | 顶层 `schemaKey`（原 `source.key`）、`hierarchyKey` |
| 根级 `sections` 目录副本 | 已移除（章节仅在 `steps.sections`） |
| `sectionType` / `sectionKey` | 六大章节稳定枚举，前端勿依赖中文标题 |
| 防护章 | 运行态为 `matrixTable` + 22 行 `protectionPoints` |
| 移动端 JSON 体积 | compact + gzip；`mode=editor` 仅调试用 |
| `precision` | 数值/计算栏小数位，常为 `1` |

实现入口：`utils/frontend_schema_rule_engine.py`（规则拼装）+ `utils/frontend_runtime_export.py`（运行态压平）。
