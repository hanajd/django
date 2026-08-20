# API 文档 v1（旧版）

Base URL: `http://localhost:11223/api/v1`

## 认证

- `POST /auth/login/`
- `POST /auth/token/refresh/`

### 登录请求/响应示例

请求体：

```json
{
  "username": "app_user",
  "password": "123456"
}
```

响应：

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

## 检测主链路（旧）

- `GET /inspections/pending`：旧版待检测任务列表（`taskNo=ASG-*`）
- `GET /inspections/history`
- `GET /inspections/{taskNo}`
- `POST /inspections/{taskNo}/start`
- `POST /inspections/{taskNo}/draft`
- `POST /inspections/{taskNo}/submit`
- `POST /inspections/submit`（兼容提交）
- `GET /inspections/{taskNo}/export-frontend-json`
- `POST /inspections/{taskNo}/manual-export-report`

### 手动导出报告（现场记录回填）

从**当前案件**关联的**现场记录 JSON**（按报告任务上配置的 `report_source_tasks` 顺序读取并深度合并）生成数据，填入**报告任务**的 HTMLPDF 模板，写入文件库 **report** 分类。行为与 v2 的 `manual-export-report` 一致，仅路径前缀为 `/api/v1`。

- **方法 / 路径**：`POST /inspections/{taskNo}/manual-export-report`
- **认证**：需登录。
- **可选参数**：Query 或 JSON Body 中的 `placeholderMapId` / `placeholder_map_id`（二者等价，可省略，传空对象 `{}` 亦可）。
- **前置条件**（不满足时返回 409）：项目下存在报告任务、案件下已有可用的现场记录 JSON 等（见后端 `_load_site_record_payload_for_report`）。
- **说明**：**无需额外开关**；调用本接口即使用现场记录聚合结果填充报告。

响应示例（成功，`downloadUrl` 在能解析到项目时可能为带 `projects/{projectId}/tasks/...` 的 v2 风格链接，否则为仅 `taskNo` 的旧路径）：

```json
{
  "success": true,
  "message": "报告导出成功",
  "data": {
    "taskNo": "ASG-24",
    "projectId": "20260401",
    "reportTask": { "id": 3, "code": "rpt001", "name": "检测报告" },
    "sourceSiteRecordTasks": [{ "id": 5, "code": "site001", "name": "现场记录" }],
    "reportFile": {
      "id": 1201,
      "name": "ASG-24-报告.pdf",
      "downloadUrl": "http://localhost:11223/api/v1/inspections/ASG-24/files/1201/download/report"
    }
  }
}
```

### 待检测列表响应示例（旧）

```json
{
  "success": true,
  "message": "获取成功",
  "data": {
    "total": 1,
    "page": 1,
    "pageSize": 20,
    "list": [
      {
        "taskNo": "ASG-24",
        "reportType": "xray_fluoroscopy",
        "status": "pending",
        "createdAt": "2026-04-16T15:00:00+08:00",
        "assignedAt": "2026-04-16T15:01:00+08:00",
        "reportInfo": {},
        "hospitalInfo": {},
        "equipmentInfo": {}
      }
    ]
  }
}
```

### 提交接口示例（旧）

- `POST /inspections/{taskNo}/submit`

请求体：

```json
{
  "reportInfo": {},
  "hospitalInfo": {},
  "equipmentInfo": {},
  "instruments": [],
  "testResult": {},
  "signatures": {},
  "conclusion": {}
}
```

响应：

```json
{
  "success": true,
  "message": "提交成功",
  "data": {
    "taskNo": "ASG-24",
    "status": "submitted",
    "submittedAt": "2026-04-22T16:28:31+08:00"
  }
}
```

## 签名 / 文件 / OCR（旧）

- `GET /inspections/{taskNo}/signatures/{role}`
- `POST /inspections/{taskNo}/signatures/{character}/upload`
- `POST /inspections/{taskNo}/files/upload`
- `GET /inspections/{taskNo}/files/{category}`
- `GET /inspections/{taskNo}/files/{id}/download/{category}`
- `POST /inspections/{taskNo}/ocr/upload`
- `GET /inspections/{taskNo}/ocr/tasks/{ocrTaskId}/status`
- `GET /inspections/{taskNo}/ocr/tasks/{ocrTaskId}/autofill`

### OCR 状态响应示例（旧）

```json
{
  "success": true,
  "message": "获取成功",
  "data": {
    "taskNo": "ASG-24",
    "ocrTaskId": 35,
    "status": "success",
    "generatedJsonFiles": [
      {
        "id": 516,
        "original_name": "asg24_ocr.json"
      }
    ]
  }
}
```

## 文件库（旧）

- `POST /library/files/ocr/`
- `GET /library/files/ocr/tasks/{taskId}/status/`
- `GET /library/files/ocr/tasks/{taskId}/autofill/`
- `GET /library/files/{id}/download/`
- `POST /library/files/upload-linked/`
- `GET /library/templates/downloadable/`
- `GET|POST /library/templates/{id}/prepare-pdf-download/`
- `GET /library/templates/temp-pdf-download/?token=...`

## 用户 / 角色 / 菜单 / Registry

- `users/*`
- `roles/*`
- `menus/*`
- `registry/organizations/*`
- `registry/contacts/*`
- `registry/devices/*`
- `registry/cases/*`
- `registry/site-records/*`
- `registry/reports/*`

---

> 说明：v1 为旧版兼容接口，建议新前端迁移到 v2（项目 -> 任务）。
