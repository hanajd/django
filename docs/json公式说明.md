# 统一模板 JSON：公式、条件公式与现场判定（前端对接说明）

本文档供 **Flutter / 现场填表前端** 与 **模板编辑器（HTMLPDF）** 对齐使用，描述当前（2026-05）已落地的 JSON 字段、表达式语法与处理建议。

---

## 0. 两套 ID 体系（必读）

| 体系 | 标识 | 典型字段 | 用途 |
|------|------|----------|------|
| **PDF 栏位** | `pdfFieldId`：`f1`、`f19`… | `fieldExpression`、`judgmentCriteriaByTestType` | 模板坐标栏位之间的计算与判定 |
| **表单控件** | `steps[].sections[].fields[].id` | `formula`、`type: computed`、`verdict.rule` | 动态表单表达式引擎 |

同一业务可能两套并存。导出给前端的 **`formSchema.steps`** 会把 PDF 侧公式合并到绑定字段的 **`formula`** / **`source.pdfFieldExpression`**，前端可按需选择消费哪一层。

**实现参考：**

- 导出管线：`utils/frontend_schema_rule_engine.py`、`utils/pdf_field_formulas.py`
- 条件公式：`utils/conditional_field_rules.py`
- 表达式求值（与后端回填对齐）：`utils/dynamic_form_expression.py`
- 判定引擎：`utils/verdict_from_criterion.py`

---

## 1. 数据存放位置

### 1.1 模板落盘（`pdf.fields[]`）

编辑器保存后，公式与判定写在 **`pdf.fields`** 各栏对象上（经 `compact_unified_pdf_fields_for_storage` 紧凑存储）：

```json
{
  "pdfFieldId": "f19",
  "page": 1,
  "x": 551.9,
  "y": 298.89,
  "w": 63.8,
  "h": 77.85,
  "placeholder": "计算结果",
  "fieldExpression": "f140 * f141",
  "formulaRules": [
    {
      "id": "rule_a1b2c3",
      "label": "默认",
      "condition": "",
      "expression": "f140 * f141"
    }
  ],
  "judgmentCriteriaByTestType": {
    "acceptance": "<=25.0",
    "status": "<=25.0"
  }
}
```

### 1.2 导出前端 schema（`formSchema.steps`）

`build_frontend_schema_by_rules` + 后处理链之后，栏位上常见键：

| 键 | 说明 |
|----|------|
| `type` | 有自动公式时为 `computed` |
| `formula` | 可执行的公式字符串（与 `fieldExpression` 同源或编译结果） |
| `fieldExpression` | 与模板一致的单行公式（兜底/默认） |
| `formulaRules` | 条件公式列表（编辑器写入、导出列表键） |
| `fieldExpressionRules` | **已废弃**；载入旧模板时只读兜底，新导出不再写入 |
| `dependsOn` | 依赖的 `pdfFieldId` 列表 |
| `judgmentCriteriaByTestType` | 验收/状态判定标准串 |
| `judgmentCriteriaManual` | `true` 表示判定已由人工定型，勿自动覆盖 |
| `source.pdfFieldExpression` | PDF 绑定来源 |
| `source.judgmentCriteriaByTestType` | PDF 绑定来源 |
| `chapterMeanPdfFieldId` | 防护表报出值栏位：该行测量均值 `pdfFieldId`（导出写入，供替换 `{mean}`） |

根级 **`formSchema`** 还可含：

```json
{
  "radiationProtectionChapter": {
    "chapterKey": "site_radiation_protection",
    "meanFormula": { "mode": "per_row_avg", "description": "同点位三次测量读数取平均" },
    "reportValueRules": []
  }
}
```

> **`fieldBindings` 不导出给前端**，由后端根据 `pdf.fields` / steps 栏位语义在服务端自行维护。

---

## 2. 公式表达式语法

### 2.1 基本约定

- 栏位引用：**`f` + 十进制整数**（大小写不敏感，规范为小写 `f12`）。
- **编号与顺序（唯一真源：编辑器侧栏）**：
  - `pdf.fields` 数组第 **N** 项 = 侧栏第 **N** 栏 = **`pdfFieldId: fN`**。
  - **不要**按 PDF 坐标、**不要**按 `f` 号数字升序来排栏位或改号——用户随时会增删/拖动侧栏，编号应随列表位置变化。
  - 侧栏重排时，编辑器在保存前执行 `applyPdfFieldIdRenumber`：按列表顺序赋 `f1…fN`，并 **自动 remap** `fieldExpression`、条件公式（`condition`/`expression`）、判定（`judgmentCriteriaByTestType`）、第五章 `reportValueRules` 等所有含 `f` 引用的文本。
  - 服务端导出 **保留** 编辑器提交的 `pdfFieldId`，不在导出链路上二次改号；`finalize` 里历史上的坐标排序 **只调整 section/step 区块顺序**，栏位数组在收尾时按 `listIndex`（`__editorOrder`）恢复为侧栏顺序。
  - **目标**：同一输入框在编辑器、库模板 JSON、前端 `steps` 中始终对应同一个 `pdfFieldId`，保证前后端操作一致。
