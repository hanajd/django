# Django 检测与文件库系统

本项目是一个基于 Django 的检测业务系统，包含：

- Web 管理后台（用户/角色/菜单、项目/任务、文件库、模板、手动导出）
- REST API（JWT 认证、任务链路、文件/OCR、业务登记）
- 提交 JSON 到模板字段映射、自动生成现场记录与报告 PDF
- 文件库与项目/任务的关联联动（包含传递性同步）

默认运行端口：`11223`。

---

## 1. 技术栈

- Python 3.10+
- Django 4.2.x
- Django REST Framework
- SimpleJWT
- PyMuPDF（PDF 填充）
- Pillow / pdf2image / pypdf / PyPDF2

依赖见 `requirements.txt`。

---

## 2. 目录结构

```text
django/
├── apps/
│   ├── core/                     # Web 页面、模型、权限、文件库逻辑
│   └── api/                      # API 接口、submit->PDF服务
├── tablet_backend/
│   ├── settings.py               # Django 配置
│   └── urls.py                   # 主路由（Web + /api/v1）
├── templates/                    # 全局模板
├── static/                       # 静态资源
├── media/
│   └── file_library/             # 文件库根目录（运行后自动创建子目录）
├── manage.py
├── API_DOCUMENTATION.md          # API 文档（已维护）
└── README.md
```

---

## 3. 本地快速启动

### 3.1 创建并激活虚拟环境

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3.2 安装依赖

```bash
pip install -r requirements.txt
```

### 3.3 数据库迁移

```bash
python manage.py migrate
```

### 3.4 创建管理员账号

```bash
python manage.py createsuperuser
```

### 3.5 启动服务

```bash
python manage.py runserver 0.0.0.0:11223
```

打开：

- Web: `http://127.0.0.1:11223/`
- Admin: `http://127.0.0.1:11223/admin/`
- API Base: `http://127.0.0.1:11223/api/v1/`

---

## 4. 配置说明

配置文件：`tablet_backend/settings.py`

关键项：

- `DEBUG=True`（当前默认开发模式）
- `ALLOWED_HOSTS=['*']`
- 数据库默认 SQLite：`db.sqlite3`
- 默认语言：`zh-hans`，时区：`Asia/Shanghai`
- JWT 有效期：
  - Access：2 小时
  - Refresh：7 天
- 文件库存储目录（默认在 `media/file_library` 下）：
  - `uploads`
  - `json`
  - `templates`
  - `site_records`
  - `reports`
  - `attachments`
  - `inspection_submits`
  - `temp`

可选环境变量：

- `SECRET_KEY`
- `DJANGO_PORT`（默认 `11223`）
- `MINERU_BACKEND`
- `OLLAMA_HOST`

---

## 5. 核心业务流程

### 5.1 任务提交链路（taskNo）

1. 前端调用 `POST /api/v1/inspections/{taskNo}/submit`
2. 后端保存提交数据到 `InspectionSubmission`
3. 同步写入 `inspection_submit` 分类 JSON（文件库）
4. 按项目关联任务挑选模板（现场记录任务 + 报告任务）
5. 基于映射规则填充模板字段
6. 渲染并落库 PDF：
   - `site_record` 或 `report`
7. 生成文件自动关联到项目与对应任务

### 5.2 手动导出 PDF（文件库页面）

- 在“检测提交”分类勾选 submit JSON 后手动导出
- 流程与自动提交导出一致
- 若项目下现场记录和报告任务都存在，可同时生成两类 PDF

### 5.3 OCR 文件链路

1. 上传 OCR 文件（按 hash 去重）
2. 自动关联项目（包括命中去重复用文件时也会补关联）
3. 异步处理后生成 JSON
4. 生成 JSON 自动关联回任务项目

---

## 6. 映射与模板维护

### 6.1 映射规则位置

- `apps/api/inspection_submit_placeholder_maps.py`
  - `SUBMIT_PLACEHOLDER_MAPS`
  - 默认规则 `_RULES_DEFAULT`

### 6.2 模板处理服务

- `apps/api/inspection_pdf_service.py`
  - 模板解析
  - 映射填充（text/check/image）
  - 派生字段计算
  - PDF 渲染与落库

### 6.3 模板文件

- 存储于 `media/file_library/templates/`
- 典型字段：
  - `placeholder`
  - `fieldType` (`text` / `check` / `image`)
  - 坐标（`x,y,w,h` 或 `x0,y0,x1,y1`）
  - `page`

---

## 7. API 文档

完整接口说明见：

- `API_DOCUMENTATION.md`

主要分组：

- 认证：`/auth/*`
- 检测任务：`/inspections/*`
- 文件接口（项目隔离）
- OCR 接口（异步）
- Registry CRUD

---

## 8. 项目与任务文件关联规则（重要）

- 项目关联任务时，会自动同步任务文件到项目
- 项目解除任务时，会同步移除对应文件关联
- 任务新增/移除文件时，会同步到所有关联项目
- 文件与任务/项目关联关系具备传递性

---

## 9. 常见问题（FAQ）

### Q1：为什么某些模板字段没填值？

- 检查模板 `placeholder` 是否与映射 key 完全一致（区分大小写）
- 检查提交 JSON 里目标字段是否存在
- 检查 `fieldType` 是否正确（text/check/image）

### Q2：为什么文件列表接口返回空？

- 检查当前 taskNo 对应项目是否关联该分类文件
- 检查调用分类路径是否正确（`/files/{category}`）
- 检查用户权限和项目可见范围限制

### Q3：手动导出为什么没生成报告？

- 检查项目是否同时关联了报告任务与有效模板
- 检查报告模板是否为可解析 HTMLPDF 字段模板

---

## 10. 开发建议

- 与模板相关逻辑统一维护在 `inspection_pdf_service.py`
- 新增映射优先扩展 `inspection_submit_placeholder_maps.py`
- 变更接口时同步更新 `API_DOCUMENTATION.md`
- 提交前至少执行：

```bash
python -m py_compile apps/api/*.py apps/core/*.py
```

---

## 11. 许可与说明

当前仓库未声明开源许可证；如需开源，请补充 `LICENSE` 文件并在此处注明。
