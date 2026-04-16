# API 接口文档

## 基础信息

- **Base URL**: `http://localhost:11223/api/v1/`
- **认证方式**: JWT Token (Bearer Token)
- **数据格式**: JSON

## 认证接口

### 1. 用户登录

**接口地址**: `POST /api/v1/auth/login/`

**请求参数**:
```json
{
  "username": "admin",
  "password": "password123"
}
```

**响应示例**:
```json
{
  "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "user": {
    "id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "first_name": "Admin",
    "last_name": "User",
    "role_code": "super_admin",
    "role_name": "超级管理员"
  }
}
```

**状态码**:
- `200 OK`: 登录成功
- `401 UNAUTHORIZED`: 用户名或密码错误

---

### 2. Token 刷新

**接口地址**: `POST /api/v1/auth/token/refresh/`

**请求参数**:
```json
{
  "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**响应示例**:
```json
{
  "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**状态码**:
- `200 OK`: 刷新成功
- `400 BAD REQUEST`: 缺少 refresh token
- `401 UNAUTHORIZED`: Token 无效或已过期

---

## 用户管理接口

### 3. 用户列表

**接口地址**: `GET /api/v1/users/`

**认证要求**: 需要登录 + 管理员权限

**请求头**:
```
Authorization: Bearer {access_token}
```

**响应示例**:
```json
[
  {
    "id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "first_name": "Admin",
    "last_name": "User",
    "phone": "13800138000",
    "department": "技术部",
    "position": "技术总监",
    "role_code": "super_admin",
    "is_staff": true,
    "is_active": true
  }
]
```

**状态码**:
- `200 OK`: 查询成功

---

### 4. 创建用户

**接口地址**: `POST /api/v1/users/`

**认证要求**: 需要登录 + 管理员权限

**请求头**:
```
Authorization: Bearer {access_token}
```

**请求参数**:
```json
{
  "username": "newuser",
  "email": "newuser@example.com",
  "password": "password123",
  "first_name": "New",
  "last_name": "User",
  "phone": "13900139000",
  "department": "运营部",
  "position": "运营专员",
  "role_code": "app_user"
}
```

**响应示例**:
```json
{
  "id": 2,
  "username": "newuser",
  "email": "newuser@example.com",
  "first_name": "New",
  "last_name": "User",
  "phone": "13900139000",
  "department": "运营部",
  "position": "运营专员",
  "role_code": "app_user",
  "is_staff": false,
  "is_active": true
}
```

**状态码**:
- `201 CREATED`: 创建成功
- `400 BAD REQUEST`: 参数错误

---

### 5. 获取单个用户

**接口地址**: `GET /api/v1/users/{id}/`

**认证要求**: 需要登录 + 管理员权限

**请求头**:
```
Authorization: Bearer {access_token}
```

**响应示例**:
```json
{
  "id": 1,
  "username": "admin",
  "email": "admin@example.com",
  "first_name": "Admin",
  "last_name": "User",
  "phone": "13800138000",
  "department": "技术部",
  "position": "技术总监",
  "role_code": "super_admin",
  "is_staff": true,
  "is_active": true
}
```

**状态码**:
- `200 OK`: 查询成功
- `404 NOT FOUND`: 用户不存在

---

### 6. 更新用户

**接口地址**: `PUT /api/v1/users/{id}/` 或 `PATCH /api/v1/users/{id}/`

**认证要求**: 需要登录 + 管理员权限

**请求头**:
```
Authorization: Bearer {access_token}
```

**请求参数** (PATCH 支持部分更新):
```json
{
  "email": "updated@example.com",
  "phone": "13800138001",
  "department": "产品部"
}
```

**响应示例**:
```json
{
  "id": 1,
  "username": "admin",
  "email": "updated@example.com",
  "first_name": "Admin",
  "last_name": "User",
  "phone": "13800138001",
  "department": "产品部",
  "position": "技术总监",
  "role_code": "super_admin",
  "is_staff": true,
  "is_active": true
}
```

**状态码**:
- `200 OK`: 更新成功
- `400 BAD REQUEST`: 参数错误
- `404 NOT FOUND`: 用户不存在

---

### 7. 删除用户

**接口地址**: `DELETE /api/v1/users/{id}/`

**认证要求**: 需要登录 + 管理员权限

**请求头**:
```
Authorization: Bearer {access_token}
```

**响应**: 无内容

**状态码**:
- `204 NO CONTENT`: 删除成功
- `404 NOT FOUND`: 用户不存在

---

### 8. 获取当前用户信息

**接口地址**: `GET /api/v1/users/me/`

**认证要求**: 需要登录

**请求头**:
```
Authorization: Bearer {access_token}
```

**响应示例**:
```json
{
  "id": 1,
  "username": "admin",
  "email": "admin@example.com",
  "first_name": "Admin",
  "last_name": "User",
  "phone": "13800138000",
  "department": "技术部",
  "position": "技术总监",
  "role_code": "super_admin",
  "is_staff": true,
  "is_active": true
}
```

**状态码**:
- `200 OK`: 查询成功

---

## 角色管理接口

### 9. 角色列表

**接口地址**: `GET /api/v1/roles/`

**认证要求**: 需要登录

**请求头**:
```
Authorization: Bearer {access_token}
```

**响应示例**:
```json
[
  {
    "id": 1,
    "name": "超级管理员",
    "code": "super_admin",
    "description": "系统最高权限管理员",
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z"
  },
  {
    "id": 2,
    "name": "普通管理员",
    "code": "admin",
    "description": "普通管理员",
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z"
  },
  {
    "id": 3,
    "name": "App 用户",
    "code": "app_user",
    "description": "平板 App 端用户",
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z"
  }
]
```

**状态码**:
- `200 OK`: 查询成功

---

### 10. 获取单个角色

**接口地址**: `GET /api/v1/roles/{id}/`

**认证要求**: 需要登录

**请求头**:
```
Authorization: Bearer {access_token}
```

**响应示例**:
```json
{
  "id": 1,
  "name": "超级管理员",
  "code": "super_admin",
  "description": "系统最高权限管理员",
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-01T00:00:00Z"
}
```

**状态码**:
- `200 OK`: 查询成功
- `404 NOT FOUND`: 角色不存在

---

## 菜单管理接口

### 11. 菜单列表

**接口地址**: `GET /api/v1/menus/`

**认证要求**: 需要登录

**请求头**:
```
Authorization: Bearer {access_token}
```

**说明**: 
- 超级管理员可获取所有菜单
- 普通用户根据角色权限获取对应菜单
- 返回顶级菜单及其子菜单

**响应示例**:
```json
[
  {
    "id": 1,
    "name": "用户管理",
    "path": "/users/",
    "icon": "user",
    "parent": null,
    "sort_order": 1,
    "is_visible": true,
    "children": [
      {
        "id": 2,
        "name": "用户列表",
        "path": "/users/",
        "icon": "list",
        "parent": 1,
        "sort_order": 1,
        "is_visible": true,
        "children": []
      },
      {
        "id": 3,
        "name": "角色管理",
        "path": "/roles/",
        "icon": "shield",
        "parent": 1,
        "sort_order": 2,
        "is_visible": true,
        "children": []
      }
    ],
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z"
  }
]
```

**状态码**:
- `200 OK`: 查询成功

---

### 12. 获取单个菜单

**接口地址**: `GET /api/v1/menus/{id}/`

**认证要求**: 需要登录

**请求头**:
```
Authorization: Bearer {access_token}
```

**响应示例**:
```json
{
  "id": 1,
  "name": "用户管理",
  "path": "/users/",
  "icon": "user",
  "parent": null,
  "sort_order": 1,
  "is_visible": true,
  "children": [
    {
      "id": 2,
      "name": "用户列表",
      "path": "/users/",
      "icon": "list",
      "parent": 1,
      "sort_order": 1,
      "is_visible": true,
      "children": []
    }
  ],
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-01T00:00:00Z"
}
```

**状态码**:
- `200 OK`: 查询成功
- `404 NOT FOUND`: 菜单不存在

---

## 权限说明

### 角色类型

- **super_admin**: 超级管理员，拥有所有权限
- **admin**: 普通管理员，可管理用户、角色、菜单
- **app_user**: App 用户，仅可查看基本信息

### 权限要求

- **用户管理接口** (增删改查): 需要 `super_admin` 或 `admin` 角色
- **角色和菜单接口** (查询): 需要登录即可
- **认证接口**: 无需认证

---

## 错误响应格式

所有错误响应遵循统一格式：

```json
{
  "error": "错误描述信息"
}
```

常见错误状态码：
- `400 BAD REQUEST`: 请求参数错误
- `401 UNAUTHORIZED`: 未认证或 Token 无效
- `403 FORBIDDEN`: 权限不足
- `404 NOT FOUND`: 资源不存在
- `500 INTERNAL SERVER ERROR`: 服务器内部错误

---

## 使用示例

### cURL 示例

```bash
# 1. 登录获取 Token
curl -X POST http://localhost:11223/api/v1/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "password123"}'

# 2. 使用 Token 获取用户列表
curl -X GET http://localhost:11223/api/v1/users/ \
  -H "Authorization: Bearer {access_token}"

# 3. 刷新 Token
curl -X POST http://localhost:11223/api/v1/auth/token/refresh/ \
  -H "Content-Type: application/json" \
  -d '{"refresh": "{refresh_token}"}'
```

### Python 示例

```python
import requests

# 1. 登录获取 Token
response = requests.post('http://localhost:11223/api/v1/auth/login/', json={
    'username': 'admin',
    'password': 'password123'
})
data = response.json()
access_token = data['access']

# 2. 使用 Token 访问受保护接口
headers = {'Authorization': f'Bearer {access_token}'}
response = requests.get('http://localhost:11223/api/v1/users/', headers=headers)
print(response.json())
```

---

## 注意事项

1. **Token 有效期**: Access Token 有效期为 2 小时，Refresh Token 有效期为 7 天
2. **Token 刷新**: Access Token 过期后，使用 Refresh Token 获取新的 Access Token
3. **权限控制**: 用户管理接口需要管理员权限，普通用户无法访问
4. **菜单过滤**: 菜单接口会根据用户角色自动过滤，只返回有权限访问的菜单
5. **分页**: 用户列表支持分页，默认每页 20 条数据
