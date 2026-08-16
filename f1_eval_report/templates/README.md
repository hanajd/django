# 预评价报告表 — 栏目模板库

每个**正文栏目**对应一个 Markdown 文件（结构说明）；**正式可复用文案**统一放在 `common/`（常用文本模板）。

## 目录结构

```
templates/
├── catalog.json              # 栏目索引（id / 顺序 / 路径）
├── loader.py                 # Python 加载接口
├── common/                   # ★ 常用文本模板（正式文案）
│   ├── catalog.json
│   ├── evaluation_basis/     # 主要评价依据各小节
│   ├── evaluation_objective/ # 评价目标①②③④与表2/表3
│   ├── device_principles/    # 设备工作原理
│   └── workflows/            # 设备工作流程
├── front/                    # 声明、目录
├── body/                     # 正文栏目结构说明（一栏目一文件）
├── library/                  # 兼容旧路径（同 common 下原理/流程）
├── defaults/                 # 兼容：整段默认 JSON 缓存
└── back/                     # 附件
```

## 正文栏目一览

| 序号 | id | 文件 |
|------|-----|------|
| 1 | `project_basic_info` | `body/01_project_basic_info.md` |
| 2 | `radiation_source` | `body/02_radiation_source.md` |
| 3 | `project_summary` | `body/03_project_summary.md` |
| 4 | `project_classification` | `body/04_project_classification.md` |
| 5 | `evaluation_basis` | `body/05_evaluation_basis.md` |
| 6 | `evaluation_objective` | `body/06_evaluation_objective.md` |
| 7 | `hazard_analysis` | `body/07_hazard_analysis.md` |
| 8 | `workplace_layout` | `body/08_workplace_layout.md` |
| 9 | `protection_measures` | `body/09_protection_measures.md` |
| 10 | `health_impact` | `body/10_health_impact.md` |
| 11 | `radiation_management` | `body/11_radiation_management.md` |
| 12 | `conclusion` | `body/12_conclusion.md` |
| 13 | `attachments` | `body/13_attachments.md` |

附图页细则：`back/attachments.md`。

## 调用示例

```python
from f1_eval_report.templates.loader import (
    list_body_sections,
    list_common_groups,
    load_body_template,
    load_device_principle,
    load_workflow,
    assemble_evaluation_basis_text,
    assemble_evaluation_objective,
)

for g in list_common_groups():
    print(g["id"], g["title"])

print(assemble_evaluation_basis_text()[:80])
obj = assemble_evaluation_objective(device_keys=["ct", "dr", "dsa"])
print(obj["evaluation_objective_intro"][:80])

print(load_device_principle("dsa"))
print(load_workflow("dsa_intervention"))
```

## 调整约定

1. **改常用正式文案** → 只改 `common/` 下对应文件  
2. **改栏目结构说明** → 改 `body/0x_*.md`  
3. **改某设备原理** → `common/device_principles/{设备}.md`  
4. **改某流程图** → `common/workflows/{流程}.md`  
5. **增删栏目** → 同步更新 `catalog.json` 与本 README  
6. 占位符统一用 `{字段名}`，如 `{shielding_rooms_summary}`
