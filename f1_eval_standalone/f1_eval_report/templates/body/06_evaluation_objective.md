# 6 评价目标

| 元数据 | 值 |
|--------|-----|
| id | `evaluation_objective` |
| 默认源 | `templates/common/evaluation_objective/` |

## ① 放射防护基本原则

**固定** → `01_principles.md`  
**表2 剂量限值**（接在①后）→ `table2_dose_limits.json`

## ② 工作场所辐射屏蔽防护要求

| 片段 | 类型 | 文件 |
|------|------|------|
| 引言 + a/b/c | **固定** | `02_shielding.md` |
| 综上（机房↔剂量） | **信息表自动生成，可编辑** | `02_project_summary.md` |
| WS76 透视防护区 | **信息表自动生成，可编辑** | `02_ws76.md` |
| 机房-剂量对应规则 | **可编辑配置** | `room_dose_map.json` |

生成逻辑：读取射线装置清单 → 按 `room_dose_map.json` 归入 ≤2.5μSv/h / ≤25μSv/h / WS76≤400μSv/h → 拼「综上」与 WS76 句。

## ③ 剂量管理目标值

文字固定 → `03_dose_target_intro.md`  
**表3 管理目标值**（接在③后；信息表优先）→ `table3_dose_targets.default.json`

## ④ 放射防护安全措施和放射防护管理

**固定** → `04_safety_management.md`
