# 统一模板 JSON：PDF 栏位公式（`fieldExpression`）与现场实时判定

本文说明两类与 **`pdfFieldId`**（`f1`、`f140`…）相关的模板数据：

1. **`fieldExpression`（及兼容的顶层 `fieldFormulas`）**：栏位之间的**算术关系**，供前端按依赖顺序重算「计算结果」等数值栏。
2. **`judgmentCriteriaByTestType` / `fieldVerdict` / `fieldVerdictPlan`**：**计算结果 / 报出值** 是否满足「判定标准」的规则，并与首页 **检测类型**（验收检测 / 状态检测）联动；语义与后端报告中的「判定标准 + 测量值」一致，实现参考 `utils/verdict_from_criterion.py` 与 `docs/报告单项判定规则与逻辑.md`。

与 `formSchema.steps` 里表单控件的 `id` / `formula` / `verdict.rule`（表达式引擎）是**另一套体系**：前者面向 **PDF 坐标栏位 ID**，后者面向 **表单控件 ID**。同一业务可同时存在多套描述，由前端约定是否把 PDF 侧结果同步到某表单字段。

---

## 1. PDF 栏位公式（推荐：`pdf.fields[].fieldExpression`）

### 1.1 模板里怎么写（推荐）

在 **`pdf.fields`** 中对应目标的栏位对象上增加 **`fieldExpression`**（别名 **`pdfFieldExpression`**），值为**仅含 `f`+数字**占位符的算术表达式（运算符：`+ - * / %`、括号 `()`）。

**示例（栏位 `f19` 由 `f140`、`f141` 计算）：**

```json
{
  "id": "透视受检者入射体表空气比释动能率典型值/(mGy/min)_计算结果",
  "rect": [1, 551.9, 298.89, 63.8, 77.85],
  "pdfFieldId": "f19",
  "fieldExpression": "f140 * f141",
  "judgmentCriteriaByTestType": {
    "acceptance": "<=25.0",
    "status": "<=25.0"
  }
}
```

**约定：**

- 表达式中的栏位标识一律 **`f` + 十进制整数**（大小写不敏感，导出时规范为小写 `f`）。
- 同一栏位可同时写 **`judgmentCriteriaByTestType`**（见第 2 节）与 **`fieldVerdict`**（互斥勾选等特殊形态），库模板经 `compact_unified_pdf_fields_for_storage` 写入磁盘时会**保留**上述键。

### 1.2 兼容：顶层 `fieldFormulas`

仍支持在模板根或 `formSchema` 下写 **`fieldFormulas`**（或 `pdfFieldFormulas`、数组写法）。保存库模板时，后端会尽量把条目**下发**到 `pdf.fields` 中匹配 `pdfFieldId` 的行的 **`fieldExpression`**；无法匹配目标的条目会**保留在根级 `fieldFormulas`** 作为兜底。

### 1.3 规则引擎导出后前端会得到什么

`build_frontend_schema_by_rules` 之后若再经过 **`merge_field_formulas_into_frontend`**（导出前端 JSON、HTMLPDF 等链路已接入）：

- **不再**在 schema 根级写入 `fieldFormulas`、`formulaPlan`（与当前前端约定一致）。
- 会把公式写到 **`steps` → `sections` → `fields`**（及 matrix 单元格）里与 PDF 绑定的字段上：
  - **`source.pdfFieldExpression`**：与模板中 `fieldExpression` 相同；
  - **`formula`**：同上字符串，便于与表单内其它 `formula` 字段统一消费。
- 会把 **`judgmentCriteriaByTestType`**、**`fieldVerdict`** 写入对应字段的 **`source`**。
- 根级可选 **`pdfFieldFormulaValidation`**：`ok`、`errors`（如环路）、`warnings`（如引用了未出现在 `pdf.fields` 中的 `f` 号）。

若需自行拓扑求值，可在后端用 `compile_pdf_field_formula_bundle` 根据合并后的公式映射生成顺序（该函数仍返回内部用的 `formulaPlan` 结构，但默认不会放进导出 JSON 根级）。

### 1.4 与表单内 `type: "computed"` 的关系

- **`fieldExpression` / `pdfFieldExpression`**：描述 **PDF 坐标栏位** 之间的数值关系。
- **`computed` / `verdict`**：描述 **表单控件层**，使用表单 `id` 与表达式引擎。

实现参考：`django/utils/pdf_field_formulas.py`、`django/utils/unified_template_fields.py`（紧凑落盘字段）；接入：`merge_field_formulas_into_frontend`。

---

