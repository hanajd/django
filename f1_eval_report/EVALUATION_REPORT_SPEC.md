# 建设项目放射性职业病危害预评价报告表 — 编制规范（索引）

> 固定内容参照《250075YP 南昌大学第二附属医院（新建院区）》预评价报告表。  
> **各栏目可编辑模板已拆分至 `templates/`，本文件只作总览与索引。**

---

## 模板目录

详见 [`templates/README.md`](templates/README.md)。

| 部分 | 路径 |
|------|------|
| 声明（封面） | [`templates/front/cover.md`](templates/front/cover.md) |
| 目录 | [`templates/front/toc.md`](templates/front/toc.md) |
| 正文 12 栏目 | [`templates/body/`](templates/body/) |
| **常用文本模板** | [`templates/common/`](templates/common/) |
| 设备原理库 | [`templates/common/device_principles/`](templates/common/device_principles/) |
| 工作流程库 | [`templates/common/workflows/`](templates/common/workflows/) |
| 附件 | [`templates/back/attachments.md`](templates/back/attachments.md) |
| 栏目索引 JSON | [`templates/catalog.json`](templates/catalog.json) |
| Python 加载 | [`templates/loader.py`](templates/loader.py) |

---

## 正文栏目一览

| 序号 | 栏目 | 模板文件 |
|------|------|----------|
| 1 | 项目基础信息 | [`body/01_project_basic_info.md`](templates/body/01_project_basic_info.md) |
| 2 | 辐射源项 | [`body/02_radiation_source.md`](templates/body/02_radiation_source.md) |
| 3 | 项目概述 | [`body/03_project_summary.md`](templates/body/03_project_summary.md) |
| 4 | 建设项目分类 | [`body/04_project_classification.md`](templates/body/04_project_classification.md) |
| 5 | 主要评价依据 | [`body/05_evaluation_basis.md`](templates/body/05_evaluation_basis.md) |
| 6 | 评价目标 | [`body/06_evaluation_objective.md`](templates/body/06_evaluation_objective.md) |
| 7 | 职业病危害因素分析 | [`body/07_hazard_analysis.md`](templates/body/07_hazard_analysis.md) |
| 8 | 工作场所布局 | [`body/08_workplace_layout.md`](templates/body/08_workplace_layout.md) |
| 9 | 防护设施和措施 | [`body/09_protection_measures.md`](templates/body/09_protection_measures.md) |
| 10 | 健康影响评价 | [`body/10_health_impact.md`](templates/body/10_health_impact.md) |
| 11 | 放射防护管理 | [`body/11_radiation_management.md`](templates/body/11_radiation_management.md) |
| 12 | 结论与建议 | [`body/12_conclusion.md`](templates/body/12_conclusion.md) |
| 13 | 附件（清单） | [`body/13_attachments.md`](templates/body/13_attachments.md) |

附图页版式与页面模式见 [`templates/back/attachments.md`](templates/back/attachments.md)、[`base/attachment_format.json`](base/attachment_format.json)。

---

## 调用方式

```python
from f1_eval_report.templates import (
    list_body_sections,
    load_body_template,
    load_device_principle,
    load_workflow,
)

for s in list_body_sections():
    print(s["order"], s["title"], s["path"])

text = load_body_template("protection_measures")
principle = load_device_principle("dsa")
flow = load_workflow("dsa_intervention")
```

---

## 内容类型标记

| 标记 | 含义 |
|------|------|
| **固定** | 骨架/全文不因项目改变；可替换少量占位符 |
| **按清单展开** | 按射线装置清单组合原理/流程/限值模板 |
| **表单填入** | 来自评价信息表 / 信息收集表 / 屏蔽设计表 |
| **判定生成** | 对照标准做符合性评价与分类 |
| **待补全** | 需从 250075YP 样例粘贴或补设备原理 |

---

## 与生成代码对应

| 用途 | 位置 |
|------|------|
| F.1 整表格式 | `base/format.json` |
| F.1 栏目结构 | `sections/gbzt.py`（标准） / `sections/yp250075.py`（250075YP）；由 `sections/catalog.py` 选择 |
| 栏目文案模板 | `templates/body/*.md`（本库） |
| A3 附图 | `embed/landscape.py` |

调整栏目文案时优先改 `templates/body/`，不必改排版引擎。
