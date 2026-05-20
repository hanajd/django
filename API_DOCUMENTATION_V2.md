# API 文档 v2（新版）

Base URL: `http://localhost:11223/api/v2`

认证：除登录外，请求头携带 `Authorization: Bearer <access_token>`。

## 设计约定

- 前端主流程：**先项目，再任务，再拉取模板（JSON + PDF），再填表提交**。
- `projectId` = 委托编号（项目 `code`），格式 **YY####**（年份后两位 + 四位流水，如 `260001`）；兼容旧版 `yyyymmNN`（8 位）。
- `taskNo` = 项目内两位序号（`01`、`02`...），与任务模板库中的任务顺序一致。
- `taskNo` 必须在对应 `projectId` 下使用。

## 后台管理 → App 数据流（任务模板库）

管理端在 **任务模板库** 中维护结构，App 只消费绑定结果：

1. **任务分类** → **报告任务**（`outputTarget: report`）→ **现场记录任务**（`outputTarget: site_record`）。
2. 每个库任务在文件库 **template** 分类下绑定成对文件：
   - **`.json`**：HTMLPDF 统一模板（含 `pdf.fields` 划框坐标）。
   - **`.pdf`**：与 JSON 对应的空白版式 PDF（App 用于预览/划框对齐）。
3. 报告任务通过 `report_source_tasks` 关联下属现场记录（管理端配置，App 无需再选手动关联）。
4. **医院项目** 勾选要执行的库任务后，App 通过 `projectId` + `taskNo` 访问对应模板与提交接口。

```
任务模板库（绑定 template: JSON + PDF）
        ↓ 项目分配任务
GET …/projects/{projectId}/tasks  →  templatePdfDownloadUrl + frontendTemplateDownloadUrl
        ↓
App：下载 PDF 底图 + 下载前端 JSON（含 rect / pdfFieldId）
        ↓
start → draft/submit →（可选）manual-export-report 生成报告 PDF
```

## 第一步：获取项目列表

- `GET /inspections/projects`（推荐）
- `GET /inspections/pending`（与 `projects` **相同**，返回项目列表）

**App 必用字段：`projectId`**（委托编号）。用它拼第二步 URL，不要用项目内的 `taskNo`（`01`、`02`…）。

```text
GET /api/v2/inspections/pending
  → 取 data.list[0].projectId   // 例如 "260001"

GET /api/v2/inspections/projects/260001/tasks
  → 取每条任务的 taskNo、模板下载地址等
```

响应示例（`pending` / `projects` 一致）：

```json
{
  "success": true,
  "message": "获取成功",
  "data": {
    "count": 1,
    "list": [
      {
        "projectId": "260001",
        "projectPk": 1,
        "projectName": "DSA报告测试",
        "commissionOrganization": "张三医院 · 老院区 · 外科",
        "description": "测试项目",
        "updatedAt": "2026-05-19T09:14:59.884431+00:00"
      }
    ]
  }
}
```

说明：

- **`projectId` 就是你要的路径参数**（上例为 `260001` → `GET …/projects/260001/tasks`）。
- 已去掉的是旧版里与 `projectId` **重复** 的 `taskNo`（以前 `taskNo` 也等于 `260001`，容易和项目内任务序号 `01` 混淆）。
- `projectPk` 为数据库主键，一般 App 不需要；对外路由统一用 `projectId`。

## 第二步：获取项目下任务（含 JSON + PDF 模板）

- `GET /inspections/projects/{projectId}/tasks`
- 路径里的 `{projectId}` **必须来自第一步** `data.list[].projectId`（如 `260001`）
- 行为：
  - 返回任务基础信息（`taskNo` / `taskCode` / `taskName` / `outputTarget`）
  - 返回 **前端 JSON** 与 **PDF 版式** 的下载链接（列表内仅 URL，避免大包体）

### `taskNo` 与 `inspectedNo`（必读）