- 保存时只存 **等号右边**；`f19=f140*f141` 入库前会剥成 `f140*f141`。
- 运算符：`+` `-` `*` `/` `%` `()`；比较 `<=` `>=` `==` `!=`；逻辑 `&&` `||`（后端会转为 `and`/`or`）。
- 整除：写法 **`~/`**（勿与范围符 `~` 混淆）。
- 三元：推荐 `if(条件, 真值, 假值)`（后端将 `if(` 预处理为 `__if__(`）。

### 2.2 范围输出（`a~b`）

用于「输出范围：最小值~最大值」类栏位，**整条公式**可写：

```text
min(f1,f2,f3)~max(f1,f2,f3)
round(min(f1,f2),1)~round(max(f1,f2),1)
```

**语义：**

- 在**顶层**（括号外）用 **`~`** 或全角 **`～`** 连接左右两段。
- 左右**分别求值**，结果格式化为字符串后用 **`~`** 拼接，例如 `1.2~5.6`。
- **`~/`** 仍为整除运算符，不会被当作范围分隔符。

后端实现：`utils/dynamic_form_expression.py` 中 `_split_top_level_range_token` + `evaluate_expression`。

### 2.3 常用函数

与 `dynamic_form_expression.build_eval_namespace` 对齐：

| 函数 | 说明 |
|------|------|
| `max(f1,f2)` / `min` | 最大 / 最小 |
| `sum` / `avg` | 求和 / 平均 |
| `std` / `stdev` / `stddev` | 样本标准差（n-1） |
| `var` / `variance` | 样本方差 |
| `median` / `count` | 中位数 / 有效数值个数 |
| `round(x, n)` | 四舍五入 |
| `abs` / `parseNum` | 绝对值 / 从带单位文本取数 |
| `if(c,a,b)` | 条件取值 |
| `coalesce` / `isEmpty` | 空值处理 |
| `lookup` / `unitFactor` / `ctdiw` | 查表 / 单位换算 / CTDIw |

**示例：**

```json
"fieldExpression": "round(avg(f1,f2,f3), 2)"
```

```json
"fieldExpression": "min(f10,f11,f12)~max(f10,f11,f12)"
```

### 2.4 条件编译（服务端导出）

若存在带 **`condition`** 的自动规则，后端可能将多条规则编译为嵌套 `if` 写入 **`formula`**（仅含「有条件」的项）：

```text
if(f12>=f13, f4*f5, if(f12<f13, avg(f1,f2,f3), ""))
```

前端以 **`formulaRules`** 为准做交互（旧 JSON 可回退读 `fieldExpressionRules`）；规则项内 **`expression` 与 `formula` 同义**，导出时二者均保留；栏位级 `formula` 可作为自动求值缓存。

---

## 3. 条件公式（`formulaRules`）

### 3.1 规则项结构（当前唯一格式）

每条规则 **仅四个字段**，无额外模式标记：

```json
{
  "id": "rule_a1b2c3d4e5f6",
  "label": "出束时间≥仪器响应时间",
  "condition": "f12>=f13",
  "expression": "avg(f1,f2,f3)"
}
```

| 字段 | 说明 |
|------|------|
| `id` | 稳定 ID，如 `rule_` + 随机 hex |
| `label` | 条件说明（给人看，不参与求值） |
| `condition` | 判断条件表达式；**可空**；导出时逻辑词规范为 `and`/`or` |
| `expression` | 公式正文（与 `fieldExpression` 同语法，含 `min()~max()`）；导出时逻辑词规范为 `and`/`or` |

章节报出值在模板里历史字段名为 **`formula`**，导出前端时会规范为 **`expression`**；二者语义相同。

### 3.2 前端默认行为（无需额外 JSON 字段）

| 场景 | 建议处理 |
|------|----------|
| `condition` 非空且 `expression` 非空 | **自动匹配**：按顺序尝试，命中则用该 `expression` |
| 多条规则中存在 `condition` 为空 | **人工选择**：现场人员点选适用哪条 `expression` |
| 仅 **一条** 规则且 `condition` 为空 | 视为 **默认公式**，直接自动计算 |
| 同时存在 `fieldExpression`（兜底） | 所有自动规则未命中时，可用兜底表达式 |

