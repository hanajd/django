# 条件公式 ↔ Flutter 前端约定

本文档对齐 **后端导出 JSON** 与 **现场 Flutter 填表端** 对 `formulaRules` / `{mean}` / `type` 的约定。  
对应前端实现（参考）：

| 行为 | Flutter 位置（示意） |
|------|----------------------|
| 解析 `formulaRules` 与 `{mean}` | `form_template.dart` |
| 有 `condition` 时自动选首个命中公式 | `form_controller.dart`（自动匹配） |
| 多条空 `condition` 时手动选择 | `form_controller.dart`（人工选择） |
| 下拉用规则 `label` 展示 | `computed_field_widget.dart` |
| **公式选择下拉仅渲染于 `type: computed`** | `dynamic_field_widget.dart` |

更完整的表达式语法见 [`json公式说明.md`](./json公式说明.md)。  
曲线拟合（`fitBinding` + `fit(y=...)` 条件公式，主格与 R² 均为 `computed`）见 [`拟合公式前后端对接说明.md`](./拟合公式前后端对接说明.md)。

---

## 1. 硬性约定：`type` 与公式 UI

| `type` | 前端表现 | 何时使用 |
|--------|----------|----------|
| **`computed`** | 走计算字段组件；**可显示公式选择下拉**；按 `formulaRules` / `formula` 求值 | 凡带公式、需自动算或需人工选公式的栏位（含报出值） |
| `number` / `text` 等 | 普通输入；**不渲染公式选择下拉** | 纯手填数值/文本 |

**关键限制（当前 Flutter）：**

- 即使栏位挂了 `formulaRules`，若 `type` 不是 `computed`，界面**不会出现**公式选择框。
- 计算逻辑仍可能把该格当成「有公式、自动/锁定」，于是出现：**后端认为可选手动公式，前端却选不了**。

**后端导出必须保证：**

> 只要需要「条件自动匹配」或「多条空 condition 人工点选」，导出字段的 **`type` 必须为 `"computed"`**。

---

## 2. `formulaRules` 语义（前后端一致）

每条规则：

```json
{
  "id": "rule_xxx",
  "label": "出束时间≥仪器响应时间",
  "condition": "f12>=f13",
  "expression": "{mean}*f208"
}
```

| 字段 | 谁用 | 说明 |
|------|------|------|
| `id` | 两端 | 稳定 ID |
| `label` | **仅 UI** | 下拉选项文案；**不参与求值** |
| `condition` | 求值 | 可空；非空则按序求布尔，命中则用该条 `expression` |
| `expression` / `formula` | 求值 | 公式正文；导出时二者可并存，语义相同 |

### 2.1 选择策略（与 Flutter 一致）

| 场景 | 前端行为 | 后端导出建议 |
|------|----------|--------------|
| ≥1 条 `condition` 非空 + `expression` 非空 | 按顺序自动匹配，用首个条件为真的公式 | 可同时写编译后的 `fieldExpression`/`formula`（`if(...)`）作兜底；**`type: computed`** |
| **多条**规则且 **全部** `condition` 为空 | **必须人工选择**（下拉用 `label`） | 保留完整 `formulaRules`；**不要**只留一条 `fieldExpression` 顶替；**`type: computed`** |
| **仅一条**且 `condition` 为空 | 视为默认公式，直接自动算 | 可写 `fieldExpression`/`formula`；`formulaRules` 可省略或保留一条；**`type: computed`** |
| 无 `formulaRules`，仅有 `fieldExpression`/`formula` | 直接自动算 | **`type: computed`** |

> `label` 里的中文（如「出束时间≥…」）**不等于** `condition`。  
> 若 `condition` 为空，前端不会做自动条件判断，只会在「多条空 condition」时弹出选择。

### 2.2 `{mean}` / `{mean2}` / `{report}`

| 阶段 | 形态 |
|------|------|
| 章节配置 `radiationProtectionChapter.reportValueRules` | 可保留占位符 `{mean}`、`{mean2}`、`{report}`、`{report2}` |
| 编辑器点「完成」写入各行报出值（仅内存） | 换成该行均值/报出值 `pdfFieldId`（如 `f223`） |
| 导出给 Flutter 的栏位 `formulaRules[].expression` | **不应再含** `{mean}`；应为行内 `f` 号 |
| Flutter 解析 | 仍兼容模板侧占位符（若偶发残留） |