| 字段 | 含义 | 前端用途 |
|------|------|----------|
| **`taskNo`** | 项目内**每个库任务唯一**序号（`01`…，按 `code,id` 排序，含报告 + 各现场记录） | **所有 API 路径、提交、下载模板**：`/tasks/{taskNo}/…` |
| **`inspectedNo`** | **受检编号**，按**报告任务**编号；同报告下多条现场记录 **相同** | 界面展示、按受检设备分组；**不能**当作 URL 里的 `taskNo` |
| **`reportTaskNo`** | 所属报告任务的 `taskNo` | 现场记录行挂到哪个报告 |
| **`relatedSiteRecordTaskNos`** | 仅报告任务有：下属现场记录的 `taskNo` 列表 | 从报告进入各现场记录 |

示例：项目任务顺序为「报告 A → 现场记录 A1 → 现场记录 A2 → 报告 B」时：

| taskNo | outputTarget | inspectedNo | reportTaskNo |
|--------|--------------|-------------|--------------|
| 01 | report | 01 | 01 |
| 02 | site_record | 01 | 01 |
| 03 | site_record | 01 | 01 |
| 04 | report | 02 | 04 |

打开现场记录 A2 的模板/提交：使用 **`taskNo=03`**，导出 JSON 里受检编号仍为 **`01`**。
- 重要：
  - 路径必须带 `inspections` 前缀：`/api/v2/inspections/projects/20260401/tasks`
  - 不要请求 `/api/v2/projects/20260401/tasks`（不存在）

响应示例：

```json
{
  "success": true,
  "message": "获取成功",
  "data": {
    "projectId": "20260401",
    "count": 1,
    "list": [
      {
        "assignmentId": 12,
        "taskNo": "02",
        "inspectedNo": "01",
        "reportTaskId": 7,
        "reportTaskNo": "01",
        "relatedSiteRecordTaskNos": [],
        "projectId": "260001",
        "taskId": 8,
        "taskCode": "js001-site",
        "taskName": "现场记录-CT",
        "outputTarget": "site_record",
        "assignedAt": "2026-04-22T16:21:10+08:00",
        "frontendTemplateDownloadUrl": "http://localhost:11223/api/v2/inspections/projects/20260401/tasks/01/export-frontend-json",
        "frontendTemplateMeta": {
          "templateFileId": 245,
          "templateFileName": "JS-001.json",
          "exported": true,
          "error": ""
        },
        "templatePdfDownloadUrl": "http://localhost:11223/api/v2/library/files/244/download/",
        "templatePdfMeta": {
          "templateFileId": 244,
          "templateFileName": "JS-001.pdf",
          "ready": true,
          "error": ""
        }
      }
    ]
  }
}
```

### App 拉取模板（推荐顺序）

对每个 `data.list` 任务项：

| 资源 | 字段 | 说明 |
|------|------|------|
| PDF 底图 | `templatePdfDownloadUrl` | `GET` 直链，需 Bearer；对应任务模板库绑定的 `.pdf` |
| 前端 JSON | `frontendTemplateDownloadUrl` | `GET` 导出接口，见下文 |

1. 检查 `templatePdfMeta.ready` / `frontendTemplateMeta.exported`；失败时读 `error`（如缺少绑定、多个 PDF 等）。
2. **先下载 PDF**（本地缓存，按 `taskNo` 或 `templatePdfMeta.templateFileId` 命名）。
3. **再下载前端 JSON**（见下一节）；用 JSON 中字段的 `rect` 与 `source.pdfFieldId` 在 PDF 上定位控件。

`templatePdfMeta` / `frontendTemplateMeta` 中的 `templateFileId` 为文件库 **template** 分类中的源文件 ID，便于排查与日志关联。

### 前端 JSON 导出：`export-frontend-json`

- `GET /inspections/projects/{projectId}/tasks/{taskNo}/export-frontend-json`
- 兼容：`GET /inspections/{taskNo}/export-frontend-json`

服务端流程（与后台 HTMLPDF 编辑器一致）：

1. 读取任务绑定的 **最新 `.json`** 统一模板。
2. 合并当前案件提交数据（无提交时导出空结构 + 任务/项目默认值）。
3. 经规则引擎生成 **前端 schema**（失败时回退旧提取逻辑）。
4. 以附件形式返回 `application/json`。

响应体为 **前端填表 schema**，不是原始统一模板文件。主要字段：