**`type` 硬性要求（与 Flutter 对齐）：** 凡带 `formulaRules` / 需自动或人工选公式的栏位，导出必须为 **`type: "computed"`**。公式选择下拉**只**在 `computed` 上渲染；挂在 `number`/`text` 上会出现「有公式但选不了」。详见 [`条件公式与前端type约定.md`](./条件公式与前端type约定.md)。

**已废弃、请勿再实现：** `requiresManualSelection`、`selectionMode`、`formulaSelectionMode`、`requiresManualFormulaSelection`、`judgmentRuleSets` 等。旧模板载入时后端/编辑器会剥离或迁移。

### 3.3 完整栏位样例（条件公式 + 判定）

**`pdf.fields[]` / 导出 `steps` 字段：**

```json
{
  "id": "output_dose_result",
  "pdfFieldId": "f19",
  "type": "computed",
  "label": "输出剂量率计算结果",
  "fieldExpression": "",
  "formulaRules": [
    {
      "id": "rule_ge_response",
      "label": "出束时间≥仪器响应时间",
      "condition": "f12>=f13",
      "expression": "f20 * f21"
    },
    {
      "id": "rule_lt_response",
      "label": "出束时间＜仪器响应时间",
      "condition": "",
      "expression": "avg(f1,f2,f3)"
    }
  ],
  "formula": "if(f12>=f13,f20*f21,\"\")",
  "dependsOn": ["f12", "f13", "f20", "f21", "f1", "f2", "f3"],
  "judgmentCriteriaByTestType": {
    "acceptance": "<=25.0",
    "status": "<=25.0"
  },
  "source": {
    "pdfFieldExpression": "",
    "judgmentCriteriaByTestType": {
      "acceptance": "<=25.0",
      "status": "<=25.0"
    }
  }
}
```

**范围输出 + 条件公式样例：**

```json
{
  "id": "dose_range",
  "pdfFieldId": "f22",
  "type": "computed",
  "formulaRules": [
    {
      "id": "rule_range",
      "label": "读数范围",
      "condition": "",
      "expression": "min(f1,f2,f3)~max(f1,f2,f3)"
    }
  ],
  "judgmentCriteriaByTestType": {
    "acceptance": "<=25.0",
    "status": "<=25.0"
  }
}
```

---

## 4. 合格判定（`judgmentCriteriaByTestType`）

### 4.1 结构

```json
"judgmentCriteriaByTestType": {
  "acceptance": "验收检测用的标准串",
  "status": "状态检测用的标准串"
}
```

与首页 **检测类型** `testType`（`enums.testType`：`acceptance` / `status`）联动。共用单元格时两行可写相同内容。

### 4.2 标准串语法

与 `verdict_from_criterion` 对齐：

| 类型 | 示例 |
|------|------|
| 上界 | `<=25.0`、`≤88.0` |
| 下界 | `>=f33`、`≥1.5` |
| 对称允差 | `0±10`、`100±15`、`f23±10.0` |
| 闭区间 | `10~20`、`10-20`（判定语境下的区间，**不同于**公式里的 `min()~max()` 范围输出） |
| 逻辑组合 | 编辑：`±5% 且 ±5000`、`<=25 或 >=10`；**导出前端**：`±5% and ±5000`、`<=25 or >=10` |

**判定标准直接写在一行字符串里**，编辑时可用 **`且`/`与`**（交集）、**`或`/`或者`**（并集），也可用 `&&`/`||`；**导出前端 JSON 时**统一规范为 **`and`/`or`**（公式 `formulaRules` 的 `condition`/`expression` 同理）。

### 4.3 动态引用 `fNN`

标准串中可写 `>=f33`：求值前将每个 `f\d+` 替换为当前映射中的数值文本；替换失败则该项 **待判定**。

### 4.4 互斥勾选（`fieldVerdict`）

```json
"fieldVerdict": {
  "kind": "exclusiveCheck",
  "passWhenCheckedPdfFieldIds": ["f124"],
  "failWhenCheckedPdfFieldIds": ["f125"]
}
```

不走数值判定引擎：仅勾选项决定合格/不合格。

---

## 5. 第五章「工作场所放射防护」章节公式

写入 **`formSchema.radiationProtectionChapter`**（键名固定），全表复用。

```json
{
  "radiationProtectionChapter": {
    "chapterKey": "site_radiation_protection",
    "meanFormula": {
      "mode": "per_row_avg",
      "description": "同点位三次测量读数取平均"
    },
    "reportValueRules": [
      {
        "id": "rule_ge_response",
        "label": "出束时间≥仪器响应时间",
        "condition": "f12>=f13",
        "expression": "{mean}*f20"
      },
      {
        "id": "rule_lt_response",
        "label": "出束时间＜仪器响应时间",
        "condition": "",
        "expression": "{mean}"
      }
    ]
  }
}
```