## 2. `fieldVerdictPlan`：现场表「计算结果 / 报出值」与检测类型联动判定

### 2.1 业务规则（与表格表头一致）

- 表头中 **「计算结果」「报出值」** 等列，在逻辑上都对应**同一行的验收或状态限值**；前端需在用户勾选 **检测类型** 后，对相应栏位做**实时合格 / 不合格 / 待判定**提示（与报告「单项判定」同源思路，见 `docs/报告单项判定规则与逻辑.md`）。
- **检测类型**由规则引擎导出为 `steps` 中的 `testType`（`enumRef: "testType"`），取值与 `enums.testType` 一致：
  - **`acceptance`**：验收检测  
  - **`status`**：状态检测  
  （见 `utils/frontend_schema_rule_engine.py` 中对 `enums.testType` 的注入。）
- 若表格中 **验收与状态共用同一单元格**，则两种检测类型下 **判定标准字符串相同**（模板中只写一条即可）。
- 若表头分列「验收」「状态」两格，则规则中分别为 `criterionTextAcceptance` / `criterionTextStatus`（或见下文字段 `criteriaByTestType`）。

### 2.2 判定标准字符串（与后端引擎对齐）

每条数值类规则的核心是 **判定标准字符串**，语法与 `verdict_from_criterion.normalize_criterion_text` + `_eval_simple` 支持的句式一致，例如：

| 类型 | 示例 |
|------|------|
| 上界 | `<=25.0`、`≤88.0`、`不超过 176.0` |
| 下界 | `>=f33`（见 2.4 动态引用）、`≥1.5` |
| 对称允差 | `0±10`、`100 ± 15`、`1.0+/-0.1` |
| 区间 | `a～b`、`a~b`（闭区间，含端点容差） |

多条件可用 **`且` / `并且` / `；`** 连接，**全部满足**才为合格（与报告逻辑一致）。

### 2.3 模板中的书写位置（建议）

与 `fieldExpression` 对称，可选用**模板根**或 **`formSchema`** 下的聚合结构 **`fieldVerdictPlan`**（见第 2.5 节）。**更推荐**与 JS001 示例一致：在 **`pdf.fields`** 各行上直接写 **`judgmentCriteriaByTestType`** / **`fieldVerdict`**，经 `merge_field_formulas_into_frontend` 后会出现在导出 **`steps`** 各字段的 **`source`** 上。

- **`merge_field_formulas_into_frontend`** 会把 `pdf.fields` 上的公式与判定元数据写入 **`steps`**；**不会**自动合并根级 `fieldVerdictPlan`（若仅用聚合 plan，需前端从原始模板 JSON 读取，或后续扩展合并逻辑）。

### 2.4 动态上界 / 下界：标准串中的 `fNN` 占位符

当标准依赖**同行或同表其它格**的数值（如「≥ 基准格」）时，允许在标准串中直接写 **`f33`、`f48`** 等占位符。前端在调用与 `verdict_from_measurement` 等价的判定前，应先将标准串中的每个 **`f\d+`** 替换为当前表单/PDF 映射中该栏位的**数值文本**（解析失败则该条规则结果为「待判定」/`null`）。

示例：`">=f33"` 在 `f33=1.2` 时等价于 `">=1.2"`。

### 2.5 `fieldVerdictPlan` 顶层结构（前端可解析的 JSON）

