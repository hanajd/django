# 统一模板 JSON：冗余分析与瘦身说明

> **状态：仅分析，暂不落地改 JSON / 改代码。**  
> 基准样本：`media/file_library/templates/001-验收检测/01-CT/site/jxfs-js009-v30-xct202641-1/current/271f09fbc8454b529f97e9a6a0e50523.json`（CT 现场记录统一模板，约 947KB / 紧凑约 471KB）。  
> 关联文档：[json公式说明.md](./json公式说明.md)、[FRONTEND_FORM_SCHEMA.md](./FRONTEND_FORM_SCHEMA.md)。

---

## 1. 结论摘要

1. **`unit` 字段已废弃并已从该样本清除**（剩余 0 处）。单位应写进公式/文案或由前端展示约定，不再作为栏位元数据启用。
2. 体积大头是 **`formSchema`（约 317KB）** 与 **`pdf.fields`（约 153KB）** 两套并存，多数键**互补而非纯拷贝**；不能在不改管线的前提下整段删除 `formSchema`。
3. 真正的「同值重复」主要集中在：`source` 镜像、`formula`≡`fieldExpression`、判定标准在 pdf / formSchema 双侧各写一份。
4. 后续若瘦身，应分 **仅改 JSON（低风险小收益）** 与 **改导出/落盘管线（大收益）** 两档推进；**当前阶段只保留本说明，不做修改。**

---

## 2. 样本体量拆解

| 区域 | 约占比 / 体量 | 说明 |
|------|----------------|------|
| `formSchema` | ~317KB（紧凑口径下约占全文件 2/3） | 前端表单树：`steps` / `radiationProtectionChapter` / `constants` 等 |
| └ `formSchema.steps` | ~297KB | 几乎全在「检测原始记录」一步；防护检测 section 最大 |
| └ `formSchema.radiationProtectionChapter` | ~20KB | 第五章扩展与行规则 |
| `pdf.fields` | ~153KB | 编辑器真源：坐标、公式、判定、分区标题 |
| 其余根字段 | 可忽略 | `templateId` / `schema` / `pdfUrl` 等 |

根结构（统一模板）：

```text
{
  schema, templateId, templateName, version, ...
  pdf: { source_pdf, fields[] },
  formSchema: { constants, enums, steps, radiationProtectionChapter }
}
```

---

## 3. 两套数据的职责（为何不能简单合并删）

| | `pdf.fields[]` | `formSchema.steps` 栏位 |
|--|----------------|-------------------------|
| **角色** | 编辑器落盘真源、PDF 回填几何 | 现场/前端填表、submit 路径、控件类型 |
| **常见独有键** | `rect`、`templateSectionKey`、`templateSectionTitle`、`title`、`fieldFormulaUserOverride`、`fieldType` | `pdfAnchor`、`hierarchyKey`、`submitPath`、`submitBucket`、`type`、`label`、`width`、`precision`、`formula`、`source` |
| **常见共有键** | `pdfFieldId`、`fieldExpression`、判定相关 | 同左（导出时从 pdf 合并写入） |

文档约定（见 `json公式说明.md`）：

- 公式与判定的**存储真源**在 **`pdf.fields`**。
- 导出前端 schema 时，会把 PDF 侧公式合并到 **`formula` / `fieldExpression` / `source.pdfFieldExpression`**。
- 因此库内模板同时带 `pdf` + `formSchema`，是「编辑器成品 + 已导出表单」的双份快照，不是无意义的双写全部键。

**若删除整块 `formSchema`（样本可省约 317KB）：** 必须保证打开模板 / 下发给前端时一定能从 `pdf.fields` **重新导出**；当前链路多处直接读模板内嵌 `formSchema`，**不改代码不可删**。

---

## 4. 已处理项：`unit`

- 历史：栏位上存在 `"unit": "mA"` / `"kV"` / `"mGy/min"` 等元数据。
- 决策：**以后不启用**；样本中已全部去掉。
- 单位表达方式：写入 `fieldExpression` / 展示文案 / 前端固定后缀，不再依赖 JSON `unit` 键。
- 其他库模板若仍残留 `unit`，可按同一策略批量清除（需单独排期，本次未改）。

---

## 5. 冗余分类与可否去掉

### 5.1 表面像重复、不能整段删

| 项 | 现象 | 原因 | 建议 |
|----|------|------|------|
| `formSchema` ↔ `pdf.fields` | 同一 `pdfFieldId` 两侧都有对象 | 职责互补（见 §3） | 保留；长期可改为「只存 pdf，用时导出」 |
| `pdfAnchor` ↔ `rect` | 672 栏双侧都有几何 | 格式不同：锚点多为 `{page, rect:[x0,y0,x1,y1], anchorType}`，pdf 侧多为 `[page,x,y,w,h]` | 保留；合并需统一几何模型与读写方 |
| 同名 `label` | 多个栏位文案相同 | 不同控件，不是同一对象拷贝 | 不删；若需去重应改命名策略 |
| `templateSectionTitle` | 仅约 6 种标题挂在 672 个 field 上 | 反范式展开，便于单栏自描述 | 可改为 section 级存一次再引用（需改模型） |

