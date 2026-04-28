# 前端 submit JSON 自动填写说明（JS-117 实战版）

本文档基于以下真实文件进行对齐说明：

- 前端模板：`media/file_library/templates/42183db9ad2041d3b1b5f802503938fd_template_frontend.json`
- 后端模板：`media/file_library/templates/23d12066c9bc4fd4ac583ca70c407ec2_JS-117.json`
- 历史提交样例：`media/file_library/inspection_submits/192f31e01b994eea93ece777be9f2a0e_02_submit_20260423091351.json`

---

## 1. 三类 JSON 的职责

### 1.1 前端模板 JSON（渲染）

- 负责前端动态表单展示（`steps -> sections -> fields`）。
- `fields[].source.key` 是前后端字段语义锚点。

### 1.2 后端模板 JSON（PDF 回填）

- 负责最终 PDF 字段写入。
- 关键字段在 `pdf.fields[]`：`id/placeholder/title/pdfFieldId/fieldType`。

### 1.3 submit JSON（业务数据）

- 前端提交到 `/submit` 的业务结果。
- 后端把 submit JSON 转换成“回填映射”，再写入后端模板字段。

---

## 2. 自动填写链路与优先级

主要代码：

- `apps/api/inspection_submit_placeholder_maps.py`
- `apps/api/inspection_pdf_service.py`

映射来源两层：

1. 规则映射：`SUBMIT_PLACEHOLDER_MAPS["default"]`
2. 服务端派生映射：`_build_submit_derived_value_mapping(...)`

同名键冲突时：

- **派生映射优先级更高**。

---

## 3. 当前已生效的关键业务规则

## 3.1 `commissionNo` 和 `testnumber` 的来源

这两个字段不是“前端提交值最终生效”，而是后端上下文注入：

- `commissionNo` <- 项目上下文（projectId 风格）
- `testnumber` <- 任务上下文（taskNo 风格）

并同步写入同义键：

- 项目：`projectId`、`entrustNo`、`委托编号`、`commissionNo`
- 任务：`taskNo`、`inspectedNo`、`受检编号`、`testnumber`

## 3.2 年月日规则

`year/month/day` 默认取 submit 根字段 `updatedAt`：

- 例：`2026-04-20T17:08:17.696570`
- `month` = `4`
- `day` = `20`
- `year` 短年规则：
  - `2026 -> 6`
  - 非 `202x` 年份取后两位

---

## 4. JS-117 典型字段对齐示例

以下为“前端字段语义 -> 后端回填语义”的典型对应：

- `commissionNo`（前端） -> `commissionNo/委托编号`（后端，最终由上下文覆盖）
- `testDate`（前端） -> `检测日期_年/月/日`（后端拆分）
- `deviceName`（前端） -> `设备名称`（后端模板）
- `environmentTemperature`（前端） -> `temperature -> 环境温度`
- `humidity`（前端） -> `RH -> 湿度`

---

## 5. 前端 submit 输出规范（推荐，多模板通用）

## 5.1 顶层结构固定

建议所有模板统一提交以下顶层结构：

- `taskNo` string
- `reportType` string（当前约束：`xray_fluoroscopy`）
- `createdAt` ISO datetime
- `updatedAt` ISO datetime
- `reportInfo` object
- `hospitalInfo` object
- `equipmentInfo` object
- `instruments` array
- `testResult` object
- `signatures` object|null
- `conclusion` object|null

## 5.2 类型规则

- `reportInfo/hospitalInfo/equipmentInfo/testResult/conclusion`：建议传 `{}`，后端兼容 `null` 但不推荐长期依赖。
- `instruments`：始终传数组（可空 `[]`）。
- `updatedAt`：必须可解析为 ISO 时间。
- `signatures`：若传值，必须是 `data:image/png;base64,...`。

## 5.3 命名与锚点规则