```json
{
  "fieldVerdictPlan": {
    "version": 1,
    "notation": "pdfFieldId",
    "testType": {
      "submitPath": "hospitalInfo.testType",
      "enumRef": "testType",
      "valueAcceptance": "acceptance",
      "valueStatus": "status"
    },
    "passLabel": "合格",
    "failLabel": "不合格",
    "unknownLabel": null,
    "rules": []
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `version` | `number` | 计划格式版本，当前为 `1`。 |
| `notation` | `string` | 固定 `"pdfFieldId"`。 |
| `testType` | `object` | 与导出表单中检测类型控件一致；用于选择用哪条标准。 |
| `passLabel` / `failLabel` | `string` | 与报告用语一致即可。 |
| `unknownLabel` | `string \| null` | 可选；无法解析时展示 copy。 |
| `rules` | `array` | 见下节 **`rule` 对象**。 |

### 2.6 单条 `rule` 对象

#### 2.6.1 数值类（`kind: "numeric"`）

用于「计算结果」「报出值」等可抽出**一个主测量数**的栏位（与报告中 `_parse_first_number` 思路一致）。

```json
{
  "id": "air_kerma_rate_row1",
  "kind": "numeric",
  "measuredPdfFieldIds": ["f19", "f20"],
  "measuredPick": "firstNumber",
  "criteriaByTestType": {
    "acceptance": "<=25.0",
    "status": "<=25.0"
  },
  "verdictDisplayPdfFieldIds": [],
  "notes": "输出剂量率：计算结果与报出值同标准"
}
```

| 字段 | 说明 |
|------|------|
| `id` | 稳定 id，便于调试与埋点。 |
| `kind` | 固定 `"numeric"`。 |
| `measuredPdfFieldIds` | 参与本行判定的栏位；通常包含「计算结果」与「报出值」两个 `pdfFieldId`。 |
| `measuredPick` | 建议 **`firstNumber`**：在合并文本或按数组顺序，取**第一个可解析浮点数**作为测量值；也可约定 `max` / `min` / `allMustPass`（若设 `allMustPass`，则每个栏位各算一次，全部合格才算合格）。 |
| `criteriaByTestType` | **必填**。键为 `acceptance` / `status`；值为标准串。二者可相同（共用单元格）。 |
| `verdictDisplayPdfFieldIds` | 可选；若现场表有「单项判定」格，可填对应 `pdfFieldId`，仅作展示绑定，不参与计算。 |
| `notes` | 可选，给人看的说明。 |

**合并单元格（验收+状态同一标准）** 时写法：

```json
"criteriaByTestType": {
  "acceptance": "<=400.0",
  "status": "<=400.0"
}
```

#### 2.6.2 互斥勾选（`kind: "exclusiveCheck"`）

用于「合格 / 不合格」二选一勾选题（如 f124 合格、f125 不合格），**与数值标准无关**。

```json
{
  "id": "overall_checkbox_pass_fail",
  "kind": "exclusiveCheck",
  "passWhenCheckedPdfFieldIds": ["f124"],
  "failWhenCheckedPdfFieldIds": ["f125"],
  "criteriaByTestType": {
    "acceptance": "__exclusive_check__",
    "status": "__exclusive_check__"
  },
  "notes": "f124 勾选=合格，f125 勾选=不合格；二者互斥"
}
```

约定：`criteriaByTestType` 中占位串 **`__exclusive_check__`** 表示忽略数值引擎；前端逻辑为：

- 仅 `passWhenCheckedPdfFieldIds` 中某一栏 `checked === true` → **合格**；
- 仅 `failWhenCheckedPdfFieldIds` 中某一栏 `checked === true` → **不合格**；
- 都未选或同时选中 → **待判定**（或业务上提示用户必须互斥）。

（若模板更倾向省略 `criteriaByTestType`，前端可对 `kind === "exclusiveCheck"` 做特判，不读标准串。）

---

## 3. JS001（JXFS-JS001 / new-test-001）类表格：规则列表示例

以下与业务描述对齐：**f19、f20** 同行；**f23、f24**；**f27、f28**；**f31** 与 **f33/f34**；**f37**；**f43、f44** 与 **f48**；**f50、f52** 允差；**f58～f77**（20 项）；**f124、f125**。  
仓库示例见 **`media/file_library/templates/f8efbadc529947cfbf49afe3e43ed838_new-test-001.json`**：公式写在对应 **`pdf.fields[]`** 的 **`fieldExpression`**，判定写在同栏位的 **`judgmentCriteriaByTestType`**，**f124/f125** 使用 **`fieldVerdict`**。导出前端 JSON 时上述内容会进入 **`steps`** 各字段的 **`source`**。

以下为**可选**的聚合结构 **`fieldVerdictPlan`**（与逐栏位写法二选一或并存，由前端择一消费）：

```json
{
  "fieldVerdictPlan": {
    "version": 1,
    "notation": "pdfFieldId",
    "testType": {
      "submitPath": "hospitalInfo.testType",
      "enumRef": "testType",
      "valueAcceptance": "acceptance",
      "valueStatus": "status"
    },
    "passLabel": "合格",
    "failLabel": "不合格",
    "rules": [
      {
        "id": "row_output_dose_rate_calc_report_25",
        "kind": "numeric",
        "measuredPdfFieldIds": ["f19", "f20"],
        "measuredPick": "firstNumber",
        "criteriaByTestType": {
          "acceptance": "<=25.0",
          "status": "<=25.0"
        }
      },
      {
        "id": "row_output_dose_rate_calc_report_88",
        "kind": "numeric",
        "measuredPdfFieldIds": ["f23", "f24"],
        "measuredPick": "firstNumber",
        "criteriaByTestType": {
          "acceptance": "<=88.0",
          "status": "<=88.0"
        }
      },
      {
        "id": "row_output_dose_rate_calc_report_176",
        "kind": "numeric",
        "measuredPdfFieldIds": ["f27", "f28"],
        "measuredPick": "firstNumber",
        "criteriaByTestType": {
          "acceptance": "<=176.0",
          "status": "<=176.0"
        }
      },
      {
        "id": "row_hvl_or_similar_ge_ref",
        "kind": "numeric",
        "measuredPdfFieldIds": ["f31"],
        "measuredPick": "firstNumber",
        "criteriaByTestType": {
          "acceptance": ">=f33",
          "status": ">=f34"
        },
        "notes": "验收用 f33 为界，状态用 f34；提交前将 f33/f34 换为数值再跑判定引擎"
      },
      {
        "id": "row_dose_indicator_upper_bound",
        "kind": "numeric",
        "measuredPdfFieldIds": ["f37"],
        "measuredPick": "firstNumber",
        "criteriaByTestType": {
          "acceptance": "<=2.0",
          "status": "<=4.0"
        }
      },
      {
        "id": "row_product_le_ref48",
        "kind": "numeric",
        "measuredPdfFieldIds": ["f43", "f44"],
        "measuredPick": "firstNumber",
        "criteriaByTestType": {
          "acceptance": "<=f48",
          "status": "<=f48"
        }
      },
      {
        "id": "row_deviation_symmetric_tolerance_f52",
        "kind": "numeric",
        "measuredPdfFieldIds": ["f52"],
        "measuredPick": "firstNumber",
        "criteriaByTestType": {
          "acceptance": "0±10",
          "status": "0±15"
        },
        "notes": "对称允差 n±m 与 verdict_from_criterion 一致；基准非 0 时改为例如 100±10"
      },
      {
        "id": "row_deviation_symmetric_tolerance_f50",
        "kind": "numeric",
        "measuredPdfFieldIds": ["f50"],
        "measuredPick": "firstNumber",
        "criteriaByTestType": {
          "acceptance": "0±10",
          "status": "0±15"
        }
      },
      {
        "id": "rows_shielding_20_lines_le_400",
        "kind": "numeric",
        "measuredPdfFieldIds": [
          "f58", "f59", "f60", "f61", "f62", "f63", "f64", "f65", "f66", "f67",
          "f68", "f69", "f70", "f71", "f72", "f73", "f74", "f75", "f76", "f77"
        ],
        "measuredPick": "allMustPass",
        "criteriaByTestType": {
          "acceptance": "<=400.0",
          "status": "<=400.0"
        },
        "notes": "20 项各自与 400.0 比较；allMustPass 表示每个栏位单独判定且均需合格"
      },
      {
        "id": "mutual_pass_fail_checks",
        "kind": "exclusiveCheck",
        "passWhenCheckedPdfFieldIds": ["f124"],
        "failWhenCheckedPdfFieldIds": ["f125"],
        "criteriaByTestType": {
          "acceptance": "__exclusive_check__",
          "status": "__exclusive_check__"
        }
      }
    ]
  }
}
```

说明：

- **`f50` / `f52`**：上例已拆成两条 `rule`，标准相同；若二者表示同一物理量且 UI 只校验「有值的那一列」，也可合并为一条并在 `measuredPdfFieldIds` 中写 `["f50","f52"]` 且使用 **`measuredPick": "firstNumber"`**（先取到数的栏位参与判定）。
- **`f58～f77`**：若模板中 20 行并非连续 `f58`…`f77`，仅替换 `measuredPdfFieldIds` 列表即可；`allMustPass` 为建议语义，也可拆成 20 条 `rule` 以便 UI 逐行标红。

