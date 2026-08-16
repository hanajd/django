# 10 健康影响评价

| 元数据 | 值 |
|--------|-----|
| id | `health_impact` |
| 类型 | 固定导语 + 表11～15 + 结论 |
| 标准 | GB 18871-2002 |

## （1）正常情况下

### 1）X射线影像诊断

导语 → **表11（预期工作量）** → **表12（年受照剂量）** → 小结。

常用模板：
- `health_impact/imaging_intro.md`
- `health_impact/table11.json` / `table12.json`
- `health_impact/imaging_after.md`

### 2）介入放射学

导语 → **表13（预期工作量）** → **表14（透视防护区工作人员）** → **表15（机房外人员）** → 结论。

常用模板：
- `health_impact/interventional_intro.md`
- `health_impact/table13.json` / `table14.json` / `table15.json`
- `health_impact/interventional_after.md`

正文用五个 `<<<F1_TABLE>>>` 依次插入表11～15；写入 `normal_condition` / `normal_condition_tables`。

## （2）异常情况下（固定）

见栏目字段 `abnormal_condition`（潜在照射及预防措施）。
