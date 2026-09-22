# App 端任务标识（taskKey）改造对接规范

> **更新日期：** 2026-09-04  
> **适用版本：** Android / iOS / 平板现场检测 App  
> **接口版本：** API v2 (`/api/v2/...`)

---

## 一、改造背景与解决的痛点

### 1. 过去的问题（动态 `taskNo` 导致的身份串位）
在旧设计中，服务端根据项目内任务模板的排序动态赋予 `taskNo`（例如 `01`、`02`、`25`）。
* **隐患：** 一旦后台在项目执行过程中新增设备、调整设备顺序，原有任务序号会发生位移（例如原来的 DR 从 `25` 变成 `26`，原来的 CT 从 `28` 变成 `25`）。
* **后果：** 导致 App 提交的数据、现场照片、案件和报告在服务端产生“错位与串案”（例如把 DR 的报告挂到了 CT 设备下）。

### 2. 本次改造方案（稳定任务键 `taskKey`）
服务端引入了**不可变的稳定任务键（`taskKey`）**，规则绑定：
`P{项目ID}-E{项目设备关联ID}-S{现场任务ID}`
* 例如：`P21-E82-S50`（表示项目 21 下，设备 82，执行的现场检测任务 50）。
* 无论后台如何增删其他设备或调整任务排列顺序，该设备的任务身份永久固定不变。

---

## 二、关键字段定义（必读对照表）

从 `GET /api/v2/inspections/projects/{projectId}/tasks` 获取任务列表时，返回的核心标识字段如下：

| 字段名 | 类型 | 示例值 | 含义 | App 端使用要求 |
|:---|:---|:---|:---|:---|
| **`taskKey`** | String | `"P21-E82-S50"` | 任务全局不可变唯一标识 | **【核心键】** 推荐作为 App 端任务唯一主键 |
| **`taskNo`** | String | `"P21-E82-S50"` | 兼容任务编号（已赋值为 `taskKey`） | **【API 路由】** 所有 URL 参数及接口请求入参使用该值 |
| **`displayTaskNo`** | String | `"01"`, `"25"` | 界面展示序号 | **【UI 展示】** 仅供 App 界面列表中显示序号徽章，**不能**作为主键 |
| **`projectEquipmentId`** | Int / null | `82` | 关联的项目设备 ID | 设备信息识别 |
| **`inspectedNo`** | String | `"01"` | 报告受检编号 | 用于按受检台次归类展示 |

---

## 三、App 端具体改造点（检查清单）

App 端开发人员请逐一核对以下 5 项：

### 1. 移除针对 `taskNo` 的两位纯数字校验（必须）
* **现状检查：** 检查 App 代码中是否有类似 `^\d{2}$`、`Integer.parseInt(taskNo)` 或把 `taskNo` 限制为 2 位纯数字的正则校验/数据模型定义。
* **修改：** `taskNo` 与 `taskKey` 均按普通 `String`（长度建议支持到 128 位）处理，允许包含字母和横杠（如 `P21-E82-S50`）。

### 2. URL 接口路径参数拼接（必须）
所有向服务端发起任务级请求的 URL，必须使用接口返回的 `taskNo`（即 `taskKey` 字符串）：
* 统一模板与表单拉取：
  `GET /api/v2/inspections/projects/{projectId}/tasks/{taskNo}/export-frontend-json`
  （或直接使用任务列表返回的完整 `frontendTemplateDownloadUrl`）
* 开始检测：
  `POST /api/v2/inspections/projects/{projectId}/tasks/{taskNo}/start`
* 保存草稿：
  `POST /api/v2/inspections/projects/{projectId}/tasks/{taskNo}/draft`
* 提交检测：
  `POST /api/v2/inspections/projects/{projectId}/tasks/{taskNo}/submit`
* 签名上传：
  `POST /api/v2/inspections/projects/{projectId}/tasks/{taskNo}/signatures/{character}/upload`
* 照片上传：
  `POST /api/v2/inspections/projects/{projectId}/tasks/{taskNo}/files/upload`

### 3. 表单提交 Payload 请求体携带（推荐）
在保存草稿（`draft`）与正式提交（`submit`）的请求体 JSON 中：
```json
{
  "taskNo": "P21-E82-S50",
  "taskKey": "P21-E82-S50",
  "projectId": "260312",
  "reportType": "ct_qc",
  "reportInfo": { ... },
  "hospitalInfo": { ... },
  "equipmentInfo": { ... },
  "testResult": { ... },
  "conclusion": { ... }
}
```
* **要求：** `taskNo` 填入获取到的任务标识；同时**建议显式携带 `taskKey` 字段**。

