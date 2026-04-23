# API 文档 v2（新版）

Base URL: `http://localhost:11223/api/v2`

## 设计约定

- 前端流程：先项目，再任务。
- `projectId` = 委托编号（项目 `code`），格式 `yyyymmNN`。
- `taskNo` = 项目内两位序号（`01`、`02`...）。
- `taskNo` 必须在对应 `projectId` 下使用。

## 第一步：获取项目列表

- `GET /inspections/projects`（推荐）
- `GET /inspections/pending`（兼容入口）

响应示例：

```json
{
  "success": true,
  "message": "获取成功",
  "data": {
    "count": 1,
    "list": [
      {
        "projectId": "20260401",
        "projectPk": 3,
        "projectName": "石城县人民医院",
        "description": "",
        "updatedAt": "2026-04-22T16:20:01+08:00"
      }
    ]
  }
}
```

兼容说明：

- `GET /inspections/projects`：不返回 `taskNo`。
- `GET /inspections/pending`：为兼容旧前端，额外返回 `taskNo`，且 `taskNo == projectId`。

## 第二步：获取项目下任务

- `GET /inspections/projects/{projectId}/tasks`
- 行为：
  - 返回任务基础信息（`taskNo/taskCode/taskName`）
  - 返回该任务前端 JSON 的下载链接，前端按需下载，避免列表响应过大
- 重要：
  - 请求路径必须带 `inspections` 前缀，正确示例：`/api/v2/inspections/projects/20260401/tasks`
  - 不要请求 `/api/v2/projects/20260401/tasks`（该路径不存在）

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
        "taskNo": "01",
        "projectId": "20260401",
        "taskId": 7,
        "taskCode": "js001",
        "taskName": "JS-001",
        "outputTarget": "site_record",
        "assignedAt": "2026-04-22T16:21:10+08:00",
        "frontendTemplateDownloadUrl": "http://localhost:11223/api/v2/inspections/projects/20260401/tasks/01/export-frontend-json",
        "frontendTemplateMeta": {
          "templateFileId": 245,
          "templateFileName": "JS-001.json",
          "exported": true,
          "error": ""
        }
      }
    ]
  }
}
```

前端拿任务对应 JSON 的方式：

- 遍历 `data.list`，读取每个任务项的 `frontendTemplateDownloadUrl`
- 调用下载链接获取该任务对应前端 JSON
- 用 `frontendTemplateMeta.exported` 判断是否导出成功
- 若失败，可查看 `frontendTemplateMeta.error`

## 项目任务详情与提交流程

- `GET /inspections/projects/{projectId}/tasks/{taskNo}`
- `POST /inspections/projects/{projectId}/tasks/{taskNo}/start`
- `POST /inspections/projects/{projectId}/tasks/{taskNo}/draft`
- `POST /inspections/projects/{projectId}/tasks/{taskNo}/submit`
- `GET /inspections/projects/{projectId}/tasks/{taskNo}/export-frontend-json`

### 提交接口示例

- `POST /inspections/projects/{projectId}/tasks/{taskNo}/submit`
- 请求体示例：

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

- 响应示例：

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

- 委托编号自动绑定：`projectId`
- 受检编号自动绑定：`taskNo`
- 兼容键：`entrustNo` / `委托编号`、`inspectedNo` / `受检编号`

## 登录示例（v2）

- `POST /auth/login/`
- `POST /auth/token/refresh/`
- 请求体示例：

```json
{
  "username": "app_user",
  "password": "123456"
}
```

- 响应示例：

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