---

## 4. 前端处理流程（建议）

1. 载入导出 schema：读取 `enums`、`steps`（得到 `testType` 当前值）；在 **`steps` 内各字段**读取 **`source.pdfFieldExpression`**、**`formula`**、**`source.judgmentCriteriaByTestType`**、**`source.fieldVerdict`**；若仍使用聚合描述，可另从原始模板读取 **`fieldVerdictPlan`**。
2. 用户每次编辑后：  
   - 按各字段上的 **`formula` / `source.pdfFieldExpression`** 解析依赖的 `fNN`，对公式目标栏位做**拓扑顺序**求值（可自行建图，或调用后端辅助接口）；  
   - 取 `testType` ∈ `{ acceptance, status }`，对每个带 **`source.judgmentCriteriaByTestType`** 的字段取对应键的标准串；若有 **`fieldVerdictPlan.rules`** 则按其语义处理；  
   - 将标准串中的 `fNN` 替换为当前数值；  
   - 调用与 `verdict_from_measurement(measured, criterion)` 等价的逻辑（或请求后端接口）得到 **合格 / 不合格 / 无法判定**。
3. **`exclusiveCheck`**：对 **`source.fieldVerdict.kind === "exclusiveCheck"`** 的栏位，不走数值引擎，仅按互斥勾选逻辑判定。