### 5.2 同值拷贝（可排期瘦身）

| 项 | 样本结论 | 约收益 | 不改代码时风险 |
|----|----------|--------|----------------|
| **`source` 与父字段同值键** | 672 个栏位有 `source`；其中 **488** 个与父对象完全同值；同值键去掉约可省 **22KB**；整段去掉 `source` 约 **46KB** | 小 | 代码/前端仍可能读 `source.pdfFieldExpression`、`source.judgment*`（见公式文档） |
| **`source.pdfFieldExpression`** | 与 `fieldExpression`、`formula`、`pdf.fields[].fieldExpression` **184/184 全等** | 含在上项 | 文档仍写前端可消费该键 |
| **`formula` ≡ `fieldExpression`** | 184 处字符串全等，约 **11KB** | 小 | 前端/导出约定显式使用 `formula` |
| **判定三元组双侧镜像** | `judgmentCriteriaByTestType` / `judgmentCriteriaManual` / `judgmentRuleSets` 在 pdf 与 formSchema **完全一致**（约 18/18/17）；去掉 pdf 侧镜像约 **6KB** | 小 | 真源应在 `pdf.fields`；若只留一侧，另一侧须由导出再生 |
| **空壳噪声** | 防护章等处大量空 `condition`、`logicExpression`、空读数键 | 很小 | 低；清理不影响语义 |

### 5.3 假性重复（不要当冗余删）

- `fieldFormulaUserOverride`（样本约 49 处）与 `fieldExpression` **不相等**：用户覆盖，必须保留。
- `hierarchyKey` 与 `submitPath` **全部不同**：前者偏展示层级文案，后者偏提交路径。
- 根级空字段（如个别 `reportType`/`enums` 空）体量可忽略。

---

## 6. 推荐路线（暂不执行）

### 阶段 A — 仅数据清理（可选、收益小）

在确认读写方兼容后，可对库模板 JSON：

1. 继续保证无 `unit`。
2. 删除 `source` 内与父字段同值的键；进一步再评估是否整段删除 `source`。
3. 清理防护章空字符串 / 空对象噪声。

**本阶段不改动代码、也不自动批量改库文件（按产品确认后再做）。**

### 阶段 B — 管线级瘦身（收益大，需改代码）

1. **模板落盘只保留 `pdf.fields`（+ 少量元数据）**；`formSchema.steps` 在「打开编辑器 / 下发前端 / 导出运行态」时由现有 `frontend_schema_rule_engine` 等链路生成。  
   - 预期：单文件体积接近「去掉 formSchema」量级（样本约省 **2/3**）。
2. **判定与公式单源**：只写 `pdf.fields`；formSchema 侧一律导出生成，禁止手改双侧。
3. **分区标题结构化**：`templateSectionTitle` 提到 section，field 只留 `templateSectionKey`。
4. **几何单源**：统一 `rect` 或 `pdfAnchor` 一种，另一侧派生。

### 阶段 C — 明确废弃键

| 键 | 状态 |
|----|------|
| `unit` | **已废弃，不再启用** |
| `fieldExpressionRules` | 文档已标废弃（旧模板只读兜底） |
| `source` 内与父级完全同值的镜像 | **候选废弃**（待确认无消费者后） |

---

## 7. 自检脚本口径（复现分析时）

对目标 JSON 可统计：

- 顶层键体量：`pdf` / `formSchema` / `formSchema.steps` 等 `json.dumps` 字节数。
- 按 `pdfFieldId` 对齐后：共有键是否同值、独有键集合。
- `source`：与父字段同值数、完全冗余 `source` 个数、`pdfFieldExpression` 与三侧公式是否全等。
- `formula` 与 `fieldExpression` 是否全等。
- 判定三键在 pdf / formSchema 是否全等。
- 全树 `unit` 剩余次数（目标为 0）。

---

## 8. 范围与非目标

- **范围内**：统一模板（`unified_form_template` 一类）库内 `current/*.json` 的体积与键重复。
- **非目标**：现场任务运行态 payload、坐标-only 模板、仪器绑定策略、主表格式编辑等（另文）。
- **当前执行策略**：**只产出本文档，不修改样本或其他模板 JSON，不修改导出代码。**

---

## 9. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-08-05 | 初稿：基于 CT 模板 `jxfs-js009-v30-xct202641-1` 样本分析；确认 `unit` 已清除；瘦身项列为待办，暂不落地。 |