- `steps[].sections[].fields[]`：表单项；每项含 `id`、`type`、`label`、`defaultValue` 等。
- `rect`：`[page, x, y, w, h]`，与统一模板 `pdf.fields` 坐标一致，用于 PDF 叠层（**每次请求由服务端从模板 `pdf.fields` 生成**，不是直接返回文件库里某份静态前端 JSON 文件）。
- `source.pdfFieldId`：关联 PDF 字段 ID（如 `f12`）。

注意：任务下若同时存在「统一模板 JSON」与「HTMLPDF 另存的前端 JSON」，接口会**优先读取含 `pdf.fields` 的统一模板**再导出；在编辑器里保存的前端 JSON 不会原样透传，除非其结构与统一模板一致且被解析为唯一模板源。
- `instruments`：任务绑定的仪器台账（根级数组，下拉数据走登记 API，不再内嵌 `instrumentCatalogOptions`）。
- `pdfUrl`：占位相对路径（如 `/pdf/templates/...`），**App 应使用任务列表中的 `templatePdfDownloadUrl` 作为真实 PDF 地址**，勿依赖 `pdfUrl` 直连。

可选 Query：`placeholderMapId` / `placeholder_map_id`（与 submit 一致，用于占位符映射）。

### PDF 其它获取方式（补充）

| 场景 | 接口 |
|------|------|
| 已知 template 文件 ID | `GET /library/files/{id}/download/` |
| Word 模板转 PDF（非 HTMLPDF 主流程） | `GET\|POST /library/templates/{id}/prepare-pdf-download/` → `temp-pdf-download?token=...` |
| 全库可下载模板清单 | `GET /library/templates/downloadable/` |

HTMLPDF 任务模板库场景：**优先使用任务列表返回的 `templatePdfDownloadUrl`**（已为该任务解析唯一 PDF）。

## 第三步：任务详情与提交流程

- `GET /inspections/projects/{projectId}/tasks/{taskNo}` — 已有草稿/提交数据
- `POST …/start` — 开始任务
- `POST …/draft` — 保存草稿
- `POST …/submit` — 提交（可触发 PDF 生成写入文件库）
- `GET …/export-frontend-json` — 刷新前端 JSON（提交后再次打开表单时使用）
- `POST …/manual-export-report` — 现场记录聚合回填报告 PDF（报告任务）

典型 App 时序：

```
登录 → 项目列表 → 任务列表 → 下载 PDF + export-frontend-json
     → start → 渲染表单（rect 对齐 PDF）→ draft（可选多次）
     → submit → 查看 pdfGeneration / 文件列表
```

### 手动导出报告（现场记录 → 报告 PDF）

从**当前案件**关联的**现场记录 JSON**（按报告任务 `report_source_tasks` 顺序深度合并）填入**报告任务**模板，写入文件库 **report** 分类。

- **推荐**：`POST /inspections/projects/{projectId}/tasks/{taskNo}/manual-export-report`
- **兼容**：`POST /inspections/{taskNo}/manual-export-report`
- 可选：`placeholderMapId` / `placeholder_map_id`（Query 或 Body）

前置条件（否则 409）：

- 项目存在 `outputTarget === "report"` 的报告任务；
- 案件下已有可合并的现场记录 JSON。

响应示例（成功）：

```json
{
  "success": true,
  "message": "报告导出成功",
  "data": {
    "taskNo": "01",
    "projectId": "20260401",
    "reportTask": { "id": 3, "code": "rpt001", "name": "检测报告" },
    "sourceSiteRecordTasks": [
      { "id": 5, "code": "site001", "name": "现场记录" }
    ],
    "reportFile": {
      "id": 1201,
      "name": "01-报告.pdf",
      "downloadUrl": "http://localhost:11223/api/v2/inspections/projects/20260401/tasks/01/files/1201/download/report"
    }
  }
}
```

`reportFile` 在首次导出失败或未生成时可能省略。

### 提交接口示例

- `POST /inspections/projects/{projectId}/tasks/{taskNo}/submit`

请求体示例：

```json
{
  "reportInfo": {
    "reportNo": "20260401-01"
  },
  "hospitalInfo": {
    "name": "石城县人民医院"
  },
  "equipmentInfo": {
    "deviceName": "碎石机",
    "model": "WS76"
  },
  "instruments": [],
  "testResult": {},
  "signatures": {},
  "conclusion": {
    "conclusionText": "检测完成"
  }
}
```