---

## 5. 与报告生成的关系

报告侧单项判定优先走 **`verdict.rule`**（若配置），否则用 **判定标准文本 + 测量文本**（见 `docs/报告单项判定规则与逻辑.md`）。  
现场 JSON 中的 **`fieldVerdictPlan`** 用于**填表阶段**与报告尽量**同源**的数值规则，减少「现场合格、报告不合格」的偏差；最终仍以报告管线解析占位符与矩阵表为准。

---

## 6. 维护建议

- **公式**：避免环路依赖；关注 `pdfFieldFormulaValidation.warnings`。  
- **判定**：新增标准句式时，与 `utils/verdict_from_criterion.py` 同步；新增行类型时同步更新 `fieldVerdictPlan.rules`。  
- **检测类型枚举**勿与 `frontend_schema_rule_engine` 中 `testType` 的 `value` 冲突（`acceptance` / `status`）。

---

## 7. 模板编辑器（HTMLPDF）里怎么用：新手向

项目在 **`htmlpdf/templates/index.html`** 侧栏提供 **「公式与判定（当前栏位）」** 面板（需先在上方列表点某一栏，或让画布上该栏的文本框获得焦点）。

### 7.1 算术公式（`fieldExpression`）

- **保存到 JSON 时只保存「等号右边」**；左边永远是当前栏的 **`pdfFieldId`**（与侧栏灰色小字 `f78` 一致）。
- 在编辑器里可以只写 **`f34+f35`**，也可以从别处粘贴整句 **`f78=f34+f35`**，保存前会自动去掉左边的 **`f78=`**。
- 允许的符号：其它栏的 **`f` + 数字**，以及 **`+ - * / % ( )`**（与第 1 节一致）。
- **「把选中的 f 号插入公式」**：先在下拉框选其它栏，再点按钮，避免手抄错号。

### 7.2 判定标准（`judgmentCriteriaByTestType`）

- **验收检测**、**状态检测** 各一行，对应 JSON 里的键 **`acceptance`**、**`status`**（与现场「检测类型」联动，见第 2 节）。
- 常用写法示例（与 `verdict_from_criterion` 一致）：
  - 上界：**`<=20.0`**、**`≤88.0`**（编辑器里推荐用 ASCII **`<=`** / **`>=`**，便于检索）。
  - 动态上界：**`>=f33`**（标准里引用另一栏的测量值）。
  - 对称允差：**`f23±10.0`** 或 **`0±10`**、**`100 ± 15`**、**`1.0+/-0.1`**。
- 若在标签里习惯写 **`f23+-10.0`**（加号、减号连写表示「正负允差」），在编辑器点 **「自动检索判定」** 时会尽量规范为 **`f23±10.0`** 再写入（仍以引擎最终能解析的格式为准）。

### 7.3 自动检索 vs 人工定型（`judgmentCriteriaManual`）

- **自动检索判定**：根据当前栏 **侧栏标签 / 占位说明** 等文案，尝试用简单规则猜 **`<=…`**、**`±`**、**`fNN+-…`** 等；猜不到时请手工填写。
- 勾选 **「判定已由人工定型」** 后，会往模板 JSON 写入 **`judgmentCriteriaManual`: `true`**。此后 **自动检索不会再覆盖** 您填写的验收/状态两行；需要重新让程序猜时，请先取消勾选再点自动检索。
- **人工修改判定文案**时，编辑也会自动勾选「人工定型」，避免后续误点自动检索冲掉您的字。

### 7.4 与统一模板落盘的关系

保存模板时，上述内容随 **`pdf.fields[]`** 一并写入库（经 `compact_unified_pdf_fields_for_storage`）；**`fieldExpression`**、**`judgmentCriteriaByTestType`**、**`judgmentCriteriaManual`** 的语义与本文第 1～2 节及 `utils/unified_template_fields.py` 一致。

---

文档版本：与 `utils/pdf_field_formulas.py`（栏位级 `fieldExpression`、导出写入 `steps`）及报告判定说明对齐；`fieldVerdictPlan` 为可选聚合约定；**第 7 节**描述 HTMLPDF 编辑器侧栏行为。
