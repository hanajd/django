# 06 运维、脚本与管理界面

**整理日期**：2026-08-20

---

## 1. Django Admin

- 入口：`/admin/`  
- 注册：`apps/core/admin.py`、`apps/evaluation_report/admin.py`  
- 用户「文件库容量配额」在用户资料内联中维护（默认约 5GiB）；超管/管理员角色通常不校验配额。

---

## 2. 自定义管理命令

用法：`python manage.py <command> [options]`

### 2.1 `apps.core`

| 命令 | 用途 |
|------|------|
| `init_data` | 角色与菜单种子 |
| `ensure_party_a_demo` | 甲方演示/引导环境 |
| `ensure_template_tester` | 模板测试角色与用户 |
| `ensure_org_roles` | 行政/检测部/评价部组织角色 |
| `ensure_org_directors` | 三部门主任演示账号（见 [组织主任账号说明.md](组织主任账号说明.md)） |
| `ensure_commission_coordinator` | 委托协调员相关 |
| `seed_sales_products` | 销售产品目录种子 |
| `set_jwt_runtime` | 写入/更新 JWT 运行时配置 |
| `backup_database` | SQLite 备份 / 列表 / 恢复 |
| `purge_library_trash` | 永久清理超期回收站（建议 cron） |
| `check_library_media` | DB 与 `media/file_library` 一致性；缺失移入回收站（启动时可自动跑） |
| `migrate_template_storage_layout` | 模板分层目录迁移 |
| `reconstruct_task_templates_from_storage` | 从 `templates/**/_task.json` 还原绑定 |
| `repair_task_template_library` | 补齐任务模板库缺口（执行前备份） |
| `fix_repaired_task_templates` | 修复还原后的模板配对 |
| `reorganize_site_records_layout` | 现场记录目录重组 |
| `reorganize_reports_layout` | 报告目录重组 |

### 2.2 `apps.api`

| 命令 | 用途 |
|------|------|
| `mock_inspection_submit` | 从前端模板 JSON 生成模拟提交 |

### 2.3 `apps.evaluation_report`

| 命令 | 用途 |
|------|------|
| `seed_evaluation_report_templates` | 评价报告书模板种子 |

Standalone 工程另有 `bootstrap_standalone` 等，不属于主站 `manage.py`。

### 2.4 `scripts/`（非 manage）

仓库 `scripts/` 下为一次性修复/审计脚本（字段映射、第五章公式等）。执行前先读脚本头注释并备份数据。

---

## 3. 日志与监控

- 可配置 `LOGGING`；`logs/` 可能含请求统计等中间件输出。  
- OCR/管线注意 `media/file_library/temp` 磁盘与超时。

---

## 4. 静态与媒体

- 生产：`collectstatic` → `STATIC_ROOT`。  
- `MEDIA_ROOT`：**必须备份**（文件库、评价报告工作区、F.1、APK、签名）。  
- 启动完整性：`library_media_integrity`（可用环境变量关闭）。

---

## 5. 安全发布清单（摘要）

- [ ] `DEBUG=False`、`SECRET_KEY` 来自环境变量  
- [ ] `ALLOWED_HOSTS`、HTTPS、`SECURE_*`  
- [ ] 数据库迁移已执行（含 `evaluation_report`）  
- [ ] `CORS_ALLOWED_ORIGINS` 收紧  
- [ ] JWT 策略与刷新保护  
- [ ] 上传大小限制；TeX/字体（若启用评价报告书）  
- [ ] OTA APK 目录权限与发版流程  
- [ ] 演示账号改密  

完整交付见 [部署流程-从零安装与源码保护评估.md](部署流程-从零安装与源码保护评估.md)。