响应示例：

```json
{
  "success": true,
  "message": "提交成功",
  "data": {
    "taskNo": "01",
    "projectId": "20260401",
    "status": "submitted",
    "submittedAt": "2026-04-22T16:28:31+08:00",
    "pdfGeneration": [
      {
        "taskCode": "js001",
        "outputTarget": "site_record",
        "ok": true,
        "reason": ""
      }
    ]
  }
}
```

`pdfGeneration` 表示服务端按模板回填生成的 **现场记录/报告 PDF**（写入 site_record / report 分类），与第二步的 **空白 template PDF** 不同。

## 签名 / 文件 / OCR（项目作用域）

- `GET /inspections/projects/{projectId}/tasks/{taskNo}/signatures/{role}`
- `POST /inspections/projects/{projectId}/tasks/{taskNo}/signatures/{character}/upload`
- `POST /inspections/projects/{projectId}/tasks/{taskNo}/files/upload`
- `GET /inspections/projects/{projectId}/tasks/{taskNo}/files/{category}`
- `GET /inspections/projects/{projectId}/tasks/{taskNo}/files/{id}/download/{category}`
- `POST /inspections/projects/{projectId}/tasks/{taskNo}/ocr/upload`
- `GET /inspections/projects/{projectId}/tasks/{taskNo}/ocr/tasks/{ocrTaskId}/status`
- `GET /inspections/projects/{projectId}/tasks/{taskNo}/ocr/tasks/{ocrTaskId}/autofill`

## v1 迁移补齐接口（v2 同步可用）

以下 v1 仍在用但未改造的接口，已在 v2 同路径提供：

- 认证：
  - `POST /auth/login/`
  - `POST /auth/token/refresh/`
- 文件库：
  - `POST /library/files/ocr/`
  - `GET /library/files/ocr/tasks/{taskId}/status/`
  - `GET /library/files/ocr/tasks/{taskId}/autofill/`
  - `GET /library/files/{id}/download/`
  - `POST /library/files/upload-linked/`
  - `GET /library/templates/downloadable/`
  - `GET|POST /library/templates/{id}/prepare-pdf-download/`
  - `GET /library/templates/temp-pdf-download/?token=...`
- 用户/角色/菜单与 Registry：
  - `users/*`
  - `roles/*`
  - `menus/*`
  - `registry/organizations/*`
  - `registry/contacts/*`
  - `registry/devices/*`
  - `registry/cases/*`
  - `registry/site-records/*`
  - `registry/reports/*`
- 旧 taskNo 链路（兼容，建议逐步迁移）：
  - `GET /inspections/history`
  - `POST /inspections/submit`
  - `GET /inspections/{taskNo}`
  - `POST /inspections/{taskNo}/start`
  - `POST /inspections/{taskNo}/draft`
  - `POST /inspections/{taskNo}/submit`
  - `GET /inspections/{taskNo}/export-frontend-json`
  - `POST /inspections/{taskNo}/manual-export-report`
  - `GET /inspections/{taskNo}/signatures/{role}`
  - `POST /inspections/{taskNo}/signatures/{character}/upload`
  - `POST /inspections/{taskNo}/files/upload`
  - `GET /inspections/{taskNo}/files/{category}`
  - `GET /inspections/{taskNo}/files/{id}/download/{category}`
  - `POST /inspections/{taskNo}/ocr/upload`
  - `GET /inspections/{taskNo}/ocr/tasks/{ocrTaskId}/status`
  - `GET /inspections/{taskNo}/ocr/tasks/{ocrTaskId}/autofill`

### v2 全量接口清单（含补齐接口）

- 认证：
  - `POST /auth/login/`
  - `POST /auth/token/refresh/`
