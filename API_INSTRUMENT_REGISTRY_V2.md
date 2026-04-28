# 仪器数据库读取接口文档（v2）

> 适用范围：前端读取“检测仪器台账（InstrumentCatalog）”  
> Base URL：`/api/v2`
> 当前只开放“读取”能力（`GET`），不开放前端直写。

## 1. 鉴权与权限

- 认证方式：`Bearer JWT` 或已登录 `Session`
- 请求头示例：
  - `Authorization: Bearer <access_token>`
- 权限要求：
  - 用户需通过认证
  - 角色需具备文件库读取权限（`perm_file_library`）
- 无权限返回：`403 Forbidden`

## 2. 接口总览

- 列表查询（分页）：`GET /api/v2/registry/instruments/`
- 单条详情：`GET /api/v2/registry/instruments/{id}/`

## 3. 数据模型字段说明

返回对象字段（单条）：

- `id`：number，主键 ID
- `code`：string，仪器编号（唯一）
- `name`：string，仪器设备名称
- `model`：string，型号
- `calibration_org`：string，检定/校准单位
- `certificate_no`：string，证书编号
- `certificate_valid_until`：string|null，证书有效期（`YYYY-MM-DD`）
- `remarks`：string，备注说明
- `is_active`：boolean，是否启用
- `created_at`：string，创建时间（ISO8601）
- `updated_at`：string，更新时间（ISO8601）

## 4. 列表接口

### 4.1 URL

`GET /api/v2/registry/instruments/`

### 4.2 Query 参数

- 分页参数
  - `page`：页码（从 1 开始）
  - `page_size`：每页条数（未启用自定义时可省略；默认 20）
- 搜索参数
  - `search`：模糊搜索，匹配字段：
    - `code`
    - `name`
    - `model`
    - `certificate_no`
    - `calibration_org`
    - `remarks`
- 排序参数
  - `ordering`：支持字段：
    - `id`
    - `code`
    - `name`
    - `certificate_valid_until`
    - `updated_at`
    - `created_at`
  - 倒序写法：字段名前加 `-`，例如：`ordering=-updated_at`

### 4.3 请求示例

#### 示例 A：按默认排序分页读取

`GET /api/v2/registry/instruments/?page=1`

#### 示例 B：按关键字搜索（证书编号 / 仪器名称）

`GET /api/v2/registry/instruments/?search=JXFS/YQ-001`

#### 示例 C：按证书有效期升序

`GET /api/v2/registry/instruments/?ordering=certificate_valid_until`

### 4.4 成功响应示例（200）

```json
{
  "count": 2,
  "next": "http://127.0.0.1:11223/api/v2/registry/instruments/?page=2",
  "previous": null,
  "results": [
    {
      "id": 1,
      "code": "JXFS/YQ-001",
      "name": "多功能X射线质量检测仪",
      "model": "451P-DE-SI-RYR",
      "calibration_org": "江苏省计量科学研究院",
      "certificate_no": "JL2026-0001",
      "certificate_valid_until": "2027-01-31",
      "remarks": "年度检定通过",
      "is_active": true,
      "created_at": "2026-04-27T09:10:12.123456+08:00",
      "updated_at": "2026-04-27T09:10:12.123456+08:00"
    }
  ]
}
```

## 5. 详情接口

### 5.1 URL

`GET /api/v2/registry/instruments/{id}/`

### 5.2 请求示例

`GET /api/v2/registry/instruments/1/`

### 5.3 成功响应示例（200）

```json
{
  "id": 1,
  "code": "JXFS/YQ-001",
  "name": "多功能X射线质量检测仪",
  "model": "451P-DE-SI-RYR",
  "calibration_org": "江苏省计量科学研究院",
  "certificate_no": "JL2026-0001",
  "certificate_valid_until": "2027-01-31",
  "remarks": "年度检定通过",
  "is_active": true,
  "created_at": "2026-04-27T09:10:12.123456+08:00",
  "updated_at": "2026-04-27T09:10:12.123456+08:00"
}
```

## 6. 错误码

- `401 Unauthorized`：未登录或令牌无效
- `403 Forbidden`：权限不足（缺少读取权限）
- `404 Not Found`：详情 ID 不存在

错误体示例（403）：

```json
{
  "detail": "You do not have permission to perform this action."
}
```

## 7. 前端联调建议

- 列表页使用：
  - 首次加载：`GET /registry/instruments/?page=1`
  - 搜索：输入后拼接 `search=xxx`
  - 排序：拼接 `ordering=...`
- 详情弹窗：
  - 行点击后请求 `GET /registry/instruments/{id}/`
- 时间显示：
  - `certificate_valid_until` 为空时展示 `-`