- 前端字段 `id`：英文小驼峰，保持稳定。
- 回填语义锚点：以 `source.key` 为主。
- 分组命名建议：`前缀_后缀`（下划线），利于规则引擎稳定分组。
- 日期建议：前端保留一个 `testDate`，由后端拆分到年/月/日。

---

## 6. 后端所需前端输出示例（JS-117 规范版）

```json
{
  "taskNo": "XF-20260420-02",
  "reportType": "xray_fluoroscopy",
  "createdAt": "2026-04-16T14:33:22.005574",
  "updatedAt": "2026-04-20T17:08:17.696570",
  "reportInfo": {
    "reportNo": "JS-117-2026-001",
    "reportDate": null,
    "commissionNo": null,
    "projectName": "自动生成任务：JS-117",
    "inspectionType": "",
    "inspectionCategory": "",
    "inspectionMethod": "",
    "deviceCount": 0,
    "inspectors": ""
  },
  "hospitalInfo": {
    "name": "南昌市第五医院",
    "address": "南昌市青山湖区",
    "contactPerson": "",
    "contactPhone": ""
  },
  "equipmentInfo": {
    "deviceName": "具有CBCT功能的C形臂血管造影机",
    "model": "Brilliance iCT",
    "manufacturer": "Philips Medical Systems",
    "ratedParams": "380-480V3-, 50/60HZ",
    "serialNo": "236339",
    "location": "门诊负一楼",
    "testStandard": "",
    "evalStandard": ""
  },
  "instruments": [],
  "testResult": {
    "isDsaDevice": true,
    "testDate": null,
    "temperature": 32.0,
    "humidity": 40.0,
    "kermaTypical": {
      "controlMode": "自动控制",
      "kv": 8.2,
      "ma": 4.4,
      "size": "200*200",
      "nk": 0.991,
      "kValue": 321.8,
      "calcValue": "19.13",
      "reportValue": "19.13"
    }
  },
  "signatures": {
    "author": "data:image/png;base64,...",
    "reviewer": "data:image/png;base64,...",
    "approver": "data:image/png;base64,...",
    "signDate": "2026-04-20T11:17:37.659114"
  },
  "conclusion": {
    "allPassed": false,
    "conclusionText": "..."
  }
}
```

---

## 7. 历史样例兼容结论

你给的历史样例整体可用，重点保持：

- `reportType = xray_fluoroscopy`
- `updatedAt` 必须存在
- 对象字段建议逐步从 `null` 迁移到 `{}`（后端虽兼容，但规范更清晰）

---

## 8. JS-117 当前模板可优化点

当前前端模板里出现了单独“检测日期”步骤且字段语义残缺（如 `id: month`, `label: 检测日期_`）。建议：

- 前端统一使用 `testDate`（date 类型）
- 后端统一拆分回填到 `检测日期_年/月/日`
- 避免日期拆分残片字段进入前端模板

---

## 9. 联调排错清单

- 委托编号/受检编号不对：确认是否误以为取前端提交值（实际由后端上下文注入）。
- 年月日异常：检查 `updatedAt` 格式与时区。
- 字段未填：检查 `source.key` 与后端模板 `id/placeholder/title` 是否可匹配。
- 422 参数错误：检查对象字段结构与签名格式。
- 报告导出空值：检查报告任务的来源现场记录任务是否已配置且有可用 JSON。

---

## 10. 新版 Binding-Driven 协议（已支持）

为支持“新增模板 0 代码修改”，后端已支持：

- submit 顶层新增可选 `templateId`
- submit 顶层新增可选 `dynamicData`（对象）
- PDF 回填时优先按 `pdfFieldId -> dynamicData[pdfFieldId]` 直连取值
- 旧版规则映射（`SUBMIT_PLACEHOLDER_MAPS`）与派生映射继续兼容

推荐前端约定：

- 渲染字段时读取 `field.source.pdfFieldId`（或 `field.source.bindKey`）
- submit 组装时写入：
  - `payload.dynamicData[pdfFieldId] = fieldValue`
- 对系统级上下文字段（如 `commissionNo/testnumber`）仍由后端覆盖注入