| 块 | 说明 |
|----|------|
| `meanFormula` | 各行测量均值：`mode: per_row_avg` 时导出前端 JSON 仅在对应均值栏位 `pdfFieldId` 下补 `fieldExpression`（如 `avg(f10,f11,f12)`），不改其它键 |
| `reportValueRules` | **章节级**报出值公式。根级始终保留完整规则（含 `{mean}`）。**`condition` 非空** → 导出时编译进各行报出值 `fieldExpression`（自动判定）；**`condition` 为空** → 不写入栏位公式，由前端工作人员**人工点选**适用哪条 |

**章节公式 vs 单元格公式（勿混用）：**

| 编辑入口 | 写入位置 | 作用范围 |
|----------|----------|----------|
| 「第五章章节公式」按钮 | `radiationProtectionChapter.reportValueRules` | 全表各行报出值**默认**公式 |
| 某一栏「公式/判定」按钮 | 该栏 `formulaRules` / `fieldExpression` | **仅该单元格**（如「本底水平」报出值） |

导出时：若某栏位**已有**单元格级公式（`fieldExpression` / `formula` / `formulaRules` / `source.pdfFieldExpression` 等），则**不再**套用章节公式覆盖该栏（均值列与报出值列均适用）。

**运行态导出 JSON 存放位置（Flutter 必读）：**

| 场景 | `radiationProtectionChapter` 位置 |
|------|-----------------------------------|
| 编辑器落盘模板（`unified_form_template/v2`） | `formSchema.radiationProtectionChapter` |
| 现场运行态导出（`frontend_form_schema/v1`） | **根级** `radiationProtectionChapter`（`formSchema` 会被剥离） |

导出前端 JSON 时（**兜底**，在 ``finalize_runtime_frontend_export`` 收尾执行）：从 `pdf.fields` 反推绑定，为均值栏位补 `avg(f*,f*,f*)`；根级 **原样保留** 模板中的 `reportValueRules`（含 `{mean}`、`formula`/`expression` 双字段，供编辑器维护）。`fieldBindings` **不导出**；各报出值栏位写入编译后的条件公式（见下表）。

**报出值：`condition` 决定自动还是人工（核心语义，勿改）：**

| `condition` | 含义 | 导出到各报出值栏位 | 前端 |
|-------------|------|-------------------|------|
| **非空** | 自动判定适用哪条公式 | 编译为栏位 `fieldExpression`（`if(...)`，`{mean}` 已换行内 f 号） | 按 §3.2 评估 `condition`，命中则用对应 `expression` |
| **为空**（多条） | 现场人工选择公式 | 写入栏位 `formulaRules`（每条 `condition` 仍为空，`expression` 中 `{mean}` 已换行内 f 号） | 按 §3.2：多条空 `condition` → UI 让人工选一条再算 |
| **为空**（仅一条） | 默认公式 | 写入该栏位 `fieldExpression` | 直接自动计算 |

---

## 6. 导出前端 JSON 完整片段样例

```json
{
  "schema": "frontend_form_schema/v1",
  "templateId": "ct-report-001",
  "enums": {
    "testType": [
      { "value": "acceptance", "label": "验收检测" },
      { "value": "status", "label": "状态检测" }
    ]
  },
  "formSchema": {
    "radiationProtectionChapter": { "...": "见 §5" },
    "steps": [
      {
        "id": "inspection",
        "title": "性能检测",
        "sections": [
          {
            "id": "site_qc_performance",
            "title": "质量控制（性能）检测项目及结果",
            "fields": [
              {
                "id": "f19",
                "type": "computed",
                "label": "计算结果",
                "formula": "f140*f141",
                "fieldExpression": "f140*f141",
                "dependsOn": ["f140", "f141"],
                "judgmentCriteriaByTestType": {
                  "acceptance": "<=25.0",
                  "status": "<=25.0"
                },
                "source": {
                  "pdfFieldId": "f19",
                  "pdfFieldExpression": "f140*f141",
                  "judgmentCriteriaByTestType": {
                    "acceptance": "<=25.0",
                    "status": "<=25.0"
                  }
                }
              },
              {
                "id": "f22",
                "type": "computed",
                "label": "读数范围",
                "formulaRules": [
                  {
                    "id": "rule_range_01",
                    "label": "范围",
                    "condition": "",
                    "expression": "min(f10,f11,f12)~max(f10,f11,f12)"
                  }
                ],
                "formulaRules": [
                  {
                    "id": "rule_range_01",
                    "label": "范围",
                    "condition": "",
                    "expression": "min(f10,f11,f12)~max(f10,f11,f12)"
                  }
                ],
                "judgmentCriteriaByTestType": {
                  "acceptance": "±5% 且 ±5000",
                  "status": "±5% 且 ±5000"
                }
              }
            ]
          }
        ]
      }
    ]
  }
}
```

