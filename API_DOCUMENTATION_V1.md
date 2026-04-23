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
