# 常用文本模板（common）

本目录集中管理预评价报告表中**可复用的正式文案模板**，与 `body/`（栏目结构说明）分离。

## 分组

| 分组 | 路径 | 说明 |
|------|------|------|
| 主要评价依据 | `evaluation_basis/` | 法律/法规/规章/规范性文件、主要标准、基础资料、参考资料 |
| 评价目标 | `evaluation_objective/` | ①基本原则 ②三类屏蔽概述 ③剂量目标文字+表2/表3 ④安全管理 |
| 设备工作原理 | `device_principles/` | 按装置选用 |
| 设备工作流程 | `workflows/` | 按装置/工艺选用 |

## 约定

1. **改常用文案** → 只改本目录对应文件（勿再往 `body/*.md` 粘贴大段正式条文）。
2. **拼装规则** → 各分组下的 `assemble.json`。
3. **占位符** → `{shielding_rooms_summary}`、`{fluoroscopy_rooms_summary}` 等由组稿程序替换。
4. 兼容：`library/`、`defaults/evaluation_basis.json` 仍可读；优先以本目录为准。

## 调用

```python
from f1_eval_report.templates.loader import (
    list_common_groups,
    load_common_text,
    assemble_evaluation_basis_text,
    assemble_evaluation_objective,
)

print(assemble_evaluation_basis_text()[:80])
```