- 新版项目任务主链路：
  - `GET /inspections/projects`
  - `GET /inspections/pending`
  - `GET /inspections/projects/{projectId}/tasks`
  - `GET /inspections/projects/{projectId}/tasks/{taskNo}`
  - `POST /inspections/projects/{projectId}/tasks/{taskNo}/start`
  - `POST /inspections/projects/{projectId}/tasks/{taskNo}/draft`
  - `POST /inspections/projects/{projectId}/tasks/{taskNo}/submit`
  - `GET /inspections/projects/{projectId}/tasks/{taskNo}/export-frontend-json`
  - `POST /inspections/projects/{projectId}/tasks/{taskNo}/manual-export-report`
  - `GET /inspections/projects/{projectId}/tasks/{taskNo}/signatures/{role}`
  - `POST /inspections/projects/{projectId}/tasks/{taskNo}/signatures/{character}/upload`
  - `POST /inspections/projects/{projectId}/tasks/{taskNo}/files/upload`
  - `GET /inspections/projects/{projectId}/tasks/{taskNo}/files/{category}`
  - `GET /inspections/projects/{projectId}/tasks/{taskNo}/files/{id}/download/{category}`
  - `POST /inspections/projects/{projectId}/tasks/{taskNo}/ocr/upload`
  - `GET /inspections/projects/{projectId}/tasks/{taskNo}/ocr/tasks/{ocrTaskId}/status`
  - `GET /inspections/projects/{projectId}/tasks/{taskNo}/ocr/tasks/{ocrTaskId}/autofill`
- 文件库补齐：
  - `POST /library/files/ocr/`
  - `GET /library/files/ocr/tasks/{taskId}/status/`
  - `GET /library/files/ocr/tasks/{taskId}/autofill/`
  - `GET /library/files/{id}/download/`
  - `POST /library/files/upload-linked/`
  - `GET /library/templates/downloadable/`
  - `GET|POST /library/templates/{id}/prepare-pdf-download/`
  - `GET /library/templates/temp-pdf-download/?token=...`
- 旧 taskNo 链路补齐（兼容）：
  - `GET /inspections/history`
  - `POST /inspections/submit`
  - `GET /inspections/{taskNo}`
  - `POST /inspections/{taskNo}/start`
  - `POST /inspections/{taskNo}/draft`
  - `POST /inspections/{taskNo}/submit`
  - `GET /inspections/{taskNo}/export-frontend-json`
  - `POST /inspections/{taskNo}/manual-export-report`
  - `GET /inspections/{taskNo}/signatures/{role}`
  - `POST /inspections/{taskNo}/signatures/{character}/upload`
  - `POST /inspections/{taskNo}/files/upload`
  - `GET /inspections/{taskNo}/files/{category}`
  - `GET /inspections/{taskNo}/files/{id}/download/{category}`
  - `POST /inspections/{taskNo}/ocr/upload`
  - `GET /inspections/{taskNo}/ocr/tasks/{ocrTaskId}/status`
  - `GET /inspections/{taskNo}/ocr/tasks/{ocrTaskId}/autofill`
- 用户与业务主数据（补齐）：
  - `users/*`
  - `roles/*`
  - `menus/*`
  - `registry/organizations/*`
  - `registry/contacts/*`
  - `registry/devices/*`
  - `registry/cases/*`
  - `registry/site-records/*`
  - `registry/reports/*`

### OCR 任务状态响应示例

```json
{
  "success": true,
  "message": "获取成功",
  "data": {
    "taskNo": "01",
    "projectId": "20260401",
    "ocrTaskId": 35,
    "status": "success",
    "batchId": "0b5636d2-4fd5-4db6-8eb3-b0a9ec43df2f",
    "generatedJsonFiles": [
      {
        "id": 516,
        "original_name": "20260401_01.json",
        "download_url": "http://localhost:11223/api/v2/inspections/projects/20260401/tasks/01/files/516/download/json"
      }
    ]
  }
}
```

## 自动填写绑定（HTMLPDF）

- 委托编号自动绑定：`projectId`（YY####）
- 受检编号自动绑定：项目内**报告任务**顺序 `01`、`02`…（同报告下的现场记录与报告同号；API 路径仍用 `taskNo` 区分具体任务）
- 兼容键：`entrustNo` / `委托编号`、`inspectedNo` / `受检编号`

## 登录示例（v2）

- `POST /auth/login/`
- `POST /auth/token/refresh/`

请求体示例：

```json
{
  "username": "app_user",
  "password": "123456"
}
```

响应示例：

```json
{
  "access": "jwt_access_token",
  "refresh": "jwt_refresh_token",
  "user": {
    "id": 8,
    "username": "app_user",
    "role_code": "app_user",
    "role_name": "App 用户"
  }
}
```
