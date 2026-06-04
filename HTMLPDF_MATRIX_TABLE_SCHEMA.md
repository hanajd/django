# HTMLPDF `matrixTable` JSON 结构规范

本文定义 Django `htmlpdf` 导出前端 JSON 时，针对 DSA 多层级固定表格的结构化输出规范。

目标：

- 后端描述表格结构，前端按结构渲染；
- 前端不再解析中文标题，不硬编码设备类型；
- 兼容旧提交路径与 PDF 回填链路。

## 1. 适用场景

当一个检测项具备“多级行头 + 固定值列 + 可合并单元格”特征时，使用：

- `section.layout = "matrixTable"`

不再拆成多个 `layout = "form"` 的 section。

## 2. section 结构

```json
{
  "id": "sec_sv_h_matrix",
  "title": "透视防护区检测平面上周围剂量当量率/(μSv/h)",
  "layout": "matrixTable",
  "matrix": {
    "headerFields": [],
    "rowHeaderColumns": [],
    "valueColumns": [],
    "rows": []
  }
}
```

字段含义：

- `headerFields`: 表格顶部参数区字段（如 `kV`、`mA`、`s`、`校准因子 CF`）
- `rowHeaderColumns`: 行头列定义（可配置自动合并）
- `valueColumns`: 值列定义（如 `检测值`、`报出值`）
- `rows`: 每行的行头文本与可填写单元格

## 3. 字段唯一性与兼容键

### 3.1 唯一 id

`matrixTable` 内每个可填写字段必须有全局唯一 `id`。

示例（值单元格）：

- `sv_h_row_0_measured_value`
- `sv_h_row_0_report_value`

### 3.2 source.key

为避免历史数据中重复 `id` 造成覆盖，建议每个字段额外携带 `source.key`（全局唯一）：

```json
"source": {
  "key": "step_qc_items.sec_sv_h_matrix.rows[0].measuredValue"
}
```

前端可优先用 `source.key` 作为状态键。

## 4. 提交路径迁移策略

`matrixTable` 默认输出结构化新路径，并保留旧路径用于兼容：

- `source.submitPath`: 新结构化路径（如 `testResult.svH.rows[0].measuredValue`）
- `source.legacySubmitPath`: 旧路径（如 `testResult.test4`）

这样可支持：

- 新前端按结构化对象提交；
- 旧后端/旧回填逻辑按 legacy 路径平滑兼容。

职责约定（固定）：

- `source.key`: 前端表单状态唯一键，防止重复 `id` 覆盖
- `source.submitPath`: 提交新结构化对象路径
- `source.legacySubmitPath`: 旧接口/旧回填兼容路径

## 5. `rows` 完整示例（单行）

```json
{
  "id": "sv_h_row_0",
  "headers": {
    "serialNo": "1",
    "inspectionItem": "透视防护区检测平面上周围剂量当量率/(μSv/h)",
    "operatorPosition": "床侧第一术者位（距球管中心60cm处）",
    "heightType": "距离地板高度",
    "point": "20cm（足部）"
  },
  "cells": {
    "measuredValue": {
      "id": "sv_h_row_0_measured_value",
      "type": "number",
      "label": "检测值",
      "required": false,
      "defaultValue": null,
      "precision": 1,
      "unit": "μSv/h",
      "source": {
        "pdfFieldId": "f85",
        "page": 2,
        "anchorType": "text",
        "submitBucket": "testResult",
        "submitPath": "testResult.svH.rows[0].measuredValue",
        "legacySubmitPath": "testResult.test",
        "key": "step_qc_items.sec_sv_h_matrix.rows[0].measuredValue"
      }
    },
    "reportValue": {
      "id": "sv_h_row_0_report_value",
      "type": "number",
      "label": "报出值",
      "required": false,
      "defaultValue": null,
      "precision": 1,
      "unit": "μSv/h",
      "source": {
        "pdfFieldId": "f90",
        "page": 2,
        "anchorType": "text",
        "submitBucket": "testResult",
        "submitPath": "testResult.svH.rows[0].reportValue",
        "legacySubmitPath": "testResult.reportvalue6",
        "key": "step_qc_items.sec_sv_h_matrix.rows[0].reportValue"
      }
    }
  }
}
```

约束：`rows[].cells.*` 必须输出完整 field schema（至少包含 `id/type/label/required/defaultValue/precision/unit/source`），不能只给值或 `pdfFieldId`。

## 6. 单位规范

剂量率表 `检测值/报出值` 的单位应统一为：

- `μSv/h`

不得沿用历史误标（如 `mm`）。

## 7. 当前后端实现说明（已落地）

规则引擎文件：`utils/frontend_schema_rule_engine.py`

在 `build_frontend_schema_by_rules()` 后处理阶段，会执行：

- `_upgrade_sv_h_sections_to_matrix_table(result)`

该转换会把：

- `sec_sv_h` -> `matrix.headerFields`
- `sec_sv_h_60cm_20cm ... sec_sv_h_120cm_155cm` -> `matrix.rows`

并生成：

- `layout: "matrixTable"`
- `matrix.rowHeaderColumns`（含 `merge: auto/none`）
- `matrix.valueColumns`（检测值、报出值）
- `source.submitPath` + `source.legacySubmitPath` + `source.key`

## 8. 前端渲染约定

前端遇到 `layout = "matrixTable"` 时：

1. 渲染 `headerFields`
2. 按 `rowHeaderColumns` 渲染左侧行头
3. 按 `valueColumns` 渲染输入列
4. `merge = "auto"` 时，相邻行相同文本自动合并
5. 单元格字段沿用现有动态字段渲染器

无需解析 `section.title` 中中文层级。