---

## 7. 前端处理流程（建议）

### 7.1 载入

1. 读 `enums.testType`、当前 `testType` 选中值。
2. 遍历 `steps[].sections[].fields[]`（含 matrix 单元格）：
   - 公式：`formulaRules` → 交互；`formula` / `fieldExpression` → 自动求值。
   - 判定：`judgmentCriteriaByTestType[testType]`。
3. 防护第五章：报出值栏位上的 `formulaRules` / `fieldExpression` 与 §3.2 一致；根级 `radiationProtectionChapter.reportValueRules` 为编辑器章节配置（含 `{mean}` 占位），运行态以各栏位已编译公式为准。

### 7.2 编辑后重算

1. **拓扑排序** `dependsOn` 或自 `f\d+` 引用建图，避免环依赖。
2. **条件公式**：
   - 按数组顺序评估 `condition`（非空项）；
   - 命中则用对应 `expression`；
   - 存在空 `condition` 的多条备选 → UI 让人工选一条再算；
   - 仅一条且无 `condition` → 直接算 `expression`。
3. **表达式求值**：与 `dynamic_form_expression.evaluate_expression` 行为对齐（含 `a~b` 范围、`if()`、统计函数）。
4. **判定**：标准串中替换 `fNN` → 调 `verdict_from_measurement` 等价逻辑 → 合格/不合格/待判定。

### 7.3 与报告的关系

现场 JSON 用于填表阶段实时提示；报告生成可走 `verdict.rule` 或「标准文本 + 测量文本」。二者宜同源（同一 `judgmentCriteriaByTestType` 句式）。

---

## 8. 模板编辑器（HTMLPDF）行为摘要

路径：`htmlpdf/templates/index.html` → **公式与判定（当前栏位）**。

| 能力 | 说明 |
|------|------|
| 单行公式 | `fieldExpression`，只存等号右边 |
| 条件公式列表 | 增删规则项：`label` + `condition` + `expression`；可选兜底公式 |
| 范围 | 工具栏可插入 `~`；示例 `min(f1,f2)~max(f1,f2)` |
| 判定 | 验收/状态各一行；可插入 `且` `或` `( )` |
| 防护第五章 | 「第五章章节公式」弹窗：第 1 节说明测量均值自动 `avg`（不列逐行公式）；第 2 节编辑 `reportValueRules`（报出值） |
| PDF 选栏位 | 点击 PDF 插入 `f` 号 |

保存写入 `pdf.fields[]` + `formSchema.radiationProtectionChapter`，导出时再生成 §6 结构。

---

## 9. 可选聚合：`fieldVerdictPlan`

根级 **`fieldVerdictPlan`** 仍为**可选**批量描述（多栏位 numeric / exclusiveCheck 规则表）。**推荐优先**使用逐栏位 `judgmentCriteriaByTestType`。若同时存在，前端可择一消费。

结构见历史版本 §2.5–§3 或仓库内 JS001 示例模板；与逐栏位写法二选一即可。

---

## 10. 维护与对齐清单

| 变更类型 | 需同步 |
|----------|--------|
| 新判定句式 | `utils/verdict_from_criterion.py` + 本文 §4 |
| 新公式函数 / `~` 范围 | `utils/dynamic_form_expression.py` + 本文 §2 |
| 条件公式 JSON 形态 | `utils/conditional_field_rules.py` + 本文 §3 |
| 防护章节 | `radiation_detection_report/chapter5_field_sync.py` + 本文 §5 |
| 编辑器 UX | `htmlpdf/templates/index.html` + 本文 §8 |

**检测类型枚举**勿改：`acceptance`、`status`。

---

## 11. 版本记录

| 日期 | 说明 |
|------|------|
| 2026-05 | 增加条件公式 `formulaRules`；判定保持 `judgmentCriteriaByTestType` 单行逻辑串；公式支持 `min()~max()` 范围输出；移除 `requiresManualSelection` 等冗余标记；增加 `radiationProtectionChapter` 章节公式 |

文档版本：与 `utils/pdf_field_formulas.py`、`utils/conditional_field_rules.py`、`utils/dynamic_form_expression.py` 当前实现一致。
