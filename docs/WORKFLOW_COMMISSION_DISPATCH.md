# 委托 → 检测工单派发流程

## 业务链路

```text
委托单位（医院信息树）
  → 科室下登记受检设备（CommissionOrgEquipment）
  → 每台设备配置设备类型 + 检测类型对应的报告模板
  → 创建项目（委托编号 YY####）
  → 项目「委托情况」勾选设备（按报告模板入项）
  → 同步任务模板 + 自动向主要负责人派发 LibraryTaskAssignment
  → App：项目列表 → 任务列表（taskNo）→ 填现场记录 → 提交 → 回填报告 PDF
```

## 任务模板库目录约定

上传文件夹时自动识别（仅 `.pdf` / `.json`，其它忽略；同名配对）：

```text
{检测类型}/                    ← LibraryTaskFolder 顶层（如 验收检测）
  {设备类型}/                  ← 子分类（如 CT）
    报告A.pdf + 报告A.json     ← 报告模板 LibraryTask(output=report)
    防护报告.pdf + …           ← 可多份报告（验收/防护等）
    空白原始记录/
      记录1.pdf + 记录1.json   ← 现场记录模板
      记录2.pdf + …
```

规则：**同一设备类型目录下，所有现场记录模板会绑定到该目录下每一份报告模板**（`report_source_tasks`）。

## App 与后台字段对应

| 概念 | 后台 | App API |
|------|------|---------|
| 委托编号 | `LibraryProject.code` | `projectId` |
| 工单路由 | 项目内任务序号 | `taskNo`（01、02…，勿与 projectId 混淆） |
| 受检编号 | 报告任务顺序 | `inspectedNo`（同报告下现场记录相同） |

## 设备多检测类型

`CommissionOrgEquipment.report_task_bindings` 示例：

```json
[
  {"inspection_type": "验收检测", "report_task_id": 12},
  {"inspection_type": "状态检测", "report_task_id": 18}
]
```

未匹配时使用默认 `report_task`。加入项目时可按检测类型解析模板链（报告 + 下属现场记录）。

## 维护入口

- 任务模板库：页顶「编辑」→ **批量导入模板文件夹**；任务/分类/模板文件重命名
- 项目工作台 · 委托情况：绑定/解绑设备
- 项目工作台 · 分配：向检测人员派发任务（绑定设备后若已设主要负责人会自动同步）
