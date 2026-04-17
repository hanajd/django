# API 接口文档（当前实现）

本文档基于当前代码生成，覆盖 `django/apps/api/urls.py` 下已注册接口。

## 基础信息

- Base URL: `http://localhost:11223/api/v1`
- 认证方式: `Authorization: Bearer <access_token>`
- 默认返回: JSON（文件下载接口返回二进制）

## 通用状态码

- `200` 成功
- `201` 创建成功
- `202` 已受理（异步任务已创建）
- `400` 参数错误
- `401` 未认证 / token 无效
- `403` 无权限
- `404` 资源不存在
- `409` 冲突（如 OCR 处理中）
- `422` 数据校验失败（检测提交系列）
- `500` 服务端内部错误

---

## 1. 认证接口

### 1.1 登录

- `POST /auth/login/`
- 请求体:

```json
{
  "username": "admin",
  "password": "123456"
}
```

- 响应:

```json
{
  "access": "jwt_access_token",
  "refresh": "jwt_refresh_token",
  "user": {
    "id": 1,
    "username": "admin",
    "email": "",
    "first_name": "",
    "last_name": "",
    "role_code": "super_admin",
    "role_name": "超级管理员"
  }
}
```

### 1.2 刷新 Token

- `POST /auth/token/refresh/`
- 请求体:

```json
{
  "refresh": "jwt_refresh_token"
}
```

---

## 2. 检测任务（taskNo 主链路）

> 推荐前端统一使用本章节接口。

### 2.1 待检测任务列表

- `GET /inspections/pending`
- Query:
  - `page` (可选，默认 1)
  - `pageSize` (可选，默认 20，最大 100)
  - `deviceType` (可选)
- 响应结构:

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

### 2.2 历史任务列表

- `GET /inspections/history`
- Query 同 `pending`
- 返回 `submitted/approved/rejected` 状态任务

### 2.3 任务详情

- `GET /inspections/{taskNo}`

### 2.4 标记任务开始

- `POST /inspections/{taskNo}/start`

### 2.5 保存草稿（允许部分字段）

- `POST /inspections/{taskNo}/draft`
- 请求体可包含任意子集:
  - `reportInfo`
  - `hospitalInfo`
  - `equipmentInfo`
  - `instruments`
  - `testResult`
  - `signatures`
  - `conclusion`

### 2.6 提交检测结果（推荐）

- `POST /inspections/{taskNo}/submit`
- 请求体: 完整提交结构（reportInfo/hospitalInfo/equipmentInfo/instruments/testResult/signatures/conclusion）
- 成功后会自动把提交内容另存为文件库 `inspection_submit` 分类 JSON 文件并关联项目。
- 成功后会自动将当前项目下模板 JSON（`template` 分类）按 placeholder 映射填充，并将填充结果写入 `inspection_submit` 分类；签名会同步写入模板 image 字段。

### 2.7 兼容提交接口（保留）

- `POST /inspections/submit`
- 说明: 兼容旧版，要求请求体包含 `taskNo`，内部复用 `/{taskNo}/submit` 流程。

### 2.8 签名下载

- `GET /inspections/{taskNo}/signatures/{role}`
- `role`: `author | reviewer | approver`

---

## 3. taskNo 文件接口（项目隔离）

### 3.1 上传文件

- `POST /inspections/{taskNo}/files/{category}`
- 表单字段:
  - `category`: `upload | json | template | site_record | report | attachment | inspection_submit`
  - `files`（多文件）或 `file`（单文件）
  - `link_entity`（可选）: `inspection_case | site_record | report`
  - `link_object_id`（可选）

### 3.2 文件列表

- `GET /inspections/{taskNo}/files/{category}`
- Path:
  - `category`: `upload | json | template | site_record | report | attachment | inspection_submit`
- 返回结果包含:
  - `category`
  - `download_url`（已包含路径中的分类，可直接用于定向下载）

### 3.3 文件下载

- `GET /inspections/{taskNo}/files/{id}/download/{category}`
- Path:
  - `category`: `upload | json | template | site_record | report | attachment | inspection_submit`
- 说明:
  - 下载会限制在该分类内匹配指定 `id`，避免跨分类命中。

---

## 4. taskNo OCR 接口（新）

### 4.1 OCR 上传（创建异步任务）

- `POST /inspections/{taskNo}/ocr/upload`
- 表单字段: `files` 或 `file`

### 4.2 OCR 任务状态

- `GET /inspections/{taskNo}/ocr/tasks/{ocrTaskId}/status`

### 4.3 OCR 自动填充

- `GET /inspections/{taskNo}/ocr/tasks/{ocrTaskId}/autofill`

---

## 5. 文件库旧接口（保留）

### 5.1 OCR 上传（旧）

- `POST /library/files/ocr/`

### 5.2 OCR 状态（旧）

- `GET /library/files/ocr/tasks/{taskId}/status/`

### 5.3 OCR 自动填充（旧）

- `GET /library/files/ocr/tasks/{taskId}/autofill/`

### 5.4 文件下载（旧）

- `GET /library/files/{id}/download/`

### 5.5 业务联动上传

- `POST /library/files/upload-linked/`

### 5.6 模板列表

- `GET /library/templates/downloadable/`

### 5.7 模板转 PDF

- `GET|POST /library/templates/{id}/prepare-pdf-download/`

### 5.8 临时 PDF 下载

- `GET /library/templates/temp-pdf-download/?token=...`

---

## 6. 用户 / 角色 / 菜单

### 6.1 用户

- `GET /users/`
- `POST /users/`
- `GET /users/{id}/`
- `PUT/PATCH /users/{id}/`
- `DELETE /users/{id}/`
- `GET /users/me/`

### 6.2 角色（只读）

- `GET /roles/`
- `GET /roles/{id}/`

### 6.3 菜单（只读）

- `GET /menus/`
- `GET /menus/{id}/`

---

## 7. 业务登记（Registry）

- `registry/organizations`（ModelViewSet）
- `registry/contacts`（ModelViewSet）
- `registry/devices`（ModelViewSet）
- `registry/cases`（ModelViewSet）
- `registry/site-records`（ModelViewSet）
- `registry/reports`（ModelViewSet）
- `GET /registry/reports/{id}/trace/`

---

## 8. taskNo 与项目隔离规则

- `inspections/*` 接口统一通过 `taskNo -> case -> project` 校验关联。
- 非管理员用户必须是该项目分配用户才可访问。
- `/{taskNo}/files/*` 与 `/{taskNo}/ocr/*` 全部按项目隔离，防止跨项目数据污染。

---

## 9. 快速调用示例

```bash
# 1) 登录
curl -X POST http://localhost:11223/api/v1/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"username":"app_user","password":"123456"}'

# 2) 查待办
curl -X GET "http://localhost:11223/api/v1/inspections/pending?page=1&pageSize=20" \
  -H "Authorization: Bearer <access_token>"

# 3) 提交（推荐新接口）
curl -X POST "http://localhost:11223/api/v1/inspections/ASG-24/submit" \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{ "reportInfo": {}, "hospitalInfo": {}, "equipmentInfo": {}, "instruments": [], "testResult": {}, "signatures": {}, "conclusion": {} }'
```