### 4. App 本地离线缓存与存储 Key（非常重要）
如果 App 本地使用 SQLite、Room、Realm、MMKV 或 SharedPreferences 缓存本地检测草稿、临时照片：
* **禁止：** 严禁以单纯的 `01`、`02` 或 `taskNo` 纯数字作为主键。如果一个项目内有两台 CT（CT 1 和 CT 2），纯序号容易导致本地草稿互相覆盖！
* **正确做法：** 本地缓存 Key 统一格式：
  ```kotlin
  val cacheKey = "${projectId}_${taskKey}" 
  // 示例: "260312_P21-E82-S50"
  ```

### 5. UI 界面展示优化
* 任务列表界面的“序号圆圈”或“项目第几项”：展示 **`displayTaskNo`**（如 `01`、`02`、`03`）。
* 设备名称与任务名称：展示 `deviceType`（如 `CT`、`DR`）、`inspectionType`（`状态检测`、`验收检测`）及 `taskName`。
* 避免将底层的 `P21-E82-S50` 作为主标题直接呈现在界面上。

---

## 四、接口数据样例参考

### 1. 任务列表接口
`GET /api/v2/inspections/projects/260312/tasks`

```json
{
  "success": true,
  "message": "获取成功",
  "data": {
    "projectId": "260312",
    "count": 2,
    "list": [
      {
        "assignmentId": 12,
        "projectEquipmentId": 82,
        "taskNo": "P21-E82-S50",
        "taskKey": "P21-E82-S50",
        "displayTaskNo": "25",
        "inspectedNo": "25",
        "taskId": 50,
        "taskCode": "jxfs-js009-v30-xct202641-2",
        "taskName": "X射线计算机体层摄影装置（CT）质量控制检测原始记录",
        "outputTarget": "site_record",
        "deviceType": "CT",
        "inspectionType": "状态检测",
        "commissionOrganization": "测试医院 · 放射科",
        "frontendTemplateDownloadUrl": "http://192.168.1.100:11223/api/v2/inspections/projects/260312/tasks/P21-E82-S50/export-frontend-json",
        "templatePdfDownloadUrl": "http://192.168.1.100:11223/api/v2/library/files/244/download/"
      },
      {
        "assignmentId": 13,
        "projectEquipmentId": 73,
        "taskNo": "P21-E73-S25",
        "taskKey": "P21-E73-S25",
        "displayTaskNo": "26",
        "inspectedNo": "26",
        "taskId": 25,
        "taskCode": "jxfs-js010-v30-xdr202641",
        "taskName": "数字X射线摄影（DR）设备质量控制检测原始记录",
        "outputTarget": "site_record",
        "deviceType": "DR",
        "inspectionType": "验收检测",
        "commissionOrganization": "测试医院 · 放射科",
        "frontendTemplateDownloadUrl": "http://192.168.1.100:11223/api/v2/inspections/projects/260312/tasks/P21-E73-S25/export-frontend-json",
        "templatePdfDownloadUrl": "http://192.168.1.100:11223/api/v2/library/files/246/download/"
      }
    ]
  }
}
```

### 2. 提交检测数据接口
`POST /api/v2/inspections/projects/260312/tasks/P21-E82-S50/submit`

```json
{
  "taskNo": "P21-E82-S50",
  "taskKey": "P21-E82-S50",
  "projectId": "260312",
  "reportType": "ct_qc",
  "createdAt": "2026-09-04T10:00:00+08:00",
  "updatedAt": "2026-09-04T10:30:00+08:00",
  "reportInfo": {
    "detection_type": "状态检测"
  },
  "hospitalInfo": {
    "hospital_name": "测试医院"
  },
  "equipmentInfo": {
    "device_name": "CT机",
    "model": "Optima CT660"
  },
  "testResult": {
    "table": []
  },
  "conclusion": {
    "qualified": true
  }
}
```

---

## 五、联调与兼容保障

1. **服务端向后兼容：**
   * 服务端在返回字段中依然保留了 `taskNo` 字段，且原有的旧项目如果未配置设备卡片，依然平滑回退，不会出现空指针异常。
2. **联调验证指标：**
   * 同一个项目下同时勾选多台同类或不同类设备（如 2 台 CT、1 台 DR 验收、1 台 DR 状态）。
   * App 端进入各任务分别保存草稿与提交。
   * 查看 Web 管理端“报告生成及预览”界面，各设备的原始记录和报告完全精准归属于对应设备，无任何交叉串位，即代表联调成功。