章节「完成」只改编辑器内存；**磁盘 JSON 仅在「保存坐标模板 JSON」时更新**。

---

## 3. 导出强制：`formulaRules` → `type: computed`

历史上 `submitBucket == "testResult"` 会把检测结果格先打成 `number`，导致带 `formulaRules` 的报出值无法出现公式下拉。

**当前规则（已落地）：**

1. `apply_formula_rules_to_field_dict`：只要存在 `formulaRules`，**一律** `type = "computed"`（不再用 `type or computed`）。
2. `frontend_schema_rule_engine` 拼装栏位末尾：若有 `formulaRules` / `fieldExpression` / `fitBinding`，**覆盖** `testResult→number`，再 enrich 条件公式。

因此凡条件公式栏位（含报出值人工选公式），导出给 Flutter 时 **`type` 必为 `"computed"`**。

---

## 4. 推荐导出形态（人工选公式的报出值）

```json
{
  "id": "f224",
  "pdfFieldId": "f224",
  "type": "computed",
  "label": "工作人员操作位K_r1_报出值D",
  "fieldExpression": "",
  "formula": "",
  "formulaRules": [
    {
      "id": "rule_ge",
      "label": "出束时间>=测量仪器相应时间",
      "condition": "",
      "expression": "f223 * f208"
    },
    {
      "id": "rule_lt",
      "label": "出束时间<测量仪器相应时间",
      "condition": "",
      "expression": "(f223-avg(f644,f645,f646,f647,f648,f649,f650,f651,f652,f653))*f209"
    }
  ],
  "chapterMeanPdfFieldId": "f223",
  "source": {
    "pdfFieldId": "f224",
    "submitBucket": "testResult"
  }
}
```

要点：

- **`type` 必须是 `computed`**（即使 `submitBucket` 仍是 `testResult`）。
- 多条空 `condition`：保留 `formulaRules`，勿强行合成单条 `fieldExpression` 导致无法选择。
- 若改为真正自动判定：把比较式写入 **`condition`**（如 `f12>=f13`），并可编译 `if(...)` 到 `formula`；仍须 **`type: computed`**。

仅一条默认公式时：

```json
{
  "type": "computed",
  "fieldExpression": "f223 * f209",
  "formula": "f223 * f209"
}
```

---

## 5. 章节公式 vs 单元格公式（编辑器）

| 入口 | 写入 | 何时落到各行栏位 |
|------|------|------------------|
| 「第五章章节公式」 | `radiationProtectionChapter.reportValueRules`（可含 `{mean}`） | 点 **完成** → 写进编辑器内存各报出值格；点 **保存坐标模板 JSON** → 落盘 |
| 单格「公式/判定」 | 该格 `formulaRules` / `fieldExpression`，并标 `fieldFormulaUserOverride` | 保存 JSON 时保留；**不再**被章节规则覆盖 |

保存 JSON 时：栏位已有公式则保留；仅空白格可做章节公式兜底。

---

## 6. 联调检查清单

导出前端 JSON 后，对「应选手动公式」的报出值确认：

1. [ ] `type === "computed"`（不是 `number` / `text`）
2. [ ] `formulaRules.length >= 2` 且各条 `condition === ""`（若走人工选择）
3. [ ] 每条有可读的 `label`（下拉文案）
4. [ ] `expression` 已是行内 `f` 号，无未替换的 `{mean}`
5. [ ] Flutter 该格出现公式选择下拉，且选项文案 = `label`

若 1 失败而 2–4 成功 → 即「公式在、下拉没有」的典型根因。

---

## 7. 与测试覆盖的关系

相关自动化测试若只覆盖 `type: computed` 的公式栏位，**测不出**「`formulaRules` + `type: number`」的 UI 缺口。  
建议至少补一条：多条空 `condition` 的 `formulaRules` 导出后 **`type` 必须为 `computed`**。

---

## 8. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-07 | 初版：对齐 Flutter 下拉仅 `computed` 的限制；标明 `testResult→number` 覆盖导致报出值无法选手动公式 |
