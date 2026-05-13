# 统一模板 JSON：按 pdfFieldId 书写的公式（fieldFormulas）

后端在**统一模板**里用 **`pdfFieldId`**（形如 `f1`、`f140`）描述 PDF 栏位之间的数值关系，供前端在填表或预览时按依赖顺序重算。这与 `formSchema.steps` 里表单字段的 `id` / `formula`（表达式引擎语法）是两套体系：前者面向 **PDF 坐标栏位 ID**，后者面向 **表单控件 ID**。

## 1. 模板里怎么写

在模板 JSON 中增加 **`fieldFormulas`**，类型为 **对象**：键为目标的 `pdfFieldId`，值为**仅含 `f`+数字**占位符的算术表达式字符串（与常见 JS 表达式运算符兼容：`+ - * / %`、括号 `()`，可按需扩展空格）。

**示例（与 JS-001 模板一致）：**

```json
"fieldFormulas": {
  "f19": "f140 * f141",
  "f23": "f143 * f144",
  "f27": "f146 * f147",
  "f43": "f42 * f49",
  "f51": "(f148 + f149 + f150) / 3",
  "f53": "(f151 + f152 + f153) / 3",
  "f54": "(f51 + f53) / 2",
  "f52": "(f51 - f54) / f54 * 100"
}
```

**约定：**

- 键、表达式中出现的栏位标识一律使用 **`f` + 十进制整数**（大小写不敏感，导出时会规范为小写 `f`）。
- 左侧键表示「该 PDF 栏位由公式计算得到」；右侧引用其它 `pdfFieldId` 或**同为公式目标**的栏位（如 `f54` 依赖 `f51`、`f53`）。
- 也可使用数组形式（与对象等价，便于从表格导入）：

  ```json
  "fieldFormulas": [
    { "pdfFieldId": "f19", "expression": "f140 * f141" }
  ]
  ```

**读取位置（优先级自上而下，命中即停）：**

1. 模板根：`fieldFormulas`
2. `formSchema.fieldFormulas`
3. 模板根或 `formSchema` 下的别名：`pdfFieldFormulas`

## 2. 规则引擎导出后前端会得到什么

调用 `build_frontend_schema_by_rules` 时，若模板中存在有效公式，会在返回的前端 schema **顶层**合并以下字段（无公式则不出现或保持原样）：

| 字段 | 含义 |
|------|------|
| `fieldFormulas` | 规范化后的 `{ "f19": "f140 * f141", ... }` |
| `formulaPlan` | 计算计划：`entries`（每条的目标 id、表达式、依赖列表、引用列表）、`evaluationOrder`（拓扑排序后的求值顺序）、`notation: "pdfFieldId"` |
| `formulaValidation` | `ok`、`errors`（如存在依赖环路）、`warnings`（如引用了未在 `pdf.fields` 中出现的 `pdfFieldId`） |

前端可将 **`evaluationOrder`** 与 **`entries`** 结合：按顺序对每个目标用表达式引擎求值，变量取值来自当前 PDF 栏位值映射（`fNN` → 该栏位数值）。若 `formulaValidation.ok` 为 `false`，应提示模板配置错误并避免静默错误结果。

## 3. 与表单内 `type: "computed"` 的关系

- **`fieldFormulas`**：描述 **PDF 层** 栏位之间的数值关系，与 PDF 模板强绑定。
- **表单步骤里的 `computed` / `verdict` 等**：描述 **表单控件层**，使用表单 `id`、`constants`、`enums` 等，由现有表达式引擎处理。

同一业务可同时存在两套公式；是否把 PDF 公式结果写回某表单字段由前端联调约定。

## 4. 维护建议

- 保存模板前可根据 `formulaValidation` 做校验；若需在后端也校验语法，可在现有表达式解析器上对 `f\d+` 做变量注册表检查。
- 避免 **环路依赖**（例如 `f1` 依赖 `f2` 且 `f2` 依赖 `f1`），否则 `evaluationOrder` 长度会小于节点数且 `errors` 非空。

实现参考：`django/utils/pdf_field_formulas.py`（解析、拓扑序、合并）；接入点：`build_frontend_schema_by_rules` 返回前对结果调用 `merge_field_formulas_into_frontend`。
