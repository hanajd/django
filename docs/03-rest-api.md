# 03 REST API（apps.api）

## 1. 路由挂载

| 前缀 | 文件 | 说明 |
|------|------|------|
| `/api/v1/` | `apps/api/urls_v1.py` | **旧版**检测相关接口（仍保留兼容） |
| `/api/v2/` | `apps/api/urls_v2.py` | **新版**「项目 → 任务」组织方式的检测接口 |

根配置：`tablet_backend/urls.py` 中 `include('apps.api.urls_v1')` 与 `urls_v2`。

> 具体路径以两个 `urls_v*.py` 为准；客户端升级时应以 v2 为目标，v1 仅作迁移参考。

## 2. 视图模块划分

| 文件 | 典型职责 |
|------|----------|
| `apps/api/api_views.py` | 登录/刷新 Token、用户/角色/菜单 ViewSet、文件库下载、模板列表与临时 PDF、OCR 上传与任务状态/回填等 |
| `apps/api/inspection_views.py` | 检测流程：开始/提交/草稿、按项目或按任务的列表与详情、文件上传下载、OCR、签名、前端 JSON 导出、手工导出报告等（v1/v2 引用子集不同） |
| `apps/api/registry_views.py` | 台账 ViewSet：受检单位、联系人、业务设备、仪器台账、案件、现场记录、报告；以及登记关联文件上传 API |
| `apps/api/inspection_pdf_service.py` | 检测 PDF 生成/填充相关服务 |
| `apps/api/inspection_report_make.py` | 报告制作/解析任务与案件等 |
| `apps/api/inspection_submit_placeholder_maps.py` | 提交占位等映射数据 |

## 3. 认证

- JWT：`AuthAPIView`、`TokenRefreshAPIView`（见各 `urls_v*`）。  
- 与 `settings.REST_FRAMEWORK`、`SIMPLE_JWT` 一致；Web 端若同源也可带 Session。

## 4. 与 core 的关系

- API 大量使用 `apps.core.models` 中的 `Library*`、`Inspection*`、`Registry*` 等模型。  
- 文件落盘路径、权限边界应与 `library_access`、`library_file_service` 保持一致，避免 API 与 Web 行为分叉。

## 5. 维护注意

- 新增对外接口：优先在 **v2** 增加并写清 OpenAPI/内部文档；避免仅在 v1 扩展。  
- 改动检测状态机或提交结构时，同步检查 **Web 端项目工作台 / 文件库** 是否依赖同一套模型字段。

## 6. 关于 `apps/api/urls.py`

根路由 `tablet_backend/urls.py` 只 `include` 了 **`urls_v1.py`** 与 **`urls_v2.py`**。  
`apps/api/urls.py` 与 v1 内容相近，但**当前未被主 urls 引用**；维护时以 `urls_v1.py`、`urls_v2.py` 为准。合并或删除前请全局搜索 `include`，确认无其它入口引用。
